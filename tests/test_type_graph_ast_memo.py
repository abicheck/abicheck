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

"""Contract of ``type_graph.ast_derived_scope``: one walk per AST, same answers.

Several consumers derive from one clang AST: the L5 ``type_graph`` pass and
``override_graph``'s two parsers each need the type edges, and the header-only
projection needs the type-file index, the edges and the entity-file index.
Each used to walk the whole tree again. Inside ``ast_derived_scope`` they now
share one index walk and one edge walk per tree. Stated as invariants:

- every answer inside a scope equals the answer outside one, and under
  ``ABICHECK_REFERENCE_MODE`` (the oracle: the uncached computation);
- inside a scope each tree is indexed once and its edges built once,
  however many consumers ask and in whatever order -- observed by spying on
  the walk itself, so a cache that caches nothing cannot pass;
- an equal-but-distinct tree never hits (identity, not equality, is the key);
- a returned value never aliases the memo: mutating it changes nothing later;
- outside a scope nothing is reused;
- through the real L5 runner, one TU's three type-graph readers walk it once.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.buildsource import l5_ast_pass, type_graph
from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.header_graph_ast_projection import project_header_graph_ast
from abicheck.buildsource.override_graph import (
    parse_clang_ast_overrides,
    parse_clang_ast_virtual_methods,
)
from abicheck.buildsource.type_graph import (
    ast_derived_scope,
    index_declared_entity_files,
    index_declared_type_files,
    parse_clang_ast_types,
)

# ── a small generator of clang-AST-shaped trees ─────────────────────────────

_NAMES = ["A", "B", "C", "D"]
_TYPES = ["int", "A", "B *", "const C &", "ns::D", "unsigned long"]


def _record(
    name: str, bases: list[str], fields: list[str], methods: list[dict]
) -> dict:
    return {
        "kind": "CXXRecordDecl",
        "name": name,
        "loc": {"file": f"include/{name.lower()}.h"},
        "tagUsed": "struct",
        "completeDefinition": True,
        "bases": [{"type": {"qualType": b}, "writtenAccess": "public"} for b in bases],
        "inner": [
            *(
                {"kind": "FieldDecl", "name": f"f{i}", "type": {"qualType": t}}
                for i, t in enumerate(fields)
            ),
            *methods,
        ],
    }


def _method(name: str, virtual: bool, param: str) -> dict:
    return {
        "kind": "CXXMethodDecl",
        "name": name,
        "mangledName": f"_Z{name}",
        "virtual": virtual,
        "type": {"qualType": f"void ({param})"},
        "inner": [{"kind": "ParmVarDecl", "name": "p", "type": {"qualType": param}}],
    }


_methods = st.lists(
    st.builds(
        _method,
        st.sampled_from(["run", "stop"]),
        st.booleans(),
        st.sampled_from(_TYPES),
    ),
    max_size=2,
)
_records = st.builds(
    _record,
    st.sampled_from(_NAMES),
    st.lists(st.sampled_from(_NAMES), max_size=2),
    st.lists(st.sampled_from(_TYPES), max_size=3),
    _methods,
)
_vars = st.builds(
    lambda n, t: {
        "kind": "VarDecl",
        "name": n,
        "loc": {"file": "include/vars.h"},
        "type": {"qualType": t},
    },
    st.sampled_from(["k", "g"]),
    st.sampled_from(_TYPES),
)
_decls = st.lists(st.one_of(_records, _vars), max_size=5)


@st.composite
def _asts(draw: Any) -> dict:
    top = draw(_decls)
    inner_ns = draw(_decls)
    return {
        "kind": "TranslationUnitDecl",
        "inner": [*top, {"kind": "NamespaceDecl", "name": "ns", "inner": inner_ns}],
    }


def _all_answers(ast: dict) -> tuple:
    return (
        parse_clang_ast_types(ast),
        index_declared_type_files(ast),
        index_declared_entity_files(ast),
        parse_clang_ast_overrides(ast),
        parse_clang_ast_virtual_methods(ast),
    )


class _WalkSpy:
    """Counts whole-tree index walks and edge builds (each starts with exactly
    one ``_new_ast_indexes`` / ``_dedupe_edges`` call)."""

    def __init__(self, mp: pytest.MonkeyPatch) -> None:
        self.indexes = 0
        self.edges = 0
        new_idx, dedupe = type_graph._new_ast_indexes, type_graph._dedupe_edges

        def spy_idx() -> Any:
            self.indexes += 1
            return new_idx()

        def spy_dedupe(edges: Any) -> Any:
            self.edges += 1
            return dedupe(edges)

        mp.setattr(type_graph, "_new_ast_indexes", spy_idx)
        mp.setattr(type_graph, "_dedupe_edges", spy_dedupe)


# ── invariants ──────────────────────────────────────────────────────────────


@settings(max_examples=150, deadline=None)
@given(ast=_asts(), order=st.permutations(range(5)))
def test_scoped_answers_equal_unscoped_and_reference_mode(
    ast: dict, order: list[int]
) -> None:
    unscoped = _all_answers(ast)
    with ast_derived_scope():
        consumers = [
            parse_clang_ast_types,
            index_declared_type_files,
            index_declared_entity_files,
            parse_clang_ast_overrides,
            parse_clang_ast_virtual_methods,
        ]
        scoped = [None] * 5
        for i in order:  # whatever order consumers ask in
            scoped[i] = consumers[i](ast)
        assert tuple(scoped) == unscoped
        assert _all_answers(ast) == unscoped  # and again, now all hits
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ABICHECK_REFERENCE_MODE", "1")
        with ast_derived_scope():
            assert _all_answers(ast) == unscoped


@settings(max_examples=60, deadline=None)
@given(ast=_asts(), repeats=st.integers(1, 4))
def test_one_index_walk_and_one_edge_build_per_tree_in_scope(
    ast: dict, repeats: int
) -> None:
    with pytest.MonkeyPatch.context() as mp:
        spy = _WalkSpy(mp)
        with ast_derived_scope():
            for _ in range(repeats):
                _all_answers(ast)
        assert (spy.indexes, spy.edges) == (1, 1)


def test_outside_a_scope_nothing_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    ast = {"kind": "TranslationUnitDecl", "inner": [_record("A", [], ["int"], [])]}
    spy = _WalkSpy(monkeypatch)
    parse_clang_ast_types(ast)
    parse_clang_ast_types(ast)
    index_declared_type_files(ast)
    assert (spy.indexes, spy.edges) == (3, 2)


def test_reference_mode_computes_every_time(monkeypatch: pytest.MonkeyPatch) -> None:
    ast = {"kind": "TranslationUnitDecl", "inner": [_record("A", ["B"], ["int"], [])]}
    spy = _WalkSpy(monkeypatch)
    monkeypatch.setenv("ABICHECK_REFERENCE_MODE", "1")
    with ast_derived_scope():
        parse_clang_ast_types(ast)
        parse_clang_ast_types(ast)
    assert (spy.indexes, spy.edges) == (2, 2)


@settings(max_examples=60, deadline=None)
@given(ast=_asts())
def test_an_equal_but_distinct_tree_never_hits(ast: dict) -> None:
    twin = copy.deepcopy(ast)
    with pytest.MonkeyPatch.context() as mp:
        spy = _WalkSpy(mp)
        with ast_derived_scope():
            first = parse_clang_ast_types(ast)
            second = parse_clang_ast_types(twin)
        assert first == second
        assert (spy.indexes, spy.edges) == (2, 2)


def test_returned_values_never_alias_the_memo() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _record("A", [], ["int"], []),
            _record("B", ["A"], ["A"], []),
            {
                "kind": "VarDecl",
                "name": "k",
                "loc": {"file": "include/k.h"},
                "type": {"qualType": "int"},
            },
        ],
    }
    with ast_derived_scope():
        edges = parse_clang_ast_types(ast)
        files = index_declared_entity_files(ast)
        type_files = index_declared_type_files(ast)
        expected = (list(edges), dict(files), dict(type_files))
        assert edges and files
        edges.clear()
        files.clear()
        type_files.clear()
        assert (
            parse_clang_ast_types(ast),
            index_declared_entity_files(ast),
            index_declared_type_files(ast),
        ) == expected


def test_header_projection_indexes_its_tree_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _record("A", [], ["int"], []),
            {
                "kind": "FunctionDecl",
                "name": "f",
                "mangledName": "_Z1fv",
                "type": {"qualType": "int ()"},
                "inner": [
                    {
                        "kind": "CompoundStmt",
                        "inner": [
                            {
                                "kind": "DeclRefExpr",
                                "referencedDecl": {"kind": "VarDecl", "name": "k"},
                            }
                        ],
                    }
                ],
            },
            {
                "kind": "VarDecl",
                "name": "k",
                "loc": {"file": "include/k.h"},
                "type": {"qualType": "int"},
            },
        ],
    }
    spy = _WalkSpy(monkeypatch)
    projection = project_header_graph_ast(ast)
    assert spy.indexes == 1
    # The entity-file index was really consulted (a DECL_REFERENCES_DECL edge
    # exists), so "one walk" is not "one fewer consumer".
    assert any(e.kind == "DECL_REFERENCES_DECL" for e in projection.type_edges)
    assert projection.entity_files


@settings(max_examples=40, deadline=None)
@given(asts=st.lists(_asts(), min_size=1, max_size=3))
def test_the_l5_runner_walks_each_tu_once_for_all_type_graph_readers(
    asts: list[dict],
) -> None:
    units = [
        CompileUnit(id=f"cu://{i}", source=f"src/{i}.cpp") for i in range(len(asts))
    ]
    by_source = {cu.source: ast for cu, ast in zip(units, asts, strict=True)}

    def fake_dump(
        clang_bin: str, argv: list[str], *, cwd: Any, diagnostics: list
    ) -> dict:
        return by_source[argv[-1]]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(l5_ast_pass, "run_clang_ast_dump", fake_dump)
        mp.setattr(l5_ast_pass, "_call_graph_jobs", lambda n: 1)
        spy = _WalkSpy(mp)
        outcomes = l5_ast_pass.run_ast_passes(
            BuildEvidence(compile_units=units), "clang++"
        )
        walked = (spy.indexes, spy.edges)
    # type_graph pass + parse_clang_ast_overrides + parse_clang_ast_virtual_methods
    # all read each TU's type graph; it is built once per TU.
    assert walked == (len(asts), len(asts))
    # And the runner's answers are the uncached ones.
    assert outcomes["type_graph"].result == type_graph.merge_type_edges(
        [parse_clang_ast_types(a) for a in asts]
    )
