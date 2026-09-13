from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    shutil.which("cc") is None or shutil.which("abi-dumper") is None,
    reason="requires cc and abi-dumper",
)
def test_compat_accepts_real_abicc_abi_dump(tmp_path: Path) -> None:
    src = tmp_path / "libx.c"
    hdr = tmp_path / "libx.h"
    so = tmp_path / "libx.so"
    dump = tmp_path / "ABI.dump"

    hdr.write_text("int foo(int x);\n", encoding="utf-8")
    src.write_text(
        '#include "libx.h"\nint foo(int x) { return x + 1; }\n', encoding="utf-8"
    )

    subprocess.run(
        [
            "cc",
            "-fPIC",
            "-shared",
            "-g",
            "-Og",
            "-gdwarf-4",
            "-o",
            str(so),
            str(src),
        ],
        check=True,
        cwd=tmp_path,
    )

    subprocess.run(
        [
            "abi-dumper",
            str(so),
            "-o",
            str(dump),
            "-lver",
            "1.0",
            "-public-headers",
            str(tmp_path),
        ],
        check=True,
        cwd=tmp_path,
    )

    proc = subprocess.run(
        [
            "python",
            "-m",
            "abicheck.cli",
            "compat",
            "check",
            "-lib",
            "libx",
            "-old",
            str(dump),
            "-new",
            str(dump),
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "Binary compatibility:" in out
    assert "Verdict:" in out
