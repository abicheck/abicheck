"""ADR-095 end to end: a real stripped library + headers through the CLI.

The library is built with gcc and a layout flag the header does not carry
(``-fpack-struct=1`` / ``-fshort-enums``), so the binary's layout breaks while
the header layout (parsed without the flag) absorbs the edit. The CLI must not
read that as a clean ``COMPATIBLE``. With DWARF kept, or with the flag passed
to the header parse, the reduced-assurance kind must not appear.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = pytest.mark.integration

_CASES = {
    "pack": (
        "-fpack-struct=1",
        "struct FC { int a; char b; };\nint fc_use(struct FC* p);\n",
        "struct FC { int a; char b; char c; };\nint fc_use(struct FC* p);\n",
    ),
    "short_enums": (
        "-fshort-enums",
        "enum color { RED, GREEN, BLUE };\nint color_use(enum color* c);\n",
        "enum color { RED, GREEN, BLUE, WIDE = 300 };\nint color_use(enum color* c);\n",
    ),
}


def _build(
    tmp: Path, side: str, flag: str, header: str, *, strip: bool
) -> tuple[Path, Path]:
    d = tmp / side
    d.mkdir()
    h = d / "api.h"
    h.write_text(header)
    src = d / "api.c"
    src.write_text(
        '#include "api.h"\nint fc_use(struct FC* p) { return p ? 1 : 0; }\n'
        if "FC" in header
        else '#include "api.h"\nint color_use(enum color* c) { return c ? 1 : 0; }\n'
    )
    so = d / "libapi.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", flag, "-o", str(so), str(src)], check=True
    )
    if strip:
        subprocess.run(["strip", "--strip-debug", str(so)], check=True)
    return so, h


def _compare(
    tmp: Path, case: str, *, strip: bool, extra: list[str]
) -> tuple[str, set[str]]:
    if not shutil.which("gcc") or not shutil.which("strip"):
        pytest.skip("gcc/strip not available")
    flag, old_h, new_h = _CASES[case]
    old_so, old_hdr = _build(tmp, "old", flag, old_h, strip=strip)
    new_so, new_hdr = _build(tmp, "new", flag, new_h, strip=strip)
    out = tmp / "report.json"
    proc = subprocess.run(
        abicheck_command(
            "compare",
            str(old_so),
            str(new_so),
            "--header",
            f"old={old_hdr}",
            "--header",
            f"new={new_hdr}",
            "-o",
            f"json={out}",
            *extra,
        ),
        capture_output=True,
        text=True,
    )
    assert out.exists(), proc.stderr
    report = json.loads(out.read_text())
    kinds = {c["kind"] for c in report["changes"]}
    return report["verdict"], kinds


@pytest.mark.parametrize("case", sorted(_CASES))
def test_stripped_headers_cannot_read_clean_compatible(
    tmp_path: Path, case: str
) -> None:
    verdict, kinds = _compare(tmp_path, case, strip=True, extra=[])
    assert "layout_build_config_unverified" in kinds
    assert verdict != "COMPATIBLE"


@pytest.mark.parametrize("case", sorted(_CASES))
def test_dwarf_evidence_silences_the_signal(tmp_path: Path, case: str) -> None:
    _, kinds = _compare(tmp_path, case, strip=False, extra=[])
    assert "layout_build_config_unverified" not in kinds
