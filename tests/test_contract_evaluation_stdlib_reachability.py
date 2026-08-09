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
from abicheck.model import AbiSnapshot, Function, Param, ScopeOrigin, Visibility
from abicheck.surface import compute_public_surface


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
