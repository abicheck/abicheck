"""Target-ISA ``-m`` flags from build info must reach the header AST parse.

A header-only SIMD library selects its vector type from predefined macros
(``__AVX2__``, ``__AVX512F__``, ``__SSE4_2__``). Those macros follow the
``-m*`` flags of the translation unit that includes the header. Before the
fix, ``-mavx2``/``-mavx512f``/``-msse4.2`` were dropped from
``CompileUnit.abi_relevant_flags`` by a closed prefix list, so the header was
parsed under the x86-64 baseline (SSE) on every side: the public layout came
out wrong for both builds and the diff reported a variant neither binary
contains.

The bug class is "a target-affecting flag is silently dropped on its way from
build evidence to the header parse". The oracle here is not the capture
helper: the compile database is an ordinary ``compile_commands.json``, and the
expected lane count per ISA is the one the compiler itself defines.

Layers:

* Default (fast) lane: which argv tokens are captured, that the compile-DB
  adapter carries them into the resolved header compile context, and that a
  unit with no ``-m`` flag gains none.
* ``integration``: a real ``dump()`` through castxml and through clang, with
  each side's own tokens, must see the ISA's macros. This exercises both the
  capture and the castxml emulation group (``castxml_compiler_emulation``),
  which copies ``-m*`` into the emulated compiler command.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess  # nosec B404 - runs the real compilers under test
from pathlib import Path
from typing import Any

import pytest

from abicheck.buildsource.abi_flags import is_target_machine_flag
from abicheck.buildsource.adapters.adapter_shared import extract_abi_relevant_flags
from abicheck.buildsource.adapters.compile_db import CompileDbAdapter
from abicheck.buildsource.header_compile_context import resolve_header_compile_context

# Self-contained: no xvec, no third-party header. The lane count is the one
# value each ISA defines, so a parse under the wrong ISA changes ``sizeof``.
_HEADER = """#pragma once
#if defined(__AVX512F__)
#define LANE_COUNT 16
#elif defined(__AVX__)
#define LANE_COUNT 8
#elif defined(__SSE4_2__)
#define LANE_COUNT 4
#else
#define LANE_COUNT 2
#endif
namespace lib {
struct Vec { float v[LANE_COUNT]; };
Vec scale(Vec x, float f);
}
"""

_SOURCE = """#include "api.h"
namespace lib {
Vec scale(Vec x, float f) { for (auto& e : x.v) e *= f; return x; }
}
"""

#: (target flags a build passes, lane count that ISA defines). ``None`` is the
#: mock target that sees no ``-m`` flag at all: the x86-64 baseline.
_ISA_CASES: list[tuple[str | None, int]] = [
    ("-msse4.2", 4),
    ("-mavx", 8),
    ("-mavx2", 8),
    ("-mavx512f", 16),
    (None, 2),
]

_ISA_IDS = [flag or "no-m-flag" for flag, _lanes in _ISA_CASES]

_X86 = platform.machine().lower() in ("x86_64", "amd64")


def _unit_argv(flag: str | None) -> list[str]:
    return ["g++", "-std=c++20", "-c", "lib.cpp", *([flag] if flag else [])]


# ---------------------------------------------------------------------------
# Default lane: capture and the compile-DB -> header context path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag",
    [
        "-mavx2",
        "-mavx512f",
        "-msse4.2",
        "-mavx",
        "-mno-avx",
        "-mfpu=neon",
        "-mcpu=cortex-a72",
        "-mhard-float",
    ],
)
def test_target_machine_flag_is_captured_into_abi_relevant_flags(flag: str) -> None:
    assert flag in extract_abi_relevant_flags(_unit_argv(flag))


def test_mllvm_and_its_operand_are_not_captured() -> None:
    # ``-mllvm`` is backend-only and its operand is parser-only: neither may
    # reach the header parse as a target flag.
    argv = ["c++", "-c", "x.cpp", "-mllvm", "-x86-asm-syntax=intel"]
    assert extract_abi_relevant_flags(argv) == []


def test_a_unit_with_no_target_flag_captures_none() -> None:
    assert extract_abi_relevant_flags(_unit_argv(None)) == ["-std=c++20"]


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("-mavx2", True),
        ("-mno-sse4.2", True),
        ("-m32", True),
        ("-mfpu=neon", True),
        ("-mllvm", False),
        ("-m", False),
        ("-MD", False),
        ("-std=c++20", False),
    ],
)
def test_is_target_machine_flag_family(token: str, expected: bool) -> None:
    assert is_target_machine_flag(token) is expected


def _write_compile_db(root: Path, flag: str | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "api.h").write_text(_HEADER, encoding="utf-8")
    (root / "lib.cpp").write_text(_SOURCE, encoding="utf-8")
    db = root / "compile_commands.json"
    db.write_text(
        json.dumps(
            [
                {
                    "directory": str(root),
                    "file": str(root / "lib.cpp"),
                    "arguments": _unit_argv(flag),
                }
            ]
        ),
        encoding="utf-8",
    )
    return db


@pytest.mark.parametrize(("flag", "lanes"), _ISA_CASES, ids=_ISA_IDS)
def test_compile_db_isa_reaches_the_resolved_header_context(
    tmp_path: Path, flag: str | None, lanes: int
) -> None:
    db = _write_compile_db(tmp_path, flag)
    evidence = CompileDbAdapter(db).collect()
    result = resolve_header_compile_context(evidence, [tmp_path / "api.h"])

    assert result.context is not None
    tokens = list(result.context.gcc_option_tokens)
    machine = [t for t in tokens if is_target_machine_flag(t)]
    if flag is None:
        assert machine == []
    else:
        assert machine == [flag]


def test_two_isas_in_one_database_are_ambiguous_for_a_shared_header(
    tmp_path: Path,
) -> None:
    # Two units of one library built for different ISAs disagree on a flag
    # that changes the header's layout. That must surface as the ambiguity the
    # resolver already reports for any other ABI-relevant disagreement, not be
    # silently collapsed onto whichever unit came first.
    from abicheck.errors import HeaderCompileContextAmbiguousError

    root = tmp_path
    root.mkdir(parents=True, exist_ok=True)
    (root / "api.h").write_text(_HEADER, encoding="utf-8")
    entries: list[dict[str, Any]] = []
    for name, flag in (("a.cpp", "-mavx2"), ("b.cpp", "-mavx512f")):
        (root / name).write_text('#include "api.h"\n', encoding="utf-8")
        entries.append(
            {
                "directory": str(root),
                "file": str(root / name),
                "arguments": ["g++", "-std=c++20", "-c", name, flag],
            }
        )
    db = root / "compile_commands.json"
    db.write_text(json.dumps(entries), encoding="utf-8")
    evidence = CompileDbAdapter(db).collect()

    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(evidence, [root / "api.h"])


# ---------------------------------------------------------------------------
# Integration: a real dump() through castxml and clang
# ---------------------------------------------------------------------------


def _build_shared_object(root: Path, flag: str | None) -> Path:
    (root / "api.h").write_text(_HEADER, encoding="utf-8")
    (root / "lib.cpp").write_text(_SOURCE, encoding="utf-8")
    so = root / "libisa.so"
    cmd = ["g++", "-std=c++20", "-O1", "-fPIC", "-shared", "-o", str(so)]
    if flag:
        cmd.append(flag)
    cmd.append(str(root / "lib.cpp"))
    subprocess.run(cmd, check=True, cwd=root, timeout=120)  # nosec B603 B607
    return so


def _resolved_tokens(root: Path, flag: str | None) -> tuple[str, ...]:
    db = _write_compile_db(root, flag)
    evidence = CompileDbAdapter(db).collect()
    result = resolve_header_compile_context(evidence, [root / "api.h"])
    assert result.context is not None
    return tuple(result.context.gcc_option_tokens)


def _lane_count(snapshot: Any, backend: str) -> int:
    # The lane count the header's macro chain selected, read from what each
    # backend reliably reports. castxml fills the record's layout, so its
    # ``size_bits`` gives the count with no type-string convention involved.
    # clang does not fill record layout, so it is read from the array extent,
    # which clang renders as the element count. (castxml renders the extent as
    # its last index, ``float[N-1]``; that is a rendering convention, not what
    # this test checks.)
    records = snapshot.declarations.types
    vecs = [r for r in records if r.name.endswith("Vec")]
    assert len(vecs) == 1, [r.name for r in records]
    vec = vecs[0]
    if backend == "castxml":
        assert vec.size_bits is not None
        return vec.size_bits // 32
    (lanes,) = vec.fields
    assert lanes.name == "v"
    base, _, rest = lanes.type.partition("[")
    assert base == "float", lanes.type
    return int(rest.rstrip("]"))


_requires_x86_gcc = pytest.mark.skipif(
    not (_X86 and shutil.which("g++")), reason="needs x86-64 and g++"
)


@pytest.mark.integration
@_requires_x86_gcc
@pytest.mark.parametrize(("flag", "lanes"), _ISA_CASES, ids=_ISA_IDS)
def test_castxml_parse_sees_each_sides_isa(
    tmp_path: Path, flag: str | None, lanes: int
) -> None:
    from abicheck.dumper import dump

    so = _build_shared_object(tmp_path, flag)
    snapshot = dump(
        so,
        [tmp_path / "api.h"],
        gcc_option_tokens=_resolved_tokens(tmp_path, flag),
        header_backend="castxml",
        lang="c++",
    )
    assert _lane_count(snapshot, "castxml") == lanes


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")
@_requires_x86_gcc
@pytest.mark.parametrize(("flag", "lanes"), _ISA_CASES, ids=_ISA_IDS)
def test_clang_parse_sees_each_sides_isa(
    tmp_path: Path, flag: str | None, lanes: int
) -> None:
    from abicheck.dumper import dump

    so = _build_shared_object(tmp_path, flag)
    snapshot = dump(
        so,
        [tmp_path / "api.h"],
        gcc_option_tokens=_resolved_tokens(tmp_path, flag),
        header_backend="clang",
        lang="c++",
    )
    assert _lane_count(snapshot, "clang") == lanes
