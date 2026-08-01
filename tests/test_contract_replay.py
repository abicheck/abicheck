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
    finding_key,
    relevance_map,
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
from abicheck.export_surface import compute_export_surface
from abicheck.finding_identity import report_finding_id as _finding_id
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
        """Keyed by the report's own ``finding_id``, over every stamped bucket.

        Built through ``finding_key`` rather than re-spelling the format:
        a duplicated key convention could drift from the receipt's while this
        test still passed (CodeRabbit review). The expected map covers the
        demoted bucket too, since ``compare`` stamps -- and the receipt
        records -- those as well.
        """
        result, ctx = _run()
        original = replay_original_decisions(ctx)
        assert original == {
            finding_key(c, _finding_id): c.contract_relevance
            for c in result.changes + result.out_of_surface_changes
            if c.contract_relevance is not None
        }

    def test_receipt_keys_separate_two_findings_on_one_symbol(self) -> None:
        """A coarse ``kind:symbol`` key would collapse these into one entry.

        Two findings of one kind on one symbol (e.g. two parameters of the
        same function) are distinct report findings with distinct
        ``finding_id`` values; the receipt must record a decision for each
        (Codex review, fresh evidence).
        """
        evidence = ContractEvidenceBlock()
        twins = [
            Change(
                kind=ChangeKind.PARAM_POINTER_LEVEL_CHANGED,
                symbol="api",
                description=f"parameter {i} pointer depth 1 -> 2",
            )
            for i in (0, 1)
        ]
        for change in twins:
            change.contract_relevance = ContractRelevance.IN_CONTRACT
        receipt = build_persisted_context(
            evidence,
            mode=ContractMode.PUBLIC,
            changes=twins,
            finding_id=_finding_id,
        ).decision_receipt
        assert len(receipt.relevance_by_finding) == 2

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
        replayed = reevaluate_from_evidence(ctx, result.changes, finding_id=_finding_id)
        comparison = compare_decisions(replay_original_decisions(ctx), replayed)
        assert comparison.is_sound
        assert comparison.agreed  # not vacuous: something actually matched

    def test_public_membership_is_recovered_from_the_persisted_graph(self) -> None:
        result, ctx = _run()
        decision = next(iter(reevaluate_from_evidence(ctx, result.changes).values()))
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "public_root_membership"
        assert decision.evidence_refs == ("public_header:old",)

    def test_forced_public_overlay_roots_survive_reevaluation(self) -> None:
        """An overlay-selected root is a ``public``-domain root on replay too.

        ADR-049 D2 counts "roots selected by explicit overlays" in the
        ``public`` domain. Reading only the ``public_header`` provider made an
        entity the live run kept *because* the user named it re-evaluate to
        ``UNKNOWN_UNRESOLVED``, even though the overlay's own evidence entry
        sits in the same block (Codex review, fresh evidence).
        """
        hidden = Function(
            name="hidden",
            mangled="hidden",
            return_type="Secret *",
            visibility=Visibility.HIDDEN,
            origin=ScopeOrigin.PRIVATE_HEADER,
        )
        secret = RecordType(name="Secret", kind="struct", fields=[])
        snap = AbiSnapshot(
            library="libdemo.so.1", version="1", functions=[hidden], types=[secret]
        )
        surf = compute_public_surface(snap)
        block = collect_contract_evidence(
            snap, snap, surf, surf, force_public_symbols={"hidden"}
        )
        roots = domain_roots(block, ContractMode.PUBLIC)
        assert "decl:hidden" in roots
        ctx = build_persisted_context(block, mode=ContractMode.PUBLIC)
        forced = Change(
            kind=ChangeKind.FUNC_REMOVED, symbol="hidden", description="hidden removed"
        )
        decision = next(iter(reevaluate_from_evidence(ctx, [forced]).values()))
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        # ...but *only* the named entity. `force_public_symbols` is a
        # per-finding override live -- it never widens the surface -- so what
        # the overlaid declaration's signature happens to reference is not
        # forced public along with it (Codex review, fresh evidence).
        receipt = build_decision_receipt(block, ContractMode.PUBLIC)
        assert "record:Secret" not in receipt.evaluated_type_closure
        secret_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Secret",
            description="Secret size changed",
        )
        collateral = next(iter(reevaluate_from_evidence(ctx, [secret_change]).values()))
        # The exact value matters: `PROVEN_OUT_OF_CONTRACT` would also be
        # "not IN_CONTRACT" while claiming more than a `public`-domain block
        # can support (CodeRabbit review).
        assert collateral.relevance is ContractRelevance.UNKNOWN_UNRESOLVED

    def test_post_manifest_roots_stay_exact(self) -> None:
        """A committed-export manifest matches verbatim, never through aliases.

        The live allowlist is compared against ``Change.symbol`` directly, so
        routing a bare ``foo`` through the alias tier would also root an
        unexported ``ns::foo`` that carries the same bare tail -- turning a
        live ``PROVEN_OUT_OF_CONTRACT`` into a replayed ``IN_CONTRACT``, the
        flip ``compare_decisions`` rejects (Codex review, fresh evidence).
        """
        cxx = Function(
            name="ns::foo",
            mangled="_ZN2ns3fooEv",
            return_type="int",
            visibility=Visibility.HIDDEN,
            origin=ScopeOrigin.PRIVATE_HEADER,
        )
        snap = AbiSnapshot(library="libdemo.so.1", version="1", functions=[cxx])
        surf = compute_public_surface(snap)
        block = collect_contract_evidence(
            snap, snap, surf, surf, public_surface_allowlist={"foo"}
        )
        roots = domain_roots(block, ContractMode.PUBLIC)
        assert "decl:_ZN2ns3fooEv" not in roots
        # The looser overlay keeps its own semantics: the same bare spelling
        # under `--public-symbol` still resolves through the alias tier.
        forced = collect_contract_evidence(
            snap, snap, surf, surf, force_public_symbols={"foo"}
        )
        assert "decl:_ZN2ns3fooEv" in domain_roots(forced, ContractMode.PUBLIC)

    def test_post_manifest_narrows_the_public_roots(self) -> None:
        """A manifest states the whole committed surface, not an addition.

        With public ``api`` and ``kept`` and a manifest naming only ``kept``,
        the live evaluator scopes ``api`` out; unioning the manifest into the
        header roots left ``decl:api`` rooted, replaying a live
        ``PROVEN_OUT_OF_CONTRACT`` as ``IN_CONTRACT`` (Codex review, fresh
        evidence).
        """
        fns = [
            Function(
                name=name,
                mangled=name,
                return_type="int",
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
            for name in ("api", "kept")
        ]
        snap = AbiSnapshot(library="libdemo.so.1", version="1", functions=fns)
        surf = compute_public_surface(snap)
        block = collect_contract_evidence(
            snap, snap, surf, surf, public_surface_allowlist={"kept"}
        )
        roots = domain_roots(block, ContractMode.PUBLIC)
        assert "decl:kept" in roots
        assert "decl:api" not in roots

    def test_overlay_rooted_decision_cites_the_overlay_record(self) -> None:
        """A decision resting on an overlay root names that overlay.

        Citing ``public_header`` for a declaration retained solely by
        ``--public-symbol`` names evidence the decision never used (Codex
        review, fresh evidence).
        """
        hidden = Function(
            name="hidden",
            mangled="hidden",
            return_type="int",
            visibility=Visibility.HIDDEN,
            origin=ScopeOrigin.PRIVATE_HEADER,
        )
        old = AbiSnapshot(library="libdemo.so.1", version="1", functions=[hidden])
        new = AbiSnapshot(library="libdemo.so.1", version="2")
        result = compare(
            old,
            new,
            contract_evaluation=True,
            force_public_symbols={"hidden"},
        )
        assert result.contract_context is not None
        removal = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="hidden",
            description="hidden() removed",
        )
        decision = next(
            iter(reevaluate_from_evidence(result.contract_context, [removal]).values())
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert "forced_public_symbols:old" in decision.evidence_refs

    def test_overlay_roots_do_not_leak_into_the_exports_domain(self) -> None:
        """Only ``public`` counts overlay-selected roots (ADR-049 D2).

        ``exports``' roots are the observed export table, and nothing else --
        an assertion by the user is not an observation of the binary.
        """
        hidden = Function(
            name="hidden",
            mangled="hidden",
            return_type="int",
            visibility=Visibility.HIDDEN,
            origin=ScopeOrigin.PRIVATE_HEADER,
        )
        elf = ElfMetadata(symbols=[ElfSymbol(name="other")])
        snap = AbiSnapshot(
            library="libdemo.so.1", version="1", functions=[hidden], elf=elf
        )
        surf = compute_public_surface(snap)
        exports = compute_export_surface(snap)
        block = collect_contract_evidence(
            snap,
            snap,
            surf,
            surf,
            exports_old=exports,
            exports_new=exports,
            force_public_symbols={"hidden"},
        )
        assert "decl:hidden" not in domain_roots(block, ContractMode.EXPORTS)

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

    def test_public_run_can_be_reevaluated_under_exports(self) -> None:
        """The P1 case: evidence collection must not depend on the run's mode.

        A comparison run under ``public`` still persists the export
        provider, so an ``exports`` re-evaluation resolves from the same
        block instead of answering ``UNKNOWN_UNRESOLVED`` for a pair whose
        export tables were fully observable (Codex review, fresh evidence).
        """
        result, ctx = _run(mode="public", with_exports=True)
        assert any(
            e.record.provider == "export_table" for e in ctx.contract_evidence.providers
        )
        replayed = reevaluate_from_evidence(
            ctx, result.changes, mode=ContractMode.EXPORTS
        )
        decision = next(iter(replayed.values()))
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.reason_code == "export_root_membership"

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

    def test_unavailable_provider_cannot_root_a_replayed_membership(self) -> None:
        """An unusable search proves no membership, graph attached or not.

        ``elf_only_mode`` leaves the header surface unresolvable, so the live
        evaluator answers ``UNKNOWN_UNRESOLVED``/``UNAVAILABLE`` -- but the
        provider entry still carries the declarations and type graph it had
        collected before bailing out, and the closure walk happily reached the
        entity through them and answered ``IN_CONTRACT``/``COMPLETE``. That is
        a strengthening ``compare_decisions`` rejects (Codex review, fresh
        evidence).
        """
        old, new = _pair()
        for snap in (old, new):
            snap.elf_only_mode = True
        result = compare(old, new, contract_evaluation=True, contract_mode="public")
        assert result.contract_context is not None
        assert result.changes
        replayed = reevaluate_from_evidence(
            result.contract_context, result.changes, finding_id=_finding_id
        )
        assert all(
            d.relevance is ContractRelevance.UNKNOWN_UNRESOLVED
            for d in replayed.values()
        )
        original = relevance_map(result.changes, _finding_id)
        assert original  # the live run did decide these findings
        assert compare_decisions(original, replayed).is_sound

    def test_exports_replay_keeps_qualified_symbols_exact(self) -> None:
        """An unexported ``ns::foo`` cannot borrow an exported C ``foo``.

        The live exports matcher passes ``allow_tail_fallback=False`` for
        exactly this collision. Offering the bare tail as a replay spelling
        reintroduced it one layer down: the unexported declaration resolved
        to the exported ``decl:foo`` node and landed in its closure (Codex
        review, fresh evidence).
        """
        elf = ElfMetadata(symbols=[ElfSymbol(name="foo")])
        snap = AbiSnapshot(
            library="libdemo.so.1",
            version="1",
            functions=[
                Function(
                    name="foo",
                    mangled="foo",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                Function(
                    name="ns::foo",
                    mangled="_ZN2ns3fooEv",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
            elf=elf,
        )
        surf = compute_public_surface(snap)
        exports = compute_export_surface(snap)
        ctx = build_persisted_context(
            collect_contract_evidence(
                snap, snap, surf, surf, exports_old=exports, exports_new=exports
            ),
            mode=ContractMode.EXPORTS,
        )
        removal = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="ns::foo",
            description="ns::foo() removed",
        )
        decision = next(
            iter(
                reevaluate_from_evidence(
                    ctx, [removal], mode=ContractMode.EXPORTS
                ).values()
            )
        )
        assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT

    def test_symbol_level_findings_ignore_their_caused_type(self) -> None:
        """Closure membership answers a *type*-level question only.

        A symbol-level finding on an unexported helper carries a
        ``caused_by_type`` that an exported signature may well reach --
        placing the helper in contract on that basis gates an internal
        function for merely using a public type, which the live evaluator's
        own ``type_scoped`` gate refuses (Codex review, fresh evidence).
        """
        elf = ElfMetadata(symbols=[ElfSymbol(name="api")])
        snap = AbiSnapshot(
            library="libdemo.so.1",
            version="1",
            functions=[
                Function(
                    name="api",
                    mangled="api",
                    return_type="Shared *",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                Function(
                    name="helper",
                    mangled="helper",
                    return_type="Shared *",
                    visibility=Visibility.HIDDEN,
                    origin=ScopeOrigin.PRIVATE_HEADER,
                ),
            ],
            types=[RecordType(name="Shared", kind="struct", fields=[])],
            elf=elf,
        )
        surf = compute_public_surface(snap)
        exports = compute_export_surface(snap)
        ctx = build_persisted_context(
            collect_contract_evidence(
                snap, snap, surf, surf, exports_old=exports, exports_new=exports
            ),
            mode=ContractMode.EXPORTS,
        )
        symbol_level = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="helper",
            caused_by_type="Shared",
            description="helper() return type changed",
        )
        decision = next(
            iter(
                reevaluate_from_evidence(
                    ctx, [symbol_level], mode=ContractMode.EXPORTS
                ).values()
            )
        )
        assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT

    def test_exported_alias_cannot_root_an_unexported_namesake(self) -> None:
        """A bare alias shared with a non-root proves nothing about either.

        The inverse collision of ``test_exports_replay_keeps_qualified_symbols
        _exact``: an exported ``ns::foo`` contributes the bare alias ``foo`` to
        the persisted graph, which an unrelated *unexported* C ``foo`` also
        answers to. ``compute_export_surface`` prunes exactly this alias from
        ``export_symbols``, so the live matcher answers
        ``PROVEN_OUT_OF_CONTRACT``; resolving the finding's own spelling
        through the alias tier here reached the exported node and replayed
        ``IN_CONTRACT`` (Codex review, fresh evidence).
        """
        elf = ElfMetadata(symbols=[ElfSymbol(name="_ZN2ns3fooEv")])
        snap = AbiSnapshot(
            library="libdemo.so.1",
            version="1",
            functions=[
                Function(
                    name="ns::foo",
                    mangled="_ZN2ns3fooEv",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                Function(
                    name="foo",
                    mangled="foo",
                    return_type="int",
                    visibility=Visibility.HIDDEN,
                    origin=ScopeOrigin.PRIVATE_HEADER,
                ),
            ],
            elf=elf,
        )
        surf = compute_public_surface(snap)
        exports = compute_export_surface(snap)
        ctx = build_persisted_context(
            collect_contract_evidence(
                snap, snap, surf, surf, exports_old=exports, exports_new=exports
            ),
            mode=ContractMode.EXPORTS,
        )
        removal = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="foo() removed",
        )
        decision = next(
            iter(
                reevaluate_from_evidence(
                    ctx, [removal], mode=ContractMode.EXPORTS
                ).values()
            )
        )
        assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT

    def test_all_mode_honors_a_persisted_post_manifest(self) -> None:
        """``all`` drops header-origin scoping, not an exact manifest.

        The live evaluator checks ``--post-manifest``'s own exclusion *before*
        its ``all``-mode shortcut, so a concrete export the manifest omits is
        ``PROVEN_OUT_OF_CONTRACT`` even under ``--contract all``. Returning
        ``IN_CONTRACT`` unconditionally here flipped that (Codex review, fresh
        evidence).
        """
        elf = ElfMetadata(symbols=[ElfSymbol(name="api")])
        old = AbiSnapshot(
            library="libdemo.so.1",
            version="1",
            functions=[
                Function(
                    name="api",
                    mangled="api",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            elf=elf,
        )
        new = AbiSnapshot(library="libdemo.so.1", version="2", elf=elf)
        result = compare(
            old,
            new,
            contract_evaluation=True,
            contract_mode="all",
            public_surface_allowlist=set(),
        )
        assert result.contract_context is not None
        findings = [*result.changes, *result.out_of_surface_changes]
        assert findings
        original = relevance_map(findings, _finding_id)
        assert ContractRelevance.PROVEN_OUT_OF_CONTRACT in original.values()
        replayed = reevaluate_from_evidence(
            result.contract_context,
            findings,
            mode=ContractMode.ALL,
            finding_id=_finding_id,
        )
        assert compare_decisions(original, replayed).is_sound

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

    def _orphan_context(self, *, mode: ContractMode, with_exports: bool = False):
        """A snapshot whose ``Orphan`` type is genuinely outside the closure."""
        elf = ElfMetadata(symbols=[ElfSymbol(name="api")]) if with_exports else None
        snap = AbiSnapshot(
            library="libdemo.so.1",
            version="1",
            functions=[
                Function(
                    name="api",
                    mangled="api",
                    return_type="Kept *",
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[
                RecordType(
                    name="Kept",
                    kind="struct",
                    fields=[],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
                RecordType(
                    name="Orphan",
                    kind="struct",
                    fields=[],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
            elf=elf,
        )
        surf = compute_public_surface(snap)
        exports = compute_export_surface(snap) if with_exports else None
        return build_persisted_context(
            collect_contract_evidence(
                snap, snap, surf, surf, exports_old=exports, exports_new=exports
            ),
            mode=mode,
        )

    def test_public_domain_never_proves_an_exclusion_from_the_graph(self) -> None:
        """Graph non-membership is not the positive proof ``public`` requires.

        ADR-049 Section 4.3's negative proof needs private/system-header
        *provenance* plus every stronger-or-equal provider having completed.
        The persisted block carries neither (it records declarations and a
        type graph, not per-entity header origin, and
        ``configuration_coverage`` is ``NOT_STARTED`` for every record this
        build writes) -- so concluding ``PROVEN_OUT_OF_CONTRACT`` here would
        claim more than the evidence supports, and this evaluator may only
        ever weaken the live decision (Codex review, fresh evidence).
        """
        ctx = self._orphan_context(mode=ContractMode.PUBLIC)
        decision = next(
            iter(reevaluate_from_evidence(ctx, [self._orphan_finding()]).values())
        )
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED

    def test_exports_domain_proves_an_exclusion_when_the_table_is_complete(
        self,
    ) -> None:
        """The one domain whose completeness *is* the ADR's terminal exclusion.

        An ``export_table`` record only reaches ``COMPLETE`` when
        ``ExportSurface.exclusion_is_provable`` does -- observed table,
        resolved root, every root typed, no unaccounted export, no unresolved
        type edge -- so absence from that closure is real evidence.
        """
        ctx = self._orphan_context(mode=ContractMode.EXPORTS, with_exports=True)
        decision = next(
            iter(reevaluate_from_evidence(ctx, [self._orphan_finding()]).values())
        )
        assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
        assert decision.reason_code == "terminal_authoritative_exclusion"

    def test_incomplete_export_provider_cannot_prove_an_exclusion(self) -> None:
        """A ``PARTIAL`` search proves no absence.

        Same snapshot as the test above, minus the export table: the type is
        just as unreachable, but the provider that would have to vouch for
        that never ran.
        """
        ctx = self._orphan_context(mode=ContractMode.EXPORTS, with_exports=False)
        decision = next(
            iter(reevaluate_from_evidence(ctx, [self._orphan_finding()]).values())
        )
        assert decision.relevance is ContractRelevance.UNKNOWN_UNRESOLVED

    def test_all_mode_cites_the_declaration_parse(self) -> None:
        """``all`` has no root provider, but its decision is not evidence-free.

        An empty ``evidence_refs`` carries the *non-entity* meaning; the live
        path cites the header provider here, so the replay must too
        (CodeRabbit review).
        """
        ctx = self._orphan_context(mode=ContractMode.ALL)
        decision = next(
            iter(
                reevaluate_from_evidence(
                    ctx, [self._orphan_finding()], mode=ContractMode.ALL
                ).values()
            )
        )
        assert decision.relevance is ContractRelevance.IN_CONTRACT
        assert decision.evidence_refs == ("public_header:old",)


class TestNodeIdentitySoundness:
    """Two graph-identity collisions, each end-to-end against a live run.

    Both are the same defect shape from opposite sides: a persisted node key
    that merges two declarations the live evaluator keeps apart, and an
    identity set that misses the spelling ``owner_class_of`` actually
    answers with. Asserted through ``compare_decisions`` rather than on the
    graph alone, because "strengthened" is the property that matters and it
    is only observable against the live decision.
    """

    @staticmethod
    def _replay(old: AbiSnapshot, new: AbiSnapshot, mode: str):
        result = compare(old, new, contract_evaluation=True, contract_mode=mode)
        assert result.contract_context is not None
        ctx = persisted_context_from_dict(
            json.loads(json.dumps(persisted_context_to_dict(result.contract_context)))
        )
        changes = list(result.changes) + list(result.out_of_surface_changes)
        assert changes
        return changes, compare_decisions(
            relevance_map(changes), reevaluate_from_evidence(ctx, changes)
        )

    def test_an_unmangled_private_overload_stays_out_of_the_public_contract(
        self,
    ) -> None:
        """The public root must not inherit a same-named overload's edges."""

        def _snap(version: str, bits: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="libdemo.so.1",
                version=version,
                functions=[
                    Function(
                        name="over",
                        mangled="",
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    ),
                    Function(
                        name="over",
                        mangled="",
                        return_type="int",
                        params=[Param(name="s", type="Secret *")],
                        visibility=Visibility.HIDDEN,
                        origin=ScopeOrigin.PRIVATE_HEADER,
                    ),
                ],
                types=[
                    RecordType(
                        name="Secret",
                        kind="struct",
                        size_bits=bits,
                        fields=[TypeField(name="x", type="int")],
                        origin=ScopeOrigin.PRIVATE_HEADER,
                    )
                ],
            )

        changes, comparison = self._replay(_snap("1", 64), _snap("2", 128), "public")
        assert [c.contract_relevance for c in changes] == [
            ContractRelevance.PROVEN_OUT_OF_CONTRACT
        ]
        assert comparison.is_sound
        assert comparison.strengthened == ()

    def test_a_member_level_finding_resolves_through_its_owner(self) -> None:
        """``symbol="Mode::B"`` names a member; the contract is about ``Mode``.

        ``_type_candidates`` strips the member for the owner-plus-member
        kinds, and the replay did not -- so an ``enum_member_added`` on an
        *exported* enum resolved to nothing and answered
        ``UNKNOWN_UNRESOLVED`` against a live ``IN_CONTRACT``. Sound (a
        weakening), but it made the replay useless for the whole member-level
        family. Found while adding the enum coverage this suite never had
        (self-review).
        """
        from abicheck.model import EnumMember, EnumType
        from abicheck.pe_metadata import PeExport, PeMetadata

        def _snap(version: str, members: list[tuple[str, int]]) -> AbiSnapshot:
            return AbiSnapshot(
                library="libdemo.so.1",
                version=version,
                functions=[
                    Function(
                        name="api",
                        mangled="api",
                        return_type="Mode",
                        visibility=Visibility.PUBLIC,
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    )
                ],
                enums=[
                    EnumType(
                        name="Mode",
                        qualified_name="ns::Mode",
                        members=[EnumMember(name=n, value=v) for n, v in members],
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    )
                ],
                pe=PeMetadata(exports=[PeExport(name="api", ordinal=1)]),
            )

        changes, comparison = self._replay(
            _snap("1", [("A", 0)]), _snap("2", [("A", 0), ("B", 1)]), "exports"
        )
        assert [c.symbol for c in changes] == ["Mode::B"]
        assert [c.contract_relevance for c in changes] == [
            ContractRelevance.IN_CONTRACT
        ]
        assert comparison.is_sound
        # The point of the fix: the replay reaches the same conclusion rather
        # than degrading to unresolved.
        assert comparison.agreed and comparison.weakened == ()

    def test_duplicate_records_sharing_one_identity_stay_unresolved(self) -> None:
        """One node in the graph, two entries in the live index.

        ``_index_surface_types`` counts record *objects* per name, so two
        ``Foo`` declarations make the spelling ambiguous live even though
        they share a canonical identity and therefore one persisted node.
        Re-deriving ambiguity from the graph could not see that collision at
        all, which is why the provider now persists the set outright (Codex
        review, fresh evidence).
        """

        def _snap(version: str, bits: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="libdemo.so.1",
                version=version,
                functions=[
                    Function(
                        name="api",
                        mangled="api",
                        return_type="int",
                        params=[Param(name="f", type="Foo *")],
                        visibility=Visibility.PUBLIC,
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    )
                ],
                types=[
                    RecordType(
                        name="Foo",
                        kind="struct",
                        size_bits=64,
                        fields=[TypeField(name="x", type="int")],
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    ),
                    RecordType(
                        name="Foo",
                        kind="struct",
                        size_bits=bits,
                        fields=[TypeField(name="y", type="int")],
                        origin=ScopeOrigin.PRIVATE_HEADER,
                    ),
                ],
            )

        changes, comparison = self._replay(_snap("1", 64), _snap("2", 128), "public")
        assert [c.contract_relevance for c in changes] == [
            ContractRelevance.UNKNOWN_UNRESOLVED
        ]
        assert comparison.is_sound
        assert comparison.strengthened == ()
        assert comparison.agreed

    def test_an_ambiguous_type_spelling_is_not_resolved_either_way(self) -> None:
        """Which of two colliding records the finding is about is unknown.

        With ``ns1::Foo`` reached from an export and ``ns2::Foo`` not, a
        finding naming the shared bare leaf resolves to both nodes. Live
        answers ``UNKNOWN_UNRESOLVED`` on exactly that ambiguity
        (``_confirmed_type_matches``); the replay used to take the reachable
        one and answer ``IN_CONTRACT`` (Codex review, fresh evidence).
        """
        from abicheck.pe_metadata import PeExport, PeMetadata

        def _snap(version: str, bits: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="libdemo.so.1",
                version=version,
                functions=[
                    Function(
                        name="api",
                        mangled="api",
                        return_type="int",
                        params=[Param(name="f", type="Foo *")],
                        visibility=Visibility.PUBLIC,
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    )
                ],
                types=[
                    RecordType(
                        name="Foo",
                        qualified_name="ns1::Foo",
                        kind="struct",
                        size_bits=64,
                        fields=[TypeField(name="x", type="int")],
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    ),
                    RecordType(
                        name="Foo",
                        qualified_name="ns2::Foo",
                        kind="struct",
                        size_bits=bits,
                        fields=[TypeField(name="y", type="int")],
                        origin=ScopeOrigin.PRIVATE_HEADER,
                    ),
                ],
                pe=PeMetadata(exports=[PeExport(name="api", ordinal=1)]),
            )

        changes, comparison = self._replay(_snap("1", 64), _snap("2", 128), "exports")
        assert [c.contract_relevance for c in changes] == [
            ContractRelevance.UNKNOWN_UNRESOLVED
        ]
        assert comparison.is_sound
        assert comparison.strengthened == ()
        # Not merely weakened into silence: the replay reached the same
        # "cannot tell which one" conclusion the live matcher did.
        assert comparison.agreed

    def test_a_reachable_record_does_not_place_a_same_named_function(self) -> None:
        """One spelling, two node kinds, two different questions.

        The live evaluator asks "is this symbol an export root" of
        declarations and "is this type in the closure" of types. This module
        resolves every spelling through one flat index, so with an exported
        ``api(foo *)``, a reachable ``struct foo`` and an unexported function
        ``foo``, the spelling ``foo`` landed on both ``decl:foo`` and
        ``record:foo`` -- and the reachable *record* placed the *function*
        removal ``IN_CONTRACT`` against a live ``PROVEN_OUT_OF_CONTRACT``
        (Codex review, fresh evidence).
        """
        from abicheck.pe_metadata import PeExport, PeMetadata

        pe = PeMetadata(exports=[PeExport(name="api", ordinal=1)])

        def _snap(version: str, *, with_helper: bool) -> AbiSnapshot:
            functions = [
                Function(
                    name="api",
                    mangled="api",
                    return_type="int",
                    params=[Param(name="f", type="foo *")],
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ]
            if with_helper:
                functions.append(
                    Function(
                        name="foo",
                        mangled="foo",
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    )
                )
            return AbiSnapshot(
                library="libdemo.so.1",
                version=version,
                functions=functions,
                types=[
                    RecordType(
                        name="foo",
                        kind="struct",
                        size_bits=64,
                        fields=[TypeField(name="x", type="int")],
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    )
                ],
                pe=pe,
            )

        changes, comparison = self._replay(
            _snap("1", with_helper=True), _snap("2", with_helper=False), "exports"
        )
        assert [c.contract_relevance for c in changes] == [
            ContractRelevance.PROVEN_OUT_OF_CONTRACT
        ]
        assert comparison.is_sound
        assert comparison.strengthened == ()
        assert comparison.agreed

    def test_a_qualified_owner_class_stays_in_the_export_contract(self) -> None:
        """The owner edge must survive a producer's bare/qualified split."""
        mangled = "_ZN2ns6Widget4drawEv"
        elf = ElfMetadata(symbols=[ElfSymbol(name=mangled)])

        def _snap(version: str, bits: int) -> AbiSnapshot:
            return AbiSnapshot(
                library="libdemo.so.1",
                version=version,
                functions=[
                    Function(
                        name="ns::Widget::draw",
                        mangled=mangled,
                        return_type="int",
                        visibility=Visibility.PUBLIC,
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    )
                ],
                types=[
                    RecordType(
                        name="Widget",
                        qualified_name="ns::Widget",
                        kind="struct",
                        size_bits=bits,
                        fields=[TypeField(name="x", type="int")],
                        origin=ScopeOrigin.PUBLIC_HEADER,
                    )
                ],
                elf=elf,
            )

        changes, comparison = self._replay(_snap("1", 64), _snap("2", 128), "exports")
        assert [c.contract_relevance for c in changes] == [
            ContractRelevance.IN_CONTRACT
        ]
        assert comparison.is_sound
        assert comparison.strengthened == ()
        # Not vacuously sound: the replay reached the same conclusion, it did
        # not merely decline to decide.
        assert comparison.agreed


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

    def test_not_applicable_transition_is_a_disagreement(self) -> None:
        """``NOT_APPLICABLE`` is not a point on the strength scale.

        Ranking it against an entity-axis value would be meaningless, so it
        gets its own bucket -- but it still fails ``is_sound``: both
        evaluators share one ``_NOT_APPLICABLE_KIND_SLUGS`` set, so a
        disagreement means the receipt and this build classify the same
        ``ChangeKind`` differently (CodeRabbit review).
        """
        into = compare_decisions(
            {"a": ContractRelevance.IN_CONTRACT},
            {"a": ContractRelevance.NOT_APPLICABLE},
        )
        assert into.disagreed == ("a",)
        assert into.strengthened == ()
        assert not into.is_sound
        out_of = compare_decisions(
            {"a": ContractRelevance.NOT_APPLICABLE},
            {"a": ContractRelevance.UNKNOWN_UNRESOLVED},
        )
        assert out_of.disagreed == ("a",)
        assert not out_of.is_sound

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

    def test_receipt_carries_its_own_version_and_fails_closed(self) -> None:
        """The receipt is versioned independently of its sibling blocks.

        Its *keys* are their own contract (the per-finding identity a
        consumer correlates against a report's ``finding_id``), so its shape
        can change while observations and configuration do not -- and a
        counter newer than this build must refuse to replay, like every other
        block's (CodeRabbit review: the block shipped with no version at all).
        """
        _result, ctx = _run()
        assert ctx.decision_receipt.schema_version >= 1
        raw = persisted_context_to_dict(ctx)
        raw["decision_receipt"]["schema_version"] = 99
        future = persisted_context_from_dict(raw)
        with pytest.raises(UnsupportedSchemaVersionError):
            replay_original_decisions(future)

    def test_empty_receipt_is_valid(self) -> None:
        context = build_persisted_context(
            ContractEvidenceBlock(), mode=ContractMode.PUBLIC
        )
        assert context.decision_receipt == DecisionReceiptBlock()
