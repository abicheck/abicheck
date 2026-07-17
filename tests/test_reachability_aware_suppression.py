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

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
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

    def test_custom_namespaces_constructor_override(self) -> None:
        """Codex review (P2): DEFAULT_INTERNAL_NAMESPACES only covers
        detail/impl/internal/__detail/_impl — a project using a different
        convention (e.g. "priv") is invisible to the reachability walk unless
        MarkReachability accepts the same namespaces override
        DetectInternalLeaks/DemoteUnreachableInternalChurn already do. No
        caller wires a non-default value through DEFAULT_PIPELINE today
        (see ADR-044's changelog for why closing this for real needs a new
        policy-level config surface); this only pins the constructor
        contract so a future override reaches this step too."""
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
        # Note: unlike a raw pre-existing change (suppressed via
        # ApplySuppression, which can attach the diagnostic), a finding
        # DetectNamespacePatterns builds fresh and suppresses inline via its
        # own ctx.suppression.is_suppressed(c) call has no diagnostic path —
        # same established scope boundary as the other late-detector
        # synthetic findings (DetectTemplatePatterns/DetectInternalLeaks's
        # own leak findings). Not suppressed at all is the fix; a
        # diagnostic for this class of finding is a separate, pre-existing
        # limitation, not something this fix regresses or is scoped to close.

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
