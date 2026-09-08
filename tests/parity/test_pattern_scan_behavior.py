# SPDX-License-Identifier: Apache-2.0
"""Behavioral half of the pattern_scan gap (plan §3 #6), **now closed**
(Phase 2b): ``compare`` reaches the same lexical pre-scan ``scan`` always
ran, automatically, on both sides, with no new flag.

``test_engine_primitive_call_sites.py`` proves ``scan_files`` structurally
has ``workflows/lexical_prescan.py`` as a second, legitimate caller now.
This module proves the positive side concretely and cheaply -- ``scan_files``
is pure lexical text scanning (no compiler, no castxml) and really does find
an ABI-risk construct scan surfaces today, over a tiny fixture file, and
``compare`` now surfaces the identical fact through its own JSON report.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.pattern_scan import scan_files

from .gaps import EXPECTED_GAPS
from .runner import compare_json, write_snapshot


def test_scan_files_finds_explicit_template_instantiation(tmp_path: Path) -> None:
    header = tmp_path / "risky.hpp"
    header.write_text("template class Widget<int>;\n", encoding="utf-8")

    result = scan_files([tmp_path])
    kinds = {f.kind.value for f in result.facts}
    assert "explicit_template_instantiation" in kinds
    assert result.files_scanned == 1


def test_the_gap_is_closed_and_deregistered() -> None:
    """`gaps.py` is the migration's definition of done: a capability
    `compare` has reached must not still be listed as scan-only."""
    assert "pattern_scan" not in EXPECTED_GAPS


def _plain_snapshot_path(tmp_path: Path, name: str):
    from abicheck.model import AbiSnapshot

    return write_snapshot(
        AbiSnapshot(library=name, version="1.0"), tmp_path / f"{name}.abi.json"
    )


def test_compare_surfaces_pattern_prescan_for_a_construct_in_scope(
    tmp_path: Path,
) -> None:
    """`compare -H header.hpp` now runs the identical lexical pre-scan on
    both sides and reports the finding through `pattern_prescan`, the
    same construct `test_scan_files_finds_explicit_template_instantiation`
    exercises directly against `scan_files`."""
    old = _plain_snapshot_path(tmp_path, "old")
    new = _plain_snapshot_path(tmp_path, "new")
    header = tmp_path / "risky.hpp"
    header.write_text("template class Widget<int>;\n", encoding="utf-8")

    report = compare_json(old, new, "-H", str(header))
    block = report["pattern_prescan"]
    for side in ("old", "new"):
        kinds = {f["kind"] for f in block[side]["facts"]}
        assert "explicit_template_instantiation" in kinds, block[side]
        assert block[side]["files_scanned"] == 1


def test_compare_pattern_prescan_degrades_honestly_with_nothing_to_scan(
    tmp_path: Path,
) -> None:
    """No `-H`, no `--old/new-sources`: the block is still present (always
    computed, ADR-068 D4/D5's "no opt-in flag"), but each side honestly
    reports zero files scanned rather than being silently omitted."""
    old = _plain_snapshot_path(tmp_path, "old")
    new = _plain_snapshot_path(tmp_path, "new")

    report = compare_json(old, new)
    block = report["pattern_prescan"]
    for side in ("old", "new"):
        assert block[side]["files_scanned"] == 0
        assert block[side]["facts"] == []
