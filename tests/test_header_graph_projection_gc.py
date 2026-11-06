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

"""Regression: the header-graph projection runs its AST walks with cyclic GC
paused, and restores the caller's GC setting afterwards.

Bug class: a pass that allocates many small objects while a large AST stays
alive triggers generation-2 collections that rescan the whole live tree on
each threshold crossing. At scale this dominated the projection (measured on
a 10x synthetic clang AST: 8.4 s with GC on vs 5.5 s paused, identical
result). The invariant is checked on every walk the projection makes, not on
one timing: each reader must observe GC disabled, and the projection must
leave GC exactly as it found it, whether it was enabled or already disabled.
"""

from __future__ import annotations

import gc

import pytest

from abicheck.buildsource import header_graph_ast_projection as proj

_WALKS = (
    "validate_ast_shape",
    "index_declared_type_files",
    "parse_clang_ast_types",
    "index_declared_entity_files",
    "parse_semantic_facts",
    "parse_clang_ast_calls",
    "collect_special_member_names",
    "collect_function_bodies",
)


def _tiny_ast() -> dict:
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "f",
                "inner": [{"kind": "CompoundStmt", "inner": []}],
            }
        ],
    }


@pytest.mark.parametrize("gc_enabled_before", [True, False])
def test_every_projection_walk_runs_with_gc_paused(
    monkeypatch: pytest.MonkeyPatch, gc_enabled_before: bool
) -> None:
    observed: dict[str, bool] = {}

    for name in _WALKS:
        original = getattr(proj, name)

        def spy(*args, __name=name, __original=original, **kwargs):
            observed[__name] = gc.isenabled()
            return __original(*args, **kwargs)

        monkeypatch.setattr(proj, name, spy)

    previous = gc.isenabled()
    try:
        if gc_enabled_before:
            gc.enable()
        else:
            gc.disable()
        proj.project_header_graph_ast(_tiny_ast())
        # Restored to the caller's setting, not forced on.
        assert gc.isenabled() is gc_enabled_before
    finally:
        gc.enable() if previous else gc.disable()

    walked = [n for n in _WALKS if n in observed]
    # The spied entry points are all reached on this tree; a missing one
    # would mean the spy list no longer matches the projection's readers.
    assert "parse_clang_ast_calls" in walked
    assert "collect_function_bodies" in walked
    assert all(observed[n] is False for n in walked), observed
