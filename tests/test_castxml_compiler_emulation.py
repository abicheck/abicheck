"""Which compiler arguments castxml's emulated compiler must also receive.

``castxml --castxml-cc-<id> <cc>`` asks *cc* for its predefined macros and
system include directories, passing it only the arguments inside the
``( <cc> ... )`` group. A macro-changing flag written only after the group
reaches castxml's parser but not that query, so the parse runs under one
configuration and the macros of another. That broke every C++20 header that
uses ``<concepts>`` under the default ``--castxml-cc-gnu g++`` emulation:
g++ reported ``__cplusplus 201703L`` and libstdc++ declared no
``std::integral``.

Two layers of tests:

* Properties of :func:`emulation_arguments` over generated argument lists
  (order, value pairing, the compiler-family allowlists).
* An end-to-end oracle against a **real** compiler: for each flag that
  changes a predefined macro, castxml run through abicheck's own command
  builder must report the same macro values as ``g++ <flag> -dM -E``. The
  oracle is the compiler, not the helper's allowlist.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - runs the real compilers under test
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from abicheck.extract.castxml_compiler_emulation import (
    emulated_compiler_command,
    emulation_arguments,
)

# Arguments that change what a GNU-family compiler reports.
_GNU_MACRO_FLAGS = [
    "-std=c++20",
    "-std=gnu++14",
    "-std=c11",
    "--sysroot=/opt/sysroot",
    "-nostdinc",
    "-nostdinc++",
    "-m32",
    "-mavx2",
    "-march=x86-64-v3",
    "-mno-sse4.2",
    "-O2",
    "-Os",
    "-fno-exceptions",
    "-fno-rtti",
    "-fopenmp",
    "-fsized-deallocation",
    "-funsigned-char",
    "-fPIC",
    "-pthread",
    "-ansi",
]
# Arguments that change nothing the emulated compiler reports, or that the
# parser owns (castxml applies -D/-I/-include itself).
_NEUTRAL_FLAGS = [
    "-DFOO=1",
    "-UNDEBUG",
    "-Wall",
    "-Wno-deprecated",
    "-MD",
    "-MF",
    "-g",
    "-fno-delayed-template-parsing",
    "-fdiagnostics-color=always",
    "-Werror",
]
_CLANG_ONLY = ["--target=aarch64-linux-gnu", "-stdlib=libc++"]

_token = st.sampled_from(_GNU_MACRO_FLAGS + _NEUTRAL_FLAGS + _CLANG_ONLY)
_arguments = st.lists(_token, max_size=12)


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(tok in it for tok in needle)


@given(_arguments)
def test_kept_arguments_are_an_ordered_subsequence(arguments: list[str]) -> None:
    """Order is preserved, so a later ``-std=`` still wins inside the group."""
    kept = emulation_arguments(arguments, cc_bin="g++", cc_id="gnu")
    assert _is_subsequence(kept, arguments)


@given(_arguments)
def test_gnu_keeps_exactly_the_macro_changing_flags(arguments: list[str]) -> None:
    kept = emulation_arguments(arguments, cc_bin="/usr/bin/g++", cc_id="gnu")
    expected = [tok for tok in arguments if tok in _GNU_MACRO_FLAGS]
    assert kept == expected


@given(_arguments)
def test_clang_emulation_also_keeps_clang_only_spellings(arguments: list[str]) -> None:
    """A Clang binary accepts ``--target=``/``-stdlib=``; GCC rejects them."""
    kept = emulation_arguments(arguments, cc_bin="/opt/llvm/bin/clang++", cc_id="gnu")
    expected = [tok for tok in arguments if tok in _GNU_MACRO_FLAGS + _CLANG_ONLY]
    assert kept == expected


@given(_arguments)
def test_msvc_emulation_never_receives_gnu_flags(arguments: list[str]) -> None:
    assert emulation_arguments(arguments, cc_bin="cl.exe", cc_id="msvc") == []


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["/std:c++20", "/W4", "/Zc:__cplusplus"], ["/std:c++20", "/Zc:__cplusplus"]),
        (["-std:c++17", "/EHsc"], ["-std:c++17"]),
        (["/DFOO", "/I", "inc"], []),
    ],
)
def test_msvc_keeps_standard_selection(
    arguments: list[str], expected: list[str]
) -> None:
    assert emulation_arguments(arguments, cc_bin="cl.exe", cc_id="msvc") == expected


@pytest.mark.parametrize(
    ("arguments", "cc_bin", "expected"),
    [
        (["-x", "c", "-std=gnu11"], "gcc", ["-x", "c", "-std=gnu11"]),
        (["--sysroot", "/sr", "-I", "inc"], "g++", ["--sysroot", "/sr"]),
        # A value that looks like a flag is still the previous flag's value.
        (["-x", "-std=c++20"], "g++", ["-x", "-std=c++20"]),
        # GCC accepts -isysroot too, and it moves the system search path.
        (["-isysroot", "/sdk"], "g++", ["-isysroot", "/sdk"]),
        (["-isysroot", "/sdk"], "clang++", ["-isysroot", "/sdk"]),
        # A trailing separate-value flag with no value is dropped, not paired.
        (["-x"], "g++", []),
    ],
)
def test_separate_value_flags_travel_with_their_value(
    arguments: list[str], cc_bin: str, expected: list[str]
) -> None:
    assert emulation_arguments(arguments, cc_bin=cc_bin, cc_id="gnu") == expected


@pytest.mark.parametrize("cc_bin", ["g++", "clang++"])
@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["-mllvm", "-inline-threshold=100", "-std=c++20"], ["-std=c++20"]),
        (["-Xclang", "-mllvm", "-Xclang", "-x", "-mavx2"], ["-mavx2"]),
        (["-Xclang", "-fno-validate-pch", "-O2"], ["-O2"]),
        (["-mllvm=-foo", "-O2"], ["-O2"]),
        (["-O2", "-mllvm"], ["-O2"]),
    ],
)
def test_parser_only_value_flags_never_reach_the_emulated_compiler(
    arguments: list[str], expected: list[str], cc_bin: str
) -> None:
    """``-mllvm``/``-Xclang`` and their operand stay with castxml's parser.

    ``-mllvm`` would otherwise match the ``-m*`` rule and reach GCC without
    its operand, and an operand behind ``-Xclang`` (including a nested
    ``-mllvm`` or ``-x``) must not be read as a flag of its own.
    """
    assert emulation_arguments(arguments, cc_bin=cc_bin, cc_id="gnu") == expected


def test_group_form_only_when_something_is_kept() -> None:
    assert emulated_compiler_command("g++", "gnu", ["-I", "inc", "-DX"]) == ["g++"]
    assert emulated_compiler_command("g++", "gnu", ["-I", "inc", "-std=c++20"]) == [
        "(",
        "g++",
        "-std=c++20",
        ")",
    ]


def test_source_replay_builder_gives_the_unit_context_to_the_emulated_compiler() -> (
    None
):
    """The L4 builder uses the same rule as the L2 one.

    g++ is given the unit's standard and sysroot, so the macros and system
    directories castxml asks it for match the parse. ``--target=`` is a
    Clang-only spelling g++ rejects, so it stays with the parser only.
    """
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors.castxml import build_castxml_command

    cu = CompileUnit(
        id="cu://src/foo.cpp#cfg",
        source="src/foo.cpp",
        directory="/proj",
        language="CXX",
        standard="c++20",
        sysroot="/sysroot",
        target_triple="aarch64-linux-gnu",
    )
    cmd = build_castxml_command(cu, Path("src/foo.cpp"), Path("out.xml"))
    group = cmd[3 : cmd.index(")") + 1]
    assert group == ["(", "g++", "-std=c++20", "--sysroot=/sysroot", ")"]
    parser_args = cmd[3 + len(group) :]
    assert "-std=c++20" in parser_args
    assert "--target=aarch64-linux-gnu" in parser_args


# ── End-to-end oracle: a real compiler ─────────────────────────────────────

_WATCHED_MACROS = (
    "__cplusplus",
    "__cpp_concepts",
    "__cpp_sized_deallocation",
    "__cpp_exceptions",
    "__EXCEPTIONS",
    "__GXX_RTTI",
    "_OPENMP",
    "__OPTIMIZE__",
    "__OPTIMIZE_SIZE__",
    "__CHAR_UNSIGNED__",
    "__AVX2__",
    "__SSE4_2__",
    "__pic__",
    "__STRICT_ANSI__",
)

# Every flag here is one the compiler turns into a different value for at least one
# watched macro, checked below so the oracle cannot go vacuous.
_ORACLE_FLAGS = [
    "-std=c++20",
    "-std=gnu++14",
    "-mavx2",
    "-msse4.2",
    "-O2",
    "-Os",
    "-fno-exceptions",
    "-fno-rtti",
    "-fopenmp",
    "-fno-sized-deallocation",
    "-funsigned-char",
    "-ansi",
]

# Flags whose effect depends on how the host compiler was configured (a
# PIE-by-default GCC already defines ``__pic__``). Each pair is checked
# against the oracle like any flag; the vacuity guard only needs one of the
# pair to change something on this host.
_HOST_DEFAULT_PAIRS = [("-fPIC", "-fno-pic")]


def _gxx_targets_pe() -> bool:
    """PE/COFF targets (MinGW, Cygwin) have no PIC model, so GCC defines no
    ``__pic__`` there and neither flag of the PIC pair can change a macro."""
    result = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
        ["g++", "-dumpmachine"], capture_output=True, text=True, check=True
    )
    return any(t in result.stdout.lower() for t in ("mingw", "cygwin", "windows"))


def _castxml_bin() -> str | None:
    return shutil.which("castxml")


def _macros(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0] == "#define" and parts[1] in _WATCHED_MACROS:
            out[parts[1]] = parts[2] if len(parts) == 3 else ""
    return out


def _gxx_output(flag: str | None) -> subprocess.CompletedProcess[str]:
    """``g++ [flag] -dM -E`` over an empty C++ file (never ``/dev/null``,
    which Windows does not have)."""
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty.cpp"
        empty.write_text("\n")
        argv = ["g++", *([flag] if flag else []), "-dM", "-E", "-x", "c++", str(empty)]
        return subprocess.run(argv, capture_output=True, text=True)  # nosec B603 - fixed argv, no shell


def _gxx_macros(flag: str | None) -> dict[str, str]:
    """The host compiler's watched macros under *flag*.

    Skips when the host compiler rejects *flag*: that is a capability the
    host lacks (Apple clang on arm64 has no ``-mavx2``/``-fopenmp``), not
    a defect in the code under test. With no flag, a failure is a failure.
    """
    result = _gxx_output(flag)
    if result.returncode != 0:
        if flag is None:
            raise AssertionError(
                f"host g++ failed with no flags: {result.stderr[-500:]}"
            )
        pytest.skip(f"host g++ rejects {flag}: {result.stderr.strip()[-200:]}")
    return _macros(result.stdout)


def _effective_on_host(flag: str) -> bool:
    """Whether the host compiler accepts *flag* and it changes a watched macro."""
    result = _gxx_output(flag)
    return result.returncode == 0 and _macros(result.stdout) != _gxx_macros(None)


def _castxml_macros(castxml: str, flag: str, tmp_path: Path) -> dict[str, str]:
    """Preprocess an empty header through abicheck's own castxml command."""
    from abicheck.dumper import _build_castxml_command

    header = tmp_path / "empty.hpp"
    header.write_text("\n")
    cmd = _build_castxml_command(
        "g++",
        "gnu",
        [],
        tmp_path / "unused.xml",
        header,
        gcc_option_tokens=(flag,),
        force_cpp=True,
        castxml_bin=castxml,
    )
    out = cmd.index("-o")
    cmd = [*cmd[:out], "-E", "-dM", str(header)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)  # nosec B603 - fixed argv, no shell
    return _macros(result.stdout)


needs_castxml_and_gxx = pytest.mark.skipif(
    _castxml_bin() is None or shutil.which("g++") is None,
    reason="needs castxml and g++",
)


@pytest.mark.integration
@needs_castxml_and_gxx
def test_oracle_flags_really_change_a_watched_macro() -> None:
    """Vacuity guard: a flag that changes nothing proves nothing below.

    Which flags are effective depends on the host compiler (Apple clang's
    default standard, a PIE-by-default GCC, no AVX on arm64), so this
    asserts on the host's own answer: the standard-selection flags -- the
    reported bug -- must be effective everywhere, and most of the list must
    be effective on any one host. The PIC pair is checked only where the
    target has a PIC model (not PE/COFF).
    """
    effective = [flag for flag in _ORACLE_FLAGS if _effective_on_host(flag)]
    assert "-std=c++20" in effective
    assert len(effective) >= len(_ORACLE_FLAGS) // 2, effective
    if _gxx_targets_pe():
        return
    for pair in _HOST_DEFAULT_PAIRS:
        assert any(_effective_on_host(flag) for flag in pair), pair


@pytest.mark.integration
@needs_castxml_and_gxx
@pytest.mark.parametrize(
    "flag", _ORACLE_FLAGS + [flag for pair in _HOST_DEFAULT_PAIRS for flag in pair]
)
def test_castxml_reports_the_real_compilers_macros(flag: str, tmp_path: Path) -> None:
    castxml = _castxml_bin()
    assert castxml is not None
    expected = _gxx_macros(flag)  # skips a flag the host compiler rejects
    if expected == _gxx_macros(None):
        pytest.skip(f"{flag} changes no watched macro on this host")
    assert _castxml_macros(castxml, flag, tmp_path) == expected


@pytest.mark.integration
@needs_castxml_and_gxx
def test_negative_control_flag_outside_group_disagrees(tmp_path: Path) -> None:
    """The pre-fix command shape, run for real, reports the wrong standard.

    Proves the oracle test above can fail: without the group, g++ reports
    its default standard even though the parser was given ``-std=c++20``.
    """
    castxml = _castxml_bin()
    assert castxml is not None
    header = tmp_path / "empty.hpp"
    header.write_text("\n")
    old_shape = [
        castxml,
        "--castxml-cc-gnu",
        "g++",
        "-std=c++20",
        "-E",
        "-dM",
        str(header),
    ]
    macros = _macros(
        subprocess.run(old_shape, capture_output=True, text=True, check=True).stdout  # nosec B603 - fixed argv, no shell
    )
    assert macros.get("__cplusplus") != _gxx_macros("-std=c++20")["__cplusplus"]


@pytest.mark.integration
@needs_castxml_and_gxx
def test_cpp20_concepts_header_parses_through_the_builder(tmp_path: Path) -> None:
    """The reported failure: libstdc++'s ``std::integral`` under C++20."""
    from abicheck.dumper import _build_castxml_command

    castxml = _castxml_bin()
    assert castxml is not None
    header = tmp_path / "sentinel.hpp"
    header.write_text(
        "#include <concepts>\n#include <functional>\n"
        "template <typename T, typename C> struct Sentinel;\n"
        "template <std::integral I> struct Sentinel<I, std::greater<>> {};\n"
    )
    out = tmp_path / "out.xml"
    cmd = _build_castxml_command(
        "g++",
        "gnu",
        [],
        out,
        header,
        gcc_option_tokens=("-std=c++20",),
        force_cpp=True,
        castxml_bin=castxml,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)  # nosec B603 - fixed argv, no shell
    assert result.returncode == 0, result.stderr[-2000:]
    assert out.stat().st_size > 0


@pytest.mark.parametrize(
    ("changed", "unchanged"),
    [
        ("_CASTXML_CACHE_SCHEMA_VERSION", "clang"),
        ("_CLANG_CACHE_SCHEMA_VERSION", "castxml"),
    ],
)
def test_each_backend_schema_constant_salts_only_its_own_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, changed: str, unchanged: str
) -> None:
    """Bumping a backend's schema constant must change that backend's key,
    read at key time (a copy taken at import would ignore the bump), and
    leave the other backend's key alone."""
    from abicheck import dumper_ast_config
    from abicheck.dumper_ast_config import _cache_key

    header = tmp_path / "h.h"
    header.write_text("void f(void);\n")
    own = "castxml" if unchanged == "clang" else "clang"
    kwargs = dict(headers=[header], extra_includes=[], compiler="c++", force_cpp=True)
    before = {b: _cache_key(**kwargs, backend=b) for b in ("clang", "castxml")}
    monkeypatch.setattr(
        dumper_ast_config, changed, getattr(dumper_ast_config, changed) + 1
    )
    assert _cache_key(**kwargs, backend=own) != before[own]
    assert _cache_key(**kwargs, backend=unchanged) == before[unchanged]
