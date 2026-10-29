# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A fundamental (builtin) type is never a rename candidate in graph reconciliation.

Regression for a false clean: ``void f(float _Complex p)`` -> ``void f(double
_Complex p)`` (parameter 8 -> 16 bytes) was reported as ``declaration_renamed``
("the same declaration, renamed") by the L5 graph reconciler. ``_Complex float``
and ``_Complex double`` are type nodes with no declaration and no declaring
file, and they sit in the same parent position, so the structural-context tier
paired them. A rename may only pair two user-declared entities.

The oracle for "fundamental" is the spelling list below, written out by hand
(not the predicate under test), and the pairing is checked through the public
reconciliation entry points.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.graph_reconcile import (
    OUTCOME_RENAMED,
    diff_graph_reconciliation_findings,
    reconcile_added_removed,
)
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.checker_policy import ChangeKind
from abicheck.model.fundamental_type import is_fundamental_type_spelling

#: Every fundamental base spelling a parameter, field or return can carry.
_BASE_SPELLINGS: tuple[str, ...] = (
    "char",
    "signed char",
    "unsigned char",
    "short",
    "int",
    "long",
    "long long",
    "float",
    "double",
    "long double",
    "float _Complex",
    "double _Complex",
    "long double _Complex",
    "_Float16",
    "__int128",
    "_Bool",
)

#: Each base with the decorations a declaration may add around it.
_DECORATIONS: tuple[str, ...] = ("{}", "const {}", "{} *", "const {} *")

_SPELLINGS: tuple[str, ...] = tuple(
    dec.format(base) for base in _BASE_SPELLINGS for dec in _DECORATIONS
)

_PARENT = GraphNode(id="type://holder", kind="record_type", label="holder")


def _graph(nodes: list[GraphNode], edges: list[GraphEdge]) -> SourceGraphSummary:
    g = SourceGraphSummary()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    return g.finalize()


def _child_graph(child: GraphNode) -> SourceGraphSummary:
    """A graph where *_PARENT* holds *child* as a field (one structural position)."""
    edge = GraphEdge(
        src=_PARENT.id,
        dst=child.id,
        kind="TYPE_HAS_FIELD_TYPE",
        attrs={"role": "field"},
    )
    return _graph([_PARENT, child], [edge])


def _builtin_node(spelling: str, suffix: str) -> GraphNode:
    return GraphNode(
        id=f"type://{spelling}{suffix}", kind="record_type", label=spelling
    )


def test_fundamental_predicate_accepts_every_builtin_spelling() -> None:
    # Positive oracle: every spelling in the matrix, plus the vector, SIMD and
    # bit-precise forms a header can spell by keyword.
    extra = (
        "float __attribute__((vector_size(16)))",
        "__m128",
        "_BitInt(8)",
        "unsigned _BitInt(16)",
        "void (*)(int)",
    )
    missed = [s for s in (*_SPELLINGS, *extra) if not is_fundamental_type_spelling(s)]
    assert missed == []


@pytest.mark.parametrize(
    "user_spelling",
    [
        "struct S",
        "struct S *",
        "S",
        "std::string",
        "myint",
        "Int",
        "unsigned_flag",
        "A::B",
    ],
)
def test_fundamental_predicate_rejects_user_declared_names(user_spelling: str) -> None:
    assert not is_fundamental_type_spelling(user_spelling)


def test_no_fundamental_pair_is_ever_reported_as_a_rename() -> None:
    # Every ordered pair of distinct fundamental spellings, each on its own
    # structural position (the producer's shape for a field or parameter).
    offenders: list[str] = []
    checked = 0
    for old_spelling in _SPELLINGS:
        for new_spelling in _SPELLINGS:
            if old_spelling == new_spelling:
                continue
            checked += 1
            old_node = _builtin_node(old_spelling, "")
            new_node = _builtin_node(new_spelling, "")
            result = reconcile_added_removed(
                [old_node],
                [new_node],
                _child_graph(old_node),
                _child_graph(new_node),
            )
            renamed = [
                f
                for f in diff_graph_reconciliation_findings(result)
                if f.kind is ChangeKind.DECLARATION_RENAMED
            ]
            if result.reconciled or renamed:
                offenders.append(f"{old_spelling!r} -> {new_spelling!r}")
                continue
            assert result.true_removed == [old_node]
            assert result.true_added == [new_node]
    assert checked == len(_SPELLINGS) * (len(_SPELLINGS) - 1)
    assert offenders == [], f"fundamental spellings reconciled as a rename: {offenders}"


def test_real_struct_rename_with_same_layout_still_reconciles() -> None:
    # Positive control: a user-declared struct renamed in place keeps reconciling
    # as a rename, through the same structural-context tier the builtin pairs
    # above are refused by.
    old_node = GraphNode(
        id="type://A",
        kind="record_type",
        label="A",
        attrs={"qualified_name": "A", "def_file": "api.h"},
    )
    new_node = GraphNode(
        id="type://B",
        kind="record_type",
        label="B",
        attrs={"qualified_name": "B", "def_file": "api.h"},
    )
    result = reconcile_added_removed(
        [old_node], [new_node], _child_graph(old_node), _child_graph(new_node)
    )
    assert [p.outcome for p in result.reconciled] == [OUTCOME_RENAMED]
    assert result.reconciled[0].match_kind == "structural_context"
    kinds = [f.kind for f in diff_graph_reconciliation_findings(result)]
    assert kinds == [ChangeKind.DECLARATION_RENAMED]


@pytest.mark.parametrize(
    ("old_name", "new_name"),
    [("Int", "Int2"), ("unsigned_flag", "unsigned_flag_v2"), ("A", "B")],
)
def test_user_names_that_look_like_keywords_still_reconcile(
    old_name: str, new_name: str
) -> None:
    # Token-level, not substring: a user identifier that only contains a
    # keyword's letters is still a user declaration and still a rename candidate.
    old_node = GraphNode(
        id=f"type://{old_name}",
        kind="record_type",
        label=old_name,
        attrs={"qualified_name": old_name, "def_file": "api.h"},
    )
    new_node = GraphNode(
        id=f"type://{new_name}",
        kind="record_type",
        label=new_name,
        attrs={"qualified_name": new_name, "def_file": "api.h"},
    )
    result = reconcile_added_removed(
        [old_node], [new_node], _child_graph(old_node), _child_graph(new_node)
    )
    assert len(result.reconciled) == 1
