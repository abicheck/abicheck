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

"""Tests for the header-only (L2) semantic graph builder (ADR-041 addendum).

Exercises ``build_header_only_graph`` against hand-built ``AbiSnapshot``
objects and (optionally) a hand-built ``clang -ast-dump=json`` tree — no
compiler or build integration required, mirroring ``test_type_graph.py``'s
"pure function, unit-tested without a compiler" discipline.
"""

from __future__ import annotations

from abicheck.buildsource.header_graph import (
    HEADER_CALL_GRAPH_PASS,
    HEADER_TYPE_GRAPH_PASS,
    build_header_only_graph,
)
from abicheck.buildsource.source_graph import (
    is_internal_dependency_node,
    is_public_dependency_node,
)
from abicheck.model import AbiSnapshot, Function, ScopeOrigin, Variable

PUBLIC_HEADER = "/proj/include/pub.h"
PRIVATE_HEADER = "/proj/include/detail/impl.h"


def _snapshot(
    functions: list[Function] | None = None, variables: list[Variable] | None = None
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=functions or [],
        variables=variables or [],
    )


def _loc(file: str) -> dict:
    return {"file": file}


def _field(name: str, qual_type: str) -> dict:
    return {"kind": "FieldDecl", "name": name, "type": {"qualType": qual_type}}


def _record(
    name: str,
    *,
    file: str,
    bases: list[dict] | None = None,
    inner: list[dict] | None = None,
) -> dict:
    d: dict = {
        "kind": "CXXRecordDecl",
        "name": name,
        "loc": _loc(file),
        "inner": inner or [],
    }
    if bases is not None:
        d["bases"] = bases
    return d


def _base(qual_type: str) -> dict:
    return {"type": {"qualType": qual_type}, "writtenAccess": "public"}


def _tu(*decls: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(decls)}


# ── decl-node seeding (no ast_root needed) ──────────────────────────────────


def test_seeds_public_and_private_function_decls_with_visibility() -> None:
    public_fn = Function(
        name="pub_api",
        mangled="_Z7pub_apiv",
        return_type="void",
        source_location=f"{PUBLIC_HEADER}:10",
        source_header=PUBLIC_HEADER,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    private_fn = Function(
        name="helper",
        mangled="_ZN6detail6helperEv",
        return_type="void",
        source_location=f"{PRIVATE_HEADER}:5",
        source_header=PRIVATE_HEADER,
        origin=ScopeOrigin.PRIVATE_HEADER,
    )
    snap = _snapshot(functions=[public_fn, private_fn])
    graph = build_header_only_graph(snap)

    node_by_id = {n.id: n for n in graph.nodes}
    pub_id = "decl://_Z7pub_apiv"
    priv_id = "decl://_ZN6detail6helperEv"
    assert node_by_id[pub_id].attrs["visibility"] == "public_header"
    assert node_by_id[priv_id].attrs["visibility"] == "private_header"
    assert any(e.kind == "SOURCE_DECLARES" and e.dst == pub_id for e in graph.edges)
    # No AST supplied: no type/call edges, no extractor passes stamped.
    assert graph.extractor_passes == {}
    assert not any(e.kind in ("DECL_CALLS_DECL", "TYPE_INHERITS") for e in graph.edges)


def test_unknown_origin_when_no_public_header_set_supplied() -> None:
    fn = Function(name="f", mangled="_Z1fv", return_type="void")
    graph = build_header_only_graph(_snapshot(functions=[fn]))
    node = next(n for n in graph.nodes if n.id == "decl://_Z1fv")
    assert "visibility" not in node.attrs


def test_variable_decl_seeded_the_same_way() -> None:
    var = Variable(
        name="g_count",
        mangled="g_count",
        type="int",
        source_header=PUBLIC_HEADER,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    graph = build_header_only_graph(_snapshot(variables=[var]))
    node = next(n for n in graph.nodes if n.id == "decl://g_count")
    assert node.attrs["visibility"] == "public_header"


# ── type-node + edge folding (ast_root supplied) ────────────────────────────


def _headline_ast() -> dict:
    """The ADR's own motivating example: a public struct with a private field
    type, and a public function taking a private parameter type."""
    return _tu(
        {
            "kind": "NamespaceDecl",
            "name": "detail",
            "inner": [_record("Impl", file=PRIVATE_HEADER)],
        },
        _record(
            "Public",
            file=PUBLIC_HEADER,
            inner=[_field("p", "detail::Impl *")],
        ),
    )


def test_public_struct_with_private_field_type_classifies_correctly() -> None:
    ast = _headline_ast()
    graph = build_header_only_graph(
        _snapshot(),
        ast,
        public_header_paths=[PUBLIC_HEADER],
    )

    node_by_id = {n.id: n for n in graph.nodes}
    public_id = "type://Public"
    private_id = "type://detail::Impl"
    assert node_by_id[public_id].attrs["visibility"] == "public_header"
    assert node_by_id[private_id].attrs["visibility"] == "private_header"
    assert any(
        e.kind == "TYPE_HAS_FIELD_TYPE" and e.src == public_id and e.dst == private_id
        for e in graph.edges
    )

    # The exact classification crosscheck.py's public_to_internal_dependency
    # and source_graph_findings' version diff both rely on.
    exported: set[str] = set()
    assert is_public_dependency_node(public_id, node_by_id, exported)
    assert is_internal_dependency_node(private_id, node_by_id, exported, {})
    assert not is_internal_dependency_node(public_id, node_by_id, exported, {})


def test_extractor_passes_stamped_when_ast_supplied() -> None:
    ast = _headline_ast()
    graph = build_header_only_graph(_snapshot(), ast)
    assert graph.extractor_passes[HEADER_TYPE_GRAPH_PASS] is True
    assert graph.extractor_passes[HEADER_CALL_GRAPH_PASS] is True
    # finalize()'s coverage recognizes the header-only pass names too, not
    # just the build-integrated ones.
    assert graph.coverage["type_edges"]["collected"] is True


def test_base_class_edge_from_headers_alone() -> None:
    ast = _tu(
        _record("Base", file=PRIVATE_HEADER),
        _record("Derived", file=PUBLIC_HEADER, bases=[_base("Base")]),
    )
    graph = build_header_only_graph(
        _snapshot(),
        ast,
        public_header_paths=[PUBLIC_HEADER],
    )
    node_by_id = {n.id: n for n in graph.nodes}
    assert node_by_id["type://Base"].attrs["visibility"] == "private_header"
    assert any(
        e.kind == "TYPE_INHERITS"
        and e.src == "type://Derived"
        and e.dst == "type://Base"
        for e in graph.edges
    )


def test_no_ast_root_yields_no_type_nodes_or_edges() -> None:
    graph = build_header_only_graph(_snapshot())
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.extractor_passes == {}
