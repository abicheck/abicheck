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

"""``SemanticIRIndex`` vs. the legacy function matching key -- real-fixture
parity (ADR-063 Phase 6B, "PR 2" preparation).

This is the concrete fact any future ``diff_symbols.py`` cutover onto
``SemanticIR`` needs proven *before* the cutover, not discovered during it:
today's production matching (``diff_symbols._match_old_function``, via
``finding_identity.SymbolIdentityIndex``) keys its exact-tier join on
``Function.mangled`` -- a plain string. A cutover would instead key on
``Function.entity_id`` (an :class:`~abicheck.model.identity.EntityId`,
already attached to every ``Function`` at parse time and already what
``SemanticIR.occurrences`` is keyed by, per ``extract/semantic_normalizer.
py``'s third slice). Swapping the join key is only behavior-preserving if
the two partition a real function set identically -- this module proves
that on a real compiled fixture, for both header-AST backends, rather than
assuming it from the two primitives' shared design docs alone.

Three things are checked, all on a real ``dump()`` snapshot:

1. **Completeness**: ``SemanticIRIndex.functions()`` sees exactly the same
   set of identities as ``AbiSnapshot.functions`` itself -- no function the
   legacy matching key can see is invisible to the index, and no phantom
   entity the index carries is absent from the legacy list.
2. **Distinctness where the legacy key is distinct**: two functions with
   different ``mangled`` values (the two overloads, and the extern "C"
   function against either) never collapse onto one ``EntityId`` -- an
   ``EntityId``-keyed join could never accidentally *merge* two symbols the
   current mangled-name join keeps apart.
3. **Determinism**: re-resolving the same mangled function's identity via
   the index (``SemanticIRIndex.entity()``) is stable across repeated
   calls, matching :class:`~abicheck.model.semantic_ir.SemanticIR`'s own
   frozen/cached contract.

Not attempted here (left for the actual cutover): the extern "C"
alias-fallback tier itself (``SymbolIdentityIndex.unique_alias_match``) is
*not* claimed to already agree with ``EntityId`` equality in the general
case -- that fallback joins on a bare, unscoped display name, while
``entity_id_for_function``'s own ``extern_c`` tag is scope-qualified (see
``model/identity.py``'s own docstring). A real compiled fixture's extern
"C" function is, in practice, at global scope on both old and new sides,
so this asymmetry does not surface in the case tested here -- it is
recorded as an open question for the eventual matcher, not silently
assumed away.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from abicheck.dumper import dump
from abicheck.model.semantic_ir_index import SemanticIRIndex

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
namespace ns {
int compute(int x);
int compute(double x);
}
extern "C" int c_api(int x);
"""

_SOURCE = """
#include "match.h"
namespace ns {
int compute(int x) { return x; }
int compute(double x) { return static_cast<int>(x); }
}
extern "C" int c_api(int x) { return x * 2; }
"""


def _require_compiler() -> None:
    if not shutil.which("g++"):
        pytest.skip("g++ is required to build the fixture library")


def _require_backend(backend: str) -> None:
    """Skip *this one* parametrization when its own backend tool is
    missing, rather than gating the whole module on every backend's tool
    at once -- a runner with only castxml (this repo's documented
    integration-marker prerequisite) must still get the castxml cases,
    and one with only clang must still get the clang cases (Codex review).
    """
    tool = "castxml" if backend == "castxml" else "clang"
    if not shutil.which(tool):
        pytest.skip(f"{tool} is required for the {backend!r} backend")


@pytest.fixture(scope="module")
def match_lib(tmp_path_factory: pytest.TempPathFactory):
    _require_compiler()
    tmp_path = tmp_path_factory.mktemp("semantic_ir_function_parity")
    header = tmp_path / "match.h"
    header.write_text(_HEADER)
    src = tmp_path / "match.cpp"
    src.write_text(_SOURCE)
    so = tmp_path / "libmatch.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-std=c++17", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
    )
    return so, header


@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_index_functions_matches_legacy_function_list_exactly(
    match_lib, backend: str
) -> None:
    _require_backend(backend)
    so, header = match_lib
    snap = dump(so, [header], header_backend=backend)
    assert snap.semantic_ir is not None

    legacy_ids = {f.entity_id for f in snap.functions if f.entity_id is not None}
    assert legacy_ids, "fixture must produce at least one identified function"

    index = SemanticIRIndex(snap.semantic_ir)
    index_ids = set(index.functions())

    assert index_ids == legacy_ids


@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_distinct_mangled_names_never_collapse_onto_one_entity_id(
    match_lib, backend: str
) -> None:
    """The legacy join key (``Function.mangled``) is distinct for the two
    overloads and the extern "C" function; ``EntityId``-keyed matching must
    never merge what the mangled-name key keeps apart."""
    _require_backend(backend)
    so, header = match_lib
    snap = dump(so, [header], header_backend=backend)

    by_mangled: dict[str, list] = {}
    for f in snap.functions:
        if f.entity_id is None:
            continue
        by_mangled.setdefault(f.mangled, []).append(f)

    mangled_names = list(by_mangled)
    assert len(mangled_names) >= 3, "expected two overloads plus the extern C function"

    entity_ids_by_mangled = {
        mangled: {f.entity_id for f in fns} for mangled, fns in by_mangled.items()
    }
    # Every mangled name resolves to exactly one EntityId (no internal
    # inconsistency for a single legacy key)...
    for mangled, ids in entity_ids_by_mangled.items():
        assert len(ids) == 1, f"{mangled!r} maps to more than one EntityId: {ids}"
    # ...and distinct mangled names never share that one EntityId.
    seen: dict[object, str] = {}
    for mangled, ids in entity_ids_by_mangled.items():
        (entity_id,) = ids
        if entity_id in seen:
            pytest.fail(
                f"{mangled!r} and {seen[entity_id]!r} collapse onto the same "
                f"EntityId {entity_id!r}"
            )
        seen[entity_id] = mangled


@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_index_entity_lookup_is_deterministic(match_lib, backend: str) -> None:
    _require_backend(backend)
    so, header = match_lib
    snap = dump(so, [header], header_backend=backend)
    index = SemanticIRIndex(snap.semantic_ir)

    for entity_id in index.functions():
        first = index.entity(entity_id)
        second = index.entity(entity_id)
        assert first is second
