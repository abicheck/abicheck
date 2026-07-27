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
    Visibility,
)
from abicheck.surface import (
    REASON_NO_PROVENANCE,
    REASON_OFF_PYTHON_SURFACE,
    REASON_PRIVATE_INTERNAL_UNREACHABLE,
    PublicSurface,
    compute_public_surface,
)


def _fn(name, ret="void", params=(), vis=Visibility.PUBLIC, origin=ScopeOrigin.UNKNOWN):
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}",
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
        # A reason string this module doesn't recognize (e.g. the separate
        # POST-manifest-surface feature's own custom reason) must not crash
        # or be silently treated as either terminal or weak -- it falls
        # through to ordinary resolvable/identity/classify_change_surface
        # handling, same as if the field were unset.
        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3apiv",
            description="",
            surface_exclusion_reason="not in POST manifest committed surface",
        )
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.IN_CONTRACT


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


class TestPublicModeTerminalExclusion:
    def test_not_exported_symbol_is_proven_out_of_contract(self) -> None:
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
            relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
            reason_code="terminal_authoritative_exclusion",
            assurance=ContractAssurance.COMPLETE,
        )

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
