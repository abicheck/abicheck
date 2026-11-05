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

"""Known-gap canaries for the castxml and clang header backends.

Each test asserts the *current, gapped* answer of one open
`docs/contribute/known-gaps.md` entry, through the public `dump`/`compare`
CLI on real compiled libraries. The entry names the test with a
``<!-- canary: ... -->`` marker (the `known-gap-canaries` gate checks the
link both ways).

When one of these fails, the gap moved: it was fixed, or it got worse. Do
not re-pin the new answer here. If it was fixed, move the entry to
`docs/contribute/archive/known-gaps-closed.md`, turn this test into a
regression test of the correct answer (drop `known_gap_canary`) and delete
the entry's marker. If it got worse, fix the regression.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._abicheck_subprocess import abicheck_command, abicheck_env
from tests.test_template_default_assurance_gap import _compare as _header_only_compare

#: Every compiled canary pins the answer measured with gcc/castxml/clang on a
#: Linux ELF host; on Windows or macOS the toolchain, binary format and so the
#: gapped answer differ, so the canary would assert the wrong bound there.
_LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="pins the gap as measured on Linux/ELF"
)

pytestmark = [pytest.mark.integration, pytest.mark.known_gap_canary, _LINUX_ONLY]

_ROOT = Path(__file__).resolve().parents[1]
_CASES = _ROOT / "catalog" / "cases"
_TEMPLATE_GAP = "template-parameter defaults"


def _require(*tools: str) -> None:
    for tool in tools:
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not installed")


def _compare_libraries(
    tmp: Path,
    *,
    frontend: str,
    lang: str,
    sides: list[tuple[str, Path, Path, list[str]]],
    debug: bool,
) -> dict[str, Any]:
    """Build each side's library, dump it with ``-H``, compare; return the JSON report.

    *sides* is ``[(tag, source, header, extra compiler flags)]`` for old, new.
    """
    compiler = ["gcc"] if lang == "c" else ["g++"]
    snaps = []
    for tag, source, header, flags in sides:
        lib = tmp / f"lib{tag}.so"
        subprocess.run(
            [
                *compiler,
                *flags,
                *(["-g"] if debug else []),
                "-shared",
                "-fPIC",
                f"-I{header.parent}",
                str(source),
                "-o",
                str(lib),
            ],
            check=True,
        )
        cfg = tmp / f"{tag}.yml"
        cfg.write_text(
            f"compile:\n  frontend: {frontend}\n  lang: {'c' if lang == 'c' else 'c++'}\n",
            encoding="utf-8",
        )
        out = tmp / f"{tag}.json"
        run = subprocess.run(
            abicheck_command("dump", lib, "-H", header, "--config", cfg, "-o", out),
            capture_output=True,
            text=True,
            env=abicheck_env(),
            check=False,
        )
        assert run.returncode == 0, run.stderr
        snaps.append(out)
    report = tmp / "report.json"
    subprocess.run(
        abicheck_command("compare", *snaps, "-o", f"json={report}"),
        capture_output=True,
        env=abicheck_env(),
        check=False,
    )
    return json.loads(report.read_text(encoding="utf-8"))


def _write_pair(
    tmp: Path, lang: str, headers: tuple[str, str], source: str
) -> list[tuple[str, Path, Path, list[str]]]:
    sides = []
    for tag, text in zip(("old", "new"), headers):
        side = tmp / tag
        side.mkdir()
        header = side / "api.h"
        header.write_text(text, encoding="utf-8")
        src = side / ("api.c" if lang == "c" else "api.cpp")
        src.write_text(source, encoding="utf-8")
        sides.append((tag, src, header, [] if lang == "c" else ["-std=c++17"]))
    return sides


def _catalog_sides(
    case: str, flags: tuple[list[str], list[str]]
) -> list[tuple[str, Path, Path, list[str]]]:
    root = _CASES / case
    return [
        (v, root / f"{v}.cpp", root / f"{v}.h", side_flags)
        for v, side_flags in zip(("v1", "v2"), flags)
    ]


def _kinds(report: dict[str, Any]) -> set[str]:
    return {c["kind"] for c in report["changes"]}


def _template_gap_notes(report: dict[str, Any]) -> list[str]:
    return [
        n for n in report["analysis_assurance"]["notes"] if n.startswith(_TEMPLATE_GAP)
    ]


# -- "A header-only default template-argument change reads NO_CHANGE at L2 on castxml"


def test_castxml_class_template_default_change_reads_partial_no_change(
    tmp_path: Path,
) -> None:
    """castxml cannot record a template default: a changed one reads NO_CHANGE, partial, named."""
    _require("castxml")
    report = _header_only_compare(
        tmp_path,
        "castxml",
        "template <class T, class U = int> struct Box { T t; U u; };",
        "template <class T, class U = long> struct Box { T t; U u; };",
    )
    assert report["verdict"] == "NO_CHANGE"
    assert "template_param_default_changed" not in _kinds(report)
    assert report["analysis_assurance"]["status"] == "partial"
    assert any("castxml cannot record them" in n for n in _template_gap_notes(report))


def test_clang_macro_spelled_alias_template_default_is_not_seen(tmp_path: Path) -> None:
    """The clang alias/variable-template scan is textual: a default spelled by a macro is unseen.

    The change is neither reported nor named as a template-defaults gap.
    """
    _require("clang")
    macro = "#define DFLT(x) = x\n"
    report = _header_only_compare(
        tmp_path,
        "clang",
        macro + "template <class T, class U DFLT(int)> using P = Q<U>;",
        macro + "template <class T, class U DFLT(long)> using P = Q<U>;",
    )
    assert report["verdict"] == "NO_CHANGE"
    assert "template_param_default_changed" not in _kinds(report)
    assert _template_gap_notes(report) == []


# -- "The clang record-layout pass leaves C headers, unnameable records and
#    alias-template arrays to DWARF"

_C_OLD = "struct S { int a; };\nint f(struct S s);\n"
_C_NEW = "struct S { int a; int b; };\nint f(struct S s);\n"
_C_SRC = '#include "api.h"\nint f(struct S s) { return s.a; }\n'


def test_clang_c_header_record_growth_without_dwarf_misses_the_size_change(
    tmp_path: Path,
) -> None:
    """The layout pass probes C++ only: a C struct that grows, built without -g, has no size."""
    _require("clang", "gcc")
    report = _compare_libraries(
        tmp_path,
        frontend="clang",
        lang="c",
        sides=_write_pair(tmp_path, "c", (_C_OLD, _C_NEW), _C_SRC),
        debug=False,
    )
    kinds = _kinds(report)
    assert "type_field_added_compatible" in kinds
    assert "type_size_changed" not in kinds
    assert report["verdict"] == "COMPATIBLE"


_ALIAS = (
    "#include <memory>\nnamespace m {{\nstruct Impl;\n"
    "template <class T> using pimpl = std::{ptr}<T>;\n"
    "struct W {{ pimpl<Impl> m[2]; }};\nint f(W* w);\n}}\n"
)
_ALIAS_SRC = '#include "api.h"\nnamespace m { int f(W*) { return 0; } }\n'


def test_clang_alias_template_array_field_retarget_is_not_a_field_type_change(
    tmp_path: Path,
) -> None:
    """`pimpl<Impl> m[2]` keeps its written spelling on clang, so retargeting the alias is
    not `type_field_type_changed` (castxml reports it on the same pair)."""
    _require("clang", "g++")
    report = _compare_libraries(
        tmp_path,
        frontend="clang",
        lang="c++",
        sides=_write_pair(
            tmp_path,
            "c++",
            (_ALIAS.format(ptr="unique_ptr"), _ALIAS.format(ptr="shared_ptr")),
            _ALIAS_SRC,
        ),
        debug=False,
    )
    assert "type_field_type_changed" not in _kinds(report)


def test_castxml_case85_reports_visibility_change_not_removal(tmp_path: Path) -> None:
    """Under castxml case85 reads `func_visibility_changed`, where the catalog lists `func_removed`."""
    _require("castxml", "g++")
    report = _compare_libraries(
        tmp_path,
        frontend="castxml",
        lang="c++",
        sides=_catalog_sides("case85_internal_template_signature_changed", ([], [])),
        debug=True,
    )
    kinds = _kinds(report)
    assert {
        "func_visibility_changed",
        "internal_template_leaks_via_public_api",
    } <= kinds
    assert "func_removed" not in kinds


# -- "Catalog known-gap rows at debug-headers depth read NO_CHANGE (case105, case98)"


def test_case105_concept_tightening_reads_no_change_at_debug_headers(
    tmp_path: Path,
) -> None:
    """Neither backend sees a C++20 concept; clang even claims complete assurance."""
    _require("castxml", "clang", "g++")
    answers = {}
    for frontend in ("castxml", "clang"):
        work = tmp_path / frontend
        work.mkdir()
        report = _compare_libraries(
            work,
            frontend=frontend,
            lang="c++",
            sides=_catalog_sides(
                "case105_concept_tightening", (["-std=c++20"], ["-std=c++20"])
            ),
            debug=True,
        )
        assert "concept_tightened" not in _kinds(report)
        answers[frontend] = (report["verdict"], report["analysis_assurance"]["status"])
    # castxml reads partial only because the public surface did not resolve,
    # not because the concept is named as unobserved.
    assert answers == {
        "castxml": ("NO_CHANGE", "partial"),
        "clang": ("NO_CHANGE", "complete"),
    }


def test_case98_standard_floor_reads_partial_no_change_at_debug_headers(
    tmp_path: Path,
) -> None:
    """A raised -std floor is invisible without build evidence: NO_CHANGE, partial."""
    _require("castxml", "g++")
    report = _compare_libraries(
        tmp_path,
        frontend="castxml",
        lang="c++",
        sides=_catalog_sides("case98_cxx_standard_floor_raised", ([], ["-std=c++20"])),
        debug=True,
    )
    assert "abi_relevant_build_flag_changed" not in _kinds(report)
    assert (report["verdict"], report["analysis_assurance"]["status"]) == (
        "NO_CHANGE",
        "partial",
    )
