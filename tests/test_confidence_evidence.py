"""Confidence tier and evidence assertion tests.

Verifies that the scanner correctly computes confidence levels and evidence
tiers based on available data sources (header, ELF, DWARF, PE, Mach-O).
"""
from __future__ import annotations

import copy
import json

from abicheck import reporter
from abicheck.checker import Verdict, compare
from abicheck.checker_policy import Confidence, EvidenceTier
from abicheck.dwarf_metadata import DwarfMetadata, StructLayout
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    Visibility,
)


def _snap(version="1.0", functions=None, variables=None, types=None,
          enums=None, typedefs=None, elf=None, dwarf=None,
          dwarf_advanced=None, from_headers=False):
    return AbiSnapshot(
        library="libtest.so.1", version=version,
        functions=functions or [], variables=variables or [],
        types=types or [], enums=enums or [],
        typedefs=typedefs or {}, elf=elf, dwarf=dwarf,
        dwarf_advanced=dwarf_advanced, from_headers=from_headers,
    )


def _pub_func(name, mangled, ret="void", params=None, **kwargs):
    return Function(name=name, mangled=mangled, return_type=ret,
                    params=params or [], visibility=Visibility.PUBLIC, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# Evidence Tier Detection
# ═══════════════════════════════════════════════════════════════════════════

class TestEvidenceTiers:
    """Verify evidence_tiers list reflects available data sources."""

    def test_header_only_evidence(self):
        """Snapshot with header data but no binary metadata → 'header' tier."""
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f]), _snap(functions=[f]))
        assert "header" in r.evidence_tiers

    def test_elf_evidence_included(self):
        """Snapshot with ELF metadata → 'elf' tier."""
        elf = ElfMetadata(
            soname="libtest.so.1",
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f], elf=elf),
                     _snap(functions=[f], elf=elf))
        assert "elf" in r.evidence_tiers

    def test_dwarf_evidence_included(self):
        """Snapshot with DWARF metadata → 'dwarf' tier."""
        dwarf = DwarfMetadata(
            structs={"Foo": StructLayout(name="Foo", byte_size=4)},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=dwarf), _snap(dwarf=dwarf))
        assert "dwarf" in r.evidence_tiers

    def test_multiple_evidence_tiers(self):
        """Snapshot with header + ELF + DWARF → all three tiers."""
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        dwarf = DwarfMetadata(
            structs={"Cfg": StructLayout(name="Cfg", byte_size=4)},
            has_dwarf=True,
        )
        # A real header-parsed dump that also carries ELF + DWARF metadata:
        # from_headers marks that the surface came from castxml/AST, which is
        # what promotes it to the header tier (DWARF-derived declarations alone
        # do not).
        r = compare(
            _snap(functions=[f], elf=elf, dwarf=dwarf, from_headers=True),
            _snap(functions=[f], elf=elf, dwarf=dwarf, from_headers=True),
        )
        assert "header" in r.evidence_tiers
        assert "elf" in r.evidence_tiers
        assert "dwarf" in r.evidence_tiers

    def test_empty_snapshot_minimal_evidence(self):
        """Empty snapshots have minimal evidence."""
        r = compare(_snap(), _snap())
        assert r.evidence_tiers == [] or r.evidence_tiers == ["header"]


# ═══════════════════════════════════════════════════════════════════════════
# Canonical Evidence Tier (ELF_ONLY / DWARF_AWARE / HEADER_AWARE)
# ═══════════════════════════════════════════════════════════════════════════

class TestCanonicalEvidenceTier:
    """Verify the single, ordered evidence_tier scalar (formalized in JSON)."""

    def test_header_surface_is_header_aware(self):
        """Functions/types present → HEADER_AWARE (the richest tier)."""
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f]), _snap(functions=[f]))
        assert r.evidence_tier == EvidenceTier.HEADER_AWARE

    def test_dwarf_without_headers_is_dwarf_aware(self):
        """DWARF debug info but no header/AST surface → DWARF_AWARE."""
        dwarf = DwarfMetadata(
            structs={"Foo": StructLayout(name="Foo", byte_size=4)},
            has_dwarf=True,
        )
        r = compare(_snap(dwarf=dwarf, elf=ElfMetadata()),
                    _snap(dwarf=dwarf, elf=ElfMetadata()))
        assert r.evidence_tier == EvidenceTier.DWARF_AWARE

    def test_symbols_only_is_elf_only(self):
        """Binary metadata only (no DWARF, no header surface) → ELF_ONLY."""
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="sym", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        r = compare(_snap(elf=elf), _snap(elf=elf))
        assert r.evidence_tier == EvidenceTier.ELF_ONLY

    def test_header_aware_distinct_from_dwarf_aware(self):
        """The documented goal: HEADER_AWARE must be distinguishable from DWARF_AWARE."""
        f = _pub_func("api", "_Z3apiv")
        dwarf = DwarfMetadata(has_dwarf=True)
        # header_aware: the surface was parsed from headers (from_headers=True),
        # even though DWARF is also present. dwarf_only: same declarations would
        # come from DWARF alone, so from_headers stays False — this is exactly
        # the distinction that was previously impossible to express.
        header_aware = compare(_snap(functions=[f], dwarf=dwarf, from_headers=True),
                               _snap(functions=[f], dwarf=dwarf, from_headers=True))
        dwarf_only = compare(_snap(functions=[f], dwarf=dwarf, elf=ElfMetadata()),
                             _snap(functions=[f], dwarf=dwarf, elf=ElfMetadata()))
        assert header_aware.evidence_tier == EvidenceTier.HEADER_AWARE
        assert dwarf_only.evidence_tier == EvidenceTier.DWARF_AWARE
        assert header_aware.evidence_tier != dwarf_only.evidence_tier

    def test_tier_rank_ordering(self):
        """Ranks must be strictly ordered shallow → deep."""
        assert (
            EvidenceTier.ELF_ONLY.rank
            < EvidenceTier.DWARF_AWARE.rank
            < EvidenceTier.HEADER_AWARE.rank
        )

    def test_real_dwarf_dump_is_not_header_aware(self):
        """Regression: a DWARF-derived dump (no headers parsed) must report
        DWARF_AWARE, not HEADER_AWARE.

        The real dumper populates ``functions``/``types`` from DWARF DIEs in
        DWARF-only mode, so "has declarations" must not be mistaken for header
        analysis. Mirrors a real ``abicheck compare libfoo.so libfoo.so`` run
        on a ``-g`` binary with no ``--header`` flag.
        """
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        dwarf = DwarfMetadata(
            structs={"Cfg": StructLayout(name="Cfg", byte_size=4)},
            has_dwarf=True,
        )
        # from_headers is False — castxml never ran.
        r = compare(
            _snap(functions=[f], elf=elf, dwarf=dwarf),
            _snap(functions=[f], elf=elf, dwarf=dwarf),
        )
        assert r.evidence_tier == EvidenceTier.DWARF_AWARE
        assert "header" not in r.evidence_tiers

    def test_real_symbols_only_dump_is_elf_only(self):
        """Regression: a stripped, symbols-only dump must report ELF_ONLY with
        no DWARF/header tiers — never the highest confidence.

        Before the provenance fix, a stripped binary (``.eh_frame`` only,
        no real DWARF) was mislabeled HEADER_AWARE/HIGH and could silently
        report NO_CHANGE for an undetectable struct break.
        """
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        r = compare(
            _snap(functions=[f], elf=elf),
            _snap(functions=[f], elf=elf),
        )
        assert r.evidence_tier == EvidenceTier.ELF_ONLY
        assert "dwarf" not in r.evidence_tiers
        assert "header" not in r.evidence_tiers

    def test_header_provenance_on_either_side_promotes_tier(self):
        """from_headers is honored when set on only one side of the compare."""
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        # Old side carries no header provenance; new side does (e.g. baseline
        # JSON dumped pre-flag vs a freshly header-parsed build).
        r = compare(
            _snap(functions=[f], elf=elf, from_headers=False),
            _snap(functions=[f], elf=elf, from_headers=True),
        )
        assert r.evidence_tier == EvidenceTier.HEADER_AWARE

    def test_evidence_tier_in_json_output(self):
        """The formalized tier must appear in the JSON report schema."""
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f]), _snap(functions=[f]))
        payload = json.loads(reporter.to_json(r))
        assert payload["evidence_tier"] == "header_aware"
        # Raw list retained for backward compatibility.
        assert "evidence_tiers" in payload

    def test_evidence_tier_in_stat_and_leaf_json(self):
        """Stat and leaf JSON projections also carry the canonical tier."""
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f]), _snap(functions=[f]))
        stat = json.loads(reporter.to_json(r, stat=True))
        leaf = json.loads(reporter.to_json(r, report_mode="leaf"))
        assert stat["evidence_tier"] == "header_aware"
        assert leaf["evidence_tier"] == "header_aware"

    def test_evidence_tier_in_markdown(self):
        """Markdown confidence section surfaces the canonical tier."""
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f]), _snap(functions=[f]))
        md = reporter.to_markdown(r)
        assert "Evidence tier" in md
        assert "header_aware" in md

    def test_evidence_tier_in_appcompat_json(self):
        """appcompat JSON surfaces the canonical tier from the underlying full diff."""
        import types

        f = _pub_func("api", "_Z3apiv")
        full = compare(_snap(functions=[f]), _snap(functions=[f]))
        fake = types.SimpleNamespace(
            full_diff=full,
            app_path="app",
            library_path="lib.so",
            verdict=full.verdict,
            used_symbols=[],
            affected_symbols=[],
            affected_changes=[],
        )
        payload = json.loads(reporter.appcompat_to_json(fake))
        assert payload["evidence_tier"] == "header_aware"


# ═══════════════════════════════════════════════════════════════════════════
# Confidence Levels
# ═══════════════════════════════════════════════════════════════════════════

class TestConfidenceLevels:
    """Verify confidence correlates with evidence sources."""

    def test_empty_snapshot_low_confidence(self):
        """Empty snapshots → LOW confidence."""
        r = compare(_snap(), _snap())
        assert r.confidence == Confidence.LOW

    def test_header_only_not_high_confidence(self):
        """Header-only analysis (no binary data) → MEDIUM or LOW."""
        f = _pub_func("api", "_Z3apiv")
        t = RecordType(name="Cfg", kind="struct", size_bits=32)
        r = compare(
            _snap(functions=[f], types=[t]),
            copy.deepcopy(_snap(functions=[f], types=[t])),
        )
        assert r.confidence in (Confidence.MEDIUM, Confidence.LOW)

    def test_elf_increases_confidence(self):
        """Adding ELF metadata should not decrease confidence vs header-only."""
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )

        # Both sides model a header-parsed surface (from_headers=True); the only
        # difference is the added ELF metadata, which must not lower confidence.
        header_only = compare(
            _snap(functions=[f], from_headers=True),
            copy.deepcopy(_snap(functions=[f], from_headers=True)),
        )
        with_elf = compare(
            _snap(functions=[f], elf=elf, from_headers=True),
            _snap(functions=[f], elf=elf, from_headers=True),
        )

        confidence_order = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
        assert confidence_order[with_elf.confidence] >= confidence_order[header_only.confidence]

    def test_elf_plus_dwarf_high_confidence(self):
        """ELF + DWARF + headers → should be HIGH or MEDIUM."""
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        dwarf = DwarfMetadata(
            structs={"Cfg": StructLayout(name="Cfg", byte_size=4)},
            has_dwarf=True,
        )

        r = compare(
            _snap(functions=[f], elf=elf, dwarf=dwarf),
            _snap(functions=[f], elf=elf, dwarf=dwarf),
        )
        assert r.confidence in (Confidence.HIGH, Confidence.MEDIUM)


# ═══════════════════════════════════════════════════════════════════════════
# Coverage Warnings
# ═══════════════════════════════════════════════════════════════════════════

class TestCoverageWarnings:
    """Verify coverage_warnings flag missing detectors."""

    def test_fewer_warnings_with_complete_data(self):
        """Full metadata → fewer warnings than header-only."""
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        dwarf = DwarfMetadata(has_dwarf=True)

        full = compare(
            _snap(functions=[f], elf=elf, dwarf=dwarf),
            _snap(functions=[f], elf=elf, dwarf=dwarf),
        )
        header_only = compare(_snap(functions=[f]), _snap(functions=[f]))
        assert len(full.coverage_warnings) <= len(header_only.coverage_warnings)

    def test_warnings_when_dwarf_missing(self):
        """Missing DWARF → at least one coverage warning."""
        f = _pub_func("api", "_Z3apiv")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )

        r = compare(
            _snap(functions=[f], elf=elf),
            _snap(functions=[f], elf=elf),
        )
        assert len(r.coverage_warnings) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Confidence with Breaking Changes
# ═══════════════════════════════════════════════════════════════════════════

class TestConfidenceWithBreakingChanges:
    """Confidence should be reported even when changes are detected."""

    def test_breaking_change_with_high_confidence(self):
        """Breaking change detected with full metadata → still reports confidence."""
        f_old = _pub_func("api", "_Z3apiv", ret="int")
        f_new = _pub_func("api", "_Z3apiv", ret="long")
        elf = ElfMetadata(
            symbols=[ElfSymbol(name="_Z3apiv", binding=SymbolBinding.GLOBAL,
                               sym_type=SymbolType.FUNC)],
        )
        dwarf = DwarfMetadata(has_dwarf=True)

        r = compare(
            _snap(functions=[f_old], elf=elf, dwarf=dwarf),
            _snap(functions=[f_new], elf=elf, dwarf=dwarf),
        )
        assert r.verdict == Verdict.BREAKING
        assert r.confidence is not None
        assert isinstance(r.evidence_tiers, list)

    def test_breaking_change_header_only_lower_confidence(self):
        """Breaking change with header-only data → lower confidence."""
        f_old = _pub_func("api", "_Z3apiv", ret="int")
        f_new = _pub_func("api", "_Z3apiv", ret="long")

        r = compare(
            _snap(functions=[f_old]),
            _snap(functions=[f_new]),
        )
        assert r.verdict == Verdict.BREAKING
        assert r.confidence in (Confidence.MEDIUM, Confidence.LOW)


# ═══════════════════════════════════════════════════════════════════════════
# Detector Results
# ═══════════════════════════════════════════════════════════════════════════

class TestDetectorResults:
    """Verify detector_results are populated for introspection."""

    def test_detector_results_populated(self):
        """At least some detectors should report results."""
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f]), _snap(functions=[f]))
        assert isinstance(r.detector_results, list)
        assert len(r.detector_results) > 0

    def test_each_detector_has_name(self):
        """Every detector result should have a name."""
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f]), _snap(functions=[f]))
        for dr in r.detector_results:
            assert hasattr(dr, "name")
            assert dr.name  # non-empty

    def test_disabled_detectors_report_reason(self):
        """Detectors disabled due to missing metadata should explain why."""
        f = _pub_func("api", "_Z3apiv")
        r = compare(_snap(functions=[f]), _snap(functions=[f]))
        # Check that at least some detectors report disabled status
        # (DWARF, advanced_dwarf should be disabled without binary data)
        disabled = [dr for dr in r.detector_results if not dr.enabled]
        # There should be some disabled detectors when no binary data
        assert len(disabled) > 0
        for dr in disabled:
            assert dr.coverage_gap  # should explain why it was disabled


class TestNoteIfSameBinaryCompared:
    """Item 4 of the abicheck code-review report: a comparison against a
    byte-identical binary silently produces a clean NO_CHANGE report with
    no signal that the comparison couldn't have caught anything either
    way -- the correct verdict and "nothing was actually compared" read
    identically without this warning."""

    def _result(self, old_sha=None, new_sha=None, evidence_tiers=None):
        from abicheck.checker import DiffResult
        from abicheck.checker_types import LibraryMetadata

        result = DiffResult(old_version="1.0", new_version="2.0", library="lib")
        if old_sha is not None:
            result.old_metadata = LibraryMetadata(
                path="/a/old.so", sha256=old_sha, size_bytes=100
            )
        if new_sha is not None:
            result.new_metadata = LibraryMetadata(
                path="/b/new.so", sha256=new_sha, size_bytes=100
            )
        if evidence_tiers is not None:
            result.evidence_tiers = evidence_tiers
        return result

    def test_identical_sha256_appends_warning(self):
        from abicheck.confidence import note_if_same_binary_compared

        result = self._result(old_sha="a" * 64, new_sha="a" * 64)
        note_if_same_binary_compared(result)
        assert any("byte-identical" in w for w in result.coverage_warnings), (
            result.coverage_warnings
        )

    def test_no_header_evidence_keeps_the_cannot_detect_claim(self):
        """No header/AST evidence was analyzed alongside the identical
        binaries -- the strong "this comparison cannot detect a change"
        claim is accurate for this case."""
        from abicheck.confidence import note_if_same_binary_compared

        result = self._result(
            old_sha="a" * 64, new_sha="a" * 64, evidence_tiers=["elf", "dwarf"]
        )
        note_if_same_binary_compared(result)
        assert any(
            "cannot detect a change" in w for w in result.coverage_warnings
        ), result.coverage_warnings

    def test_header_evidence_present_qualifies_the_claim(self):
        """Codex review: the binaries being byte-identical says nothing
        about whether a real API/source-level change could still be
        caught when header/AST evidence was also analyzed (e.g. distinct
        --old-header/--new-header or --build-info content) -- the warning
        must not claim this comparison "cannot detect a change" when
        header evidence genuinely could."""
        from abicheck.confidence import note_if_same_binary_compared

        result = self._result(
            old_sha="a" * 64, new_sha="a" * 64, evidence_tiers=["elf", "header"]
        )
        note_if_same_binary_compared(result)
        assert any(
            "cannot detect a change" not in w and "byte-identical" in w
            for w in result.coverage_warnings
        ), result.coverage_warnings
        assert any(
            "header/build evidence" in w for w in result.coverage_warnings
        ), result.coverage_warnings

    def test_different_sha256_appends_nothing(self):
        from abicheck.confidence import note_if_same_binary_compared

        result = self._result(old_sha="a" * 64, new_sha="b" * 64)
        note_if_same_binary_compared(result)
        assert result.coverage_warnings == []

    def test_real_findings_without_header_tier_also_qualify_the_claim(self):
        """Codex review, fresh evidence: L3-L5 build/source-pack evidence
        can detect and report a real change without ever setting "header"
        in evidence_tiers (that list only reflects snapshot-level elf/
        dwarf/header/pe/macho facts) -- a non-empty result.changes already
        contradicts "cannot detect a change" regardless of which tier
        produced it."""
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change
        from abicheck.confidence import note_if_same_binary_compared

        result = self._result(
            old_sha="a" * 64, new_sha="a" * 64, evidence_tiers=["elf", "dwarf"]
        )
        result.changes = [
            Change(ChangeKind.FUNC_REMOVED, symbol="f", description="removed")
        ]
        note_if_same_binary_compared(result)
        assert any(
            "cannot detect a change" not in w and "byte-identical" in w
            for w in result.coverage_warnings
        ), result.coverage_warnings

    def test_symvers_manifest_appends_no_warning_at_all(self, tmp_path):
        """Codex review, fresh evidence: two identical `Module.symvers`
        kABI manifests are not binaries at all, so `collect_metadata` must
        read `None` for them the same way it already does for a JSON/Perl
        snapshot -- this predicate must never claim "old and new binaries
        are byte-identical" for a comparison with no binary artifact."""
        from abicheck.service import collect_metadata

        text = "0x12345678\tfoo\tvmlinux\tEXPORT_SYMBOL\n"
        old_p = tmp_path / "old.symvers"
        new_p = tmp_path / "new.symvers"
        old_p.write_text(text)
        new_p.write_text(text)
        assert collect_metadata(old_p) is None
        assert collect_metadata(new_p) is None

    def test_missing_old_metadata_is_a_noop(self):
        from abicheck.confidence import note_if_same_binary_compared

        result = self._result(old_sha=None, new_sha="a" * 64)
        note_if_same_binary_compared(result)
        assert result.coverage_warnings == []

    def test_missing_new_metadata_is_a_noop(self):
        from abicheck.confidence import note_if_same_binary_compared

        result = self._result(old_sha="a" * 64, new_sha=None)
        note_if_same_binary_compared(result)
        assert result.coverage_warnings == []

    def test_both_metadata_absent_is_a_noop(self):
        from abicheck.confidence import note_if_same_binary_compared

        result = self._result()
        note_if_same_binary_compared(result)
        assert result.coverage_warnings == []

    def test_end_to_end_through_the_real_cli_compare_command(
        self, tmp_path, monkeypatch
    ):
        """Public-surface test: exercised through the real `compare` CLI
        entry point (same path/dump-mocking pattern as
        TestUsedByScopedOnlyChange in test_cli_compare_audit_suppressions.py),
        not only the internal helper directly."""
        from unittest.mock import MagicMock

        from click.testing import CliRunner

        from abicheck import dumper as dumper_mod
        from abicheck.cli import main

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        monkeypatch.setattr(dumper_mod, "dump", MagicMock(side_effect=[snap, snap]))

        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(so_path), str(so_path)])
        assert "byte-identical" in result.stdout, result.stdout

    def test_oneline_profile_still_surfaces_the_warning_on_stderr(
        self, tmp_path, monkeypatch
    ):
        """Codex review: `--profile quick` renders through
        `service_render.to_stat`, a fixed one-line summary with no room for
        a `coverage_warnings` entry -- every other format already surfaces
        it inline (JSON/SARIF/markdown/HTML), so this format silently
        dropped a same-binary warning entirely, mirroring the identical gap
        already fixed for `scan --against`'s own text renderer."""
        from unittest.mock import MagicMock

        from click.testing import CliRunner

        from abicheck import dumper as dumper_mod
        from abicheck.cli import main

        so_path = tmp_path / "lib.so"
        so_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        monkeypatch.setattr(dumper_mod, "dump", MagicMock(side_effect=[snap, snap]))

        runner = CliRunner()
        result = runner.invoke(
            main, ["compare", "--profile", "quick", str(so_path), str(so_path)]
        )
        assert "byte-identical" in result.output, result.output

    def test_oneline_profile_still_omits_unrelated_coverage_warnings(
        self, tmp_path
    ):
        """Codex review, fresh evidence: an earlier revision of the fix
        above echoed *every* `coverage_warnings` entry in `--profile
        quick`, not just the same-binary one -- breaking the pre-existing,
        tested one-line contract for the common case of comparing two
        JSON snapshots with no binary metadata (which appends a "no
        binary metadata available" warning, unrelated to this feature).
        Only the same-binary warning may ever reach the one-line output."""
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(
            library="libtest.so", version="1.0",
            functions=[
                Function(
                    name="foo", mangled="_Z3foov", return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text(snapshot_to_json(snap), encoding="utf-8")
        new_p.write_text(snapshot_to_json(snap), encoding="utf-8")

        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--profile", "quick"]
        )
        assert result.exit_code == 0, result.output
        # stdout, not the stderr-mixed `result.output`: `quick`'s
        # `depth=binary` (ADR-063 Phase 8's ceiling fix) means this
        # unscoped-headers fixture no longer resolves a public-header
        # surface at that depth either, and that scope-fallback warning is
        # by design routed to stderr so it never corrupts this contract.
        assert result.stdout.strip().count("\n") == 0, result.output
        assert "Warning:" not in result.stdout, result.output

    def test_native_compare_cli_hashes_through_a_multi_hop_linker_script_chain(
        self, tmp_path, monkeypatch
    ):
        """Follow-up (Codex review): `_normalize_binary_input` (called ahead
        of `_finalize_compare_result` in `cli_compare_helpers.py`) only ever
        resolves one linker-script hop, while `resolve_input()` follows the
        whole chain recursively -- so a script pointing at another script
        still hashed the intermediate script, not the final target, and the
        warning was omitted even though both sides resolve to the same
        binary."""
        from unittest.mock import MagicMock

        from click.testing import CliRunner

        from abicheck import dumper as dumper_mod
        from abicheck.cli import main

        real_so = tmp_path / "libfoo.so.1.2.3"
        real_so.write_bytes(b"\x7fELF" + b"\x00" * 200)
        middle_script = tmp_path / "libfoo.so.1"
        middle_script.write_text("INPUT(libfoo.so.1.2.3)\n")
        outer_script = tmp_path / "libfoo.so"
        outer_script.write_text("INPUT(libfoo.so.1)\n")
        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        monkeypatch.setattr(dumper_mod, "dump", MagicMock(side_effect=[snap, snap]))

        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(real_so), str(outer_script)])
        assert "byte-identical" in result.stdout, result.stdout

    def test_typed_compare_request_hashes_through_a_linker_script(
        self, tmp_path, monkeypatch
    ):
        """Follow-up (Codex review): the typed ``CompareRequest``/
        ``run_compare_request`` path -- shared by the Python API and any
        other Tier-2 caller -- collected metadata from the caller's
        original operand path, not the artifact ``resolve_side_snapshot()``
        actually resolved through. A GNU ld linker script named as one side
        against its own resolved target DSO on the other therefore never
        warned, even though both sides resolve to the same binary."""
        from unittest.mock import MagicMock

        from abicheck import dumper as dumper_mod
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.service_compare_pipeline import run_compare_request

        real_so = tmp_path / "libfoo.so.1"
        real_so.write_bytes(b"\x7fELF" + b"\x00" * 200)
        script_so = tmp_path / "libfoo.so"
        script_so.write_text("INPUT(libfoo.so.1)\n")
        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        monkeypatch.setattr(dumper_mod, "dump", MagicMock(side_effect=[snap, snap]))

        request = CompareRequest(
            old=InputSpec.of(real_so), new=InputSpec.of(script_so)
        )
        result = run_compare_request(request)
        assert any("byte-identical" in w for w in result.diff.coverage_warnings), (
            result.diff.coverage_warnings
        )

    def test_typed_compare_request_snapshot_matching_linker_script_regex_does_not_warn(
        self, tmp_path, monkeypatch
    ):
        """Codex review, fresh evidence: the typed `CompareRequest` path had
        the identical snapshot-misclassified-as-linker-script gap already
        fixed for `scan --against` -- a JSON snapshot whose own serialized
        text matches the INPUT()/GROUP() probe must never be resolved as a
        linker script pointing at a same-named real DSO."""
        from abicheck.api_types import CompareRequest, InputSpec
        from abicheck.serialization import snapshot_to_json
        from abicheck.service_compare_pipeline import run_compare_request

        real_so = tmp_path / "libfoo.so"
        real_so.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_snap = AbiSnapshot(library="INPUT(libfoo.so)", version="1.0", functions=[])
        old_path = tmp_path / "old.abicheck.json"
        old_path.write_text(snapshot_to_json(old_snap), encoding="utf-8")
        new_path = tmp_path / "new.abicheck.json"
        new_snap = AbiSnapshot(library="INPUT(libfoo.so)", version="1.0", functions=[])
        new_path.write_text(snapshot_to_json(new_snap), encoding="utf-8")

        request = CompareRequest(old=InputSpec.of(old_path), new=InputSpec.of(new_path))
        result = run_compare_request(request)
        assert not any(
            "byte-identical" in w for w in result.diff.coverage_warnings
        ), result.diff.coverage_warnings

    def test_native_compare_cli_hashes_the_pre_embed_paths(
        self, tmp_path, monkeypatch
    ):
        """Codex review: `--old/new-sources` naming a raw checkout (or a raw
        `--build-info`) makes `_embed_inline_source_sides` rewrite
        `old_input`/`new_input` to a temporary embedded-snapshot `.abi.json`
        path *before* `_report_compare_result` calls `_finalize_compare_
        result` -- which must still hash the two real, original binaries,
        not the rewritten JSON snapshot path `_collect_metadata` always
        reads as non-hashable, or the warning silently vanishes for every
        deep-compare-folded-into-compare run even when both real binaries
        are byte-identical. Exercised through the real `compare` CLI entry
        point (`run_compare`'s actual call-site wiring), with only the
        inline-embed dump itself stubbed out -- the wiring under test is
        which path pair reaches `_finalize_compare_result`, not the dump
        machinery `_embed_inline_source_side` would otherwise invoke."""
        from unittest.mock import MagicMock

        from click.testing import CliRunner

        import abicheck.cli_compare_helpers as cch
        from abicheck import dumper as dumper_mod
        from abicheck.cli import main

        real_so = tmp_path / "lib.so"
        real_so.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_sources = tmp_path / "old-src"
        old_sources.mkdir()
        embedded = tmp_path / "old.abi.json"
        embedded.write_text(
            json.dumps(
                {
                    "library": "libfoo.so",
                    "version": "1.0",
                    "functions": [],
                }
            )
        )

        def _fake_embed(ctx, *, old_input, new_input, **kwargs):
            # Simulates the real _embed_inline_source_side rewriting the
            # --old-sources side's operand to a temporary snapshot path,
            # without needing a real inline dump toolchain.
            return embedded, None, None, new_input, kwargs["new_sources"], kwargs["new_build_info"]

        monkeypatch.setattr(cch, "_embed_inline_source_sides", _fake_embed)

        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        monkeypatch.setattr(dumper_mod, "dump", MagicMock(side_effect=[snap, snap]))

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compare", str(real_so), str(real_so),
                "--sources", f"old={old_sources}",
            ],
        )
        assert "byte-identical" in result.stdout, result.output

    def test_native_compare_cli_excludes_symvers_manifests(self, tmp_path):
        """Codex review, fresh evidence: `_finalize_compare_result`'s own
        `_collect_metadata` (a separate, frontends-layer copy of
        `service.collect_metadata`) still excluded only JSON/Perl -- two
        identical `Module.symvers` manifests, not binaries at all, still
        produced a false "old and new binaries are byte-identical" claim
        through the native `compare` CLI."""
        from click.testing import CliRunner

        from abicheck.cli import main

        text = "0x12345678\tfoo\tvmlinux\tEXPORT_SYMBOL\n"
        old_p = tmp_path / "old.symvers"
        new_p = tmp_path / "new.symvers"
        old_p.write_text(text)
        new_p.write_text(text)

        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "json"]
        )
        assert result.exit_code == 0, result.output
        assert "byte-identical" not in result.output, result.output

    def test_finalize_compare_result_does_not_hash_a_json_snapshot_path(
        self, tmp_path
    ):
        """Sanity check for the fix above: confirms `_collect_metadata`
        really does treat a `.abi.json` snapshot path as non-hashable (the
        precondition that makes the bug this fix closes possible), so a
        caller that mistakenly passed the post-embed operand here would see
        the warning silently vanish rather than this test passing
        vacuously."""
        from abicheck.checker import DiffResult
        from abicheck.frontends.cli.runtime import _finalize_compare_result

        embedded_snapshot = tmp_path / "old.abi.json"
        embedded_snapshot.write_text(json.dumps({"library": "libfoo.so"}))
        real_so = tmp_path / "libfoo.so"
        real_so.write_bytes(b"\x7fELF" + b"\x00" * 200)

        result = DiffResult(old_version="1.0", new_version="1.0", library="lib")
        _finalize_compare_result(
            result,
            embedded_snapshot,
            real_so,
            show_redundant=False,
            show_filtered=False,
        )
        assert result.coverage_warnings == []
