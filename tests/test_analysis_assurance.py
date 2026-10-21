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
the ``max`` discipline directly. Axis modules split out of this file:
``test_analysis_assurance_{context_asymmetry,graph_completeness,
pack_accounting,cli}.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from abicheck import checker
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import DiffResult
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.policy.analysis_assurance import (
    ANALYSIS_ASSURANCE_SCHEMA_VERSION,
    ASSURANCE_STATUS_VALUES,
    AnalysisAssurance,
    ExportAccounting,
    TargetAccounting,
    TranslationUnitAccounting,
    analysis_assurance_exit_contribution,
    compute_analysis_assurance,
)
from tests._analysis_assurance_support import (
    elf_only_pair,
    fn,
    folded,
    header_pair,
)


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
        old, new = header_pair()
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.status == "complete"
        assert aa.effective_depth == "headers"
        assert aa.header_context_status == "clean"

    def test_elf_only_minimal_run_is_not_requested(self) -> None:
        old, new = elf_only_pair()
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
            functions=[fn("pub_a", "_Z5pub_av")],
            from_headers=True,
            contract=ExtractionContract(
                scope_fingerprint="sha256:aaa", profile_fingerprint="sha256:ppp"
            ),
        )
        new = AbiSnapshot(
            library="libfoo.so.1",
            version="2.0",
            functions=[fn("pub_a", "_Z5pub_av")],
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

        old, new = header_pair()
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

        old, new = header_pair()
        old.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-2"), source_abi=SourceAbiSurface()
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.fact_set_comparability == "unknown"
        assert aa.status == "partial"

    def test_mismatched_fact_set_identity_is_not_comparable(self) -> None:
        """P1 review, Finding 1: two L4 surfaces that are each internally
        consistent (no fact_set_inconsistent on either side) but carry
        mutually DIFFERENT fact_set identities (name mismatch here) must not
        report fact_set_comparability="comparable", and status must not
        read "complete" -- mirroring diff_source_abi()'s own
        SOURCE_FACT_COVERAGE_INCOMPLETE cross-side check."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = header_pair()
        tu_coverage = {"compile_units_selected": 2, "compile_units_parsed": 2}
        old.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-fs-old"),
            source_abi=SourceAbiSurface(
                coverage={
                    **tu_coverage,
                    "fact_set": {"name": "clang-facts", "version": 1},
                }
            ),
        )
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-fs-new"),
            source_abi=SourceAbiSurface(
                coverage={
                    **tu_coverage,
                    "fact_set": {"name": "gcc-facts", "version": 1},
                }
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.fact_set_comparability != "comparable"
        assert aa.status != "complete"
        assert any("fact_set_name_mismatch" in n for n in aa.notes)

    def test_mismatched_fact_set_producer_is_not_fully_comparable(self) -> None:
        """P1 review, Finding 1 (softer variant): a differing producer
        between two otherwise-agreeing, internally-consistent fact_set
        identities is a real cross-side mismatch too (opaque-hash/content
        comparisons are not trustworthy across it) -- must not read
        "comparable" either, even though it's not the hard name/version
        mismatch that also fails the whole run."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = header_pair()
        tu_coverage = {"compile_units_selected": 2, "compile_units_parsed": 2}
        old.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-fs-prod-old"),
            source_abi=SourceAbiSurface(
                coverage={
                    **tu_coverage,
                    "fact_set": {
                        "name": "clang-facts",
                        "version": 1,
                        "producer": "clang-plugin",
                    },
                }
            ),
        )
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-fs-prod-new"),
            source_abi=SourceAbiSurface(
                coverage={
                    **tu_coverage,
                    "fact_set": {
                        "name": "clang-facts",
                        "version": 1,
                        "producer": "gcc-plugin",
                    },
                }
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.fact_set_comparability == "unknown"
        assert aa.status != "complete"
        assert any("producer_mismatch" in n for n in aa.notes)

    def test_matching_fact_set_identity_stays_comparable(self) -> None:
        """Sanity check for the Finding 1 fix's negative case: two sides
        with the identical fact_set identity must still read
        "comparable"/"complete" -- the fix must not over-trigger on a
        genuinely matching pair."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = header_pair()
        same_fact_set = {
            "name": "clang-facts",
            "version": 1,
            "producer": "clang-plugin",
            "producer_version": "1.0",
            "compiler_version": "18.0",
            "compiler_family": "clang",
        }
        tu_coverage = {"compile_units_selected": 2, "compile_units_parsed": 2}
        old.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-fs-match-old"),
            source_abi=SourceAbiSurface(
                coverage={**tu_coverage, "fact_set": dict(same_fact_set)}
            ),
        )
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-fs-match-new"),
            source_abi=SourceAbiSurface(
                coverage={**tu_coverage, "fact_set": dict(same_fact_set)}
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.fact_set_comparability == "comparable"
        assert aa.status == "complete"

    def test_header_parse_context_drift_is_surfaced_structurally(self) -> None:
        """Directly exercises the rollup over an already-built DiffResult
        carrying a header_parse_context_drift finding -- the structured
        header_context_status field, not just the prose finding."""
        old, new = header_pair()
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

        old, new = header_pair()
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
                    # "_Z5pub_av" is classified (internal, below) so it no
                    # longer belongs here too -- the old fixture named an
                    # unrelated "_Z6privXv" not even in exported_symbols,
                    # a shape link_source_abi can't actually produce.
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
                    "non_public_symbol_to_reason": {"_Z5pub_av": "internal"},
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
        assert aa.export_accounting.internal == 1
        # Both exports accounted for (one internal, one unaccounted), so
        # nothing is left as genuinely source-linked (Codex review, #788).
        assert aa.export_accounting.source_linked == 0
        # A failed TU is exactly the kind of shortfall that keeps this run
        # from reading as unconditionally "complete".
        assert aa.status == "partial"

    def test_unaccounted_exports_alone_are_partial_not_complete(self) -> None:
        """P1 review, Finding 2: every TU parsed cleanly (selected == parsed,
        so ``translation_units.failed`` is 0) but at least one exported
        symbol could not be associated with a source declaration
        (``export_accounting.unaccounted > 0``). None of the pre-existing
        predicates noticed this on its own -- the L4 row stays ``PRESENT``
        on the manifest (only downgraded when *no* exports match at all),
        so this fell through to ``status="complete"`` even though source-
        level analysis could not account for every exported entry point."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = header_pair()
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-unaccounted"),
            source_abi=SourceAbiSurface(
                coverage={
                    "compile_units_selected": 5,
                    "compile_units_parsed": 5,
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
                    "non_public_symbol_to_reason": {},
                },
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.translation_units.failed == 0
        assert aa.export_accounting.unaccounted == 1
        assert aa.status == "partial"
        assert any("unaccounted" in n for n in aa.notes)

    def test_require_complete_analysis_exits_nonzero_for_unaccounted_exports(
        self,
    ) -> None:
        """Function-level check that the ``--require-complete-analysis``
        exit floor actually reacts to this -- mirrors this file's other
        ``analysis_assurance_exit_contribution``-level checks rather than a
        full CLI round-trip, since ``build_source`` is attached directly to
        the in-memory snapshot here (not via a CLI flag)."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = header_pair()
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-unaccounted-2"),
            source_abi=SourceAbiSurface(
                coverage={"compile_units_selected": 3, "compile_units_parsed": 3},
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
                    "non_public_symbol_to_reason": {},
                },
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.status == "partial"
        assert aa.export_accounting.unaccounted == 1
        from abicheck.policy.analysis_assurance import (
            analysis_assurance_exit_contribution,
        )

        assert analysis_assurance_exit_contribution(result, require_complete=True) == 1
        assert analysis_assurance_exit_contribution(result, require_complete=False) == 0

    def test_partial_contract_coverage_is_partial_not_complete(self) -> None:
        """Exactly one side carrying an ExtractionContract axis means it
        could never be checked: the run reads partial, with the reason named."""
        old, new = header_pair()
        result = checker.compare(old, new)
        result.contract_coverage = "partial"
        aa = compute_analysis_assurance(result, old, new)
        assert aa.status == "partial", aa
        assert any("contract_coverage is 'partial'" in n for n in aa.notes), aa.notes


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
        assert folded(4, result) == 4

    def test_fold_raises_a_clean_zero_to_one(self) -> None:
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo",
            analysis_assurance=AnalysisAssurance(status="not_comparable"),
        )
        assert folded(0, result) == 1

    def test_missing_analysis_assurance_contributes_nothing(self) -> None:
        """Defensive: a hand-built DiffResult with no analysis_assurance at
        all (e.g. an older in-memory object) must not crash the fold."""
        result = DiffResult(old_version="1.0", new_version="2.0", library="libfoo")
        assert folded(0, result) == 0
