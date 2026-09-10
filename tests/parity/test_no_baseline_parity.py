# SPDX-License-Identifier: Apache-2.0
"""F-22/F-23 (plan §7): ``--no-baseline`` audit parity.

``compare --no-baseline NEW`` (plan §3 #2/#16, ADR-068 D2) is the intended
replacement for ``scan ARTIFACT`` without ``--against`` (a single-artifact
audit) and for ``scan --artifact-set`` (an N-library audit, over a
directory). Prerequisite P1 (the ``declared_absent`` acquisition state) and
Phase 2e have now landed for the **single-artifact** shape (plan §3 #2). The
**directory** shape (plan §3 #16, the eventual replacement for
``scan --artifact-set``) still depends on ADR-065 S3's package component
inventories (plan §5 P5, "not started"), so it stays a declared, tested gap
below rather than a silent one.
"""

from __future__ import annotations

import json
from pathlib import Path

from .runner import invoke_cli, scan_json, write_snapshot


def _empty_snapshot(name: str = "libfoo.so"):
    from abicheck.model import AbiSnapshot

    return AbiSnapshot(library=name, version="1.0")


def test_scan_supports_single_artifact_audit_without_against(tmp_path: Path) -> None:
    """F-22: scan's audit mode -- candidate-side findings, no compatibility
    verdict, no --against baseline required at all."""
    path = write_snapshot(_empty_snapshot(), tmp_path / "libfoo.so.abi.json")
    report = scan_json(path)
    assert report["mode"] == "audit"
    assert "diff" not in report or report.get("diff") is None


def test_compare_no_baseline_single_artifact_audit(tmp_path: Path) -> None:
    """F-22, closed: `compare --no-baseline NEW` reports candidate-side
    facts with no addition, no removal, and no compatibility verdict --
    ADR-068 D2, over ADR-065's `declared_absent` acquisition state (plan
    §5 P1)."""
    path = write_snapshot(_empty_snapshot(), tmp_path / "libfoo.so.abi.json")
    result = invoke_cli("compare", "--no-baseline", str(path), "--format", "json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["changes"] == []
    assert report["verdict"] is None
    assert report["run_outcome"]["compatibility"] is None
    assert report["run_outcome"]["scope"] == "complete"
    member = report["comparison_scope"]["members"][0]
    assert member["state"] == "declared_absent"


def test_compare_single_operand_is_a_usage_error_not_an_audit(tmp_path: Path) -> None:
    """ADR-068 D2: arity must never silently imply audit mode -- `compare
    NEW` (one operand) is a declared usage error (exit 64), not a quiet
    fallback to "no baseline". Confirms the gap is a genuine missing
    capability, not merely an unadvertised existing one."""
    path = write_snapshot(_empty_snapshot(), tmp_path / "libfoo.so.abi.json")
    result = invoke_cli("compare", str(path))
    assert result.exit_code == 64, result.output
    assert "Missing argument" in result.output


def test_compare_no_baseline_rejects_two_operands(tmp_path: Path) -> None:
    """ADR-068 D2: `compare --no-baseline OLD NEW` is a usage error too --
    `--no-baseline` and a real OLD operand are mutually exclusive, never
    "OLD is ignored"."""
    old_path = write_snapshot(_empty_snapshot("old.so"), tmp_path / "old.so.abi.json")
    new_path = write_snapshot(_empty_snapshot("new.so"), tmp_path / "new.so.abi.json")
    result = invoke_cli("compare", "--no-baseline", str(old_path), str(new_path))
    assert result.exit_code == 64, result.output


def test_no_command_offers_a_directory_audit_any_more() -> None:
    """F-23, restated after the capability left `scan` too.

    `scan --artifact-set` used to be the N-library, no-baseline audit this
    module tracked as "scan has it, compare doesn't". ADR-068's second
    2026-09-09 amendment rules it (b) -- retired, because preserving its
    per-member manifest/coverage accounting needs ADR-065 S3's package
    component inventories, and routing it onto `compare --no-baseline DIR`
    before those land would silently narrow those guarantees. So the gap is
    now *total*, which is the honest thing to pin: neither command offers a
    directory audit, and `--artifact-set` is not merely undocumented but
    gone from the parser. The single-artifact audit below is unaffected, and
    `test_compare_directory_no_baseline_is_unreachable` pins compare's own
    half.
    """
    assert invoke_cli("scan", "--help-all").exit_code == 0
    assert "--artifact-set" not in invoke_cli("scan", "--help-all").output
    # Not just hidden from help -- rejected by the parser.
    rejected = invoke_cli("scan", "--artifact-set", "release/")
    assert rejected.exit_code != 0
    assert "No such option" in rejected.output
    # The single-artifact audit it scaled up from is untouched: `--against`
    # is still optional, and its absence still means an audit.
    assert "Without --against" in invoke_cli("scan", "--help-all").output


def test_compare_directory_no_baseline_is_unreachable(tmp_path: Path) -> None:
    """F-23: the directory form of --no-baseline (plan §3 #16's "compare
    --no-baseline DIR" replacement for --artifact-set) stays an explicit,
    declared gap -- ADR-065 S3's package component inventories (plan §5 P5)
    haven't landed, so `--no-baseline` itself now exists (Phase 2e) but
    refuses a directory operand with a real usage error rather than
    silently mis-auditing it."""
    lib_dir = tmp_path / "release"
    lib_dir.mkdir()
    write_snapshot(_empty_snapshot(), lib_dir / "libfoo.so.abi.json")

    result = invoke_cli("compare", "--no-baseline", str(lib_dir))
    assert result.exit_code == 64, result.output
    assert "directory" in result.output
