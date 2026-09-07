# SPDX-License-Identifier: Apache-2.0
"""``compare --since``/``--changed-path`` (Phase 2c) and ``compare --abi3``
(Phase 2d) -- ``docs/contribute/plans/one-comparison-product.md`` §6.

The invariants here are stated over the *primitives* (the seed resolver and
the ADR-043 D7 collect-mode rule) rather than only through one CLI
invocation, per AGENTS.md's "Primitive-level property tests": the rule these
two flags share -- "changed-path scope when a seed exists, else the current
library target, never a zero-TU no-op" -- is exactly the kind of generic
scoping decision a single worked example forecloses nothing about.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.source_replay import CI_MODE_TO_SCOPE
from abicheck.cli import main
from abicheck.model import AbiSnapshot
from abicheck.python_ext import PythonExtMetadata
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.changed_paths import (
    ChangedPathSeed,
    localized_collect_mode,
    resolve_changed_seed,
)

# Every collect mode the depth resolver can produce, plus two the mapping does
# not know, so the rule is exercised over its whole domain rather than the one
# mode the feature was written for.
_ALL_COLLECT_MODES = (*CI_MODE_TO_SCOPE, "unknown-mode", "")
_SEEDS = ((), ("src/a.cc",), ("src/a.cc", "include/a.h"))


def _write(snapshot: AbiSnapshot, path: Path) -> Path:
    path.write_text(snapshot_to_json(snapshot), encoding="utf-8")
    return path


def _abi3_snapshot() -> AbiSnapshot:
    return AbiSnapshot(
        library="foo.abi3.so",
        version="1.0",
        python_ext=PythonExtMetadata(
            module_name="foo",
            init_symbol="PyInit_foo",
            cpython_imports=["PyList_New", "PyType_GetName"],
        ),
    )


class TestLocalizedCollectModeProperties:
    """ADR-043 D7's scoping rule, stated as invariants over its whole domain."""

    @pytest.mark.parametrize(
        ("mode", "seed"), list(itertools.product(_ALL_COLLECT_MODES, _SEEDS))
    )
    def test_never_introduces_a_changed_scope_without_a_seed(
        self, mode: str, seed: tuple[str, ...]
    ) -> None:
        """The regression this rule exists to prevent: narrowing to a
        changed-file replay scope with no changed files selects zero TUs
        (ADR-043 D7). A mode the *caller* already resolved to changed scope
        is passed through untouched -- `collect_inline_pack` owns that case's
        own headers-only fallback -- so the invariant is that localization
        never *introduces* one. The oracle is the independent scope table
        (`CI_MODE_TO_SCOPE`), not this function's own mapping."""
        result = localized_collect_mode(mode, seed)
        if not seed and CI_MODE_TO_SCOPE.get(result) == "changed":
            assert CI_MODE_TO_SCOPE.get(mode) == "changed"

    @pytest.mark.parametrize(
        ("mode", "seed"), list(itertools.product(_ALL_COLLECT_MODES, _SEEDS))
    )
    def test_only_the_target_source_mode_ever_moves(
        self, mode: str, seed: tuple[str, ...]
    ) -> None:
        """A seed narrows the target-scoped source replay and nothing else:
        `graph-full` is a deliberate full-scope request, and off/build/
        graph-build run no replay to narrow."""
        result = localized_collect_mode(mode, seed)
        assert result == (
            "source-changed" if (seed and mode == "source-target") else mode
        )

    @pytest.mark.parametrize(
        ("mode", "seed"), list(itertools.product(_ALL_COLLECT_MODES, _SEEDS))
    )
    def test_is_idempotent(self, mode: str, seed: tuple[str, ...]) -> None:
        once = localized_collect_mode(mode, seed)
        assert localized_collect_mode(once, seed) == once

    def test_a_list_seed_behaves_like_a_tuple_seed(self) -> None:
        assert localized_collect_mode("source-target", ["a.cc"]) == "source-changed"


class TestResolveChangedSeed:
    def test_changed_path_wins_over_since(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "abicheck.workflows.changed_paths.git_changed_paths",
            lambda *a, **k: ["never/used.cc"],
        )
        seed = resolve_changed_seed(("a.cc",), "origin/main", None)
        assert seed == ChangedPathSeed(("a.cc",), "--changed-path", True)

    def test_an_empty_git_diff_is_seeded_but_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A no-op PR is a *valid* seed with no paths -- distinct from a
        failed seed, which is what keeps the scope broad instead of empty."""
        monkeypatch.setattr(
            "abicheck.workflows.changed_paths.git_changed_paths", lambda *a, **k: []
        )
        seed = resolve_changed_seed((), "origin/main", None)
        assert seed.paths == () and seed.seeded is True
        # ... and it still cannot produce a zero-TU replay scope.
        assert localized_collect_mode("source-target", seed.paths) == "source-target"

    def test_a_failed_git_diff_is_not_seeded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "abicheck.workflows.changed_paths.git_changed_paths", lambda *a, **k: None
        )
        seed = resolve_changed_seed((), "nope", None)
        assert seed.paths == () and seed.seeded is False
        assert "seed failed" in seed.source

    def test_no_input_is_no_seed(self) -> None:
        assert resolve_changed_seed((), None, None) == ChangedPathSeed()

    def test_git_failure_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import subprocess

        def _boom(*a: object, **k: object) -> None:
            raise OSError("no git here")

        monkeypatch.setattr(subprocess, "run", _boom)
        messages: list[str] = []
        from abicheck.workflows.changed_paths import git_changed_paths

        assert git_changed_paths("main", tmp_path, notify=messages.append) is None
        assert messages and "git diff" in messages[0]


class TestCompareChangedPathCli:
    def test_both_options_exist_on_compare(self) -> None:
        out = CliRunner().invoke(main, ["compare", "--help-all"]).output
        assert "--since" in out and "--changed-path" in out

    def test_scoping_only_no_finding_of_its_own(self, tmp_path: Path) -> None:
        path = _write(
            AbiSnapshot(library="libfoo.so", version="1.0"), tmp_path / "s.json"
        )
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(path),
                str(path),
                "--changed-path",
                "src/a.cc",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["changes"] == []

    def test_request_carries_the_seed_to_the_shared_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The CLI must reach the *same* per-side primitive the typed API
        uses (`_resolve_side_snapshot_impl(changed_paths=...)`), not a second
        scoping path of its own."""
        seen: list[tuple[str, ...]] = []
        # Patched on the *caller's* module: `service_compare_pipeline` binds
        # the name at import time, so a patch on the defining module (or on
        # the `service_input_resolution` facade) would never be read.
        import abicheck.service_compare_pipeline as execute_mod

        real = execute_mod._resolve_side_snapshot_impl

        def _spy(*args: object, **kwargs: object) -> object:
            seen.append(tuple(kwargs.get("changed_paths") or ()))
            return real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(execute_mod, "_resolve_side_snapshot_impl", _spy)
        path = _write(
            AbiSnapshot(library="libfoo.so", version="1.0"), tmp_path / "s.json"
        )
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(path),
                str(path),
                "--changed-path",
                "src/a.cc",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert seen == [("src/a.cc",), ("src/a.cc",)]


class TestReleaseOperandRejection:
    """A directory/package `compare` implements none of the three flags, so
    accepting one there would be a flag that silently does nothing -- the
    same failure `--sources`/`--depth source` are already rejected for."""

    @pytest.mark.parametrize(
        ("flag", "value"),
        [("--since", "origin/main"), ("--changed-path", "a.cc"), ("--abi3", "3.9")],
    )
    def test_rejected_for_a_directory_operand(
        self, flag: str, value: str, tmp_path: Path
    ) -> None:
        old, new = tmp_path / "old", tmp_path / "new"
        for side in (old, new):
            side.mkdir()
            _write(
                AbiSnapshot(library="libfoo.so", version="1.0"),
                side / "libfoo.so.abi.json",
            )
        result = CliRunner().invoke(main, ["compare", str(old), str(new), flag, value])
        assert result.exit_code == 64, result.output
        assert flag in result.output and "not supported" in result.output

    def test_a_release_compare_without_them_still_works(self, tmp_path: Path) -> None:
        """The negative control: the rejection fires on the flags, not on
        every directory operand."""
        old, new = tmp_path / "old", tmp_path / "new"
        for side in (old, new):
            side.mkdir()
            _write(
                AbiSnapshot(library="libfoo.so", version="1.0"),
                side / "libfoo.so.abi.json",
            )
        result = CliRunner().invoke(main, ["compare", str(old), str(new)])
        assert result.exit_code == 0, result.output


class TestTypedRequestParity:
    def test_a_seeded_request_narrows_both_sides_collect_mode(self) -> None:
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service_compare_evidence import resolve_compare_request_evidence

        def _modes(changed: tuple[str, ...]) -> set[str]:
            request = CompareRequest(
                old=InputSpec(path=Path("old.so")),
                new=InputSpec(path=Path("new.so")),
                depth="source",
                changed_paths=changed,
            )
            old, new = resolve_compare_request_evidence(request, None)
            return {old.collect_mode, new.collect_mode}

        assert _modes(()) == {"source-target"}
        assert _modes(("src/a.cc",)) == {"source-changed"}


class TestCompareAbi3:
    def test_findings_are_marked_candidate_side_and_advisory(
        self, tmp_path: Path
    ) -> None:
        path = _write(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
        result = CliRunner().invoke(
            main, ["compare", str(path), str(path), "--abi3", "3.9", "--format", "json"]
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.stdout)
        findings = [
            c for c in report["changes"] if c["kind"] == "python_stable_abi_violation"
        ]
        assert findings, report["changes"]
        assert all(c["candidate_side_enrichment"] is True for c in findings)
        assert {c["severity"] for c in findings} == {"risk"}

    def test_the_audit_is_candidate_side_only(self, tmp_path: Path) -> None:
        """The OLD side is never audited: an offending baseline compared
        against a clean candidate produces nothing."""
        old = _write(_abi3_snapshot(), tmp_path / "old.abi.json")
        clean = AbiSnapshot(
            library="foo.abi3.so",
            version="2.0",
            python_ext=PythonExtMetadata(
                module_name="foo",
                init_symbol="PyInit_foo",
                cpython_imports=["PyList_New"],
            ),
        )
        new = _write(clean, tmp_path / "new.abi.json")
        result = CliRunner().invoke(
            main, ["compare", str(old), str(new), "--abi3", "3.9", "--format", "json"]
        )
        report = json.loads(result.stdout)
        assert not [
            c
            for c in report["changes"]
            if c["kind"] == "python_stable_abi_violation"
            and c.get("candidate_side_enrichment")
        ]

    def test_non_extension_candidate_is_exit_7(self, tmp_path: Path) -> None:
        path = _write(
            AbiSnapshot(library="libfoo.so", version="1.0"), tmp_path / "s.json"
        )
        result = CliRunner().invoke(
            main, ["compare", str(path), str(path), "--abi3", "3.9", "--format", "json"]
        )
        assert result.exit_code == 7, result.output
        report = json.loads(result.stdout)
        assert report["exit"]["evidence_contract_error_contribution"] == 7
        assert "not a recognisable CPython extension module" in result.stderr

    def test_an_invalid_floor_is_a_usage_error(self, tmp_path: Path) -> None:
        path = _write(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
        result = CliRunner().invoke(
            main, ["compare", str(path), str(path), "--abi3", "nonsense"]
        )
        assert result.exit_code == 64, result.output

    def test_without_the_flag_nothing_changes(self, tmp_path: Path) -> None:
        path = _write(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
        result = CliRunner().invoke(
            main, ["compare", str(path), str(path), "--format", "json"]
        )
        assert result.exit_code == 0
        report = json.loads(result.stdout)
        assert not [
            c for c in report["changes"] if c["kind"] == "python_stable_abi_violation"
        ]


class TestAbi3FindingsReachPolicy:
    """Security review of PR #1123: the audit's findings must enter the
    change set *before* classification, so policy/suppression/verdict/exit
    all act on them. "Advisory" is the kind's own RISK default verdict, not
    a finding rendered after the verdict was already fixed.

    Parametrized over the whole severity vocabulary a policy `overrides:`
    entry accepts, against an independently-stated oracle (the verdict a
    finding of that class produces for any other kind), rather than the one
    "break" case the review reported.
    """

    def _policy(self, tmp_path: Path, severity: str) -> Path:
        path = tmp_path / f"policy-{severity}.yaml"
        path.write_text(
            f"overrides:\n  python_stable_abi_violation: {severity}\n",
            encoding="utf-8",
        )
        return path

    @pytest.mark.parametrize(
        ("severity", "verdict", "exit_code"),
        [
            ("break", "BREAKING", 4),
            ("warn", "API_BREAK", 2),
            ("risk", "COMPATIBLE_WITH_RISK", 0),
            ("ignore", "COMPATIBLE", 0),
        ],
    )
    def test_a_policy_override_moves_the_verdict_and_the_exit_code(
        self, severity: str, verdict: str, exit_code: int, tmp_path: Path
    ) -> None:
        path = _write(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(path),
                str(path),
                "--abi3",
                "3.9",
                "--policy",
                str(self._policy(tmp_path, severity)),
                "--format",
                "json",
            ],
        )
        assert result.exit_code == exit_code, result.output
        report = json.loads(result.stdout)
        assert report["verdict"] == verdict
        assert report["exit"]["code"] == exit_code

    def test_the_default_stays_advisory(self, tmp_path: Path) -> None:
        """The negative control for the parametrization above: with no
        policy in effect the same findings still gate nothing."""
        path = _write(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
        result = CliRunner().invoke(
            main, ["compare", str(path), str(path), "--abi3", "3.9", "--format", "json"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["verdict"] == "COMPATIBLE_WITH_RISK"

    def test_a_suppression_rule_can_reach_them(self, tmp_path: Path) -> None:
        """The other half of "before classification": suppression sees them
        too, so they land in the disposition ledger rather than the change
        list -- impossible for a finding appended after the fact."""
        path = _write(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
        suppress = tmp_path / "suppress.yaml"
        suppress.write_text(
            "version: 1\nsuppressions:\n"
            "  - change_kind: python_stable_abi_violation\n"
            "    symbol: python:foo\n"
            "    reachability: any\n"
            "    reason: accepted for this release\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(path),
                str(path),
                "--abi3",
                "3.9",
                "--suppress",
                str(suppress),
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.stdout)
        assert not [
            c for c in report["changes"] if c["kind"] == "python_stable_abi_violation"
        ]
        # ADR-067's conserved ledger accounts for it -- "detected, then
        # suppressed by rule X", not silently absent.
        audit = report["disposition_audit"]
        assert audit["detected_total"] == 1 and audit["effective_total"] == 0
        assert audit["counts"]["suppressed"] == 1, audit

    def test_the_typed_api_folds_them_the_same_way(self, tmp_path: Path) -> None:
        """Front-end parity: the fold is one shared rule, so a typed caller
        cannot silently get the pre-fix ordering."""
        from abicheck.model import AbiSnapshot as _S
        from abicheck.workflows import abi3_audit

        folded, failure = abi3_audit.fold(None, _abi3_snapshot(), (3, 9))
        assert failure is None
        assert folded and all(c.candidate_side_enrichment for c in folded)
        # A non-extension candidate contributes no finding, only the abort.
        folded2, failure2 = abi3_audit.fold(
            ["pre-existing"], _S(library="libfoo.so", version="1.0"), (3, 9)
        )
        assert folded2 == ["pre-existing"] and failure2 is not None


class TestAbi3FloorConfigDefault:
    """ADR-068 D5: the floor is a stable project property with a per-run
    CLI override; the changed-path seed is per-run only."""

    def _config(self, tmp_path: Path, floor: str) -> Path:
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(f"python:\n  abi3_floor: '{floor}'\n", encoding="utf-8")
        return cfg

    def test_config_supplies_the_floor(self, tmp_path: Path) -> None:
        path = _write(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
        cfg = self._config(tmp_path, "3.9")
        result = CliRunner().invoke(
            main,
            ["compare", str(path), str(path), "--config", str(cfg), "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        assert [
            c
            for c in json.loads(result.stdout)["changes"]
            if c["kind"] == "python_stable_abi_violation"
        ]

    def test_the_flag_overrides_the_configured_floor(self, tmp_path: Path) -> None:
        """A 3.12 floor accepts PyType_GetName (added 3.11), so the explicit
        flag visibly wins over the project's 3.9."""
        path = _write(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
        cfg = self._config(tmp_path, "3.9")
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(path),
                str(path),
                "--config",
                str(cfg),
                "--abi3",
                "3.12",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        findings = [
            c
            for c in json.loads(result.stdout)["changes"]
            if c["kind"] == "python_stable_abi_violation"
        ]
        assert not findings, findings

    def test_the_config_key_round_trips(self, tmp_path: Path) -> None:
        from abicheck.buildsource.build_config import BuildConfig

        cfg = BuildConfig.from_dict({"python": {"abi3_floor": "3.9"}})
        assert cfg.python_abi3_floor == "3.9"
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg
