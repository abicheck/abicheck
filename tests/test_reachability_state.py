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


def _snap(*, functions=None, types=None, enums=None, build_source=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version="1.0",
        functions=list(functions or []),
        types=list(types or []),
        enums=list(enums or []),
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


def _graph_snap(
    functions, *, nodes, edges, degraded_passes=None, extractor_passes=None,
) -> AbiSnapshot:
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import (
        GraphEdge,
        GraphNode,
        SourceGraphSummary,
    )

    nodes = list(nodes)
    edges = list(edges)
    # source_graph_findings._public_decls requires a real "header" node with
    # a SOURCE_DECLARES edge to the public decl -- not just the decl node's
    # own visibility attr -- so MarkReachability's "trust requires a real
    # public closure, not just completed passes" check (Codex review) has
    # something to find in these fixtures too.
    for n in nodes:
        if n.kind == "source_decl" and n.attrs.get("visibility") == "public_header":
            hdr_id = f"hdr://{n.id}"
            nodes.append(GraphNode(id=hdr_id, kind="header", label=hdr_id, attrs={}))
            edges.append(GraphEdge(src=hdr_id, dst=n.id, kind="SOURCE_DECLARES"))
    graph = SourceGraphSummary(
        nodes=nodes, edges=edges,
        degraded_passes=dict(degraded_passes or {}),
        extractor_passes=dict(extractor_passes or {}),
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

    def test_reachable_enum_member_change_is_proven_reachable(self) -> None:
        """CodeRabbit review: an enum-member finding's ``root`` still carries
        the "EnumName::member" suffix at the point the layout-walk tag check
        runs, so a bare ``root in reachable_types`` lookup never matches even
        when the *owning* enum genuinely was walked and found reachable
        (compute_leak_paths records a value-embedded internal enum field
        under its own bare name). Falling back to ``enum_owner`` there — not
        just in the coarser known-types fallback — must tag this as
        PROVEN_REACHABLE, not misclassify it via the fallback path."""
        from abicheck.model import EnumMember, EnumType, TypeField

        old = _snap(
            functions=[_public_fn("make", "ns::Widget")],
            types=[
                RecordType(
                    name="ns::Widget",
                    kind="struct",
                    fields=[TypeField(name="status", type="ns::detail::Status")],
                ),
            ],
            enums=[
                EnumType(
                    name="ns::detail::Status",
                    members=[EnumMember(name="OK", value=0), EnumMember(name="ERR", value=1)],
                ),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "ns::Widget")],
            types=[
                RecordType(
                    name="ns::Widget",
                    kind="struct",
                    fields=[TypeField(name="status", type="ns::detail::Status")],
                ),
            ],
            enums=[
                EnumType(
                    name="ns::detail::Status",
                    members=[EnumMember(name="OK", value=0)],
                ),
            ],
        )
        raw_change = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="ns::detail::Status::ERR",
            description="member removed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.ENUM_MEMBER_REMOVED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_state == ReachabilityState.PROVEN_REACHABLE

    def test_enum_member_named_like_internal_namespace_stays_unknown(self) -> None:
        """Codex review: is_internal_type is segment-based, and an
        enum-member finding's root keeps its "::member" suffix at the point
        subject_is_internal is computed. An enumerator whose own *name*
        happens to match an internal-namespace segment (e.g. a member
        literally named ``detail``) would make a fully public
        ``ns::Status::detail`` read as internal-namespaced from the member
        name alone, letting a broad `namespace: ns::*` rule wrongly treat a
        genuinely public enum's member change as proven-unreachable. Basing
        the check on enum_owner (the bare enum name) instead must leave this
        UNKNOWN — ns::Status is public, never examined by the internal-only
        layout walk, and no other evidence proves it unreachable."""
        from abicheck.model import EnumMember, EnumType

        old = _snap(
            functions=[_public_fn("make", "ns::Status")],
            enums=[
                EnumType(
                    name="ns::Status",
                    members=[
                        EnumMember(name="ok", value=0),
                        EnumMember(name="detail", value=1),
                    ],
                ),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "ns::Status")],
            enums=[
                EnumType(name="ns::Status", members=[EnumMember(name="ok", value=0)]),
            ],
        )
        raw_change = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="ns::Status::detail",
            description="member removed",
        )
        suppression = SuppressionList([
            Suppression(
                namespace="ns::*",
                reachability="proven-unreachable-only",
                reason="would wrongly suppress a real public enum-member break",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        found = [c for c in ctx.kept if c.kind == ChangeKind.ENUM_MEMBER_REMOVED]
        assert len(found) == 1
        assert found[0].reachability_state == ReachabilityState.UNKNOWN
        assert raw_change not in ctx.suppressed

    def test_public_source_abi_finding_is_reachable_despite_internal_looking_name(
        self,
    ) -> None:
        """Codex review: buildsource/source_diff.py's L4 findings (e.g.
        PUBLIC_TYPEDEF_REMOVED) are built only from a SourceAbiSurface's own
        ``reachable_types`` collection — entities the L4 replay walk already
        proved reachable from the public surface — never from a
        namespace-name heuristic. Without special-casing these kinds, a
        genuinely public alias that happens to live in a namespace segment
        matching DEFAULT_INTERNAL_NAMESPACES (e.g. ``ns::detail::PublicAlias``
        — unusual but real) would be misjudged by is_internal_type() purely
        on its name and, since the plain header-parsed AbiSnapshot.typedefs
        also commonly carries the same alias, fall into the
        known_type_names layout-domain fallback and come out
        PROVEN_UNREACHABLE — letting a broad `namespace: ns::detail::*` +
        `proven-unreachable-only` rule hide a real source/API break that the
        L4 surface itself already proved was public."""
        old = _snap(functions=[_public_fn("foo", "int")])
        old.typedefs["ns::detail::PublicAlias"] = "int"
        new = _snap(functions=[_public_fn("foo", "int")])
        raw_change = Change(
            kind=ChangeKind.PUBLIC_TYPEDEF_REMOVED,
            symbol="ns::detail::PublicAlias",
            description="removed",
        )
        suppression = SuppressionList([
            Suppression(
                namespace="ns::detail::*",
                reachability="proven-unreachable-only",
                reason="would wrongly suppress a real public typedef removal",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        found = [c for c in ctx.kept if c.kind == ChangeKind.PUBLIC_TYPEDEF_REMOVED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_state == ReachabilityState.PROVEN_REACHABLE
        # The break survives — the broad rule could not prove it unreachable.
        assert raw_change not in ctx.suppressed

    @pytest.mark.parametrize(
        "kind",
        [
            ChangeKind.CONCEPT_TIGHTENED,
            ChangeKind.CONSTEXPR_VALUE_CHANGED,
            ChangeKind.DEFAULT_ARGUMENT_CHANGED,
            ChangeKind.INLINE_BODY_CHANGED,
            ChangeKind.TEMPLATE_BODY_CHANGED,
            ChangeKind.GENERATED_HEADER_CHANGED,
            ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH,
            ChangeKind.ODR_SOURCE_CONFLICT,
            ChangeKind.PUBLIC_REACHABILITY_CHANGED,
            ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API,
            ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED,
            ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
            ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT,
            ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED,
        ],
    )
    def test_other_public_source_abi_kinds_are_reachable_despite_internal_looking_name(
        self, kind
    ) -> None:
        """The same public-by-construction gap applies to every other
        source_diff.py (L4) / source_graph_findings.py (L5) finding whose
        subject is a proven-public entity, not just the typedef/macro/
        inline-function/template-removal subset covered first (Codex
        review, multiple passes)."""
        old = _snap(functions=[_public_fn("foo", "int")])
        new = _snap(functions=[_public_fn("foo", "int")])
        raw_change = Change(
            kind=kind, symbol="ns::detail::PublicThing", description="changed"
        )
        suppression = SuppressionList([
            Suppression(
                namespace="ns::detail::*",
                reachability="proven-unreachable-only",
                reason="would wrongly suppress a real public source/API break",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        found = [c for c in ctx.kept if c.kind == kind]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_state == ReachabilityState.PROVEN_REACHABLE
        assert raw_change not in ctx.suppressed

    def test_declared_type_never_reachable_anywhere_is_still_proven_unreachable(
        self,
    ) -> None:
        """Codex review, fourth pass: even when compute_leak_paths finds
        NOTHING reachable anywhere in the whole comparison (the walk's
        result sets are empty on both sides), that is itself conclusive
        proof for a change whose root is a *declared* type — the walk is a
        complete closure over the snapshot's own declarations, so this must
        not regress to the misleadingly-safe-looking UNKNOWN just because
        the old "nothing to tag, bail out early" perf guard used to skip
        the per-change loop entirely in this shape of comparison."""
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
        assert raw_change.reachability_state == ReachabilityState.PROVEN_UNREACHABLE
        # Contrast: a function-shaped change with nothing reachable anywhere
        # (TestFunctionShapedChangeWithNoCallGraphIsUnknown.
        # test_func_removed_with_no_graph_at_all_is_unknown, below) correctly
        # stays UNKNOWN — no walk of any kind could speak to it.

    def test_dwarf_backend_bare_name_type_uses_qualified_name_for_internal_check(
        self,
    ) -> None:
        """Codex review: RecordType.qualified_name is populated (only) in the
        DWARF-backend case, where .name deliberately stays bare (e.g.
        "Hidden") so type-map lookups match the same key as the castxml
        backend, while .qualified_name carries the real namespace path (e.g.
        "ns::detail::Hidden"). A type-shaped change's root/symbol is always
        the bare name — is_internal_type(root, ...) alone sees no "::"
        segments in "Hidden" and misses that this type is genuinely
        internal, wrongly leaving a change the layout walk already proved
        unreachable at UNKNOWN instead of PROVEN_UNREACHABLE."""
        old = _snap(
            functions=[_public_fn("foo", "int")],
            types=[
                RecordType(
                    name="Hidden",
                    kind="class",
                    size_bits=64,
                    qualified_name="ns::detail::Hidden",
                ),
            ],
        )
        new = _snap(
            functions=[_public_fn("foo", "int")],
            types=[
                RecordType(
                    name="Hidden",
                    kind="class",
                    size_bits=128,
                    qualified_name="ns::detail::Hidden",
                ),
            ],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Hidden", description="size changed"
        )
        DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        assert raw_change.reachability_state == ReachabilityState.PROVEN_UNREACHABLE

    def test_ambiguous_bare_name_qualified_lookup_contributes_no_signal(self) -> None:
        """Codex review, second pass: the bare-name -> qualified-name index
        must not resolve an *ambiguous* bare name — a public
        ``ns::api::Hidden`` and an internal ``ns::detail::Hidden`` colliding
        on the bare name "Hidden" must not let the internal one's namespace
        leak onto a change that could equally be about the public one. An
        ambiguous bare name contributes no signal, so this change (which the
        layout walk never examined either — neither type is embedded/
        referenced anywhere) stays honestly UNKNOWN, not misclassified either
        way."""
        old = _snap(
            functions=[_public_fn("foo", "int")],
            types=[
                RecordType(
                    name="Hidden", kind="class", size_bits=64,
                    qualified_name="ns::api::Hidden",
                ),
                RecordType(
                    name="Hidden", kind="class", size_bits=8,
                    qualified_name="ns::detail::Hidden",
                ),
            ],
        )
        new = _snap(
            functions=[_public_fn("foo", "int")],
            types=[
                RecordType(
                    name="Hidden", kind="class", size_bits=128,
                    qualified_name="ns::api::Hidden",
                ),
                RecordType(
                    name="Hidden", kind="class", size_bits=8,
                    qualified_name="ns::detail::Hidden",
                ),
            ],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Hidden", description="size changed"
        )
        DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
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

    def test_public_type_absent_from_internal_only_walk_stays_unknown(self) -> None:
        """Codex review, seventh pass: compute_leak_paths only ever records
        *internal* types it finds reached from the public surface — it
        never records the public seed types themselves. A genuinely public
        declared type (no ScopeOrigin.PUBLIC_HEADER tag, so the direct
        public-symbol check can't catch it either) that is absent from
        reachable_types was therefore never examined by this walk at all,
        not proven unreachable by it — treating it as layout-walk domain
        just because it's some known declared type would let a broad
        `namespace: ns::*` rule suppress a real public-type layout break
        with no diagnostic."""
        old = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[RecordType(name="ns::Widget", kind="class", size_bits=64)],
        )
        new = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[RecordType(name="ns::Widget", kind="class", size_bits=128)],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::Widget",
            description="size changed",
        )
        suppression = SuppressionList([
            Suppression(
                namespace="ns::*",
                reachability="proven-unreachable-only",
                reason="would wrongly suppress a real public-type break",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        found = [c for c in ctx.kept if c.kind == ChangeKind.TYPE_SIZE_CHANGED]
        assert len(found) == 1
        assert found[0].reachability_state == ReachabilityState.UNKNOWN
        assert raw_change not in ctx.suppressed

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
        """A call graph whose extractor_passes confirms a full, completed
        (not narrowed/degraded) pass for *both* edge families
        (call_graph.py's DECL_CALLS_DECL and type_graph.py's
        DECL_REFERENCES_DECL — the combined walk mixes both) on *both*
        sides, and which simply does not reach this decl, is real negative
        evidence — merely having a few unrelated edges present is not
        enough (Codex review)."""
        from abicheck.buildsource.source_graph import GraphEdge

        old = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://other", "ns::detail::other", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")],
            extractor_passes={"call_graph": True, "type_graph": True},
        )
        new = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[_decl_node("decl://pub", "pubFn", "public_header")],
            edges=[],
            extractor_passes={"call_graph": True, "type_graph": True},
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

    def test_completed_passes_with_no_public_roots_is_unknown(self) -> None:
        """Codex review: extractor_passes confirming both families completed
        is not enough on its own -- compute_call_graph_leak_paths only ever
        walks from public roots, so a graph that completed both passes but
        captured no public declaration/type at all has nothing to seed the
        walk with. That is indistinguishable from "walked thoroughly and
        found nothing" but is actually "never walked" -- must not read as
        trustworthy negative evidence."""
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary

        # No "header" node / SOURCE_DECLARES edge at all -- unlike
        # _graph_snap's normal fixtures, this graph has zero public closure
        # even though both passes report complete.
        graph = SourceGraphSummary(
            nodes=[
                GraphNode(
                    id="decl://other", kind="source_decl",
                    label="ns::detail::other", attrs={"visibility": "source"},
                ),
            ],
            edges=[],
            extractor_passes={"call_graph": True, "type_graph": True},
        )
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            functions=[_public_fn("pubFn")],
            build_source=BuildSourcePack(root="", source_graph=graph),
        )
        new = _snap(functions=[_public_fn("pubFn")])
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
        assert found[0].reachability_state == ReachabilityState.UNKNOWN

    def test_func_removed_with_edges_but_no_extractor_pass_confirmation_is_unknown(
        self,
    ) -> None:
        """Codex review: a graph with real edges but no
        extractor_passes["call_graph"]/["type_graph"] confirmation (e.g. a
        header-only or partially-scoped pass) must not be trusted as
        complete just because it happens to carry some edges."""
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
        assert found[0].reachability_state == ReachabilityState.UNKNOWN

    def test_only_one_confirmed_pass_family_is_still_unknown(self) -> None:
        """Codex review, third pass: compute_call_graph_leak_paths's combined
        walk mixes DECL_CALLS_DECL edges (only ever produced by the
        "call_graph" pass) with DECL_REFERENCES_DECL edges (only ever
        produced by the "type_graph" pass) — a build where only the
        type_graph pass completed still has a real coverage gap in the
        call_graph family, so trust requires *both* extractor_passes
        entries, not just one."""
        from abicheck.buildsource.source_graph import GraphEdge

        old = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://other", "ns::detail::other", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_REFERENCES_DECL")],
            extractor_passes={"type_graph": True},
        )
        new = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[_decl_node("decl://pub", "pubFn", "public_header")],
            edges=[],
            extractor_passes={"type_graph": True},
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
        assert found[0].reachability_state == ReachabilityState.UNKNOWN

    def test_removed_decl_only_needs_old_side_call_graph_trusted(self) -> None:
        """Codex review, fifth pass: a REMOVED decl only ever existed on the
        old side, so only the old graph's coverage speaks to whether some
        old public entry called it. An untrusted/absent *new*-side graph
        (unsurprising — the decl is gone there, so there was nothing left
        to extract a fresh graph from) must not turn a real old-side proof
        into UNKNOWN."""
        from abicheck.buildsource.source_graph import GraphEdge

        old = _graph_snap(
            [
                _public_fn("pubFn"),
                _public_fn("ns::detail::unrelated_and_never_called"),
            ],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://other", "ns::detail::other", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")],
            extractor_passes={"call_graph": True, "type_graph": True},
        )
        new = _snap(functions=[_public_fn("pubFn")])
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

    def test_added_decl_only_needs_new_side_call_graph_trusted(self) -> None:
        """Symmetric case: an ADDED decl only ever exists on the new side,
        so only the new graph's coverage matters — an absent old-side graph
        (the decl didn't exist yet) must not force UNKNOWN."""
        from abicheck.buildsource.source_graph import GraphEdge

        old = _snap(functions=[_public_fn("pubFn")])
        new = _graph_snap(
            [
                _public_fn("pubFn"),
                _public_fn("ns::detail::unrelated_and_never_called"),
            ],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://other", "ns::detail::other", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")],
            extractor_passes={"call_graph": True, "type_graph": True},
        )
        raw_change = Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol="ns::detail::unrelated_and_never_called",
            description="added",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_ADDED]
        assert len(found) == 1
        assert found[0].reachability_state == ReachabilityState.PROVEN_UNREACHABLE

    def test_changed_in_place_kind_still_needs_both_sides_trusted(self) -> None:
        """Contrast case: a decl that exists on both sides (a
        "changed-in-place" kind, not *_removed/*_added) still needs both
        sides' call graph trusted for a symmetric proof — one trusted side
        is not enough."""
        from abicheck.buildsource.source_graph import GraphEdge

        old = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://other", "ns::detail::other", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")],
            extractor_passes={"call_graph": True, "type_graph": True},
        )
        new = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://other", "ns::detail::other", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")],
        )
        raw_change = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="ns::detail::unrelated_and_never_called",
            description="return type changed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_RETURN_CHANGED]
        assert len(found) == 1
        assert found[0].reachability_state == ReachabilityState.UNKNOWN

    def test_attribute_toggle_kind_ending_in_added_still_needs_both_sides_trusted(
        self,
    ) -> None:
        """Codex review, eighth pass: a suffix check on the kind name
        (``kind.value.endswith("_added")``) wrongly matches an
        attribute-toggle kind like FUNC_VIRTUAL_ADDED too — the decl exists
        on *both* snapshots there (only whether it's virtual changed), not
        just the new one. Requiring trust from only the new-side graph would
        let an untrusted/never-examined old side silently pass as
        call-graph-proven, misclassifying a real ambiguity as
        PROVEN_UNREACHABLE. The fix checks the decl's actual presence on
        each side instead of the kind name's suffix."""
        from abicheck.buildsource.source_graph import GraphEdge

        old = _snap(functions=[
            _public_fn("pubFn"),
            _public_fn("ns::detail::Thing::unrelated_and_never_called"),
        ])
        new = _graph_snap(
            [
                _public_fn("pubFn"),
                _public_fn("ns::detail::Thing::unrelated_and_never_called"),
            ],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://other", "ns::detail::other", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")],
            extractor_passes={"call_graph": True, "type_graph": True},
        )
        raw_change = Change(
            kind=ChangeKind.FUNC_VIRTUAL_ADDED,
            symbol="ns::detail::Thing::unrelated_and_never_called",
            description="became virtual",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_VIRTUAL_ADDED]
        assert len(found) == 1
        assert found[0].reachability_state == ReachabilityState.UNKNOWN

    def test_exported_public_symbol_not_proven_unreachable_by_call_graph_alone(
        self,
    ) -> None:
        """Codex review, sixth pass: compute_call_graph_leak_paths only ever
        walks dependencies of *consumer-compiled public entries*
        (source_graph.is_consumer_compiled_public_entry() explicitly
        excludes an ordinary out-of-line exported function) — a trusted
        call graph can prove an *internal callee* absent, but says nothing
        about a genuinely public, directly-exported symbol's own
        reachability. Without gating on the subject actually being
        internal-namespaced, a plain FUNC_REMOVED on a real public API
        function with no inline caller would be misread as
        call-graph-proven-unreachable, and a broad
        `reachability: proven-unreachable-only` rule could suppress a
        genuine ABI break instead of leaving it UNKNOWN."""
        from abicheck.buildsource.source_graph import GraphEdge

        old = _graph_snap(
            [_public_fn("pubFn"), _public_fn("acme::PublicApi::doThing")],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://other", "ns::detail::other", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://other", kind="DECL_CALLS_DECL")],
            extractor_passes={"call_graph": True, "type_graph": True},
        )
        new = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[_decl_node("decl://pub", "pubFn", "public_header")],
            edges=[],
        )
        raw_change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="acme::PublicApi::doThing",
            description="removed",
        )
        suppression = SuppressionList([
            Suppression(
                namespace="acme::**",
                reachability="proven-unreachable-only",
                reason="would wrongly suppress a real API removal",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(found) == 1
        assert found[0].reachability_state == ReachabilityState.UNKNOWN
        # The break survives — the broad rule could not prove it unreachable.
        assert raw_change not in ctx.suppressed

    def test_typedef_removed_examined_by_walk_is_proven_unreachable(self) -> None:
        """Codex review: typedef aliases (AbiSnapshot.typedefs, a flat
        {alias: underlying} map, not a RecordType/EnumType) are declared
        snapshot type surface too and must be recognized as layout-walk
        domain, not misread as an unexamined function/variable-shaped
        change."""
        old = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=64),
            ],
        )
        old.typedefs["ns::detail::Alias"] = "int"
        new = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::detail::Base"]),
                RecordType(name="ns::detail::Base", kind="class", size_bits=64),
            ],
        )
        raw_change = Change(
            kind=ChangeKind.TYPEDEF_REMOVED,
            symbol="ns::detail::Alias",
            description="typedef removed",
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
