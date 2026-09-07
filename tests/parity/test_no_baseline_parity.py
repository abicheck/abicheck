# SPDX-License-Identifier: Apache-2.0
"""F-22/F-23 (plan §7): ``--no-baseline`` audit parity.

``compare --no-baseline NEW`` (plan §3 #2/#16, ADR-068 D2) is the intended
replacement for ``scan ARTIFACT`` without ``--against`` (a single-artifact
audit) and for ``scan --artifact-set`` (an N-library audit, over a
directory). Neither exists on ``compare`` yet — Phase 1 (``declared_absent``
acquisition state) and Phase 2e haven't landed — so both scenarios are
expected-gap today. This module pins the exact usage-error shape so its own
tests start failing the moment ``--no-baseline`` is accepted at all,
prompting the ``tests/parity/gaps.py`` entries to be reconsidered (a
usage-error test failing "for the right reason" is exactly this harness's
job, per plan §6 Phase 0).
"""

from __future__ import annotations

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


def test_compare_has_no_no_baseline_option(tmp_path: Path) -> None:
    """F-22: `compare` cannot express "declare OLD absent" at all today."""
    path = write_snapshot(_empty_snapshot(), tmp_path / "libfoo.so.abi.json")
    result = invoke_cli("compare", "--no-baseline", str(path))
    assert result.exit_code == 64, result.output
    assert "No such option" in result.output and "--no-baseline" in result.output


def test_compare_single_operand_is_a_usage_error_not_an_audit(tmp_path: Path) -> None:
    """ADR-068 D2: arity must never silently imply audit mode -- `compare
    NEW` (one operand) is a declared usage error (exit 64), not a quiet
    fallback to "no baseline". Confirms the gap is a genuine missing
    capability, not merely an unadvertised existing one."""
    path = write_snapshot(_empty_snapshot(), tmp_path / "libfoo.so.abi.json")
    result = invoke_cli("compare", str(path))
    assert result.exit_code == 64, result.output
    assert "Missing argument" in result.output


def test_scan_supports_directory_audit_without_against() -> None:
    """F-23: `scan --artifact-set` is an audit (no baseline) over N
    libraries in one directory -- `--against` is optional, matching the
    single-artifact audit shape scaled up to a whole release. Checked via
    `--help-all` rather than a real multi-library discovery run:
    `discover_artifact_set` parses real ELF program/dynamic tables, which
    is real-binary-fixture territory (`integration`), not a fast-lane
    concern -- this module's own compare-side test below is what actually
    proves the gap, this is just "scan really has the option"."""
    result = invoke_cli("scan", "--help-all")
    assert result.exit_code == 0, result.output
    assert "--artifact-set" in result.output
    # --against is documented as optional even in --artifact-set mode
    # (an audit, not a comparison) -- see cli_scan.py's own help text.
    assert "Without --against" in result.output


def test_compare_directory_no_baseline_is_unreachable(tmp_path: Path) -> None:
    """F-23: the directory form of --no-baseline (plan §3 #16's "compare
    --no-baseline DIR" replacement for --artifact-set) is unreachable for
    the same reason the single-artifact form is -- the flag doesn't exist."""
    lib_dir = tmp_path / "release"
    lib_dir.mkdir()
    write_snapshot(_empty_snapshot(), lib_dir / "libfoo.so.abi.json")

    result = invoke_cli("compare", "--no-baseline", str(lib_dir))
    assert result.exit_code == 64, result.output
    assert "No such option" in result.output and "--no-baseline" in result.output
