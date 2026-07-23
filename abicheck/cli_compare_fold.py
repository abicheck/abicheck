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

"""Scoped-gate (``--used-by``/``--required-symbol(s)``) summary fold-in for
the ``compare`` command's rendered report text.

Size-split from :mod:`abicheck.cli_compare_helpers` (AI-readiness file-size
cap). These functions only reach leaf report-formatting modules
(:mod:`abicheck.reporter`, :mod:`abicheck.reporter_markdown`,
:mod:`abicheck.severity`, :mod:`abicheck.checker_policy`) -- unlike
:func:`abicheck.cli_compare_helpers._fold_evidence_depth_into_json` (which
stayed in ``cli_compare_helpers.py``), none of them touch
``cli_dump_helpers``/``cli_buildsource_helpers``, so this module does not
join the CLI-registration import-cycle SCC those do (CLAUDE.md "What NOT to
do": extending ``IMPORT_CYCLE_ALLOWLIST`` needs an ADR, so the split
boundary was chosen specifically to avoid needing one).
"""

from __future__ import annotations

from typing import Any


def _resolve_scoped_gate_findings(
    result: Any, severity_config: Any, show_only: str | None,
) -> tuple[list[Any], list[str], bool, str]:
    """Resolve the scoped-only ``Change``s and missing-contract labels relevant
    to the ``--used-by``/``--required-symbol`` gate, deduped against
    ``result.changes`` and filtered by ``--show-only``.

    Factored out of the JSON branch below so markdown/text/review output can
    render the identical actionable findings instead of only a bare count
    (Codex review: a scoped run whose only gated issue was a missing contract
    member or a scoped-only change like ``PE_ORDINAL_RETARGETED`` didn't name
    either one in the default text report, unlike JSON/SARIF/JUnit).

    Returns ``(scoped_only_changes, missing_labels, blocks, missing_kind)``.
    """
    from .reporter import _finding_id, apply_show_only
    from .reporter_markdown import ShowOnlyFilter
    from .severity import missing_contract_exit_code

    existing_ids = {_finding_id(c) for c in result.changes}
    eff_sets = result._effective_kind_sets()
    scoped_only = list(getattr(result, "scoped_only_changes", ()) or ())
    if show_only and scoped_only:
        scoped_only = apply_show_only(
            scoped_only,
            show_only,
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )
    scoped_only = [c for c in scoped_only if _finding_id(c) not in existing_ids]

    gate_scope = getattr(result, "gate_scope", None)
    missing_kind = (
        "used_by_missing_symbol" if gate_scope == "used_by"
        else "required_symbol_missing"
    )
    blocks = (
        severity_config is None
        or missing_contract_exit_code(severity_config) != 0
    )
    # A missing-contract label has no backing Change/ChangeKind, so it can't
    # run through apply_show_only -- but --show-only's severity dimension
    # still applies: without this, a --show-only run that excludes breaking
    # findings would still include a blocking missing-contract entry the
    # filter was meant to exclude (Codex review, mirrors the identical
    # sarif.to_sarif fix). Element/action tokens don't cleanly apply to "a
    # symbol is simply absent", so only the severity dimension is checked.
    missing_severity_label = "breaking" if blocks else "compatible"
    show_only_severities = (
        ShowOnlyFilter.parse(show_only).severities if show_only else frozenset()
    )
    missing_labels = list(
        getattr(result, "scoped_missing_labels", ()) or ()
        if not show_only_severities or missing_severity_label in show_only_severities
        else ()
    )
    return scoped_only, missing_labels, blocks, missing_kind


# Maps a rendered change's "severity" label (report_model.VERDICT_PRESENTATION,
# and the "breaking"/"compatible" literals _resolve_scoped_gate_findings' missing-
# contract entries use) to the summary-block key it contributes to -- shared by
# _fold_scoped_compat_into_text's post-append summary recompute.
_SEVERITY_TO_SUMMARY_BUCKET = {
    "breaking": "breaking",
    "api_break": "source_breaks",
    "risk": "risk_changes",
    "compatible": "compatible_additions",
}


def _fold_scoped_compat_into_text(
    text: str, fmt: str, result: Any, severity_config: Any = None,
    show_only: str | None = None,
) -> str:
    """Fold ``--used-by``/``--required-symbol(s)`` summaries into the rendered text.

    JSON gets the summaries as real keys (round-tripped through the existing
    payload); other text-based formats get a small appended section. Binary/
    structured formats (sarif, junit, html) are left untouched -- the full
    verdict they already carry stays authoritative for those consumers.

    *severity_config* (when the run used a severity scheme) decides whether a
    synthesized missing-contract entry is itself blocking, mirroring
    ``sarif._missing_contract_result``/``junit_report``'s severity-aware
    missing-contract handling.

    *show_only*, when given, filters ``scoped_only_changes`` before they are
    folded into the JSON ``changes`` array -- ``to_json`` already filtered
    ``result.changes`` by the same tokens upstream, so leaving the
    scoped-only fold-in unfiltered would let a `--show-only` run re-surface a
    finding it explicitly excluded (Codex review, mirrors the identical
    ``sarif.to_sarif`` fix). Pass ``None`` (the default) for a render that is
    deliberately always-unfiltered, e.g. the ``--secondary-format`` render,
    which ignores the primary format's own ``--show-only``.
    """
    used_by = getattr(result, "used_by", None)
    required_symbols = getattr(result, "required_symbols", None)
    if used_by is None and required_symbols is None:
        return text
    scoped_verdict = getattr(result, "scoped_verdict", None)
    scoped_verdict_value = getattr(scoped_verdict, "value", scoped_verdict)

    if fmt == "json":
        import json

        try:
            payload = json.loads(text)
        except ValueError:
            return text
        payload["full_verdict"] = payload.get("verdict")
        if scoped_verdict_value is not None:
            payload["verdict"] = scoped_verdict_value
        if used_by is not None:
            payload["used_by"] = used_by
        if required_symbols is not None:
            payload["required_symbol_contract"] = required_symbols
        # Under a severity scheme, `severity.exit_code`/`blocking` describe
        # the *full-library* gate decision -- but the process actually exits
        # with the scoped exit code computed above (Codex review): without
        # this, a scoped-compatible run that exits 0 could still carry
        # `severity.exit_code: 4`/`blocking: true` in its own JSON body, the
        # opposite of what the command that produced it just did. Mirrors the
        # verdict/full_verdict swap above -- the full-library breakdown moves
        # to `full_severity`, `severity` becomes the scoped gate.
        scoped_exit_code = getattr(result, "scoped_exit_code", None)
        scoped_exit_code_scheme = getattr(result, "scoped_exit_code_scheme", None)
        severity_block = payload.get("severity")
        if (
            scoped_exit_code is not None
            and scoped_exit_code_scheme == "severity"
            and isinstance(severity_block, dict)
        ):
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
            from .checker_policy import EvidenceStatus, ReachabilityState
            from .reporter import _change_to_dict

            eff_sets = result._effective_kind_sets()
            scoped_only, missing_labels, blocks, missing_kind = (
                _resolve_scoped_gate_findings(result, severity_config, show_only)
            )
            for c in scoped_only:
                changes_list.append(
                    _change_to_dict(
                        c,
                        policy=result.policy or "strict_abi",
                        kind_sets=eff_sets,
                        policy_file=result.policy_file,
                        # Codex review: a scoped-only change (PE_ORDINAL_RETARGETED,
                        # CONSUMER_REQUIRED_SYMBOL_REMOVED, CONSUMER_RUNTIME_LOAD_FAILED)
                        # is proven by the real consumer's own import table/execution,
                        # not by an artifact-level library diff -- evidence_status_for_change
                        # would otherwise report "artifact_proven" purely from the kind's
                        # BREAKING/RISK category, same as appcompat_to_json's own
                        # CONSUMER_PROVEN override for this exact finding shape.
                        evidence_status_override=EvidenceStatus.CONSUMER_PROVEN,
                    )
                )
            for label in missing_labels:
                changes_list.append(
                    {
                        "kind": missing_kind,
                        "symbol": label,
                        "description": (
                            f"Required symbol/version '{label}' is missing "
                            "from the new library."
                        ),
                        "old_value": None,
                        "new_value": None,
                        "severity": "breaking" if blocks else "compatible",
                        "relevant_to_gate": True,
                        "blocks_gate": blocks,
                        # G29 Phase 3 slice 1 (ADR-051, Codex review): a
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
                )
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
                    bucket = _SEVERITY_TO_SUMMARY_BUCKET.get(severity, "") if isinstance(
                        severity, str
                    ) else None
                    if bucket:
                        bucket_counts[bucket] += 1
                payload["summary"] = {
                    **full_summary,
                    **bucket_counts,
                    "total_changes": len(changes_list),
                }
        elif isinstance(full_summary, dict):
            # Codex review: `--format json --stat` (to_stat_json) emits a
            # summary-only payload with no `changes` array at all, so the
            # branch above -- gated on `isinstance(changes_list, list)` --
            # never runs for it. Without this, a `--stat --used-by`/
            # `--required-symbol` run still swaps `verdict` to the scoped
            # gate result (above) but leaves `summary` as the stale
            # full-library counts and never adds `full_summary`: a scoped
            # BREAKING verdict sitting next to unrelated full-library
            # summary numbers, the exact contradiction this fold-in exists
            # to remove. There's no per-change list to recompute bucket
            # counts from here, so instead add each scoped-only/missing-
            # contract synthetic finding's own contribution on top of the
            # already-correct full-library counts.
            from .checker_policy import EvidenceStatus
            from .reporter import _change_to_dict

            scoped_only, missing_labels, blocks, _missing_kind = (
                _resolve_scoped_gate_findings(result, severity_config, show_only)
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
                        evidence_status_override=EvidenceStatus.CONSUMER_PROVEN,
                    )
                    severity = entry.get("severity")
                    bucket = _SEVERITY_TO_SUMMARY_BUCKET.get(severity) if isinstance(
                        severity, str
                    ) else None
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
        return json.dumps(payload, indent=2)

    if fmt in ("markdown", "text", "review"):
        full_verdict_value = getattr(getattr(result, "verdict", None), "value", None)
        header: list[str] = []
        if (
            scoped_verdict_value is not None
            and full_verdict_value is not None
            and scoped_verdict_value != full_verdict_value
        ):
            # The exit code is computed from the *scoped* result (ADR-043
            # worst-wins), which can disagree with the full-library verdict
            # this report's own headline already rendered above -- state
            # which one is authoritative for CI instead of leaving the two to
            # silently disagree (Codex review). Under a severity scheme the
            # exit code is NOT a fixed BREAKING->4/API_BREAK->2 mapping of
            # scoped_verdict -- e.g. --severity-preset info-only can floor it
            # at 0 even for a BREAKING scoped verdict -- so state the actual
            # computed value/scheme instead of asserting the exit code
            # "reflects" the scoped verdict, which would be false in that
            # case (Codex review follow-up).
            scoped_exit_code = getattr(result, "scoped_exit_code", None)
            scoped_exit_code_scheme = getattr(result, "scoped_exit_code_scheme", None)
            exit_note = (
                f"the CLI process exits {scoped_exit_code} under the "
                f"{scoped_exit_code_scheme} exit-code scheme for this run"
                if scoped_exit_code is not None
                else "this is what the exit code reflects"
            )
            header = [
                f"**Scoped verdict: {scoped_verdict_value}** "
                f"({exit_note}; the full library verdict above is "
                f"{full_verdict_value}).",
                "",
            ]
        lines = [*header, text, ""]
        if used_by is not None:
            lines.append("## Scoped to --used-by applications")
            for summary in used_by:
                lines.append(
                    f"- {summary['app']}: {summary['verdict']} "
                    f"(missing {len(summary['missing_symbols'])} symbol(s), "
                    f"{len(summary['missing_versions'])} version(s), "
                    f"{summary['relevant_change_count']} relevant change(s))"
                )
                # Name the actual missing symbols/versions, not just their
                # count (Codex review) -- a human reading the default text
                # report otherwise has no way to tell *which* symbol broke
                # this app without re-running with --format json.
                for sym in summary["missing_symbols"]:
                    lines.append(f"  - missing symbol: `{sym}`")
                for ver in summary["missing_versions"]:
                    lines.append(f"  - missing version: `{ver}`")
        if required_symbols is not None:
            lines.append("## Scoped to --required-symbol(s) contract")
            lines.append(
                f"- verdict: {required_symbols['verdict']} "
                f"(missing {len(required_symbols['missing_entrypoints'])} of "
                f"{len(required_symbols['required_entrypoints'])} required "
                f"entrypoint(s))"
            )
            for entrypoint in required_symbols["missing_entrypoints"]:
                lines.append(f"  - missing entrypoint: `{entrypoint}`")
        # Scoped-only changes (e.g. PE_ORDINAL_RETARGETED) and uncovered
        # missing-contract labels are relevant to the scoped gate but never
        # land in `result.changes` -- name them here too, mirroring the
        # JSON/SARIF/JUnit fold-in, so a text/markdown/review reader sees the
        # same actionable findings a JSON consumer would (Codex review).
        scoped_only, missing_labels, blocks, _missing_kind = (
            _resolve_scoped_gate_findings(result, severity_config, show_only)
        )
        if scoped_only or missing_labels:
            lines.append("## Additional scoped-gate findings")
            severity_tag = "breaking" if blocks else "compatible"
            for label in missing_labels:
                lines.append(
                    f"- `{label}` is required but missing from the new "
                    f"library ({severity_tag})"
                )
            for c in scoped_only:
                lines.append(f"- {c.kind.value}: {c.description}")
        return "\n".join(lines)

    return text
