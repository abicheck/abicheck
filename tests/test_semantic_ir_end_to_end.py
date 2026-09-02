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

"""End-to-end ``AbiSnapshot.semantic_ir`` population (ADR-063 Phase 6,
second slice) -- exercises the real production assembly call sites this
slice wired (``dumper.py``'s ``_dump_elf``, via ``dumper_manifest.
resolve_header_ast_result``), not the normalizer in isolation
(``test_semantic_normalizer.py`` covers that). Requires castxml, clang, and
g++ -- gated the same way ``test_castxml_clang_parity_gate.py`` is.

A fixture with a namespaced record, a namespaced enum, and a
partially-qualified-nested-type typedef exercises exactly the shape this
phase's own acceptance criteria names: real cross-backend agreement on
identity (the shared ``OccurrenceId``), and a real, honestly-recorded
*disagreement* on typedef spelling (castxml resolves the typedef's
underlying type to a bare, non-fully-qualified name; clang resolves it
fully qualified -- see ``test_hybrid_dump_records_the_real_typedef_spelling_
conflict`` below), which is exactly the kind of cross-backend canonicalization
gap this phase exists to make visible rather than silently pick a winner
for.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from abicheck.dumper import dump

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="header-AST semantic_ir wiring is exercised on the ELF/Linux "
        "dump path (see module docstring)",
    ),
]

_HEADER = """
#pragma once
namespace outer {
namespace inner {
struct Point { int x; int y; };
}
enum class Color { RED, GREEN, BLUE };
typedef inner::Point PointAlias;
}
"""

_SOURCE = """
#include "api.h"
int use_point(outer::inner::Point p) { return p.x; }
"""


def _require_tools() -> None:
    required = ("clang", "gcc", "g++", "castxml")
    if not all(shutil.which(t) for t in required):
        pytest.skip("clang, gcc, g++, and castxml are all required")


@pytest.fixture(scope="module")
def compiled_lib(tmp_path_factory: pytest.TempPathFactory):
    _require_tools()
    tmp_path = tmp_path_factory.mktemp("semantic_ir_e2e")
    header = tmp_path / "api.h"
    header.write_text(_HEADER)
    src = tmp_path / "api.cpp"
    src.write_text(_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )
    return so, header


@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_semantic_ir_populated_end_to_end(compiled_lib, backend: str) -> None:
    so, header = compiled_lib
    snap = dump(so, [header], header_backend=backend)

    assert snap.semantic_ir is not None
    leaf_names = {occ.entity_id.leaf_name for occ in snap.semantic_ir.occurrences}
    assert leaf_names == {"Point", "Color", "PointAlias"}
    assert all(
        entity.producer == backend for entity in snap.semantic_ir.occurrences.values()
    )

    # The record's own EntityId is genuinely shared -- both backends resolve
    # `outer::inner::Point` to the identical scope/kind/leaf_name, which is
    # this phase's actual point (identity canonicalized once, not once per
    # backend, per ADR-063 Phase 2's option (a)).
    record_entry = next(
        (occ, e)
        for occ, e in snap.semantic_ir.occurrences.items()
        if occ.entity_id.leaf_name == "Point"
    )
    assert record_entry[1].canonical_spelling.value == "outer::inner::Point"


def test_hybrid_dump_records_the_real_typedef_spelling_conflict(compiled_lib) -> None:
    """``--ast-frontend hybrid`` reconciles both sub-snapshots' SemanticIR
    (``dumper_hybrid.merge_snapshots()``, landed in this phase's first
    slice) -- this is the first real, non-empty exercise of that
    reconciliation, now that a real backend actually populates
    ``semantic_ir``. castxml resolves ``PointAlias``'s underlying type to
    the bare ``"Point"``; clang resolves it to the partially-qualified
    ``"inner::Point"`` -- a genuine disagreement (not a null-vs-value gap),
    so the merge keeps castxml's value as base and records the conflict
    rather than silently discarding either side.
    """
    so, header = compiled_lib
    snap = dump(so, [header], header_backend="hybrid")

    assert snap.semantic_ir is not None
    typedef_entry = next(
        (occ, e)
        for occ, e in snap.semantic_ir.occurrences.items()
        if occ.entity_id.leaf_name == "PointAlias"
    )
    occ_id, entity = typedef_entry
    assert entity.producer == "castxml"
    assert entity.canonical_spelling.value == "Point"

    from abicheck.model.semantic_ir import semantic_ir_conflict_key

    conflict_key = semantic_ir_conflict_key(occ_id, "canonical_spelling")
    assert conflict_key in snap.semantic_ir_conflicts
    assert "inner::Point" in snap.semantic_ir_conflicts[conflict_key]
