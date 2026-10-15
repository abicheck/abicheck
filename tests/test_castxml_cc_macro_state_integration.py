"""A macro-spelled calling convention is read as the preprocessor saw it *at
the declaration* (2026-10-11 re-audit, P1).

A final ``-E -dM`` macro table got three shapes wrong on the castxml lane:
``#undef CALL`` after the declaration (convention lost: ``NO_CHANGE`` for a
real ``sysv_abi`` -> ``ms_abi`` switch), a function-like ``CALL(ms_abi)``
(skipped: ``NO_CHANGE``), and a later redefinition of ``CALL`` (the new body
read into the earlier declaration: false ``BREAKING``).

The oracle is the compiler, not abicheck: GCC accepts
``int (__attribute__((ms_abi)) *p)(int, int) = f;`` (with incompatible
pointer types an error) exactly when ``f`` really is ``ms_abi``. Every
spelling x post-declaration macro history x convention is checked against
it, so the whole class -- not the three reported inputs -- is covered.
"""

from __future__ import annotations

import itertools
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.extract.headers.castxml.backend import CastxmlBackend
from abicheck.extract.headers.castxml.calling_convention import (
    source_calling_conventions,
)
from abicheck.extract.headers.header_ast_backend import HeaderParseRequest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (shutil.which("castxml") and shutil.which("gcc")),
        reason="needs castxml and gcc",
    ),
    # ms_abi/sysv_abi are x86-64 conventions; elsewhere they are ignored.
    pytest.mark.skipif(
        platform.machine().lower() not in ("x86_64", "amd64"),
        reason="x86-64 calling conventions",
    ),
]

_ATTR = "__attribute__(({cc}))"

#: How the declaration spells its convention (``{cc}`` is the convention).
_SPELLINGS = {
    "literal": f"{_ATTR} int f(int a, int b);\n",
    "object_macro": f"#define CALL {_ATTR}\nCALL int f(int a, int b);\n",
    "nested_macro": f"#define CC_{{cc}} {_ATTR}\n#define CALL CC_{{cc}}\n"
    "int CALL f(int a, int b);\n",
    "function_macro": "#define CALL(x) __attribute__((x))\nint CALL({cc}) f(int a,\n"
    "    int b);\n",
    "ifdef_macro": f"#define USE_IT\n#ifdef USE_IT\n#define CALL {_ATTR}\n#else\n"
    "#define CALL\n#endif\nCALL int f(int a, int b);\n",
    "redefined_before": "#define CALL __attribute__((fastcall))\n#undef CALL\n"
    f"#define CALL {_ATTR}\nCALL int f(int a, int b);\n",
}

#: What happens to ``CALL`` after the declaration (``{other}`` is the other
#: convention). None of it may change the declaration's convention.
_AFTER = {
    "nothing": "",
    "undef": "#undef CALL\n",
    "redefined_other": "#undef CALL\n#define CALL __attribute__(({other}))\n",
    "redefined_function_like": "#undef CALL\n#define CALL(x) __attribute__(({other}))\n",
}

_CCS = ("ms_abi", "sysv_abi")


def _target_default() -> str:
    """gcc's default x86-64 convention: ms_abi on Windows targets."""
    machine = subprocess.run(
        ["gcc", "-dumpmachine"], capture_output=True, text=True, check=False
    ).stdout.lower()
    windows = any(t in machine for t in ("mingw", "windows", "cygwin", "msys"))
    return "ms_abi" if windows else "sysv_abi"


def _gcc_says_ms_abi(header: Path, scratch: Path) -> bool:
    probe = scratch / "probe.c"
    probe.write_text(
        f'#include "{header}"\nint (__attribute__((ms_abi)) *p)(int, int) = f;\n'
    )
    result = subprocess.run(
        ["gcc", "-fsyntax-only", "-Werror=incompatible-pointer-types", str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return result.returncode == 0


def _abicheck_conventions(header: Path) -> set[str]:
    parser = CastxmlBackend().parse(
        HeaderParseRequest(
            headers=[header], extra_includes=[], public_header_paths=[str(header)]
        )
    )
    ctx = parser._ctx
    found = [
        source_calling_conventions(ctx, el, "f")
        for el in ctx.id_map.values()
        if el.tag == "Function" and el.get("name") == "f"
    ]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize(
    ("spelling", "after", "cc"),
    list(itertools.product(_SPELLINGS, _AFTER, _CCS)),
)
def test_convention_matches_the_compiler(
    tmp_path: Path, spelling: str, after: str, cc: str
) -> None:
    if spelling == "literal" and after != "nothing":
        pytest.skip("no macro to change")
    other = "sysv_abi" if cc == "ms_abi" else "ms_abi"
    header = tmp_path / "api.h"
    header.write_text(
        _SPELLINGS[spelling].format(cc=cc) + _AFTER[after].format(other=other)
    )
    ms = _gcc_says_ms_abi(header, tmp_path)
    assert ms == (cc == "ms_abi")  # the oracle itself sees the declaration's cc
    # The target's own default convention (sysv_abi on SysV targets, ms_abi
    # on Windows) changes nothing and reads as no convention.
    effective = "ms_abi" if ms else "sysv_abi"
    assert _abicheck_conventions(header) == ({effective} - {_target_default()})
