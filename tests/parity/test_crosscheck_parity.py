# SPDX-License-Identifier: Apache-2.0
"""Parity for the eleven cross-source checks (plan §7 F-5/F-6/F-7/F-10 and
the rest of ADR-068 §1's list).

Each check is exercised on a **committed, compiler-free** snapshot fixture —
the G20 audit/cross-source example corpus (``catalog/cases/case14x-18x``,
also used by ``tests/test_g20_catalog.py``) for the eight checks it covers,
plus three small synthetic snapshots (``_synthetic_snapshots.py``) for the
checks that corpus doesn't happen to exercise on its own
(``compile_context_conflict``, ``source_surface_dso_mismatch``,
``identity_collision_detected``).

For each check: ``run_crosschecks`` (the production function
``scan_engine.py`` is the sole caller of) must find it; the real ``compare``
CLI, given the same evidence self-compared, must NOT — that absence is
exactly what ``tests/parity/gaps.py`` records as an expected, phase-owned
gap. If either side of that ever flips, ``assert_no_capability_loss`` fails
loudly, naming the check.
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


#: check name -> (snapshot factory, source, F-row) -- covers all eleven
#: crosscheck.ALL_CHECKS entries. "source" documents where the fixture
#: comes from, for a reader diffing this against ADR-068 §1's own list.
_SCENARIOS: dict[str, tuple[object, str]] = {
    # F-7: exported symbol not publicly declared.
    "exported_not_public": (
        lambda: _g20_snapshot("case143_audit_accidental_export"),
        "case143_audit_accidental_export",
    ),
    # F-6: public declaration not exported (case150 exercises both halves
    # of the bidirectional pair -- see test_both_halves_of_case150 below).
    "public_not_exported": (
        lambda: _g20_snapshot("case150_xcheck_export_public_pair"),
        "case150_xcheck_export_public_pair",
    ),
    # F-10: preprocessing/build-context inconsistency.
    "header_build_context_mismatch": (
        lambda: _g20_snapshot("case148_xcheck_header_build_mismatch"),
        "case148_xcheck_header_build_mismatch",
    ),
    # F-5: private header leak.
    "private_header_leak": (
        lambda: _g20_snapshot("case144_audit_private_header_leak"),
        "case144_audit_private_header_leak",
    ),
    "odr_type_variant": (
        lambda: _g20_snapshot("case149_xcheck_odr_variant"),
        "case149_xcheck_odr_variant",
    ),
    "public_to_internal_dependency": (
        lambda: _g20_snapshot("case181_xcheck_public_to_internal_dependency"),
        "case181_xcheck_public_to_internal_dependency",
    ),
    "unversioned_exported_symbol": (
        lambda: _g20_snapshot("case145_audit_unversioned_export"),
        "case145_audit_unversioned_export",
    ),
    "rtti_for_internal_type": (
        lambda: _g20_snapshot("case146_audit_rtti_for_internal"),
        "case146_audit_rtti_for_internal",
    ),
    # F-10's sibling: also a preprocessing/build-context class of check.
    "compile_context_conflict": (
        synth.compile_context_conflict_snapshot,
        "synthetic (tests/parity/_synthetic_snapshots.py)",
    ),
    "source_surface_dso_mismatch": (
        synth.source_surface_dso_mismatch_snapshot,
        "synthetic (tests/parity/_synthetic_snapshots.py)",
    ),
    "identity_collision_detected": (
        synth.identity_collision_snapshot,
        "synthetic (tests/parity/_synthetic_snapshots.py)",
    ),
}


def test_scenarios_cover_every_crosscheck() -> None:
    """Guard against silently dropping a check from the corpus above."""
    from abicheck.buildsource.crosscheck import ALL_CHECKS

    assert set(_SCENARIOS) == set(ALL_CHECKS)
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
