"""End to end: a C++20 library dumped with no build data parses its headers.

Reproduces the reported defect through the real ``dump`` CLI: a public header
whose C++20 syntax (``concept``/``requires``) lives in an included sub-header
used to fail castxml's default C++17 parse (``unknown type name 'T'``) and
abort the run. The binary's own DWARF producer records the dialect it was
built with, so the parse now picks it up. Requires gcc and castxml.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.serialization import load_snapshot
from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds an ELF .so"),
    pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not found"),
    pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml not found"),
]

CONCEPT_H = (
    "#pragma once\ntemplate <typename T>\nconcept Lane = requires(T a) { a + a; };\n"
)
NO_CONCEPT_H = "#pragma once\n"
SIMD_H = (
    "#pragma once\n"
    "#include <cstddef>\n"
    "#include <immintrin.h>\n"
    "#include <xv/detail/lane.h>\n"
    "namespace xv {\n"
    "{template_block}"
    "int add(int a, int b);\n"
    "#ifdef __AVX2__\n"
    "__m256 load8(const float* p);\n"
    "#endif\n"
    "}\n"
)
TEMPLATE_BLOCK = "template <Lane T>\nT twice(T v) { return v + v; }\n"
LIB_CPP = (
    "#include <xv/simd.h>\n"
    "#include <immintrin.h>\n"
    "namespace xv {\n"
    "int add(int a, int b) { return a + b; }\n"
    "#ifdef __AVX2__\n"
    "__m256 load8(const float* p) { return _mm256_loadu_ps(p); }\n"
    "#endif\n"
    "}\n"
)


def _build(root: Path, std: str) -> Path:
    """A ``-mavx2`` shared object built as *std*, with C++20 syntax in a
    header only C++20 builds may contain."""
    include = root / "inc"
    (include / "xv" / "detail").mkdir(parents=True)
    cxx20 = std in ("c++20", "c++23")
    (include / "xv" / "detail" / "lane.h").write_text(
        CONCEPT_H if cxx20 else NO_CONCEPT_H
    )
    (include / "xv" / "simd.h").write_text(
        SIMD_H.replace("{template_block}", TEMPLATE_BLOCK if cxx20 else "")
    )
    src = root / "lib.cpp"
    src.write_text(LIB_CPP)
    so = root / "libxv.so"
    subprocess.run(
        [
            "g++",
            f"-std={std}",
            "-g",
            "-mavx2",
            "-shared",
            "-fPIC",
            f"-I{include}",
            str(src),
            "-o",
            str(so),
        ],
        check=True,
    )
    return so


def _dump(so: Path, out: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    include = so.parent / "inc"
    return subprocess.run(
        abicheck_command(
            "dump",
            str(so),
            "-H",
            str(include / "xv" / "simd.h"),
            "-I",
            str(include),
            "-o",
            str(out),
        ),
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ},
    )


def _function_names(snap) -> set[str]:
    return {fn.name for fn in snap.declarations.functions}


@pytest.mark.parametrize("std", ["c++17", "c++20", "c++23"])
def test_header_parses_without_build_data_for_each_dialect(tmp_path, std):
    so = _build(tmp_path, std)
    out = tmp_path / "old.json"
    result = _dump(so, out, tmp_path)
    assert result.returncode == 0, result.stderr[-2000:]
    snap = load_snapshot(out)
    names = _function_names(snap)
    assert any(n.endswith("add") for n in names), names
    # The -mavx2 the binary records must reach the parse: load8 is declared
    # only under __AVX2__.
    assert any(n.endswith("load8") for n in names), names
    implied = snap.ast_toolchain.get("implied_options", "")
    assert "-mavx2" in implied.split()
    if std in ("c++20", "c++23"):
        assert "-std=gnu++20" in implied.split()
        assert snap.ast_toolchain.get("language_standard_explicit") == "0"
    else:
        assert "-std=gnu++20" not in implied.split()


def test_explicit_standard_from_the_user_is_not_overridden(tmp_path):
    so = _build(tmp_path, "c++20")
    out = tmp_path / "old.json"
    # General compiler flags come from .abicheck.yml's compile block.
    (tmp_path / ".abicheck.yml").write_text(
        "compile:\n  options:\n    - -std=gnu++20\n"
    )
    result = _dump(so, out, tmp_path)
    assert result.returncode == 0, result.stderr[-2000:]
    snap = load_snapshot(out)
    implied = snap.ast_toolchain.get("implied_options", "").split()
    assert not any(token.startswith("-std=") for token in implied)
    assert snap.ast_toolchain.get("language_standard_explicit") == "1"
