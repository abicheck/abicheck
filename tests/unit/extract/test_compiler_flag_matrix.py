"""Compiler-flag consumer matrix.

One table, one row per (spelling, compiler family). Each row records what the
three pure consumers must keep from the same argv tokens:

* ``abi``  -- :func:`abicheck.buildsource.abi_flags.is_abi_relevant_flag`
  (per-token capture predicate; a separate operand is not a flag token).
* ``emu``  -- :func:`abicheck.extract.castxml_compiler_emulation.emulation_arguments`
  (what castxml's emulated compiler must also be given).
* ``scan`` -- :func:`abicheck.buildsource.source_extractors.compile_invocation._scan_argv_for_extra_flags`
  (forced-include and include-search tokens carried into the replayed argv).

``None`` means the consumer's contract does not cover that spelling, so the
cell is not asserted.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.abi_flags import is_abi_relevant_flag
from abicheck.buildsource.source_extractors.compile_invocation import (
    _scan_argv_for_extra_flags,
)
from abicheck.extract.castxml_compiler_emulation import emulation_arguments

GCC = ("g++", "gnu")
CLANG = ("clang++", "gnu")
MSVC = ("cl", "msvc")

_TARGET_SEPARATE_GAP = pytest.mark.xfail(
    strict=True,
    reason="known gap: separate --target is not a separate-value flag in emulation",
)

# (label, cc family, tokens, abi, emu, scan)
ROWS = [
    ("std joined", GCC, ["-std=c++17"], True, True, False),
    ("std msvc spelling", MSVC, ["/std:c++17"], True, True, False),
    ("std msvc spelling under gcc", GCC, ["/std:c++17"], True, False, False),
    ("mavx2 joined", GCC, ["-mavx2"], True, True, False),
    ("march joined", GCC, ["-march=x86-64-v3"], True, True, False),
    ("march joined under cl", MSVC, ["-march=x86-64-v3"], True, False, False),
    ("m32", GCC, ["-m32"], True, True, False),
    ("target joined clang", CLANG, ["--target=x86_64-linux-gnu"], True, True, False),
    ("target joined gcc", GCC, ["--target=x86_64-linux-gnu"], True, False, False),
    pytest.param(
        "target separate clang",
        CLANG,
        ["--target", "x86_64-linux-gnu"],
        None,
        True,
        None,
        marks=_TARGET_SEPARATE_GAP,
        id="target separate clang",
    ),
    ("fno-exceptions", GCC, ["-fno-exceptions"], True, True, False),
    ("fexceptions under cl", MSVC, ["-fexceptions"], True, False, False),
    ("x joined c", GCC, ["-xc"], False, True, False),
    ("x joined c++", GCC, ["-xc++"], False, True, False),
    ("x joined under cl", MSVC, ["-xc"], False, False, False),
    ("x separate c", GCC, ["-x", "c"], None, True, None),
    ("sysroot separate", GCC, ["--sysroot", "/sys"], True, True, False),
    ("nostdinc", GCC, ["-nostdinc"], False, True, False),
    ("isystem joined", GCC, ["-isystem/usr/inc"], False, False, False),
    ("isystem separate", GCC, ["-isystem", "/usr/inc"], False, False, False),
    ("iquote separate", GCC, ["-iquote", "dir"], False, False, True),
    ("iquote joined", GCC, ["-iquotedir"], False, False, True),
    ("iquote separate under cl", MSVC, ["-iquote", "dir"], False, False, True),
    ("gnu -I is structured", GCC, ["-I", "inc"], False, False, False),
    ("msvc /I separate", MSVC, ["/I", "inc"], False, False, True),
    ("msvc /I under gcc", GCC, ["/I", "inc"], False, False, False),
    ("msvc /external:I separate", MSVC, ["/external:I", "inc"], False, False, False),
    ("msvc /external:I joined", MSVC, ["/external:Iinc"], False, False, False),
    ("msvc /external:I under gcc", GCC, ["/external:I", "inc"], False, False, False),
    ("gnu -include forced", GCC, ["-include", "cfg.h"], False, False, True),
    ("msvc /FI forced", MSVC, ["/FI", "cfg.h"], False, False, True),
    ("D joined", GCC, ["-DFOO=1"], False, False, False),
    ("D separate", GCC, ["-D", "FOO=1"], False, False, False),
    ("U separate", GCC, ["-U", "FOO"], False, False, False),
    ("mllvm is parser-only", CLANG, ["-mllvm", "-x86-asm"], False, False, False),
]


def _emu(tokens: list[str], family: tuple[str, str]) -> bool:
    return bool(emulation_arguments(tokens, cc_bin=family[0], cc_id=family[1]))


@pytest.mark.parametrize("label,family,tokens,abi,emu,scan", ROWS)
def test_flag_consumer_matrix(
    label: str,
    family: tuple[str, str],
    tokens: list[str],
    abi: bool | None,
    emu: bool | None,
    scan: bool | None,
) -> None:
    if abi is not None:
        assert any(is_abi_relevant_flag(t) for t in tokens) is abi, label
    if emu is not None:
        assert _emu(tokens, family) is emu, label
    if scan is not None:
        assert bool(_scan_argv_for_extra_flags(tokens, family[1])) is scan, label


def test_joined_x_keeps_the_exact_token() -> None:
    # The emulated compiler must receive the language spelling as written.
    assert emulation_arguments(["-xc++"], cc_bin="g++", cc_id="gnu") == ["-xc++"]


@pytest.mark.xfail(
    strict=True,
    reason="known gap: /external:I is not carried into the replayed argv "
    "(test_external_include_does_not_change_the_rendered_replay pins it out)",
)
def test_msvc_external_include_operand_is_carried_with_its_directory() -> None:
    assert _scan_argv_for_extra_flags(["/external:I", "inc"], "msvc") == [
        "/external:I",
        "inc",
    ]
