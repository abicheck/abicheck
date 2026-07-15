# Copyright 2026 Nikolay Petrov
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

"""Tests for ``clang_source_edges.build_source_edges`` (P1 #17-18): the
``SourceAbiTu.source_edges`` populator that reuses ``call_graph``'s /
``type_graph``'s pure AST parsers on an already-parsed clang AST dict.

Hand-built ``clang -ast-dump=json`` trees so no compiler is required."""

from __future__ import annotations

import pytest

import abicheck.buildsource.call_graph as call_graph
import abicheck.buildsource.type_graph as type_graph
from abicheck.buildsource.source_extractors.clang_source_edges import (
    build_source_edges,
)


def _ref(kind: str, name: str, mangled: str = "") -> dict:
    d: dict = {"kind": kind, "name": name}
    if mangled:
        d["mangledName"] = mangled
    return d


def _direct_call(callee: dict) -> dict:
    return {
        "kind": "CallExpr",
        "inner": [
            {
                "kind": "ImplicitCastExpr",
                "inner": [{"kind": "DeclRefExpr", "referencedDecl": callee}],
            }
        ],
    }


def _func(name: str, mangled: str, body: list[dict]) -> dict:
    return {
        "kind": "FunctionDecl",
        "name": name,
        "mangledName": mangled,
        "inner": [{"kind": "CompoundStmt", "inner": body}],
    }


def _base(qual_type: str) -> dict:
    return {"type": {"qualType": qual_type}, "writtenAccess": "public"}


def _record(name: str, *, bases: list[dict] | None = None) -> dict:
    d: dict = {"kind": "CXXRecordDecl", "name": name, "inner": []}
    if bases is not None:
        d["bases"] = bases
    return d


def _tu(*decls: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(decls)}


def test_build_source_edges_maps_call_and_type_edges() -> None:
    ast = _tu(
        _record("Base"),
        _record("Derived", bases=[_base("Base")]),
        _func(
            "caller",
            "_Zcaller",
            [_direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))],
        ),
    )
    diags: list[str] = []
    edges = build_source_edges(ast, diags)
    assert diags == []

    call_edges = [e for e in edges if e["edge"] == "DECL_CALLS_DECL"]
    assert call_edges == [
        {
            "edge": "DECL_CALLS_DECL",
            "src": "_Zcaller",
            "dst": "_Zcallee",
            "provenance": "clang-ast-inline",
            "confidence": "high",
            "attrs": {"call_kind": "direct", "resolution": "exact"},
        }
    ]

    type_edges = [e for e in edges if e["edge"] == "TYPE_INHERITS"]
    assert len(type_edges) == 1
    inherits = type_edges[0]
    assert inherits["src"] == "Derived"
    assert inherits["dst"] == "Base"
    assert inherits["provenance"] == "clang-ast-inline"


def test_build_source_edges_dedupes_identical_kind_src_dst() -> None:
    ast = _tu(
        _func(
            "caller",
            "_Zcaller",
            [
                _direct_call(_ref("FunctionDecl", "callee", "_Zcallee")),
                _direct_call(_ref("FunctionDecl", "callee", "_Zcallee")),
            ],
        ),
    )
    edges = build_source_edges(ast, [])
    assert len(edges) == 1


def test_build_source_edges_empty_ast_yields_no_edges() -> None:
    assert build_source_edges(_tu(), []) == []


def test_build_source_edges_call_failure_preserves_type_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A call-parser failure must not discard the type parser's successful
    # edges (CodeRabbit review, P2): each parser is caught independently.
    def _boom(ast_root: dict) -> list:
        raise ValueError("boom")

    monkeypatch.setattr(call_graph, "parse_clang_ast_calls", _boom)
    ast = _tu(_record("Base"), _record("Derived", bases=[_base("Base")]))
    diags: list[str] = []
    edges = build_source_edges(ast, diags)
    assert any(e["edge"] == "TYPE_INHERITS" for e in edges)
    assert not any(e["edge"] == "DECL_CALLS_DECL" for e in edges)
    assert len(diags) == 1
    assert diags[0].startswith("source_edges unavailable: call parser failed:")


def test_build_source_edges_type_failure_preserves_call_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A type-parser failure must not discard the call parser's successful
    # edges (CodeRabbit review, P2): each parser is caught independently.
    def _boom(ast_root: dict) -> list:
        raise ValueError("boom")

    monkeypatch.setattr(type_graph, "parse_clang_ast_types", _boom)
    ast = _tu(
        _func(
            "caller",
            "_Zcaller",
            [_direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))],
        )
    )
    diags: list[str] = []
    edges = build_source_edges(ast, diags)
    assert any(e["edge"] == "DECL_CALLS_DECL" for e in edges)
    assert not any(e["edge"] == "TYPE_INHERITS" for e in edges)
    assert len(diags) == 1
    assert diags[0].startswith("source_edges unavailable: type parser failed:")


def test_build_source_edges_both_parsers_fail_yields_two_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(ast_root: dict) -> list:
        raise ValueError("boom")

    monkeypatch.setattr(call_graph, "parse_clang_ast_calls", _boom)
    monkeypatch.setattr(type_graph, "parse_clang_ast_types", _boom)
    diags: list[str] = []
    edges = build_source_edges(_tu(), diags)
    assert edges == []
    assert len(diags) == 2
