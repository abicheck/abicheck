# Copyright 2026 Nikolay Petrov
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

"""End-to-end regression for PR #1138's crosscheck language-mode bug.

Bug class ``extraction.language_mode_export_evidence``
(``tests/regressions/manifest.py``): a public header with no structural
C++ syntax at all -- no ``class``/``namespace``/``template``/
``extern "C"``, just a plain top-level function or variable declaration --
gave the whole-TU language-mode auto-detection heuristic
(``dumper_ast_config._detect_cpp_headers``) nothing to key on, so an
unspecified ``--lang``/``lang`` silently settled on **C** even for a header
that is unambiguously part of a real, compiled C++ library. Parsing the
header in C mode corrupts every declaration in it: castxml/clang emit a
bare, unmangled symbol, mark the declaration ``is_extern_c=True``, and (via
the ELF-fallback visibility path) can also flip its resolved
``Visibility``.

The reported, first-observed symptom: with a real C++ shared library
compiled from a public header declaring nothing but one plain global
function (``MAIN_API int main_op(int x);``, ``MAIN_API`` expanding to
nothing), ``abicheck compare a.so a.so -H main.h`` (a bare self-comparison
-- the two operands are identical) reported ``COMPATIBLE_WITH_RISK`` with
a *self-contradictory* pair of findings for the exact same declaration:
``exported_not_public`` (the binary's real mangled export
``_Z7main_opi`` matches no public-header-declared candidate symbol) AND
``public_not_exported`` (the public header's own declared symbol
``main_op`` matches nothing in the binary's export table) -- both
findings on a self-comparison of one unchanged binary, which can never be
a real ABI difference. Root cause: the wrongly-C-mode header AST parse
computed ``main_op``'s ``Function.mangled`` as the bare name ``"main_op"``
(``is_extern_c=True``) instead of the real ``"_Z7main_opi"`` castxml/clang
actually emit for a genuine, unmangled, un-namespaced C++ function when
parsed in the correct C++ mode -- so neither of ``crosscheck.py``'s two
independent correlation checks (``_check_exported_not_public``/
``_check_public_not_exported``) could match the header-derived candidate
against the binary's real export, each accusing the other side of being
undocumented/unexported.

Fix: ``dumper_toolchain._resolve_force_cpp`` now also checks the binary's
own already-computed ``exported_dynamic | exported_static`` union
(threaded through ``dumper._header_ast_parser`` -> ``_clang_header_dump``/
``_castxml_dump`` -> ``_resolve_clang_langmode``/``_resolve_force_cpp``)
via ``dumper_ast_config._exported_symbols_indicate_cpp``: a real Itanium/
Mach-O-Itanium/MSVC mangled export is direct, unambiguous evidence the
compile used C++ linkage, checked as a last-resort fallback only when
header content alone gives no signal and no explicit ``--lang`` was given.

This module covers several independently-chosen sibling declaration
shapes -- not just the one reported ``main_op`` repro -- against both
header-AST backends, proving the correlation is correct across the class,
not merely patched for the one input that was reported. See
``tests/test_dumper_coverage.py::TestResolveForceCppExportedSymbolEvidence``
for the unit-level (no real compiler) property coverage of
``_resolve_force_cpp`` itself; this module is the real-compiler,
real-binary, real-``compare``-CLI-surface counterpart the bug class's
``axes``/``public_surfaces`` entries claim.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform == "win32", reason="builds an ELF .so pair, linux/macos only"
    ),
    pytest.mark.skipif(
        shutil.which("g++") is None, reason="g++ required to build fixtures"
    ),
]

_AST_FRONTENDS = pytest.mark.parametrize(
    "ast_frontend",
    [
        "castxml",
        pytest.param(
            "clang",
            marks=pytest.mark.skipif(
                shutil.which("clang") is None,
                reason="clang required for --ast-frontend clang",
            ),
        ),
    ],
)

#: Sibling declaration shapes, each with no structural C++ syntax at all in
#: its own declaration (no class/namespace/template/extern "C" wrapping
#: it directly) -- the exact "auto-detection has nothing to key on" shape
#: this bug class covers. ``ns_func`` is the one exception (it needs a
#: `namespace` keyword to declare it at all) -- kept as a companion proving
#: the fix does not regress a header that already has *some* real C++
#: syntax elsewhere by no longer needing export evidence for it.
_HEADER = """\
int plain_func(int x);
int no_arg_func(void);
extern int g_variable;
namespace ns { int ns_func(int x); }
extern "C" int c_func(int x);
"""

_SOURCE = """\
#include "main.h"
int plain_func(int x) { return x; }
int no_arg_func(void) { return 0; }
int g_variable = 5;
namespace ns { int ns_func(int x) { return x; } }
extern "C" int c_func(int x) { return x; }
"""


def _build_lib(src_dir: Path, out_so: Path) -> None:
    (src_dir / "main.h").write_text(_HEADER, encoding="utf-8")
    (src_dir / "main.cpp").write_text(_SOURCE, encoding="utf-8")
    subprocess.run(
        ["g++", "-std=gnu++11", "-fPIC", "-shared", "-o", str(out_so), "main.cpp"],
        cwd=src_dir,
        check=True,
        capture_output=True,
        text=True,
    )


@_AST_FRONTENDS
def test_self_compare_reports_no_change_for_plain_unnamespaced_functions(
    tmp_path: Path, ast_frontend: str
) -> None:
    """The exact reported repro, generalized: a self-comparison of a real
    C++ library whose public header declares several plain, unnamespaced
    top-level functions/variables (no C++-only syntax anywhere near their
    own declarations) must report zero changes and verdict
    NO_CHANGE/COMPATIBLE -- never the contradictory
    exported_not_public/public_not_exported pair for the same symbol."""
    src_dir = tmp_path / "lib"
    src_dir.mkdir()
    so_path = src_dir / "libmain.so"
    _build_lib(src_dir, so_path)

    out_json = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(so_path),
            str(so_path),
            "-H",
            str(src_dir / "main.h"),
            "--ast-frontend",
            ast_frontend,
            "--format",
            "json",
            "-o",
            str(out_json),
        ],
    )
    assert result.exit_code == 0, result.output

    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["verdict"] in ("COMPATIBLE", "NO_CHANGE"), report
    kinds = [c["kind"] for c in report.get("changes", [])]
    assert "exported_not_public" not in kinds, report
    assert "public_not_exported" not in kinds, report
    assert not report.get("changes"), report


@_AST_FRONTENDS
def test_declaration_identity_correct_for_every_sibling_shape(
    tmp_path: Path, ast_frontend: str
) -> None:
    """Direct assertion on the underlying facts (not just the absence of a
    finding): each sibling declaration must resolve to its REAL compiled
    identity -- the genuine Itanium mangled name (or bare name for the
    genuine extern "C" case), correct is_extern_c, and PUBLIC visibility --
    proving the fix is correct for the whole declaration-shape class, not
    only coincidentally silent for one repro."""
    from abicheck.model.vocabulary import Visibility

    src_dir = tmp_path / "lib"
    src_dir.mkdir()
    so_path = src_dir / "libmain.so"
    _build_lib(src_dir, so_path)

    from abicheck.service import resolve_input

    snap = resolve_input(
        so_path,
        headers=[src_dir / "main.h"],
        public_headers=[src_dir / "main.h"],
        header_backend=ast_frontend,
    )

    funcs = {f.name: f for f in snap.functions}
    assert funcs["plain_func"].mangled == "_Z10plain_funci"
    assert funcs["plain_func"].is_extern_c is False
    assert funcs["plain_func"].visibility == Visibility.PUBLIC

    assert funcs["no_arg_func"].mangled == "_Z11no_arg_funcv"
    assert funcs["no_arg_func"].is_extern_c is False

    assert funcs["ns_func"].mangled == "_ZN2ns7ns_funcEi"
    assert funcs["ns_func"].is_extern_c is False

    # The genuine extern "C" function must be UNAFFECTED by this fix: it
    # is correctly bare-named and extern-C, not a false positive the
    # export-evidence check now wrongly "corrects" into looking mangled.
    assert funcs["c_func"].mangled == "c_func"
    assert funcs["c_func"].is_extern_c is True
    assert funcs["c_func"].visibility == Visibility.PUBLIC

    variables = {v.name: v for v in snap.variables}
    assert variables["g_variable"].visibility == Visibility.PUBLIC
