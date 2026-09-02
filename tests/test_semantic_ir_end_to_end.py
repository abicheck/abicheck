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
second, third, and fourth slices) -- exercises the real production assembly
call sites this phase wired (``dumper.py``'s ``_dump_elf``, via
``dumper_manifest.resolve_header_ast_result``), not the normalizer in
isolation (``test_semantic_normalizer.py`` covers that). Requires castxml,
clang, and g++ -- gated the same way ``test_castxml_clang_parity_gate.py``
is.

A fixture with a namespaced record, a namespaced enum, a
partially-qualified-nested-type typedef, and a function taking that record
by const reference exercises exactly the shape this phase's own acceptance
criteria names: real cross-backend agreement on identity (the shared
``OccurrenceId``), and a real, honestly-recorded *disagreement* on
namespace-qualification spelling -- for the typedef's underlying type
(castxml resolves it to a bare, non-fully-qualified name; clang resolves it
fully qualified -- see ``test_hybrid_dump_records_the_real_typedef_spelling_
conflict`` below) and, identically, for the function's own parameter type
(see ``test_hybrid_dump_merges_a_real_mangled_function_occurrence`` below) --
which is exactly the kind of cross-backend canonicalization
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
int compute(const inner::Point &p);
constexpr int kMaxPoints = 10;
}
"""

_SOURCE = """
#include "api.h"
int outer::compute(const outer::inner::Point &p) { return p.x; }
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
    # `compute` has a real mangled name, so `entity_id_for_function`'s own
    # mangled branch blanks its `leaf_name` (see that function's own
    # docstring) -- named leaf names cover the record/enum/typedef trio,
    # the mangled function is asserted separately below.
    leaf_names = {
        occ.entity_id.leaf_name
        for occ in snap.semantic_ir.occurrences
        if occ.entity_id.leaf_name
    }
    assert leaf_names == {"Point", "Color", "PointAlias", "kMaxPoints"}
    assert all(
        entity.producer == backend for entity in snap.semantic_ir.occurrences.values()
    )

    # `compute`'s own signature: a top-level by-value cv-qualifier on the
    # return type is kept (there is none here), the reference parameter's
    # pointee cv-qualifier and sigil spacing are normalized regardless of
    # backend. Both backends must agree on `EntityId` too (a real mangled
    # name is already globally unique). The two backends do NOT have to
    # agree on the parameter type's own namespace-qualification spelling --
    # that is the identical, already-documented cross-backend gap
    # `test_hybrid_dump_records_the_real_typedef_spelling_conflict` below
    # exercises for the typedef case (castxml: bare `"Point"`; clang:
    # partially-qualified `"inner::Point"`), which this canonicalization
    # slice reuses `canonicalize_type_name`/
    # `canonicalize_function_signature_param_type` for, not solves.
    #
    # castxml also normalizes `Point`'s compiler-generated copy/move
    # assignment operators here (real mangled names, no synthetic-key
    # hazard -- see `semantic_normalizer.py`'s own skip condition), so
    # `compute` is found by its own distinctive spelling rather than by
    # asserting an exact function-occurrence count, which differs between
    # backends (clang's AST walk never emits an implicit node at all).
    compute_entries = [
        e
        for occ, e in snap.semantic_ir.occurrences.items()
        if occ.entity_id.kind.value == "function"
        and e.canonical_spelling.is_present
        and e.canonical_spelling.value.startswith("int(")
    ]
    assert len(compute_entries) == 1
    assert compute_entries[0].canonical_spelling.value.endswith("Point const &)")

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

    # `kMaxPoints` (ADR-063 Phase 6, fourth slice): the raw value text is
    # projected verbatim, no canonicalization -- see
    # `extract/semantic_normalizer.py`'s own docstring ("Scope of the fourth
    # slice") for why. Both backends agree byte-for-byte here since there is
    # no cross-backend spelling difference to reconcile for a plain integer
    # literal.
    constant_entry = next(
        (occ, e)
        for occ, e in snap.semantic_ir.occurrences.items()
        if occ.entity_id.leaf_name == "kMaxPoints"
    )
    assert constant_entry[0].entity_id.kind.value == "constant"
    assert constant_entry[1].canonical_spelling.value == "10"
    assert constant_entry[1].producer == backend


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


def test_hybrid_dump_merges_a_real_mangled_function_occurrence(compiled_lib) -> None:
    """``compute`` has a real, globally-unique mangled name, so both
    sub-snapshots resolve the identical ``EntityId`` for it (ADR-063 Phase 6,
    third slice) -- the merge matches the two occurrences one-to-one (not
    left ambiguous/unioned), keeps castxml's spelling as base, and records
    the same kind of namespace-qualification-spelling disagreement the
    typedef case above has (castxml: bare ``"Point"``; clang:
    partially-qualified ``"inner::Point"``) as a genuine conflict rather
    than silently discarding either side.
    """
    so, header = compiled_lib
    snap = dump(so, [header], header_backend="hybrid")

    assert snap.semantic_ir is not None
    # castxml's own compiler-generated copy/move assignment operators are
    # normalized here too (real mangled names) -- `compute` is found by its
    # own distinctive spelling, same as the plain-dump test above.
    compute_entries = [
        (occ, e)
        for occ, e in snap.semantic_ir.occurrences.items()
        if occ.entity_id.kind.value == "function"
        and e.canonical_spelling.is_present
        and e.canonical_spelling.value.startswith("int(")
    ]
    assert len(compute_entries) == 1
    occ_id, entity = compute_entries[0]
    assert entity.producer == "castxml"
    assert entity.canonical_spelling.value == "int(Point const &)"

    from abicheck.model.semantic_ir import semantic_ir_conflict_key

    conflict_key = semantic_ir_conflict_key(occ_id, "canonical_spelling")
    assert conflict_key in snap.semantic_ir_conflicts
    assert "inner::Point" in snap.semantic_ir_conflicts[conflict_key]
