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

ADR-061 Phase 2 item 5's last open piece: the fold that makes a JSON report
describe the *scoped* gate (the one the process actually exits on under
``--used-by``/``--required-symbol``) instead of the full-library one now
runs as a plain ``dict`` mutation, applied by :func:`apply_scoped_gate` from
inside :func:`abicheck.reporter_contract_blocks.render_json_with_side_facts`
-- before the payload is rendered, never as a render -> ``json.loads`` ->
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
``report/`` -- even though it must locally import the still-flat, legacy
``reporter``/``reporter_markdown`` (both already ``layers.report.
legacy_paths`` members, i.e. the same architectural layer as this file, so
the import is same-layer and not a direction violation).
"""

from __future__ import annotations

from typing import Any, cast

from ..contract_gating import zero_scoped_out_gate_contributions


def _reporter() -> Any:
    """``abicheck.reporter``, resolved via ``importlib`` rather than a
    static ``from ..reporter import ...`` (Codex review, fresh evidence):
    ``reporter.py`` imports ``reporter_contract_blocks.py``, which imports
    this module's :func:`apply_scoped_gate` -- a static import back to
    ``reporter`` here would close that into a real cycle
    ``check_ai_readiness.py``'s ``import-cycle-growth`` gate flags (it walks
    every ``ast.Import``/``ast.ImportFrom`` node regardless of function
    scope). ``importlib.import_module`` is the same escape hatch
    ``workflows/render.py``'s own ``_service_render()`` already uses for an
    identical reason -- see that module's docstring. Every call site below
    ``cast``s its own result, since a module resolved this way carries no
    static attribute types."""
    import importlib

    return importlib.import_module("..reporter", __package__)

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
    result: Any, severity_config: Any, show_only: str | None
) -> tuple[Any, Any, bool, Any]:
    """The scoped-only changes, missing-contract labels, and gate blocking
    decision this run's scoped gate actually rests on."""
    # cast: resolved via importlib (see _reporter()'s docstring), so mypy
    # sees this call's return as Any rather than
    # `_resolve_scoped_gate_findings`'s own concrete, fully-typed signature.
    return cast(
        "tuple[Any, Any, bool, Any]",
        _reporter()._resolve_scoped_gate_findings(result, severity_config, show_only),
    )


def apply_scoped_gate(
    payload: dict[str, Any],
    result: Any,
    *,
    severity_config: Any = None,
    show_only: str | None = None,
    contract_evaluation: bool = False,
) -> None:
    """Fold ``--used-by``/``--required-symbol(s)`` scoping into *payload*.

    No-op unless *result* carries a stamped ``used_by``/``required_symbols``
    (set by ``cli_helpers_compare.py`` before rendering ever starts) --
    every other caller of ``to_json``/``to_stat_json`` is unaffected. The
    full-library verdict/severity/run_outcome/summary move to their
    ``full_*`` siblings and the scoped ones take their place, so the body
    agrees with the exit code the process is about to use. See this
    module's docstring for why this now runs pre-render.
    """
    used_by = getattr(result, "used_by", None)
    required_symbols = getattr(result, "required_symbols", None)
    if used_by is None and required_symbols is None:
        return
    payload["full_verdict"] = payload.get("verdict")
    scoped_verdict_value = _scoped_verdict_value(result)
    if scoped_verdict_value is not None:
        payload["verdict"] = scoped_verdict_value
    if used_by is not None:
        payload["used_by"] = used_by
    if required_symbols is not None:
        payload["required_symbol_contract"] = required_symbols
    _swap_in_scoped_severity(payload, result)
    _swap_in_scoped_run_outcome(payload, result, scoped_verdict_value)
    # Scoped-only changes (e.g. PE_ORDINAL_RETARGETED, synthesized fresh
    # per app/host by scope_diff_to_app/scope_diff_to_required_symbols)
    # and uncovered missing-contract labels are relevant to the scoped
    # gate but never land in `result.changes` -- without folding them
    # into `changes` here too, a --used-by/--required-symbol run whose
    # only gated issue is one of these reports an empty `changes` array
    # despite a nonzero scoped exit code/verdict, so a JSON consumer
    # (e.g. the GitHub Action's `--on changes` PR-comment gate, which
    # buckets purely off this array) sees nothing to explain the failure
    # and silently skips posting (Codex review, mirrors
    # sarif.to_sarif/junit_report._build_testsuite's identical fold-in).
    changes_list = payload.get("changes")
    full_summary = payload.get("summary")
    if isinstance(changes_list, list):
        _fold_findings_into_changes(
            payload,
            changes_list,
            full_summary,
            result,
            severity_config=severity_config,
            show_only=show_only,
            contract_evaluation=contract_evaluation,
        )
    elif isinstance(full_summary, dict):
        _fold_findings_into_stat_summary(
            payload,
            full_summary,
            result,
            severity_config=severity_config,
            show_only=show_only,
        )


def _swap_in_scoped_severity(payload: dict[str, Any], result: Any) -> None:
    """Move the full-library severity block aside for the scoped one.

    Under a severity scheme, `severity.exit_code`/`blocking` describe
    the *full-library* gate decision -- but the process actually exits
    with the scoped exit code computed above (Codex review): without
    this, a scoped-compatible run that exits 0 could still carry
    `severity.exit_code: 4`/`blocking: true` in its own JSON body, the
    opposite of what the command that produced it just did. Mirrors the
    verdict/full_verdict swap above -- the full-library breakdown moves
    to `full_severity`, `severity` becomes the scoped gate.
    """
    scoped_exit_code = getattr(result, "scoped_exit_code", None)
    scoped_exit_code_scheme = getattr(result, "scoped_exit_code_scheme", None)
    severity_block = payload.get("severity")
    if (
        scoped_exit_code is None
        or scoped_exit_code_scheme != "severity"
        or not isinstance(severity_block, dict)
    ):
        return
    payload["full_severity"] = severity_block
    # `categories.*.count` must also move to the scoped tally --
    # otherwise a scoped-compatible `exit_code: 0` could still show
    # an error-level `categories.abi_breaking.count > 0` left over
    # from the full-library breakdown, contradicting the now-scoped
    # `blocking`/`blocking_categories` fields above (Codex review).
    scoped_counts = getattr(result, "scoped_severity_counts", None) or {}
    full_categories = severity_block.get("categories")
    scoped_categories = (
        {
            cat: (
                {**info, "count": scoped_counts.get(cat, 0)}
                if isinstance(info, dict)
                else info
            )
            for cat, info in full_categories.items()
        }
        if isinstance(full_categories, dict)
        else full_categories
    )
    payload["severity"] = {
        **severity_block,
        "categories": scoped_categories,
        "exit_code": scoped_exit_code,
        "blocking": scoped_exit_code != 0,
        "blocking_categories": list(
            getattr(result, "scoped_blocking_categories", ()) or ()
        ),
    }


def _swap_in_scoped_run_outcome(
    payload: dict[str, Any], result: Any, scoped_verdict_value: Any
) -> None:
    """Move the full-library ``run_outcome`` block aside for the scoped one.

    ``run_outcome`` (ADR-063 Phase 7) is built by ``report.run_outcome.
    run_outcome_dict_for_diff_result`` before any ``--used-by``/
    ``--required-symbol`` scoping is applied, so it describes the
    full-library compatibility gate by construction -- the identical
    problem `_swap_in_scoped_severity` above already exists to fix for
    the legacy ``severity`` block, on the newer structured axis (Codex
    review): without this, a scoped-compatible run that exits 0 could
    still carry a blocking ``run_outcome.gate`` describing an unrelated
    full-library break, and since `GateInfo.from_report_data` prefers
    the structured `run_outcome` over `severity`/`exit_code`, an
    aggregate reading this report would fail on a target whose actual,
    scoped process exit passed. Uses
    ``result.scoped_compatibility_contribution`` -- the *pre*-coverage/
    analysis-assurance-floor scoped exit code
    ``cli_compare_helpers.run_compare`` stamps before folding those
    orthogonal axes into ``result.scoped_exit_code`` -- so this stays
    the pure compatibility-gate value ``run_outcome.gate`` represents,
    under both the legacy and severity exit-code schemes alike (unlike
    `_swap_in_scoped_severity` above, which only fires under the
    severity scheme, since only that scheme has a `severity` block to
    swap in the first place).
    """
    run_outcome = payload.get("run_outcome")
    scoped_compat = getattr(result, "scoped_compatibility_contribution", None)
    if not isinstance(run_outcome, dict) or scoped_compat is None:
        return
    from .not_comparable import policy_gate_decision_for_exit_code

    payload["full_run_outcome"] = run_outcome
    payload["run_outcome"] = {
        **run_outcome,
        "compatibility": scoped_verdict_value,
        "gate": policy_gate_decision_for_exit_code(scoped_compat).value,
    }


def _fold_findings_into_changes(
    payload: dict[str, Any],
    changes_list: list[Any],
    full_summary: Any,
    result: Any,
    *,
    severity_config: Any,
    show_only: str | None,
    contract_evaluation: bool,
) -> None:
    """Append the scoped gate's own findings to the ``changes`` array.

    Each appended entry also registers its root-cause grouping key, and the
    summary counts are recomputed afterwards from the now-complete array.
    """
    from ..checker_policy import EvidenceStatus, ReachabilityState
    from ..reporter_markdown import (
        apply_show_only,
        root_cause_evidence_lookup_for_changes,
    )

    _rep = _reporter()
    _add_entries_to_root_causes = _rep._add_entries_to_root_causes
    _change_to_dict = _rep._change_to_dict
    _finding_id = _rep._finding_id
    _root_cause_key_and_display = _rep._root_cause_key_and_display
    root_cause_for_change = _rep.root_cause_for_change

    eff_sets = result._effective_kind_sets()
    scoped_only, missing_labels, blocks, missing_kind = _scoped_gate_findings(
        result, severity_config, show_only
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
    # ADR-049 D1: `gate_contribution` is defined as the number that
    # *actually* gated, and under --used-by/--required-symbol the
    # scoped gate is what the run exits on -- this fold is where it
    # replaces the primary verdict and severity block. A full-diff
    # finding the selected consumer does not use contributes nothing
    # to that gate, so leaving its full-library number in place
    # published `gate_contribution: 4` on a run that exited 0 (Codex
    # review, reproduced with a removal outside the required-symbol
    # contract). Only entries that already carry the field are
    # touched, so a run without --contract is unaffected.
    # Called here, *before* the scoped-only/missing-contract fold-in
    # below: those are the scoped gate's own findings and only the
    # ones tracked in `scoped_relevant_finding_ids` would survive it.
    zero_scoped_out_gate_contributions(payload, result)
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
    # `summary` above was computed from result.changes *before*
    # scoped_only/missing_labels were appended to `changes` here --
    # so a scoped run whose only gating issue is one of these
    # synthetic entries could report e.g. verdict "BREAKING" next to
    # summary.total_changes: 0, an internally contradictory JSON
    # body (audit finding: scoped CLI JSON summary can be stale).
    # Move the pre-scoped summary to `full_summary` (mirrors the
    # verdict/full_verdict and severity/full_severity swap above)
    # and recompute the count buckets `summary` reports from the
    # now-complete `changes` array. `binary_compatibility_pct`/
    # `affected_pct` describe the full library surface and are left
    # as-is -- recomputing them for the scoped subset would need
    # old_symbol_count context this fold-in doesn't have.
    if isinstance(full_summary, dict):
        payload["full_summary"] = full_summary
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
    severity_config: Any,
    show_only: str | None,
) -> None:
    """Adjust a ``--stat`` payload's summary-only counts for the scoped gate.

    Codex review: `--format json --stat` (to_stat_json) emits a
    summary-only payload with no `changes` array at all, so the
    branch above -- gated on `isinstance(changes_list, list)` --
    never runs for it. Without this, a `--stat --used-by`/
    `--required-symbol` run still swaps `verdict` to the scoped
    gate result (above) but leaves `summary` as the stale
    full-library counts and never adds `full_summary`: a scoped
    BREAKING verdict sitting next to unrelated full-library
    summary numbers, the exact contradiction this fold-in exists
    to remove. There's no per-change list to recompute bucket
    counts from here, so instead add each scoped-only/missing-
    contract synthetic finding's own contribution on top of the
    already-correct full-library counts.
    """
    from ..checker_policy import EvidenceStatus

    _change_to_dict = _reporter()._change_to_dict

    scoped_only, missing_labels, blocks, _missing_kind = _scoped_gate_findings(
        result, severity_config, show_only
    )
    if scoped_only or missing_labels:
        payload["full_summary"] = full_summary
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
