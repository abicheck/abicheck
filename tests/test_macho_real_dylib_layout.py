"""Mach-O object sizes and layout findings on real Apple-clang dylibs.

The synthetic tests (``test_macho_object_sizes.py``, ``test_macho_layout_detector.py``)
build images with ``write_dylib``. This module compiles C++ with the host's
Apple clang and links with the real linker, so the chained-fixup and classic
rebase encodings are the ones ld64 actually emits. It runs only on macOS.

Every size oracle is written by hand from the Itanium C++ ABI, not read from the
implementation:

* ``_ZTV4Base`` for ``struct Base { virtual void f(); virtual void g(); }`` is
  offset-to-top, typeinfo, f, g: 4 words = 32 bytes.
* ``_ZTV4Base`` after adding ``virtual void h()`` is 5 words = 40 bytes.
* ``_ZTI4Base`` for a class with no bases is ``__class_type_info``: vptr and
  name, 2 words = 16 bytes.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.macho_metadata import parse_macho_metadata
from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "darwin" or shutil.which("clang++") is None,
        reason="Requires macOS with Apple clang++",
    ),
]

_V1 = """
struct Base { virtual void f(); virtual void g(); };
void Base::f() {}
void Base::g() {}
"""
_V2 = """
struct Base { virtual void f(); virtual void g(); virtual void h(); };
void Base::f() {}
void Base::g() {}
void Base::h() {}
"""
_INSTALL = "-Wl,-install_name,@rpath/libbase.dylib"
#: Default: chained fixups on a modern deployment target. ``-no_fixup_chains``
#: forces the classic rebase/bind encoding.
_ENCODINGS = {
    "chained": [],
    "classic": ["-Wl,-no_fixup_chains"],
}


def _build(src: str, out: Path, extra: list[str]) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    src_file = out.with_suffix(".cpp")
    src_file.write_text(src)
    cmd = [
        "clang++",
        "-shared",
        "-fPIC",
        "-O0",
        _INSTALL,
        *extra,
        "-o",
        str(out),
        str(src_file),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"clang++ failed:\n{result.stderr}"
    return out


def _sizes(path: Path) -> dict[str, int | None]:
    meta = parse_macho_metadata(path)
    return {e.name: e.size for e in meta.exports}


@pytest.mark.parametrize("encoding", sorted(_ENCODINGS))
@pytest.mark.parametrize("stripped", [False, True], ids=["unstripped", "strip-x"])
def test_real_dylib_object_sizes_match_the_itanium_layout(
    tmp_path: Path, encoding: str, stripped: bool
) -> None:
    lib = _build(_V1, tmp_path / "libbase.dylib", _ENCODINGS[encoding])
    if stripped:
        subprocess.run(["strip", "-x", str(lib)], check=True)
    sizes = _sizes(lib)
    assert sizes.get("ZTV4Base") == 32, sizes
    assert sizes.get("ZTI4Base") == 16, sizes


@pytest.mark.parametrize("encoding", sorted(_ENCODINGS))
def test_vtable_growth_on_real_dylibs_is_a_slot_change(
    tmp_path: Path, encoding: str
) -> None:
    old = _build(_V1, tmp_path / "old" / "libbase.dylib", _ENCODINGS[encoding])
    new = _build(_V2, tmp_path / "new" / "libbase.dylib", _ENCODINGS[encoding])
    assert _sizes(old)["ZTV4Base"] == 32
    assert _sizes(new)["ZTV4Base"] == 40
    cmp = subprocess.run(
        abicheck_command("compare", str(old), str(new)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert "VTABLE_SLOT_COUNT_CHANGED" in cmp.stdout + cmp.stderr, (
        cmp.stdout + cmp.stderr
    )


@pytest.mark.parametrize("encoding", sorted(_ENCODINGS))
def test_unchanged_rebuild_on_real_dylibs_has_no_slot_change(
    tmp_path: Path, encoding: str
) -> None:
    a = _build(_V1, tmp_path / "a" / "libbase.dylib", _ENCODINGS[encoding])
    b = _build(_V1, tmp_path / "b" / "libbase.dylib", _ENCODINGS[encoding])
    cmp = subprocess.run(
        abicheck_command("compare", str(a), str(b)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert "VTABLE_SLOT_COUNT_CHANGED" not in cmp.stdout + cmp.stderr, (
        cmp.stdout + cmp.stderr
    )
    assert cmp.returncode == 0, cmp.stdout + cmp.stderr
