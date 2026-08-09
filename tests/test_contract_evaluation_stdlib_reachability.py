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

"""ADR-049 Phase 3 shadow evaluator: ``directly_referenced_stdlib_old``/
``directly_referenced_stdlib_new`` confirmation.

Split out of ``test_contract_evaluation.py`` (AI-readiness 2000-line hard
cap): a second, independent confirmation source alongside
``PublicSurface.public_types`` for a stdlib type a public signature names
outright -- ``public_types`` deliberately excludes stdlib types as
non-ABI-surface toolchain internals (see
``contract_evaluation._in_surface_result_is_confirmed``'s own docstring).
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.contract_evaluation import evaluate_change_contract_relevance
from abicheck.contract_relevance_types import ContractMode, ContractRelevance
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    Visibility,
)
from abicheck.surface import compute_public_surface
from abicheck.type_reachability import directly_referenced_stdlib_type_spellings


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


class TestPublicModeDirectlyReferencedStdlib:
    """``directly_referenced_stdlib_old``/``directly_referenced_stdlib_new`` --
    a second confirmation source alongside ``public_types``, for a stdlib
    type a public signature names outright (``public_types`` deliberately
    excludes stdlib types as non-ABI-surface toolchain internals)."""

    def test_finding_on_a_directly_referenced_stdlib_type_is_confirmed(
        self,
    ) -> None:
        # public_types has nothing to say about a stdlib type at all
        # (surface.py never walks into it) -- confirmation must come purely
        # from the reachability evidence.
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="vector<int, std::allocator<int> >",
            description="",
        )
        decision = evaluate_change_contract_relevance(
            c,
            s,
            s,
            mode=ContractMode.PUBLIC,
            directly_referenced_stdlib_old=frozenset(
                {"vector<int, std::allocator<int> >"}
            ),
            directly_referenced_stdlib_new=frozenset(
                {"vector<int, std::allocator<int> >"}
            ),
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"

    def test_a_nested_directly_referenced_type_does_not_confirm_its_container(
        self,
    ) -> None:
        # Regression (Codex review, fresh evidence): matching was originally
        # containment (does the referenced identity appear *inside* the
        # finding's own spelling), which let an unrelated, independently
        # directly-referenced std::allocator<int> confirm a TYPE_SIZE_CHANGED
        # finding on the unrelated, larger
        # std::vector<int, std::allocator<int> > purely because one spelling
        # embeds the other -- reproduced empirically before the fix (exact
        # matching only) landed. The vector itself is genuinely NOT among
        # the directly-referenced identities here.
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="vector<int, std::allocator<int> >",
            description="",
        )
        decision = evaluate_change_contract_relevance(
            c,
            s,
            s,
            mode=ContractMode.PUBLIC,
            directly_referenced_stdlib_old=frozenset({"std::allocator<int>"}),
            directly_referenced_stdlib_new=frozenset({"std::allocator<int>"}),
        )
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"

    def test_new_side_only_evidence_does_not_confirm_an_old_side_authoritative_finding(
        self,
    ) -> None:
        # ADR-049 D4: a modification (not in ADDITION_KINDS) is judged by the
        # old side alone -- new-side-only reachability evidence must not
        # manufacture confidence about an old, unresolved obligation, same
        # as the existing public_types side-authority rule.
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="vector<int, std::allocator<int> >",
            description="",
        )
        decision = evaluate_change_contract_relevance(
            c,
            s,
            s,
            mode=ContractMode.PUBLIC,
            directly_referenced_stdlib_old=frozenset(),
            directly_referenced_stdlib_new=frozenset(
                {"vector<int, std::allocator<int> >"}
            ),
        )
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"

    def test_omitting_the_params_is_unaffected(self) -> None:
        # Default None on both -- every pre-existing caller's behaviour is
        # unchanged.
        snap = AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        s = compute_public_surface(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="vector<int, std::allocator<int> >",
            description="",
        )
        decision = evaluate_change_contract_relevance(c, s, s, mode=ContractMode.PUBLIC)
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"


class TestExportOnlyReferenceDoesNotConfirmThePublicContract:
    """Codex review, fresh evidence (PR #677): a stdlib type reached only via
    an ``EXPORT_ONLY``-origin declaration (exported by the binary, no header
    at all) must not confirm a ``--contract public`` finding -- that
    declaration is exactly the evidence the separate ``exports`` domain
    exists to evaluate.

    The evidence-source snapshot deliberately carries its own,
    reachability-empty ``PublicSurface`` (``_evidence_snapshot()``'s
    functions are the only public declarations, and none is used to build
    ``s`` here) so this isolates ``_in_surface_result_is_confirmed``'s
    *second* confirmation source (``directly_referenced_stdlib_old``/
    ``_new``) from its first (``auth.public_types``, via
    ``compute_public_surface``) -- mirroring how
    ``TestPublicModeDirectlyReferencedStdlib`` above already decouples the
    two so a synthetic evidence set alone drives the assertion. Feeding the
    same snapshot into both ``compute_public_surface`` and
    ``directly_referenced_stdlib_type_spellings`` would let
    ``surface.py``'s own closure -- which, unlike this module, seeds a type
    from a ``Visibility.PUBLIC`` declaration of *any* origin including
    ``EXPORT_ONLY`` -- confirm the finding through the first source
    regardless of this fix, masking whether the fix under test does
    anything at all."""

    def _evidence_snapshot(self) -> AbiSnapshot:
        return AbiSnapshot(
            library="l",
            version="1",
            functions=[
                _fn(
                    "api",
                    params=["std::string"],
                    origin=ScopeOrigin.EXPORT_ONLY,
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )

    def test_export_only_reference_leaves_the_finding_unresolved(self) -> None:
        evidence_snap = self._evidence_snapshot()
        # `functions=[_fn("api")]`, same as this module's other tests: a
        # plain public function with no stdlib reference at all, just to
        # make the surface resolvable (`has_public`) without itself
        # contributing anything to `public_types`.
        s = compute_public_surface(
            AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        )
        evidence = directly_referenced_stdlib_type_spellings(
            evidence_snap, exclude_export_only_roots=True
        )
        assert evidence == frozenset()
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="std::string", description=""
        )
        decision = evaluate_change_contract_relevance(
            c,
            s,
            s,
            mode=ContractMode.PUBLIC,
            directly_referenced_stdlib_old=evidence,
            directly_referenced_stdlib_new=evidence,
        )
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
        assert decision.reason_code == "required_evidence_incomplete"

    def test_the_unscoped_evidence_would_have_wrongly_confirmed_it(self) -> None:
        # Documents the bug this guards against: without
        # exclude_export_only_roots, the same export-only reference *would*
        # confirm the finding -- the fix is the flag, not a change to
        # confirmation matching itself.
        evidence_snap = self._evidence_snapshot()
        # `functions=[_fn("api")]`, same as this module's other tests: a
        # plain public function with no stdlib reference at all, just to
        # make the surface resolvable (`has_public`) without itself
        # contributing anything to `public_types`.
        s = compute_public_surface(
            AbiSnapshot(library="l", version="1", functions=[_fn("api")])
        )
        evidence = directly_referenced_stdlib_type_spellings(evidence_snap)
        assert evidence == frozenset({"std::string", "string"})
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="std::string", description=""
        )
        decision = evaluate_change_contract_relevance(
            c,
            s,
            s,
            mode=ContractMode.PUBLIC,
            directly_referenced_stdlib_old=evidence,
            directly_referenced_stdlib_new=evidence,
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT
