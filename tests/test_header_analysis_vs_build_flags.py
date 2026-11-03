"""Product build settings must not break or perturb the header analysis.

Two shapes of one class (the build-source lane of the catalog, 19 cases):

* a C library's compile database carries a C ``-std=``; its header has the
  usual ``#ifdef __cplusplus extern "C"`` guard, which made the header parse
  pick C++ mode, and the C ``-std=`` then made the frontend reject the parse;
* the build force-includes the very public header under comparison
  (``-include v1.h`` with ``-H v1.h``); the forced include entered the
  profile fingerprint by content, so the API change itself made the pair
  "not comparable".
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck._compiler_options import explicit_c_standard
from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.header_compile_context import resolve_header_compile_context
from abicheck.extract.headers.toolchain import _resolve_force_cpp


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (("-std=gnu11",), True),
        (("-std=c99",), True),
        (("--std=c17",), True),
        (("-std=iso9899:1999",), True),
        (("-std=gnu++17",), False),
        (("-std=c11", "-std=c++20"), False),  # last one wins
        (("-std=c++20", "-std=c11"), True),
        (("-O2",), False),
        ((), False),
        (("-std=bogus",), False),
    ],
)
def test_explicit_c_standard(tokens, expected):
    assert explicit_c_standard(None, tokens) is expected


HEADERS = {
    "plain_c": "struct S { int new; };\nint f(int);\n",
    "guarded_extern_c": (
        '#ifdef __cplusplus\nextern "C" {\n#endif\nstruct S { int x; };\n'
        "int f(int);\n#ifdef __cplusplus\n}\n#endif\n"
    ),
    "cxx_class": "class C { public: int f(); };\n",
    "cxx_namespace": "namespace ns { int f(); }\n",
}
STDS = (None, "gnu11", "c99", "c17", "gnu++17", "c++11")


def _accepts(cc: str, mode: str, std: str | None, header: Path) -> bool:
    # -Werror: GCC only *warns* that a C++ -std= is not valid for C, which is
    # not a real C compilation.
    cmd = [cc, "-x", mode, "-Werror", "-fsyntax-only", str(header)]
    if std:
        cmd.insert(1, f"-std={std}")
    return subprocess.run(cmd, capture_output=True).returncode == 0


@pytest.mark.integration
@pytest.mark.parametrize(("shape", "std"), list(itertools.product(HEADERS, STDS)))
def test_chosen_language_mode_is_one_the_compiler_accepts(tmp_path, shape, std):
    """Oracle: the real compiler. Whenever some mode accepts the header under
    this ``-std=``, the mode abicheck picks must accept it too."""
    cc = shutil.which("gcc")
    if cc is None:
        pytest.skip("gcc not available")
    header = tmp_path / "api.h"
    header.write_text(HEADERS[shape])
    tokens = (f"-std={std}",) if std else ()
    cxx = _resolve_force_cpp(None, [header], None, tokens)
    accepted = {m for m in ("c", "c++") if _accepts(cc, m, std, header)}
    if accepted:
        assert ("c++" if cxx else "c") in accepted, (shape, std, accepted)


def test_cxx_only_syntax_outranks_a_c_standard(tmp_path):
    header = tmp_path / "api.h"
    header.write_text(HEADERS["cxx_class"])
    assert _resolve_force_cpp(None, [header], None, ("-std=gnu11",)) is True


def test_guarded_extern_c_stays_cxx_without_a_c_standard(tmp_path):
    # Unchanged behaviour when the build says nothing about the language.
    header = tmp_path / "api.h"
    header.write_text(HEADERS["guarded_extern_c"])
    assert _resolve_force_cpp(None, [header], None, ()) is True
    assert _resolve_force_cpp(None, [header], None, ("-O2",)) is True


def _evidence(tmp_path: Path, args: list[str]) -> BuildEvidence:
    src = tmp_path / "lib.c"
    src.write_text('#include "api.h"\n')
    cu = CompileUnit(
        id="cu://lib.c",
        source=str(src),
        directory=str(tmp_path),
        target_id="",
        compiler="cc",
        language="C",
        standard="",
        defines={},
        undefines=[],
        include_paths=[],
        system_include_paths=[],
        sysroot=None,
        target_triple="",
        abi_relevant_flags=[],
        argv=["cc", "-c", str(src), *args],
    )
    return BuildEvidence(compile_units=[cu])


def _forced(result) -> list[str]:
    toks = list(result.context.gcc_option_tokens) if result.context else []
    return [toks[i + 1] for i, t in enumerate(toks) if t == "-include"]


@pytest.mark.parametrize("spelling", ["absolute", "relative"])
def test_forcing_the_declared_header_is_not_rendered(tmp_path, spelling):
    header = tmp_path / "api.h"
    header.write_text("int f(int);\n")
    operand = str(header) if spelling == "absolute" else "api.h"
    result = resolve_header_compile_context(
        _evidence(tmp_path, ["-include", operand]), [header]
    )
    assert _forced(result) == []


def test_declared_header_given_as_a_directory_is_skipped_too(tmp_path):
    inc = tmp_path / "include"
    inc.mkdir()
    header = inc / "api.h"
    header.write_text("int f(int);\n")
    (tmp_path / "lib.c").write_text('#include "api.h"\n')
    result = resolve_header_compile_context(
        _evidence(tmp_path, ["-I", str(inc), "-include", str(header)]), [inc]
    )
    assert _forced(result) == []


def test_a_config_header_forced_alongside_is_still_rendered(tmp_path):
    header = tmp_path / "api.h"
    header.write_text("int f(int);\n")
    config = tmp_path / "config.h"
    config.write_text("#define FEATURE 1\n")
    result = resolve_header_compile_context(
        _evidence(tmp_path, ["-include", str(config), "-include", str(header)]),
        [header],
    )
    assert _forced(result) == [config.as_posix()]
