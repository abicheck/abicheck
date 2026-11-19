"""``STRUCT_PACKING_CHANGED`` at ``{B,H}``: packing read from the header layout.

A record that becomes packed (``__attribute__((packed))`` or ``#pragma pack``)
moves its members, and the header-recorded alignment drops below the members'
natural alignment. The oracle is the compiler's own layout: a probe compiled
and run with each compiler prints ``offsetof`` for the record's last member,
so the expected finding follows the real layout, not the detector's formula.

Only the LP64 x86_64 ELF model is decided; the module is Linux-only.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.dumper import dump

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux" or __import__("platform").machine() != "x86_64",
        reason="LP64 x86_64 ELF layout oracle",
    ),
]

_COMPILERS = {"gcc": ("gcc", "g++"), "clang": ("clang", "clang++")}


def _driver(family: str) -> str:
    cc, cxx = _COMPILERS[family]
    candidates = [cc, f"{cc}-19", f"{cc}-18", f"{cc}-17"] if family == "clang" else [cc]
    for name in candidates:
        if shutil.which(name):
            return name
    pytest.skip(f"no {family} compiler on PATH")
    raise AssertionError("unreachable")


def _build(family: str, src: Path, lib: Path) -> Path:
    subprocess.run(
        [_driver(family), "-O0", "-fPIC", "-shared", str(src), "-o", str(lib)],
        check=True,
        capture_output=True,
    )
    return lib


def _last_member_offset(family: str, header: str, probe: Path) -> int:
    """The offset of ``S::b`` as the compiler lays it out (the oracle)."""
    probe.write_text(
        f"#include <stddef.h>\n#include <stdio.h>\n{header}\n"
        'int main(void) { printf("%zu\\n", offsetof(struct S, b)); return 0; }\n',
        encoding="utf-8",
    )
    exe = probe.with_suffix(".bin")
    subprocess.run(
        [_driver(family), str(probe), "-o", str(exe)], check=True, capture_output=True
    )
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
    return int(out.strip())


def _pair(
    tmp: Path, family: str, old_decl: str, new_decl: str
) -> tuple[Path, Path, Path, Path]:
    """Old and new headers and libraries, each declaring ``struct S``."""
    old_h = tmp / "old.h"
    new_h = tmp / "new.h"
    body = "int use_s(struct S *s);\n"
    old_h.write_text(old_decl + body, encoding="utf-8")
    new_h.write_text(new_decl + body, encoding="utf-8")
    impl = "int use_s(struct S *s) { return s->b; }\n"
    old_c = tmp / "old.c"
    new_c = tmp / "new.c"
    old_c.write_text('#include "old.h"\n' + impl, encoding="utf-8")
    new_c.write_text('#include "new.h"\n' + impl, encoding="utf-8")
    old_lib = _build(family, old_c, tmp / f"libold_{family}.so")
    new_lib = _build(family, new_c, tmp / f"libnew_{family}.so")
    return old_h, new_h, old_lib, new_lib


def _packing_kinds(
    tmp: Path, family: str, old_decl: str, new_decl: str
) -> set[ChangeKind]:
    old_h, new_h, old_lib, new_lib = _pair(tmp, family, old_decl, new_decl)
    old = dump(old_lib, headers=[old_h])
    new = dump(new_lib, headers=[new_h])
    return {c.kind for c in compare(old, new).changes}


_NATURAL = "struct S { char a; int b; };\n"
_PACKED = "struct __attribute__((packed)) S { char a; int b; };\n"


@pytest.mark.parametrize("family", ["gcc", "clang"])
def test_a_record_that_becomes_packed_is_structure_packing_changed(
    tmp_path: Path, family: str
) -> None:
    # Oracle: the compiler moves b from offset 4 (natural) to offset 1 (packed).
    assert _last_member_offset(family, _NATURAL, tmp_path / "pn.c") == 4
    assert _last_member_offset(family, _PACKED, tmp_path / "pp.c") == 1
    kinds = _packing_kinds(tmp_path, family, _NATURAL, _PACKED)
    assert ChangeKind.STRUCT_PACKING_CHANGED in kinds, sorted(k.value for k in kinds)


@pytest.mark.parametrize("family", ["gcc", "clang"])
def test_an_alignas_that_raises_alignment_is_not_packing(
    tmp_path: Path, family: str
) -> None:
    """Negative: ``aligned(16)`` raises the record's alignment; it does not pack it."""
    raised = "struct __attribute__((aligned(16))) S { char a; int b; };\n"
    kinds = _packing_kinds(tmp_path, family, _NATURAL, raised)
    assert ChangeKind.STRUCT_PACKING_CHANGED not in kinds, sorted(
        k.value for k in kinds
    )


@pytest.mark.parametrize("family", ["gcc", "clang"])
def test_an_unpacked_record_with_only_char_members_is_not_packing(
    tmp_path: Path, family: str
) -> None:
    """Negative: all-``char`` members are naturally 1-byte aligned, so nothing is packed."""
    chars = "struct S { char a; char b; };\n"
    kinds = _packing_kinds(tmp_path, family, chars, chars)
    assert ChangeKind.STRUCT_PACKING_CHANGED not in kinds, sorted(
        k.value for k in kinds
    )


@pytest.mark.parametrize("family", ["gcc", "clang"])
def test_an_unchanged_packed_record_is_not_a_packing_change(
    tmp_path: Path, family: str
) -> None:
    """Negative: the same packed record on both sides."""
    kinds = _packing_kinds(tmp_path, family, _PACKED, _PACKED)
    assert ChangeKind.STRUCT_PACKING_CHANGED not in kinds, sorted(
        k.value for k in kinds
    )


@pytest.mark.parametrize("family", ["gcc", "clang"])
def test_a_member_of_unknown_alignment_gives_no_packing_finding(
    tmp_path: Path, family: str
) -> None:
    """Negative: a nested record member's alignment is not decided here (unknown,
    never guessed), so packing is not compared even though the layout changed."""
    inner = "struct I { char c; };\n"
    old = inner + "struct S { struct I i; int b; };\n"
    new = inner + "struct __attribute__((packed)) S { struct I i; int b; };\n"
    kinds = _packing_kinds(tmp_path, family, old, new)
    assert ChangeKind.STRUCT_PACKING_CHANGED not in kinds, sorted(
        k.value for k in kinds
    )
