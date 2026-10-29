"""DWARF-only comparison of function-pointer types (integration).

``-g`` builds, no headers: the subroutine type's signature and its
``DW_AT_calling_convention`` must reach the comparison.

Per compiler, what DWARF can carry:

* clang writes ``DW_AT_calling_convention`` (0xc1, ``ms_abi``) on the
  ``DW_TAG_subroutine_type``; the DWARF lane reports the change.
* GCC writes no convention for ``ms_abi`` on a subroutine type (verified with
  ``readelf --debug-dump=info`` on GCC 13), so a GCC-built ``ms_abi`` pointer is
  indistinguishable from the default one in DWARF. That cell is an xfail: the
  header lanes are the only evidence for it on GCC.
* A callback whose signature changed is visible in either compiler's DWARF:
  the subroutine type spells its parameters, not the opaque ``fn(...)``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]

_MS_V1 = "void f(void (__attribute__((ms_abi)) *p)(int)) { (void)p; }\n"
_PLAIN_V2 = "void f(void (*p)(int)) { (void)p; }\n"
_LONG_V2 = "void f(void (*p)(long)) { (void)p; }\n"


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.skip(f"{name} not available")
    return path


def _build(compiler: str, src_text: str, out: Path) -> Path:
    src = out.with_suffix(".c")
    src.write_text(src_text, encoding="utf-8")
    lib = out.with_suffix(".so")
    proc = subprocess.run(
        [
            compiler,
            "-g",
            "-O0",
            "-shared",
            "-fPIC",
            "-Wl,-soname,libx.so.1",
            str(src),
            "-o",
            str(lib),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"{compiler} failed: {proc.stderr}")
    return lib


def _compare(old: Path, new: Path, report: Path) -> tuple[int, list[dict]]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        abicheck_command(
            "compare",
            str(old),
            str(new),
            "-o",
            f"json={report}",
        ),
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    changes = json.loads(report.read_text(encoding="utf-8")).get("changes", [])
    return proc.returncode, changes


@pytest.fixture
def work(tmp_path: Path) -> Path:
    return tmp_path


def test_clang_ms_abi_pointer_is_reported_from_dwarf(work: Path) -> None:
    clang = _tool("clang")
    old = _build(clang, _MS_V1, work / "c1")
    new = _build(clang, _PLAIN_V2, work / "c2")
    code, changes = _compare(old, new, work / "clang.json")
    assert code == 4, f"clang ms_abi pointer read as {code}: {changes}"
    assert any(c.get("symbol") == "f" for c in changes), changes


@pytest.mark.xfail(
    strict=True,
    reason="GCC writes no DW_AT_calling_convention for ms_abi on a subroutine type; "
    "DWARF cannot observe the convention, only the header lanes can",
)
def test_gcc_ms_abi_pointer_is_reported_from_dwarf(work: Path) -> None:
    gcc = _tool("gcc")
    old = _build(gcc, _MS_V1, work / "g1")
    new = _build(gcc, _PLAIN_V2, work / "g2")
    code, _changes = _compare(old, new, work / "gcc.json")
    assert code == 4


def test_dwarf_callback_signature_change_is_reported(work: Path) -> None:
    gcc = _tool("gcc")
    old = _build(gcc, _PLAIN_V2, work / "s1")
    new = _build(gcc, _LONG_V2, work / "s2")
    code, changes = _compare(old, new, work / "sig.json")
    assert code == 4, f"callback parameter change read as {code}: {changes}"
    assert any(c.get("symbol") == "f" for c in changes), changes
