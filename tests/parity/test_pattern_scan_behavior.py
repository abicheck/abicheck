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

import json
import shutil
import subprocess
from pathlib import Path

import pytest

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
        # plan §3 rows 6/8 (Codex review, finding #4): an explicit coverage
        # status, not just an inferred "0 files scanned" -- distinguishes
        # "not evaluated" from "evaluated, genuinely nothing there".
        assert block[side]["coverage"]["status"] == "not_collected"


@pytest.mark.integration
def test_compare_with_old_new_sources_scans_the_source_tree_not_only_headers(
    tmp_path: Path,
) -> None:
    """Codex review (P1, finding #1): a native ``compare --sources`` run
    must still scan the source tree, not only ``-H``'s header set -- an
    earlier revision passed the CLI's own already-consumed (post-embed,
    reset-to-``None``) ``old_sources``/``new_sources`` locals to the fold,
    silently losing the whole source-tree half of the pre-scan for exactly
    the runs (a real ``--old-sources``/``--new-sources`` compare) it exists
    to cover.

    A real ELF operand is needed (not a bare stub JSON snapshot): the CLI's
    inline-source-embed path dumps the ``--sources`` tree against the given
    library operand -- hence ``integration`` (needs ``gcc``).
    """
    if shutil.which("g++") is None:
        pytest.skip("g++ not on PATH")

    tree = tmp_path / "tree"
    tree.mkdir()
    header = tree / "widget.h"
    header.write_text("int widget_get(void);\n", encoding="utf-8")
    src = tree / "widget.cpp"
    src.write_text(
        '#include "widget.h"\n'
        "template <typename T> struct Widget { T v; };\n"
        "template class Widget<int>;\n"  # only in the SOURCE, not the header
        "int widget_get(void) { return 42; }\n",
        encoding="utf-8",
    )
    (tree / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tree),
                    "command": "c++ -c widget.cpp",
                    "file": str(src),
                }
            ]
        ),
        encoding="utf-8",
    )
    old_so = tmp_path / "old.so"
    new_so = tmp_path / "new.so"
    for out in (old_so, new_so):
        r = subprocess.run(
            ["g++", "-shared", "-fPIC", "-g", "-o", str(out), str(src)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            pytest.skip(f"library compile failed: {r.stderr[:200]}")

    report = compare_json(
        old_so, new_so, "-H", str(header), "--sources", str(tree), "--depth", "build"
    )
    block = report["pattern_prescan"]
    for side in ("old", "new"):
        kinds = {f["kind"] for f in block[side]["facts"]}
        assert "explicit_template_instantiation" in kinds, (side, block[side])
        # header (1) + widget.c (1) -- proves the tree was actually walked.
        assert block[side]["files_scanned"] >= 2, block[side]
