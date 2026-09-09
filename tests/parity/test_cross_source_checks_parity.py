# SPDX-License-Identifier: Apache-2.0
"""Parity for the eleven cross-source checks (plan §7 F-5/F-6/F-7/F-10 and
the rest of ADR-068 §1's list).

All eleven checks are migrated onto ``compare()``'s automatic pipeline now
(plan §3 #3, ``workflows/cross_source_evolution.py``), so this module's own
scan-only table (``_SCENARIOS``) is empty — every check instead gets its own
positive "reaches compare" test below, mirroring
``test_unversioned_exported_symbol_reaches_compare``'s original shape. The
G20 audit/cross-source example corpus (``catalog/cases/case14x-18x``, also
used by ``tests/test_g20_catalog.py``) and three small synthetic snapshots
(``_synthetic_snapshots.py``, for ``compile_context_conflict``,
``source_surface_dso_mismatch``, and ``identity_collision_detected`` — the
checks that corpus doesn't happen to exercise on its own) still supply every
fixture; only the assertion direction changed as each check's gap closed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

from abicheck.model import AbiSnapshot  # noqa: E402
from abicheck.serialization import load_snapshot  # noqa: E402

from . import _synthetic_snapshots as synth  # noqa: E402
from .gaps import EXPECTED_GAPS  # noqa: E402
from .runner import (  # noqa: E402
    assert_no_capability_loss,
    compare_finding_set,
    crosscheck_finding_set,
    kinds_of,
    write_snapshot,
)


def _g20_snapshot(case_name: str, filename: str = "snapshot.abi.json") -> AbiSnapshot:
    path = example_catalog.case_dir(case_name) / filename
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return load_snapshot(path)


#: check name -> (snapshot factory, source, F-row). Empty now: all eleven
#: cross_source_checks.ALL_CHECKS entries have migrated onto compare()'s automatic
#: pipeline (ADR-068 §3), so there is no remaining scan-only check to table
#: here -- see each check's own "reaches_compare" test below instead.
_SCENARIOS: dict[str, tuple[object, str]] = {}


def test_scenarios_cover_every_crosscheck() -> None:
    """Guard against silently dropping a check from the corpus above.

    All eleven checks are deliberately excluded now: ``unversioned_exported_
    symbol`` and ``private_header_leak`` landed first; ``exported_not_
    public``, ``public_not_exported``, ``rtti_for_internal_type``, and
    ``public_to_internal_dependency`` landed next (ADR-068 §3 rows 3-5); and
    ``header_build_context_mismatch``, ``odr_type_variant``,
    ``identity_collision_detected``, ``compile_context_conflict``, and
    ``source_surface_dso_mismatch`` land in this PR. All eleven have already
    reached ``compare()`` and so have no place in this "scan-only" table --
    see ``test_unversioned_exported_symbol_reaches_compare`` and its ten
    siblings below instead.
    """
    from abicheck.buildsource.cross_source_checks import ALL_CHECKS

    assert set(_SCENARIOS) == set(ALL_CHECKS) - set(ALL_CHECKS)
    assert set(_SCENARIOS) == set()
    # Every one of these must also be a registered, phase-owned gap --
    # otherwise a real loss here would (correctly) fail as "unexplained".
    assert set(_SCENARIOS) <= set(EXPECTED_GAPS)


@pytest.mark.parametrize("check_name", sorted(_SCENARIOS))
def test_crosscheck_is_scan_only(check_name: str, tmp_path: Path) -> None:
    factory, source = _SCENARIOS[check_name]
    snapshot = factory()

    scan_findings = crosscheck_finding_set(snapshot)
    assert check_name in kinds_of(scan_findings), (
        f"fixture regression: {source} no longer makes run_crosschecks "
        f"produce {check_name!r} -- fix the fixture, not this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    # Self-compared: the same evidence on both sides. Any OLD/NEW pairing
    # would do -- compare's pipeline never calls run_crosschecks at all
    # (ADR-068 §1), so this is not about diffing two releases.
    compare_findings = compare_finding_set(snap_path, snap_path)

    assert_no_capability_loss(
        scan_findings=scan_findings,
        compare_findings=compare_findings,
        context=f"crosscheck {check_name!r} ({source})",
    )


def test_unversioned_exported_symbol_reaches_compare(tmp_path: Path) -> None:
    """ADR-068 §3 row 3's first slice: unlike every other row in
    ``_SCENARIOS`` above, this check is no longer scan-only -- ``compare``
    must find it too, self-compared, the same way ``run_crosschecks`` does.
    """
    snapshot = _g20_snapshot("case145_audit_unversioned_export")
    scan_findings = crosscheck_finding_set(snapshot)
    assert "unversioned_exported_symbol" in kinds_of(scan_findings), (
        "fixture regression: case145_audit_unversioned_export no longer "
        "makes run_crosschecks produce unversioned_exported_symbol -- fix "
        "the fixture, not this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    compare_findings = compare_finding_set(snap_path, snap_path)
    assert "unversioned_exported_symbol" in kinds_of(compare_findings), (
        "capability regression: compare() no longer reaches "
        "unversioned_exported_symbol automatically (ADR-068 D3/D4/D5, "
        "checker.compare's cross_source_checks)"
    )


def test_private_header_leak_reaches_compare(tmp_path: Path) -> None:
    """ADR-068 §3 row 4: like ``unversioned_exported_symbol``, this check is
    no longer scan-only -- ``compare`` must find it too, self-compared, the
    same way ``run_crosschecks`` does.
    """
    snapshot = _g20_snapshot("case144_audit_private_header_leak")
    scan_findings = crosscheck_finding_set(snapshot)
    assert "private_header_leak" in kinds_of(scan_findings), (
        "fixture regression: case144_audit_private_header_leak no longer "
        "makes run_crosschecks produce private_header_leak -- fix the "
        "fixture, not this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    compare_findings = compare_finding_set(snap_path, snap_path)
    assert "private_header_leak" in kinds_of(compare_findings), (
        "capability regression: compare() no longer reaches "
        "private_header_leak automatically (ADR-068 D3/D4/D5, "
        "checker.compare's cross_source_checks)"
    )


def test_exported_not_public_reaches_compare(tmp_path: Path) -> None:
    """ADR-068 §3 row 5 (this PR): like ``private_header_leak``, this check
    is no longer scan-only -- ``compare`` must find it too, self-compared,
    the same way ``run_crosschecks`` does."""
    snapshot = _g20_snapshot("case143_audit_accidental_export")
    scan_findings = crosscheck_finding_set(snapshot)
    assert "exported_not_public" in kinds_of(scan_findings), (
        "fixture regression: case143_audit_accidental_export no longer "
        "makes run_crosschecks produce exported_not_public -- fix the "
        "fixture, not this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    compare_findings = compare_finding_set(snap_path, snap_path)
    assert "exported_not_public" in kinds_of(compare_findings), (
        "capability regression: compare() no longer reaches "
        "exported_not_public automatically (ADR-068 D3/D4/D5, "
        "checker.compare's cross_source_checks)"
    )


def test_public_not_exported_reaches_compare(tmp_path: Path) -> None:
    """ADR-068 §3 row 5 (this PR): see
    ``test_exported_not_public_reaches_compare`` above -- same shape, the
    check's bidirectional sibling."""
    snapshot = _g20_snapshot("case150_xcheck_export_public_pair")
    scan_findings = crosscheck_finding_set(snapshot)
    assert "public_not_exported" in kinds_of(scan_findings), (
        "fixture regression: case150_xcheck_export_public_pair no longer "
        "makes run_crosschecks produce public_not_exported -- fix the "
        "fixture, not this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    compare_findings = compare_finding_set(snap_path, snap_path)
    assert "public_not_exported" in kinds_of(compare_findings), (
        "capability regression: compare() no longer reaches "
        "public_not_exported automatically (ADR-068 D3/D4/D5, "
        "checker.compare's cross_source_checks)"
    )


def test_rtti_for_internal_type_reaches_compare(tmp_path: Path) -> None:
    """ADR-068 §3 row 4 (this PR): see
    ``test_exported_not_public_reaches_compare`` above -- same shape."""
    snapshot = _g20_snapshot("case146_audit_rtti_for_internal")
    scan_findings = crosscheck_finding_set(snapshot)
    assert "rtti_for_internal_type" in kinds_of(scan_findings), (
        "fixture regression: case146_audit_rtti_for_internal no longer "
        "makes run_crosschecks produce rtti_for_internal_type -- fix the "
        "fixture, not this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    compare_findings = compare_finding_set(snap_path, snap_path)
    assert "rtti_for_internal_type" in kinds_of(compare_findings), (
        "capability regression: compare() no longer reaches "
        "rtti_for_internal_type automatically (ADR-068 D3/D4/D5, "
        "checker.compare's cross_source_checks)"
    )


def test_public_to_internal_dependency_reaches_compare(tmp_path: Path) -> None:
    """ADR-068 §3 row 3 (this PR): see
    ``test_exported_not_public_reaches_compare`` above -- same shape."""
    snapshot = _g20_snapshot("case181_xcheck_public_to_internal_dependency")
    scan_findings = crosscheck_finding_set(snapshot)
    assert "public_to_internal_dependency" in kinds_of(scan_findings), (
        "fixture regression: case181_xcheck_public_to_internal_dependency no "
        "longer makes run_crosschecks produce public_to_internal_dependency "
        "-- fix the fixture, not this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    compare_findings = compare_finding_set(snap_path, snap_path)
    assert "public_to_internal_dependency" in kinds_of(compare_findings), (
        "capability regression: compare() no longer reaches "
        "public_to_internal_dependency automatically (ADR-068 D3/D4/D5, "
        "checker.compare's cross_source_checks)"
    )


def test_header_build_context_mismatch_reaches_compare(tmp_path: Path) -> None:
    """ADR-068 §3 #3 (this PR): like ``public_to_internal_dependency``, this
    check is no longer scan-only -- ``compare`` must find it too, self-compared,
    the same way ``run_crosschecks`` does."""
    snapshot = _g20_snapshot("case148_xcheck_header_build_mismatch")
    scan_findings = crosscheck_finding_set(snapshot)
    assert "header_build_context_mismatch" in kinds_of(scan_findings), (
        "fixture regression: case148_xcheck_header_build_mismatch no longer "
        "makes run_crosschecks produce header_build_context_mismatch -- fix "
        "the fixture, not this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    compare_findings = compare_finding_set(snap_path, snap_path)
    assert "header_build_context_mismatch" in kinds_of(compare_findings), (
        "capability regression: compare() no longer reaches "
        "header_build_context_mismatch automatically (ADR-068 D3/D4/D5, "
        "checker.compare's cross_source_checks)"
    )


def test_odr_type_variant_reaches_compare(tmp_path: Path) -> None:
    """ADR-068 §3 #3 (this PR): see
    ``test_header_build_context_mismatch_reaches_compare`` above -- same shape."""
    snapshot = _g20_snapshot("case149_xcheck_odr_variant")
    scan_findings = crosscheck_finding_set(snapshot)
    assert "odr_type_variant" in kinds_of(scan_findings), (
        "fixture regression: case149_xcheck_odr_variant no longer makes "
        "run_crosschecks produce odr_type_variant -- fix the fixture, not "
        "this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    compare_findings = compare_finding_set(snap_path, snap_path)
    assert "odr_type_variant" in kinds_of(compare_findings), (
        "capability regression: compare() no longer reaches "
        "odr_type_variant automatically (ADR-068 D3/D4/D5, checker.compare's "
        "cross_source_checks)"
    )


def test_compile_context_conflict_reaches_compare(tmp_path: Path) -> None:
    """ADR-068 §3 #3 (this PR): the evidence-coherence checks migrate too --
    same shape as the crosscheck-proper five, from the synthetic fixture the
    scan-only table above used to key it under."""
    snapshot = synth.compile_context_conflict_snapshot()
    scan_findings = crosscheck_finding_set(snapshot)
    assert "compile_context_conflict" in kinds_of(scan_findings), (
        "fixture regression: compile_context_conflict_snapshot no longer "
        "makes run_crosschecks produce compile_context_conflict -- fix the "
        "fixture, not this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    compare_findings = compare_finding_set(snap_path, snap_path)
    assert "compile_context_conflict" in kinds_of(compare_findings), (
        "capability regression: compare() no longer reaches "
        "compile_context_conflict automatically (ADR-068 D3/D4/D5, "
        "checker.compare's cross_source_checks)"
    )


def test_source_surface_dso_mismatch_reaches_compare(tmp_path: Path) -> None:
    """ADR-068 §3 #3 (this PR): see
    ``test_compile_context_conflict_reaches_compare`` above -- same shape."""
    snapshot = synth.source_surface_dso_mismatch_snapshot()
    scan_findings = crosscheck_finding_set(snapshot)
    assert "source_surface_dso_mismatch" in kinds_of(scan_findings), (
        "fixture regression: source_surface_dso_mismatch_snapshot no longer "
        "makes run_crosschecks produce source_surface_dso_mismatch -- fix "
        "the fixture, not this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    compare_findings = compare_finding_set(snap_path, snap_path)
    assert "source_surface_dso_mismatch" in kinds_of(compare_findings), (
        "capability regression: compare() no longer reaches "
        "source_surface_dso_mismatch automatically (ADR-068 D3/D4/D5, "
        "checker.compare's cross_source_checks)"
    )


def test_identity_collision_detected_reaches_compare(tmp_path: Path) -> None:
    """ADR-068 §3 #3 (this PR): see
    ``test_compile_context_conflict_reaches_compare`` above -- same shape."""
    snapshot = synth.identity_collision_snapshot()
    scan_findings = crosscheck_finding_set(snapshot)
    assert "identity_collision_detected" in kinds_of(scan_findings), (
        "fixture regression: identity_collision_snapshot no longer makes "
        "run_crosschecks produce identity_collision_detected -- fix the "
        "fixture, not this assertion"
    )

    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")
    compare_findings = compare_finding_set(snap_path, snap_path)
    assert "identity_collision_detected" in kinds_of(compare_findings), (
        "capability regression: compare() no longer reaches "
        "identity_collision_detected automatically (ADR-068 D3/D4/D5, "
        "checker.compare's cross_source_checks)"
    )


def test_case150_covers_both_halves_of_the_bidirectional_pair() -> None:
    """case150 is the one G20 fixture that fires two checks at once
    (F-6 exported_not_public and F-7 public_not_exported) -- assert both,
    not just the one this module's scenario table happens to key it under.
    """
    snapshot = _g20_snapshot("case150_xcheck_export_public_pair")
    kinds = kinds_of(crosscheck_finding_set(snapshot))
    assert {"exported_not_public", "public_not_exported"} <= kinds


def test_findings_carry_resolved_identity_severity_and_evidence() -> None:
    """Spot-check the Finding projection itself, not just kind presence --
    plan §6 Phase 0 requires diffing on identity/severity/evidence, not
    only "did this kind fire"."""
    snapshot = _g20_snapshot("case144_audit_private_header_leak")
    findings = crosscheck_finding_set(snapshot)
    leaks = [f for f in findings if f.kind == "private_header_leak"]
    assert leaks, "fixture regression: expected a private_header_leak finding"
    finding = leaks[0]
    assert finding.identity, "a crosscheck finding must resolve to a real symbol"
    assert finding.severity == "COMPATIBLE_WITH_RISK"
    assert finding.evidence_refs, "expected at least one corroborating provider"
