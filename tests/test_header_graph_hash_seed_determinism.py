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

"""The header graph's order does not depend on the process's string hashing.

Two cold dumps of the same headers stored different graphs: the type-node
seeding iterated a ``set`` of qualified names, and the unseeded
``DECL_REFERENCES_DECL`` sources came from another ``set``, so node order --
and with it the stored string/fact tables -- followed ``PYTHONHASHSEED``.

The oracle is the document itself: every order asserted here is the order in
which the names first appear in the AST, computed by a plain scan that shares
no code with the projection. The cross-process test runs the real builders
under several hash seeds, which is the only way to vary string hashing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from hypothesis import given, settings, strategies as st

from abicheck.buildsource.header_graph_ast_projection import (
    _unseeded_decl_endpoints,
)
from abicheck.buildsource.type_graph import (
    TypeEdge,
    index_declared_type_files,
)

PUB = "/proj/include/pub.h"
PRIV = "/proj/include/detail/impl.h"


def _loc(file: str) -> dict[str, Any]:
    return {"file": file, "line": 1, "col": 1}


def _tu(names: list[str]) -> dict[str, Any]:
    """One namespace per name prefix, one record per name, and a field in
    each record whose initializer references a variable -- so both the
    type-file index and the reference-source list are exercised."""
    inner: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        file = PUB if i % 2 else PRIV
        inner.append(
            {
                "kind": "VarDecl",
                "name": f"k{i}",
                "loc": _loc(file),
                "type": {"qualType": "int"},
            }
        )
        inner.append(
            {
                "kind": "CXXRecordDecl",
                "name": name,
                "tagUsed": "struct",
                "completeDefinition": True,
                "loc": _loc(file),
                "inner": [
                    {
                        "kind": "FieldDecl",
                        "name": "x",
                        "type": {"qualType": "int"},
                        "inner": [
                            {
                                "kind": "DeclRefExpr",
                                "referencedDecl": {
                                    "kind": "VarDecl",
                                    "name": f"k{i}",
                                    "loc": _loc(file),
                                },
                            }
                        ],
                    }
                ],
            }
        )
    return {"kind": "TranslationUnitDecl", "inner": inner}


_NAMES = [f"T{i:02d}{c}" for i, c in enumerate("qwertyuiopasdfghjklzxcvbnm")]

_CHILD = r"""
import json, sys
from pathlib import Path
from abicheck.buildsource.header_graph import build_header_only_graph
from abicheck.buildsource.header_graph_ast_projection import project_header_graph_ast
from abicheck.buildsource.header_graph_ast_stream import project_header_graph_ast_file
from abicheck.model import AbiSnapshot

path = Path(sys.argv[1])
whole = project_header_graph_ast(json.loads(path.read_text()))
streamed = project_header_graph_ast_file(path)
snap = AbiSnapshot(library="libx.so", version="1", functions=[], variables=[],
                   types=[], enums=[])
graph = build_header_only_graph(snap, ast_projection=whole,
                                public_header_paths=[sys.argv[2]])
print(json.dumps({
    "whole": list(whole.type_files),
    "streamed": list(streamed.type_files),
    "nodes": [n.id for n in graph.nodes],
    "edges": [list(e.relation_key()) for e in graph.edges],
}))
"""


def _run(path: Path, seed: str) -> dict[str, Any]:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run(
        [sys.executable, "-c", _CHILD, str(path), PUB],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_graph_order_is_the_same_under_every_hash_seed(tmp_path: Path) -> None:
    path = tmp_path / "ast.json"
    path.write_text(json.dumps(_tu(_NAMES)))
    runs = [_run(path, seed) for seed in ("0", "1", "2", "12345")]
    first = runs[0]
    # Document order, from the input list itself rather than the projection.
    assert first["whole"] == _NAMES
    assert first["streamed"] == _NAMES
    assert len(first["nodes"]) > len(_NAMES), "graph must not be vacuous"
    for other in runs[1:]:
        assert other == first


@settings(max_examples=200, deadline=None)
@given(st.lists(st.sampled_from(_NAMES), min_size=0, max_size=20))
def test_declared_type_files_follow_document_order(names: list[str]) -> None:
    type_files = index_declared_type_files(_tu(names))
    # First occurrence, derived independently; the referenced variables
    # (also in the underlying index) are excluded.
    assert list(type_files) == list(dict.fromkeys(names))


@settings(max_examples=200, deadline=None)
@given(st.lists(st.sampled_from(["a", "b", "c", "d", "e", "f"]), max_size=15))
def test_reference_sources_are_in_first_occurrence_order(srcs: list[str]) -> None:
    edges = [
        TypeEdge(src=s, dst=f"v{i}", kind="DECL_REFERENCES_DECL", dst_file=PUB)
        for i, s in enumerate(srcs)
    ]
    endpoints = _unseeded_decl_endpoints({s: PUB for s in srcs}, edges, [])
    sources = [ident for ident, _ in endpoints[len(edges) :]]
    assert sources == list(dict.fromkeys(srcs))


def test_streamed_type_files_follow_document_order_in_process(tmp_path: Path) -> None:
    from abicheck.buildsource.header_graph_ast_stream import (
        project_header_graph_ast_file,
    )

    names = list(reversed(_NAMES))
    path = tmp_path / "ast.json"
    path.write_text(json.dumps(_tu(names)))
    assert list(project_header_graph_ast_file(path).type_files) == names
