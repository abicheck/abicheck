"""`--depth binary` reads the debug info a binary carries -- and says so.

Known gap "`compare --depth binary` still performs a deep DWARF type walk the
public evidence-depth contract says that rung skips" was a disagreement
between code and docs, resolved by the maintainer (2026-10-01) in favour of
the code: `policy/depth_projection.py` keeps L1 facts at `binary`, so a
struct layout break in DWARF is still caught with no headers at all. These
tests pin both halves so they cannot drift apart again: the behaviour,
through the real CLI on real compiled binaries, and every place the rung is
described to a user.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

_ROOT = Path(__file__).resolve().parent.parent

#: Every user-facing description of the `binary` rung.
_DOC_ROWS = (
    _ROOT / "docs/use/evidence-depth.md",
    _ROOT / "docs/learn/evidence-and-detectability.md",
)


@pytest.mark.parametrize("doc", _DOC_ROWS, ids=lambda p: p.name)
def test_docs_do_not_promise_binary_skips_debug_info(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| `binary` |")]
    assert rows
    for row in rows:
        assert "DWARF" in row
        for retired in ("no deep DWARF type walk", "debug-info *presence*"):
            assert retired not in row
    assert "no deep DWARF type walk" not in text


@pytest.mark.parametrize("command", ["compare", "dump"])
def test_depth_help_does_not_say_symbols_only(command: str) -> None:
    out = CliRunner().invoke(main, [command, "--help"], terminal_width=400).output
    help_text = " ".join(out.split())
    match = re.search(r"--depth .*?binary=([^,]+),", help_text)
    assert match, help_text
    assert "DWARF" in match.group(1)
    assert "symbols only" not in match.group(1)


_STRUCTS = {
    "field_added": (
        "struct P { int x, y; };",
        "struct P { int x, y, z; };",
        "type_size_changed",
    ),
    "field_widened": (
        "struct P { int x; int y; };",
        "struct P { long x; int y; };",
        "type_size_changed",
    ),
    "field_reordered": (
        "struct P { int x; char c; };",
        "struct P { char c; int x; };",
        None,
    ),
}


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="ELF/DWARF tests require Linux")
@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
@pytest.mark.parametrize("case", sorted(_STRUCTS))
def test_depth_binary_reports_dwarf_layout_breaks_without_headers(
    tmp_path: Path, case: str
) -> None:
    old_src, new_src, expected_kind = _STRUCTS[case]
    for side, body in (("old", old_src), ("new", new_src)):
        src = tmp_path / f"{side}.c"
        src.write_text(f"{body}\nint use(struct P *p) {{ return sizeof *p; }}\n")
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-g",
                str(src),
                "-o",
                str(tmp_path / f"lib{side}.so"),
            ],
            check=True,
        )
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(tmp_path / "libold.so"),
            str(tmp_path / "libnew.so"),
            "--depth",
            "binary",
            "-o",
            "json=-",
        ],
    )
    doc = json.loads(result.output[result.output.index("{") :])
    kinds = {c["kind"] for c in doc["changes"]}
    # Every case is a real layout change; the rung must not read as clean.
    assert doc["verdict"] != "NO_CHANGE", kinds
    if expected_kind:
        assert expected_kind in kinds
