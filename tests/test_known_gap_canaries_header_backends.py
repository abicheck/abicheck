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

#: Every compiled canary pins the answer measured with gcc/castxml/clang on a
#: Linux ELF host; on Windows or macOS the toolchain, binary format and so the
#: gapped answer differ, so the canary would assert the wrong bound there.
_LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="pins the gap as measured on Linux/ELF"
)

pytestmark = [pytest.mark.integration, pytest.mark.known_gap_canary, _LINUX_ONLY]

_ROOT = Path(__file__).resolve().parents[1]
_CASES = _ROOT / "catalog" / "cases"


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


# -- "The clang record-layout pass leaves unnameable records and
#    alias-template arrays to DWARF" (the C-header residual closed with
#    extractor generation 39; its canary is now a regression test in
#    tests/test_clang_c_header_layout.py)

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


# -- "`dumper.dump()` never runs the clang record-layout pass"

_DD_HEADER = "struct W { int a; long b; };\nint f(W* w);\n"
_DD_SRC = '#include "api.h"\nint f(W* w) { return w->a; }\n'


def test_legacy_dumper_dump_leaves_a_clang_record_unsized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without DWARF, the in-process `dumper.dump()` has no record size on clang;
    `run_dump()` on the same library lays the record out (the control)."""
    _require("clang", "g++")
    from abicheck.dumper import dump
    from abicheck.service import run_dump

    (tmp_path / "api.h").write_text(_DD_HEADER, encoding="utf-8")
    (tmp_path / "api.cpp").write_text(_DD_SRC, encoding="utf-8")
    lib = tmp_path / "lib.so"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-shared",
            "-fPIC",
            f"-I{tmp_path}",
            str(tmp_path / "api.cpp"),
            "-o",
            str(lib),
        ],
        check=True,
    )
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    headers = [tmp_path / "api.h"]

    def sizes(snap: Any) -> list[tuple[str, int | None]]:
        return [(t.name, t.size_bits) for t in snap.declarations.types]

    controlled = run_dump(lib, "elf", headers=headers, version="v1", frontend="clang")
    assert sizes(controlled) == [("W", 128)]
    assert sizes(dump(lib, headers=headers, version="v1")) == [("W", None)]


# -- "Catalog known-gap rows at debug-headers depth read NO_CHANGE (case98)"
# case105 (concept) is fixed on both backends; its regression test is
# tests/test_concept_header_scan.py::test_case105_concept_tightening_through_the_cli_per_backend.


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
