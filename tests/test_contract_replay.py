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

"""ADR-049 Phase 4: original-decision replay and new-policy re-evaluation."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.contract_context import (
    build_decision_receipt,
    build_persisted_context,
    domain_roots,
)
from abicheck.contract_context_io import (
    persisted_context_from_dict,
    persisted_context_to_dict,
)
from abicheck.contract_evidence import (
    ContractEvidenceBlock,
    DecisionReceiptBlock,
    UnsupportedSchemaVersionError,
)
from abicheck.contract_evidence_collect import collect_contract_evidence
from abicheck.contract_relevance_types import ContractMode, ContractRelevance
from abicheck.contract_replay import (
    compare_decisions,
    load_replayable_context,
    reevaluate_from_evidence,
    replay_original_decisions,
    unresolved_rate,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
from abicheck.surface import compute_public_surface


def _pair(*, with_exports: bool = False) -> tuple[AbiSnapshot, AbiSnapshot]:
    widget = RecordType(
        name="Widget",
        kind="struct",
        size_bits=64,
        fields=[TypeField(name="x", type="int")],
    )
    elf = ElfMetadata(symbols=[ElfSymbol(name="api")]) if with_exports else None
    old = AbiSnapshot(
        library="libdemo.so.1",
        version="1",
        functions=[
            Function(
                name="api",
                mangled="api",
                return_type="int",
                params=[Param(name="w", type="Widget *")],
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        types=[widget],
        elf=elf,
    )
    new = AbiSnapshot(library="libdemo.so.1", version="2", types=[widget], elf=elf)
    return old, new


def _run(*, mode: str = "public", with_exports: bool = False):
    old, new = _pair(with_exports=with_exports)
    result = compare(old, new, contract_evaluation=True, contract_mode=mode)
    assert result.contract_context is not None
    # Persist and reload: everything below must work off the wire format, not
    # off in-process objects that happen to still be alive.
    ctx = persisted_context_from_dict(
        json.loads(json.dumps(persisted_context_to_dict(result.contract_context)))
    )
    return result, ctx


class TestVersionGate:
    def test_supported_context_loads(self) -> None:
        _result, ctx = _run()
        assert load_replayable_context(ctx) is ctx

    def test_future_version_fails_closed(self) -> None:
        _result, ctx = _run()
        raw = persisted_context_to_dict(ctx)
        raw["evaluation_context"]["evaluator_version"] = 99
        future = persisted_context_from_dict(raw)
        with pytest.raises(UnsupportedSchemaVersionError):
            load_replayable_context(future)
        with pytest.raises(UnsupportedSchemaVersionError):
            replay_original_decisions(future)
        with pytest.raises(UnsupportedSchemaVersionError):
            reevaluate_from_evidence(future, [])

    def test_mixed_but_supported_versions_are_not_an_error(self) -> None:
        """Older evidence + a current context is the re-evaluation case.

        Only an individual counter *above* this build's ceiling fails; a
        mixed pair below it is exactly what plan Section 5.1 describes.
        """
        _result, ctx = _run()
        mixed = replace(
            ctx,
            contract_evidence=ContractEvidenceBlock(
                providers=ctx.contract_evidence.providers,
                schema_version=1,
                identity_algorithm_version=1,
            ),
        )
        assert load_replayable_context(mixed) is mixed


class TestReplayOriginalDecisions:
    def test_receipt_is_returned_verbatim(self) -> None:
        result, ctx = _run()
        original = replay_original_decisions(ctx)
        assert original == {
            f"{c.kind.value}:{c.symbol or ''}": c.contract_relevance
            for c in result.changes
        }

    def test_replay_ignores_this_build_s_evidence(self) -> None:
        """Emptying the evidence block cannot change a replayed decision.

        "Current required-provider defaults cannot alter the recorded
        original decision" -- a replay reads the receipt and nothing else.
        """
        _result, ctx = _run()
        stripped = replace(ctx, contract_evidence=ContractEvidenceBlock())
        assert replay_original_decisions(stripped) == replay_original_decisions(ctx)


class TestReevaluateFromEvidence:
    def test_reproduces_the_original_domain_by_default(self) -> None:
        result, ctx = _run()
        replayed = reevaluate_from_evidence(ctx, result.changes)
        comparison = compare_decisions(replay_original_decisions(ctx), replayed)
        assert comparison.is_sound
        assert comparison.agreed  # not vacuous: something actually matched

    def test_public_membership_is_recovered_from_the_persisted_graph(self) -> None:
        result, ctx = _run()
        decision = next(iter(reevaluate_from_evidence(ctx, result.changes).values()))
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"
        assert decision.evidence_refs == ("public_header:old",)

    def test_a_different_mode_needs_no_new_evidence(self) -> None:
        """The whole point of a policy-independent block.

        The run below was evaluated under ``public``; re-evaluating it under
        ``all`` reads the same persisted observations, with no re-collection.
        """
        result, ctx = _run()
        replayed = reevaluate_from_evidence(ctx, result.changes, mode=ContractMode.ALL)
        assert all(
            d.relevance is ContractRelevance.IN_CONTRACT
            and d.reason_code == "all_mode_normalized_entity"
            for d in replayed.values()
        )

    def test_exports_domain_reevaluates_from_export_roots(self) -> None:
        result, ctx = _run(mode="exports", with_exports=True)
        replayed = reevaluate_from_evidence(ctx, result.changes)
        decision = next(iter(replayed.values()))
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "export_root_membership"

    def test_missing_side_evidence_degrades_to_unresolved(self) -> None:
        """A legacy/one-sided context is readable but unresolved.

        Plan Section 5.1: "legacy snapshots remain readable but become
        unresolved where old-side facts needed by ``public`` are absent."
        """
        result, ctx = _run()
        stripped = replace(ctx, contract_evidence=ContractEvidenceBlock())
        replayed = reevaluate_from_evidence(stripped, result.changes)
        assert all(
            d.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
            for d in replayed.values()
        )
        assert unresolved_rate(replayed.values()) == 1.0

    def test_unknown_entity_is_unresolved_not_proven_out(self) -> None:
        """Absence from the graph is unplaceable, never proof of exclusion."""
        result, ctx = _run()
        change = replace(result.changes[0], symbol="never_seen")
        decision = next(iter(reevaluate_from_evidence(ctx, [change]).values()))
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED

    def _orphan_finding(self) -> Change:
        """A finding about a type the snapshot knows but no root reaches."""
        return Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Orphan",
            description="Orphan size changed",
        )

    def _orphan_context(self, *, with_provenance: bool):
        origin = ScopeOrigin.PUBLIC_HEADER if with_provenance else ScopeOrigin.UNKNOWN
        snap = AbiSnapshot(
            library="libdemo.so.1",
            version="1",
            functions=[
                Function(
                    name="api",
                    mangled="api",
                    return_type="Kept *",
                    visibility=Visibility.PUBLIC,
                    origin=origin,
                )
            ],
            types=[
                RecordType(name="Kept", kind="struct", fields=[], origin=origin),
                RecordType(name="Orphan", kind="struct", fields=[], origin=origin),
            ],
        )
        surf = compute_public_surface(snap)
        return build_persisted_context(
            collect_contract_evidence(snap, snap, surf, surf), mode=ContractMode.PUBLIC
        )

    def test_complete_provider_proves_an_exclusion(self) -> None:
        ctx = self._orphan_context(with_provenance=True)
        decision = next(
            iter(reevaluate_from_evidence(ctx, [self._orphan_finding()]).values())
        )
        assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
        assert decision.reason_code == "terminal_authoritative_exclusion"

    def test_incomplete_provider_cannot_prove_an_exclusion(self) -> None:
        """A ``PARTIAL`` search proves no absence.

        Identical snapshot to the test above, minus declaration provenance:
        the type is just as unreachable, but the provider that would have to
        vouch for that did not complete, so the honest answer is unresolved.
        """
        ctx = self._orphan_context(with_provenance=False)
        decision = next(
            iter(reevaluate_from_evidence(ctx, [self._orphan_finding()]).values())
        )
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED


class TestDecisionComparison:
    def test_weakening_is_sound_strengthening_is_not(self) -> None:
        original = {
            "a": ContractRelevance.IN_CONTRACT,
            "b": ContractRelevance.UNKNOWN_UNRESOLVED,
        }
        weaker = compare_decisions(
            original,
            {
                "a": ContractRelevance.UNKNOWN_UNRESOLVED,
                "b": ContractRelevance.UNKNOWN_UNRESOLVED,
            },
        )
        assert weaker.is_sound and weaker.weakened == ("a",)
        stronger = compare_decisions(
            original,
            {
                "a": ContractRelevance.IN_CONTRACT,
                "b": ContractRelevance.PROVEN_OUT_OF_CONTRACT,
            },
        )
        assert not stronger.is_sound and stronger.strengthened == ("b",)

    def test_equally_strong_but_opposite_is_a_defect(self) -> None:
        """``IN_CONTRACT`` -> ``PROVEN_OUT_OF_CONTRACT`` is not a weakening."""
        flipped = compare_decisions(
            {"a": ContractRelevance.IN_CONTRACT},
            {"a": ContractRelevance.PROVEN_OUT_OF_CONTRACT},
        )
        assert flipped.strengthened == ("a",)
        assert not flipped.is_sound

    def test_a_lost_finding_is_not_sound(self) -> None:
        lost = compare_decisions({"a": ContractRelevance.IN_CONTRACT}, {})
        assert lost.only_in_original == ("a",)
        assert not lost.is_sound

    def test_unresolved_rate_of_nothing_is_zero(self) -> None:
        assert unresolved_rate([]) == 0.0


class TestDecisionReceipt:
    def test_all_mode_records_no_roots(self) -> None:
        """``all`` computes no closure, so it claims none.

        Reporting every declaration as a root would read as a computed
        closure the mode by definition never computes (ADR-049 D2).
        """
        _result, ctx = _run()
        assert domain_roots(ctx.contract_evidence, ContractMode.ALL) == ()

    def test_receipt_closure_comes_from_the_persisted_graph(self) -> None:
        _result, ctx = _run()
        receipt = build_decision_receipt(ctx.contract_evidence, ContractMode.PUBLIC)
        assert "decl:api" in receipt.evaluated_contract_roots
        assert "record:Widget" in receipt.evaluated_type_closure

    def test_receipt_is_order_independent(self) -> None:
        _result, ctx = _run()
        forward = build_decision_receipt(ctx.contract_evidence, ContractMode.PUBLIC)
        backward = build_decision_receipt(
            ContractEvidenceBlock(
                providers=tuple(reversed(ctx.contract_evidence.providers))
            ),
            ContractMode.PUBLIC,
        )
        assert forward == backward

    def test_empty_receipt_is_valid(self) -> None:
        context = build_persisted_context(
            ContractEvidenceBlock(), mode=ContractMode.PUBLIC
        )
        assert context.decision_receipt == DecisionReceiptBlock()
