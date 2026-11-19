"""``STRUCT_RETURN_CONVENTION_CHANGED`` at ``{B,H}``: the SysV return class from the header layout.

A record returned by value comes back in registers up to 16 bytes and through a
hidden caller-provided pointer (sret) above that. The oracle is the compiler's
own code generation: a caller that passes its explicit argument in ``%rsi``
passes a hidden return pointer in ``%rdi`` (the sret case); one that passes it
in ``%rdi`` returns in registers. The detector's answer is compared to that
generated call, not to its own classifier.

Only the LP64 x86_64 ELF model is decided; the module is Linux x86_64 only.
"""

from __future__ import annotations

import platform
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
        sys.platform != "linux" or platform.machine() != "x86_64",
        reason="LP64 x86_64 ELF return-convention oracle",
    ),
]


def _driver(family: str) -> str:
    candidates = (
        [f"{family}-19", f"{family}-18", f"{family}-17", family]
        if family == "clang"
        else [family]
    )
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


_IMPL = "struct S mk(long a) { struct S s; {FILL} return s; }\n"


def _fill(size: int) -> str:
    """Set every eightbyte of a *size*-byte ``struct S`` from ``a``."""
    return " ".join(f"s.{m} = a + {i};" for i, m in enumerate("abc"[: size // 8]))


def _lib(tmp: Path, family: str, name: str, decl: str, size: int) -> tuple[Path, Path]:
    header = tmp / f"{name}.h"
    header.write_text(decl + "struct S mk(long a);\n", encoding="utf-8")
    source = tmp / f"{name}.c"
    body = _IMPL.replace("{FILL}", _fill(size))
    source.write_text(f'#include "{name}.h"\n' + body, encoding="utf-8")
    return header, _build(family, source, tmp / f"lib{name}_{family}.so")


def _kinds(
    tmp: Path, family: str, old_decl: str, new_decl: str, old_size: int, new_size: int
) -> set[ChangeKind]:
    old_h, old_lib = _lib(tmp, family, "old", old_decl, old_size)
    new_h, new_lib = _lib(tmp, family, "new", new_decl, new_size)
    old = dump(old_lib, headers=[old_h])
    new = dump(new_lib, headers=[new_h])
    return {c.kind for c in compare(old, new).changes}


def _uses_hidden_return_pointer(family: str, decl: str, tmp: Path) -> bool:
    """Oracle: does the compiler pass the explicit argument in ``%rsi`` (or ``%esi``)?

    With a memory-class return, the hidden pointer takes ``%rdi`` and the one
    explicit argument moves to ``%rsi``. Compile a caller and look at the
    setup of its ``call``.
    """
    caller = tmp / "caller.c"
    caller.write_text(
        decl
        + "struct S mk(long a);\nint use(void) { struct S r = mk(7); return (int)r.a; }\n",
        encoding="utf-8",
    )
    asm = tmp / "caller.s"
    subprocess.run(
        [_driver(family), "-O0", "-S", str(caller), "-o", str(asm)],
        check=True,
        capture_output=True,
    )
    lines = asm.read_text(encoding="utf-8").splitlines()
    call = next(i for i, line in enumerate(lines) if "call" in line and "mk" in line)
    setup = "\n".join(lines[max(0, call - 8) : call])
    return "%rsi" in setup or "%esi" in setup


_OLD_REG = "struct S { long a; long b; };\n"
_NEW_MEM = "struct S { long a; long b; long c; };\n"


@pytest.mark.parametrize("family", ["gcc", "clang"])
def test_a_return_that_grows_past_the_registers_is_struct_return_convention_changed(
    tmp_path: Path, family: str
) -> None:
    # Oracle: the compiler returns the 16-byte struct in registers and the
    # 24-byte struct through a hidden pointer.
    assert not _uses_hidden_return_pointer(family, _OLD_REG, tmp_path)
    assert _uses_hidden_return_pointer(family, _NEW_MEM, tmp_path)
    kinds = _kinds(tmp_path, family, _OLD_REG, _NEW_MEM, 16, 24)
    assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED in kinds, sorted(
        k.value for k in kinds
    )


@pytest.mark.parametrize("family", ["gcc", "clang"])
def test_an_unchanged_register_return_is_not_a_convention_change(
    tmp_path: Path, family: str
) -> None:
    """Negative: the same 16-byte struct on both sides."""
    kinds = _kinds(tmp_path, family, _OLD_REG, _OLD_REG, 16, 16)
    assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED not in kinds, sorted(
        k.value for k in kinds
    )


@pytest.mark.parametrize("family", ["gcc", "clang"])
def test_a_growth_that_is_never_returned_by_value_is_not_a_convention_change(
    tmp_path: Path, family: str
) -> None:
    """Negative: the record grows, but no declared function returns it by value.

    The function takes it by pointer, so no caller's return convention moves.
    """
    old_h = tmp_path / "old.h"
    new_h = tmp_path / "new.h"
    old_h.write_text(_OLD_REG + "long use(struct S *s);\n", encoding="utf-8")
    new_h.write_text(_NEW_MEM + "long use(struct S *s);\n", encoding="utf-8")
    body = "long use(struct S *s) { return s->a; }\n"
    old_c = tmp_path / "old.c"
    new_c = tmp_path / "new.c"
    old_c.write_text('#include "old.h"\n' + body, encoding="utf-8")
    new_c.write_text('#include "new.h"\n' + body, encoding="utf-8")
    old = dump(_build(family, old_c, tmp_path / "lo.so"), headers=[old_h])
    new = dump(_build(family, new_c, tmp_path / "ln.so"), headers=[new_h])
    kinds = {c.kind for c in compare(old, new).changes}
    assert ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED not in kinds, sorted(
        k.value for k in kinds
    )
