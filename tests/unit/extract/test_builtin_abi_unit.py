# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Default-lane unit tests for ``extract/builtin_abi.py`` (no compiler run).

The probe's compiler is a :class:`StrictProcessRunner` script, so the probe's
command lines, its generated source and its parse of the program's output are
all checked, with no gcc/clang. The oracle for sizes and encodings is a
hand-written table of the LP64 x86-64 System V ABI (and an ILP32 table for the
target-dependence check), never the module's own dict or probe list.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from _strict_process import StrictProcessRunner, proc

from abicheck.extract import builtin_abi
from abicheck.extract.builtin_abi import (
    BUILTIN_ENCODING,
    BuiltinAbi,
    builtin_type_fact,
    plain_char_is_unsigned,
    probe_builtin_abi,
)
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.fact import Fact

_S = FundamentalEncoding.SIGNED
_U = FundamentalEncoding.UNSIGNED
_B = FundamentalEncoding.BOOLEAN
_F = FundamentalEncoding.FLOAT

#: Hand-written LP64 x86-64 System V oracle: spelling -> (encoding, bytes).
#: Alignment equals size for every scalar here (x86-64 natural alignment).
_LP64_SCALARS: dict[str, tuple[FundamentalEncoding, int]] = {
    "signed char": (_S, 1),
    "unsigned char": (_U, 1),
    "short": (_S, 2),
    "unsigned short": (_U, 2),
    "int": (_S, 4),
    "unsigned int": (_U, 4),
    "long": (_S, 8),
    "unsigned long": (_U, 8),
    "long long": (_S, 8),
    "unsigned long long": (_U, 8),
    "__int128": (_S, 16),
    "unsigned __int128": (_U, 16),
    "_Bool": (_B, 1),
    "float": (_F, 4),
    "double": (_F, 8),
    "long double": (_F, 16),
}

#: Every spelling the module encodes, with the LP64 x86-64 encoding and size.
#: Covers the GCC long-form names, the C23/glibc names and the char family.
_ALL_BUILTIN_ORACLE: tuple[tuple[str, FundamentalEncoding, int], ...] = (
    ("signed char", _S, 1),
    ("unsigned char", _U, 1),
    ("short int", _S, 2),
    ("short", _S, 2),
    ("int", _S, 4),
    ("signed int", _S, 4),
    ("long int", _S, 8),
    ("long", _S, 8),
    ("long long int", _S, 8),
    ("long long", _S, 8),
    ("__int128", _S, 16),
    ("short unsigned int", _U, 2),
    ("unsigned short", _U, 2),
    ("unsigned int", _U, 4),
    ("unsigned", _U, 4),
    ("long unsigned int", _U, 8),
    ("unsigned long", _U, 8),
    ("long long unsigned int", _U, 8),
    ("unsigned long long", _U, 8),
    ("unsigned __int128", _U, 16),
    ("char16_t", _U, 2),
    ("char32_t", _U, 4),
    ("char8_t", _U, 1),
    ("_Bool", _B, 1),
    ("bool", _B, 1),
    ("float", _F, 4),
    ("double", _F, 8),
    ("long double", _F, 16),
    ("_Float16", _F, 2),
    ("_Float32", _F, 4),
    ("_Float64", _F, 8),
    ("_Float128", _F, 16),
    ("_Float32x", _F, 8),
    ("_Float64x", _F, 16),
    ("__float128", _F, 16),
)

#: The ILP32 (32-bit ``long``) target, for the probed-size dependence check.
_ILP32_LONG_LAYOUT: dict[str, tuple[int, int]] = {
    "char": (1, 1),
    "int": (4, 4),
    "unsigned int": (4, 4),
    "long": (4, 4),
    "unsigned long": (4, 4),
    "float": (4, 4),
    "double": (8, 8),
}

#: The probe source lines the module's ``P(...)`` macro must print for.
_PROBED_NAMES = ("char", *_LP64_SCALARS)


def _lp64_layout() -> dict[str, tuple[int, int]]:
    """The probe's ``(size, align)`` for every probed name on LP64 x86-64."""
    layout = {"char": (1, 1)}
    layout.update({name: (size, size) for name, (_enc, size) in _LP64_SCALARS.items()})
    return layout


def _probe_stdout(layout: dict[str, tuple[int, int]]) -> str:
    """The text the probe program prints: ``name size align`` per line."""
    return "".join(f"{name} {size} {align}\n" for name, (size, align) in layout.items())


def _fact(
    encoding: FundamentalEncoding, size: int, lanes: int = 0
) -> Fact[AbiFundamental]:
    """The fact a scalar (or vector, aligned to its own size) must carry."""
    return Fact.present(AbiFundamental(encoding, size, size, lanes))


_LP64 = BuiltinAbi(plain_char_unsigned=False, layout=_lp64_layout())


@pytest.fixture(autouse=True)
def _fresh_probe_memos() -> Any:
    """The probes are process-lifetime memos; each test starts from empty."""
    plain_char_is_unsigned.cache_clear()
    probe_builtin_abi.cache_clear()
    yield
    plain_char_is_unsigned.cache_clear()
    probe_builtin_abi.cache_clear()


def _install(
    monkeypatch: pytest.MonkeyPatch, *steps: dict[str, Any]
) -> StrictProcessRunner:
    """Script the probe's subprocess calls, in order, and patch them in."""
    runner = StrictProcessRunner()
    for step in steps:
        runner.expect(**step)
    runner.install(monkeypatch, builtin_abi, "run_bounded")
    return runner


def _is_probe_compile(argv: tuple[str, ...]) -> bool:
    # cc <opts> -x c <src> -o <exe>
    return argv[-5] == "-x" and argv[-4] == "c" and argv[-2] == "-o"


def _is_probe_run(argv: tuple[str, ...]) -> bool:
    return len(argv) == 1 and argv[0].endswith("/probe")


def _plain_char_step(
    stdout: str = "", returncode: int = 0, raises: BaseException | None = None
) -> dict[str, Any]:
    """The ``cc -m32 -dM -E`` call that reads the predefined macros."""
    step: dict[str, Any] = {"argv": ("cc", "-m32", "-dM", "-E", "-x", "c", os.devnull)}
    if raises is not None:
        step["raises"] = raises
    else:
        step["returns"] = proc(stdout=stdout, returncode=returncode)
    return step


def _compile_step(
    sources: list[str], returncode: int = 0, raises: BaseException | None = None
) -> dict[str, Any]:
    """The ``cc -m32 -x c <src> -o <exe>`` call; records the probe source text."""

    def compile_(argv: tuple[str, ...], _kwargs: dict[str, Any]) -> Any:
        sources.append(Path(argv[-3]).read_text(encoding="utf-8"))
        return proc(returncode=returncode)

    step: dict[str, Any] = {"argv_matches": _is_probe_compile}
    if raises is not None:
        step["raises"] = raises
    else:
        step["returns"] = compile_
    return step


def _run_step(
    stdout: str = _probe_stdout(_lp64_layout()),
    returncode: int = 0,
    raises: BaseException | None = None,
) -> dict[str, Any]:
    """Running the compiled probe program."""
    step: dict[str, Any] = {"argv_matches": _is_probe_run}
    if raises is not None:
        step["raises"] = raises
    else:
        step["returns"] = proc(stdout=stdout, returncode=returncode)
    return step


# ---------------------------------------------------------------------------
# plain_char_is_unsigned: the predefined macro, not a name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("#define __CHAR_UNSIGNED__ 1\n#define __SIZEOF_INT__ 4\n", True),
        ("#define __SIZEOF_INT__ 4\n#define __x86_64__ 1\n", False),
        ("", False),
    ],
    ids=["macro-defined", "macro-absent", "no-macros"],
)
def test_plain_char_follows_the_predefined_macro(
    monkeypatch: pytest.MonkeyPatch, stdout: str, expected: bool
) -> None:
    runner = _install(monkeypatch, _plain_char_step(stdout=stdout))
    assert plain_char_is_unsigned("cc", ("-m32",)) is expected
    runner.assert_exhausted()


@pytest.mark.parametrize(
    "failure",
    [
        {"returncode": 1},
        {"raises": OSError("cc: No such file or directory")},
        {"raises": subprocess.TimeoutExpired(cmd="cc", timeout=60)},
    ],
    ids=["nonzero-exit", "missing-binary", "timeout"],
)
def test_plain_char_is_unknown_when_the_compiler_does_not_run(
    monkeypatch: pytest.MonkeyPatch, failure: dict[str, Any]
) -> None:
    runner = _install(monkeypatch, _plain_char_step(**failure))
    assert plain_char_is_unsigned("cc", ("-m32",)) is None
    runner.assert_exhausted()


def test_plain_char_probe_names_the_target_options_and_devnull_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = {
        "argv": ("cc", "-m32", "-funsigned-char", "-dM", "-E", "-x", "c", os.devnull),
        "returns": proc(stdout="#define __CHAR_UNSIGNED__ 1\n"),
    }
    runner = _install(monkeypatch, step)
    assert plain_char_is_unsigned("cc", ("-m32", "-funsigned-char")) is True
    runner.assert_exhausted()


# ---------------------------------------------------------------------------
# probe_builtin_abi: compile, run, parse
# ---------------------------------------------------------------------------


def test_probe_reports_the_lp64_layout_and_the_plain_char_signedness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources: list[str] = []
    runner = _install(
        monkeypatch,
        _plain_char_step(stdout="#define __CHAR_UNSIGNED__ 1\n"),
        _compile_step(sources),
        _run_step(),
    )
    result = probe_builtin_abi("cc", ("-m32",))
    assert result is not None
    assert result.plain_char_unsigned is True
    assert dict(result.layout) == _lp64_layout()
    runner.assert_exhausted()
    (source,) = sources
    lines = source.splitlines()
    for name in _PROBED_NAMES:
        assert f"P({name})" in lines, name


def test_probe_source_prints_each_builtin_with_its_name_size_and_alignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources: list[str] = []
    _install(
        monkeypatch,
        _plain_char_step(),
        _compile_step(sources),
        _run_step(),
    )
    probe_builtin_abi("cc", ("-m32",))
    (source,) = sources
    assert '#define P(T) printf("%s %zu %zu\\n", #T, sizeof(T), _Alignof(T));' in source
    assert "int main(void) {" in source


def test_probe_reads_the_long_size_the_target_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        _plain_char_step(),
        _compile_step([]),
        _run_step(stdout=_probe_stdout(_ILP32_LONG_LAYOUT)),
    )
    result = probe_builtin_abi("cc", ("-m32",))
    assert result is not None
    assert result.layout["long"] == (4, 4)
    assert result.plain_char_unsigned is False


def test_probe_empty_output_yields_no_layout_but_a_known_signedness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        _plain_char_step(),
        _compile_step([]),
        _run_step(stdout=""),
    )
    assert probe_builtin_abi("cc", ("-m32",)) == BuiltinAbi(
        plain_char_unsigned=False, layout={}
    )


@pytest.mark.parametrize(
    "bad_line",
    ["int 4", "onlyname", ""],
    ids=["two-fields", "one-field", "blank"],
)
def test_probe_line_without_three_fields_drops_the_whole_probe(
    monkeypatch: pytest.MonkeyPatch, bad_line: str
) -> None:
    """A line that is not ``name size align`` makes the whole probe unknown:
    the probe is trusted only when every line parses."""
    _install(
        monkeypatch,
        _plain_char_step(),
        _compile_step([]),
        _run_step(stdout=f"char 1 1\n{bad_line}\n"),
    )
    assert probe_builtin_abi("cc", ("-m32",)) is None


@pytest.mark.parametrize(
    ("stage", "failure"),
    [
        ("compile", {"returncode": 1}),
        ("compile", {"raises": subprocess.TimeoutExpired(cmd="cc", timeout=60)}),
        ("run", {"returncode": 139}),
        ("run", {"raises": OSError("exec format error")}),
    ],
    ids=["compile-exit", "compile-timeout", "run-signal", "run-oserror"],
)
def test_probe_that_does_not_compile_or_run_has_no_layout(
    monkeypatch: pytest.MonkeyPatch, stage: str, failure: dict[str, Any]
) -> None:
    steps: list[dict[str, Any]] = [_plain_char_step(), _compile_step([])]
    if stage == "compile":
        steps[1] = _compile_step([], **failure)
    else:
        steps.append(_run_step(**failure))
    runner = _install(monkeypatch, *steps)
    assert probe_builtin_abi("cc", ("-m32",)) is None
    runner.assert_exhausted()


def test_probe_with_no_plain_char_answer_does_not_compile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the macro answer the probe never compiles: the runner allows
    exactly one call, so a compile attempt would fail the test."""
    runner = _install(monkeypatch, _plain_char_step(returncode=1))
    assert probe_builtin_abi("cc", ("-m32",)) is None
    assert len(runner.calls) == 1


# ---------------------------------------------------------------------------
# builtin_type_fact: the closed builtin set, against the LP64 oracle
# ---------------------------------------------------------------------------


def test_every_builtin_spelling_the_module_encodes_has_an_oracle_row() -> None:
    assert {name for name, _enc, _size in _ALL_BUILTIN_ORACLE} == set(BUILTIN_ENCODING)


@pytest.mark.parametrize(
    ("spelling", "encoding", "size"),
    _ALL_BUILTIN_ORACLE,
    ids=[row[0] for row in _ALL_BUILTIN_ORACLE],
)
def test_builtin_spelling_carries_its_encoding_size_and_natural_alignment(
    spelling: str, encoding: FundamentalEncoding, size: int
) -> None:
    layout = {name: (s, s) for name, _e, s in _ALL_BUILTIN_ORACLE}
    abi = BuiltinAbi(plain_char_unsigned=False, layout=layout)
    assert builtin_type_fact(spelling, abi) == _fact(encoding, size)


@pytest.mark.parametrize(
    "unsigned_char", [False, True], ids=["plain-signed", "plain-unsigned"]
)
def test_plain_char_takes_the_probed_signedness_and_is_one_byte(
    unsigned_char: bool,
) -> None:
    abi = BuiltinAbi(plain_char_unsigned=unsigned_char, layout={"char": (1, 1)})
    expected = _U if unsigned_char else _S
    assert builtin_type_fact("char", abi) == _fact(expected, 1)


def test_plain_char_without_a_probed_layout_has_no_fact() -> None:
    abi = BuiltinAbi(plain_char_unsigned=True, layout={})
    assert builtin_type_fact("char", abi) is None


@pytest.mark.parametrize(
    "spelling",
    [
        "wchar_t",
        "void",
        "size_t",
        "int8_t",
        "uint32_t",
        "enum E",
        "struct S",
        "std::string",
        "_Complex double",
        "double _Complex",
        "float _Complex",
        "_BitInt(8)",
        "const int",
        "volatile unsigned long",
        "int *",
        "int &",
        "int[4]",
        "",
    ],
)
def test_names_outside_the_builtin_set_have_no_fact(spelling: str) -> None:
    """A typedef name, a cv-qualified or pointer spelling, a complex or
    bit-precise type, or a user type is not a builtin here: the caller keeps
    its name-based verdict (the spelling is expected desugared)."""
    assert builtin_type_fact(spelling, _LP64) is None


def test_surrounding_whitespace_is_ignored_but_inner_spacing_is_not() -> None:
    assert builtin_type_fact("  int \n", _LP64) == _fact(_S, 4)
    assert builtin_type_fact("unsigned   int", _LP64) is None


def test_no_probe_means_no_fact_for_any_spelling() -> None:
    assert builtin_type_fact("int", None) is None
    assert builtin_type_fact("__attribute__((vector_size(16))) float", None) is None


def test_scalar_absent_from_the_probed_layout_has_no_fact() -> None:
    """The probe is the only source of size: a spelling it did not report
    is no fact, even though the encoding dict knows its class."""
    abi = BuiltinAbi(plain_char_unsigned=False, layout={"int": (4, 4)})
    assert builtin_type_fact("double", abi) is None
    assert builtin_type_fact("int", abi) == _fact(_S, 4)


@pytest.mark.parametrize(
    "layout",
    [{"int": (0, 0)}, {"int": (4, 0)}, {"int": (0, 4)}],
    ids=["zero-size-and-align", "zero-align", "zero-size"],
)
def test_non_positive_probed_size_or_alignment_has_no_fact(
    layout: dict[str, tuple[int, int]],
) -> None:
    assert builtin_type_fact("int", BuiltinAbi(False, layout)) is None


# ---------------------------------------------------------------------------
# _Atomic of a builtin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spelling", "encoding", "size"),
    [
        ("_Atomic(int)", _S, 4),
        ("_Atomic int", _S, 4),
        ("_Atomic(  unsigned char )", _U, 1),
        ("_Atomic(double)", _F, 8),
        ("_Atomic(_Bool)", _B, 1),
        ("_Atomic(__int128)", _S, 16),
        ("_Atomic(long double)", _F, 16),
    ],
    ids=["paren", "bare", "padded-paren", "double", "bool", "int128", "long-double"],
)
def test_atomic_scalar_is_naturally_aligned_to_its_size(
    spelling: str, encoding: FundamentalEncoding, size: int
) -> None:
    assert builtin_type_fact(spelling, _LP64) == _fact(encoding, size)


@pytest.mark.parametrize(
    "spelling",
    [
        "_Atomic(float __attribute__((vector_size(16))))",
        "_Atomic(struct S)",
        "_Atomic(enum E)",
        "_Atomic(wchar_t)",
        "_Atomic",
        "_Atomic(int",
    ],
)
def test_atomic_of_a_non_scalar_or_malformed_spelling_has_no_fact(
    spelling: str,
) -> None:
    assert builtin_type_fact(spelling, _LP64) is None


# ---------------------------------------------------------------------------
# Vectors: GCC spelling, clang spelling, and the failure branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spelling", "encoding", "size", "lanes"),
    [
        ("float __attribute__((vector_size(16)))", _F, 16, 4),
        ("double __attribute__((vector_size(32)))", _F, 32, 4),
        ("int __attribute__((vector_size(16)))", _S, 16, 4),
        ("unsigned char __attribute__((vector_size(8)))", _U, 8, 8),
        ("__int128 __attribute__((vector_size(32)))", _S, 32, 2),
        ("long double __attribute__((vector_size(32)))", _F, 32, 2),
        ("_Bool __attribute__((vector_size(4)))", _B, 4, 4),
        ("float __attribute__((vector_size(12)))", _F, 12, 3),
    ],
    ids=[
        "float4",
        "double4-avx",
        "int4",
        "uchar8",
        "int128-x2",
        "ldouble-x2",
        "bool4",
        "float3",
    ],
)
def test_gcc_vector_spelling_gives_size_lanes_and_element_class(
    spelling: str, encoding: FundamentalEncoding, size: int, lanes: int
) -> None:
    assert builtin_type_fact(spelling, _LP64) == _fact(encoding, size, lanes)


@pytest.mark.parametrize(
    ("spelling", "encoding", "size", "lanes"),
    [
        ("__attribute__((__vector_size__(2 * sizeof(float)))) float", _F, 8, 2),
        ("__attribute__((__vector_size__(4 * sizeof(int)))) int", _S, 16, 4),
        ("__attribute__((__vector_size__(sizeof(double) * 2))) double", _F, 16, 2),
        (
            "__attribute__((__vector_size__(16 * sizeof(unsigned char)))) unsigned char",
            _U,
            16,
            16,
        ),
        ("__attribute__((__vector_size__(3 * sizeof(float)))) float", _F, 12, 3),
        ("__attribute__((__vector_size__((2 + 2) * sizeof(int)))) int", _S, 16, 4),
    ],
    ids=[
        "float-x2",
        "int-x4",
        "double-x2-rev",
        "uchar-x16",
        "float-x3",
        "parenthesised",
    ],
)
def test_clang_vector_spelling_sizes_the_byte_expression_from_the_probe(
    spelling: str, encoding: FundamentalEncoding, size: int, lanes: int
) -> None:
    assert builtin_type_fact(spelling, _LP64) == _fact(encoding, size, lanes)


def test_clang_vector_sizeof_uses_the_probed_long_size_not_an_lp64_guess() -> None:
    """On a 32-bit ``long`` the same spelling is a different vector: its
    element is 4 bytes, so the two-lane vector is 8 bytes, not 16."""
    abi = BuiltinAbi(plain_char_unsigned=False, layout=_ILP32_LONG_LAYOUT)
    clang = "__attribute__((__vector_size__(2 * sizeof(long)))) long"
    gcc = "long __attribute__((vector_size(8)))"
    assert builtin_type_fact(clang, abi) == _fact(_S, 8, 2)
    assert builtin_type_fact(gcc, abi) == _fact(_S, 8, 2)
    assert builtin_type_fact(clang, _LP64) == _fact(_S, 16, 2)


def test_plain_char_vector_element_follows_the_probed_char_signedness() -> None:
    unsigned = BuiltinAbi(plain_char_unsigned=True, layout={"char": (1, 1)})
    signed = BuiltinAbi(plain_char_unsigned=False, layout={"char": (1, 1)})
    spelling = "char __attribute__((vector_size(16)))"
    assert builtin_type_fact(spelling, unsigned) == _fact(_U, 16, 16)
    assert builtin_type_fact(spelling, signed) == _fact(_S, 16, 16)


@pytest.mark.parametrize(
    "spelling",
    [
        # GCC spelling: the byte count is not a whole number of elements,
        # is zero, names a non-scalar, is complex, or has no vector_size.
        "float __attribute__((vector_size(6)))",
        "float __attribute__((vector_size(0)))",
        "struct S __attribute__((vector_size(16)))",
        "_Complex double __attribute__((vector_size(16)))",
        "float __attribute__((aligned(16)))",
        "__attribute__((vector_size(16)))",
        # Clang spelling: the expression is not a constant the probe knows,
        # is not a sum/product of integers, is not balanced, or is empty.
        "__attribute__((__vector_size__(sizeof(struct S)))) float",
        "__attribute__((__vector_size__(2**3))) float",
        "__attribute__((__vector_size__(N))) float",
        "__attribute__((__vector_size__(0))) float",
        "__attribute__((__vector_size__(6))) float",
        "__attribute__((__vector_size__(16))) struct S",
        "__attribute__((__vector_size__(16))) _Complex double",
        "__attribute__((__vector_size__(()))) float",
        "__attribute__((__vector_size__(16)) float",
        "__attribute__((__vector_size__(16)))",
        "__attribute__((foo(16))) float",
        "__attribute__((__vector_size__(2 +))) float",
        "__attribute__((__vector_size__(* 4))) float",
        # clang prints only the ``__vector_size__`` attribute, never the bare
        # ``vector_size`` attribute-first form: that is no fact here.
        "__attribute__((vector_size(16))) float",
    ],
)
def test_malformed_or_unsupported_vector_spellings_have_no_fact(spelling: str) -> None:
    assert builtin_type_fact(spelling, _LP64) is None


def test_vector_whose_probed_element_size_is_zero_has_no_fact() -> None:
    abi = BuiltinAbi(plain_char_unsigned=False, layout={"int": (0, 0)})
    assert builtin_type_fact("int __attribute__((vector_size(16)))", abi) is None
    assert (
        builtin_type_fact("__attribute__((__vector_size__(sizeof(int)))) int", abi)
        is None
    )


def test_vector_of_an_atomic_element_is_not_a_vector_fact() -> None:
    """A vector element must be a scalar: an ``_Atomic`` element is no fact."""
    assert (
        builtin_type_fact("_Atomic(int) __attribute__((vector_size(16)))", _LP64)
        is None
    )


# ---------------------------------------------------------------------------
# Compiler callable as a strict script: no catch-all answers
# ---------------------------------------------------------------------------


def test_probe_is_not_attempted_for_a_non_builtin_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``builtin_type_fact`` never runs a process: it reads the probe it is
    given. An unscripted call would fail the runner with no expectations."""
    runner = _install(monkeypatch)
    assert builtin_type_fact("struct S", _LP64) is None
    assert runner.calls == []


def test_run_bounded_failures_are_absorbed_as_an_unknown_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run`` turns an OS-level failure into ``None`` for every caller."""

    def boom(_argv: list[str], **_kwargs: Any) -> None:
        raise OSError("no compiler")

    monkeypatch.setattr(builtin_abi, "run_bounded", boom)
    assert builtin_abi._run(["cc", "-dM"]) is None
    assert plain_char_is_unsigned("cc-absent", ()) is None
    assert probe_builtin_abi("cc-absent", ()) is None


def test_run_bounded_result_is_passed_through_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = proc(stdout="ok", returncode=0)
    calls: list[list[str]] = []

    def fake(argv: list[str], **kwargs: Any) -> Any:
        calls.append(argv)
        assert kwargs == {"timeout": 60, "capture_output": True, "text": True}
        return done

    monkeypatch.setattr(builtin_abi, "run_bounded", fake)
    assert builtin_abi._run(["cc", "-dM"]) is done
    assert calls == [["cc", "-dM"]]


@pytest.mark.parametrize(
    "line", ["int four four", "int 4", "long double 16 x", "x -4 4"]
)
def test_garbled_probe_output_gives_no_layout(
    monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    _install(
        monkeypatch,
        _plain_char_step(),
        _compile_step([]),
        _run_step(stdout=line + "\n"),
    )
    assert probe_builtin_abi("cc", ("-m32",)) is None
