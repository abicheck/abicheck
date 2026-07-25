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

"""ADR-044: reachability-aware suppression.

Regression coverage for the pipeline-order correctness bug the ADR fixes —
a broad ``namespace``/``source_location`` suppression rule used to be able to
remove the raw evidence for an internal-type change *before*
``DetectInternalLeaks`` ever saw it, silently hiding a genuine leak through
the public ABI surface with no trace in the report. These tests build
synthetic ``AbiSnapshot``/``Change`` objects — no compiler needed — and are
part of the default fast test suite.
"""
from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    Variable,
    Visibility,
)
from abicheck.post_processing import DEFAULT_PIPELINE
from abicheck.suppression import Suppression, SuppressionList


def _snap(*, functions=None, types=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version="1.0",
        functions=list(functions or []),
        types=list(types or []),
    )


def _public_fn(name: str, ret: str = "void") -> Function:
    return Function(name=name, mangled=name, return_type=ret, params=[], visibility=Visibility.PUBLIC)


def _reachable_scenario():
    """A public factory function returning a type that inherits an internal
    base — the minimal reproduction of the review's oneDAL dispatcher shape
    (a public entry point whose reachability closure includes an internal
    type)."""
    old = _snap(
        functions=[_public_fn("make", "oneapi::dal::kmeans::descriptor*")],
        types=[
            RecordType(
                name="oneapi::dal::kmeans::descriptor",
                kind="class",
                bases=["oneapi::dal::kmeans::detail::descriptor_base"],
            ),
            RecordType(
                name="oneapi::dal::kmeans::detail::descriptor_base",
                kind="class",
                size_bits=64,
            ),
        ],
    )
    new = _snap(
        functions=[_public_fn("make", "oneapi::dal::kmeans::descriptor*")],
        types=[
            RecordType(
                name="oneapi::dal::kmeans::descriptor",
                kind="class",
                bases=["oneapi::dal::kmeans::detail::descriptor_base"],
            ),
            RecordType(
                name="oneapi::dal::kmeans::detail::descriptor_base",
                kind="class",
                size_bits=128,
            ),
        ],
    )
    raw_change = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED,
        symbol="oneapi::dal::kmeans::detail::descriptor_base",
        description="size changed",
    )
    return old, new, raw_change


def _unreachable_scenario():
    old = _snap(
        functions=[_public_fn("foo", "int")],
        types=[RecordType(name="oneapi::dal::kmeans::detail::hidden", kind="class", size_bits=64)],
    )
    new = _snap(
        functions=[_public_fn("foo", "int")],
        types=[RecordType(name="oneapi::dal::kmeans::detail::hidden", kind="class", size_bits=128)],
    )
    raw_change = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED,
        symbol="oneapi::dal::kmeans::detail::hidden",
        description="size changed",
    )
    return old, new, raw_change


def _needs_evidence_suppression() -> SuppressionList:
    """A minimal suppression whose one rule is broad, so
    SuppressionList.needs_reachability_evidence() is True and
    MarkReachability actually runs its walk — used by tests that want to
    observe the tag directly without a specific rule's matching semantics
    being the point of the test."""
    return SuppressionList([
        Suppression(namespace="__never_matches__::*", reason="evidence trigger only")
    ])


class TestMarkReachability:
    def test_tags_reachable_internal_change(self) -> None:
        old, new, raw_change = _reachable_scenario()
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.TYPE_SIZE_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "value_embedding"
        assert found[0].reachability_proof_path

    def test_public_header_type_own_change_is_reachable(self) -> None:
        """Codex review (fresh evidence): a header-only type never referenced
        by an exported function/variable (so compute_leak_paths's walk never
        reaches it) still needs its OWN change tagged reachable when it
        carries RecordType.origin == ScopeOrigin.PUBLIC_HEADER — that walk
        only ever records *internal* types found while walking from the
        public surface, never the public seed types themselves."""
        old = _snap(
            types=[RecordType(
                name="lib::Widget", kind="class", size_bits=64,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        new = _snap(
            types=[RecordType(
                name="lib::Widget", kind="class", size_bits=128,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="lib::Widget",
            description="size changed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.TYPE_SIZE_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "direct_public_symbol"

    def test_public_header_variable_own_change_is_reachable(self) -> None:
        """Codex review (fresh evidence): Variable/Function/EnumType all
        carry ScopeOrigin too, not just RecordType — a public-header
        variable's own change needs the same direct tag."""
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            variables=[Variable(
                name="lib::kFlag", mangled="lib::kFlag", type="int",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        new = AbiSnapshot(
            library="libtest.so", version="1.0",
            variables=[Variable(
                name="lib::kFlag", mangled="lib::kFlag", type="long",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        raw_change = Change(
            kind=ChangeKind.VAR_TYPE_CHANGED,
            symbol="lib::kFlag",
            description="type changed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.VAR_TYPE_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "direct_public_symbol"

    def test_public_header_cxx_function_removal_is_reachable(self) -> None:
        """Codex review, fresh evidence: root (from _root_type_name_for_change)
        is Change.symbol verbatim for a function-shaped change, and
        diff_symbols.py sets that to the *mangled* linker name -- while
        _public_header_names collects Function.name, which is demangled.
        root == a public_header_names entry therefore never matches for a
        real (mangled) C++ symbol, so a public-header-declared function's
        own FUNC_REMOVED fell through the direct-public-symbol check
        entirely and relied solely on the layout/call-graph walks to tag it
        -- which a standalone public entry point nothing else references or
        embeds is reachable by neither. EnrichSourceLocations (runs before
        MarkReachability) sets Change.qualified_name from the demangled
        Function.name for exactly this FUNC_REMOVED case, so it must be
        checked too."""
        mangled = "_ZN2ns6detail3apiEv"
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            functions=[Function(
                name="ns::detail::api", mangled=mangled, return_type="void",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        new = AbiSnapshot(library="libtest.so", version="1.0", functions=[])
        raw_change = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol=mangled, description="removed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "direct_public_symbol"

    def test_public_header_cxx_variable_removal_is_reachable(self) -> None:
        """Mirror of test_public_header_cxx_function_removal_is_reachable for
        variables: diff_symbols.py sets Change.symbol to Variable.mangled (the
        linker name) for VAR_REMOVED, while _public_header_names collects the
        demangled Variable.name -- so the direct-public-symbol check only
        matches if EnrichSourceLocations also recovers qualified_name for
        variable changes, not just function changes."""
        mangled = "_ZN2ns6detail3varE"
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            variables=[Variable(
                name="ns::detail::var", mangled=mangled, type="int",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        new = AbiSnapshot(library="libtest.so", version="1.0", variables=[])
        raw_change = Change(
            kind=ChangeKind.VAR_REMOVED, symbol=mangled, description="removed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.VAR_REMOVED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "direct_public_symbol"

    def test_public_header_cxx_variable_changed_in_place_is_reachable(self) -> None:
        """Codex review: the mangled-vs-demangled identity gap is not unique
        to VAR_ADDED/VAR_REMOVED -- diff_symbols.py sets Change.symbol to the
        mangled linker name for every "changed in place" variable kind too
        (VAR_TYPE_CHANGED, VAR_BECAME_CONST/VAR_LOST_CONST,
        VAR_ALIGNMENT_CHANGED, VAR_VALUE_CHANGED, VAR_ACCESS_CHANGED/WIDENED,
        VAR_DEPRECATED_ADDED/REMOVED). The variable exists on both sides with
        the same mangled name, so this exercises _qualified_name_for_change's
        generic "unchanged mangled symbol, old/new qualified names agree"
        branch rather than the ADDED/REMOVED branches."""
        mangled = "_ZN2ns6detail3varE"
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            variables=[Variable(
                name="ns::detail::var", mangled=mangled, type="int",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        new = AbiSnapshot(
            library="libtest.so", version="1.0",
            variables=[Variable(
                name="ns::detail::var", mangled=mangled, type="long",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        raw_change = Change(
            kind=ChangeKind.VAR_TYPE_CHANGED, symbol=mangled, description="type changed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.VAR_TYPE_CHANGED]
        assert len(found) == 1
        assert found[0].qualified_name == "ns::detail::var"
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "direct_public_symbol"

    def test_public_header_cxx_variable_removal_reachable_with_bare_castxml_name(
        self,
    ) -> None:
        """Codex review, fresh evidence: the default CastXML backend never
        qualifies Function.name/Variable.name with namespace context --
        dumper_castxml.py's parse_variables()/_function_display_name() both
        store the bare declaration ``name`` XML attribute, so a real
        public-header variable in namespace ``ns::detail`` reaches this
        pipeline as ``Variable(name="var", ...)``, not
        ``Variable(name="ns::detail::var", ...)`` as every other test in this
        class hand-constructs it. The prior '"::" in name' guard silently
        never fired for this (the only real) shape -- qualified identity must
        be recovered from the mangled linker symbol via demangling instead."""
        mangled = "_ZN2ns6detail3varE"
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            variables=[Variable(
                name="var", mangled=mangled, type="int",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        new = AbiSnapshot(library="libtest.so", version="1.0", variables=[])
        raw_change = Change(
            kind=ChangeKind.VAR_REMOVED, symbol=mangled, description="removed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.VAR_REMOVED]
        assert len(found) == 1
        assert found[0].qualified_name == "ns::detail::var"
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "direct_public_symbol"

    def test_public_header_cxx_function_removal_reachable_with_bare_castxml_name(
        self,
    ) -> None:
        """Mirror of the variable case above for functions: the same
        bare-name CastXML shape applies to Function.name, and this exact
        mechanism predates this PR's variable extension -- confirming the gap
        was never function-specific either."""
        mangled = "_ZN2ns6detail3apiEv"
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            functions=[Function(
                name="api", mangled=mangled, return_type="void",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        new = AbiSnapshot(library="libtest.so", version="1.0", functions=[])
        raw_change = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol=mangled, description="removed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(found) == 1
        assert found[0].qualified_name == "ns::detail::api"
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "direct_public_symbol"

    def test_public_header_enum_member_change_is_reachable(self) -> None:
        """Codex review (fresh evidence): an ENUM_MEMBER_* finding's symbol
        is "EnumName::member" (diff_types.py), not the plain enum name —
        needs owner-stripping before the public-header origin check."""
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            enums=[EnumType(
                name="lib::Color",
                members=[EnumMember(name="RED", value=0)],
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        new = AbiSnapshot(
            library="libtest.so", version="1.0",
            enums=[EnumType(name="lib::Color", members=[], origin=ScopeOrigin.PUBLIC_HEADER)],
        )
        raw_change = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="lib::Color::RED",
            description="member removed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.ENUM_MEMBER_REMOVED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "direct_public_symbol"

    def test_non_public_header_type_own_change_stays_untagged(self) -> None:
        """Without --public-header (ScopeOrigin.UNKNOWN, the common case),
        a type not reached via the leak-path walk gets no signal — same as
        before this fix, not a regression."""
        old = _snap(types=[RecordType(name="lib::Widget", kind="class", size_bits=64)])
        new = _snap(types=[RecordType(name="lib::Widget", kind="class", size_bits=128)])
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="lib::Widget",
            description="size changed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.TYPE_SIZE_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is False
        assert found[0].reachability_kind is None

    def test_custom_namespaces_constructor_override(self) -> None:
        """Codex review (P2): DEFAULT_INTERNAL_NAMESPACES only covers
        detail/impl/internal/__detail/_impl — a project using a different
        convention (e.g. "priv") is invisible to the reachability walk unless
        MarkReachability accepts the same namespaces override
        DetectInternalLeaks/DemoteUnreachableInternalChurn already do. An
        explicit constructor argument is the lowest-level override; see
        test_policy_internal_namespaces_reaches_mark_reachability below for
        the ADR-044 P1 item 5 end-to-end PolicyFile.internal_namespaces path
        that now also reaches this step via ctx.internal_namespaces."""
        from abicheck.post_processing import MarkReachability, PipelineContext

        old = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::priv::Base"]),
                RecordType(name="ns::priv::Base", kind="class", size_bits=64),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::priv::Base"]),
                RecordType(name="ns::priv::Base", kind="class", size_bits=128),
            ],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::priv::Base",
            description="size changed",
        )
        ctx = PipelineContext(old=old, new=new, suppression=_needs_evidence_suppression())

        # Default namespaces: "priv" is not recognized as internal, so the
        # walk never sees ns::priv::Base as a leak root.
        MarkReachability().run([raw_change], ctx)
        assert raw_change.public_reachable is False

        # Custom namespaces: "priv" is now recognized, closing the gap.
        raw_change2 = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::priv::Base",
            description="size changed",
        )
        MarkReachability(namespaces=("priv",)).run([raw_change2], ctx)
        assert raw_change2.public_reachable is True

    def test_pipeline_internal_namespaces_reaches_mark_reachability(self) -> None:
        """ADR-044 P1 item 5: DEFAULT_PIPELINE.run(internal_namespaces=...) —
        the value PolicyFile.internal_namespaces feeds via checker.py's
        _run_post_processing — must reach MarkReachability's walk through
        PipelineContext.internal_namespaces, without requiring a bespoke
        MarkReachability(namespaces=...) construction."""
        old = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::priv::Base"]),
                RecordType(name="ns::priv::Base", kind="class", size_bits=64),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::priv::Base"]),
                RecordType(name="ns::priv::Base", kind="class", size_bits=128),
            ],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::priv::Base",
            description="size changed",
        )
        # Without internal_namespaces, "priv" is not recognized as internal.
        DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        assert raw_change.public_reachable is False

        raw_change2 = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::priv::Base",
            description="size changed",
        )
        ctx2 = DEFAULT_PIPELINE.run(
            [raw_change2],
            old,
            new,
            suppression=_needs_evidence_suppression(),
            internal_namespaces=("priv",),
        )
        found = [c for c in ctx2.kept if c.kind == ChangeKind.TYPE_SIZE_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is True

    def test_pipeline_internal_namespaces_reaches_demote_unreachable_churn(self) -> None:
        """ADR-044 P1 item 5: PipelineContext.internal_namespaces must also
        reach DetectInternalLeaks/DemoteUnreachableInternalChurn, not just
        MarkReachability — otherwise a project using a non-default internal
        convention would have its reachability *tag* corrected but the
        unreachable-churn demotion still blind to it."""
        old = _snap(
            functions=[_public_fn("foo", "int")],
            types=[RecordType(name="ns::priv::Hidden", kind="class", size_bits=64)],
        )
        new = _snap(
            functions=[_public_fn("foo", "int")],
            types=[RecordType(name="ns::priv::Hidden", kind="class", size_bits=128)],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::priv::Hidden",
            description="size changed",
        )
        # Without internal_namespaces: "priv" isn't recognized as internal at
        # all, so DemoteUnreachableInternalChurn never considers it for
        # demotion — the raw churn stays in ctx.kept.
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new)
        assert raw_change in ctx.kept

        raw_change2 = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::priv::Hidden",
            description="size changed",
        )
        # With internal_namespaces=("priv",): recognized as internal,
        # unreachable from the public surface -> demoted out of ctx.kept.
        ctx2 = DEFAULT_PIPELINE.run(
            [raw_change2], old, new, internal_namespaces=("priv",)
        )
        assert raw_change2 not in ctx2.kept
        assert raw_change2 in ctx2.out_of_surface

    def test_pipeline_internal_namespaces_reaches_detect_template_patterns(self) -> None:
        """Codex review, fresh evidence: DetectTemplatePatterns's
        detect_internal_template_leaks uses _INTERNAL_TEMPLATE_NAMESPACES —
        the same internal-implementation convention MarkReachability/
        DetectInternalLeaks/DemoteUnreachableInternalChurn use (unlike
        DetectNamespacePatterns's unrelated experimental_namespaces) — but
        DetectTemplatePatterns.run() never threaded ctx.internal_namespaces
        through at all, a genuine fourth gap distinct from the
        DetectNamespacePatterns exclusion, which is deliberate."""
        old = _snap(functions=[_public_fn("lib::priv::walk<int>")])
        new = _snap(functions=[_public_fn("lib::priv::walk<char>")])

        # Without internal_namespaces: "priv" is not in
        # _INTERNAL_TEMPLATE_NAMESPACES, so no finding.
        ctx = DEFAULT_PIPELINE.run([], old, new)
        assert not any(
            c.kind == ChangeKind.INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API
            for c in ctx.kept
        )

        # With internal_namespaces=("priv",): recognized, finding fires.
        ctx2 = DEFAULT_PIPELINE.run([], old, new, internal_namespaces=("priv",))
        found = [
            c for c in ctx2.kept
            if c.kind == ChangeKind.INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API
        ]
        assert len(found) == 1

    def test_policy_file_internal_namespaces_reaches_checker_pipeline(self) -> None:
        """ADR-044 P1 item 5, full glue: checker._run_post_processing must
        read PolicyFile.internal_namespaces and thread it into
        DEFAULT_PIPELINE.run — this is the one hop not exercised by the
        DEFAULT_PIPELINE.run(internal_namespaces=...)-level tests above."""
        from abicheck.checker import _run_post_processing
        from abicheck.policy_file import PolicyFile

        old = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::priv::Base"]),
                RecordType(name="ns::priv::Base", kind="class", size_bits=64),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "ns::Widget*")],
            types=[
                RecordType(name="ns::Widget", kind="class", bases=["ns::priv::Base"]),
                RecordType(name="ns::priv::Base", kind="class", size_bits=128),
            ],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="ns::priv::Base",
            description="size changed",
        )
        policy_file = PolicyFile(internal_namespaces=["priv"])
        kept, *_rest = _run_post_processing(
            [raw_change],
            old,
            new,
            suppression=_needs_evidence_suppression(),
            policy_file=policy_file,
            scope_to_public_surface=False,
            force_public_symbols=None,
            collapse_versioned_symbols=False,
        )
        found = [c for c in kept if c.kind == ChangeKind.TYPE_SIZE_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is True

    def test_does_not_tag_unreachable_internal_change(self) -> None:
        old, new, raw_change = _unreachable_scenario()
        # DemoteUnreachableInternalChurn removes truly-unreachable internal
        # churn from ctx.kept — check the tag directly on the object instead.
        DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        assert raw_change.public_reachable is False
        assert raw_change.reachability_kind is None

    def test_skips_when_suppression_has_only_narrow_rules(self) -> None:
        """Codex review: a suppression file containing only narrow rules with
        the default (or explicit "any") reachability can never actually
        consult Change.public_reachable — both of Suppression's reachability
        gates short-circuit before reading it. Running the public-surface
        walk for such a file is pure waste; SuppressionList.
        needs_reachability_evidence() proves it and MarkReachability must
        skip. This is the common case: a handful of exact symbol: waivers."""
        old, new, raw_change = _reachable_scenario()
        suppression = SuppressionList([
            Suppression(symbol="something_unrelated", reason="exact waiver"),
            Suppression(symbol_pattern=".*_unrelated", reason="pattern waiver"),
            Suppression(
                symbol="something_else", reachability="any", reason="explicit any, still narrow"
            ),
        ])
        assert suppression.needs_reachability_evidence() is False
        DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change.public_reachable is False
        assert raw_change.reachability_kind is None

    def test_runs_when_any_rule_is_broad_even_amongst_narrow_ones(self) -> None:
        old, new, raw_change = _reachable_scenario()
        suppression = SuppressionList([
            Suppression(symbol="something_unrelated", reason="exact waiver"),
            Suppression(namespace="oneapi::dal::**::detail::**", reason="broad rule"),
        ])
        assert suppression.needs_reachability_evidence() is True
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        found = [c for c in ctx.kept if c.kind == ChangeKind.TYPE_SIZE_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is True

    def test_runs_for_narrow_rule_with_explicit_non_any_reachability(self) -> None:
        old, new, raw_change = _reachable_scenario()
        suppression = SuppressionList([
            Suppression(
                symbol="oneapi::dal::kmeans::detail::descriptor_base",
                reachability="unreachable-only",
                reason="narrow but explicitly reachability-gated",
            )
        ])
        assert suppression.needs_reachability_evidence() is True
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        found = [c for c in ctx.kept if c.kind == ChangeKind.TYPE_SIZE_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        # And the rule correctly declines to suppress a reachable change.
        assert raw_change not in ctx.suppressed

    def test_skips_without_suppression(self) -> None:
        """Perf guard (CI benchmark_scaling.py regression): MarkReachability
        must not run its public-surface BFS when no suppression is configured
        at all — the tags it produces have no other consumer in this slice,
        and internal_leak.compute_leak_paths is expensive enough that running
        it unconditionally on every compare() roughly doubled the cost
        DetectInternalLeaks already pays (caught by CI's baseline-regression
        gate)."""
        old, new, raw_change = _reachable_scenario()
        DEFAULT_PIPELINE.run([raw_change], old, new)  # no suppression passed
        assert raw_change.public_reachable is False
        assert raw_change.reachability_kind is None

    def test_pointer_only_pure_layout_change_not_flagged_reachable(self) -> None:
        """Codex review: DetectInternalLeaks does not treat a pure-layout
        change reached only through a pointer as a leak (it is not
        consumer-visible) — MarkReachability must not tag it reachable
        either, or a broad suppression rule gets refused (and a
        suppression_would_hide_public_break diagnostic appended) for churn
        that was always going to be demoted as unreachable anyway."""
        old = _snap(
            functions=[Function(
                name="use", mangled="use", return_type="void",
                params=[Param(name="h", type="oneapi::dal::kmeans::detail::hidden*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            )],
            types=[RecordType(name="oneapi::dal::kmeans::detail::hidden", kind="struct", size_bits=32)],
        )
        new = _snap(
            functions=[Function(
                name="use", mangled="use", return_type="void",
                params=[Param(name="h", type="oneapi::dal::kmeans::detail::hidden*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            )],
            types=[RecordType(name="oneapi::dal::kmeans::detail::hidden", kind="struct", size_bits=64)],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="oneapi::dal::kmeans::detail::hidden",
            description="size changed",
        )
        suppression = SuppressionList([
            Suppression(namespace="oneapi::dal::**::detail::**", reason="private")
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change.public_reachable is False
        assert raw_change in ctx.suppressed
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK not in [c.kind for c in ctx.kept]

    def test_source_location_suppression_of_direct_public_symbol_is_a_known_limitation(
        self,
    ) -> None:
        """Codex review raised this exact scenario (a genuinely public symbol
        matched by a broad source_location glob purely by file path, not by
        name) as a gap; a fix was attempted (tagging any non-internal-
        namespaced subject reachable) and then reverted after it broke
        tests/test_libabigail_parity_extended.py's own
        test_suppress_by_source_location — a private helper with no
        namespace-segment hint under an "internal/" path, the ordinary,
        long-relied-upon use of this exact selector.

        AbiSnapshot's visibility model marks every exported C/C++ symbol
        Visibility.PUBLIC regardless of whether the maintainer considers it
        part of the contract, and neither case's name need contain an
        internal-namespace segment — there is no naming heuristic that tells
        "genuinely public, accidentally path-matched" apart from "genuinely
        private, correctly path-matched". This test documents the accepted,
        current behavior (matches pre-ADR-044 semantics for this selector)
        rather than asserting a fix; closing the gap for real needs actual
        dependency evidence (ADR-044's P1/P2 roadmap: the L5 call-graph /
        consumer-import work), not a heuristic on the symbol's own spelling.
        """
        old = _snap(functions=[_public_fn("foo", "int")])
        new = _snap(functions=[])
        raw_change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="function removed",
            source_location="/project/internal/detail.h:10",
        )
        suppression = SuppressionList([
            Suppression(source_location="*/internal/*", reason="internal headers")
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change.public_reachable is False
        assert raw_change in ctx.suppressed


def _graph_snap(
    functions=None, *, nodes=None, edges=None,
) -> AbiSnapshot:
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import SourceGraphSummary

    graph = SourceGraphSummary(nodes=list(nodes or []), edges=list(edges or []))
    return AbiSnapshot(
        library="libtest.so",
        version="1.0",
        functions=list(functions or []),
        build_source=BuildSourcePack(root="", source_graph=graph),
    )


def _decl_node(node_id: str, label: str, visibility: str):
    from abicheck.buildsource.source_graph import GraphNode

    return GraphNode(
        id=node_id, kind="source_decl", label=label, attrs={"visibility": visibility}
    )


class TestCallGraphReachability:
    """ADR-044 P1 items 1-2: the call-graph analogue of the layout-only
    reachability walk above, closing the exact oneDAL dispatcher gap the P0
    slice's own "What this ADR does not fix" section named — a public
    inline function's body calling into a removed internal template
    specialization has no field/base/signature evidence at all, only a
    DECL_CALLS_DECL edge in the optional L5 source graph."""

    def test_mark_reachability_tags_call_graph_only_change(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge

        old = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::train_ops_dispatcher", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        new = _graph_snap([_public_fn("pubFn")])
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
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "symbol_availability"
        assert "pubFn" in (found[0].reachability_proof_path or "")

    def test_mark_reachability_matches_via_qualified_name_fallback(self) -> None:
        """Codex review, fresh evidence: header_graph.py (--header-graph, no
        real build) never creates a SOURCE_DECL_MAPS_TO_SYMBOL edge, so
        compute_call_graph_leak_paths's mangled-symbol key is unavailable in
        that mode. Change.qualified_name (EnrichSourceLocations, set from
        Function.name independent of graph provenance) must serve as a
        fallback lookup key so MarkReachability still tags the change."""
        from abicheck.buildsource.source_graph import GraphEdge

        mangled = "_ZN2ns6detail19train_ops_dispatcherEv"
        old = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::train_ops_dispatcher", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        new = _graph_snap([_public_fn("pubFn")])
        raw_change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=mangled,
            qualified_name="ns::detail::train_ops_dispatcher",
            description="removed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "symbol_availability"

    def test_detect_internal_leaks_emits_call_graph_overlay_finding(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge

        old = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::train_ops_dispatcher", "source"),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        new = _graph_snap([_public_fn("pubFn")])
        raw_change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="ns::detail::train_ops_dispatcher",
            description="removed",
        )
        ctx = DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        overlay = [
            c for c in ctx.kept
            if c.kind == ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API
        ]
        assert len(overlay) == 1
        assert overlay[0].symbol == "ns::detail::train_ops_dispatcher"

    def test_broad_suppression_cannot_hide_call_graph_reachable_break(self) -> None:
        """The exact oneDAL scenario end-to-end: a blanket detail:: namespace
        suppression must not silently hide the func_removed break once the
        call graph proves it is public-reachable — it must survive, tagged,
        with the withheld-rule diagnostic explaining why."""
        from abicheck.buildsource.source_graph import GraphEdge

        old = _graph_snap(
            [_public_fn("pubFn")],
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node(
                    "decl://int",
                    "oneapi::dal::kmeans::detail::train_ops_dispatcher",
                    "source",
                ),
            ],
            edges=[GraphEdge(src="decl://pub", dst="decl://int", kind="DECL_CALLS_DECL")],
        )
        new = _graph_snap([_public_fn("pubFn")])
        raw_change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="oneapi::dal::kmeans::detail::train_ops_dispatcher",
            description="removed",
        )
        suppression = SuppressionList([
            Suppression(namespace="oneapi::dal::**::detail::**", reason="detail churn")
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        found = [c for c in ctx.kept if c.kind == ChangeKind.FUNC_REMOVED]
        assert len(found) == 1
        assert found[0] not in ctx.suppressed
        overlay = [
            c for c in ctx.kept
            if c.kind == ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API
        ]
        assert len(overlay) == 1
        assert overlay[0] not in ctx.suppressed
        # Two distinct changes are each withheld by the same broad rule here
        # — the raw FUNC_REMOVED (tagged reachable by MarkReachability's
        # call-graph fallback, withheld by ApplySuppression) and the
        # synthetic overlay finding DetectInternalLeaks builds afterwards
        # (withheld via _merge_findings_respecting_suppression) — mirroring
        # the layout case's identical two-diagnostic shape above.
        diag = [
            c for c in ctx.kept
            if c.kind == ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK
        ]
        assert len(diag) == 2

    def test_without_embedded_graph_no_behavior_change(self) -> None:
        """No --sources/--build-info/--header-graph evidence: behaves exactly
        as before this P1 slice — degrades cleanly, no crash."""
        old, new, raw_change = _unreachable_scenario()
        DEFAULT_PIPELINE.run(
            [raw_change], old, new, suppression=_needs_evidence_suppression()
        )
        assert raw_change.public_reachable is False


class TestLateSyntheticLeakFindingsNotSuppressible:
    """Codex review: DetectTemplatePatterns (and any other detector running
    after ApplySuppression) creates fresh Change objects MarkReachability
    never had a chance to tag. A synthetic finding whose entire meaning is
    "this internal thing leaks through the public API" — the same class as
    internal_leak.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API — must therefore mark
    itself public_reachable at construction time, or a broad namespace rule
    default-suppresses it with no overreach diagnostic."""

    def test_internal_template_leak_survives_broad_namespace_suppression(self) -> None:
        old = _snap(functions=[
            Function(
                name="lib::__detail::walk<int>", mangled="walk_int",
                return_type="void", params=[], visibility=Visibility.PUBLIC,
            ),
        ])
        new = _snap(functions=[
            Function(
                name="lib::__detail::walk<double>", mangled="walk_double",
                return_type="void", params=[], visibility=Visibility.PUBLIC,
            ),
        ])
        suppression = SuppressionList([
            Suppression(namespace="lib::__detail::*", reason="private detail namespace")
        ])
        ctx = DEFAULT_PIPELINE.run([], old, new, suppression=suppression)
        kinds = [c.kind for c in ctx.kept]
        assert ChangeKind.INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API in kinds
        leak = next(
            c for c in ctx.kept if c.kind == ChangeKind.INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API
        )
        assert leak.public_reachable is True
        assert leak not in ctx.suppressed


class TestSuppressionPipelineOrderFix:
    """The ADR's headline regression: example B from the review report."""

    def test_broad_namespace_suppression_does_not_hide_reachable_break(self) -> None:
        old, new, raw_change = _reachable_scenario()
        suppression = SuppressionList([
            Suppression(
                namespace="oneapi::dal::**::detail::**",
                reason="Private implementation details",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)

        # The raw layout evidence must survive — this is the bug: it used to
        # be removed by ApplySuppression before DetectInternalLeaks ran.
        kinds = [c.kind for c in ctx.kept]
        assert ChangeKind.TYPE_SIZE_CHANGED in kinds
        # DetectInternalLeaks had real evidence to correlate, so the leak
        # finding fires too.
        assert ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API in kinds
        # And the report explains why the suppression rule did not apply.
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK in kinds
        diag = next(c for c in ctx.kept if c.kind == ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK)
        assert "allow_public_break" in diag.description
        # The diagnostic must name the actual rule that was withheld, not a
        # fallback placeholder (self-review finding: entity_namespace was
        # missing from _build_suppression_overreach_change's selector chain).
        assert "oneapi::dal::**::detail::**" in diag.description
        assert ctx.suppressed == []

    def test_entity_namespace_only_rule_named_in_diagnostic(self) -> None:
        """Same scenario, but using the canonical ``entity_namespace`` spelling
        instead of the legacy ``namespace`` alias — regression test for a
        self-review finding where the diagnostic fell back to ``"?"`` because
        ``entity_namespace`` was missing from the selector fallback chain."""
        old, new, raw_change = _reachable_scenario()
        suppression = SuppressionList([
            Suppression(
                entity_namespace="oneapi::dal::**::detail::**",
                reason="Private implementation details",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        diag = next(
            c for c in ctx.kept if c.kind == ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK
        )
        assert "Suppression rule 'oneapi::dal::**::detail::**' matched" in diag.description

    def test_broad_namespace_suppression_does_not_hide_reachable_variable_removal(
        self,
    ) -> None:
        """Variable counterpart of test_broad_namespace_suppression_does_not_hide_reachable_break:
        a public-header C++ variable removed from a namespace covered by a
        broad ``namespace`` suppression rule must survive, not be silently
        dropped because the mangled Change.symbol never gets resolved back
        to the demangled Variable.name that seeded public-header reachability."""
        mangled = "_ZN2ns6detail3varE"
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            variables=[Variable(
                name="ns::detail::var", mangled=mangled, type="int",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        new = AbiSnapshot(library="libtest.so", version="1.0", variables=[])
        raw_change = Change(
            kind=ChangeKind.VAR_REMOVED, symbol=mangled, description="removed",
        )
        suppression = SuppressionList([
            Suppression(namespace="ns::detail::*", reason="Private implementation details")
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)

        found = [c for c in ctx.kept if c.kind == ChangeKind.VAR_REMOVED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert ctx.suppressed == []

    def test_broad_namespace_suppression_does_not_hide_reachable_variable_change(
        self,
    ) -> None:
        """Codex review: same as the VAR_REMOVED counterpart above, but for a
        variable changed in place (VAR_TYPE_CHANGED) -- the mangled-symbol
        identity gap affects every "changed in place" variable kind, not
        just additions/removals, since diff_symbols.py sets Change.symbol to
        the mangled name there too."""
        mangled = "_ZN2ns6detail3varE"
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            variables=[Variable(
                name="ns::detail::var", mangled=mangled, type="int",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        new = AbiSnapshot(
            library="libtest.so", version="1.0",
            variables=[Variable(
                name="ns::detail::var", mangled=mangled, type="long",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )],
        )
        raw_change = Change(
            kind=ChangeKind.VAR_TYPE_CHANGED, symbol=mangled, description="type changed",
        )
        suppression = SuppressionList([
            Suppression(namespace="ns::detail::*", reason="Private implementation details")
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)

        found = [c for c in ctx.kept if c.kind == ChangeKind.VAR_TYPE_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert ctx.suppressed == []

    def test_broad_namespace_suppression_still_suppresses_unreachable_churn(self) -> None:
        """Unreachable internal churn is unaffected — no regression for the
        common, safe case this rule shape is meant for."""
        old, new, raw_change = _unreachable_scenario()
        suppression = SuppressionList([
            Suppression(
                namespace="oneapi::dal::**::detail::**",
                reason="Private implementation details",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change in ctx.suppressed
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK not in [c.kind for c in ctx.kept]

    def test_allow_public_break_makes_the_override_explicit(self) -> None:
        old, new, raw_change = _reachable_scenario()
        suppression = SuppressionList([
            Suppression(
                namespace="oneapi::dal::**::detail::**",
                reason="Reviewed — safe to hide",
                allow_public_break=True,
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change in ctx.suppressed
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK not in [c.kind for c in ctx.kept]

    def test_narrow_symbol_suppression_unaffected_by_default(self) -> None:
        """A narrow (exact symbol/type) selector suppresses a public-reachable
        BREAKING change with no extra ceremony — allow_public_break is only
        required for a *broad* (namespace/source_location) rule (ADR-044 D2).
        Naming one exact symbol is already the deliberate, audited action;
        requiring allow_public_break there too would make it impossible to
        suppress an ordinary, intentional public API removal without
        ceremony — the failure mode this ADR targets is a *glob*
        over-matching something its author never reasoned about, not this."""
        old, new, raw_change = _reachable_scenario()
        raw_change.kind = ChangeKind.TYPE_ALIGNMENT_CHANGED  # still BREAKING
        suppression = SuppressionList([
            Suppression(
                symbol="oneapi::dal::kmeans::detail::descriptor_base",
                reason="exact symbol, known safe",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change in ctx.suppressed
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK not in [c.kind for c in ctx.kept]

    def test_narrow_symbol_plus_source_location_filter_stays_narrow(self) -> None:
        """Codex review: adding source_location/namespace as an *additional*
        filter alongside an exact symbol selector can only narrow which
        changes match (AND semantics) — it can never introduce a match the
        bare symbol: selector wouldn't already have matched, so it must not
        lose the narrow-selector "unchanged behavior" guarantee and start
        requiring allow_public_break."""
        old, new, raw_change = _reachable_scenario()
        raw_change.kind = ChangeKind.TYPE_ALIGNMENT_CHANGED  # still BREAKING
        raw_change.source_location = "/project/internal/descriptor_base.h:1"
        suppression = SuppressionList([
            Suppression(
                symbol="oneapi::dal::kmeans::detail::descriptor_base",
                source_location="*/internal/*",
                reason="exact symbol, scoped to internal headers",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change in ctx.suppressed
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK not in [c.kind for c in ctx.kept]

    def test_member_name_plus_namespace_stays_broad(self) -> None:
        """Unlike symbol/symbol_pattern/type_pattern, member_name alone
        matches a bare trailing name across *any* containing type/namespace
        — combined with a namespace filter, that filter is still doing the
        real scoping work, not merely narrowing an already-pinned-down
        match, so this combination must stay broad (require
        allow_public_break for a public-reachable BREAKING change)."""
        old, new, raw_change = _reachable_scenario()
        raw_change.kind = ChangeKind.TYPE_ALIGNMENT_CHANGED  # still BREAKING
        suppression = SuppressionList([
            Suppression(
                namespace="oneapi::dal::**::detail::**",
                member_name="descriptor_base",
                reason="broad namespace + bare member name",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change not in ctx.suppressed
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK in [c.kind for c in ctx.kept]

    def test_broad_rule_with_reachability_any_suppresses_non_breaking_reachable_change(
        self,
    ) -> None:
        """The allow_public_break gate only concerns BREAKING/API_BREAK kinds
        (ADR-044 D2) — once a broad rule's reachability is explicitly widened
        to "any" (opting back into pre-ADR-044 matching), a public-reachable
        but merely RISK-classified change still suppresses with no
        allow_public_break needed; only a BREAKING/API_BREAK kind would."""
        old, new, raw_change = _reachable_scenario()
        raw_change.kind = ChangeKind.FUNC_NOEXCEPT_REMOVED  # COMPATIBLE_WITH_RISK, not BREAKING
        raw_change.symbol = "oneapi::dal::kmeans::detail::descriptor_base"
        suppression = SuppressionList([
            Suppression(
                namespace="oneapi::dal::**::detail::**", reachability="any", reason="private"
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change.public_reachable is True
        assert raw_change in ctx.suppressed

    def test_public_only_reachability_matches_only_reachable_changes(self) -> None:
        """reachability: public-only is the inverse of the unreachable-only
        default — useful for isolating leak findings while investigating.
        Uses a non-BREAKING kind so the allow_public_break short-circuit
        doesn't mask the plain public-only fallthrough for either side."""
        old, new, reachable_change = _reachable_scenario()
        reachable_change.kind = ChangeKind.FUNC_NOEXCEPT_REMOVED
        _, _, unreachable_change = _unreachable_scenario()
        unreachable_change.kind = ChangeKind.FUNC_NOEXCEPT_REMOVED
        suppression = SuppressionList([
            Suppression(
                namespace="oneapi::dal::**::detail::**",
                reachability="public-only",
                reason="isolate leaks under investigation",
            )
        ])
        ctx = DEFAULT_PIPELINE.run(
            [reachable_change, unreachable_change], old, new, suppression=suppression
        )
        assert reachable_change in ctx.suppressed
        assert unreachable_change not in ctx.suppressed
        # Codex review: a public-only rule correctly declining to match
        # genuinely unreachable churn is not "hiding a public break" — no
        # diagnostic should be emitted for it (the change is simply kept,
        # for the ordinary reason that no rule suppressed it).
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK not in [c.kind for c in ctx.kept]

    def test_broad_default_declining_a_risk_only_reachable_change_has_no_diagnostic(
        self,
    ) -> None:
        """Codex review: the unreachable-only default correctly declining to
        match a public-reachable but merely RISK-classified change is not
        "hiding a public break" either — emitting the diagnostic here would
        wrongly claim the symbol needs allow_public_break, which would not
        even change the outcome for a non-breaking kind (allow_public_break
        only ever bypasses the reachability gate for a BREAKING/API_BREAK
        change)."""
        old, new, raw_change = _reachable_scenario()
        raw_change.kind = ChangeKind.FUNC_NOEXCEPT_REMOVED  # COMPATIBLE_WITH_RISK
        suppression = SuppressionList([
            Suppression(namespace="oneapi::dal::**::detail::**", reason="private")
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change.public_reachable is True
        assert raw_change not in ctx.suppressed
        assert raw_change in ctx.kept
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK not in [c.kind for c in ctx.kept]


class TestSuppressionYamlRoundTrip:
    def test_new_keys_parse(self, tmp_path) -> None:
        p = tmp_path / "suppressions.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - namespace: "oneapi::dal::**::detail::**"\n'
            "    reachability: unreachable-only\n"
            '    reason: "private"\n'
            '  - cause_namespace: "oneapi::dal::**::detail::**"\n'
            "    allow_public_break: true\n"
            '    reason: "explicit"\n'
        )
        sl = SuppressionList.load(p)
        assert len(sl) == 2
        assert sl._suppressions[0].namespace == "oneapi::dal::**::detail::**"
        assert sl._suppressions[1].cause_namespace == "oneapi::dal::**::detail::**"
        assert sl._suppressions[1].allow_public_break is True

    def test_namespace_and_entity_namespace_conflict_rejected(self, tmp_path) -> None:
        p = tmp_path / "suppressions.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - namespace: "a::*"\n'
            '    entity_namespace: "b::*"\n'
            '    reason: "x"\n'
        )
        with pytest.raises(ValueError, match="aliases"):
            SuppressionList.load(p)

    def test_invalid_reachability_value_rejected(self, tmp_path) -> None:
        p = tmp_path / "suppressions.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - namespace: "a::*"\n'
            "    reachability: sometimes\n"
            '    reason: "x"\n'
        )
        with pytest.raises(ValueError, match="reachability"):
            SuppressionList.load(p)

    def test_allow_public_break_string_value_rejected(self, tmp_path) -> None:
        """Codex review: bool("false") is True in Python — a quoted string
        must be rejected outright rather than silently coerced to True for
        this safety-critical override."""
        p = tmp_path / "suppressions.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - namespace: "a::*"\n'
            '    allow_public_break: "false"\n'
            '    reason: "x"\n'
        )
        with pytest.raises(ValueError, match="allow_public_break"):
            SuppressionList.load(p)

    def test_allow_public_break_true_bool_accepted(self, tmp_path) -> None:
        p = tmp_path / "suppressions.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - namespace: "a::*"\n'
            "    allow_public_break: true\n"
            '    reason: "x"\n'
        )
        sl = SuppressionList.load(p)
        assert sl._suppressions[0].allow_public_break is True

    def test_allow_public_break_string_rejected_on_direct_construction(self) -> None:
        """Codex review: SuppressionList.load's validation doesn't protect a
        programmatic caller constructing Suppression directly — Python does
        not enforce the dataclass field's bool annotation at runtime, so
        Suppression(allow_public_break="false") must be rejected in
        __post_init__ too, or the truthy string would silently enable this
        safety-critical override."""
        with pytest.raises(ValueError, match="allow_public_break"):
            Suppression(namespace="a::*", allow_public_break="false", reason="x")


class TestLateDetectorSyntheticFindings:
    """Codex review: a detector that runs *after* ApplySuppression (e.g.
    DetectNamespacePatterns) builds brand-new Change objects that
    MarkReachability never had a chance to tag — if the finding's own
    construction doesn't set public_reachable, a broad suppression rule
    silently hides it with no diagnostic, since it defaults to False."""

    def test_experimental_removed_without_replacement_survives_broad_suppression(
        self,
    ) -> None:
        old = _snap(functions=[_public_fn("lib::experimental::foo", "int")])
        new = _snap(functions=[])
        suppression = SuppressionList([
            Suppression(namespace="lib::experimental::*", reason="experimental churn")
        ])
        ctx = DEFAULT_PIPELINE.run([], old, new, suppression=suppression)
        found = [
            c for c in ctx.kept
            if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        ]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0] not in ctx.suppressed
        # Codex review (fresh evidence): DetectNamespacePatterns now routes
        # its late findings through the same evaluate()-based helper
        # ApplySuppression uses, so the matched-but-withheld broad rule
        # produces the same diagnostic here too.
        diag = [
            c for c in ctx.kept
            if c.kind == ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK
        ]
        assert len(diag) == 1
        assert "lib::experimental::foo" in diag[0].symbol

    def test_cpo_kind_changed_survives_broad_suppression(self) -> None:
        """Codex review: DetectTemplatePatterns's CPO_KIND_CHANGED (a
        function-to-variable CPO flip for a public name) has the same gap as
        EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT above."""
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            functions=[Function(
                name="lib::detail::foo", mangled="lib::detail::foo",
                return_type="int", params=[], visibility=Visibility.PUBLIC,
            )],
        )
        new = AbiSnapshot(
            library="libtest.so", version="1.0",
            # Variable.name set already-qualified (unrealistic for a real
            # castxml dump, which never namespace-qualifies it — see
            # test_diff_templates.py's TestCpoKindChanged docstring — but
            # sidesteps needing a real Itanium-mangled string here; the
            # detector's _qualified_function_name returns name unchanged
            # whenever it already contains "::").
            variables=[Variable(
                name="lib::detail::foo", mangled="lib::detail::foo",
                type="lib::detail::__foo_fn", visibility=Visibility.PUBLIC,
            )],
        )
        suppression = SuppressionList([
            Suppression(namespace="lib::detail::*", reason="detail namespace churn")
        ])
        ctx = DEFAULT_PIPELINE.run([], old, new, suppression=suppression)
        found = [c for c in ctx.kept if c.kind == ChangeKind.CPO_KIND_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0] not in ctx.suppressed
        diag = [
            c for c in ctx.kept
            if c.kind == ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK
        ]
        assert len(diag) == 1


class TestLateDetectorSuppressionDiagnostic:
    """Codex review (fresh evidence, post-construction-time-tagging round):
    DetectCppPatterns/DetectTemplatePatterns/DetectNamespacePatterns/
    DetectInternalLeaks all build findings *after* ApplySuppression and
    filtered them via the plain SuppressionList.is_suppressed() boolean,
    which silently drops the SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK diagnostic
    ApplySuppression itself would have produced for a broad rule that
    matched but was withheld by the reachability gate. All four now route
    through the shared _merge_findings_respecting_suppression() helper
    (post_processing.py), which uses evaluate() instead."""

    def test_internal_leak_finding_gets_withheld_diagnostic(self) -> None:
        """DetectInternalLeaks's own INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
        finding is tagged public_reachable=True at construction — a broad
        default (unreachable-only) rule matching its namespace is withheld,
        and must now produce the diagnostic like any other withheld rule."""
        old, new, raw_change = _reachable_scenario()
        suppression = SuppressionList([
            Suppression(
                namespace="oneapi::dal::**::detail::**",
                reason="detail namespace churn",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        found = [
            c for c in ctx.kept
            if c.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
        ]
        assert len(found) == 1
        assert found[0] not in ctx.suppressed
        # Two distinct changes are each withheld by the same broad rule here
        # — the raw TYPE_SIZE_CHANGED (tagged reachable by MarkReachability,
        # withheld by ApplySuppression) and the synthetic leak finding
        # DetectInternalLeaks builds afterwards (withheld by the fix under
        # test) — so two diagnostics are expected, one per withheld change.
        diag = [
            c for c in ctx.kept
            if c.kind == ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK
        ]
        assert len(diag) == 2
        assert any(
            "descriptor_base" in (d.symbol or "") for d in diag
        )

    def test_late_detector_finding_records_suppression_rule(self) -> None:
        """G29 Phase 3 slice 2 follow-up (Codex review): a late-pattern-
        detector finding (DetectCppPatterns/DetectTemplatePatterns/
        DetectNamespacePatterns) that a rule actually suppresses -- not just
        withholds -- must also get Change.suppression_rule stamped, the
        same as ApplySuppression's own direct suppressions. This exercises
        _merge_findings_respecting_suppression (the shared helper all three
        late detectors route through) directly with a narrow, non-broad
        rule that suppresses outright rather than being gated."""
        from abicheck.post_processing import (
            PipelineContext,
            _merge_findings_respecting_suppression,
        )

        old, new = _snap(), _snap()
        suppression = SuppressionList(
            [Suppression(symbol="late::finding", label="late-workaround")]
        )
        ctx = PipelineContext(old=old, new=new, suppression=suppression)
        changes: list[Change] = []
        new_finding = Change(
            kind=ChangeKind.CPO_KIND_CHANGED,
            symbol="late::finding",
            description="late synthetic finding",
        )
        _merge_findings_respecting_suppression(changes, [new_finding], ctx)

        assert changes == []
        assert ctx.suppressed == [new_finding]
        assert new_finding.suppression_rule == "late-workaround"


class TestCheckerLevelSuppressionDiagnostic:
    """ADR-044 P1 item 6: checker.py's own _filter_suppressed_changes (SONAME/
    platform-floor policy findings) and _apply_surface_metrics (ADR-027
    surface roll-ups) build/receive Change objects directly — outside
    DEFAULT_PIPELINE entirely — so they had the same is_suppressed()-only gap
    the post_processing.py late-detector fixes (TestLateDetectorSyntheticFindings
    above) already closed for findings built inside the pipeline. Both now
    route through SuppressionList.evaluate() too."""

    def test_filter_suppressed_changes_emits_withheld_diagnostic(self) -> None:
        from abicheck.checker import _filter_suppressed_changes

        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="oneapi::dal::kmeans::detail::train_ops_dispatcher",
            description="removed",
            public_reachable=True,
        )
        suppression = SuppressionList([
            Suppression(namespace="oneapi::dal::**::detail::**", reason="broad rule")
        ])
        suppressed: list[Change] = []
        visible = _filter_suppressed_changes([c], suppression, suppressed)
        assert c in visible
        assert c not in suppressed
        diag = [v for v in visible if v.kind == ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK]
        assert len(diag) == 1
        assert "train_ops_dispatcher" in diag[0].symbol

    def test_filter_suppressed_changes_still_suppresses_narrow_rule(self) -> None:
        """Unchanged behavior for the common case: a narrow symbol: waiver
        still suppresses outright, with no diagnostic."""
        from abicheck.checker import _filter_suppressed_changes

        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="lib::internal_helper",
            description="removed",
        )
        suppression = SuppressionList([
            Suppression(symbol="lib::internal_helper", reason="exact waiver")
        ])
        suppressed: list[Change] = []
        visible = _filter_suppressed_changes([c], suppression, suppressed)
        assert visible == []
        assert suppressed == [c]

    def test_filter_suppressed_changes_noop_without_suppression(self) -> None:
        from abicheck.checker import _filter_suppressed_changes

        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="removed")
        suppressed: list[Change] = []
        assert _filter_suppressed_changes([c], None, suppressed) == [c]
        assert suppressed == []

    def test_apply_surface_metrics_emits_withheld_diagnostic(self, monkeypatch) -> None:
        """Wiring test: _apply_surface_metrics now filters
        diff_surface_metrics()'s output through the same evaluate()-based
        helper. Surface-metric findings are COMPATIBLE-only in practice
        today (see the function's own docstring), so a BREAKING finding is
        injected here purely to exercise the withheld-diagnostic mechanism,
        not to claim diff_surface_metrics produces one."""
        import abicheck.diff_surface_metrics as dsm
        from abicheck.checker import _apply_surface_metrics

        finding = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="lib::detail::Foo",
            description="synthetic surface finding",
            public_reachable=True,
        )
        monkeypatch.setattr(dsm, "diff_surface_metrics", lambda old, new: [finding])
        suppression = SuppressionList([
            Suppression(namespace="lib::detail::*", reason="broad rule")
        ])
        old = _snap()
        new = _snap()
        kept: list[Change] = []
        suppressed: list[Change] = []
        kept2, verdict = _apply_surface_metrics(
            old,
            new,
            kept,
            [],
            suppressed,
            suppression,
            "strict_abi",
            None,
            Verdict.NO_CHANGE,
        )
        assert finding in kept2
        assert finding not in suppressed
        diag = [c for c in kept2 if c.kind == ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK]
        assert len(diag) == 1
        assert verdict != Verdict.NO_CHANGE

    def test_apply_surface_metrics_still_suppresses_narrow_rule(self, monkeypatch) -> None:
        import abicheck.diff_surface_metrics as dsm
        from abicheck.checker import _apply_surface_metrics

        finding = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="lib::Foo", description="synthetic"
        )
        monkeypatch.setattr(dsm, "diff_surface_metrics", lambda old, new: [finding])
        suppression = SuppressionList([Suppression(symbol="lib::Foo", reason="exact")])
        old = _snap()
        new = _snap()
        kept: list[Change] = []
        suppressed: list[Change] = []
        kept2, verdict = _apply_surface_metrics(
            old,
            new,
            kept,
            [],
            suppressed,
            suppression,
            "strict_abi",
            None,
            Verdict.NO_CHANGE,
        )
        assert kept2 == []
        assert suppressed == [finding]
        assert verdict == Verdict.NO_CHANGE
