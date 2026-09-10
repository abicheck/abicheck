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

"""End-to-end reproduction of the reported false-positive avalanche
(ADR-063 Phase 5, eleventh batch): dump the SAME compiled library once with
``-H`` (header-derived) and once with no headers at all (DWARF-derived),
and confirm ``compare()`` no longer manufactures a change from the pair.

Self-skipping on ``gcc``/``castxml`` (not marked ``integration``, matching
``test_clang_param_restrict.py``'s own convention — each test pays for its
own real requirement rather than the whole module skipping on the
``integration`` marker's stricter Linux gate).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.diff_symbols import _check_params_change
from abicheck.dumper import dump
from abicheck.model import ParamKind

_HEADER = """
#ifndef LIB_H
#define LIB_H
typedef struct S { int x; int y; } S;
void take_ptr(S *s);
int add(int a, int b);
S *make(void);
void free_s(S *s);
#endif
"""

_SOURCE = """
#include "lib.h"
#include <stdlib.h>
void take_ptr(S *s) { s->x = 1; }
int add(int a, int b) { return a + b; }
S *make(void) { return calloc(1, sizeof(S)); }
void free_s(S *s) { free(s); }
"""


def _compile_so(tmp_path: Path) -> tuple[Path, Path]:
    if shutil.which("gcc") is None:
        pytest.skip("gcc not installed")
    header = tmp_path / "lib.h"
    header.write_text(_HEADER)
    source = tmp_path / "lib.c"
    source.write_text(_SOURCE)
    so_path = tmp_path / "liblib.so"
    proc = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-o", str(so_path), str(source)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"gcc failed: {proc.stderr}"
    return so_path, header


class TestHeaderVsDwarfParamKindNoLongerManufacturesChanges:
    def test_pointer_params_no_change_across_header_and_dwarf_dumps(
        self, tmp_path: Path
    ) -> None:
        if shutil.which("castxml") is None:
            pytest.skip("castxml not installed")
        so_path, header = _compile_so(tmp_path)
        headers_snap = dump(so_path, [header])
        dwarf_snap = dump(so_path, [])

        by_name = {f.name: f for f in headers_snap.functions}
        for name in ("take_ptr", "free_s"):
            assert by_name[name].params[0].kind is ParamKind.POINTER, (
                "castxml must now determine a pointer parameter's real kind "
                "-- see extract/headers/castxml/type_resolution."
                "top_level_param_kind"
            )

        result = compare(headers_snap, dwarf_snap)
        assert result.verdict == Verdict.NO_CHANGE, (
            f"comparing a header-derived dump against a DWARF-derived dump "
            f"of the IDENTICAL library manufactured: "
            f"{[(c.kind, c.symbol) for c in result.changes]}"
        )
        assert result.changes == []


_CXX_TYPEDEF_HEADER = """
#ifndef LIB_CXX_H
#define LIB_CXX_H
typedef int &Ref;
typedef int &&RRef;
void take_ref(Ref x);
void take_rref(RRef x);
#endif
"""

_CXX_TYPEDEF_SOURCE = """
#include "lib.h"
void take_ref(Ref x) { x = 1; }
void take_rref(RRef x) { x = 2; }
"""


def _compile_cxx_so(tmp_path: Path) -> tuple[Path, Path]:
    if shutil.which("g++") is None:
        pytest.skip("g++ not installed")
    header = tmp_path / "lib.h"
    header.write_text(_CXX_TYPEDEF_HEADER)
    source = tmp_path / "lib.cpp"
    source.write_text(_CXX_TYPEDEF_SOURCE)
    so_path = tmp_path / "liblib.so"
    proc = subprocess.run(
        ["g++", "-shared", "-fPIC", "-g", "-o", str(so_path), str(source)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"g++ failed: {proc.stderr}"
    return so_path, header


class TestTypedefWrappedParamKindAgreesAcrossProducers:
    """Codex review, PR #1200: castxml's own type-graph walk already
    unwraps a typedef'd reference/rvalue-reference (``top_level_param_kind``
    walks ``Typedef`` nodes), but neither clang's spelling heuristic (which
    saw only the bare alias name, no ``&``/``&&`` token to find) nor
    ``dwarf_snapshot.py``'s reference-type check (which inspected only the
    immediate, un-unwrapped ``DW_AT_type`` target) did -- so comparing a
    castxml-derived dump against a clang- or DWARF-derived dump of the
    IDENTICAL, unchanged declaration manufactured ``FUNC_PARAMS_CHANGED``
    for every typedef'd reference/rvalue-reference parameter."""

    def test_castxml_vs_dwarf_typedef_reference_no_change(self, tmp_path: Path) -> None:
        if shutil.which("castxml") is None:
            pytest.skip("castxml not installed")
        so_path, header = _compile_cxx_so(tmp_path)
        headers_snap = dump(so_path, [header])
        dwarf_snap = dump(so_path, [])

        headers_by_name = {f.name: f for f in headers_snap.functions}
        dwarf_by_name = {f.name: f for f in dwarf_snap.functions}
        for fname, expected in (
            ("take_ref", ParamKind.REFERENCE),
            ("take_rref", ParamKind.RVALUE_REF),
        ):
            assert headers_by_name[fname].params[0].kind is expected
            assert dwarf_by_name[fname].params[0].kind is expected, (
                "dwarf_snapshot.py must see through the typedef the same "
                "way it already does for a typedef'd pointer -- see "
                "dwarf_utils.unwrap_cv_typedef"
            )

        # `checker.compare()` itself is not used as the oracle here: this
        # library's typedef declarations ALSO trigger a real, pre-existing,
        # unrelated gap (castxml and DWARF spell a typedef's own
        # underlying-type text differently, "int &" vs "int&", manufacturing
        # TYPEDEF_BASE_CHANGED), and this codebase's own diff_filtering
        # redundancy removal then suppresses the sibling FUNC_PARAMS_CHANGED
        # finding as apparently caused by that typedef change -- masking
        # whether THIS fix (the parameter's own kind) actually works, in
        # either direction. `diff_symbols._check_params_change` is the real
        # detector this fix touches, called directly on the real,
        # toolchain-produced `Function` objects above -- the precise,
        # unmasked oracle for this specific fix.
        params_changed = _check_params_change(
            "take_ref", headers_by_name["take_ref"], dwarf_by_name["take_ref"]
        ) + _check_params_change(
            "take_rref", headers_by_name["take_rref"], dwarf_by_name["take_rref"]
        )
        assert params_changed == [], (
            f"typedef'd reference/rvalue-reference params manufactured: "
            f"{[(c.kind, c.symbol) for c in params_changed]}"
        )

    def test_castxml_vs_clang_typedef_reference_no_change(self, tmp_path: Path) -> None:
        if shutil.which("castxml") is None:
            pytest.skip("castxml not installed")
        if shutil.which("clang") is None:
            pytest.skip("clang not installed")
        so_path, header = _compile_cxx_so(tmp_path)
        castxml_snap = dump(so_path, [header], header_backend="castxml")
        clang_snap = dump(so_path, [header], header_backend="clang")

        castxml_by_name = {f.name: f for f in castxml_snap.functions}
        clang_by_name = {f.name: f for f in clang_snap.functions}
        for fname, expected in (
            ("take_ref", ParamKind.REFERENCE),
            ("take_rref", ParamKind.RVALUE_REF),
        ):
            assert castxml_by_name[fname].params[0].kind is expected
            assert clang_by_name[fname].params[0].kind is expected, (
                "clang must see through the typedef via desugaredQualType -- "
                "see extract.headers.clang.context.qualtype_desugared"
            )

        # A full checker.compare() is not used here: castxml wraps gcc's own
        # header-parsing frontend while `header_backend="clang"` invokes
        # clang directly, so the two dumps carry genuinely different
        # compiler_family/compiler_version profile fingerprints -- a real,
        # unrelated ProfileMismatchError, not something this fix touches.
        # Direct kind-agreement above is the precise, in-scope assertion.
