"""Detectors excluded only by an explicitly requested ``--depth`` are scope,
not an evidence gap.

They collapse into one ``coverage_warnings`` summary line, stay listed one by
one in ``detectors[]`` (``excluded_by_depth``), are not a PR-comment
limitation, and do not lower confidence below a comparison of snapshots that
never had headers. (Analysis assurance does drop to ``partial`` when the
projected snapshots carried debug info the projection cannot rebuild.) A
detector disabled for any
other reason keeps its own line.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.confidence import (
    DEPTH_EXCLUSION_PREFIX,
    compute_confidence,
    detector_disablement_warning,
)
from abicheck.detectors import DetectorResult
from abicheck.model import AbiSnapshot
from abicheck.report.evidence_summary import evidence_summary
from abicheck.report.review_compute import compact_coverage_warnings


def _excluded(name: str, depth: str = "binary") -> DetectorResult:
    return DetectorResult(
        name=name,
        changes_count=0,
        enabled=False,
        coverage_gap=f"declarations.types not available at depth {depth}",
        not_evaluated=True,
        excluded_by_depth=depth,
    )


_OTHER = DetectorResult(
    name="pe", changes_count=0, enabled=False, coverage_gap="missing PE metadata"
)


@pytest.mark.parametrize("count", [1, 2, 6, 7, 25])
def test_depth_exclusions_collapse_to_one_line(count: int) -> None:
    names = [f"det{i}" for i in range(count)]
    results = [_excluded(n) for n in names] + [_OTHER]
    snap = AbiSnapshot(library="libx.so", version="1")
    _, _, warnings, _ = compute_confidence(results, snap, snap)

    summary = [w for w in warnings if w.startswith(DEPTH_EXCLUSION_PREFIX)]
    assert len(summary) == 1
    assert f"--depth binary: {count} detector" in summary[0]
    # The first names are listed, the rest elided -- never a line per detector.
    assert names[0] in summary[0]
    assert (names[-1] in summary[0]) == (count <= 6)
    assert not any(w.startswith("Detector 'det") for w in warnings)
    # A detector disabled for another reason keeps its individual line.
    assert detector_disablement_warning("pe", "missing PE metadata") in warnings


def test_one_summary_line_per_requested_depth() -> None:
    results = [_excluded("a", "binary"), _excluded("b", "debug"), _excluded("c")]
    snap = AbiSnapshot(library="libx.so", version="1")
    _, _, warnings, _ = compute_confidence(results, snap, snap)
    summary = sorted(w for w in warnings if w.startswith(DEPTH_EXCLUSION_PREFIX))
    assert len(summary) == 2
    assert "--depth binary: 2 detectors" in summary[0]
    assert "--depth debug: 1 detector " in summary[1]


def test_summary_is_not_a_material_limitation() -> None:
    results = [_excluded("a"), _excluded("b"), _OTHER]
    snap = AbiSnapshot(library="libx.so", version="1")
    _, _, warnings, _ = compute_confidence(results, snap, snap)
    report = {
        "coverage_warnings": warnings,
        "detectors": [
            {
                "name": r.name,
                "enabled": r.enabled,
                "coverage_gap": r.coverage_gap,
                "not_evaluated": r.not_evaluated,
                "excluded_by_depth": r.excluded_by_depth,
            }
            for r in results
        ],
    }
    summary = evidence_summary(report)
    assert not any(
        w.startswith(DEPTH_EXCLUSION_PREFIX) for w in summary.coverage_warnings
    )
    assert any(
        w.startswith(DEPTH_EXCLUSION_PREFIX)
        for w in summary.detector_disablement_warnings
    )
    assert not any(
        w.startswith(DEPTH_EXCLUSION_PREFIX)
        for w in compact_coverage_warnings(warnings)
    )


# --- end to end on a real compiled library ---------------------------------

_HEADER_V1 = (
    "struct S { int a; };\nenum E { X, Y };\ntypedef int T;\nint f(struct S*);\n"
)
_HEADER_V2 = _HEADER_V1.replace("int a;", "int a; int b;")


def _build(tmp: Path, tag: str, header: str) -> tuple[Path, Path]:
    gcc = shutil.which("gcc")
    if gcc is None or shutil.which("castxml") is None:
        pytest.skip("gcc and castxml are required")
    h = tmp / f"{tag}.h"
    h.write_text(header)
    c = tmp / f"{tag}.c"
    c.write_text(f'#include "{tag}.h"\nint f(struct S*s){{return s->a;}}\n')
    so = tmp / f"lib{tag}.so"
    proc = subprocess.run(
        [gcc, "-g", "-shared", "-fPIC", str(c), "-o", str(so)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return so, h


def _cli(*args: str) -> int:
    return CliRunner().invoke(main, list(args), catch_exceptions=False).exit_code


@pytest.mark.integration
def test_binary_depth_on_header_snapshots_matches_binary_only(tmp_path: Path) -> None:
    so1, h1 = _build(tmp_path, "v1", _HEADER_V1)
    so2, h2 = _build(tmp_path, "v2", _HEADER_V2)
    snaps = {}
    for tag, so, hdr in (("1", so1, h1), ("2", so2, h2)):
        snaps[f"h{tag}"] = tmp_path / f"h{tag}.json"
        snaps[f"b{tag}"] = tmp_path / f"b{tag}.json"
        assert _cli("dump", str(so), "-H", str(hdr), "-o", str(snaps[f"h{tag}"])) == 0
        assert _cli("dump", str(so), "-o", str(snaps[f"b{tag}"])) == 0

    depth_json, depth_md = tmp_path / "depth.json", tmp_path / "depth.md"
    exit_depth = _cli(
        "compare", str(snaps["h1"]), str(snaps["h2"]), "--depth", "binary",
        "-o", f"json={depth_json}", "-o", f"markdown={depth_md}",
    )  # fmt: skip
    oracle_json = tmp_path / "oracle.json"
    exit_oracle = _cli(
        "compare", str(snaps["b1"]), str(snaps["b2"]), "-o", f"json={oracle_json}"
    )
    depth = json.loads(depth_json.read_text(encoding="utf-8"))
    oracle = json.loads(oracle_json.read_text(encoding="utf-8"))

    excluded = [d for d in depth["detectors"] if d.get("excluded_by_depth")]
    assert len(excluded) > 1
    assert all(d["excluded_by_depth"] == "binary" for d in excluded)
    assert all(d["not_evaluated"] and not d["enabled"] for d in excluded)

    summary = [
        w for w in depth["coverage_warnings"] if w.startswith(DEPTH_EXCLUSION_PREFIX)
    ]
    assert len(summary) == 1
    assert f": {len(excluded)} detectors " in summary[0]
    assert not any("not available at depth" in w for w in depth["coverage_warnings"])
    # Disabled for another reason (which ones depends on the host's binary
    # format): still one line each.
    other = [
        d
        for d in depth["detectors"]
        if not d.get("enabled", True)
        and d.get("excluded_by_depth") is None
        and d.get("coverage_gap")
    ]
    assert other
    for d in other:
        assert (
            detector_disablement_warning(d["name"], d["coverage_gap"])
            in depth["coverage_warnings"]
        )
    md = depth_md.read_text(encoding="utf-8")
    assert md.count(DEPTH_EXCLUSION_PREFIX) == 1
    assert "not available at depth" not in md

    # The excluded detectors are scope, not an evidence gap. What does lower
    # assurance is the debug info (``-g``) the binary-only oracle reads and the
    # header snapshot's projection cannot rebuild: that run reads ``partial``
    # and names it (known-gaps: depth-binary projection), never a clean claim
    # stronger than the oracle's.
    assert exit_depth == exit_oracle
    assert oracle["analysis_assurance"]["status"] == "complete"
    assert depth["analysis_assurance"]["status"] == "partial"
    assert any(
        n.startswith("not assessed at depth binary:")
        for n in depth["analysis_assurance"]["notes"]
    )
    assert depth["confidence"] == oracle["confidence"]
    assert depth.get("scope_resolved", True) and depth.get(
        "manual_review_required"
    ) in (
        None,
        False,
    )
    assert depth["verdict"] == oracle["verdict"]
