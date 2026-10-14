# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""Per-declaration header body coverage (ADR-077 D5; known gap
``header-body-coverage-is-not-per-body``, closed).

A header-only pass sees a call/reference only inside a body written in a
header. These tests build header-only graphs from hand-built clang ASTs and
check the bug-class invariant through the public finding
(``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED``):

* a body-dependent dependency added inside a body **seen on both sides** is
  reported;
* one whose body is **unseen on either side** (out-of-line there) is
  declined -- never a finding invented from a side that could not look.

The oracle is independent of the implementation: for each case the
expectation is written down from the AST shape (which side carries the body,
which side carries the call), not computed by ``bodies_seen``.
"""

from __future__ import annotations

import itertools
import json

import pytest

from abicheck.buildsource.call_graph import collect_function_bodies
from abicheck.buildsource.header_graph import build_header_only_graph
from abicheck.buildsource.header_graph_ast_projection import (
    merge_header_graph_ast_projections,
    project_header_graph_ast,
)
from abicheck.buildsource.header_graph_ast_stream import (
    project_header_graph_ast_file,
)
from abicheck.buildsource.header_graph_projection_cache import (
    decode_projection,
    encode_projection,
)
from abicheck.buildsource.source_graph_findings import diff_source_graph_findings
from abicheck.model import AbiSnapshot, Function, ScopeOrigin
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.model.source_graph_coverage import (
    CALL_GRAPH_PASS,
    HEADER_BODY_ATTR,
    TYPE_GRAPH_PASS,
    bodies_seen,
    body_scoped_closures,
)

PUB = "/proj/include/pub.h"
PRIV = "/proj/include/detail/impl.h"
ADDED = "public_api_internal_dependency_added"

_PUBLIC_FNS = {"f": "_Z1fv", "g": "_Z1gv"}
_PRIVATE_FNS = {"helper": "_ZN6detail6helperEv", "other": "_ZN6detail5otherEv"}


def _fn(name: str, mangled: str, header: str, origin: ScopeOrigin) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        source_location=f"{header}:1",
        source_header=header,
        origin=origin,
    )


def _snapshot() -> AbiSnapshot:
    fns = [_fn(n, m, PUB, ScopeOrigin.PUBLIC_HEADER) for n, m in _PUBLIC_FNS.items()]
    fns += [
        _fn(n, m, PRIV, ScopeOrigin.PRIVATE_HEADER) for n, m in _PRIVATE_FNS.items()
    ]
    return AbiSnapshot(library="libfoo.so.1", version="1", functions=fns)


def _call(callee: str) -> dict:
    mangled = _PRIVATE_FNS[callee]
    return {
        "kind": "CallExpr",
        "inner": [
            {
                "kind": "DeclRefExpr",
                "referencedDecl": {
                    "kind": "FunctionDecl",
                    "name": callee,
                    "mangledName": mangled,
                },
            }
        ],
    }


#: A private global a body can reference without calling anything.
_PRIVATE_VAR = {
    "kind": "VarDecl",
    "name": "counter",
    "mangledName": "_ZN6detail7counterE",
    "loc": {"file": PRIV},
    "type": {"qualType": "int"},
}


def _ref() -> dict:
    """A non-call read of the private global -> ``DECL_REFERENCES_DECL``."""
    return {
        "kind": "ImplicitCastExpr",
        "inner": [
            {
                "kind": "DeclRefExpr",
                "referencedDecl": {
                    "kind": "VarDecl",
                    "name": "counter",
                    "mangledName": "_ZN6detail7counterE",
                },
            }
        ],
    }


def _decl(name: str, mangled: str, file: str, body: list[dict] | None) -> dict:
    node: dict = {
        "kind": "FunctionDecl",
        "name": name,
        "mangledName": mangled,
        "loc": {"file": file},
        "type": {"qualType": "void ()"},
    }
    if body is not None:
        node["inner"] = [{"kind": "CompoundStmt", "inner": body}]
    return node


def _ast(bodies: dict[str, list[dict] | None]) -> dict:
    """A TU declaring every private helper, then each public function with
    the given body (``None`` = prototype only, i.e. an out-of-line body)."""
    decls = [_PRIVATE_VAR, *(_decl(n, m, PRIV, None) for n, m in _PRIVATE_FNS.items())]
    decls += [_decl(n, _PUBLIC_FNS[n], PUB, b) for n, b in bodies.items()]
    return {"kind": "TranslationUnitDecl", "inner": decls}


def _graph(bodies: dict[str, list[dict] | None]) -> SourceGraphSummary:
    return build_header_only_graph(
        _snapshot(),
        ast_projection=project_header_graph_ast(_ast(bodies)),
        public_header_paths=[PUB],
    )


def _added(old: SourceGraphSummary, new: SourceGraphSummary) -> set[str]:
    return {
        c.symbol for c in diff_source_graph_findings(old, new) if c.kind.value == ADDED
    }


# ── the bug class: every (old body, new body) shape for one public entry ────

#: (old body, new body) -> the entry the finding must name, from the shapes
#: alone: reported only when both sides carry the body and only new calls
#: the private helper.
_SHAPES = [
    # both bodies seen, call added -> reported
    ([], [_call("helper")], True),
    # both seen, call to a second helper added next to an existing one
    ([_call("helper")], [_call("helper"), _call("other")], True),
    # both seen, nothing new -> nothing
    ([_call("helper")], [_call("helper")], False),
    # both seen, call removed -> not an addition
    ([_call("helper")], [], False),
    # old body out-of-line (unseen): its absence proves nothing -> declined
    (None, [_call("helper")], False),
    # new body out-of-line: no edge observed, nothing to report
    ([], None, False),
    (None, None, False),
]


@pytest.mark.parametrize(("old_body", "new_body", "reported"), _SHAPES)
def test_body_dependent_call_judged_only_where_both_bodies_seen(
    old_body: list[dict] | None, new_body: list[dict] | None, reported: bool
) -> None:
    old = _graph({"f": old_body, "g": []})
    new = _graph({"f": new_body, "g": []})
    assert _added(old, new) == ({"f"} if reported else set())


def test_unseen_sibling_body_does_not_leak_trust_to_seen_one() -> None:
    # f's body is seen on both sides and gains a call; g's body is out of
    # line on the old side and also "gains" one on the new side. Only f is
    # reportable -- g's old absence was never looked for.
    old = _graph({"f": [], "g": None})
    new = _graph({"f": [_call("helper")], "g": [_call("other")]})
    assert _added(old, new) == {"f"}


@pytest.mark.parametrize(
    ("old_f", "old_g"), list(itertools.product([True, False], repeat=2))
)
def test_every_seen_unseen_combination_across_two_entries(
    old_f: bool, old_g: bool
) -> None:
    # Exhaustive small domain: both entries gain a call on the new side,
    # whose bodies are always seen; each old body is seen or not.
    old = _graph({"f": [] if old_f else None, "g": [] if old_g else None})
    new = _graph({"f": [_call("helper")], "g": [_call("other")]})
    expected = {name for name, seen in (("f", old_f), ("g", old_g)) if seen}
    assert _added(old, new) == expected


@pytest.mark.parametrize(
    ("old_f", "reported"), [([], True), ([_ref()], False), (None, False)]
)
def test_non_call_reference_judged_per_body_too(
    old_f: list[dict] | None, reported: bool
) -> None:
    old = _graph({"f": old_f, "g": []})
    new = _graph({"f": [_ref()], "g": []})
    assert any(
        e.kind == "DECL_REFERENCES_DECL" and e.src == "decl://_Z1fv" for e in new.edges
    )
    assert not any(e.kind == "DECL_CALLS_DECL" for e in new.edges)
    assert _added(old, new) == ({"f"} if reported else set())


# ── the producer: which bodies the header pass marks ────────────────────────


def test_marks_exactly_the_bodies_written_in_headers() -> None:
    graph = _graph({"f": [], "g": None})
    marked = {n.id for n in graph.nodes if n.attrs.get(HEADER_BODY_ATTR) is True}
    assert marked == {"decl://_Z1fv"}
    assert bodies_seen(graph, CALL_GRAPH_PASS) == frozenset({"decl://_Z1fv"})
    assert bodies_seen(graph, TYPE_GRAPH_PASS) == frozenset({"decl://_Z1fv"})


def test_collect_function_bodies_qualifies_scope_and_skips_prototypes() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "NamespaceDecl",
                "name": "ns",
                "inner": [
                    {
                        "kind": "CXXRecordDecl",
                        "name": "C",
                        "inner": [
                            {
                                "kind": "CXXMethodDecl",
                                "name": "m",
                                "type": {"qualType": "void ()"},
                                "inner": [{"kind": "CompoundStmt"}],
                            },
                            {
                                "kind": "CXXMethodDecl",
                                "name": "proto",
                                "type": {"qualType": "void ()"},
                            },
                        ],
                    }
                ],
            },
            "not-a-node",
        ],
    }
    found = collect_function_bodies(ast)
    assert len(found) == 1
    assert next(iter(found)).startswith("ns::C::m")


def test_projection_merge_and_cache_keep_bodies() -> None:
    a = project_header_graph_ast(_ast({"f": []}))
    b = project_header_graph_ast(_ast({"g": []}))
    merged = merge_header_graph_ast_projections([a, b])
    assert merged.function_bodies == {"_Z1fv", "_Z1gv"}
    decoded = decode_projection(encode_projection(merged))
    assert decoded is not None
    assert decoded.function_bodies == merged.function_bodies


# ── bodies_seen / body_scoped_closures edge cases ───────────────────────────


def test_degraded_or_absent_header_pass_vouches_for_no_body() -> None:
    graph = _graph({"f": [], "g": []})
    graph.degraded_passes["header_call_graph"] = True
    assert bodies_seen(graph, CALL_GRAPH_PASS) == frozenset()
    assert bodies_seen(SourceGraphSummary(), CALL_GRAPH_PASS) == frozenset()
    assert bodies_seen(graph, "no_such_pass") == frozenset()


def test_build_integrated_pass_vouches_for_every_body() -> None:
    full = SourceGraphSummary()
    full.extractor_passes[CALL_GRAPH_PASS] = True
    assert bodies_seen(full, CALL_GRAPH_PASS) is None
    header = _graph({"f": [_call("helper")], "g": None})
    # Full old side x header new side: the joint set is the new side's.
    old_c, new_c, kinds = body_scoped_closures(full, header, frozenset())
    assert "DECL_CALLS_DECL" in kinds
    assert old_c is full
    assert all(
        e.src == "decl://_Z1fv" for e in new_c.edges if e.kind == "DECL_CALLS_DECL"
    )


def test_no_qualifying_body_leaves_inputs_untouched() -> None:
    empty = SourceGraphSummary()
    graph = _graph({"f": [], "g": []})
    assert body_scoped_closures(empty, graph, frozenset()) == (
        empty,
        graph,
        frozenset(),
    )


def test_streamed_projection_records_the_same_bodies(tmp_path) -> None:
    ast = _ast({"f": [_call("helper")], "g": None})
    path = tmp_path / "tu.ast.json"
    path.write_text(json.dumps(ast), encoding="utf-8")
    streamed = project_header_graph_ast_file(path)
    assert streamed.function_bodies == {"_Z1fv"}
    assert streamed.function_bodies == project_header_graph_ast(ast).function_bodies
