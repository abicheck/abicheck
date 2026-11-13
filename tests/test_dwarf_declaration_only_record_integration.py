"""Known-gap canary: a record the old binary only declares in DWARF reads as added.

GCC emits only a declaration DIE for a class with a virtual base and no key
function in a TU that never constructs it. When the new library carries the
definition, abicheck reads the type as added and the run as COMPATIBLE, so
the 16 -> 8 byte size change and the lost virtual base vanish at DWARF-only
depth. ``docs/contribute/known-gaps.md`` names this canary; when it fails the
gap moved.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed"),
    # The gap is in the ELF/DWARF path; g++ on Windows builds a PE image.
    pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="Linux ELF/DWARF only"
    ),
]

OLD = "struct LB2 { int k; };\nstruct LD2 : virtual LB2 { int d; };\nint ld2_use(LD2* p) { return p->d; }\n"
NEW = "struct LB2 { int k; };\nstruct LD2 : LB2 { int d; };\nint ld2_use(LD2* p) { return p->d; }\n"


def _build(tmp: Path, name: str, src: str) -> Path:
    cpp = tmp / f"{name}.cpp"
    cpp.write_text(src)
    out = tmp / f"lib{name}.so"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O0",
            "-g",
            "-shared",
            "-fPIC",
            str(cpp),
            "-o",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


@pytest.mark.known_gap_canary
def test_declaration_only_old_record_reads_as_added(tmp_path: Path) -> None:
    old, new = _build(tmp_path, "old", OLD), _build(tmp_path, "new", NEW)
    report = tmp_path / "r.json"
    subprocess.run(
        abicheck_command("compare", str(old), str(new), "-o", f"json={report}"),
        check=False,
        capture_output=True,
        timeout=600,
    )
    data = json.loads(report.read_text())
    kinds = {c["kind"] for c in data.get("changes", [])}
    # The gapped answer: only additions, no size or base change, not breaking.
    assert "type_added" in kinds
    assert not kinds & {
        "type_size_changed",
        "base_class_virtual_changed",
        "type_base_changed",
    }
    assert data["verdict"] in ("COMPATIBLE", "NO_CHANGE")
