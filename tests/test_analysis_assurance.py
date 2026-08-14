# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""P0.4: the analysis-assurance axis (analysis_assurance.py).

Mirrors ``test_contract_coverage_exit.py``'s style for the same reason: the
interesting claim is an **exit code**, not merely that a JSON field exists,
so the CLI-level tests lead with exit codes and the fold-level tests assert
the ``max`` discipline directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck import checker
from abicheck.analysis_assurance import (
    ANALYSIS_ASSURANCE_SCHEMA_VERSION,
    ASSURANCE_STATUS_VALUES,
    AnalysisAssurance,
    ExportAccounting,
    TargetAccounting,
    TranslationUnitAccounting,
    analysis_assurance_exit_contribution,
    compute_analysis_assurance,
    fold_analysis_assurance_exit,
)
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change, DiffResult
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _write(tmp_path: Path, old: AbiSnapshot, new: AbiSnapshot) -> tuple[Path, Path]:
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _header_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A real, header-scoped, unchanged pair -- resolves a clean public
    surface, so ``scope_resolved`` is True and ``status`` should read
    ``"complete"`` with no gating flag involved.
    """
    common = {"library": "libfoo.so.1", "from_headers": True}
    fns = [_fn("pub_a", "_Z5pub_av")]
    return (
        AbiSnapshot(version="1.0", functions=fns, **common),
        AbiSnapshot(version="2.0", functions=fns, **common),
    )


def _elf_only_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """No headers, no build evidence -- the minimal invocation shape.

    ``elf_only_mode=True`` is what keeps ``_detect_evidence_tiers`` from
    falling back to "a declaration-only snapshot is header-level evidence"
    (its own documented in-memory/unit-test carve-out) -- without it this
    fixture would misleadingly read as HEADER_AWARE.
    """
    fns = [_fn("pub_a", "_Z5pub_av")]
    common = {"library": "libfoo.so.1", "elf_only_mode": True}
    return (
        AbiSnapshot(version="1.0", functions=fns, **common),
        AbiSnapshot(version="2.0", functions=fns, **common),
    )


def _breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    common = {"library": "libfoo.so.1", "from_headers": True}
    return (
        AbiSnapshot(
            version="1.0",
            functions=[_fn("pub_a", "_Z5pub_av"), _fn("pub_b", "_Z5pub_bv")],
            **common,
        ),
        AbiSnapshot(version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common),
    )


def _compare(tmp_path: Path, pair, *extra: str):
    old_p, new_p = _write(tmp_path, *pair)
    return CliRunner().invoke(main, ["compare", str(old_p), str(new_p), *extra])


class TestAnalysisAssuranceModel:
    """Construction/serialization -- the model is real, typed data, not a
    stringly-typed dict some caller has to guess the shape of."""

    def test_status_vocabulary_is_the_required_five(self) -> None:
        assert ASSURANCE_STATUS_VALUES == {
            "complete",
            "partial",
            "failed",
            "not_comparable",
            "not_requested",
        }

    def test_default_construction_round_trips_through_to_dict(self) -> None:
        aa = AnalysisAssurance()
        d = aa.to_dict()
        assert d["schema_version"] == ANALYSIS_ASSURANCE_SCHEMA_VERSION
        assert d["status"] == "not_requested"
        assert d["requested_depth"] is None
        assert d["translation_units"] == {
            "selected": None,
            "parsed": None,
            "failed": None,
            "skipped": None,
        }
        assert d["export_accounting"] == {
            "total": None,
            "source_linked": None,
            "internal": None,
            "unaccounted": None,
        }
        assert d["target_accounting"] == {
            "requested": None,
            "resolved": None,
            "transitive_count": None,
        }
        assert d["notes"] == []

    def test_target_accounting_is_json_serializable_when_populated(self) -> None:
        ta = TargetAccounting(requested=("//foo:bar",), resolved=("//foo:bar",))
        d = ta.to_dict()
        json.dumps(d)  # must not raise
        assert d["requested"] == ["//foo:bar"]

    def test_tu_and_export_accounting_to_dict(self) -> None:
        tu = TranslationUnitAccounting(selected=10, parsed=8, failed=2)
        exp = ExportAccounting(total=5, source_linked=4, internal=1, unaccounted=1)
        assert tu.to_dict()["failed"] == 2
        assert exp.to_dict()["unaccounted"] == 1


class TestComputeAnalysisAssurance:
    """Direct unit tests of the pure rollup function."""

    def test_header_scoped_unchanged_pair_is_complete(self) -> None:
        old, new = _header_pair()
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.status == "complete"
        assert aa.effective_depth == "headers"
        assert aa.header_context_status == "clean"

    def test_elf_only_minimal_run_is_not_requested(self) -> None:
        old, new = _elf_only_pair()
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.status == "not_requested"
        assert aa.effective_depth == "binary"

    def test_diagnostic_comparison_mismatch_is_not_comparable(self) -> None:
        """A genuine ADR-050 comparability mismatch, waived through via
        --diagnostic-comparison, must read as not_comparable -- every other
        axis is unreliable once old/new aren't provably the same contract."""
        from abicheck.model import ExtractionContract

        old = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[_fn("pub_a", "_Z5pub_av")],
            from_headers=True,
            contract=ExtractionContract(
                scope_fingerprint="sha256:aaa", profile_fingerprint="sha256:ppp"
            ),
        )
        new = AbiSnapshot(
            library="libfoo.so.1",
            version="2.0",
            functions=[_fn("pub_a", "_Z5pub_av")],
            from_headers=True,
            contract=ExtractionContract(
                scope_fingerprint="sha256:bbb", profile_fingerprint="sha256:ppp"
            ),
        )
        result = checker.compare(old, new, diagnostic_comparison=True)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.status == "not_comparable"
        assert result.assurance == "none"

    def test_fact_set_inconsistent_l4_surface_is_failed(self) -> None:
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = _header_pair()
        old.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack"),
            source_abi=SourceAbiSurface(coverage={"fact_set_inconsistent": True}),
        )
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-new"),
            source_abi=SourceAbiSurface(),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.fact_set_comparability == "inconsistent"
        assert aa.status == "failed"
        assert any("fact_set_inconsistent" in n for n in aa.notes)

    def test_asymmetric_l4_surface_is_unknown_and_partial(self) -> None:
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = _header_pair()
        old.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-2"), source_abi=SourceAbiSurface()
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.fact_set_comparability == "unknown"
        assert aa.status == "partial"

    def test_header_parse_context_drift_is_surfaced_structurally(self) -> None:
        """Directly exercises the rollup over an already-built DiffResult
        carrying a header_parse_context_drift finding -- the structured
        header_context_status field, not just the prose finding."""
        old, new = _header_pair()
        drift_change = Change(
            kind=ChangeKind.HEADER_PARSE_CONTEXT_DRIFT,
            symbol="pub_a",
            description="parsed under a different build context",
        )
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so.1",
            changes=[drift_change],
            scope_resolved=True,
        )
        aa = compute_analysis_assurance(result, old, new)
        assert aa.header_context_status == "drift_detected"
        assert aa.status == "partial"

    def test_translation_unit_and_export_accounting_roll_up_l4_surface(self) -> None:
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = _header_pair()
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-3"),
            source_abi=SourceAbiSurface(
                coverage={
                    "compile_units_selected": 10,
                    "compile_units_parsed": 9,
                },
                roots={
                    "exported_symbols": ["_Z5pub_av", "_Z5pub_bv"],
                    "public_header_declarations": [],
                    "forced_public": [],
                },
                unmatched={
                    "symbols_without_decl": ["_Z5pub_bv"],
                    "decls_without_symbol": [],
                },
                mappings={
                    "source_decl_to_binary_symbol": {},
                    "source_type_to_debug_type": {},
                    "public_header_to_target": {},
                    "synthesized_symbol_to_owner": {},
                    "template_instantiation_symbol_to_decl": {},
                    "allocator_interposer_symbol_to_owner": {},
                    "non_public_symbol_to_reason": {"_Z6privXv": "internal"},
                },
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.translation_units.selected == 10
        assert aa.translation_units.parsed == 9
        assert aa.translation_units.failed == 1
        assert aa.export_accounting.total == 2
        assert aa.export_accounting.unaccounted == 1
        assert aa.export_accounting.source_linked == 1
        assert aa.export_accounting.internal == 1
        # A failed TU is exactly the kind of shortfall that keeps this run
        # from reading as unconditionally "complete".
        assert aa.status == "partial"


class TestAnalysisAssuranceExitFold:
    """The ``max``-based orthogonal fold, at the function level -- mirrors
    ``TestTheCoverageExitIsApplied`` in test_contract_coverage_exit.py."""

    def test_contribution_is_zero_when_not_required(self) -> None:
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo",
            analysis_assurance=AnalysisAssurance(status="failed"),
        )
        assert analysis_assurance_exit_contribution(result, require_complete=False) == 0

    def test_contribution_is_zero_when_status_is_complete(self) -> None:
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo",
            analysis_assurance=AnalysisAssurance(status="complete"),
        )
        assert analysis_assurance_exit_contribution(result, require_complete=True) == 0

    def test_contribution_is_one_when_required_and_not_complete(self) -> None:
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo",
            analysis_assurance=AnalysisAssurance(status="partial"),
        )
        assert analysis_assurance_exit_contribution(result, require_complete=True) == 1

    def test_fold_never_lowers_a_real_abi_break(self) -> None:
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo",
            analysis_assurance=AnalysisAssurance(status="failed"),
        )
        assert fold_analysis_assurance_exit(4, result, require_complete=True) == 4

    def test_fold_raises_a_clean_zero_to_one(self) -> None:
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo",
            analysis_assurance=AnalysisAssurance(status="not_comparable"),
        )
        assert fold_analysis_assurance_exit(0, result, require_complete=True) == 1

    def test_missing_analysis_assurance_contributes_nothing(self) -> None:
        """Defensive: a hand-built DiffResult with no analysis_assurance at
        all (e.g. an older in-memory object) must not crash the fold."""
        result = DiffResult(old_version="1.0", new_version="2.0", library="libfoo")
        assert fold_analysis_assurance_exit(0, result, require_complete=True) == 0


class TestAnalysisAssuranceCliIntegration:
    """End-to-end: the CLI always reports the block; the flag is what
    changes the exit code, and only when asked."""

    def test_json_report_always_carries_analysis_assurance(
        self, tmp_path: Path
    ) -> None:
        res = _compare(tmp_path, _header_pair(), "--format", "json")
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output[res.output.index("{") :])
        aa = payload["analysis_assurance"]
        assert aa["schema_version"] == ANALYSIS_ASSURANCE_SCHEMA_VERSION
        assert aa["status"] in ASSURANCE_STATUS_VALUES

    def test_complete_run_is_unaffected_by_the_flag(self, tmp_path: Path) -> None:
        res = _compare(tmp_path, _header_pair(), "--require-complete-analysis")
        assert res.exit_code == 0, res.output

    def test_flag_omitted_never_changes_the_exit_code(self, tmp_path: Path) -> None:
        """A run whose status is not 'complete' (the minimal ELF-only pair)
        must exit 0 exactly as it always did when the flag isn't passed --
        purely additive."""
        res = _compare(tmp_path, _elf_only_pair())
        assert res.exit_code == 0, res.output
        payload_res = _compare(tmp_path, _elf_only_pair(), "--format", "json")
        payload = json.loads(payload_res.output[payload_res.output.index("{") :])
        # Sanity: this fixture really is a non-"complete" case, so the next
        # assertion (flag raises it to 1) is actually testing something.
        assert payload["analysis_assurance"]["status"] != "complete"

    def test_flag_raises_a_clean_exit_to_one_on_incomplete_status(
        self, tmp_path: Path
    ) -> None:
        res = _compare(tmp_path, _elf_only_pair(), "--require-complete-analysis")
        assert res.exit_code == 1, res.output

    def test_flag_never_lowers_a_real_abi_break_end_to_end(
        self, tmp_path: Path
    ) -> None:
        res = _compare(tmp_path, _breaking_pair(), "--require-complete-analysis")
        assert res.exit_code == 4, res.output

    def test_flag_rejected_for_directory_release_compares(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--require-complete-analysis",
            ],
        )
        assert res.exit_code != 0
        assert "--require-complete-analysis" in res.output
