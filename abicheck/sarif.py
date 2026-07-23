# Copyright 2026 Nikolay Petrov
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

"""SARIF 2.1.0 output for abicheck.

Produces a Static Analysis Results Interchange Format (SARIF) document
suitable for upload to GitHub Code Scanning via:

    abicheck compare old.so new.so --format sarif > results.sarif

GitHub Code Scanning docs:
  https://docs.github.com/en/code-security/code-scanning/integrating-with-code-scanning/sarif-support-for-code-scanning

SARIF spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""

from __future__ import annotations

import json
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.checker_policy import (
    EvidenceStatus,
    ReachabilityState,
    evidence_status_for_change,
    impact_for,
    policy_for,
)
from abicheck.impact import assess_change
from abicheck.report_model import VERDICT_TO_SARIF_LEVEL as _VERDICT_TO_SARIF_LEVEL
from abicheck.reporter import _finding_id, apply_show_only
from abicheck.reporter_markdown import ShowOnlyFilter
from abicheck.severity import missing_contract_exit_code

if TYPE_CHECKING:
    from abicheck.severity import SeverityConfig

# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------
_BREAKING_SEVERITY = "error"

# Rule ID = change_kind value (snake_case, already stable)


def _tool_version() -> str:
    try:
        return _pkg_version("abicheck")
    except Exception:  # noqa: BLE001
        return "unknown"


# Canonical verdict→SARIF-level map lives in report_model (ADR-036), imported
# above under its historical private name so call sites are unchanged.


_SEVERITY_LEVEL_TO_SARIF = {
    "error": "error",
    "warning": "warning",
    "info": "note",
}


def _severity(
    change: Change,
    result: DiffResult,
    severity_config: SeverityConfig | None = None,
) -> str:
    """Return the SARIF ``level`` for *change*.

    When *severity_config* is given, the result level follows the configured
    severity for this change's effective issue category
    (:func:`abicheck.severity.classify_effective_change`) — the same
    classification the exit code and ``severityGate`` properties block use —
    so a SARIF consumer keying off ``level`` never disagrees with the
    configured gate (e.g. ``--severity-addition error`` must show additions
    as ``level: error``, not the legacy policy severity).

    Without a *severity_config*, whenever the canonical per-finding verdict
    (``result._effective_verdict_for_change`` — A4 per-finding
    ``effective_verdict`` (ADR-027), a PolicyFile verdict override, *or* a
    named base policy like ``plugin_abi``/``sdk_vendor`` reclassifying this
    change's kind) differs from the kind's inherent default verdict, the
    canonical verdict→SARIF-level table (ADR-036) applies, so SARIF can never
    disagree with the JSON report or the gate/exit code. Comparing against
    the *kind's own* default verdict (rather than checking for specific
    override mechanisms) catches every reclassification path uniformly — a
    hand-maintained ``has_override`` allowlist previously missed base-policy
    downgrades entirely. Findings still at their kind's default verdict keep
    the coarser per-kind default severity from the policy registry, which is
    intentionally finer-grained than the 4-way verdict table (e.g.
    distinguishing "warning" additions from "note"-worthy ones).
    """
    if severity_config is not None:
        from abicheck.severity import classify_effective_change

        category = classify_effective_change(
            change,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        level = severity_config.level_for(category)
        return _SEVERITY_LEVEL_TO_SARIF.get(level.value, "warning")

    entry = policy_for(change.kind)
    verdict = result._effective_verdict_for_change(change)
    if verdict != entry.default_verdict:
        return _VERDICT_TO_SARIF_LEVEL.get(verdict, entry.severity)
    return entry.severity


def _parse_source_location(loc: str) -> tuple[str, int | None, int | None]:
    """Parse a ``file[:line[:column]]`` source location for a SARIF region.

    Parses from the right rather than assuming the file is everything
    before the *first* colon — a path can itself contain colons (a
    synthetic/virtual scheme like ``generated:headers/foo.h:42``, or a
    Windows drive letter like ``C:\\foo\\bar.h:42``), and the file is
    whatever colon segments remain once the trailing numeric line[:column]
    is peeled off, not a fixed prefix.

    ``loc.rsplit(":", 2)`` gives at most the last two colon-separated
    segments as candidates for line/column:

    * If the middle segment is numeric, it's the line and everything before
      it (which may itself contain colons) is the file; the last segment is
      the column if it's numeric too, otherwise it's dropped (a malformed
      trailing column shouldn't hide a good line number).
    * If the middle segment *isn't* numeric, the split point assumed too few
      file-side colons (e.g. the drive-letter or ``generated:`` cases above)
      — recombine it into the file and treat the last segment as the line.
    * Fewer than two colons: fall back to a single trailing split for
      ``file:line``.

    Any shape that doesn't resolve to a numeric line returns the location
    unchanged with no region.
    """
    three = loc.rsplit(":", 2)
    if len(three) == 3:
        file_part, mid, last = three
        if mid.isdigit():
            column = int(last) if last.isdigit() else None
            return file_part, int(mid), column
        if last.isdigit():
            return f"{file_part}:{mid}", int(last), None
        return loc, None, None

    two = loc.rsplit(":", 1)
    if len(two) == 2 and two[1].isdigit():
        return two[0], int(two[1]), None
    return loc, None, None


def _rule_for(kind: ChangeKind) -> dict[str, Any]:
    """Produce a SARIF reportingDescriptor for a ChangeKind."""
    rule_id = kind.value
    severity = policy_for(kind).severity
    doc_slug = policy_for(kind).doc_slug
    help_uri = f"https://github.com/abicheck/abicheck/blob/main/docs/reference/change-kinds.md#{doc_slug}"
    impact = impact_for(kind)
    full_desc = (
        impact if impact else f"ABI change detected: {rule_id.replace('_', ' ')}"
    )
    return {
        "id": rule_id,
        "name": "".join(w.capitalize() for w in rule_id.split("_")),
        "shortDescription": {"text": rule_id.replace("_", " ").capitalize()},
        "fullDescription": {"text": full_desc},
        "helpUri": help_uri,
        "defaultConfiguration": {"level": severity},
        "properties": {"tags": ["abi", "binary-compatibility"]},
    }


def _missing_contract_rule(rule_id: str) -> dict[str, Any]:
    """Produce a SARIF reportingDescriptor for a synthetic missing-contract rule id.

    Mirrors :func:`_rule_for`'s shape so ``used_by_missing_symbol``/
    ``required_symbol_missing`` results carry the same rule metadata as any
    other -- without a matching entry in ``tool.driver.rules``, a SARIF
    consumer that resolves annotations by rule id would have no metadata for
    these synthetic findings (Codex review).
    """
    return {
        "id": rule_id,
        "name": "".join(w.capitalize() for w in rule_id.split("_")),
        "shortDescription": {"text": rule_id.replace("_", " ").capitalize()},
        "fullDescription": {
            "text": "A required symbol/version/entrypoint is missing from the new library."
        },
        "helpUri": "https://github.com/abicheck/abicheck/blob/main/docs/reference/exit-codes.md",
        "defaultConfiguration": {"level": "error"},
        "properties": {"tags": ["abi", "binary-compatibility", "missing-contract"]},
    }


def _result_for(
    change: Change,
    result: DiffResult,
    severity_config: SeverityConfig | None = None,
    *,
    relevant_ids: frozenset[str] | None = None,
    evidence_status_override: EvidenceStatus | None = None,
) -> dict[str, Any]:
    """Produce a SARIF result object for a Change.

    *relevant_ids*, when not ``None``, means a ``--used-by``/``--required-symbol``
    gate is active: a change whose :func:`_finding_id` is absent from the set is
    not relevant to that gate, so its ``level`` is downgraded to ``"note"``
    (informational, never blocks the scoped gate) regardless of its own
    computed severity, and its ``properties.relevantToGate`` is set to
    ``false`` so a consumer can distinguish "not severe" from "out of scope"
    (CLI-audit P1: SARIF result levels must follow the scoped gate, not just
    the full-library verdict).

    *evidence_status_override*, when given, wins over the kind-derived
    :func:`evidence_status_for_change` — mirrors ``reporter._change_to_dict``'s
    own override, for a scoped-only finding (``PE_ORDINAL_RETARGETED``,
    ``CONSUMER_REQUIRED_SYMBOL_REMOVED``, ``CONSUMER_RUNTIME_LOAD_FAILED``)
    proven by the real consumer's own import table/execution, not by an
    artifact-level library diff (Codex review).
    """
    library, old_version, new_version = (
        result.library,
        result.old_version,
        result.new_version,
    )
    msg_parts = [change.description]
    if change.old_value or change.new_value:
        msg_parts.append(f"({change.old_value or '?'} → {change.new_value or '?'})")

    # Build physical location — prefer source header over .so when available
    phys_loc: dict[str, Any]
    if change.source_location:
        uri, line, column = _parse_source_location(change.source_location)
        phys_loc = {
            "artifactLocation": {"uri": uri, "uriBaseId": "%SRCROOT%"},
        }
        if line is not None:
            region: dict[str, int] = {"startLine": line}
            if column is not None:
                region["startColumn"] = column
            phys_loc["region"] = region
    else:
        phys_loc = {
            "artifactLocation": {"uri": library, "uriBaseId": "%SRCROOT%"},
        }

    properties: dict[str, Any] = {
        "symbol": change.symbol,
        "oldVersion": old_version,
        "newVersion": new_version,
    }
    if change.old_value is not None:
        properties["oldValue"] = change.old_value
    if change.new_value is not None:
        properties["newValue"] = change.new_value
    if change.affected_symbols:
        properties["affectedSymbols"] = change.affected_symbols
    if change.caused_by_type:
        properties["causedByType"] = change.caused_by_type
    if change.caused_count > 0:
        properties["causedCount"] = change.caused_count
    if change.correlated_change_kind:
        properties["correlatedChangeKind"] = change.correlated_change_kind
    # ADR-044 P1 item 4 — structured reachability evidence (previously
    # description-text-only, e.g. inside the suppression_would_hide_public_break
    # diagnostic's prose): whether this change is public-reachable, how, and
    # the shortest proof path.
    if change.public_reachable:
        properties["publicReachable"] = True
        if change.reachability_kind:
            properties["reachabilityKind"] = change.reachability_kind
        if change.reachability_proof_path:
            properties["reachabilityProofPath"] = change.reachability_proof_path
    # G31 Phase B B3 (ADR-048) — structured graph impact data. SARIF's own
    # relatedLocations/codeFlows model source-file locations, not abstract
    # graph node/edge references, so surfacing this as typed `properties`
    # (matching every other graph-derived field on this object) is the
    # pragmatic fit here rather than forcing an artificial codeFlow —
    # documented as a deliberate scope decision in ADR-048.
    if change.affected_public_roots:
        properties["affectedPublicRoots"] = change.affected_public_roots
    if change.impact_proof_path:
        properties["impactProofPath"] = change.impact_proof_path
    if change.impact_is_direct is not None:
        properties["impactIsDirect"] = change.impact_is_direct
    # G29 Phase 3 slice 1 (ADR-051): same unified read view reporter.py's
    # JSON output gained -- reachabilityState always present (the tri-state
    # signal from PR #607, never surfaced in SARIF before this), and the
    # unified impactAssessment object when it carries more than the defaults.
    assessment = assess_change(change)
    properties["reachabilityState"] = assessment.reachability_state.value
    if assessment.has_signal():
        properties["impactAssessment"] = assessment.to_dict()
    evidence_status = evidence_status_override or evidence_status_for_change(change)
    if evidence_status is not None:
        properties["evidenceStatus"] = evidence_status.value

    level = _severity(change, result, severity_config)
    if relevant_ids is not None:
        is_relevant = _finding_id(change) in relevant_ids
        properties["relevantToGate"] = is_relevant
        if not is_relevant:
            level = "note"

    return {
        "ruleId": change.kind.value,
        "level": level,
        "message": {
            "text": " ".join(msg_parts),
        },
        "locations": [
            {
                "physicalLocation": phys_loc,
                "logicalLocations": [
                    {
                        "name": change.symbol,
                        "kind": "member",
                    }
                ],
            }
        ],
        "properties": properties,
    }


def _severity_gate_properties(
    result: DiffResult, severity_config: SeverityConfig,
) -> dict[str, Any]:
    """Build a compact, auditable ``severityGate`` block for SARIF ``properties``.

    Mirrors the categories/exit-code contract of JSON's ``severity`` block
    (:func:`abicheck.reporter._build_severity_json`) so a SARIF consumer can
    tell *why* the invocation's exit code is what it is without
    cross-referencing the JSON report separately. Both routed through
    :func:`abicheck.severity.compute_gate_decision` — the single canonical
    gate computation — so ``exitCode``/``blocking``/``blockingCategories``
    can never independently drift apart from each other or from JSON's
    equivalent block.
    """
    from abicheck.severity import compute_gate_decision

    gate = compute_gate_decision(
        result.changes,
        severity_config,
        policy=result.policy,
        kind_sets=result._effective_kind_sets(),
        policy_file=result.policy_file,
    )
    return {
        "exitCode": gate.exit_code,
        "blocking": gate.blocking,
        "blockingCategories": list(gate.blocking_categories),
        "config": {
            "abi_breaking": severity_config.abi_breaking.value,
            "potential_breaking": severity_config.potential_breaking.value,
            "quality_issues": severity_config.quality_issues.value,
            "addition": severity_config.addition.value,
        },
    }


def _missing_contract_result(
    label: str, gate_scope: str, severity_config: SeverityConfig | None,
) -> dict[str, Any]:
    """Synthesize a SARIF result for a missing required symbol/version/entrypoint.

    A required contract member absent from the new library (--used-by's
    ``missing_symbols``/``missing_versions``, or --required-symbol's
    ``missing_entrypoints``) has no backing diff ``Change`` -- it was never in
    ``result.changes`` to begin with, so :func:`_result_for` never emits it.
    Without a synthetic result the gate's own ``exitCode`` could be a nonzero
    (BREAKING) value while ``results`` shows nothing to explain it (CLI-audit
    P1).

    The level must follow the same severity decision as the gate's own exit
    code (:func:`abicheck.severity.missing_contract_exit_code`, the function
    ``_scoped_exit_code`` floors on): under the legacy scheme (no
    *severity_config*) a missing contract member is unconditionally BREAKING,
    but under a severity scheme that demotes ``abi_breaking`` (e.g.
    ``--severity-preset info-only``), the scoped exit code can be 0 for the
    same missing member -- emitting ``level: "error"`` regardless would let a
    SARIF/code-scanning consumer flag/block a finding the gate itself passed
    (Codex review).
    """
    rule_id = (
        "used_by_missing_symbol" if gate_scope == "used_by" else "required_symbol_missing"
    )
    blocks = (
        severity_config is None
        or missing_contract_exit_code(severity_config) != 0
    )
    return {
        "ruleId": rule_id,
        "level": "error" if blocks else "note",
        "message": {
            "text": f"Required symbol/version '{label}' is missing from the new library.",
        },
        # relevantToGate is always true here -- a missing-contract member is
        # in the --used-by/--required-symbol scope by construction, distinct
        # from whether severity config makes it block (`blocksGate`). The two
        # axes are orthogonal: severity decides blocking, not scope
        # membership (CodeRabbit review).
        "properties": {
            "relevantToGate": True,
            "blocksGate": blocks,
            "missingContractMember": label,
            # G29 Phase 3 slice 1 (ADR-051, Codex review): a missing-contract
            # member has no backing Change for assess_change to read, but
            # reachabilityState is "always present" everywhere else this
            # slice touches (D3/D4) -- a missing symbol/version is a hard
            # absence, not a reachability question, so UNKNOWN (not proven
            # either way) is the honest, consistent value here.
            "reachabilityState": ReachabilityState.UNKNOWN.value,
        },
    }


def _scoped_gate_properties(result: DiffResult) -> dict[str, Any] | None:
    """Build a ``scopedGate`` block when ``--used-by``/``--required-symbol(s)``
    scoping was requested (ADR-043).

    The scoped gate (``result.scoped_verdict``/``scoped_exit_code``) is
    authoritative for this document's own ``invocations[0].exitCode`` and each
    result's ``level`` (CLI-audit P1 fix) -- ``result.verdict`` (the full,
    unscoped library verdict) is still reported here as ``fullLibraryVerdict``
    for context, but no longer drives what SARIF consumers treat as
    blocking. This block also carries the relevant/unrelated finding counts so
    a consumer can see how many of ``results`` actually gated this run.
    """
    scoped_verdict = getattr(result, "scoped_verdict", None)
    if scoped_verdict is None:
        return None
    used_by = getattr(result, "used_by", None)
    required_symbols = getattr(result, "required_symbols", None)
    scoped_exit_code = getattr(result, "scoped_exit_code", None)
    scoped_exit_code_scheme = getattr(result, "scoped_exit_code_scheme", None)
    gate_scope = getattr(result, "gate_scope", None)
    relevant_ids = getattr(result, "scoped_relevant_finding_ids", None) or frozenset()
    relevant_in_changes = sum(1 for c in result.changes if _finding_id(c) in relevant_ids)
    # scoped-only changes (e.g. PE_ORDINAL_RETARGETED) and missing-contract
    # members are relevant by construction -- they exist only because
    # scope_diff_to_app/scope_diff_to_required_symbols found them relevant --
    # and are never in result.changes, so they don't affect unrelatedFindingCount
    # (which counts only irrelevant entries *within* result.changes) but do
    # count toward relevantFindingCount (CodeRabbit review).
    scoped_only_count = len(getattr(result, "scoped_only_changes", ()) or ())
    missing_count = len(getattr(result, "scoped_missing_labels", ()) or ())
    relevant_count = relevant_in_changes + scoped_only_count + missing_count
    block: dict[str, Any] = {
        "gateScope": gate_scope,
        "gateVerdict": scoped_verdict.value,
        "fullLibraryVerdict": result.verdict.value,
        "relevantFindingCount": relevant_count,
        "unrelatedFindingCount": len(result.changes) - relevant_in_changes,
        # Back-compat alias for the block's original field name.
        "scopedVerdict": scoped_verdict.value,
    }
    if scoped_exit_code is not None:
        block["gateExitCode"] = scoped_exit_code
        block["gateExitCodeScheme"] = scoped_exit_code_scheme
        # Back-compat aliases.
        block["scopedExitCode"] = scoped_exit_code
        block["scopedExitCodeScheme"] = scoped_exit_code_scheme
    if used_by is not None:
        block["usedBy"] = used_by
    if required_symbols is not None:
        block["requiredSymbolContract"] = required_symbols
    return block


def to_sarif(
    result: DiffResult,
    *,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
) -> dict[str, Any]:
    """Convert a DiffResult to a SARIF 2.1.0 document (dict).

    *severity_config*, when given, drives the invocation's ``exitCode`` from
    the actual severity-aware gate instead of inferring it purely from
    ``result.verdict`` — compatibility and "blocks CI" are independent
    decisions once severity configuration is in play (e.g. an addition
    configured ``error`` blocks the build despite a ``COMPATIBLE`` verdict).
    A ``severityGate`` properties block is added so the reason is auditable
    in the SARIF document itself.

    ``executionSuccessful`` is unrelated to any of this: per the SARIF spec
    it reports whether the *analysis tool ran to completion*, not whether it
    found blocking issues — the spec's own example shows a successful run
    with ``exitCode: 1`` and warnings alongside ``executionSuccessful: true``.
    A completed comparison (breaking, gate-failing, or otherwise) is always a
    successful execution here; gate/verdict outcome belongs solely in
    ``exitCode``, ``exitCodeDescription``, result ``level``\\ s, and
    ``properties.severityGate``.
    """
    tool_version = _tool_version()

    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )

    # Collect unique rules used
    rules_seen: dict[str, dict[str, Any]] = {}
    sarif_results: list[dict[str, Any]] = []

    # When --used-by/--required-symbol scoping is active, relevant_ids makes
    # each result's own level follow the scoped gate rather than the full
    # library verdict (CLI-audit P1 fix); None means no scoping is active, so
    # _result_for's existing full-library severity computation is unchanged.
    relevant_ids = getattr(result, "scoped_relevant_finding_ids", None)
    for change in changes:
        rule_id = change.kind.value
        if rule_id not in rules_seen:
            rules_seen[rule_id] = _rule_for(change.kind)
        sarif_results.append(
            _result_for(change, result, severity_config, relevant_ids=relevant_ids)
        )

    # Scoped-only changes: `scope_diff_to_app`/`scope_diff_to_required_symbols`
    # can synthesize a Change (e.g. PE_ORDINAL_RETARGETED) that is relevant to
    # the gate but was never added to `result.changes` -- without rendering
    # these too, a --used-by run that fails solely because of one of these
    # would report a nonzero gate exitCode with zero results to explain it
    # (Codex review). Run them through the same `--show-only` filter as
    # `result.changes` above -- otherwise a `--show-only additions` run would
    # still upload a scoped-only breaking result the user explicitly asked
    # to filter out, unlike the normal `result.changes` path (Codex review
    # follow-up).
    scoped_only_changes = list(getattr(result, "scoped_only_changes", ()) or ())
    if show_only and scoped_only_changes:
        scoped_only_changes = apply_show_only(
            scoped_only_changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
    for change in scoped_only_changes:
        rule_id = change.kind.value
        if rule_id not in rules_seen:
            rules_seen[rule_id] = _rule_for(change.kind)
        sarif_results.append(
            _result_for(
                change, result, severity_config, relevant_ids=relevant_ids,
                # Codex review: proven by the real consumer's own import
                # table/execution, not an artifact-level library diff --
                # mirrors reporter.appcompat_to_json's own override for this
                # exact finding shape.
                evidence_status_override=EvidenceStatus.CONSUMER_PROVEN,
            )
        )

    gate_scope = getattr(result, "gate_scope", None)
    if gate_scope is not None:
        # A missing-contract label has no backing Change/ChangeKind, so it
        # can't run through apply_show_only (which resolves severity via
        # effective_verdict_for_change) -- but --show-only's severity
        # dimension still applies: without this, a --show-only run that
        # excludes breaking findings would still upload an `error`-level
        # missing-contract result the filter was meant to exclude (Codex
        # review follow-up to the scoped_only_changes show-only fix above).
        # Element/action tokens don't cleanly apply to "a symbol is simply
        # absent", so only the severity dimension is checked here.
        missing_severity = (
            "breaking"
            if severity_config is None or missing_contract_exit_code(severity_config) != 0
            else "compatible"
        )
        show_only_severities = (
            ShowOnlyFilter.parse(show_only).severities if show_only else frozenset()
        )
        if not show_only_severities or missing_severity in show_only_severities:
            for label in getattr(result, "scoped_missing_labels", ()) or ():
                rule_id = (
                    "used_by_missing_symbol" if gate_scope == "used_by"
                    else "required_symbol_missing"
                )
                if rule_id not in rules_seen:
                    rules_seen[rule_id] = _missing_contract_rule(rule_id)
                sarif_results.append(
                    _missing_contract_result(label, gate_scope, severity_config)
                )

    severity_gate = (
        _severity_gate_properties(result, severity_config)
        if severity_config is not None
        else None
    )
    scoped_gate = _scoped_gate_properties(result)
    scoped_exit_code = getattr(result, "scoped_exit_code", None)

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "abicheck",
                        "version": tool_version,
                        "informationUri": "https://github.com/abicheck/abicheck",
                        "rules": list(rules_seen.values()),
                    }
                },
                "invocations": [
                    {
                        # Always true: this reports the SARIF tool run, which
                        # completed. It must not encode the ABI/severity gate
                        # outcome — see the docstring above.
                        "executionSuccessful": True,
                        # Exit codes mirror abicheck compare CLI contract when no
                        # severity_config is given: BREAKING=4 (mapped to SARIF 1),
                        # API_BREAK=2, others=0. COMPATIBLE_WITH_RISK intentionally
                        # exits 0 — binary-compatible, deployment risk is surfaced
                        # via exitCodeDescription only. When severity_config *is*
                        # given, the exit code instead follows the severity-aware
                        # gate (severityGate.exitCode below). When --used-by/
                        # --required-symbol scoping is active, the scoped gate
                        # wins over both — it's what the CLI process actually
                        # exits with (CLI-audit P1 fix; matches
                        # cli_compare_helpers.run_compare's unconditional
                        # sys.exit(scoped_exit_code) when scoping was requested).
                        "exitCode": (
                            scoped_exit_code
                            if scoped_exit_code is not None
                            else severity_gate["exitCode"]
                            if severity_gate is not None
                            else (
                                4
                                if result.verdict == Verdict.BREAKING
                                else 2
                                if result.verdict == Verdict.API_BREAK
                                else 0
                            )
                        ),
                        "exitCodeDescription": (
                            f"{scoped_gate['gateVerdict']} (scoped: {scoped_gate['gateScope']})"
                            if scoped_gate is not None
                            else f"{result.verdict.value} (severity-gated)"
                            if severity_gate is not None
                            else result.verdict.value
                        ),
                    }
                ],
                "results": sarif_results,
                "automationDetails": {
                    "id": f"abicheck/{result.library}/{result.old_version}_to_{result.new_version}",
                    "description": {
                        "text": (
                            f"ABI comparison: {result.library} "
                            f"{result.old_version} → {result.new_version} "
                            f"verdict={result.verdict.value}"
                        )
                    },
                },
                "properties": {
                    "abiVerdict": result.verdict.value,
                    "oldVersion": result.old_version,
                    "newVersion": result.new_version,
                    "library": result.library,
                    "changeCount": len(changes),
                    "suppressedCount": result.suppressed_count,
                    **(
                        {"severityGate": severity_gate}
                        if severity_gate is not None
                        else {}
                    ),
                    **(
                        {"scopedGate": scoped_gate}
                        if scoped_gate is not None
                        else {}
                    ),
                    **(
                        {"redundantCount": result.redundant_count}
                        if result.redundant_count > 0
                        else {}
                    ),
                    **(
                        {
                            "oldFile": {
                                "path": result.old_metadata.path,
                                "sha256": result.old_metadata.sha256,
                                "sizeBytes": result.old_metadata.size_bytes,
                            }
                        }
                        if result.old_metadata is not None
                        else {}
                    ),
                    **(
                        {
                            "newFile": {
                                "path": result.new_metadata.path,
                                "sha256": result.new_metadata.sha256,
                                "sizeBytes": result.new_metadata.size_bytes,
                            }
                        }
                        if result.new_metadata is not None
                        else {}
                    ),
                    "confidence": result.confidence.value,
                    "evidenceTiers": list(result.evidence_tiers),
                    **(
                        {"coverageWarnings": list(result.coverage_warnings)}
                        if result.coverage_warnings
                        else {}
                    ),
                    "policy": result.policy or "strict_abi",
                    **(
                        {
                            "policyOverrides": {
                                k.value: v.value
                                for k, v in result.policy_file.overrides.items()
                            }
                        }
                        if result.policy_file and result.policy_file.overrides
                        else {}
                    ),
                    # ADR-024 §D4/D5: header-scope ledger. Out-of-surface
                    # findings are disclosed here for auditability (never
                    # silently dropped) when --scope-public-headers is active.
                    **(
                        {
                            "surfaceScope": {
                                "enabled": True,
                                "confidence": result.surface_scope_confidence,
                                "notes": list(result.surface_scope_notes),
                                "outOfSurfaceCount": result.out_of_surface_count,
                                "outOfSurfaceChanges": [
                                    {
                                        "kind": c.kind.value,
                                        "symbol": c.symbol,
                                        "description": c.description,
                                        **(
                                            {"sourceLocation": c.source_location}
                                            if c.source_location
                                            else {}
                                        ),
                                        **(
                                            {"reason": c.surface_exclusion_reason}
                                            if c.surface_exclusion_reason
                                            else {}
                                        ),
                                    }
                                    for c in result.out_of_surface_changes
                                ],
                            }
                        }
                        if result.scope_to_public_surface
                        else {}
                    ),
                    # ADR-039: build-context reconciliation ledger. Findings
                    # cleared as context-free header-parse artifacts are disclosed
                    # here (never silently dropped) when reconciliation removed any.
                    **(
                        {
                            "buildContextReconciled": {
                                "count": result.reconciled_count,
                                "changes": [
                                    {
                                        "kind": c.kind.value,
                                        "symbol": c.symbol,
                                        "description": c.description,
                                        **(
                                            {"sourceLocation": c.source_location}
                                            if c.source_location
                                            else {}
                                        ),
                                        **(
                                            {"reason": c.surface_exclusion_reason}
                                            if c.surface_exclusion_reason
                                            else {}
                                        ),
                                    }
                                    for c in result.reconciled_changes
                                ],
                            }
                        }
                        if result.reconciled_changes
                        else {}
                    ),
                },
            }
        ],
    }


def to_sarif_str(
    result: DiffResult,
    indent: int = 2,
    *,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
) -> str:
    """Serialize DiffResult to a SARIF JSON string."""
    return json.dumps(
        to_sarif(result, show_only=show_only, severity_config=severity_config),
        indent=indent,
    )


def write_sarif(result: DiffResult, path: Path) -> None:
    """Write SARIF output to *path*."""
    path.write_text(to_sarif_str(result), encoding="utf-8")
