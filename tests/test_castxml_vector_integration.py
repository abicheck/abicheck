"""castxml header path: vector changes are seen through the public compare workflow.

Before the clang join, castxml rendered every vector as ``Unimplemented`` and a
same-size element change (``float x8`` -> ``int x8``) compared ``NO_CHANGE``
through headers. These run ``python -m abicheck compare`` on a gcc-built ELF
with ``-H`` headers. They need Linux (gcc emits ELF there), castxml and clang.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="gcc emits ELF on Linux only"),
    pytest.mark.skipif(
        not (shutil.which("castxml") and shutil.which("clang") and shutil.which("gcc")),
        reason="needs castxml, clang and gcc",
    ),
]

_NO_CHANGE = 0
_BREAKING = 4


def _build(tmp_path: Path, name: str, header: str, source: str) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    header_path = tmp_path / f"{name}.h"
    header_path.write_text(header)
    source_path = tmp_path / f"{name}.c"
    source_path.write_text(f'#include "{name}.h"\n{source}\n')
    so_path = tmp_path / f"lib{name}.so"
    result = subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-g",
            "-w",
            "-Wno-psabi",
            "-I",
            str(tmp_path),
            "-o",
            str(so_path),
            str(source_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return so_path, header_path


def _compare(
    tmp_path: Path, old: tuple[Path, Path], new: tuple[Path, Path]
) -> subprocess.CompletedProcess[str]:
    old_so, old_h = old
    new_so, new_h = new
    return subprocess.run(
        abicheck_command("compare",
            str(old_so),
            str(new_so),
            "-H",
            f"old={old_h}",
            "-H",
            f"new={new_h}",
        ),
        capture_output=True,
        text=True,
        timeout=300,
        cwd=tmp_path,
    )


def test_same_size_element_change_through_typedef_is_breaking(tmp_path: Path) -> None:
    old = _build(
        tmp_path / "o",
        "vec",
        "typedef float f8 __attribute__((vector_size(32)));\n"
        "struct S { f8 a; int n; };\nextern struct S g_s;\n",
        "struct S g_s;\n",
    )
    new = _build(
        tmp_path / "n",
        "vec",
        "typedef int f8 __attribute__((vector_size(32)));\n"
        "struct S { f8 a; int n; };\nextern struct S g_s;\n",
        "struct S g_s;\n",
    )
    result = _compare(tmp_path, old, new)
    assert result.returncode == _BREAKING, result.stdout + result.stderr
    assert "typedef_base_changed" in result.stdout


def test_identical_vector_headers_report_no_change(tmp_path: Path) -> None:
    header = (
        "typedef float f8 __attribute__((vector_size(32)));\n"
        "struct S { f8 a; int n; };\nextern struct S g_s;\n"
    )
    old = _build(tmp_path / "o", "same", header, "struct S g_s;\n")
    new = _build(tmp_path / "n", "same", header, "struct S g_s;\n")
    result = _compare(tmp_path, old, new)
    assert result.returncode == _NO_CHANGE, result.stdout + result.stderr


def test_inline_vector_field_element_change_is_breaking(tmp_path: Path) -> None:
    # No typedef: the field's vector is declared inline, so only its own node
    # carries the element type.
    old = _build(
        tmp_path / "o",
        "inl",
        "struct I { float __attribute__((vector_size(32))) v; int n; };\n"
        "extern struct I g_i;\n",
        "struct I g_i;\n",
    )
    new = _build(
        tmp_path / "n",
        "inl",
        "struct I { int __attribute__((vector_size(32))) v; int n; };\n"
        "extern struct I g_i;\n",
        "struct I g_i;\n",
    )
    result = _compare(tmp_path, old, new)
    assert result.returncode == _BREAKING, result.stdout + result.stderr
    assert (
        "type_field_type_changed" in result.stdout
        or "typedef_base_changed" in result.stdout
    )


def test_inline_vector_parameter_element_change_is_breaking(tmp_path: Path) -> None:
    old = _build(
        tmp_path / "o",
        "par",
        "void inl(float __attribute__((vector_size(16))) p);\n",
        "void inl(float __attribute__((vector_size(16))) p) { (void)p; }\n",
    )
    new = _build(
        tmp_path / "n",
        "par",
        "void inl(int __attribute__((vector_size(16))) p);\n",
        "void inl(int __attribute__((vector_size(16))) p) { (void)p; }\n",
    )
    result = _compare(tmp_path, old, new)
    assert result.returncode == _BREAKING, result.stdout + result.stderr
    assert "func_params_changed" in result.stdout
