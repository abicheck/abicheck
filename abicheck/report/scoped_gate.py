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

"""Native scoped-gate (``--used-by``/``--required-symbol(s)``) JSON construction.

**Workstream D-S1 (vision-api-abi-evolution.md "D. Optional prebuilt-consumer
lifecycle") reverted this module's original design.** It used to make the
JSON report describe the *scoped* gate (the one the process used to exit on
under ``--used-by``/``--required-symbol``) *instead of* the full-library one
-- swapping ``verdict``/``severity``/``run_outcome``/``summary`` aside to
``full_*`` siblings and putting the scoped values in their place, and zeroing
every out-of-scope finding's ``gate_contribution``. A supplied consumer's own
result is a confirmed/potential/unresolved per-consumer impact assessment
reported **beside** the full-library compatibility result; it never
substitutes for it or narrows what the run's own gate/exit code reports. So
:func:`apply_scoped_gate` no longer swaps any of ``verdict``/``severity``/
``run_outcome``/``summary`` -- those stay exactly what an unscoped comparison
would have produced. It still folds ``used_by``/``required_symbol_contract``
(the per-app/per-host breakdown) and any scoped-only findings
(``scope_diff_to_app``/``scope_diff_to_required_symbols`` synthesize these
fresh, e.g. ``PE_ORDINAL_RETARGETED``, ``CONSUMER_REQUIRED_SYMBOL_REMOVED``)
into the payload, since those are real, additional facts worth surfacing --
just never in place of the global result.

Applied by :func:`apply_scoped_gate` from inside
:func:`abicheck.reporter_contract_blocks.render_json_with_side_facts` --
before the payload is rendered, never as a render -> ``json.loads`` ->
patch -> ``json.dumps`` pass over already-serialized text.

``cli_compare_fold.py``'s ``_ScopedFold`` used to own this logic (as
``into_json``/``_swap_in_scoped_severity``/``_swap_in_scoped_run_outcome``/
``_fold_findings_into_changes``/``_fold_findings_into_stat_summary``) and
apply it that way. It moved here once ``reporter.to_json``/``to_stat_json``
themselves gained a real ``contract_evaluation`` parameter, threaded from
the CLI the same way ``severity_config``/``show_only`` already were -- the
other inputs the fold needs (``used_by``, ``required_symbols``, and every
``scoped_*`` ``DiffResult`` attribute ``cli_helpers_compare.py``/
``cli_compare_helpers.py`` stamp before rendering) were already plain
attributes read off *result* via ``getattr``, so nothing about *how* they
reach this fold changed -- only *when* the fold itself runs.
``cli_compare_fold._fold_scoped_compat_into_text``'s JSON branch is now a
no-op passthrough: ``to_json``/``to_stat_json`` already return the fully
scoped-aware payload by the time that function ever sees it.

Lives in this package, not as a new flat ``abicheck/reporter_*.py`` sibling:
``architecture/modules.yaml``'s ``frozen_root_families`` closes the
``reporter_`` flat-namespace family to new members (ADR-061), so new
report-construction logic goes to its real responsibility-package owner --
``report/``.

ADR-063 T10: this module used to resolve ``abicheck.reporter`` via
``importlib`` (``_reporter()``) purely to reach six small per-change
helpers still defined in ``reporter.py``/``reporter_markdown.py``
(``_change_to_dict``, ``_add_entries_to_root_causes``, ``_finding_id``,
``_root_cause_key_and_display``, ``root_cause_for_change``,
``_resolve_scoped_gate_findings``) -- a static import back to ``reporter``
would have closed a real cycle (``reporter`` imports
``reporter_contract_blocks``, which imports this module's
:func:`apply_scoped_gate`). Rather than keep bridging the cycle with
``importlib``, the caller that already holds all six names --
``reporter_contract_blocks.render_json_with_side_facts``, via
``reporter.py``'s own module-level re-exports -- now passes them down as
data (:class:`ScopedGateChangeHelpers`), which makes this module a pure
leaf with no dependency on ``reporter``/``reporter_markdown`` at all,
static or dynamic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import date


@dataclass(frozen=True)
class ScopedGateChangeHelpers:
    """The ``reporter``/``reporter_markdown`` per-change helpers
    :func:`apply_scoped_gate` needs to turn a scoped-only ``Change`` into a
    report row and register its root-cause grouping key.

    Supplied by the caller (``reporter_contract_blocks.
    render_json_with_side_facts``, via ``reporter.py``'s own
    :data:`~abicheck.reporter._SCOPED_GATE_HELPERS`) rather than resolved
    here via ``importlib`` -- see this module's own docstring for why.
    """

    change_to_dict: Callable[..., dict[str, Any]]
    add_entries_to_root_causes: Callable[..., None]
    finding_id: Callable[[Any], str]
    root_cause_key_and_display: Callable[..., tuple[str, str]]
    root_cause_for_change: Callable[..., Any]
    resolve_scoped_gate_findings: Callable[
        [Any, Any, str | None], tuple[Any, Any, bool, Any]
    ]


# Maps a rendered change's "severity" label (report_model.VERDICT_PRESENTATION,
# and the "breaking"/"compatible" literals a missing-contract entry uses) to
# the summary-block key it contributes to -- shared by apply_scoped_gate's two
# post-fold summary recomputes.
_SEVERITY_TO_SUMMARY_BUCKET = {
    "breaking": "breaking",
    "api_break": "source_breaks",
    "risk": "risk_changes",
    "compatible": "compatible_additions",
}


def _scoped_verdict_value(result: Any) -> Any:
    """The scoped verdict as its plain value (an enum's ``.value``)."""
    scoped_verdict = getattr(result, "scoped_verdict", None)
    return getattr(scoped_verdict, "value", scoped_verdict)


def _scoped_gate_findings(
    result: Any,
    severity_config: Any,
    show_only: str | None,
    helpers: ScopedGateChangeHelpers,
) -> tuple[Any, Any, bool, Any]:
    """The scoped-only changes, missing-contract labels, and gate blocking
    decision this run's scoped gate actually rests on."""
    return helpers.resolve_scoped_gate_findings(result, severity_config, show_only)


def apply_scoped_gate(
    payload: dict[str, Any],
    result: Any,
    *,
    helpers: ScopedGateChangeHelpers,
    severity_config: Any = None,
    show_only: str | None = None,
    contract_evaluation: bool = False,
    today: date | None = None,
) -> None:
    """Fold ``--used-by``/``--required-symbol(s)`` *enrichment* into *payload*.

    No-op unless *result* carries a stamped ``used_by``/``required_symbols``
    (set by ``cli_helpers_compare.py`` before rendering ever starts) --
    every other caller of ``to_json``/``to_stat_json`` is unaffected.

    Workstream D-S1: ``verdict``/``severity``/``run_outcome``/``summary`` are
    never touched here -- they already describe the full-library
    compatibility result the process's own exit code comes from, exactly as
    they would for a run with no consumer supplied at all. This function only
    *adds* to the payload: the per-app/per-host ``used_by``/
    ``required_symbol_contract`` breakdown, a purely informational
    ``consumer_scope`` block naming what the consumer-scoped assessment would
    have concluded on its own, and any scoped-only findings folded into
    ``changes``/``summary`` as additional, non-gating facts (see
    :func:`_fold_findings_into_changes` and its ``--stat`` sibling). *today*,
    forwarded to both, keeps a scoped-only finding's verdict agreeing with an
    already-frozen ``ReportEnvelope`` (Codex review, fresh evidence).
    """
    used_by = getattr(result, "used_by", None)
    required_symbols = getattr(result, "required_symbols", None)
    if used_by is None and required_symbols is None:
        return
    if used_by is not None:
        payload["used_by"] = used_by
        # Workstream D-S1: "N of M consumers affected" -- only meaningful for
        # --used-by (a --required-symbol run has exactly one host contract,
        # not a population of consumers to summarize).
        consumer_impact_summary = getattr(result, "consumer_impact_summary", None)
        if consumer_impact_summary is not None:
            payload["consumer_impact_summary"] = consumer_impact_summary
    if required_symbols is not None:
        payload["required_symbol_contract"] = required_symbols
    consumer_scope = _consumer_scope_block(result)
    if consumer_scope is not None:
        payload["consumer_scope"] = consumer_scope
    # Scoped-only changes (e.g. PE_ORDINAL_RETARGETED, synthesized fresh
    # per app/host by scope_diff_to_app/scope_diff_to_required_symbols)
    # and uncovered missing-contract labels are real, additional facts about
    # the supplied consumer(s) that never land in `result.changes` -- folded
    # into `changes` here too, purely additively, so a JSON consumer sees
    # them (mirrors sarif.to_sarif/junit_report._build_testsuite's identical
    # fold-in). They enrich the report; they never move the already-rendered
    # `verdict`/`severity`/`run_outcome`/`summary` off the full-library
    # result (workstream D-S1).
    changes_list = payload.get("changes")
    summary = payload.get("summary")
    if isinstance(changes_list, list):
        _fold_findings_into_changes(
            payload,
            changes_list,
            summary,
            result,
            helpers=helpers,
            severity_config=severity_config,
            show_only=show_only,
            contract_evaluation=contract_evaluation,
            today=today,
        )
    elif isinstance(summary, dict):
        _fold_findings_into_stat_summary(
            payload,
            summary,
            result,
            helpers=helpers,
            severity_config=severity_config,
            show_only=show_only,
            today=today,
        )


def _consumer_scope_block(result: Any) -> dict[str, Any] | None:
    """The purely informational ``consumer_scope`` block for *payload*.

    States what a consumer-only assessment would have concluded (the verdict/
    exit code ``--used-by``/``--required-symbol`` used to substitute for the
    process's own gate, pre workstream D-S1) without ever feeding it back
    into ``verdict``/``severity``/``run_outcome``/the process exit code. A
    reader that wants "what does this consumer see" reads this block instead
    of a swapped-in top-level field.
    """
    scoped_verdict_value = _scoped_verdict_value(result)
    if scoped_verdict_value is None:
        return None
    block: dict[str, Any] = {
        "verdict": scoped_verdict_value,
        "scope": getattr(result, "gate_scope", None),
        "note": (
            "Informational: this consumer's own assessment. It does not "
            "affect this run's compatibility verdict, severity, run_outcome, "
            "or exit code -- those always describe the full-library result."
        ),
    }
    scoped_exit_code = getattr(result, "scoped_exit_code", None)
    if scoped_exit_code is not None:
        block["exit_code"] = scoped_exit_code
        block["exit_code_scheme"] = getattr(result, "scoped_exit_code_scheme", None)
    return block


def _fold_findings_into_changes(
    payload: dict[str, Any],
    changes_list: list[Any],
    full_summary: Any,
    result: Any,
    *,
    helpers: ScopedGateChangeHelpers,
    severity_config: Any,
    show_only: str | None,
    contract_evaluation: bool,
    today: date | None = None,
) -> None:
    """Append the scoped gate's own findings to the ``changes`` array.

    Each appended entry also registers its root-cause grouping key, and the
    summary counts are recomputed afterwards from the now-complete array.
    """
    from ..policy.evidence_status import EvidenceStatus, ReachabilityState
    from ..reporter_markdown import (
        apply_show_only,
        root_cause_evidence_lookup_for_changes,
    )

    _add_entries_to_root_causes = helpers.add_entries_to_root_causes
    _change_to_dict = helpers.change_to_dict
    _finding_id = helpers.finding_id
    _root_cause_key_and_display = helpers.root_cause_key_and_display
    root_cause_for_change = helpers.root_cause_for_change

    eff_sets = result._effective_kind_sets()
    scoped_only, missing_labels, blocks, missing_kind = _scoped_gate_findings(
        result, severity_config, show_only, helpers
    )
    # G29 Phase 6 follow-up (Codex review): the scoped-only entries built
    # below never routed through _add_changes_block's own evidence
    # lookup (that ran earlier, over `changes` alone, before this
    # fold-in even has `scoped_only` in hand) -- correlate over the same
    # combined set sarif.to_sarif uses (`result.changes` filtered by
    # --show-only, plus `scoped_only`), so a scoped-only finding that is
    # itself a RootCauseCorrelator group member (e.g. a
    # CONSUMER_REQUIRED_SYMBOL_REMOVED sibling of a regular
    # FUNC_REMOVED) carries the same root_cause_evidence JSON's regular
    # `changes[]` entries do.
    primary_changes = list(result.changes)
    if show_only:
        primary_changes = apply_show_only(
            primary_changes,
            show_only,
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )
    rc_evidence = root_cause_evidence_lookup_for_changes(primary_changes + scoped_only)
    # Workstream D-S1: every full-diff finding's own `gate_contribution`
    # (ADR-049 D1 -- the number that actually gates the *global*
    # compatibility axis under `--contract`) is left exactly as computed for
    # the full-library result. A finding a supplied consumer does not happen
    # to use still gated the process the same way it would have without
    # `--used-by`/`--required-symbol` -- consumer scoping is enrichment, not
    # a second, narrower gate, so it no longer zeroes any full-diff finding's
    # contribution here (reverting the earlier "the scoped gate is what the
    # run exits on" design this fold used to implement).
    # G29 Phase 3 slice 3 (ADR-052, Codex review): these synthetic
    # entries are appended to `changes` after `_to_json_root_cause`
    # already grouped `result.changes` into `root_causes` -- without
    # tracking their own grouping key/root here too, a scoped run
    # whose only gated issue is one of these would report a nonempty
    # `changes` array next to `root_cause_count: 0`, losing the only
    # gate failure for a root-cause consumer.
    # Mirrors _to_json_root_cause's own referenced_causes computation
    # (Codex review): a symbol only groups when some caused_by_type
    # actually names it, not merely because it's shared.
    referenced_causes: frozenset[str] = frozenset(
        str(entry.get("caused_by_type"))
        for entry in changes_list
        if isinstance(entry, dict) and entry.get("caused_by_type")
    ) | frozenset(c.caused_by_type for c in scoped_only if c.caused_by_type)
    root_cause_entries: list[tuple[str, str, dict[str, object]]] = []
    for c in scoped_only:
        entry = _change_to_dict(
            c,
            policy=result.policy or "strict_abi",
            kind_sets=eff_sets,
            policy_file=result.policy_file,
            # ADR-049 D1's per-finding `gate_contribution` is only
            # truthful if it is computed under the scheme the run
            # exits on -- a scoped-only finding does reach the scoped
            # gate, so this is not one of the always-0 ledger cases.
            severity_config=severity_config,
            # Codex review: a scoped-only change (PE_ORDINAL_RETARGETED,
            # CONSUMER_REQUIRED_SYMBOL_REMOVED) is proven by the real
            # consumer's own import table,
            # not by an artifact-level library diff -- evidence_status_for_change
            # would otherwise report "artifact_proven" purely from the kind's
            # BREAKING/RISK category, same as appcompat_to_json's own
            # CONSUMER_PROVEN override for this exact finding shape.
            evidence_status_override=EvidenceStatus.CONSUMER_PROVEN,
            # G29 Phase 3 follow-up (ADR-052): feeds
            # impact_assessment.root_cause_id -- None (the singleton
            # case) for a scoped-only change with no real correlation
            # signal, same rule root_cause_lookup_for_changes applies
            # elsewhere; the *entries* below never skip a singleton,
            # since --report-mode root-cause's own grouping is
            # deliberately complete, unlike this per-finding field.
            root_cause=root_cause_for_change(c, referenced_causes=referenced_causes),
            root_cause_evidence=rc_evidence.get(_finding_id(c)),
            today=today,
        )
        changes_list.append(entry)
        key, root_display = _root_cause_key_and_display(
            c.caused_by_type,
            c.symbol,
            c.kind.value,
            _finding_id(c),
            referenced_causes=referenced_causes,
        )
        root_cause_entries.append((key, root_display, entry))
    for label in missing_labels:
        from ..workflows.findings import (
            missing_contract_finding,
            report_canonical_finding_id,
            report_finding_id,
        )

        identity = missing_contract_finding(missing_kind, label)
        entry = {
            "kind": missing_kind,
            "symbol": label,
            "description": identity.description,
            # A missing-contract label has no backing Change, so this
            # entry never routed through `_change_to_dict` and carried
            # no id at all -- leaving the decision this same loop
            # stamps below unjoinable to ADR-049's own
            # `decision_receipt`, which is keyed by exactly this id
            # (Codex review, fresh evidence).
            "finding_id": report_finding_id(identity),
            # Same gap as finding_id above, for canonical_finding_id
            # (schema 2.35): when a missing-contract label is the
            # only blocking finding, it was the one entry in the
            # whole response missing the field every other changes[]
            # entry carries uniformly (Codex review, fresh evidence).
            "canonical_finding_id": report_canonical_finding_id(identity),
            "old_value": None,
            "new_value": None,
            "severity": "breaking" if blocks else "compatible",
            "relevant_to_gate": True,
            "blocks_gate": blocks,
            # G29 Phase 3 slice 1 (ADR-052, Codex review): a
            # missing-contract label has no backing Change for
            # _change_to_dict/assess_change to read (unlike the
            # scoped_only loop above, which already routes
            # through _change_to_dict and picks up
            # reachability_state for free). reachability_state is
            # "always present" for every changes[] entry per D3
            # -- a missing symbol/version is a hard absence, not
            # a reachability question, so UNKNOWN (not proven
            # either way) is the honest, consistent value here.
            "reachability_state": ReachabilityState.UNKNOWN.value,
        }
        if contract_evaluation:
            # ADR-049 D1's full per-finding shape, not just the
            # relevance: this entry is frequently the *only* blocking
            # finding in the response, so omitting its decision and
            # contribution left the one row that explains the exit
            # code as the least complete one (Codex review).
            from ..contract_scoped_promotion import (
                missing_contract_gate_contribution,
                stamp_missing_contract_entry,
            )

            stamp_missing_contract_entry(
                entry,
                gate_contribution=missing_contract_gate_contribution(
                    severity_config, blocks
                ),
            )
        changes_list.append(entry)
        # A missing-contract label has no caused_by_type; its
        # `symbol` (the label) only becomes a *grouping* key if some
        # other finding's caused_by_type names it, same as any other
        # symbol-only fallback (see referenced_causes above). There is
        # no real Change/finding_id to disambiguate an unreferenced
        # label by, so the label itself (always unique per label)
        # fills that role instead.
        key, root_display = _root_cause_key_and_display(
            None,
            label,
            missing_kind,
            label,
            referenced_causes=referenced_causes,
        )
        root_cause_entries.append((key, root_display, entry))
    _add_entries_to_root_causes(payload, root_cause_entries)
    # `summary` was computed from `result.changes` *before* scoped_only/
    # missing_labels were appended to `changes` above -- purely additive: the
    # scoped-only/missing-contract entries are real, additional consumer
    # findings, so their own bucket counts and `total_changes` are folded in
    # on top of the already-correct full-library counts (workstream D-S1: no
    # `full_summary`/`summary` swap -- `summary` always described, and
    # continues to describe, the full-library result plus this addition).
    # `binary_compatibility_pct`/`affected_pct` describe the full library
    # surface and are left as-is -- recomputing them for the consumer-scoped
    # subset would need old_symbol_count context this fold-in doesn't have.
    if isinstance(full_summary, dict):
        bucket_counts = {
            "breaking": 0,
            "source_breaks": 0,
            "risk_changes": 0,
            "compatible_additions": 0,
        }
        for entry in changes_list:
            severity = entry.get("severity") if isinstance(entry, dict) else None
            bucket = (
                _SEVERITY_TO_SUMMARY_BUCKET.get(severity, "")
                if isinstance(severity, str)
                else None
            )
            if bucket:
                bucket_counts[bucket] += 1
        payload["summary"] = {
            **full_summary,
            **bucket_counts,
            "total_changes": len(changes_list),
        }


def _fold_findings_into_stat_summary(
    payload: dict[str, Any],
    full_summary: dict[str, Any],
    result: Any,
    *,
    helpers: ScopedGateChangeHelpers,
    severity_config: Any,
    show_only: str | None,
    today: date | None = None,
) -> None:
    """Add a supplied consumer's own scoped-only findings to a ``--stat`` summary.

    Codex review: `--format json --stat` (to_stat_json) emits a
    summary-only payload with no `changes` array at all, so the branch
    above -- gated on `isinstance(changes_list, list)` -- never runs for it.
    There's no per-change list to recompute bucket counts from here, so
    instead each scoped-only/missing-contract synthetic finding's own
    contribution is added on top of the already-correct full-library counts
    (workstream D-S1: purely additive, no `full_summary`/`summary` swap).
    """
    from ..policy.evidence_status import EvidenceStatus

    _change_to_dict = helpers.change_to_dict

    scoped_only, missing_labels, blocks, _missing_kind = _scoped_gate_findings(
        result, severity_config, show_only, helpers
    )
    if scoped_only or missing_labels:
        eff_sets = result._effective_kind_sets()
        added_counts = {
            "breaking": 0,
            "source_breaks": 0,
            "risk_changes": 0,
            "compatible_additions": 0,
        }
        for c in scoped_only:
            entry = _change_to_dict(
                c,
                policy=result.policy or "strict_abi",
                kind_sets=eff_sets,
                policy_file=result.policy_file,
                severity_config=severity_config,
                evidence_status_override=EvidenceStatus.CONSUMER_PROVEN,
                today=today,
            )
            severity = entry.get("severity")
            bucket = (
                _SEVERITY_TO_SUMMARY_BUCKET.get(severity)
                if isinstance(severity, str)
                else None
            )
            if bucket:
                added_counts[bucket] += 1
        for _label in missing_labels:
            bucket = _SEVERITY_TO_SUMMARY_BUCKET["breaking" if blocks else "compatible"]
            added_counts[bucket] += 1
        payload["summary"] = {
            **full_summary,
            **{k: full_summary.get(k, 0) + v for k, v in added_counts.items()},
            "total_changes": (
                full_summary.get("total_changes", 0)
                + len(scoped_only)
                + len(missing_labels)
            ),
        }
