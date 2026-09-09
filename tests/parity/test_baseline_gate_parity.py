# SPDX-License-Identifier: Apache-2.0
"""Whole-run parity between ``scan --against`` and ``compare`` on a baseline
comparison (plan §7 Phase 4/6, ADR-068 D3).

``test_crosscheck_parity.py`` already proved every cross-source check's
finding *reaches* ``compare`` (ADR-068 D3's migration). It never checked
what happens to that finding once it is there: ``scan --against``'s
baseline path still calls
``cli_scan_baseline._strip_automatic_cross_source_findings`` to keep every
cross-source finding advisory-only, while ``compare``'s automatic
``cross_source_checks`` stage (ADR-068 D4/D5: "no opt-out for a real front
end") leaves it as a real, gating finding. A bare finding-*set* diff cannot
see this: under the default severity preset the stripped finding is simply
absent from ``scan``'s ``diff.findings`` and present in ``compare``'s
``changes`` -- read as `compare_only`, which
``tests/parity/runner.py::ParityReport`` explicitly treats as "richer,
always allowed". The two tools' *verdicts* already disagree there
(``NO_CHANGE`` vs ``COMPATIBLE_WITH_RISK``), and under
``--severity-preset strict`` (which promotes a ``RISK``-bucket finding to an
error-level, exit-2 gate) the two tools' *exit codes* disagree outright:
``scan`` still exits 0, ``compare`` exits 2.

This module is the harness fix plan step 1 calls for: it diffs the *whole
run outcome* (verdict, exit code, and each shared finding's actually-applied
severity/gate_contribution -- :func:`~.runner.assert_full_parity`), not only
the finding set, and is deliberately red until plan step 3 lands the
semantics decision recorded in the ADR-068 amendment
(``docs/contribute/adr/068-one-comparison-product-and-scan-retirement.md``).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

from abicheck.model import AbiSnapshot  # noqa: E402
from abicheck.serialization import load_snapshot  # noqa: E402

from .runner import (  # noqa: E402
    assert_full_parity,
    compare_outcome,
    scan_outcome,
    write_snapshot,
)


def _g20_snapshot(case_name: str, filename: str = "snapshot.abi.json") -> AbiSnapshot:
    path = example_catalog.case_dir(case_name) / filename
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return load_snapshot(path)


def test_exported_not_public_baseline_verdict_parity(tmp_path: Path) -> None:
    """Self-compared (same evidence on both sides, matching
    ``test_exported_not_public_reaches_compare``): ``scan --against`` and
    ``compare`` must agree on the overall verdict for the identical
    accidental-export finding, not just on whether the finding fires
    somewhere in their output.
    """
    snapshot = _g20_snapshot("case143_audit_accidental_export")
    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")

    scan = scan_outcome(snap_path, "--against", str(snap_path))
    compare = compare_outcome(snap_path, snap_path)

    assert_full_parity(
        scan=scan,
        compare=compare,
        context="case143 exported_not_public, default severity (baseline verdict parity)",
    )


def test_exported_not_public_baseline_exit_code_parity_under_strict_severity(
    tmp_path: Path,
) -> None:
    """Same fixture, ``--severity-preset strict`` (promotes a RISK-bucket
    finding to an error-level gate): the two tools' *exit codes* must
    agree. Directly reproduces the task's repro -- ``compare`` exits 2 on
    the retained, gating ``exported_not_public`` finding while ``scan
    --against`` still exits 0, because its baseline path stripped the
    finding to advisory before any gate ever saw it.
    """
    snapshot = _g20_snapshot("case143_audit_accidental_export")
    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")

    scan = scan_outcome(
        snap_path, "--against", str(snap_path), "--severity-preset", "strict"
    )
    compare = compare_outcome(snap_path, snap_path, "--severity-preset", "strict")

    assert_full_parity(
        scan=scan,
        compare=compare,
        context="case143 exported_not_public, --severity-preset strict (exit code parity)",
    )


def test_private_header_leak_baseline_verdict_parity(tmp_path: Path) -> None:
    """Same shape as the accidental-export case above, for the other
    cross-source check ``test_crosscheck_parity.py`` uses as its severity
    spot-check fixture (``COMPATIBLE_WITH_RISK``) -- the divergence is the
    stripping mechanism itself, not one specific check kind."""
    snapshot = _g20_snapshot("case144_audit_private_header_leak")
    snap_path = write_snapshot(snapshot, tmp_path / "snap.abi.json")

    scan = scan_outcome(snap_path, "--against", str(snap_path))
    compare = compare_outcome(snap_path, snap_path)

    assert_full_parity(
        scan=scan,
        compare=compare,
        context="case144 private_header_leak, default severity (baseline verdict parity)",
    )
