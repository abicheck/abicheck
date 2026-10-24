"""Real-binary check: a library rebuilt with -mavx512f instead of -mavx2 gets a
compiled-target root finding, and its layout consequences stay visible."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.dumper import dump

# The fixture is ELF x86-64 by construction: `-mavx2` is an x86 flag, and the
# detector reads the ELF producer and GNU-property note. macOS compilers (Apple
# clang on arm64) reject it, and MinGW on Windows emits PE, so those platforms
# have no ELF target to compare. Skip where the premise does not hold; a real
# compiler failure on Linux x86-64 still fails the test.
_ELF_X86_64 = sys.platform.startswith("linux") and platform.machine() in (
    "x86_64",
    "AMD64",
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _ELF_X86_64, reason="ELF x86-64 fixture (Linux x86-64 only)"
    ),
]

_VEC_SOURCE = r"""
#include <immintrin.h>
#if defined(__AVX512F__)
typedef __m512 vec_t;
#else
typedef __m256 vec_t;
#endif
typedef struct { vec_t v; int tag; } Packed;
int packed_tag(const Packed *p) { return p->tag; }
"""


def _build(tmp: Path, name: str, flags: list[str]) -> Path:
    src = tmp / f"{name}.c"
    src.write_text(_VEC_SOURCE, encoding="utf-8")
    so = tmp / f"lib{name}.so"
    res = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-O2", "-g", *flags, str(src), "-o", str(so)],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        # The compiler ran and rejected a flag this test depends on: a real
        # failure, not a missing tool.
        pytest.fail(f"gcc {flags} failed:\n{res.stderr}")
    return so


@pytest.fixture
def avx_pair(tmp_path: Path) -> tuple[Path, Path]:
    if shutil.which("gcc") is None:
        pytest.skip("gcc not found in PATH")
    return _build(tmp_path, "old", ["-mavx2"]), _build(tmp_path, "new", ["-mavx512f"])


def test_avx2_to_avx512f_reports_the_target_root_and_its_consequences(avx_pair):
    old_so, new_so = avx_pair
    result = compare(dump(old_so, []), dump(new_so, []))
    kinds = [c.kind for c in result.changes]

    (root,) = [c for c in result.changes if c.kind is ChangeKind.TARGET_ISA_CHANGED]
    assert root.old_value == "x86-64 (+avx2)"
    assert root.new_value == "x86-64 (+avx512f)"

    # The layout findings the wider vector member causes are still reported...
    assert ChangeKind.TYPE_SIZE_CHANGED in kinds
    assert ChangeKind.TYPE_FIELD_OFFSET_CHANGED in kinds
    # ...and each is annotated as a possible consequence of the root.
    for change in result.changes:
        if change.kind in (
            ChangeKind.TYPE_SIZE_CHANGED,
            ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ):
            assert "Possibly a consequence of the compiled target ISA change" in (
                change.description
            )


def test_same_flags_report_no_target_change(tmp_path: Path):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not found in PATH")
    a = _build(tmp_path, "a", ["-mavx2"])
    b = _build(tmp_path, "b", ["-mavx2"])
    result = compare(dump(a, []), dump(b, []))
    assert ChangeKind.TARGET_ISA_CHANGED not in {c.kind for c in result.changes}
