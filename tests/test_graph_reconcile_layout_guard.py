# SPDX-License-Identifier: Apache-2.0
"""A rename never changes a record's layout, so a layout mismatch is no rename.

Structural-context reconciliation pairs two records that sit in the same parent
position. When both records recorded a size or alignment and those differ, the
pair is two declarations, not one renamed declaration. Two consequences are
pinned here: the reconciler refuses the pair, and a public entry's newly reached
target therefore stays a ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`` finding instead
of being reported as a rename (``PUBLIC_API_INTERNAL_TYPE_RENAMED``) that says the
dependency did not change.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.graph_reconcile import (
    OUTCOME_RENAMED,
    reconcile_added_removed,
)
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.buildsource.source_graph_findings import diff_source_graph_findings
from abicheck.model.change_catalog.kinds import ChangeKind


def _graph(nodes: list[GraphNode], edges: list[GraphEdge]) -> SourceGraphSummary:
    g = SourceGraphSummary()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    return g.finalize()


def _record(name: str, **layout: int) -> GraphNode:
    return GraphNode(
        id=f"type://{name}",
        kind="record_type",
        label=name,
        attrs={"qualified_name": name, "def_file": "api.h", **layout},
    )


def _held(child: GraphNode) -> SourceGraphSummary:
    """A graph in which a ``holder`` record has *child* as its field."""
    holder = GraphNode(id="type://holder", kind="record_type", label="holder")
    edge = GraphEdge(
        src=holder.id, dst=child.id, kind="TYPE_HAS_FIELD_TYPE", attrs={"role": "field"}
    )
    return _graph([holder, child], [edge])


def _pair(old: GraphNode, new: GraphNode):  # noqa: ANN202 -- a GraphReconciliation
    return reconcile_added_removed([old], [new], _held(old), _held(new))


@pytest.mark.parametrize(
    ("old_layout", "new_layout"),
    [
        ({"size_bits": 64}, {"size_bits": 128}),
        ({"alignment_bits": 32}, {"alignment_bits": 64}),
        (
            {"size_bits": 64, "alignment_bits": 32},
            {"size_bits": 64, "alignment_bits": 64},
        ),
    ],
)
def test_layout_mismatch_is_not_a_rename(
    old_layout: dict[str, int], new_layout: dict[str, int]
) -> None:
    old = _record("A", **old_layout)
    new = _record("B", **new_layout)
    result = _pair(old, new)
    assert result.reconciled == []
    assert result.true_removed == [old]
    assert result.true_added == [new]


def test_matching_recorded_layout_still_reconciles() -> None:
    old = _record("A", size_bits=64, alignment_bits=32)
    new = _record("B", size_bits=64, alignment_bits=32)
    result = _pair(old, new)
    assert [p.outcome for p in result.reconciled] == [OUTCOME_RENAMED]


def test_unrecorded_layout_on_one_side_proves_nothing() -> None:
    # A side that did not collect its layout cannot contradict the other side.
    old = _record("A", size_bits=64)
    new = _record("B")
    assert [p.outcome for p in _pair(old, new).reconciled] == [OUTCOME_RENAMED]


def _dependency_fixture(old_size: int, new_size: int):  # noqa: ANN202
    parent = GraphNode(
        id="type:demo::Config",
        kind="record_type",
        label="demo::Config",
        attrs={"qualified_name": "demo::Config", "visibility": "public_header"},
    )
    header = GraphNode(
        id="hdr:include/demo/config.h", kind="header", label="include/demo/config.h"
    )
    decl = GraphEdge(src=header.id, dst=parent.id, kind="SOURCE_DECLARES")
    old_target = _record("demo::detail::RawConfig", size_bits=old_size)
    new_target = _record("demo::detail::RawConfigV2", size_bits=new_size)
    for target in (old_target, new_target):
        target.attrs["def_file"] = "detail.h"
        target.attrs["visibility"] = "private_header"

    def field(target: str) -> GraphEdge:
        return GraphEdge(
            src=parent.id,
            dst=target,
            kind="TYPE_HAS_FIELD_TYPE",
            attrs={"role": "field"},
        )

    old = _graph([parent, header, old_target], [decl, field(old_target.id)])
    new = _graph([parent, header, new_target], [decl, field(new_target.id)])
    return old, new


def test_layout_changing_dependency_stays_an_added_finding() -> None:
    old, new = _dependency_fixture(old_size=64, new_size=128)
    kinds = [c.kind for c in diff_source_graph_findings(old, new)]
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED in kinds
    assert ChangeKind.PUBLIC_API_INTERNAL_TYPE_RENAMED not in kinds
    assert ChangeKind.DECLARATION_RENAMED not in kinds


def test_same_layout_dependency_rename_is_still_one_finding() -> None:
    old, new = _dependency_fixture(old_size=64, new_size=64)
    kinds = [c.kind for c in diff_source_graph_findings(old, new)]
    assert kinds.count(ChangeKind.PUBLIC_API_INTERNAL_TYPE_RENAMED) == 1
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED not in kinds
