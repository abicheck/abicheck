"""Tests for the frozen-namespace policy (oneTBB detail::r1 shape).

Synthetic snapshots — no compiler needed. Exercises:

- ``PolicyFile.frozen_namespaces`` YAML loading and parsing.
- The ``EscalateFrozenNamespaceViolations`` post-processing step.
- The ``Suppression.namespace`` selector.
- The verdict-computation guard that blocks downgrades of tagged findings.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.policy_file import PolicyFile
from abicheck.post_processing import (
    DEFAULT_PIPELINE,
    EscalateFrozenNamespaceViolations,
)
from abicheck.suppression import Suppression, SuppressionList

# ── Test fixtures ──────────────────────────────────────────────────────────


def _snap(version: str, functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions,
        variables=[],
        types=[],
    )


def _fn(name: str, mangled: str, params: list[Param] | None = None) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=params or [],
        visibility=Visibility.PUBLIC,
    )


# ── PolicyFile YAML parsing ────────────────────────────────────────────────


class TestPolicyFileFrozenNamespaces:
    def test_default_is_empty_list(self, tmp_path: Path) -> None:
        p = tmp_path / "policy.yaml"
        p.write_text("base_policy: strict_abi\n", encoding="utf-8")
        pf = PolicyFile.load(p)
        assert pf.frozen_namespaces == []

    def test_parses_glob_list(self, tmp_path: Path) -> None:
        p = tmp_path / "policy.yaml"
        p.write_text(
            "base_policy: strict_abi\n"
            "frozen_namespaces:\n"
            '  - "**::detail::r1*"\n'
            '  - "**::detail::d1*"\n',
            encoding="utf-8",
        )
        pf = PolicyFile.load(p)
        assert pf.frozen_namespaces == ["**::detail::r1*", "**::detail::d1*"]

    def test_rejects_non_list(self, tmp_path: Path) -> None:
        p = tmp_path / "policy.yaml"
        p.write_text(
            'frozen_namespaces: "**::detail::r1*"\n',  # string, not list
            encoding="utf-8",
        )
        with pytest.raises(Exception, match="frozen_namespaces"):
            PolicyFile.load(p)

    def test_rejects_non_string_entry(self, tmp_path: Path) -> None:
        p = tmp_path / "policy.yaml"
        p.write_text(
            "frozen_namespaces:\n  - 42\n",
            encoding="utf-8",
        )
        with pytest.raises(Exception, match="expected string"):
            PolicyFile.load(p)


# ── EscalateFrozenNamespaceViolations step ─────────────────────────────────


class TestEscalateFrozenNamespaceViolations:
    def test_no_globs_no_op(self) -> None:
        """With no configured frozen_namespaces, the step is a no-op."""
        c = Change(
            kind=ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="ns::detail::r1::dispatch",
            description="param changed",
        )
        old = _snap("1.0", [])
        new = _snap("2.0", [])
        pp = DEFAULT_PIPELINE.run([c], old, new, frozen_namespaces=[])
        assert pp.kept[0].frozen_namespace_violation is None
        assert not pp.kept[0].description.startswith("[frozen-namespace")

    def test_tags_matching_symbol(self) -> None:
        c = Change(
            kind=ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="ns::detail::r1::dispatch",
            description="param changed",
        )
        old = _snap("1.0", [])
        new = _snap("2.0", [])
        pp = DEFAULT_PIPELINE.run(
            [c], old, new, frozen_namespaces=["**::detail::r1::*"],
        )
        kept = pp.kept[0]
        assert kept.frozen_namespace_violation == "**::detail::r1::*"
        assert kept.description.startswith("[frozen-namespace violation:")

    def test_tags_via_caused_by_type(self) -> None:
        """Synthetic overlay findings carry the root cause in
        caused_by_type rather than symbol; the step must match both."""
        c = Change(
            kind=ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API,
            symbol="overlay_id_42",
            description="leak",
            caused_by_type="ns::detail::r1::registry",
        )
        ctx_changes = EscalateFrozenNamespaceViolations().run(
            [c],
            type("Ctx", (), {
                "frozen_namespaces": ["**::detail::r1::*"],
            })(),
        )
        assert ctx_changes[0].frozen_namespace_violation == "**::detail::r1::*"

    def test_strips_template_args_before_matching(self) -> None:
        c = Change(
            kind=ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="ns::detail::r1::foo<int>",
            description="param changed",
        )
        old = _snap("1.0", [])
        new = _snap("2.0", [])
        pp = DEFAULT_PIPELINE.run(
            [c], old, new, frozen_namespaces=["**::detail::r1::*"],
        )
        assert pp.kept[0].frozen_namespace_violation == "**::detail::r1::*"

    def test_non_matching_namespace_untagged(self) -> None:
        c = Change(
            kind=ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="ns::other::dispatch",
            description="param changed",
        )
        old = _snap("1.0", [])
        new = _snap("2.0", [])
        pp = DEFAULT_PIPELINE.run(
            [c], old, new, frozen_namespaces=["**::detail::r1::*"],
        )
        assert pp.kept[0].frozen_namespace_violation is None

    def test_func_added_in_frozen_ns_still_compatible(self) -> None:
        """Adding a new symbol in the frozen namespace is the documented
        evolution path — the underlying ChangeKind stays COMPATIBLE so the
        verdict computation downgrades correctly. The step only TAGS the
        finding; it does not invent a new kind or escalate."""
        old = _snap("1.0", [])
        new = _snap("2.0", [_fn("ns::detail::r1::new_entry", "_ZN2ns6detail2r19new_entryEv")])
        r = compare(old, new, policy_file=PolicyFile(
            base_policy="strict_abi",
            frozen_namespaces=["**::detail::r1::*"],
        ))
        # FUNC_ADDED is COMPATIBLE in strict_abi.  Verdict must be COMPATIBLE
        # even though the symbol is inside the frozen namespace.
        assert r.verdict == Verdict.COMPATIBLE


# ── Verdict-downgrade guard ────────────────────────────────────────────────


class TestFrozenNamespaceBlocksDowngrade:
    def test_downgrade_override_ignored_for_tagged_change(self) -> None:
        """A policy override that downgrades FUNC_PARAMS_CHANGED to ignore
        must not apply to a finding inside a frozen namespace."""
        old = _snap("1.0", [
            _fn("ns::detail::r1::dispatch", "_ZN2ns6detail2r18dispatchEi",
                params=[Param(name="n", type="int")]),
        ])
        new = _snap("2.0", [
            _fn("ns::detail::r1::dispatch", "_ZN2ns6detail2r18dispatchEi",
                params=[Param(name="n", type="long")]),
        ])
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_PARAMS_CHANGED: Verdict.COMPATIBLE},
            frozen_namespaces=["**::detail::r1::*"],
        )
        r = compare(old, new, policy_file=pf)
        # The override would normally downgrade the param-change to
        # COMPATIBLE, but the frozen-ns tag blocks the downgrade.
        assert r.verdict == Verdict.BREAKING

    def test_override_outside_frozen_ns_still_applies(self) -> None:
        """The same downgrade override still works for findings outside
        the frozen namespace — the guard must be scoped to tagged changes."""
        old = _snap("1.0", [
            _fn("ns::pub::dispatch", "_ZN2ns3pub8dispatchEi",
                params=[Param(name="n", type="int")]),
        ])
        new = _snap("2.0", [
            _fn("ns::pub::dispatch", "_ZN2ns3pub8dispatchEi",
                params=[Param(name="n", type="long")]),
        ])
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_PARAMS_CHANGED: Verdict.COMPATIBLE},
            frozen_namespaces=["**::detail::r1::*"],
        )
        r = compare(old, new, policy_file=pf)
        assert r.verdict == Verdict.COMPATIBLE


# ── Suppression namespace selector ─────────────────────────────────────────


class TestSuppressionNamespaceSelector:
    def test_namespace_suppresses_symbol_match(self) -> None:
        c = Change(
            kind=ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="ns::detail::r1::dispatch",
            description="x",
        )
        sup = Suppression(namespace="**::detail::r1::*", reason="known")
        assert sup.matches(c)

    def test_namespace_suppresses_caused_by_type_match(self) -> None:
        c = Change(
            kind=ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API,
            symbol="overlay_id",
            description="x",
            caused_by_type="ns::detail::r1::registry",
        )
        sup = Suppression(namespace="**::detail::r1::*", reason="known")
        assert sup.matches(c)

    def test_namespace_does_not_match_other_ns(self) -> None:
        c = Change(
            kind=ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="ns::pub::dispatch",
            description="x",
        )
        sup = Suppression(namespace="**::detail::r1::*", reason="known")
        assert not sup.matches(c)

    def test_namespace_only_is_a_valid_selector(self) -> None:
        """The selector validation must accept ``namespace:`` on its own."""
        sup = Suppression(namespace="**::internal::*")
        # No exception — the constructor accepts namespace-only selectors.
        assert sup.namespace == "**::internal::*"

    def test_namespace_in_suppressionlist_end_to_end(self) -> None:
        """A namespace suppression filters a real change through compare()."""
        old = _snap("1.0", [
            _fn("ns::detail::r1::dispatch", "_ZN2ns6detail2r18dispatchEi",
                params=[Param(name="n", type="int")]),
        ])
        new = _snap("2.0", [])  # function disappeared
        suppression = SuppressionList(
            [Suppression(namespace="**::detail::r1::*", reason="legacy churn")],
        )
        r = compare(old, new, suppression=suppression)
        # The finding is suppressed → verdict is NO_CHANGE.
        assert r.verdict == Verdict.NO_CHANGE
