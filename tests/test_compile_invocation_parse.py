"""Unit tests for the replayed-token rules of ``compile_invocation`` (plan Phase 3).

The differential golden (``test_compile_invocation_differential.py``) pins the
rendered argv. These tests pin the rules the parse applies to a single
``CompileUnit``, so each branch states its own expectation: an ABI-carried
forced include is kept and deduplicated against the structured argv, an
include-search token scanned from ``argv`` is never deduplicated, and an MSVC
``/I`` is recognized only in CL mode.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.source_extractors.compile_invocation import (
    CarriedFlag,
    parse_compile_invocation,
    replayed_tokens,
    replayed_tokens_for,
)


def _cu(**overrides: object) -> CompileUnit:
    fields: dict[str, object] = {
        "id": "cu://a.cpp",
        "source": "a.cpp",
        "directory": "/work",
        "argv": ["g++", "-c", "a.cpp"],
        "language": "CXX",
    }
    fields.update(overrides)
    return CompileUnit(**fields)  # type: ignore[arg-type]


def test_abi_carried_forced_include_is_kept_and_marked_deduplicated() -> None:
    inv = parse_compile_invocation(
        _cu(abi_relevant_flags=["-include", "cfg.h", "-fPIC"]), "g++"
    )
    assert inv.carried == (
        CarriedFlag("-include", deduplicated=True),
        CarriedFlag("cfg.h", deduplicated=True),
        CarriedFlag("-fPIC", deduplicated=True),
    )


def test_structured_argv_suppresses_only_deduplicated_tokens() -> None:
    inv = parse_compile_invocation(
        _cu(abi_relevant_flags=["-include", "cfg.h", "-fPIC"]), "g++"
    )
    assert replayed_tokens(inv, ["-include", "cfg.h"]) == ["-fPIC"]


def test_argv_scanned_include_search_is_never_suppressed_by_structured_argv() -> None:
    inv = parse_compile_invocation(
        _cu(argv=["g++", "-iquote", "q", "-c", "a.cpp"]), "g++"
    )
    assert inv.carried == (
        CarriedFlag("-iquote", deduplicated=False),
        CarriedFlag("q", deduplicated=False),
    )
    assert replayed_tokens(inv, ["-iquote", "q"]) == ["-iquote", "q"]


@pytest.mark.parametrize(
    ("argv", "compiler", "expected"),
    [
        (
            ["clang-cl", "/I", "inc", "/Iother", "/c", "a.cpp"],
            "clang-cl",
            ["/I", "inc", "/Iother"],
        ),
        # The same spelling is not an include-search option to a GNU driver.
        (["clang-cl", "/I", "inc", "/c", "a.cpp"], "g++", []),
    ],
)
def test_msvc_include_search_is_recognized_only_in_cl_mode(
    argv: list[str], compiler: str, expected: list[str]
) -> None:
    assert replayed_tokens_for(_cu(argv=argv), compiler) == expected


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        # Separate and joined spellings of both GNU include-search options.
        (["g++", "-iquote", "q1", "-idirafter", "d1"], ("q1", "d1")),
        (["g++", "-iquotex", "-idirafterx"], ("x", "x")),
        # A trailing option with no operand contributes nothing and ends the scan.
        (["g++", "-iquote", "q1", "-idirafter"], ("q1",)),
        # MSVC /I, -I and /external:I (separate and joined) are probed in CL
        # mode. The rendered replay still carries only /I (see the carried
        # tests above), so -I and /external:I reach the probe alone.
        (["cl.exe", "/I", "s", "/Ij", "-Ik"], ("s", "j", "k")),
        (["cl.exe", "/external:I", "e", "/external:Ix"], ("e", "x")),
    ],
)
def test_replayed_include_dirs_carries_each_spelling_once_in_order(
    argv: list[str], expected: tuple[str, ...]
) -> None:
    inv = parse_compile_invocation(
        _cu(argv=argv, language="CXX"), "g++" if argv[0] != "cl.exe" else "cl.exe"
    )
    assert inv.replayed_include_dirs == expected
