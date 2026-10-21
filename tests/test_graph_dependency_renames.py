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

"""G31 Phase D: a public entry's renamed internal dependency target is one finding.

``PUBLIC_API_INTERNAL_TYPE_RENAMED`` replaces the ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED``
finding for a target that ADR-048 reconciliation resolved as the renamed spelling of a
node the same public entry already reached. Everything else keeps today's behaviour:
an unrelated add, a move with no rename, and an ambiguous or unresolved candidate.
"""

from __future__ import annotations

from types import SimpleNamespace

from abicheck.buildsource.dependency_renames import renamed_dependency_targets
from abicheck.buildsource.graph_reconcile import reconcile_graph_diff
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


def _record(node_id: str, qualified: str, def_file: str) -> GraphNode:
    return GraphNode(
        id=node_id,
        kind="record_type",
        label=qualified,
        attrs={
            "qualified_name": qualified,
            "def_file": def_file,
            "visibility": "private_header",
        },
    )


def _public_struct(name: str, header: str) -> tuple[GraphNode, GraphNode, GraphEdge]:
    """A public struct declared in *header*, and the SOURCE_DECLARES edge to it."""
    node = GraphNode(
        id=f"type:{name}",
        kind="record_type",
        label=name,
        attrs={"qualified_name": name, "visibility": "public_header"},
    )
    header_node = GraphNode(id=f"hdr:{header}", kind="header", label=header)
    decl = GraphEdge(src=header_node.id, dst=node.id, kind="SOURCE_DECLARES")
    return node, header_node, decl


def _field(parent: str, target: str) -> GraphEdge:
    return GraphEdge(
        src=f"type:{parent}",
        dst=f"type:{target}",
        kind="TYPE_HAS_FIELD_TYPE",
        attrs={"role": "field"},
    )


def _edge_to(parent: str, target_id: str) -> GraphEdge:
    """A TYPE_HAS_FIELD_TYPE edge to a node whose id is already spelled out."""
    return GraphEdge(
        src=f"type:{parent}",
        dst=target_id,
        kind="TYPE_HAS_FIELD_TYPE",
        attrs={"role": "field"},
    )


def _kinds(changes: list) -> list[ChangeKind]:
    return [c.kind for c in changes]


def _rename_fixture(new_target: str) -> tuple[SourceGraphSummary, SourceGraphSummary]:
    """demo::Config's private field target RawConfig, renamed to *new_target*."""
    parent, hdr, decl = _public_struct("demo::Config", "include/demo/config.h")
    old_t = _record(
        "type:demo::detail::RawConfig", "demo::detail::RawConfig", "detail.h"
    )
    new_t = _record(f"type:{new_target}", new_target, "detail.h")
    old = _graph(
        [parent, hdr, old_t], [decl, _field("demo::Config", "demo::detail::RawConfig")]
    )
    new = _graph([parent, hdr, new_t], [decl, _field("demo::Config", new_target)])
    return old, new


def test_renamed_public_dependency_target_is_one_finding() -> None:
    old, new = _rename_fixture("demo::detail::RawConfigV2")
    findings = diff_source_graph_findings(old, new)
    kinds = _kinds(findings)
    assert kinds.count(ChangeKind.PUBLIC_API_INTERNAL_TYPE_RENAMED) == 1
    # The add+remove pair is gone: no ADDED finding for the renamed target, and the
    # declaration-level rename for the same pair is not reported a second time.
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED not in kinds
    assert ChangeKind.DECLARATION_RENAMED not in kinds
    [renamed] = [
        c for c in findings if c.kind is ChangeKind.PUBLIC_API_INTERNAL_TYPE_RENAMED
    ]
    assert renamed.symbol == "demo::Config"
    assert renamed.old_value == "demo::detail::RawConfig"
    assert renamed.new_value == "demo::detail::RawConfigV2"
    # Record before disposing: the match evidence stays visible in the description.
    assert "match evidence: structural_context" in renamed.description


def test_unrelated_add_and_remove_stay_two_observations() -> None:
    # Config drops its RawConfig field; a different public struct, Other, gains an
    # unrelated field instead. The two sit at different structural positions, so no
    # rename is reconciled: the add stays an ADDED finding, and the removal is still
    # recorded by the reconciliation as a true removal.
    parent, hdr, decl = _public_struct("demo::Config", "include/demo/config.h")
    other, other_hdr, other_decl = _public_struct("demo::Other", "include/demo/other.h")
    old_t = _record(
        "type:demo::detail::RawConfig", "demo::detail::RawConfig", "detail.h"
    )
    new_t = _record(
        "type:demo::detail::Unrelated", "demo::detail::Unrelated", "other.h"
    )
    old = _graph(
        [parent, hdr, old_t],
        [decl, _field("demo::Config", "demo::detail::RawConfig")],
    )
    new = _graph(
        [parent, hdr, other, other_hdr, new_t],
        [decl, other_decl, _field("demo::Other", "demo::detail::Unrelated")],
    )
    findings = diff_source_graph_findings(old, new)
    kinds = _kinds(findings)
    assert ChangeKind.PUBLIC_API_INTERNAL_TYPE_RENAMED not in kinds
    assert ChangeKind.DECLARATION_RENAMED not in kinds
    [added] = [
        c for c in findings if c.kind is ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED
    ]
    assert "demo::detail::Unrelated" in added.description
    reconciliation = reconcile_graph_diff(old, new)
    assert [n.id for n in reconciliation.true_removed] == [old_t.id]


def test_moved_target_without_rename_adds_no_kind_and_names_the_move() -> None:
    # Same qualified name, different declaring file, distinct node ids: reconciliation
    # resolves a move, not a rename. The ADDED finding stays and says it was a move.
    parent, hdr, decl = _public_struct("demo::Config", "include/demo/config.h")
    old_t = _record(
        "type:demo::detail::RawConfig#v1", "demo::detail::RawConfig", "detail_v1.h"
    )
    new_t = _record(
        "type:demo::detail::RawConfig#v2", "demo::detail::RawConfig", "detail_v2.h"
    )
    old = _graph([parent, hdr, old_t], [decl, _edge_to("demo::Config", old_t.id)])
    new = _graph([parent, hdr, new_t], [decl, _edge_to("demo::Config", new_t.id)])
    findings = diff_source_graph_findings(old, new)
    kinds = _kinds(findings)
    assert ChangeKind.PUBLIC_API_INTERNAL_TYPE_RENAMED not in kinds
    [added] = [
        c for c in findings if c.kind is ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED
    ]
    assert "Reconciled as a move" in added.description


def test_ambiguous_candidates_fall_back_to_todays_findings() -> None:
    # Two private targets of the same public struct are renamed at once. Structural
    # context cannot say which old name maps to which new one, so reconciliation
    # produces no pair. Nothing is fabricated: both stay ADDED, with no rename.
    parent, hdr, decl = _public_struct("demo::Config2", "include/demo/config2.h")
    old_a = _record("type:demo::detail::A", "demo::detail::A", "detail.h")
    old_b = _record("type:demo::detail::B", "demo::detail::B", "detail.h")
    new_a = _record("type:demo::detail::A2", "demo::detail::A2", "detail.h")
    new_b = _record("type:demo::detail::B2", "demo::detail::B2", "detail.h")
    old = _graph(
        [parent, hdr, old_a, old_b],
        [
            decl,
            _edge_to("demo::Config2", old_a.id),
            _edge_to("demo::Config2", old_b.id),
        ],
    )
    new = _graph(
        [parent, hdr, new_a, new_b],
        [
            decl,
            _edge_to("demo::Config2", new_a.id),
            _edge_to("demo::Config2", new_b.id),
        ],
    )
    kinds = _kinds(diff_source_graph_findings(old, new))
    assert ChangeKind.PUBLIC_API_INTERNAL_TYPE_RENAMED not in kinds
    assert kinds.count(ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED) == 1


def test_consumption_requires_the_old_side_reached_from_the_same_entry() -> None:
    # A reconciled rename is consumed only for an entry that already reached its old side.
    # Entries that did not are untouched, so nothing is reported as renamed for them.
    rename = SimpleNamespace(old_node=SimpleNamespace(id="type:old"))
    renamed_by_new = {"type:new": rename}
    reached_old = {"demo::Config": frozenset({"type:old"})}
    assert renamed_dependency_targets(
        {("demo::Config", "type:new")}, reached_old, renamed_by_new
    ) == {("demo::Config", "type:new"): rename}
    assert (
        renamed_dependency_targets(
            {("demo::Other", "type:new")}, reached_old, renamed_by_new
        )
        == {}
    )
