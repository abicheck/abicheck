"""Symbol binding and the removed-instantiation list on template findings.

Two findings in ``diff_templates`` previously stayed BREAKING under a consumer
policy that demotes WEAK (COMDAT) losses, because neither carried
``Change.symbol_binding``, so a ``binding: weak`` selector could never match
them:

* ``INSTANTIATION_MISSING_FROM_BINARY`` now stamps the old-side linkage.
* ``INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API`` aggregates the linkage over its
  whole removed set (``"weak"`` only when every removed instantiation is
  WEAK, fail closed to ``None`` on any unknown member) and lists every removed
  instantiation in the uncapped ``removed_instantiations`` field, while
  ``symbol`` stays the template stem that finding identity keys off.

Verdicts and kind defaults are unchanged; these tests pin the new fields and
that the existing ``binding:`` selector now reaches the cases it was meant to.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.compare.naming_conventions import _INTERNAL_TEMPLATE_NAMESPACES
from abicheck.compare.template_surface import aggregate_removed_binding
from abicheck.diff_templates import (
    detect_internal_template_leaks,
    detect_missing_instantiations,
)
from abicheck.model.declarations import Function, Visibility
from abicheck.model.elf_facts import SymbolBinding
from abicheck.model.snapshot import AbiSnapshot
from abicheck.policy.reclassify import (
    ReclassifyRule,
    first_matching_reclassify_verdict,
)
from abicheck.report.change_annotations import change_annotation_fields

_LEAK_STEM = "lib::__detail::walk"
_MISSING_STEM = "descriptor"


def _fn(
    name: str,
    binding: SymbolBinding | None,
    *,
    mangled: str | None = None,
) -> Function:
    """A public function whose mangled name is not ``_Z``-prefixed.

    Keeping the mangled name out of the Itanium shape means identity falls back
    to the declared ``name`` even when a demangler is installed, so the stem
    and instantiation grouping are deterministic on every CI lane.
    """
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"sym:{name}",
        return_type="void",
        visibility=Visibility.PUBLIC,
        elf_binding=binding,
    )


def _snap(funcs: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(library="libx.so", version="1", functions=funcs)


def _leak_pair(
    removed: list[tuple[str, SymbolBinding | None]],
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Old has a surviving ``walk<int>`` plus the given removed instantiations;
    new keeps only the survivor, so the stem is confirmed by the removals."""
    survivor = _fn(f"{_LEAK_STEM}<int>", SymbolBinding.GLOBAL)
    old = _snap([survivor] + [_fn(f"{_LEAK_STEM}<{arg}>", b) for arg, b in removed])
    new = _snap([_fn(f"{_LEAK_STEM}<int>", SymbolBinding.GLOBAL)])
    return old, new


def _leak_change(removed: list[tuple[str, SymbolBinding | None]]):
    old, new = _leak_pair(removed)
    changes = detect_internal_template_leaks(old, new)
    assert len(changes) == 1
    return changes[0]


# ---------------------------------------------------------------------------
# INSTANTIATION_MISSING_FROM_BINARY: old-side linkage on the finding
# ---------------------------------------------------------------------------


def _missing_pair(dropped_binding: SymbolBinding | None):
    """``descriptor<float>`` survives; ``descriptor<double>`` (the dropped one)
    carries *dropped_binding*, so the stem is still confirmed by a survivor."""
    old = _snap(
        [
            _fn(f"{_MISSING_STEM}<float>::get", SymbolBinding.GLOBAL),
            _fn(f"{_MISSING_STEM}<double>::get", dropped_binding),
        ]
    )
    new = _snap([_fn(f"{_MISSING_STEM}<float>::get", SymbolBinding.GLOBAL)])
    return old, new


class TestMissingInstantiationBinding:
    def test_weak_dropped_instantiation_stamps_weak_and_selector_matches(self) -> None:
        old, new = _missing_pair(SymbolBinding.WEAK)
        findings = detect_missing_instantiations(old, new)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == ChangeKind.INSTANTIATION_MISSING_FROM_BINARY
        assert f.symbol_binding == "weak"
        rule = ReclassifyRule(
            to_verdict=Verdict.COMPATIBLE_WITH_RISK,
            to="risk",
            change_kind="instantiation_missing_from_binary",
            symbol_pattern=".*",
            binding="weak",
        )
        assert rule.matches(f)

    def test_global_dropped_instantiation_stamps_global_and_selector_skips(
        self,
    ) -> None:
        old, new = _missing_pair(SymbolBinding.GLOBAL)
        (f,) = detect_missing_instantiations(old, new)
        assert f.symbol_binding == "global"
        rule = ReclassifyRule(
            to_verdict=Verdict.COMPATIBLE_WITH_RISK,
            to="risk",
            change_kind="instantiation_missing_from_binary",
            symbol_pattern=".*",
            binding="weak",
        )
        assert not rule.matches(f)

    def test_uncaptured_binding_stays_none(self) -> None:
        old, new = _missing_pair(None)
        (f,) = detect_missing_instantiations(old, new)
        assert f.symbol_binding is None


# ---------------------------------------------------------------------------
# INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API: aggregate binding
# ---------------------------------------------------------------------------


class TestLeakBindingAggregate:
    def test_all_weak_gives_weak(self) -> None:
        c = _leak_change([("double", SymbolBinding.WEAK), ("char", SymbolBinding.WEAK)])
        assert c.symbol_binding == "weak"

    def test_weak_plus_global_gives_global(self) -> None:
        c = _leak_change(
            [("double", SymbolBinding.WEAK), ("char", SymbolBinding.GLOBAL)]
        )
        assert c.symbol_binding == "global"

    def test_one_unknown_binding_fails_closed_to_none(self) -> None:
        c = _leak_change([("double", SymbolBinding.WEAK), ("char", None)])
        assert c.symbol_binding is None

    def test_symbol_stays_the_template_stem(self) -> None:
        # Finding identity keys off `symbol`; the aggregate lives only in
        # `symbol_binding` and `removed_instantiations`.
        c = _leak_change([("double", SymbolBinding.WEAK)])
        assert c.symbol == _LEAK_STEM

    def test_removed_instantiations_record_mangled_demangled_and_binding(self) -> None:
        c = _leak_change(
            [("double", SymbolBinding.WEAK), ("char", SymbolBinding.GLOBAL)]
        )
        assert c.removed_instantiations is not None
        by_mangled = {r.mangled: r for r in c.removed_instantiations}
        assert set(by_mangled) == {
            f"sym:{_LEAK_STEM}<double>",
            f"sym:{_LEAK_STEM}<char>",
        }
        assert by_mangled[f"sym:{_LEAK_STEM}<double>"].binding == "weak"
        assert by_mangled[f"sym:{_LEAK_STEM}<char>"].binding == "global"
        assert by_mangled[f"sym:{_LEAK_STEM}<char>"].demangled == f"{_LEAK_STEM}<char>"

    def test_more_than_three_removed_are_all_listed(self) -> None:
        # `detail` names at most three; the structured field must not cap.
        args = ["int8", "int16", "int32", "int64", "uint8"]
        c = _leak_change([(a, SymbolBinding.WEAK) for a in args])
        assert c.removed_instantiations is not None
        assert len(c.removed_instantiations) == len(args)
        assert {r.mangled for r in c.removed_instantiations} == {
            f"sym:{_LEAK_STEM}<{a}>" for a in args
        }
        assert c.symbol_binding == "weak"
        # The human description keeps its three-name cap; the structured list
        # above is what carries the rest.
        assert f"{_LEAK_STEM}<int8>" not in c.description
        assert f"{_LEAK_STEM}<uint8>" not in c.description

    def test_other_kinds_carry_no_removed_list(self) -> None:
        (f,) = detect_missing_instantiations(*_missing_pair(SymbolBinding.WEAK))
        assert f.removed_instantiations is None

    def test_binding_weak_reclassify_demotes_only_the_all_weak_leak(self) -> None:
        # The consumer-policy case this change exists for: a `binding: weak`
        # reclassify demotes a leak whose whole removed set is WEAK, and leaves
        # a mixed set (one GLOBAL member) at its BREAKING verdict.
        rule = ReclassifyRule(
            to_verdict=Verdict.COMPATIBLE_WITH_RISK,
            to="risk",
            change_kind="internal_template_leaks_via_public_api",
            symbol_pattern=".*",
            binding="weak",
        )
        all_weak = _leak_change(
            [("double", SymbolBinding.WEAK), ("char", SymbolBinding.WEAK)]
        )
        mixed = _leak_change(
            [("double", SymbolBinding.WEAK), ("char", SymbolBinding.GLOBAL)]
        )
        unknown = _leak_change([("double", SymbolBinding.WEAK), ("char", None)])
        assert first_matching_reclassify_verdict([rule], all_weak) == (
            Verdict.COMPATIBLE_WITH_RISK
        )
        assert first_matching_reclassify_verdict([rule], mixed) is None
        assert first_matching_reclassify_verdict([rule], unknown) is None

    def test_projection_is_uncapped_and_serialized(self) -> None:
        args = ["int8", "int16", "int32", "int64", "uint8"]
        c = _leak_change([(a, SymbolBinding.WEAK) for a in args])
        fields = change_annotation_fields(c)
        assert "removed_instantiations" in fields
        rows = fields["removed_instantiations"]
        assert len(rows) == len(args)
        assert all(set(r) == {"mangled", "demangled", "binding"} for r in rows)
        assert fields["symbol_binding"] == "weak"

    def test_serialized_list_validates_against_the_published_schema(self) -> None:
        from abicheck.schemas import load_compare_report_schema
        from tests.schema_validation import validate_instance

        c = _leak_change([("double", SymbolBinding.WEAK), ("char", None)])
        rows = change_annotation_fields(c)["removed_instantiations"]
        prop = load_compare_report_schema()["$defs"]["change"]["properties"][
            "removed_instantiations"
        ]
        validate_instance(rows, prop)


# ---------------------------------------------------------------------------
# Aggregation rule, over every combination of up to three members
# ---------------------------------------------------------------------------

_BINDINGS = [
    SymbolBinding.WEAK,
    SymbolBinding.GLOBAL,
    SymbolBinding.UNIQUE,
    SymbolBinding.OTHER,
    SymbolBinding.LOCAL,
    None,
]


@pytest.mark.parametrize("size", [1, 2, 3])
def test_aggregate_invariant_over_every_combination(size: int) -> None:
    """``"weak"`` iff every member is WEAK; ``None`` iff any member is unknown;
    otherwise a non-weak value that is actually present in the set."""
    for combo in itertools.product(_BINDINGS, repeat=size):
        removed = [
            (_fn(f"{_LEAK_STEM}<t{i}>", b), f"{_LEAK_STEM}<t{i}>")
            for i, b in enumerate(combo)
        ]
        got = aggregate_removed_binding(removed)
        if any(b is None for b in combo):
            assert got is None, combo
        elif all(b is SymbolBinding.WEAK for b in combo):
            assert got == "weak", combo
        else:
            assert got is not None and got != "weak", combo
            assert got in {b.value for b in combo}, combo


def test_aggregate_of_nothing_is_none() -> None:
    assert aggregate_removed_binding([]) is None


def test_namespace_fixture_is_internal() -> None:
    # Guard the fixture's own premise: `lib::__detail::` must be an internal
    # namespace, or the leak detector would never reach these snapshots.
    assert "__detail" in _INTERNAL_TEMPLATE_NAMESPACES
