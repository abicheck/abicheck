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

"""Tests for safe old/new graph reconciliation (G31 Phase B B2, ADR-048).

Covers exact canonical-id match, exact alias match, ambiguous-no-match, true
add/remove, and — critically — the authority-rule regression proving graph
reconciliation never deletes or downgrades an artifact-proven finding.
"""

from __future__ import annotations

from abicheck.buildsource.graph_reconcile import (
    OUTCOME_MOVED,
    OUTCOME_RECONCILED,
    OUTCOME_RENAMED,
    diff_graph_reconciliation_findings,
    reconcile_added_removed,
    reconcile_graph_diff,
)
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.checker_policy import ChangeKind, Verdict


def _graph(nodes: list[GraphNode], edges: list[GraphEdge]) -> SourceGraphSummary:
    g = SourceGraphSummary()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    return g.finalize()


def test_exact_canonical_id_match_via_mangled_alias() -> None:
    # Same mangled name on both sides -> reconciles via canonical id, even
    # though the node ids themselves differ (simulating two independently
    # id-hashed producers).
    old_node = GraphNode(
        id="decl://old_id_1",
        kind="source_decl",
        label="ns::foo",
        attrs={"qualified_name": "ns::foo", "mangled_name": "_ZN2ns3fooEi"},
    )
    new_node = GraphNode(
        id="decl://new_id_1",
        kind="source_decl",
        label="ns::foo",
        attrs={"qualified_name": "ns::foo", "mangled_name": "_ZN2ns3fooEi"},
    )
    old_g = _graph([old_node], [])
    new_g = _graph([new_node], [])
    result = reconcile_added_removed([old_node], [new_node], old_g, new_g)
    assert len(result.reconciled) == 1
    pair = result.reconciled[0]
    assert pair.match_kind == "canonical_id"
    assert not result.true_added
    assert not result.true_removed


def test_exact_alias_match_unambiguous_qualified_name() -> None:
    old_node = GraphNode(
        id="type://old",
        kind="record_type",
        label="ns::Widget",
        attrs={"qualified_name": "ns::Widget"},
    )
    new_node = GraphNode(
        id="type://new",
        kind="record_type",
        label="ns::Widget",
        attrs={"qualified_name": "ns::Widget"},
    )
    old_g = _graph([old_node], [])
    new_g = _graph([new_node], [])
    result = reconcile_added_removed([old_node], [new_node], old_g, new_g)
    assert len(result.reconciled) == 1
    assert result.reconciled[0].match_kind == "alias"


def test_rename_reconciles_via_structural_context() -> None:
    # A public struct's private field-type target is renamed; same file, so
    # this classifies as OUTCOME_RENAMED, matched via structural context
    # since the qualified name (and thus every B1 alias) changed.
    parent = GraphNode(
        id="type://demo::Config", kind="record_type", label="demo::Config"
    )
    old_internal = GraphNode(
        id="type://demo::detail::RawConfig",
        kind="record_type",
        label="demo::detail::RawConfig",
        attrs={"qualified_name": "demo::detail::RawConfig", "def_file": "detail.h"},
    )
    new_internal = GraphNode(
        id="type://demo::detail::RawConfigV2",
        kind="record_type",
        label="demo::detail::RawConfigV2",
        attrs={"qualified_name": "demo::detail::RawConfigV2", "def_file": "detail.h"},
    )
    old_edge = GraphEdge(
        src=parent.id,
        dst=old_internal.id,
        kind="TYPE_HAS_FIELD_TYPE",
        attrs={"role": "field"},
    )
    new_edge = GraphEdge(
        src=parent.id,
        dst=new_internal.id,
        kind="TYPE_HAS_FIELD_TYPE",
        attrs={"role": "field"},
    )
    old_g = _graph([parent, old_internal], [old_edge])
    new_g = _graph([parent, new_internal], [new_edge])
    result = reconcile_added_removed([old_internal], [new_internal], old_g, new_g)
    assert len(result.reconciled) == 1
    pair = result.reconciled[0]
    assert pair.match_kind == "structural_context"
    assert pair.outcome == OUTCOME_RENAMED


def test_structural_context_uses_neighbor_kind_not_raw_id() -> None:
    """Codex review: when old/new header graphs are collected from separate
    checkout roots, a declaring-parent neighbor (e.g. a header node) gets a
    different raw node id on each side even though nothing about it actually
    changed (same kind, same role). Keying the structural-context tuple on
    the neighbor's raw id -- instead of its kind, as the function's own
    docstring says it should -- would make an otherwise-unique rename
    compare as a different context and silently fail to reconcile."""
    old_parent = GraphNode(
        id="header:///tmp/checkout_old/include/api.h",
        kind="header",
        label="api.h",
    )
    new_parent = GraphNode(
        id="header:///tmp/checkout_new/include/api.h",
        kind="header",
        label="api.h",
    )
    old_internal = GraphNode(
        id="type://demo::detail::RawConfig",
        kind="record_type",
        label="demo::detail::RawConfig",
        attrs={"qualified_name": "demo::detail::RawConfig", "def_file": "detail.h"},
    )
    new_internal = GraphNode(
        id="type://demo::detail::RawConfigV2",
        kind="record_type",
        label="demo::detail::RawConfigV2",
        attrs={"qualified_name": "demo::detail::RawConfigV2", "def_file": "detail.h"},
    )
    old_edge = GraphEdge(
        src=old_parent.id,
        dst=old_internal.id,
        kind="SOURCE_DECLARES",
        attrs={"role": "declares"},
    )
    new_edge = GraphEdge(
        src=new_parent.id,
        dst=new_internal.id,
        kind="SOURCE_DECLARES",
        attrs={"role": "declares"},
    )
    old_g = _graph([old_parent, old_internal], [old_edge])
    new_g = _graph([new_parent, new_internal], [new_edge])
    result = reconcile_added_removed([old_internal], [new_internal], old_g, new_g)
    assert len(result.reconciled) == 1
    pair = result.reconciled[0]
    assert pair.match_kind == "structural_context"
    assert pair.outcome == OUTCOME_RENAMED


def test_move_reconciles_when_file_changes_but_name_does_not() -> None:
    old_node = GraphNode(
        id="type://old",
        kind="record_type",
        label="ns::Widget",
        attrs={"qualified_name": "ns::Widget", "def_file": "a.h"},
    )
    new_node = GraphNode(
        id="type://new",
        kind="record_type",
        label="ns::Widget",
        attrs={"qualified_name": "ns::Widget", "def_file": "b.h"},
    )
    old_g = _graph([old_node], [])
    new_g = _graph([new_node], [])
    result = reconcile_added_removed([old_node], [new_node], old_g, new_g)
    assert len(result.reconciled) == 1
    assert result.reconciled[0].outcome == OUTCOME_MOVED


def test_different_checkout_roots_not_misclassified_as_moved() -> None:
    """Codex review: old/new graphs collected from two independently-rooted
    checkouts (separate temp dirs, or separate CI job workspaces) never share
    an absolute root. Comparing raw def_file prefixes would classify a node
    whose declaring file didn't actually change project-relatively as
    "moved" just because the checkout root differs -- root-relative
    normalization must strip that before classifying."""
    old_node = GraphNode(
        id="type://old",
        kind="record_type",
        label="ns::Widget",
        attrs={
            "qualified_name": "ns::Widget",
            "def_file": "/tmp/checkout_old/include/api.h",
        },
    )
    new_node = GraphNode(
        id="type://new",
        kind="record_type",
        label="ns::Widget",
        attrs={
            "qualified_name": "ns::Widget",
            "def_file": "/tmp/checkout_new/include/api.h",
        },
    )
    old_g = _graph([old_node], [])
    new_g = _graph([new_node], [])
    result = reconcile_added_removed([old_node], [new_node], old_g, new_g)
    assert len(result.reconciled) == 1
    # Same qualified name, same project-relative file -- neither renamed nor
    # moved once the checkout root is stripped.
    assert result.reconciled[0].outcome == OUTCOME_RECONCILED


def test_real_move_still_detected_across_different_checkout_roots() -> None:
    """The root-relative normalization must not mask a genuine move -- only
    strip the checkout-root prefix, not the real project-relative path."""
    old_node = GraphNode(
        id="type://old",
        kind="record_type",
        label="ns::Widget",
        attrs={
            "qualified_name": "ns::Widget",
            "def_file": "/tmp/checkout_old/include/a.h",
        },
    )
    new_node = GraphNode(
        id="type://new",
        kind="record_type",
        label="ns::Widget",
        attrs={
            "qualified_name": "ns::Widget",
            "def_file": "/tmp/checkout_new/include/b.h",
        },
    )
    old_g = _graph([old_node], [])
    new_g = _graph([new_node], [])
    result = reconcile_added_removed([old_node], [new_node], old_g, new_g)
    assert len(result.reconciled) == 1
    assert result.reconciled[0].outcome == OUTCOME_MOVED


def test_multi_file_common_root_stripped_preserves_real_subdirectory_move() -> None:
    """With more than one declaring file on each side, the checkout-root
    normalization must derive the actual shared root from all of them (not
    fall back to reserving just a basename), so a real move to a different
    project subdirectory is still detected -- not masked by, and not
    confused with, the checkout-root difference."""
    old_stable = GraphNode(
        id="type://old_stable",
        kind="record_type",
        label="ns::Stable",
        attrs={
            "qualified_name": "ns::Stable",
            "def_file": "/tmp/checkout_old/project/include/stable.h",
        },
    )
    old_moved = GraphNode(
        id="type://old_moved",
        kind="record_type",
        label="ns::Moved",
        attrs={
            "qualified_name": "ns::Moved",
            "def_file": "/tmp/checkout_old/project/include/detail/moved.h",
        },
    )
    new_stable = GraphNode(
        id="type://new_stable",
        kind="record_type",
        label="ns::Stable",
        attrs={
            "qualified_name": "ns::Stable",
            "def_file": "/tmp/checkout_new/project/include/stable.h",
        },
    )
    new_moved = GraphNode(
        id="type://new_moved",
        kind="record_type",
        label="ns::Moved",
        attrs={
            "qualified_name": "ns::Moved",
            "def_file": "/tmp/checkout_new/project/include/public/moved.h",
        },
    )
    old_g = _graph([old_stable, old_moved], [])
    new_g = _graph([new_stable, new_moved], [])
    result = reconcile_added_removed(
        [old_stable, old_moved], [new_stable, new_moved], old_g, new_g
    )
    outcomes = {p.old_node.id: p.outcome for p in result.reconciled}
    assert len(result.reconciled) == 2
    # Same project-relative file across checkout roots -- not moved.
    assert outcomes["type://old_stable"] == OUTCOME_RECONCILED
    # Genuinely moved to a different project subdirectory (detail/ -> public/).
    assert outcomes["type://old_moved"] == OUTCOME_MOVED


def test_ambiguous_rename_does_not_reconcile() -> None:
    """Two sibling internal field-type targets of the SAME parent, both
    renamed at once: neither alias nor structural context can disambiguate
    which old name maps to which new one (identical position: sole
    TYPE_HAS_FIELD_TYPE:field target... except there are two, so the
    "position" collapses to the same tuple set for both). Must NOT reconcile
    -- correctly stays a true add + true remove pair on each side.
    """
    parent = GraphNode(
        id="type://demo::Config", kind="record_type", label="demo::Config"
    )
    old_a = GraphNode(
        id="type://demo::detail::RawA",
        kind="record_type",
        label="demo::detail::RawA",
        attrs={"qualified_name": "demo::detail::RawA", "def_file": "detail.h"},
    )
    old_b = GraphNode(
        id="type://demo::detail::RawB",
        kind="record_type",
        label="demo::detail::RawB",
        attrs={"qualified_name": "demo::detail::RawB", "def_file": "detail.h"},
    )
    new_x = GraphNode(
        id="type://demo::detail::RawX",
        kind="record_type",
        label="demo::detail::RawX",
        attrs={"qualified_name": "demo::detail::RawX", "def_file": "detail.h"},
    )
    new_y = GraphNode(
        id="type://demo::detail::RawY",
        kind="record_type",
        label="demo::detail::RawY",
        attrs={"qualified_name": "demo::detail::RawY", "def_file": "detail.h"},
    )
    edge_kwargs = {"kind": "TYPE_HAS_FIELD_TYPE", "attrs": {"role": "field"}}
    old_g = _graph(
        [parent, old_a, old_b],
        [
            GraphEdge(src=parent.id, dst=old_a.id, **edge_kwargs),
            GraphEdge(src=parent.id, dst=old_b.id, **edge_kwargs),
        ],
    )
    new_g = _graph(
        [parent, new_x, new_y],
        [
            GraphEdge(src=parent.id, dst=new_x.id, **edge_kwargs),
            GraphEdge(src=parent.id, dst=new_y.id, **edge_kwargs),
        ],
    )
    result = reconcile_added_removed([old_a, old_b], [new_x, new_y], old_g, new_g)
    assert result.reconciled == []
    assert {n.id for n in result.true_removed} | {
        n.id for n in result.ambiguous_old
    } == {
        old_a.id,
        old_b.id,
    }
    assert {n.id for n in result.true_added} | {n.id for n in result.ambiguous_new} == {
        new_x.id,
        new_y.id,
    }


def test_true_add_and_true_remove_no_candidate_at_all() -> None:
    old_node = GraphNode(
        id="type://gone",
        kind="record_type",
        label="ns::Gone",
        attrs={"qualified_name": "ns::Gone"},
    )
    new_node = GraphNode(
        id="type://new",
        kind="record_type",
        label="ns::Brand::New::Thing",
        attrs={"qualified_name": "ns::Brand::New::Thing"},
    )
    old_g = _graph([old_node], [])
    new_g = _graph([new_node], [])
    result = reconcile_added_removed([old_node], [new_node], old_g, new_g)
    assert result.reconciled == []
    assert [n.id for n in result.true_removed] == [old_node.id]
    assert [n.id for n in result.true_added] == [new_node.id]


def test_reconcile_graph_diff_wraps_diff_source_graph() -> None:
    old_node = GraphNode(
        id="type://old",
        kind="record_type",
        label="ns::Widget",
        attrs={"qualified_name": "ns::Widget"},
    )
    new_node = GraphNode(
        id="type://new",
        kind="record_type",
        label="ns::Widget",
        attrs={"qualified_name": "ns::Widget"},
    )
    old_g = _graph([old_node], [])
    new_g = _graph([new_node], [])
    result = reconcile_graph_diff(old_g, new_g)
    assert len(result.reconciled) == 1


def test_diff_graph_reconciliation_findings_emits_each_change_kind() -> None:
    """Each of the three ChangeKinds is actually produced end-to-end from a
    real reconciled pair, not just referenced by name (mirrors
    tests/test_changekind_completeness.py's coverage requirement)."""
    rename_parent = GraphNode(id="type://parent", kind="record_type", label="ns::Parent")
    rename_old = GraphNode(
        id="type://old_rename",
        kind="record_type",
        label="ns::Old",
        attrs={"qualified_name": "ns::Old", "def_file": "a.h"},
    )
    rename_new = GraphNode(
        id="type://new_rename",
        kind="record_type",
        label="ns::New",
        attrs={"qualified_name": "ns::New", "def_file": "a.h"},
    )
    rename_edge_kwargs = {"kind": "TYPE_HAS_FIELD_TYPE", "attrs": {"role": "field"}}
    moved_old = GraphNode(
        id="type://old_moved",
        kind="record_type",
        label="ns::Same",
        attrs={"qualified_name": "ns::Same", "def_file": "a.h"},
    )
    moved_new = GraphNode(
        id="type://new_moved",
        kind="record_type",
        label="ns::Same",
        attrs={"qualified_name": "ns::Same", "def_file": "b.h"},
    )
    recon_old = GraphNode(
        id="type://old_recon",
        kind="enum_type",
        label="ns::E1",
        attrs={"mangled_name": "_Znotreal_recon_a", "qualified_name": "ns::E1"},
    )
    recon_new = GraphNode(
        id="type://new_recon",
        kind="enum_type",
        label="ns::E1",
        attrs={"mangled_name": "_Znotreal_recon_a", "qualified_name": "ns::E1"},
    )
    old_g = _graph(
        [rename_parent, rename_old, moved_old, recon_old],
        [GraphEdge(src=rename_parent.id, dst=rename_old.id, **rename_edge_kwargs)],
    )
    new_g = _graph(
        [rename_parent, rename_new, moved_new, recon_new],
        [GraphEdge(src=rename_parent.id, dst=rename_new.id, **rename_edge_kwargs)],
    )
    result = reconcile_added_removed(
        [rename_old, moved_old, recon_old],
        [rename_new, moved_new, recon_new],
        old_g,
        new_g,
    )
    findings = diff_graph_reconciliation_findings(result)
    kinds = {f.kind for f in findings}
    assert ChangeKind.DECLARATION_RENAMED in kinds
    assert ChangeKind.DECLARATION_MOVED in kinds
    # recon_old/recon_new share every alias (same mangled/qualified name,
    # same file/scope: both empty) -- classified OUTCOME_RECONCILED since
    # neither name nor file differs between the pair (a same-shape,
    # non-rename/non-move alias match, e.g. an attribute-only change a
    # future producer might reconcile on).
    assert ChangeKind.DECLARATION_IDENTITY_RECONCILED in kinds


def test_diff_graph_reconciliation_findings_emits_expected_kind() -> None:
    old_node = GraphNode(
        id="type://old",
        kind="record_type",
        label="ns::Widget",
        attrs={"qualified_name": "ns::Widget", "def_file": "a.h"},
    )
    new_node = GraphNode(
        id="type://new",
        kind="record_type",
        label="ns::WidgetV2",
        attrs={"qualified_name": "ns::WidgetV2", "def_file": "a.h"},
    )
    old_g = _graph([old_node], [])
    new_g = _graph([new_node], [])
    result = reconcile_graph_diff(old_g, new_g)
    # No alias/canonical overlap possible (qualified name differs and there's
    # no shared structural context computed here without edges), so this one
    # stays unreconciled by design -- demonstrates findings are only emitted
    # for actual matches, never fabricated.
    findings = diff_graph_reconciliation_findings(result)
    assert len(findings) == len(result.reconciled)
    for f in findings:
        assert f.kind in (
            ChangeKind.DECLARATION_RENAMED,
            ChangeKind.DECLARATION_MOVED,
            ChangeKind.DECLARATION_IDENTITY_RECONCILED,
        )


def test_reconciliation_never_deletes_or_downgrades_artifact_finding() -> None:
    """THE authority-rule regression test (ADR-028 D3 / ADR-031 D6 / ADR-048).

    Builds an old/new AbiSnapshot pair with a genuine artifact-proven
    func_removed finding, plus an independent, reconcilable graph rename in
    the same comparison (mirroring how ``diff_source_graph_findings``'s
    output rides into ``checker.compare`` as ``extra_changes`` in production
    — see ``cli_buildsource_helpers.py``: ``changes.extend(_gr)``). Asserts
    the func_removed finding survives, unmodified and still BREAKING, and
    the reconciliation finding is purely additive.
    """
    from abicheck.checker import compare
    from abicheck.model import AbiSnapshot, Function, Visibility

    old_snap = AbiSnapshot(
        library="test",
        version="1.0",
        functions=[
            Function(
                name="doomed",
                mangled="doomed",
                return_type="int",
                params=[],
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    new_snap = AbiSnapshot(library="test", version="2.0")

    # A rename in the L5 graph, independent of the artifact-level change
    # above -- exercises the exact production merge path (extra_changes).
    old_type = GraphNode(
        id="type://old",
        kind="record_type",
        label="ns::Widget",
        attrs={"qualified_name": "ns::Widget", "def_file": "a.h"},
    )
    new_type = GraphNode(
        id="type://new",
        kind="record_type",
        label="ns::Widget",
        attrs={"qualified_name": "ns::Widget", "def_file": "a.h"},
    )
    old_g = _graph([old_type], [])
    new_g = _graph([new_type], [])
    reconciliation = reconcile_graph_diff(old_g, new_g)
    graph_findings = diff_graph_reconciliation_findings(reconciliation)
    assert graph_findings, "fixture must actually exercise a reconciled pair"

    baseline = compare(old_snap, new_snap)
    baseline_func_removed = [
        c for c in baseline.changes if c.kind == ChangeKind.FUNC_REMOVED
    ]
    assert len(baseline_func_removed) == 1
    assert baseline.verdict == Verdict.BREAKING

    enriched = compare(old_snap, new_snap, extra_changes=graph_findings)
    enriched_func_removed = [
        c for c in enriched.changes if c.kind == ChangeKind.FUNC_REMOVED
    ]
    # The artifact-proven finding survives unchanged: same count, same
    # symbol, and it is still classified BREAKING under the active policy --
    # reconciliation evidence must never delete or downgrade it.
    assert len(enriched_func_removed) == 1
    assert enriched_func_removed[0].symbol == baseline_func_removed[0].symbol
    assert enriched.verdict == Verdict.BREAKING
    assert enriched_func_removed[0] in enriched.breaking

    # The reconciliation findings are purely additive: present, RISK-tier,
    # never overriding the BREAKING verdict.
    reconciled_in_result = [
        c
        for c in enriched.changes
        if c.kind
        in (
            ChangeKind.DECLARATION_RENAMED,
            ChangeKind.DECLARATION_MOVED,
            ChangeKind.DECLARATION_IDENTITY_RECONCILED,
        )
    ]
    assert len(reconciled_in_result) == len(graph_findings)
    assert len(enriched.changes) == len(baseline.changes) + len(graph_findings)
    for c in reconciled_in_result:
        assert c not in enriched.breaking
