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

"""ADR-049 Phase 3: tests for the shadow contract-relevance evaluator.

``contract_evaluation.py`` is not consulted by any live pipeline stage (see
its own module docstring) -- these tests exercise the pure function directly:
the ``NOT_APPLICABLE`` kind curation, the ``ALL``/``PUBLIC`` mode split, the
unresolvable-surface and identity-ambiguity downgrades, and the
terminal-vs-weak surface-exclusion-reason mapping. They do not re-verify
``surface.classify_change_surface``'s own correctness (``test_surface.py``
already owns that); real snapshots are used wherever a real one produces the
needed surface state, with a real repro also given for the harder-to-reach
``no-provenance`` weak reason rather than mocking the collaborator.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.contract_evaluation import (
    ContractEvaluationDecision,
    evaluate_change_contract_relevance,
    evaluate_snapshot_pair_contract_relevance,
)
from abicheck.contract_relevance_types import (
    ContractAssurance,
    ContractMode,
    ContractRelevance,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
from abicheck.surface import (
    REASON_NO_PROVENANCE,
    REASON_OFF_PYTHON_SURFACE,
    REASON_PRIVATE_INTERNAL_UNREACHABLE,
    PublicSurface,
    compute_public_surface,
)


def _fn(
    name,
    ret="void",
    params=(),
    vis=Visibility.PUBLIC,
    origin=ScopeOrigin.UNKNOWN,
    mangled=None,
):
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        origin=origin,
    )


def _rec(name, size=64, origin=ScopeOrigin.UNKNOWN):
    return RecordType(name=name, kind="struct", size_bits=size, origin=origin)


_UNRESOLVABLE = PublicSurface()  # resolvable defaults to False


class TestNotApplicableKinds:
    """A NOT_APPLICABLE kind short-circuits before mode or surface evidence
    is even consulted -- true regardless of mode or surface resolvability."""

    def test_soname_changed_is_not_applicable_under_public_mode(self) -> None:
        c = Change(kind=ChangeKind.SONAME_CHANGED, symbol="", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.NOT_APPLICABLE,
            reason_code="non_entity_finding",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_not_applicable_kind_wins_even_with_resolvable_surfaces(self) -> None:
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(kind=ChangeKind.RELRO_WEAKENED, symbol="", description="")
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.NOT_APPLICABLE
        assert decision.reason_code == "non_entity_finding"

    def test_ordinary_entity_kind_is_not_not_applicable(self) -> None:
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3foov", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision.relevance is not ContractRelevance.NOT_APPLICABLE


class TestUnsupportedMode:
    @pytest.mark.parametrize("mode", [ContractMode.EXPORTS, "exports"])
    def test_exports_mode_raises_not_implemented(self, mode: object) -> None:
        # A bare serialized value must be coerced through ContractMode(...)
        # the same as a real enum member, not silently misrouted.
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3foov", description="")
        with pytest.raises(NotImplementedError):
            evaluate_change_contract_relevance(
                c, _UNRESOLVABLE, _UNRESOLVABLE, mode=mode
            )

    def test_invalid_mode_value_raises_value_error(self) -> None:
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3foov", description="")
        with pytest.raises(ValueError, match="mode must be one of"):
            evaluate_change_contract_relevance(
                c, _UNRESOLVABLE, _UNRESOLVABLE, mode="bogus"
            )


class TestAllMode:
    def test_ordinary_finding_is_in_contract(self) -> None:
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3foov", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.ALL
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="all_mode_normalized_entity",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_all_mode_does_not_need_resolvable_surfaces(self) -> None:
        # ALL mode makes no closed-world claim about a header-derived
        # surface at all, so an unresolvable surface must not downgrade it.
        c = Change(kind=ChangeKind.VAR_REMOVED, symbol="g_x", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.ALL
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT

    def test_not_applicable_kind_still_wins_under_all_mode(self) -> None:
        c = Change(kind=ChangeKind.PIE_DISABLED, symbol="", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.ALL
        )
        assert decision.relevance is ContractRelevance.NOT_APPLICABLE

    def test_bare_all_string_is_coerced_to_the_real_enum_member(self) -> None:
        # Regression (Codex review): `mode="all"` (a bare str, not the
        # ContractMode enum member) satisfied the `_SUPPORTED_MODES`
        # membership check (equality/hash) but then failed the
        # `is ContractMode.ALL` identity check, silently falling through
        # to the PUBLIC path -- so an unresolvable-surface finding wrongly
        # came back UNKNOWN_UNRESOLVED instead of IN_CONTRACT.
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3foov", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode="all"
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT


class TestPublicModeUnresolvedSurface:
    def test_unresolvable_surface_downgrades_to_unknown_unresolved(self) -> None:
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3foov", description="")
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.UNAVAILABLE,
        )

    def test_one_sided_unresolvable_surface_downgrades_too(self) -> None:
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        resolvable = compute_public_surface(snap)
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3apiv", description="")
        decision = evaluate_change_contract_relevance(
            c, resolvable, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"

    def test_python_prefixed_kind_bypasses_unresolvable_surface(self) -> None:
        # Regression (Codex review, eleventh round): a `python_*` finding
        # lives on a distinct evidence axis (the Python API/stub surface)
        # from the C/C++ header surface this gate checks -- a definitive
        # event like PYTHON_API_FUNCTION_REMOVED must stay IN_CONTRACT even
        # when the unrelated C header surface is completely unresolvable
        # (e.g. a pure-Python-facing comparison with no header evidence at
        # all), not be downgraded by a gate that doesn't apply to it.
        c = Change(
            kind=ChangeKind.PYTHON_API_FUNCTION_REMOVED,
            symbol="mymodule.some_func",
            description="",
        )
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="public_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )


class TestPublicModeAlreadyExcludedByPipeline:
    """A finding already demoted to the audit ledger by an earlier pipeline
    step (post_processing.py's DemoteOffPythonSurface/
    DemoteUnreachableInternalChurn) carries that step's own authoritative
    ``surface_exclusion_reason`` -- it must be consulted directly rather
    than recomputed from scratch, since a from-scratch
    classify_change_surface call can reach a different, weaker conclusion
    (e.g. the unresolvable-surface branch for an off-Python-surface
    finding, which by construction has no C-header surface at all)."""

    def test_off_python_surface_reason_wins_even_with_unresolvable_surfaces(
        self,
    ) -> None:
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Foo",
            description="",
            surface_exclusion_reason=REASON_OFF_PYTHON_SURFACE,
        )
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
            reason_code="terminal_authoritative_exclusion",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_private_internal_unreachable_reason_wins_over_fresh_reclassification(
        self,
    ) -> None:
        # Even with a resolvable, all-public surface that a from-scratch
        # classify_change_surface call would happily call IN_CONTRACT, the
        # already-recorded confirmed-private reason must win.
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="detail::Impl",
            description="",
            surface_exclusion_reason=REASON_PRIVATE_INTERNAL_UNREACHABLE,
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
        assert decision.reason_code == "terminal_authoritative_exclusion"

    def test_weak_pipeline_reason_downgrades_to_unresolved(self) -> None:
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Foo",
            description="",
            surface_exclusion_reason=REASON_NO_PROVENANCE,
        )
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.PARTIAL,
        )

    def test_all_mode_ignores_pipeline_surface_exclusion_reason(self) -> None:
        # ALL mode makes no surface-membership claim at all -- a pre-existing
        # exclusion reason from public-surface scoping is irrelevant to it.
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Foo",
            description="",
            surface_exclusion_reason=REASON_OFF_PYTHON_SURFACE,
        )
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.ALL
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT

    def test_unrecognized_pipeline_reason_falls_through_to_normal_classification(
        self,
    ) -> None:
        # A reason string this module genuinely does not recognize must not
        # crash or be silently treated as either terminal or weak -- it
        # falls through to ordinary resolvable/identity/classify_change_surface
        # handling, same as if the field were unset.
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3api",
            description="",
            surface_exclusion_reason="some future pipeline stage's own custom reason",
        )
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT

    def test_post_manifest_not_committed_reason_is_terminal(self) -> None:
        # Regression (Codex review, twelfth round): `compare --post-manifest`
        # (post_processing.py's FilterNonPublicSurface._run_allowlist) demotes
        # a concrete export absent from the committed allowlist with its own
        # "not in POST manifest committed surface" reason -- a confident,
        # terminal exclusion (ADR-049 D2's "exact manifests" evidence
        # provider), not an unrecognized string. Previously fell through to a
        # fresh classify_change_surface recomputation, which could reclassify
        # it IN_CONTRACT purely because the symbol also happens to be
        # header-resolvable, even though the exact-manifest domain already
        # excluded it. Uses the same literal `contract_evaluation`'s own
        # `_REASON_POST_MANIFEST_NOT_COMMITTED` holds (not a shared import --
        # post_processing.py is at the repo's 2000-line hard cap and cannot
        # export a constant without a separate splitting effort) rather than
        # post_processing.py's private module internals.
        import abicheck.contract_evaluation as mod

        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3api",
            description="",
            surface_exclusion_reason=mod._REASON_POST_MANIFEST_NOT_COMMITTED,
        )
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
            reason_code="terminal_authoritative_exclusion",
            assurance=ContractAssurance.COMPLETE,
        )


class TestPublicModeIdentityAmbiguous:
    def test_reduced_tier_identity_downgrades_before_surface_is_consulted(self) -> None:
        # A type-level change with no symbol/qualified_name at all resolves
        # to finding_identity's REDUCED tier (see resolve_change_identity's
        # docstring) -- the surfaces here are deliberately unresolvable too,
        # confirming the identity check is (at minimum) not order-dependent
        # on surface resolvability for this decision.
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="", description="")
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="identity_ambiguous",
            assurance=ContractAssurance.PARTIAL,
        )

    def test_never_filter_kind_bypasses_the_identity_ambiguous_downgrade(
        self,
    ) -> None:
        # Regression (Codex review, thirteenth round): VISIBILITY_LEAK's
        # sole producer emits a batch-shaped finding with symbol="<visibility>"
        # (a synthetic spokesperson, never a real entity) -- finding_identity
        # deliberately resolves this to the REDUCED tier (confirmed
        # empirically). Since _NEVER_FILTER_KIND_NAMES findings are trusted
        # unconditionally by construction (the same as python_* findings),
        # they must bypass the identity-ambiguity gate entirely rather than
        # being downgraded to UNKNOWN_UNRESOLVED purely because their
        # symbol isn't a resolvable per-entity identity.
        c = Change(
            kind=ChangeKind.VISIBILITY_LEAK,
            symbol="<visibility>",
            description="leaked: foo, bar",
        )
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="public_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_never_filter_kind_bypasses_unresolvable_surface_too(self) -> None:
        # Same finding, but with fully unresolvable surfaces -- confirms the
        # bypass happens before the resolvable-surface gate as well, not
        # just before the identity-ambiguity gate.
        c = Change(
            kind=ChangeKind.VISIBILITY_LEAK,
            symbol="<visibility>",
            description="leaked: foo, bar",
        )
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"


class TestPublicModeSourceAbiKindsTrustedByConstruction:
    """L4/L5 source-derived kinds in ``post_processing._PUBLIC_SOURCE_ABI_KINDS``
    are built only from an already-proven-public entity -- their ``symbol``
    (a macro/inline-function/typedef/etc. name) is never a real C/C++
    header-surface function/variable/type ``classify_change_surface`` can
    place, so it always fell through to that function's "cannot place it --
    keep it" conservative fallback, which ``_in_surface_result_is_confirmed``
    correctly refuses to treat as genuine confirmation -- wrongly downgrading
    every one of these definitively-public findings to
    ``UNKNOWN_UNRESOLVED`` even with fully resolvable surfaces on both sides
    (Codex review, fresh evidence)."""

    def test_public_macro_removed_is_in_contract_despite_no_universe_evidence(
        self,
    ) -> None:
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.PUBLIC_MACRO_REMOVED, symbol="MY_MACRO", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="public_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_inline_function_removed_bypasses_unresolvable_surface_too(self) -> None:
        # Same bypass ordering as _NEVER_FILTER_KIND_NAMES/python_*: this
        # must not require a resolvable surface at all.
        c = Change(
            kind=ChangeKind.INLINE_FUNCTION_REMOVED,
            symbol="my_inline_fn",
            description="",
        )
        decision = evaluate_change_contract_relevance(
            c, _UNRESOLVABLE, _UNRESOLVABLE, mode=ContractMode.PUBLIC
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_public_typedef_removed_bypasses_the_identity_ambiguous_downgrade(
        self,
    ) -> None:
        # An empty symbol would otherwise resolve to finding_identity's
        # REDUCED tier (see TestPublicModeIdentityAmbiguous) -- confirms the
        # bypass happens before the identity-ambiguity gate too.
        c = Change(kind=ChangeKind.PUBLIC_TYPEDEF_REMOVED, symbol="", description="")
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"


class TestPublicModeInSurface:
    def test_public_function_is_in_contract(self) -> None:
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="api", description="")
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="public_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )


class TestPublicModeConservativeRetentionIsNotConfirmation:
    """``classify_change_surface`` returns ``(True, None)`` from several
    sources with very different confidence -- genuine public-root/closure
    membership, but also surface.py's own anti-hiding "cannot place this
    finding, so keep it" fallback (an empty type-candidate set, or an
    internal-namespace type deferred to the internal-leak detector).
    Neither of the latter two is evidence of public membership, and must
    not be silently upgraded to IN_CONTRACT (Codex review, eighth round)."""

    def test_type_unknown_to_either_snapshot_is_unresolved_not_confirmed(
        self,
    ) -> None:
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="TotallyUnknownType",
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.PARTIAL,
        )

    def test_type_level_finding_is_not_confirmed_by_a_colliding_symbol_name(
        self,
    ) -> None:
        # Regression (Codex review, fourteenth round): classify_change_surface
        # never consults public_symbols for a type-level kind at all (its own
        # _classify_symbol_level call is gated on `not type_level_finding`) --
        # exactly to prevent a type name colliding with an unrelated exported
        # function/variable of the same spelling. A type totally unknown to
        # either snapshot (the "cannot place it, keep it" fallback -- not
        # genuine confirmation) whose symbol happens to match an unrelated
        # public function of the same name must not be confirmed via that
        # coincidental symbol-universe match.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("Foo", origin=ScopeOrigin.PUBLIC_HEADER)],
        )
        s = compute_public_surface(snap)
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Foo", description="")
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"

    def test_internal_namespace_type_with_no_leak_finding_is_unresolved(
        self,
    ) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api")],
            types=[_rec("detail::Impl")],
        )
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="detail::Impl", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"

    def test_constant_changed_stays_in_contract_despite_no_universe_evidence(
        self,
    ) -> None:
        # constant_changed is a _NEVER_FILTER_KIND_NAMES kind -- public by
        # construction (the dumper only extracts PUBLIC_HEADER constants),
        # so it must stay trusted even though a constant name never
        # appears in public_symbols/public_types at all.
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(kind=ChangeKind.CONSTANT_CHANGED, symbol="MY_CONST", description="")
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_python_prefixed_kind_stays_in_contract_despite_no_universe_evidence(
        self,
    ) -> None:
        # A python_* finding lives on a distinct evidence axis the
        # header-surface universes don't cover at all.
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.PYTHON_ABI3_DROPPED,
            symbol="some.python.name",
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_qualified_symbol_tail_confirms_via_public_symbols(self) -> None:
        # A namespace-qualified symbol whose trailing ::-segment matches a
        # public symbol is genuine confirmation, mirroring
        # classify_change_surface's own qualified-tail handling.
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="ns::api", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_qualified_symbol_tail_not_in_public_symbols_falls_through_to_type_check(
        self,
    ) -> None:
        # The qualified-tail symbol check's non-matching branch: a
        # non-type-level finding whose symbol's trailing ::-segment isn't a
        # public symbol either, falling through to the type-candidate check
        # (which also fails to confirm, since the symbol isn't a type name).
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="ns::totally_unrelated",
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"


class TestPublicModeMemberLevelConfirmation:
    """A member-level finding (``TYPE_FIELD_OFFSET_CHANGED`` etc.) is
    owner-qualified: ``symbol="Point::x"``. Confirmation must check the
    *owner* (``Point``) against ``public_types``, mirroring
    ``classify_change_surface``'s own owner-stripping -- passing the full
    ``"Point::x"`` to ``_type_identifiers`` yields ``{"Point::x", "x"}``,
    never ``"Point"``, so this previously always failed confirmation
    (Codex review, ninth round)."""

    def test_public_struct_field_offset_change_confirms_via_owner_type(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Point", origin=ScopeOrigin.PUBLIC_HEADER)],
            types=[
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=64,
                    fields=[TypeField(name="x", type="int")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
        )
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
            symbol="Point::x",
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="public_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_enum_member_change_confirms_via_owner_enum(self) -> None:
        from abicheck.model import EnumMember, EnumType

        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Mode", origin=ScopeOrigin.PUBLIC_HEADER)],
            enums=[
                EnumType(
                    name="Mode",
                    members=[EnumMember(name="A", value=0)],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
        )
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
            symbol="Mode::A",
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"


class TestPublicModeAmbiguousTypeRoot:
    """When two distinct records/enums share one bare tail name (e.g.
    ``one::Point``/``two::Point``, both spelled bare ``Point``) and an
    unqualified ``Point *`` public signature cannot identify which one it
    references, ``compute_public_surface`` deliberately keeps *both* in
    ``public_types`` (its own anti-hiding rule) while recording ``Point`` in
    ``ambiguous_type_names``. Confirmation must not treat that conservative
    closure expansion as proof of root membership (Codex review, eleventh
    round)."""

    def test_ambiguous_bare_tail_is_unresolved_not_confirmed(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="void", params=["Point *"])],
            types=[
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="one::Point",
                ),
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=32,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="two::Point",
                ),
            ],
        )
        s = compute_public_surface(snap)
        assert "Point" in s.ambiguous_type_names
        assert "Point" in s.public_types
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Point", description="")
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"

    def test_unambiguous_candidate_still_confirms_alongside_an_ambiguous_one(
        self,
    ) -> None:
        # A finding whose candidate set includes both an ambiguous name and
        # a genuinely unambiguous public type must still confirm via the
        # unambiguous one -- the ambiguity check should not overreject.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[
                _fn("api", ret="void", params=["Point *", "Clear *"]),
            ],
            types=[
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="one::Point",
                ),
                RecordType(
                    name="Point",
                    kind="struct",
                    size_bits=32,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="two::Point",
                ),
                RecordType(
                    name="Clear",
                    kind="struct",
                    size_bits=8,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
        )
        s = compute_public_surface(snap)
        assert "Point" in s.ambiguous_type_names
        assert "Clear" not in s.ambiguous_type_names
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Clear", description="")
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_qualified_candidate_reached_only_via_ambiguous_bare_tail_is_unresolved(
        self,
    ) -> None:
        # Regression (Codex review, twelfth round): ns1::Mode/ns2::Mode share
        # the bare tail "Mode". A public signature referencing only bare
        # "Mode" makes _walk_type_closure add *both* qualified names to
        # public_types (walking each ambiguous match, per its own anti-hiding
        # rule) -- but ambiguous_type_names only ever records the bare tail
        # "Mode", never the qualified names themselves. A member-level
        # finding owner-stripped to the qualified "ns1::Mode" therefore
        # matched public_types directly and was wrongly confirmed, even
        # though the public signature never disambiguated which of the two
        # enums it actually reaches.
        from abicheck.model import EnumMember, EnumType

        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="void", params=["Mode"])],
            enums=[
                EnumType(
                    name="ns1::Mode",
                    members=[EnumMember(name="X", value=0)],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                EnumType(
                    name="ns2::Mode",
                    members=[EnumMember(name="Y", value=0)],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
        )
        s = compute_public_surface(snap)
        assert "Mode" in s.ambiguous_type_names
        assert {"ns1::Mode", "ns2::Mode"} <= s.public_types
        c = Change(
            kind=ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
            symbol="ns1::Mode::X",
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"

    def test_qualified_candidate_with_unambiguous_bare_tail_still_confirms(
        self,
    ) -> None:
        # The propagated ambiguity check must not overreject: a qualified
        # candidate whose own bare tail is genuinely unambiguous still
        # confirms normally.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="ns::Widget")],
            types=[
                RecordType(
                    name="Widget",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="ns::Widget",
                ),
            ],
        )
        s = compute_public_surface(snap)
        assert "Widget" not in s.ambiguous_type_names
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="ns::Widget", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_exact_qualified_reference_still_over_rejected_known_gap(self) -> None:
        # Known, deliberately-deferred limitation (Codex review, fifteenth
        # round): a public signature naming ``ns1::Point`` *explicitly*
        # (fully qualified) is a genuinely exact, unambiguous reference --
        # but `_type_identifiers` derives the bare tail "Point" from that
        # same qualified string (by design, to also match a short in-
        # namespace alias), and that bare tail also drives the *ambiguous*
        # widening path that adds the unrelated `ns2::Point` to
        # `public_types` too. The two routes are indistinguishable from
        # `public_types`/`ambiguous_type_names` alone, so this exact match
        # is currently (over-)rejected the same as a genuinely ambiguous
        # bare reference would be. This test locks in today's conservative
        # (never wrongly IN_CONTRACT) behavior -- see
        # `_in_surface_result_is_confirmed`'s docstring for why a precise
        # fix needs new provenance data in `surface.py` itself, out of
        # scope for a drive-by fix here. If `surface.py` ever gains that
        # provenance and this starts resolving to IN_CONTRACT instead, that
        # is progress, not a regression -- update this test then.
        # Mirrors the twelfth-round test's DWARF-style convention (the
        # namespace is baked directly into `.name`, not a separate
        # `qualified_name`) -- with the bare-name + `qualified_name`
        # convention (castxml/clang), `record_by_name` never even keys the
        # qualified spelling at all (only the bare tail), so the qualified
        # candidate wouldn't reach `public_types` either way and this
        # scenario couldn't be reproduced through that convention.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="void", params=["ns1::Point *"])],
            types=[
                RecordType(
                    name="ns1::Point",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                RecordType(
                    name="ns2::Point",
                    kind="struct",
                    size_bits=32,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
        )
        s = compute_public_surface(snap)
        assert "Point" in s.ambiguous_type_names
        assert {"ns1::Point", "ns2::Point"} <= s.public_types
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="ns1::Point", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"


class TestPublicModeHiddenFriendConfirmation:
    """``hidden_friend_removed``/``hidden_friend_added`` findings go through
    ``surface._classify_hidden_friend_surface`` instead of the ordinary
    symbol/type path -- a hidden friend can never produce a real export, so
    it will typically not appear in ``public_symbols``/``public_types`` at
    all. Confirmation must be based on the classifier's own origin-based
    checks (owner or friend-symbol confidently ``PUBLIC_HEADER``), not
    universe membership (Codex review, tenth round)."""

    def test_owner_confirmed_public_by_origin_is_in_contract(self) -> None:
        # Mirrors test_surface.py's TestHiddenFriendSurface.
        # test_public_project_hidden_friend_retained: the owner is
        # confidently PUBLIC_HEADER by *origin*, but is never referenced by
        # any public function signature, so it never enters public_types.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("public_api", origin=ScopeOrigin.PUBLIC_HEADER)],
            types=[
                RecordType(
                    name="point",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
        )
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.HIDDEN_FRIEND_REMOVED,
            symbol="_ZN5mylibeqERKNS_5pointES2_",
            caused_by_type="mylib::point",
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="public_root_membership",
            assurance=ContractAssurance.COMPLETE,
        )

    def test_friend_symbol_confirmed_public_by_origin_is_in_contract(self) -> None:
        # No caused_by_type at all -- confirmation must fall back to the
        # friend function's own recorded origin.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[
                _fn(
                    "mylib::operator==",
                    mangled="_ZN5mylibeqERKNS_5pointES2_",
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
        )
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.HIDDEN_FRIEND_REMOVED,
            symbol="_ZN5mylibeqERKNS_5pointES2_",
            caused_by_type=None,
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_unresolved_hidden_friend_owner_is_unresolved_not_confirmed(self) -> None:
        # No caused_by_type and no matching friend-symbol origin either --
        # classify_change_surface's own conservative "keep it" fallback,
        # not genuine provenance confirmation.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("public_api", origin=ScopeOrigin.PUBLIC_HEADER)],
        )
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.HIDDEN_FRIEND_REMOVED,
            symbol="_ZN5mylibeqERKNS_5pointES2_",
            caused_by_type=None,
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.PARTIAL,
        )

    def test_owner_present_but_inconclusive_falls_through_to_unresolved(self) -> None:
        # caused_by_type resolves to a real, present type, but its origin is
        # UNKNOWN (neither confidently public nor confidently private) --
        # must fall through to the friend-symbol check, and since that is
        # also inconclusive here, the overall result is the classifier's
        # conservative "keep it" fallback, not confirmed provenance.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("public_api", origin=ScopeOrigin.PUBLIC_HEADER)],
            types=[
                RecordType(
                    name="point",
                    kind="struct",
                    size_bits=64,
                    origin=ScopeOrigin.UNKNOWN,
                )
            ],
        )
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.HIDDEN_FRIEND_REMOVED,
            symbol="_ZN5mylibeqERKNS_5pointES2_",
            caused_by_type="mylib::point",
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"


class TestPublicModeTerminalExclusion:
    def test_non_public_type_is_proven_out_of_contract(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("InternalCache")],
        )
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="InternalCache", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
        assert decision.reason_code == "terminal_authoritative_exclusion"


class TestPublicModeWeakReason:
    def test_no_provenance_reachability_demotion_stays_unresolved(self) -> None:
        # A type reachable by nothing, with UNKNOWN origin, while the rest of
        # the snapshot *does* carry provenance and typed roots (surface.py's
        # own documented "reduced confidence" case -- REASON_NO_PROVENANCE,
        # never treated as a terminal exclusion here).
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="int", origin=ScopeOrigin.PUBLIC_HEADER)],
            types=[_rec("InternalCache", origin=ScopeOrigin.UNKNOWN)],
        )
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="InternalCache", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.PARTIAL,
        )

    def test_not_exported_symbol_is_unresolved_not_terminal(self) -> None:
        # Regression (Codex review, eleventh round): ADR-049 D2 defines
        # `public` mode's evidence domain as including "public declarations"
        # independent of ELF-export status -- distinguishing exported vs.
        # not is exactly what the separate `exports` mode is for. A symbol
        # that is `known` but not `Visibility.PUBLIC` for a reason other
        # than a confident private/system-header origin (REASON_NOT_EXPORTED)
        # must therefore stay UNKNOWN_UNRESOLVED, not be treated as a
        # terminal proof of exclusion.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api"), _fn("internal", vis=Visibility.ELF_ONLY)],
        )
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="internal", description=""
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision == ContractEvaluationDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.PARTIAL,
        )

    def test_public_header_but_hidden_visibility_is_unresolved_not_terminal(
        self,
    ) -> None:
        # The concrete real-world shape the finding above names: a function
        # genuinely declared in a public header (origin PUBLIC_HEADER) but
        # not ELF-exported (e.g. an inline, or explicit visibility-hidden
        # attribute) -- confirmed empirically that surface.py's own
        # _classify_symbol_level emits REASON_NOT_EXPORTED for exactly this
        # shape (no private/system-header signal fires first). Treating it
        # as terminal would have wrongly resolved a real public-header
        # declaration to PROVEN_OUT_OF_CONTRACT.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[
                _fn("public_api", origin=ScopeOrigin.PUBLIC_HEADER),
                _fn(
                    "inline_api",
                    vis=Visibility.HIDDEN,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
        )
        s = compute_public_surface(snap)
        c = Change(kind=ChangeKind.FUNC_REMOVED, symbol="inline_api", description="")
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"


class TestNeverEmitsUnknownUnproven:
    """The module's central safety rule (see its docstring): no code path
    constructs UNKNOWN_UNPROVEN, ever."""

    def test_source_never_constructs_unknown_unproven(self) -> None:
        # The module docstring *discusses* UNKNOWN_UNPROVEN (to explain why
        # it's avoided) -- what must never appear is executable code that
        # references the enum member, so this walks the AST rather than
        # grepping raw source text (which would also match the docstring).
        import ast
        import inspect

        import abicheck.contract_evaluation as mod

        tree = ast.parse(inspect.getsource(mod))
        module_docstring = ast.get_docstring(tree)
        body_without_docstring = tree.body[1:] if module_docstring else tree.body
        names = {
            node.attr
            for stmt in body_without_docstring
            for node in ast.walk(stmt)
            if isinstance(node, ast.Attribute)
        }
        assert "UNKNOWN_UNPROVEN" not in names


class TestContractEvaluationDecisionValidation:
    def test_unknown_reason_code_rejected(self) -> None:
        with pytest.raises(ValueError):
            ContractEvaluationDecision(
                relevance=ContractRelevance.IN_CONTRACT,
                reason_code="not_a_real_reason_code",
                assurance=ContractAssurance.COMPLETE,
            )


class TestEvaluateSnapshotPairContractRelevance:
    def test_matches_per_change_evaluation(self) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api"), _fn("internal", vis=Visibility.ELF_ONLY)],
        )
        s = compute_public_surface(snap)
        changes = [
            Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="api", description=""),
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="internal", description=""
            ),
            Change(kind=ChangeKind.SONAME_CHANGED, symbol="", description=""),
        ]
        decisions = evaluate_snapshot_pair_contract_relevance(
            changes, s, s, mode=ContractMode.PUBLIC
        )
        expected = [
            evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
            for c in changes
        ]
        assert decisions == expected

    def test_empty_changes_list(self) -> None:
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        assert evaluate_snapshot_pair_contract_relevance([], s, s) == []
