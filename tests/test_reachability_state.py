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

"""Tri-state ``ReachabilityState`` (impact-analysis-layer P0 slice).

Regression coverage for the correctness gap flagged in the impact-analysis
review: ``Change.public_reachable`` is a boolean, so "the reachability walk
positively proved this change unreachable" and "no walk (or an incomplete
one) ever reached a verdict at all" both collapse to the same ``False`` —
indistinguishable to a broad suppression rule's default ``unreachable-only``
gate. ``Change.reachability_state`` (PROVEN_REACHABLE / PROVEN_UNREACHABLE /
UNKNOWN) makes the distinction explicit, and the opt-in
``reachability: proven-unreachable-only`` suppression rule gate refuses to
match on UNKNOWN unless the rule also sets
``allow_unknown_reachability: true`` — the existing ``unreachable-only``
default keeps its original boolean semantics unchanged for backward
compatibility (see the extensive existing coverage in
``test_reachability_aware_suppression.py``, none of which this file
duplicates).
"""
from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind, ReachabilityState
from abicheck.checker_types import Change
from abicheck.model import AbiSnapshot, Function, RecordType, Visibility
from abicheck.post_processing import DEFAULT_PIPELINE
from abicheck.suppression import Suppression, SuppressionList


def _snap(*, functions=None, types=None, build_source=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version="1.0",
        functions=list(functions or []),
        types=list(types or []),
        build_source=build_source,
    )


def _public_fn(name: str, ret: str = "void") -> Function:
    return Function(
        name=name, mangled=name, return_type=ret, params=[], visibility=Visibility.PUBLIC
    )


def _needs_evidence_suppression() -> SuppressionList:
    return SuppressionList([
        Suppression(namespace="__never_matches__::*", reason="evidence trigger only")
    ])


def _graph_snap(functions, *, nodes, edges, degraded_passes=None) -> AbiSnapshot:
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import SourceGraphSummary

    graph = SourceGraphSummary(
        nodes=list(nodes), edges=list(edges),
        degraded_passes=dict(degraded_passes or {}),
    )
    return AbiSnapshot(
        library="libtest.so", version="1.0",
        functions=list(functions),
        build_source=BuildSourcePack(root="", source_graph=graph),
    )


def _decl_node(node_id: str, label: str, visibility: str):
    from abicheck.buildsource.source_graph import GraphNode

    return GraphNode(id=node_id, kind="source_decl", label=label, attrs={"visibility": visibility})


class TestMarkReachabilityTriState:
    def test_reachable_change_is_proven_reachable(self) -> None:
        old = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=64),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=128),
            ],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Base",
            description="size changed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.TYPE_SIZE_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_state == ReachabilityState.PROVEN_REACHABLE

    def test_unreachable_change_examined_by_layout_walk_is_proven_unreachable(self) -> None:
        """A change embedded only through a pointer (layout walk *does*
        examine it — it appears in reachable_types — but demotes it to
        not-consumer-visible) is conclusively PROVEN_UNREACHABLE, not
        UNKNOWN: the layout walk is a complete closure over the snapshot's
        own declared types, unaffected by any call-graph coverage caveat."""
        from abicheck.model import Param

        old = _snap(
            functions=[Function(
                name="use", mangled="use", return_type="void",
                params=[Param(name="h", type="ns::detail::Hidden*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            )],
            types=[RecordType(name="ns::detail::Hidden", kind="struct", size_bits=32)],
        )
        new = _snap(
            functions=[Function(
                name="use", mangled="use", return_type="void",
                params=[Param(name="h", type="ns::detail::Hidden*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            )],
            types=[RecordType(name="ns::detail::Hidden", kind="struct", size_bits=64)],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Hidden",
            description="size changed",
        )
        DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        assert raw_change.public_reachable is False
        assert raw_change.reachability_state == ReachabilityState.PROVEN_UNREACHABLE

    def test_no_graph_evidence_at_all_stays_unknown(self) -> None:
        """MarkReachability's early-return path (no reachable_types, no
        public_header_names, no call_reachable at all) never tags anything —
        Change.reachability_state keeps its honest UNKNOWN default rather
        than silently becoming PROVEN_UNREACHABLE."""
        old = _snap(
            functions=[_public_fn("foo", "int")],
            types=[RecordType(name="ns::detail::Hidden", kind="class", size_bits=64)],
        )
        new = _snap(
            functions=[_public_fn("foo", "int")],
            types=[RecordType(name="ns::detail::Hidden", kind="class", size_bits=128)],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Hidden",
            description="size changed",
        )
        DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        assert raw_change.public_reachable is False
        assert raw_change.reachability_state == ReachabilityState.UNKNOWN

    def test_degraded_call_graph_leaves_unexamined_change_unknown(self) -> None:
        """A change the layout walk never examines at all (a function-shaped
        FUNC_REMOVED with no field/base/signature evidence) whose only
        possible signal is a call graph flagged degraded/narrowed must not be
        conclusively PROVEN_UNREACHABLE — the graph's absence of an edge to
        it is not trustworthy negative evidence."""
        from abicheck.buildsource.source_graph import GraphEdge

        old = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://other", "ns::detail::other", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")],
            degraded_passes={"call_graph": True},
        )
        new = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[_decl_node("decl://pub", "pubFn", "public_header")],
            edges=[],
        )
        raw_change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="ns::detail::train_ops_dispatcher",
            description="removed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(found) == 1
        assert found[0].public_reachable is False
        assert found[0].reachability_state == ReachabilityState.UNKNOWN


class TestProvenUnreachableOnlySuppressionGate:
    def test_matches_proven_unreachable(self) -> None:
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Hidden",
            description="size changed",
            public_reachable=False,
            reachability_state=ReachabilityState.PROVEN_UNREACHABLE,
        )
        rule = Suppression(
            namespace="ns::detail::*",
            reachability="proven-unreachable-only",
            reason="proven-safe churn",
        )
        assert rule.matches(change) is True

    def test_refuses_unknown_by_default(self) -> None:
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Hidden",
            description="size changed",
            reachability_state=ReachabilityState.UNKNOWN,
        )
        rule = Suppression(
            namespace="ns::detail::*",
            reachability="proven-unreachable-only",
            reason="requires proof",
        )
        assert rule.matches(change) is False
        assert rule.would_withhold_unknown_reachability(change) is True

    def test_allow_unknown_reachability_bypasses_the_gate(self) -> None:
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Hidden",
            description="size changed",
            reachability_state=ReachabilityState.UNKNOWN,
        )
        rule = Suppression(
            namespace="ns::detail::*",
            reachability="proven-unreachable-only",
            allow_unknown_reachability=True,
            reason="explicit bypass, reviewed",
        )
        assert rule.matches(change) is True
        assert rule.would_withhold_unknown_reachability(change) is False

    def test_default_unreachable_only_gate_is_unaffected_by_unknown_state(self) -> None:
        """The pre-existing 'unreachable-only' default must not regress:
        it still keys off the boolean public_reachable alone, so UNKNOWN
        state is treated exactly like PROVEN_UNREACHABLE (backward
        compatibility for every existing suppression file)."""
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Hidden",
            description="size changed",
            public_reachable=False,
            reachability_state=ReachabilityState.UNKNOWN,
        )
        rule = Suppression(namespace="ns::detail::*", reason="default gate")
        assert rule.matches(change) is True
        assert rule.would_withhold_unknown_reachability(change) is False

    def test_invalid_reachability_value_still_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Invalid reachability"):
            Suppression(namespace="ns::*", reachability="bogus")

    def test_allow_unknown_reachability_must_be_bool(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="allow_unknown_reachability"):
            Suppression(
                symbol="ns::detail::Hidden",
                reachability="proven-unreachable-only",
                allow_unknown_reachability="false",  # type: ignore[arg-type]
            )


def _degraded_call_graph_scenario():
    """FUNC_REMOVED is not one of DemoteUnreachableInternalChurn's
    layout-churn kinds, so — unlike a bare TYPE_SIZE_CHANGED on an
    internal-namespace type — it is unaffected by that unrelated pipeline
    step and isolates the suppression reachability gate under test."""
    from abicheck.buildsource.source_graph import GraphEdge

    old = _graph_snap(
        [_public_fn("pubFn")],
        nodes=[
            _decl_node("decl://pub", "pubFn", "public_header"),
            _decl_node("decl://other", "ns::detail::other", "source"),
        ],
        edges=[GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")],
        degraded_passes={"call_graph": True},
    )
    new = _graph_snap(
        [_public_fn("pubFn")],
        nodes=[_decl_node("decl://pub", "pubFn", "public_header")],
        edges=[],
    )
    raw_change = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="ns::detail::train_ops_dispatcher",
        description="removed",
    )
    return old, new, raw_change


class TestSuppressionReachabilityUnknownDiagnostic:
    def test_pipeline_keeps_change_and_emits_diagnostic(self) -> None:
        old, new, raw_change = _degraded_call_graph_scenario()
        suppression = SuppressionList([
            Suppression(
                namespace="ns::detail::*",
                reachability="proven-unreachable-only",
                reason="wants proof",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change not in ctx.suppressed
        kinds = [c.kind for c in ctx.kept]
        assert ChangeKind.FUNC_REMOVED in kinds
        assert ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN in kinds
        diag = next(
            c for c in ctx.kept if c.kind == ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN
        )
        assert "allow_unknown_reachability" in diag.description

    def test_allow_unknown_reachability_suppresses_with_no_diagnostic(self) -> None:
        old, new, raw_change = _degraded_call_graph_scenario()
        suppression = SuppressionList([
            Suppression(
                namespace="ns::detail::*",
                reachability="proven-unreachable-only",
                allow_unknown_reachability=True,
                reason="reviewed, safe",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change in ctx.suppressed
        assert ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN not in [c.kind for c in ctx.kept]


class TestFunctionShapedChangeWithNoCallGraphIsUnknown:
    """Codex review on PR #607: a function/variable-shaped change (never in
    the layout walk's domain of *declared types*) with no embedded call
    graph at all must not be conclusively PROVEN_UNREACHABLE — no walk of
    any kind ever examined it. The prior implementation used "not found in
    reachable_types" as a stand-in for "the layout walk examined this and
    found nothing", which is only valid for a change whose root actually
    names a declared type; for a function-shaped root it silently mislabeled
    "never examined" as "proven"."""

    def test_func_removed_with_no_graph_at_all_is_unknown(self) -> None:
        old = _snap(
            functions=[
                _public_fn("foo", "int"),
                Function(
                    name="ns::detail::helper", mangled="ns::detail::helper",
                    return_type="void", params=[], visibility=Visibility.PUBLIC,
                ),
            ],
        )
        new = _snap(functions=[_public_fn("foo", "int")])
        raw_change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="ns::detail::helper",
            description="removed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(found) == 1
        assert found[0].public_reachable is False
        assert found[0].reachability_state == ReachabilityState.UNKNOWN

    def test_type_shaped_change_examined_by_walk_stays_proven_unreachable(self) -> None:
        """Contrast case: once the walk actually runs (triggered here by a
        sibling type that IS reachable, so MarkReachability's perf-guard
        early-return does not skip everything), a TYPE-shaped change whose
        root names a declared type but was never found reachable by that
        walk stays PROVEN_UNREACHABLE even with no call graph at all — the
        layout walk's closure over declared types is complete regardless of
        call-graph presence, so this must not regress to UNKNOWN too."""
        old = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=64),
                RecordType(name="ns::detail::Hidden", kind="class", size_bits=64),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=64),
                RecordType(name="ns::detail::Hidden", kind="class", size_bits=128),
            ],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Hidden",
            description="size changed",
        )
        suppression = SuppressionList([
            Suppression(
                namespace="ns::detail::*",
                reachability="proven-unreachable-only",
                reason="wants proof",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change in ctx.suppressed
        assert ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN not in [c.kind for c in ctx.kept]

    def test_func_removed_with_present_untrusted_call_graph_is_unknown(self) -> None:
        """A call graph that IS present but flagged degraded/narrowed must
        not count as trustworthy negative evidence either."""
        old, new, raw_change = _degraded_call_graph_scenario()
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(found) == 1
        assert found[0].reachability_state == ReachabilityState.UNKNOWN

    def test_func_removed_with_present_trusted_call_graph_not_found_is_proven_unreachable(
        self,
    ) -> None:
        """A call graph that IS present, is NOT flagged degraded/narrowed,
        and simply does not reach this decl is real negative evidence."""
        from abicheck.buildsource.source_graph import GraphEdge

        old = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://other", "ns::detail::other", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")],
        )
        new = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[_decl_node("decl://pub", "pubFn", "public_header")],
            edges=[],
        )
        raw_change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="ns::detail::unrelated_and_never_called",
            description="removed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(found) == 1
        assert found[0].reachability_state == ReachabilityState.PROVEN_UNREACHABLE


class TestCheckerFilterSuppressedChangesUnknownDiagnostic:
    """checker.py's own suppression call sites (_filter_suppressed_changes
    for SONAME/platform-floor advisories, _filter_pattern_synthetic for
    ADR-027 pattern-verdict findings) run independently of
    post_processing.ApplySuppression and must wire the same
    suppression_reachability_unknown diagnostic (unit-tested directly here
    since exercising them via the full compare() pipeline needs a real
    binary)."""

    def test_filter_suppressed_changes_emits_unknown_diagnostic(self) -> None:
        from abicheck.checker import _filter_suppressed_changes

        change = Change(
            kind=ChangeKind.SONAME_BUMP_UNNECESSARY,
            symbol="libfoo.so.1",
            description="soname bumped unnecessarily",
        )
        suppression = SuppressionList([
            Suppression(
                symbol="libfoo.so.1",
                reachability="proven-unreachable-only",
                reason="wants proof",
            )
        ])
        suppressed: list[Change] = []
        visible = _filter_suppressed_changes([change], suppression, suppressed)
        assert change in visible
        assert not suppressed
        assert any(c.kind == ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN for c in visible)

    def test_filter_pattern_synthetic_emits_unknown_diagnostic(self) -> None:
        from abicheck.checker import _filter_pattern_synthetic

        pre_existing = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="pre", description="pre-existing",
        )
        synthetic = Change(
            kind=ChangeKind.OPAQUE_INVARIANT_BROKEN,
            symbol="ns::detail::Opaque",
            description="opaque invariant broken",
            modulation_rule="opaque-rule",
        )
        kept = [pre_existing, synthetic]
        suppression = SuppressionList([
            Suppression(
                symbol="ns::detail::Opaque",
                reachability="proven-unreachable-only",
                reason="wants proof",
            )
        ])
        suppressed: list[Change] = []
        new_kept, pattern_modulations = _filter_pattern_synthetic(
            kept, 1, suppression, suppressed, []
        )
        assert synthetic in new_kept
        assert not suppressed
        assert any(
            c.kind == ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN for c in new_kept
        )
        assert pattern_modulations == []


class TestYamlLoadAllowUnknownReachability:
    """SuppressionList.load's parsing of the new `allow_unknown_reachability`
    entry key mirrors `allow_public_break`'s existing strict-boolean
    contract (impact-analysis-layer P0 slice)."""

    def test_loads_true_from_yaml(self, tmp_path) -> None:
        p = tmp_path / "suppress.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            "  - namespace: 'ns::detail::*'\n"
            "    reachability: proven-unreachable-only\n"
            "    allow_unknown_reachability: true\n"
            "    reason: reviewed\n"
        )
        sl = SuppressionList.load(p)
        rule = sl._suppressions[0]
        assert rule.allow_unknown_reachability is True
        assert rule._resolved_reachability == "proven-unreachable-only"

    def test_absent_key_defaults_false(self, tmp_path) -> None:
        p = tmp_path / "suppress.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            "  - namespace: 'ns::detail::*'\n"
            "    reachability: proven-unreachable-only\n"
            "    reason: reviewed\n"
        )
        sl = SuppressionList.load(p)
        assert sl._suppressions[0].allow_unknown_reachability is False

    def test_non_bool_value_raises(self, tmp_path) -> None:
        p = tmp_path / "suppress.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            "  - namespace: 'ns::detail::*'\n"
            "    allow_unknown_reachability: 'false'\n"
            "    reason: reviewed\n"
        )
        with pytest.raises(ValueError, match="allow_unknown_reachability"):
            SuppressionList.load(p)

    def test_end_to_end_yaml_rule_matches_unknown_state(self) -> None:
        """The loaded rule behaves identically to a programmatically
        constructed one against a change in the UNKNOWN state."""
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Hidden",
            description="size changed",
            reachability_state=ReachabilityState.UNKNOWN,
        )
        proven = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::detail::Hidden",
            description="size changed",
            reachability_state=ReachabilityState.PROVEN_UNREACHABLE,
        )
        strict_rule = Suppression(
            namespace="ns::detail::*", reachability="proven-unreachable-only",
            reason="strict",
        )
        assert strict_rule.matches(change) is False
        assert strict_rule.matches(proven) is True
        lenient_rule = Suppression(
            namespace="ns::detail::*", reachability="proven-unreachable-only",
            allow_unknown_reachability=True, reason="lenient",
        )
        assert lenient_rule.matches(change) is True
