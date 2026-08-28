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

"""ADR-049 section 4.3's explicit-scope evidence tier, and what it promotes.

:mod:`abicheck.contract_evaluation` answers "is this finding inside the
declared contract" from *snapshot* evidence -- header surfaces, export
tables, the type graph. This module answers the one question that evidence
cannot: a run given a concrete consumer (``--used-by``) or an explicit
entrypoint list (``--required-symbol``) has been *told* what the contract is,
by the caller, and ADR-049 section 4.3 item 1 ranks that above anything the
two snapshots can show on their own.

So everything here runs *after* ``compare()`` returned, over the collections
a scoping pass built, and only ever promotes -- to ``IN_CONTRACT``, with the
one reason code below. The promotion is not cosmetic since Phase 7 made
relevance authoritative: it decides whether compatibility policy scores the
finding, so each function here also carries the consequences with it (the
finding's own decision, the verdict it may raise, the number it contributes
to the gate, and the receipt row that records all three).

Split out of :mod:`abicheck.contract_evaluation` (CodeRabbit review) because
it is one concern with one entry point per caller, and because that module
was within thirty lines of the 2000-line hard cap. The dependency runs one
way -- this module reads the reason-code set it shares with the evaluator,
and nothing there imports back.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .checker_policy import Verdict
from .contract_evaluation import _NOT_APPLICABLE_KIND_SLUGS
from .contract_relevance_types import ContractAssurance, ContractRelevance

# ADR-049 section 4.3 item 1's strongest public-evidence tier: "explicit
# required symbol, exact contract/ABI manifest, package symbols metadata,
# or concrete consumer import/relocation/recorded entrypoint." Every entry
# in used_by/required_symbols scoping's own relevant/scoped-only/missing-
# label collections *is*, by construction, one of those -- a
# consumer-import-derived finding (appcompat.scope_diff_to_app) or an
# explicit required-symbol/entrypoint finding
# (appcompat.scope_diff_to_required_symbols) -- so it is authoritative
# IN_CONTRACT evidence on its own, regardless of what a from-scratch
# header-surface evaluation would conclude. Shared by mcp_server.py's
# abi_compare tool and cli_compare_helpers.py/cli_compare_fold.py's
# compare CLI command -- both apply the identical override to the
# identical class of finding (Codex review: originally private to
# mcp_server.py only, leaving the CLI's own --contract +
# --used-by/--required-symbol combination permanently unstamped).
#: The reason code stamp_explicit_scope_contract_evaluation always uses --
#: hoisted so the two assignment branches below can't drift apart (CodeRabbit
#: review), and already a registered key in CONTRACT_REASON_CODES.
_EXPLICIT_SCOPE_REASON_CODE = "explicit_consumer_or_required_symbol_evidence"


def stamp_explicit_scope_contract_evaluation(c: Any) -> None:
    """Stamp *c* (a ``Change`` or a plain dict entry) ``IN_CONTRACT`` using
    ADR-049's explicit consumer/required-symbol evidence tier.

    ``used_by``/``required_symbols`` scoping
    (``appcompat.scope_diff_to_app``/``scope_diff_to_required_symbols``) can
    produce fresh ``Change`` objects (e.g. a synthetic
    ``consumer_required_symbol_removed`` or a PE ordinal-retarget finding),
    and a missing-symbol/missing-entrypoint label is a plain dict built
    directly by the caller -- neither ever passes through
    ``checker._apply_contract_evaluation_shadow`` (the only place inside
    ``compare()`` that stamps a shadow decision), so both stay permanently
    unstamped even when the caller opted into ``contract_evaluation=True``.

    A generic header-surface evaluation (``evaluate_change_contract_relevance``)
    would be *wrong* here, not merely weaker: these findings are exactly the
    "explicit required symbol ... or concrete consumer import" evidence
    ADR-049 section 4.3 ranks as the *strongest* public-contract proof --
    stronger than, and independent of, header-derived public root
    membership. A binary-only ``used_by`` snapshot has no resolvable header
    surface at all, so running these through the header-surface path would
    misclassify authoritative in-contract evidence as ``UNKNOWN_UNRESOLVED``.

    Accepts either a ``Change`` (attribute-set) or a plain ``dict`` (the
    missing-label shape) via duck typing. Unconditionally overwrites any
    existing decision -- including one ``compare()``'s own
    ``_apply_contract_evaluation_shadow`` already stamped onto a ``Change``
    that is *also* present in the caller's already-classified finding set
    (the ordinary case: a scoping pass's own relevance set is not limited to
    *fresh* findings). That header-surface-derived decision can be a real,
    weaker misclassification (e.g. ``UNKNOWN_UNRESOLVED`` on a binary-only
    snapshot with no resolvable header surface at all) that this call's own
    stronger, authoritative evidence must override, not merely fill in when
    absent.

    EXCEPTION -- non-entity kinds: ``_is_relevant_to_app``/
    ``scope_diff_to_required_symbols`` deliberately include a handful of
    *loader/deployment* findings in the relevant set for reasons that have
    nothing to do with matching a symbol/entrypoint the consumer actually
    references -- ``SONAME_CHANGED`` when the consumer records the old
    SONAME in ``DT_NEEDED``, and ``COMPAT_VERSION_CHANGED`` (Mach-O)
    unconditionally for every consumer. This module already classifies both
    as ``NOT_APPLICABLE`` (``_NOT_APPLICABLE_KIND_SLUGS``, ADR-049 D2's
    "non-entity" column) since neither is about a specific function/
    variable/type a consumer's code references. Overriding those to
    ``IN_CONTRACT`` here would be a false contract decision, not a stronger
    one -- so a ``Change`` whose kind is in that non-entity set is left
    alone (whatever ``_apply_contract_evaluation_shadow`` already computed
    for it, i.e. ``NOT_APPLICABLE``) instead of being overwritten.
    """
    is_dict = isinstance(c, dict)
    if not is_dict:
        kind = getattr(c, "kind", None)
        kind_value = getattr(kind, "value", None)
        if kind_value in _NOT_APPLICABLE_KIND_SLUGS:
            return

    # The evidence this decision rests on is the run's own explicit
    # consumer/required-symbol input, not a per-side provider record in a
    # `ContractEvidenceBlock` -- this function runs after `compare()` already
    # returned, in a caller that holds no block at all. A run-level reference
    # is what `contract_evidence_collect.validate_decision_evidence` accepts
    # for exactly this case, so the finding still carries evidence rather
    # than an empty ledger entry that would read as "no provider consulted"
    # (the non-entity meaning).
    from .contract_evidence_collect import EXPLICIT_SCOPE_EVIDENCE_REF

    # ADR-049 D1: relevance decides whether compatibility policy runs, so a
    # promotion to IN_CONTRACT must carry the status with it. Leaving a
    # stale NOT_EVALUATED behind would be worse than not stamping at all --
    # `contract_gating.is_evaluated` reads the stamped status first, so the
    # gate would keep excluding a finding this call just proved a real
    # consumer depends on.
    from .contract_relevance_types import CompatibilityEvaluationStatus

    if is_dict:
        c["contract_relevance"] = ContractRelevance.IN_CONTRACT.value
        c["contract_reason_code"] = _EXPLICIT_SCOPE_REASON_CODE
        c["contract_assurance"] = ContractAssurance.COMPLETE.value
        c["contract_evidence_refs"] = [EXPLICIT_SCOPE_EVIDENCE_REF]
        c["compatibility_evaluation_status"] = (
            CompatibilityEvaluationStatus.EVALUATED.value
        )
    else:
        c.contract_relevance = ContractRelevance.IN_CONTRACT
        c.contract_reason_code = _EXPLICIT_SCOPE_REASON_CODE
        c.contract_assurance = ContractAssurance.COMPLETE
        c.contract_evidence_refs = (EXPLICIT_SCOPE_EVIDENCE_REF,)
        c.compatibility_evaluation_status = CompatibilityEvaluationStatus.EVALUATED


def recompute_verdict_after_promotion(
    result: Any, *, policy: str | None = None, policy_file: Any = None
) -> None:
    """Re-derive ``result.verdict`` once an explicit-scope promotion changed
    which findings compatibility policy scores.

    The scoped promotion mutates the *same* ``Change`` objects the full
    ``DiffResult`` holds -- by design, so the rendered finding shows the
    consumer-proven ``IN_CONTRACT`` decision. Since ADR-049 Phase 7 that
    mutation also moves the finding back onto the compatibility axis, and
    ``DiffResult``'s verdict buckets are computed *lazily* from the current
    field state while ``verdict`` was frozen before the promotion. Left
    alone, one report said ``full_verdict: NO_CHANGE`` beside
    ``full_summary.breaking: 1`` (Codex review, confirmed via
    ``--required-symbol`` under ``--contract exports``).

    Recomputing is the right direction rather than reverting the mutation:
    ADR-049 §4.3 ranks explicit consumer/required-symbol evidence *above*
    the header/export-derived conclusion, so a finding it proves in-contract
    genuinely belongs in the full verdict too. A no-op when nothing was
    promoted onto the axis, and for every run that never opted into contract
    evaluation.
    """
    from .checker_policy import compute_verdict
    from .contract_gating import is_evaluated
    from .reclassify import _VERDICT_ORDER

    changes = getattr(result, "changes", None)
    if not changes:
        return
    scored = [c for c in changes if is_evaluated(c)]
    effective_policy = policy or getattr(result, "policy", None) or "strict_abi"
    pf = (
        policy_file if policy_file is not None else getattr(result, "policy_file", None)
    )
    recomputed = (
        pf.compute_verdict(scored)
        if pf is not None
        else compute_verdict(scored, policy=effective_policy)
    )
    # Combined monotonically with the standing verdict, never assigned over
    # it. Two reasons, and the second is the one that bites:
    #
    # 1. A promotion only ever moves findings *onto* the compatibility axis
    #    (NOT_EVALUATED -> EVALUATED), never off it, so the correct new
    #    verdict is by construction no weaker than the old one. Taking the
    #    stronger of the two is the exact answer, not a safety margin.
    # 2. `checker.compare()` computes its verdict from `kept +
    #    verdict_redundant` -- non-rename redundant findings score too -- and
    #    that set is *not* recoverable from the returned `DiffResult`:
    #    `redundant_changes` is `redundant + opaque_filtered`, and
    #    `opaque_filtered` is deliberately excluded from the verdict while
    #    rename halves are excluded as well. Recomputing from `changes` alone
    #    therefore dropped a redundant child's stronger verdict and *lowered*
    #    `full_verdict` from BREAKING to COMPATIBLE on any unrelated scoped
    #    promotion (Codex review, reproduced). Keeping the stronger of the
    #    two preserves whatever those findings contributed, without this
    #    function having to reconstruct a set the result no longer
    #    distinguishes.
    if _VERDICT_ORDER.index(recomputed) > _VERDICT_ORDER.index(result.verdict):
        result.verdict = recomputed


def stamp_scoped_changes(
    changes: Iterable[Any],
    *,
    policy: str | None = None,
    policy_file: Any = None,
) -> None:
    """Promote an explicitly-scoped finding set, decisions included.

    The promotion half of :func:`stamp_scoped_result_findings`, callable on a
    bare list — which is what the ``--used-by``/``--required-symbol`` paths
    need, because they compute their *scoped exit code* from one app's or
    host's relevant changes before the aggregated ``result.scoped_*``
    collections those paths later expose even exist.

    Ordering matters since ADR-049 Phase 7 made relevance authoritative: an
    explicit consumer/required-symbol contract is §4.3's strongest
    in-contract evidence, so a finding it covers must be promoted *before*
    the scoped gate reads it. Promoting afterwards left the gate scoring a
    weaker `UNKNOWN_UNRESOLVED` the header/export path had reached, which
    could report a scoped exit of 0 beside a rendered finding whose own
    decision says BREAKING (Codex review, confirmed via `--required-symbol`
    under `--contract exports`).
    """
    promoted = list(changes)
    for change in promoted:
        stamp_explicit_scope_contract_evaluation(change)
    _record_scoped_compatibility_decisions(
        promoted, policy=policy, policy_file=policy_file
    )


def _record_scoped_compatibility_decisions(
    promoted: list[Any], *, policy: str | None, policy_file: Any
) -> None:
    """Recompute each promoted finding's own compatibility decision.

    A promotion to ``IN_CONTRACT`` can turn a finding compare() left
    ``NOT_EVALUATED`` into an evaluated one, and an evaluated finding owes
    the report its decision (ADR-049 D1) — computed from the same
    per-finding classifier the gate uses, rather than left as the stale
    ``None`` the earlier decision wrote, which would read as "policy
    declined to score a finding it did score".

    Guarded by the same ``is_evaluated`` predicate
    ``ContractEvaluationStage.record_compatibility_decisions`` uses, so the
    two recorders cannot disagree about what a decision means. Today every
    finding reaching here is evaluated -- ``stamp_explicit_scope_...``
    either promotes it to ``IN_CONTRACT`` or returns early only for
    ``_NOT_APPLICABLE_KIND_SLUGS``, which is itself an evaluated relevance --
    but that is an argument about the current early-return set, not a
    property of this function; a future kind class added there with a
    non-evaluated relevance would otherwise publish a decision beside
    ``compatibility_evaluation_status: NOT_EVALUATED`` (CodeRabbit review).
    """
    if not promoted:
        return
    from .contract_gating import is_evaluated
    from .severity import effective_verdict_for_change

    for change in promoted:
        if isinstance(change, dict) or getattr(change, "kind", None) is None:
            continue
        if not is_evaluated(change):
            change.compatibility_decision = None
            continue
        change.compatibility_decision = effective_verdict_for_change(
            change, policy=policy, policy_file=policy_file
        )


def missing_contract_gate_contribution(severity_config: Any, blocks: bool) -> int:
    """What a missing-contract label contributes to the exit code.

    One derivation for the CLI and the MCP tool, mirroring the floor
    ``_scoped_exit_code`` applies in each: under a severity gate it is
    ``severity.missing_contract_exit_code``; under the legacy scheme a
    missing required symbol/version/entrypoint is a hard break, so it is the
    same ``4`` that scheme maps ``BREAKING`` to. ``0`` when the resolved gate
    does not block on it at all (e.g. ``--severity-preset info-only``).
    """
    from .severity import missing_contract_exit_code

    if not blocks:
        return 0
    if severity_config is None:
        return 4
    return missing_contract_exit_code(severity_config)


def stamp_missing_contract_entry(
    entry: dict[str, Any], *, gate_contribution: int
) -> None:
    """Complete ADR-049 D1's canonical shape on a missing-contract entry.

    A missing ``--required-symbol``/``--used-by`` label has no backing
    ``Change`` -- callers synthesize a plain dict and emit it directly in
    their ``changes`` array -- so nothing gives it the per-finding decision
    fields an ordinary finding gets from ``reporter._change_to_dict``. It
    got the relevance and the evaluation status but neither the decision nor
    the contribution, which is conspicuous precisely because this entry is
    often the *only* blocking finding in the response (Codex review).

    The decision is ``BREAKING`` by construction: the label names a symbol,
    version or entrypoint the consumer requires and the new library does not
    provide. *gate_contribution* is supplied by the caller because only it
    knows the resolved scheme -- ``severity.missing_contract_exit_code``
    under a severity gate, the legacy floor otherwise -- so the number here
    is the one that actually applied rather than a re-derivation.
    """
    stamp_explicit_scope_contract_evaluation(entry)
    entry["compatibility_decision"] = Verdict.BREAKING.value
    entry["gate_contribution"] = gate_contribution


def stamp_scoped_result_findings(
    result: Any, *, finding_id: Callable[[Any], str]
) -> None:
    """Stamp every ``--used-by``/``--required-symbol``-relevant finding on
    *result* with the explicit-scope ``IN_CONTRACT`` decision
    (:func:`stamp_explicit_scope_contract_evaluation`).

    Covers both collections a scoping pass (``_apply_used_by_scoping``/
    ``_apply_required_symbol_scoping`` in ``cli_compare_helpers.py``, or the
    equivalent inline logic in ``mcp_server.py``'s ``abi_compare``) can
    populate on *result*: an existing ``result.changes`` entry matched by
    ``scoped_relevant_finding_ids`` (the ordinary case, e.g. a plain
    ``FUNC_REMOVED`` both the full diff and the scoped gate already agree
    on), and every fresh ``Change`` in ``scoped_only_changes`` (synthesized
    by the scoping pass itself, e.g. a PE ordinal retarget, and never added
    to ``result.changes``).

    Hoisted here (CodeRabbit review) because the *traversal* over these two
    collections was independently hand-copied in both
    ``cli_compare_helpers.py`` and ``mcp_server.py`` -- exactly the kind of
    duplication that let one of the two call sites go unstamped for a real
    PR cycle before being caught by review. A no-op when *result* carries
    neither collection (no scoping was applied) or when calling this
    without a caller having opted into ``contract_evaluation=True`` in the
    first place -- callers are expected to gate the call on that flag
    themselves, mirroring every other function in this module.

    *finding_id* is injected rather than imported here: the real
    implementation lives in ``reporter_markdown.py``/``reporter.py``, both
    of which import ``checker.py`` at module level, and ``checker.py``
    already imports this module (function-locally, in
    ``_apply_contract_evaluation_shadow``) -- importing it back from here
    would close a real ``checker -> contract_evaluation -> reporter[...] ->
    checker`` cycle (caught by the ``import-cycle-growth`` AI-readiness
    gate). Every caller already has its own ``_finding_id`` import in scope
    for other reasons.
    """
    relevant_ids = getattr(result, "scoped_relevant_finding_ids", None) or frozenset()
    promoted: list[Any] = []
    if relevant_ids:
        promoted.extend(c for c in result.changes if finding_id(c) in relevant_ids)
    promoted.extend(getattr(result, "scoped_only_changes", ()) or ())
    # Idempotent by construction, and deliberately still called even though
    # the `--used-by`/`--required-symbol` paths now promote their own set
    # earlier (before their scoped exit code): this is the one place that
    # covers `result.changes` entries and the scoped-only overlay together,
    # and it is what refreshes the receipt below.
    stamp_scoped_changes(
        promoted,
        policy=getattr(result, "policy", None),
        policy_file=getattr(result, "policy_file", None),
    )
    refresh_contract_receipt(result, finding_id=finding_id)


def _missing_contract_findings(result: Any) -> list[Any]:
    """Every ``scoped_missing_labels`` entry, in ``Change``-compatible shape.

    One place that turns a run's missing-contract labels into the identity
    every report format's synthesized entry carries, so the receipt and the
    emitted findings key alike.
    """
    from .finding_identity import missing_contract_finding, missing_contract_kind

    kind = missing_contract_kind(getattr(result, "gate_scope", None))
    return [
        missing_contract_finding(kind, label)
        for label in getattr(result, "scoped_missing_labels", ()) or ()
    ]


def refresh_contract_receipt(result: Any, *, finding_id: Callable[[Any], str]) -> None:
    """Re-key *result*'s persisted decision receipt to its current decisions.

    ``checker.compare`` freezes the ``decision_receipt`` block before
    returning, but ``--used-by``/``--required-symbol`` scoping runs *after*
    that: it overwrites already-recorded findings with the stronger
    explicit-scope ``IN_CONTRACT`` decision and synthesizes fresh
    ``scoped_only_changes`` the receipt never saw. Left alone, the persisted
    receipt disagrees with the report emitted beside it, and
    :func:`~abicheck.contract_replay.replay_original_decisions` reproduces
    decisions that were never the run's own (Codex review, fresh evidence).

    Merges rather than rebuilds: the receipt covers every finding
    ``compare()`` classified, including the suppressed/redundant/out-of-surface
    ones that never reach ``result.changes``, so recomputing the map from the
    emitted collections alone would silently drop them. Only what a scoping
    pass can touch is re-derived.

    ``scoped_missing_labels`` is covered too, and needs its own step: a
    missing contract member has no backing ``Change``, so it is in neither
    collection above, yet every report format synthesizes an entry for it and
    stamps that entry ``IN_CONTRACT``. Without this the emitted finding had a
    decision and the receipt had no matching key, so
    :func:`~abicheck.contract_replay.replay_original_decisions` could not
    reproduce the whole report (Codex review, fresh evidence). It is keyed
    through :func:`~abicheck.finding_identity.missing_contract_finding`, the
    same identity the synthesized entries carry.

    A no-op when the caller never opted into ``contract_evaluation=True`` (no
    context to refresh), and deliberately tolerant of a ``contract_context``
    that is not a :class:`PersistedContractContext`: the field is typed
    ``object`` on ``DiffResult`` precisely so ``checker_types`` need not
    import this block's module.
    """
    from dataclasses import replace

    from .contract_context import relevance_map
    from .contract_evidence import PersistedContractContext

    ctx = getattr(result, "contract_context", None)
    if not isinstance(ctx, PersistedContractContext):
        return
    touched = list(result.changes) + list(
        getattr(result, "scoped_only_changes", ()) or ()
    )
    merged = dict(ctx.decision_receipt.relevance_by_finding)
    merged.update(relevance_map(touched, finding_id))
    merged.update(
        {
            finding_id(missing): ContractRelevance.IN_CONTRACT
            for missing in _missing_contract_findings(result)
        }
    )
    result.contract_context = replace(
        ctx,
        decision_receipt=replace(ctx.decision_receipt, relevance_by_finding=merged),
    )
