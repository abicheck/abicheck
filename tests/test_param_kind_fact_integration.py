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
