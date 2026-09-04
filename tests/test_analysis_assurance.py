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

    def test_mismatched_fact_set_identity_is_not_comparable(self) -> None:
        """P1 review, Finding 1: two L4 surfaces that are each internally
        consistent (no fact_set_inconsistent on either side) but carry
        mutually DIFFERENT fact_set identities (name mismatch here) must not
        report fact_set_comparability="comparable", and status must not
        read "complete" -- mirroring diff_source_abi()'s own
        SOURCE_FACT_COVERAGE_INCOMPLETE cross-side check."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = _header_pair()
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

        old, new = _header_pair()
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

        old, new = _header_pair()
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

        old, new = _header_pair()
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

        old, new = _header_pair()
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
        from abicheck.analysis_assurance import (
            analysis_assurance_exit_contribution,
        )

        assert analysis_assurance_exit_contribution(result, require_complete=True) == 1
        assert analysis_assurance_exit_contribution(result, require_complete=False) == 0


class TestHeaderContextAsymmetry:
    """P1 review (finding: one-sided header evidence): ``header_context_status``
    previously used ``or`` to decide header evidence was "present" -- so a
    header-snapshot-vs-ELF-only comparison read as fully evaluated (no drift
    finding to raise, since the ELF-only side has no headers to drift
    against), even though only half the comparison actually has header/API
    evidence. Must report the asymmetry explicitly and never read
    ``status="complete"`` for it."""

    def _asymmetric_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        fns = [_fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0", library="libfoo.so.1", functions=fns, from_headers=True
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            elf_only_mode=True,
        )
        return old, new

    def test_one_sided_header_evidence_is_reported_as_asymmetric(self) -> None:
        old, new = self._asymmetric_pair()
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.header_context_status == "asymmetric"
        assert aa.status != "complete"
        assert any("asymmetric" in n for n in aa.notes)

    def test_require_complete_analysis_exits_nonzero_for_one_sided_headers(
        self, tmp_path: Path
    ) -> None:
        old, new = self._asymmetric_pair()
        res = _compare(
            tmp_path,
            (old, new),
            "--no-scope-public-headers",
            "--require-complete-analysis",
        )
        assert res.exit_code != 0, res.output


class TestDwarfContextAsymmetry:
    """P1 review, Finding 1: ``confidence._detect_evidence_tiers()`` combines
    both sides' DWARF availability with OR semantics when promoting the
    aggregate ``result.evidence_tier`` to ``DWARF_AWARE``, so a comparison
    where only one side carries usable DWARF still read as DWARF-aware
    overall -- with no per-side check anywhere in this rollup, that silently
    made ``nothing_requested`` false without any partial predicate tripping,
    so the run fell through to ``status="complete"`` even though the real
    DWARF-based layout detectors (``diff_platform.py``,
    ``dwarf_advanced.diff_advanced_dwarf``) explicitly skip their own
    comparison whenever either side lacks DWARF -- struct/enum layout
    changes on the DWARF-lacking side could be silently missed."""

    def _dwarf_asymmetric_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        from abicheck.dwarf_metadata import DwarfMetadata

        fns = [_fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=True),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=False),
        )
        return old, new

    def test_one_sided_dwarf_is_reported_as_asymmetric(self) -> None:
        old, new = self._dwarf_asymmetric_pair()
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.dwarf_context_status == "asymmetric"
        assert aa.status != "complete"
        assert any("DWARF" in n and "asymmetric" in n for n in aa.notes)

    def test_symmetric_dwarf_on_both_sides_is_clean(self) -> None:
        from abicheck.dwarf_metadata import DwarfMetadata

        fns = [_fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=True),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=True),
        )
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert aa.dwarf_context_status == "clean"

    def test_require_complete_analysis_exits_nonzero_for_one_sided_dwarf(
        self, tmp_path: Path
    ) -> None:
        old, new = self._dwarf_asymmetric_pair()
        res = _compare(
            tmp_path,
            (old, new),
            "--no-scope-public-headers",
            "--require-complete-analysis",
        )
        assert res.exit_code != 0, res.output


class TestL0ContextAsymmetry:
    """Self-audit finding (P0.4 review): the same one-sided-evidence pattern
    already fixed for L1 (DWARF)/L2 (headers)/L3 (build evidence) also
    applies to L0 -- a comparison where only one side carries a real binary
    export table (the other a synthetic/snapshot-only input,
    ``cli_scan_helpers._intrinsic_coverage``'s own documented "no binary
    export table (snapshot-only input)" state) previously fell through to
    ``status="complete"`` with nothing checking L0 presence by side."""

    def _asymmetric_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        from abicheck.elf_metadata import ElfMetadata

        fns = [_fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            elf=ElfMetadata(soname="libfoo.so.1"),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            elf=None,
        )
        return old, new

    def test_one_sided_binary_evidence_is_reported_as_asymmetric(self) -> None:
        old, new = self._asymmetric_pair()
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.l0_context_status == "asymmetric"
        assert aa.status != "complete"
        assert any("L0" in n and "asymmetric" in n for n in aa.notes)

    def test_symmetric_binary_presence_on_both_sides_is_clean(self) -> None:
        from abicheck.elf_metadata import ElfMetadata

        fns = [_fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            elf=ElfMetadata(soname="libfoo.so.1"),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            elf=ElfMetadata(soname="libfoo.so.1"),
        )
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert aa.l0_context_status == "clean"

    def test_symmetric_absence_is_not_evaluated_not_asymmetric(self) -> None:
        """Neither side carrying a binary (a pure header/source-only
        comparison) is a legitimate, symmetric shape, not an asymmetry."""
        old, new = _header_pair()
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.l0_context_status == "not_evaluated"

    def test_require_complete_analysis_exits_nonzero_for_one_sided_binary(
        self, tmp_path: Path
    ) -> None:
        old, new = self._asymmetric_pair()
        res = _compare(
            tmp_path,
            (old, new),
            "--no-scope-public-headers",
            "--require-complete-analysis",
        )
        assert res.exit_code != 0, res.output


class TestL3ContextAsymmetry:
    """P1 review, Finding 3: when the baseline carries L3 ``BuildEvidence``
    but the target has none, ``prepare_embedded_build_source()`` already
    emits a prose-only ``layer_coverage_asymmetric`` finding -- but nothing
    in this rollup's own predicates examined L3 presence by side, so an
    otherwise-matching comparison fell through to ``status="complete"``."""

    def _asymmetric_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        from abicheck.buildsource.build_evidence import BuildEvidence
        from abicheck.buildsource.pack import BuildSourcePack

        old, new = _header_pair()
        old.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-l3-old"),
            build_evidence=BuildEvidence(),
        )
        return old, new

    def test_one_sided_l3_evidence_is_reported_as_asymmetric(self) -> None:
        old, new = self._asymmetric_pair()
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.l3_context_status == "asymmetric"
        assert aa.status != "complete"
        assert any("L3" in n and "asymmetric" in n for n in aa.notes)

    def test_symmetric_l3_evidence_on_both_sides_is_clean(self) -> None:
        from abicheck.buildsource.build_evidence import BuildEvidence
        from abicheck.buildsource.pack import BuildSourcePack

        old, new = _header_pair()
        old.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-l3-both-old"),
            build_evidence=BuildEvidence(),
        )
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-l3-both-new"),
            build_evidence=BuildEvidence(),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.l3_context_status == "clean"

    def test_require_complete_analysis_exits_nonzero_for_one_sided_l3(
        self,
    ) -> None:
        """Function-level check (mirrors this file's other
        analysis_assurance_exit_contribution-level checks) rather than a
        full CLI round-trip, since ``build_source`` is attached directly to
        the in-memory snapshot here (not via a CLI flag)."""
        old, new = self._asymmetric_pair()
        result = checker.compare(old, new)
        assert analysis_assurance_exit_contribution(result, require_complete=True) == 1
        assert analysis_assurance_exit_contribution(result, require_complete=False) == 0


class TestTargetAccounting:
    """P1 review, Finding 2: P0.2's Bazel root-target resolution
    (``BuildEvidence.target_scope``) has landed, but ``target_accounting``
    was left permanently empty pending it -- hiding a genuine partial
    resolution (e.g. two requested Bazel roots with only one resolved) with
    no predicate downgrading the overall status for the gap."""

    def test_fully_resolved_target_scope_is_populated_not_partial_for_it(
        self,
    ) -> None:
        from abicheck.buildsource.build_evidence import BuildEvidence, TargetScope
        from abicheck.buildsource.pack import BuildSourcePack

        old, new = _header_pair()
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-targets-full"),
            build_evidence=BuildEvidence(
                target_scope=TargetScope(
                    requested=["//foo:bar", "//foo:baz"],
                    resolved=["//foo:bar", "//foo:baz"],
                    transitive_count=12,
                )
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.target_accounting.requested == ("//foo:bar", "//foo:baz")
        assert aa.target_accounting.resolved == ("//foo:bar", "//foo:baz")
        assert aa.target_accounting.transitive_count == 12

    def test_two_requested_one_resolved_is_partial_not_complete(self) -> None:
        """The exact shape named in the review finding: two requested Bazel
        roots, only one resolved (a typo'd target label, say)."""
        from abicheck.buildsource.build_evidence import BuildEvidence, TargetScope
        from abicheck.buildsource.pack import BuildSourcePack

        old, new = _header_pair()
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-targets-partial"),
            build_evidence=BuildEvidence(
                target_scope=TargetScope(
                    requested=["//foo:bar", "//foo:typo"],
                    resolved=["//foo:bar"],
                )
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.target_accounting.requested == ("//foo:bar", "//foo:typo")
        assert aa.target_accounting.resolved == ("//foo:bar",)
        assert aa.status != "complete"
        assert any("did not all resolve" in n for n in aa.notes)

    def test_no_target_scope_requested_stays_none_not_empty(self) -> None:
        """When neither side requested root-target scoping at all,
        target_accounting must stay the documented "not requested" None
        state, not an empty tuple."""
        old, new = _header_pair()
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.target_accounting.requested is None
        assert aa.target_accounting.resolved is None

    def test_require_complete_analysis_exits_nonzero_for_partial_targets(
        self,
    ) -> None:
        from abicheck.buildsource.build_evidence import BuildEvidence, TargetScope
        from abicheck.buildsource.pack import BuildSourcePack

        old, new = _header_pair()
        new.build_source = BuildSourcePack(
            root=Path("/tmp/nonexistent-pack-targets-cli"),
            build_evidence=BuildEvidence(
                target_scope=TargetScope(
                    requested=["//foo:bar", "//foo:typo"],
                    resolved=["//foo:bar"],
                )
            ),
        )
        result = checker.compare(old, new)
        assert analysis_assurance_exit_contribution(result, require_complete=True) == 1
        assert analysis_assurance_exit_contribution(result, require_complete=False) == 0


class TestGraphCompleteness:
    """P1 review (finding 2): ``graph_completeness`` previously only ever
    looked at ``degraded_passes`` and defaulted to ``"complete"`` for every
    other state -- silently treating a narrowed-scope pass, absent pass
    coverage, and old/new graph asymmetry as full coverage."""

    def _pack_with_graph(self, tmp_path: Path, name: str, graph) -> object:
        from abicheck.buildsource.pack import BuildSourcePack

        return BuildSourcePack(root=tmp_path / name, source_graph=graph)

    def test_narrowed_pass_is_not_complete(self, tmp_path: Path) -> None:
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        graph = SourceGraphSummary(narrowed_passes={"call_graph": True})
        old.build_source = self._pack_with_graph(tmp_path, "old_pack", graph)
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(narrowed_passes={"call_graph": True}),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "narrowed"
        assert aa.status == "partial"
        assert any("narrowed" in n for n in aa.notes)

    def test_missing_pass_coverage_is_unknown_not_complete(
        self, tmp_path: Path
    ) -> None:
        """A graph with no extractor_passes/narrowed_passes recorded at all
        (e.g. a hand-built or pre-slice-2 graph) must not default to
        "complete" -- which parts of the project it covers is unknown."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path, "old_pack", SourceGraphSummary()
        )
        new.build_source = self._pack_with_graph(
            tmp_path, "new_pack", SourceGraphSummary()
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "unknown"
        assert aa.status == "partial"

    def test_asymmetric_graph_presence_is_unknown_not_complete(
        self, tmp_path: Path
    ) -> None:
        """Only one side carries an L5 graph -- the comparison never
        examined the other side's implementation at all."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        assert new.build_source is None
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "unknown"
        assert aa.status == "partial"
        assert any("only" in n or "unknown" in n for n in aa.notes)

    def test_full_confirmed_passes_on_both_sides_is_complete(
        self, tmp_path: Path
    ) -> None:
        """The positive case: both sides ran a confirmed full-project pass,
        clean -- graph_completeness must still read complete."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "complete"
        assert aa.status == "complete"

    def test_asymmetric_confirmed_family_coverage_is_not_complete(
        self, tmp_path: Path
    ) -> None:
        """P1 review, round 8: both sides can individually be "confirmed
        complete" on a nonempty extractor_passes mapping while covering
        DIFFERENT pass families entirely -- an old header-only graph
        confirmed on header_call_graph/header_type_graph vs. a new
        build-integrated graph confirmed on call_graph/type_graph. The
        cross-snapshot graph diff only ever compares edges within a shared
        family, so a comparison with no overlapping confirmed family must
        not read as "complete"."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(
                extractor_passes={
                    "header_call_graph": True,
                    "header_type_graph": True,
                }
            ),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness != "complete"
        assert aa.graph_completeness == "unknown"
        assert aa.status == "partial"
        assert any("confirmed on only one side" in n for n in aa.notes)

    def test_overlapping_confirmed_family_on_same_name_is_still_complete(
        self, tmp_path: Path
    ) -> None:
        """Companion to the asymmetric-family test above: both sides
        confirmed complete on the SAME family name must still read
        "complete" -- the new overlap check must not regress the ordinary,
        matching-family case."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(
                extractor_passes={"call_graph": True, "type_graph": True}
            ),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "complete"
        assert aa.status == "complete"

    def test_degraded_pass_still_reads_degraded_not_narrowed_or_unknown(
        self, tmp_path: Path
    ) -> None:
        """Regression guard: degraded_passes must keep taking priority over
        the new narrowed/unknown states, not be masked by them."""
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = self._pack_with_graph(
            tmp_path,
            "old_pack",
            SourceGraphSummary(degraded_passes={"call_graph": True}),
        )
        new.build_source = self._pack_with_graph(
            tmp_path,
            "new_pack",
            SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "degraded"
        assert aa.status == "partial"

    def test_partial_l5_extractor_record_is_not_complete(self, tmp_path: Path) -> None:
        """P1 review (finding: include L5 extractor failures in assurance).
        A manifest recording one successful L5 graph extractor
        (``call_graph:clang``) alongside a sibling that is ``partial``
        (``type_graph:clang``) must not read as complete just because every
        boolean extractor_passes/narrowed_passes/degraded_passes flag on the
        SourceGraphSummary itself happens to be clean."""
        from abicheck.buildsource.model import ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = BuildSourcePack(
            root=tmp_path / "old_pack",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        old.build_source.manifest.extractors = [
            ExtractorRecord(name="call_graph:clang", status="ok"),
            ExtractorRecord(
                name="type_graph:clang", status="partial", detail="0 type edges"
            ),
        ]
        new.build_source = BuildSourcePack(
            root=tmp_path / "new_pack",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        new.build_source.manifest.extractors = [
            ExtractorRecord(name="call_graph:clang", status="ok"),
            ExtractorRecord(name="type_graph:clang", status="ok"),
        ]
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "degraded"
        assert aa.status == "partial"
        assert any("type_graph:clang" in n and "partial" in n for n in aa.notes)

    def test_failed_l5_extractor_record_is_not_complete(self, tmp_path: Path) -> None:
        from abicheck.buildsource.model import ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = BuildSourcePack(
            root=tmp_path / "old_pack2",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        old.build_source.manifest.extractors = [
            ExtractorRecord(
                name="include_graph:clang", status="failed", detail="clang++ not found"
            ),
        ]
        new.build_source = BuildSourcePack(
            root=tmp_path / "new_pack2",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "degraded"
        assert aa.status == "partial"


class TestConfirmedZeroEdgeGraphPassIsNotDegraded:
    """P2 review, Finding 3: a real producer
    (``buildsource/inline_graph_fold.py``'s ``fold_call_graph``/
    ``fold_type_graph``/... family) stamps its own ``ExtractorRecord.status``
    as ``"ok" if added else "partial"`` -- keyed on whether the pass found
    any edges, not on whether it examined everything it was asked to. A
    clean, full-project pass over a simple library with no calls/overrides/
    templates/etc. to discover legitimately records ``status="partial"``
    while *also* stamping ``SourceGraphSummary.extractor_passes[family] =
    True`` (confirmed full coverage) unconditionally on success. Treating
    every non-``"ok"`` record as a shortfall -- the fix this session
    previously landed for a *different*, real gap (an L5 extractor whose
    family the ``SourceGraphSummary`` booleans said nothing about at all) --
    regressed this legitimate case into a false ``"degraded"``/``"partial"``
    reading. Both directions are tested here so a future change cannot
    silently re-break either one.
    """

    def test_confirmed_full_pass_with_zero_edges_reads_complete(
        self, tmp_path: Path
    ) -> None:
        """(b) A confirmed, zero-edge, fully-executed pass -- the exact
        shape ``inline_graph_fold.py`` really produces for an edge-free
        project -- must read as complete, not degraded."""
        from abicheck.buildsource.model import ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        for side, root in (("old", "confirmed_old"), ("new", "confirmed_new")):
            pack = BuildSourcePack(
                root=tmp_path / root,
                source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
            )
            pack.manifest.extractors = [
                ExtractorRecord(
                    name="call_graph:clang",
                    status="partial",
                    detail="0 call edges from 3 compile units",
                ),
            ]
            setattr(old if side == "old" else new, "build_source", pack)
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "complete", aa.notes
        assert aa.status == "complete"
        assert not any("call_graph:clang" in n for n in aa.notes)

    def test_confirmed_narrowed_pass_with_partial_status_is_narrowed(
        self, tmp_path: Path
    ) -> None:
        """A confirmed *narrowed* (not full-project) zero-edge pass must
        still read as ``"narrowed"`` -- not silently promoted to
        ``"complete"``, and not double-counted into ``"degraded"`` either."""
        from abicheck.buildsource.model import ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        for side, root in (("old", "narrowed_old"), ("new", "narrowed_new")):
            pack = BuildSourcePack(
                root=tmp_path / root,
                source_graph=SourceGraphSummary(narrowed_passes={"call_graph": True}),
            )
            pack.manifest.extractors = [
                ExtractorRecord(
                    name="call_graph:clang",
                    status="partial",
                    detail="0 call edges from 1 compile unit (narrowed)",
                ),
            ]
            setattr(old if side == "old" else new, "build_source", pack)
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "narrowed"
        assert aa.status == "partial"
        assert not any("call_graph:clang" in n for n in aa.notes)

    def test_unconfirmed_partial_record_still_reads_degraded(
        self, tmp_path: Path
    ) -> None:
        """(a) The P1 finding this session's own prior fix addressed must
        stay fixed: a partial/failed L5 extractor record whose family is
        NOT separately confirmed by extractor_passes/narrowed_passes (a
        genuine shortfall -- e.g. a per-TU crash that stopped short of
        setting either) must still fold into degraded/partial, never
        silently pass as complete."""
        from abicheck.buildsource.model import ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import SourceGraphSummary

        old, new = _header_pair()
        old.build_source = BuildSourcePack(
            root=tmp_path / "shortfall_old",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        old.build_source.manifest.extractors = [
            ExtractorRecord(name="call_graph:clang", status="ok"),
            ExtractorRecord(
                name="type_graph:clang",
                status="partial",
                detail="crashed on 2/10 TUs",
            ),
        ]
        new.build_source = BuildSourcePack(
            root=tmp_path / "shortfall_new",
            source_graph=SourceGraphSummary(extractor_passes={"call_graph": True}),
        )
        new.build_source.manifest.extractors = [
            ExtractorRecord(name="call_graph:clang", status="ok"),
            ExtractorRecord(name="type_graph:clang", status="ok"),
        ]
        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert aa.graph_completeness == "degraded"
        assert aa.status == "partial"
        assert any("type_graph:clang" in n and "partial" in n for n in aa.notes)


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

    def test_json_report_persists_the_exit_contribution_for_aggregate(
        self, tmp_path: Path
    ) -> None:
        """`abicheck aggregate` reads `analysis_assurance_exit_contribution` directly
        rather than recomputing it (Codex review, PR #780) -- a real `compare` invocation must actually write it, end to end."""
        res = _compare(
            tmp_path,
            _elf_only_pair(),
            "--require-complete-analysis",
            "--format",
            "json",
        )
        assert res.exit_code == 1, res.output
        # The floor diagnostic is echoed to stderr (see
        # test_the_floor_diagnostic_is_echoed_to_stderr's sibling in
        # test_scan_analysis_assurance.py) -- parse stdout alone so it
        # doesn't corrupt the JSON payload.
        payload = json.loads(res.stdout[res.stdout.index("{") :])
        assert payload["analysis_assurance_exit_contribution"] == 1

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


# ``scan --against --require-complete-analysis``'s own CLI-integration
# tests moved to tests/test_scan_analysis_assurance.py (this file is at the
# AGENTS.md 2000-line hard cap; a sibling module is the established split
# pattern, not a second copy of the fixtures below).


class TestAnalysisAssuranceOutOfBandPack:
    """P1 review (finding 1): ``--build-info``/``--sources`` point ``compare``
    at an out-of-band pack directory that ``_resolve_side_pack`` uses for the
    run's real findings and coverage *without* ever attaching it back onto
    ``old``/``new``. Before the fix, ``analysis_assurance`` was computed from
    the bare snapshots alone (which carry no embedded ``build_source`` here),
    so a genuinely partial/failed out-of-band pack was invisible to it --
    ``status`` could read ``"complete"`` and ``--require-complete-analysis``
    would exit 0 despite the real evidence being incomplete. Regression
    coverage mirrors ``TestEvidenceDepthOutOfBandPack`` in
    ``tests/test_reporter_side_facts.py``, the existing test for the identical class of
    gap on the ``old_evidence_depth``/``new_evidence_depth`` JSON fields.
    """

    def _write_partial_pack(self, tmp_path: Path, name: str) -> Path:
        """A real, on-disk out-of-band pack whose L4 source-ABI surface is
        genuinely partial: 10 compile units selected, only 3 parsed."""
        from abicheck.buildsource import BuildSourcePack, pack_io
        from abicheck.buildsource.source_abi import SourceAbiSurface

        pack_dir = tmp_path / name
        pack = BuildSourcePack(
            root=pack_dir,
            source_abi=SourceAbiSurface(
                coverage={
                    "compile_units_selected": 10,
                    "compile_units_parsed": 3,
                },
            ),
        )
        pack_io.write(pack)
        return pack_dir

    def test_out_of_band_pack_partial_evidence_is_not_complete(
        self, tmp_path: Path
    ) -> None:
        old, new = _header_pair()
        # Neither snapshot carries an embedded build_source -- the only
        # evidence of incompleteness is the out-of-band pack directory.
        assert old.build_source is None
        assert new.build_source is None
        old_p, new_p = _write(tmp_path, old, new)
        pack_dir = self._write_partial_pack(tmp_path, "old_pack")

        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--build-info",
                "old=" + str(pack_dir),
                "--format",
                "json",
            ],
        )
        assert res.exit_code in (0, 1, 2, 4), res.output
        payload = json.loads(res.output[res.output.index("{") :])
        aa = payload["analysis_assurance"]
        # Finding 1's actual claim: the *out-of-band* pack's incompleteness
        # (7 of 10 compile units never parsed) must be visible here, even
        # though it was never attached to old.build_source/new.build_source.
        assert aa["status"] != "complete", aa
        assert aa["translation_units"]["failed"] == 7, aa
        assert aa["translation_units"]["selected"] == 10, aa

    def test_require_complete_analysis_exits_nonzero_for_out_of_band_partial_pack(
        self, tmp_path: Path
    ) -> None:
        old, new = _header_pair()
        old_p, new_p = _write(tmp_path, old, new)
        pack_dir = self._write_partial_pack(tmp_path, "old_pack")

        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--build-info",
                "old=" + str(pack_dir),
                "--require-complete-analysis",
            ],
        )
        # This is the whole point of --require-complete-analysis: a partial
        # out-of-band pack must not be able to exit 0.
        assert res.exit_code != 0, res.output

    def test_out_of_band_pack_beats_absent_embedded_snapshot_directly(self) -> None:
        """Direct unit-level check of the recomputation itself (bypassing the
        CLI): compute_analysis_assurance must reflect the pack passed via
        old_pack/new_pack, not whatever old.build_source/new.build_source
        happen to carry (nothing, here)."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = _header_pair()
        assert old.build_source is None
        assert new.build_source is None
        result = checker.compare(old, new)
        # Sanity: with no pack at all, the bare rollup reads complete.
        assert result.analysis_assurance.status == "complete"

        pack = BuildSourcePack(
            root=Path("/tmp/nonexistent-out-of-band-pack"),
            source_abi=SourceAbiSurface(
                coverage={
                    "compile_units_selected": 10,
                    "compile_units_parsed": 3,
                },
            ),
        )
        aa = compute_analysis_assurance(result, old, new, old_pack=pack)
        assert aa.status != "complete"
        assert aa.translation_units.failed == 7


class TestAnalysisAssurancePartialManifestLayer:
    """New review finding (P1): both sides carry an L4 pack whose extraction
    failed before producing any TU counts -- e.g.
    ``inline._run_inline_source_abi()``'s clang-unavailable path, which
    returns a bare ``SourceAbiSurface()`` (present, but with an empty
    ``coverage`` dict) and records the pack's own manifest L4 coverage row as
    ``partial`` (``build_inline_coverage``'s ``any_entities`` check). Before
    this fix, ``compute_analysis_assurance`` never looked at the manifest at
    all: both sides read as fact-set ``comparable`` (no
    ``fact_set_inconsistent`` flag -- there's no coverage dict to carry one),
    ``translation_units.failed`` stayed ``None`` (nothing to subtract, since
    ``selected``/``parsed`` were never set rather than zero), and status fell
    through to ``"complete"`` -- letting ``--require-complete-analysis`` exit
    0 even though L4 extraction failed entirely on both sides.
    """

    def _failed_l4_pack(self, tmp_path: Path, name: str):
        from abicheck.buildsource.model import (
            BuildSourceManifest,
            CoverageStatus,
            DataLayer,
            LayerCoverage,
        )
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        manifest = BuildSourceManifest(
            coverage=[
                LayerCoverage(
                    layer=DataLayer.L4_SOURCE_ABI.value,
                    status=CoverageStatus.PARTIAL,
                    detail="clang not found in PATH; source-only checks disabled",
                ),
            ],
        )
        return BuildSourcePack(
            root=tmp_path / name,
            manifest=manifest,
            # Mirrors inline._run_inline_source_abi()'s own clang-unavailable
            # return value verbatim: present, but with no real facts and no
            # TU accounting at all.
            source_abi=SourceAbiSurface(),
        )

    def test_both_sides_partial_l4_manifest_is_not_complete(
        self, tmp_path: Path
    ) -> None:
        old, new = _header_pair()
        old.build_source = self._failed_l4_pack(tmp_path, "old_pack")
        new.build_source = self._failed_l4_pack(tmp_path, "new_pack")

        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        # The manifest's own partial L4 row must be visible in the rollup...
        assert aa.status != "complete", aa
        assert any("partial" in n for n in aa.notes), aa.notes

    def test_require_complete_analysis_exits_nonzero_for_partial_l4_manifest(
        self, tmp_path: Path
    ) -> None:
        old, new = _header_pair()
        old.build_source = self._failed_l4_pack(tmp_path, "old_pack")
        new.build_source = self._failed_l4_pack(tmp_path, "new_pack")
        old_p, new_p = _write(tmp_path, old, new)

        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--require-complete-analysis",
            ],
        )
        assert res.exit_code != 0, res.output

    def test_l4_present_without_any_tu_accounting_is_flagged_directly(self) -> None:
        """Unit-level check of the second, independent signal this fix adds:
        an L4 surface present on a side with no compile_units_selected/
        compile_units_parsed keys at all (not zero -- simply absent), even
        with no manifest attached, must not read as complete."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = _header_pair()
        pack = BuildSourcePack(
            root=Path("/tmp/nonexistent-empty-l4-pack"),
            source_abi=SourceAbiSurface(),
        )
        result = checker.compare(old, new)
        aa = compute_analysis_assurance(result, old, new, old_pack=pack, new_pack=pack)
        assert aa.status != "complete"
        assert any("no translation-unit accounting" in n for n in aa.notes), aa.notes


class TestScopedExitFloorAppliedBeforeRendering:
    """New review finding (P2): the ``--used-by``/``--required-symbol`` scoped-
    exit path used to render both the primary and secondary reports from the
    *pre-floor* ``result.scoped_exit_code`` and only fold the coverage/
    analysis-assurance floors into a local variable afterward, right before
    ``sys.exit`` -- so a rendered SARIF/JUnit/JSON artifact could show a
    passing ``gateExitCode``/``scoped_exit_code`` of 0 while the CLI process
    itself exited 1 under ``--require-complete-analysis``. The fix folds both
    floors into ``result.scoped_exit_code`` itself, before any report is
    rendered, so every renderer -- present or future -- reads the one
    authoritative, already-floored value.
    """

    @staticmethod
    def _pair(tmp_path: Path) -> tuple[Path, Path]:
        old, new = _elf_only_pair()
        return _write(tmp_path, old, new)

    def test_sarif_gate_exit_code_matches_process_exit_code(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = self._pair(tmp_path)
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--required-symbol",
                "_Z5pub_av",
                "--format",
                "sarif",
                "--require-complete-analysis",
            ],
        )
        # This is the crux of the finding: the flag must be able to fail the
        # process even though the *scoped* verdict itself is compatible (the
        # ELF-only fixture's incompleteness is what --require-complete-analysis
        # is catching, not a real ABI break).
        assert res.exit_code == 1, res.output
        out = res.output
        doc = json.loads(out[out.index("{") :])
        props = doc["runs"][0]["properties"]
        scoped_gate = props["scopedGate"]
        # The rendered artifact's own exit-code fields must agree with the
        # real process exit code -- not the pre-floor value.
        assert scoped_gate["gateExitCode"] == res.exit_code
        assert scoped_gate["scopedExitCode"] == res.exit_code
        assert doc["runs"][0]["invocations"][0]["exitCode"] == res.exit_code

    def test_junit_gate_exit_code_matches_process_exit_code(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = self._pair(tmp_path)
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--required-symbol",
                "_Z5pub_av",
                "--format",
                "junit",
                "--require-complete-analysis",
            ],
        )
        assert res.exit_code == 1, res.output
        assert f'name="abicheck.gate_exit_code" value="{res.exit_code}"' in res.output
        assert f'name="abicheck.scoped_exit_code" value="{res.exit_code}"' in res.output

    def test_assurance_floor_diagnostic_is_emitted_on_the_scoped_path(
        self, tmp_path: Path
    ) -> None:
        """Before the fix, this path skipped ``assurance_floor_diagnostic()``
        entirely -- the stderr explanation of *why* the exit code was raised
        never appeared for a scoped run."""
        old_p, new_p = self._pair(tmp_path)
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--required-symbol",
                "_Z5pub_av",
                "--require-complete-analysis",
            ],
        )
        assert res.exit_code == 1, res.output
        assert "Analysis assurance incomplete" in res.output

    def test_flag_omitted_scoped_exit_code_still_zero(self, tmp_path: Path) -> None:
        """Sanity: without the flag, the scoped gate stays purely additive --
        same fixture, same scoping, no floor applied."""
        old_p, new_p = self._pair(tmp_path)
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--required-symbol",
                "_Z5pub_av",
                "--format",
                "sarif",
            ],
        )
        assert res.exit_code == 0, res.output
        out = res.output
        doc = json.loads(out[out.index("{") :])
        scoped_gate = doc["runs"][0]["properties"]["scopedGate"]
        assert scoped_gate["gateExitCode"] == 0
        assert doc["runs"][0]["invocations"][0]["exitCode"] == 0


class TestExportAccountingBothSides:
    """Round-7 review, Finding 1 (``discussion_r3787513758``): the previous
    ``_export_accounting`` picked ONE side's accounting -- new first,
    falling back to old only when new carried no L4 surface at all -- so a
    comparison where BOTH sides link an L4 surface, but only the OLD side
    has unmatched exports, silently returned the NEW side's clean
    accounting and never examined the old side. ``export_accounting.
    unaccounted`` therefore read 0 and, with no other partial signal
    tripping, ``status`` could read ``"complete"`` under
    ``--require-complete-analysis`` despite genuinely incomplete baseline
    linking. Fixed by summing both sides' counts additively, mirroring
    ``_translation_units()``'s own both-sides summation.
    """

    def _pack_with_surface(
        self, tmp_path: Path, name: str, *, unaccounted: tuple[str, ...]
    ):
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        surface = SourceAbiSurface(
            roots={
                "exported_symbols": ["a", "b", "c"],
                "public_header_declarations": [],
                "forced_public": [],
            },
            unmatched={
                "symbols_without_decl": list(unaccounted),
                "decls_without_symbol": [],
            },
            # Set so this fixture isolates the export-accounting rollup --
            # without this, an empty ``coverage`` dict would also trip the
            # separate ``l4_present_without_accounting`` signal, confounding
            # what actually made ``status`` non-complete.
            coverage={"compile_units_selected": 3, "compile_units_parsed": 3},
        )
        return BuildSourcePack(root=tmp_path / name, source_abi=surface)

    def test_unaccounted_export_on_old_side_only_is_not_shadowed_by_new(
        self, tmp_path: Path
    ) -> None:
        old, new = _header_pair()
        old_pack = self._pack_with_surface(tmp_path, "old_pack", unaccounted=("a",))
        new_pack = self._pack_with_surface(tmp_path, "new_pack", unaccounted=())

        result = checker.compare(old, new)
        aa = compute_analysis_assurance(
            result, old, new, old_pack=old_pack, new_pack=new_pack
        )
        # The old side's single unaccounted export must be visible in the
        # combined rollup -- not silently dropped because the new side (read
        # first in source order) happened to be clean.
        assert aa.export_accounting.unaccounted == 1
        assert aa.status != "complete"
        assert any("unaccounted" in n for n in aa.notes), aa.notes

    def test_both_sides_clean_stays_zero_unaccounted(self, tmp_path: Path) -> None:
        old, new = _header_pair()
        old_pack = self._pack_with_surface(tmp_path, "old_pack", unaccounted=())
        new_pack = self._pack_with_surface(tmp_path, "new_pack", unaccounted=())

        result = checker.compare(old, new)
        aa = compute_analysis_assurance(
            result, old, new, old_pack=old_pack, new_pack=new_pack
        )
        assert aa.export_accounting.unaccounted == 0
        # Both sides contribute total exports (3 + 3 = 6), same additive
        # rollup as translation-unit accounting.
        assert aa.export_accounting.total == 6

    def test_require_complete_analysis_exits_nonzero_for_old_side_unaccounted(
        self, tmp_path: Path
    ) -> None:
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        old, new = _header_pair()
        old.build_source = BuildSourcePack(
            root=tmp_path / "old_pack",
            source_abi=SourceAbiSurface(
                roots={
                    "exported_symbols": ["a"],
                    "public_header_declarations": [],
                    "forced_public": [],
                },
                unmatched={
                    "symbols_without_decl": ["a"],
                    "decls_without_symbol": [],
                },
            ),
        )
        new.build_source = BuildSourcePack(
            root=tmp_path / "new_pack",
            source_abi=SourceAbiSurface(
                roots={
                    "exported_symbols": ["a"],
                    "public_header_declarations": [],
                    "forced_public": [],
                },
            ),
        )
        old_p, new_p = _write(tmp_path, old, new)
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--require-complete-analysis",
            ],
        )
        assert res.exit_code != 0, res.output


class TestDwarfChannelAsymmetry:
    """Round-7 review, Finding 2 (``discussion_r3787513766``): the previous
    ``_dwarf_context_status`` OR'd ``dwarf.has_dwarf``/``dwarf_advanced.
    has_dwarf`` into ONE combined per-side boolean before comparing the two
    sides -- so an old snapshot with only basic ``dwarf`` and a new
    snapshot with only ``dwarf_advanced`` both read as "this side has SOME
    dwarf evidence", and the combined booleans compared equal (clean), even
    though ``diff_platform.py``'s struct/enum layout diff (basic ``dwarf``
    only) and ``dwarf_advanced.diff_advanced_dwarf`` (advanced only) each
    independently skip their own comparison whenever EITHER side lacks
    THEIR channel specifically. Fixed by checking each channel
    independently.
    """

    def _cross_channel_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        fns = [_fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=True),
            dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=False),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=False),
            dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=True),
        )
        return old, new

    def test_cross_channel_dwarf_is_reported_as_asymmetric(self) -> None:
        old, new = self._cross_channel_pair()
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.dwarf_context_status == "asymmetric"
        assert aa.status != "complete"
        # Both channels must be named -- the old side lacks basic dwarf, the
        # new side lacks dwarf_advanced, and both are real, independent gaps.
        assert any("basic dwarf channel" in n for n in aa.notes), aa.notes
        assert any("dwarf_advanced channel" in n for n in aa.notes), aa.notes

    def test_unit_level_both_channels_flagged_independently(self) -> None:
        from abicheck.analysis_assurance import _dwarf_context_status

        old, new = self._cross_channel_pair()
        status, notes = _dwarf_context_status(old, new)
        assert status == "asymmetric"
        assert len(notes) == 2

    def test_matching_single_channel_on_both_sides_is_clean(self) -> None:
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata

        fns = [_fn("pub_a", "_Z5pub_av")]
        old = AbiSnapshot(
            version="1.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=False),
            dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=True),
        )
        new = AbiSnapshot(
            version="2.0",
            library="libfoo.so.1",
            functions=fns,
            dwarf=DwarfMetadata(has_dwarf=False),
            dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=True),
        )
        result = checker.compare(old, new, scope_to_public_surface=False)
        aa = result.analysis_assurance
        assert aa.dwarf_context_status == "clean"

    def test_require_complete_analysis_exits_nonzero_for_cross_channel_dwarf(
        self, tmp_path: Path
    ) -> None:
        old, new = self._cross_channel_pair()
        res = _compare(
            tmp_path,
            (old, new),
            "--no-scope-public-headers",
            "--require-complete-analysis",
        )
        assert res.exit_code != 0, res.output


class TestSourceTreeMismatchFailure:
    """Round-7 review, Finding 3 (``discussion_r3787513768``): inline
    collection records ``ExtractorRecord(name=
    "build_info_source_tree_mismatch", status="failed", ...)``
    (``inline._check_build_info_source_mismatch``) when most of a compile
    database's own source files are absent from the ``--sources`` tree --
    i.e. the build metadata and the checked-out sources may not even be the
    same codebase. The previous ``_manifest_layer_incompleteness`` only
    recognized a failed/partial extractor whose name started with
    ``source_abi``/``compile_``/``source_graph`` -- this record's name
    matches none of those, so it was silently ignored: if the resulting L4
    surface still carried ordinary TU accounting and no other partial
    signal tripped, ``status`` could read ``"complete"`` and
    ``--require-complete-analysis`` could exit 0 even though the source
    facts may come from a different checkout.
    """

    def _pack_with_mismatch(self, tmp_path: Path, name: str):
        from abicheck.buildsource.model import BuildSourceManifest, ExtractorRecord
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        manifest = BuildSourceManifest(
            extractors=[
                ExtractorRecord(
                    name="build_info_source_tree_mismatch",
                    status="failed",
                    detail=(
                        "9/10 compile-DB source files are absent from the "
                        "--sources tree; build metadata and sources may be "
                        "different checkouts"
                    ),
                ),
            ],
        )
        return BuildSourcePack(
            root=tmp_path / name,
            manifest=manifest,
            source_abi=SourceAbiSurface(
                coverage={
                    "compile_units_selected": 10,
                    "compile_units_parsed": 10,
                },
            ),
        )

    def test_source_tree_mismatch_is_not_complete(self, tmp_path: Path) -> None:
        old, new = _header_pair()
        old.build_source = self._pack_with_mismatch(tmp_path, "old_pack")
        new.build_source = self._pack_with_mismatch(tmp_path, "new_pack")

        result = checker.compare(old, new)
        aa = result.analysis_assurance
        assert isinstance(aa, AnalysisAssurance)
        assert aa.status != "complete", aa
        assert any(
            "build_info_source_tree_mismatch" in n and "failed" in n for n in aa.notes
        ), aa.notes

    def test_unit_level_manifest_scan_flags_unprefixed_failed_record(
        self, tmp_path: Path
    ) -> None:
        from abicheck.analysis_assurance import _manifest_layer_incompleteness

        pack = self._pack_with_mismatch(tmp_path, "pack")
        incomplete, notes = _manifest_layer_incompleteness(pack, pack)
        assert incomplete is True
        assert any("build_info_source_tree_mismatch" in n for n in notes), notes

    def test_require_complete_analysis_exits_nonzero_for_source_tree_mismatch(
        self, tmp_path: Path
    ) -> None:
        old, new = _header_pair()
        old.build_source = self._pack_with_mismatch(tmp_path, "old_pack")
        new.build_source = self._pack_with_mismatch(tmp_path, "new_pack")
        old_p, new_p = _write(tmp_path, old, new)

        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--require-complete-analysis",
            ],
        )
        assert res.exit_code != 0, res.output
