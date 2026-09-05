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

import hashlib
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.checker_policy import (
    EvidenceStatus,
    ReachabilityState,
    evidence_status_for_result,
    impact_for,
    policy_for,
)
from abicheck.contract_gating import (
    contract_relevance_of,
    evaluation_status_of,
    is_evaluated,
)
from abicheck.contract_relevance_types import CompatibilityEvaluationStatus
from abicheck.finding_identity import missing_contract_kind
from abicheck.impact import assess_change
from abicheck.policy.gate_decision import gate_decision_for_result
from abicheck.report.disposition_audit import compute_disposition_audit
from abicheck.report.render_json import render_mapping_as_json
from abicheck.report_model import VERDICT_TO_SARIF_LEVEL as _VERDICT_TO_SARIF_LEVEL
from abicheck.reporter import (
    _finding_id,
    _suppress_dangling_correlation_notes,
    apply_show_only,
)
from abicheck.reporter_markdown import (
    ShowOnlyFilter,
    _root_cause_key_and_display,
    root_cause_evidence_lookup_for_changes,
    root_cause_lookup_for_changes,
)
from abicheck.severity import missing_contract_exit_code

if TYPE_CHECKING:
    from abicheck.severity import GateDecision, SeverityConfig

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
    configured gate (e.g. ``severity.addition: error`` must show additions
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


def _change_detail_properties(change: Change) -> dict[str, Any]:
    """The optional per-change value/attribution entries of the properties bag.

    Each is omitted rather than emitted as ``null`` when it carries nothing, so
    a SARIF consumer can tell "not applicable" from "empty".
    """
    props: dict[str, Any] = {}
    if change.old_value is not None:
        props["oldValue"] = change.old_value
    if change.new_value is not None:
        props["newValue"] = change.new_value
    if change.affected_symbols:
        props["affectedSymbols"] = change.affected_symbols
    if change.caused_by_type:
        props["causedByType"] = change.caused_by_type
    if change.caused_count > 0:
        props["causedCount"] = change.caused_count
    if change.correlated_change_kind:
        props["correlatedChangeKind"] = change.correlated_change_kind
    # ELF symbol linkage of a removed symbol (see reporter.py's identical
    # symbol_binding property for the full rationale) -- Codex review.
    if change.symbol_binding:
        props["symbolBinding"] = change.symbol_binding
    return props


def _reachability_and_impact_properties(change: Change) -> dict[str, Any]:
    """Structured reachability evidence and graph-impact data.

    ADR-044 P1 item 4 — structured reachability evidence (previously
    description-text-only, e.g. inside the suppression_would_hide_public_break
    diagnostic's prose): whether this change is public-reachable, how, and
    the shortest proof path.

    G31 Phase B B3 (ADR-048) — structured graph impact data. SARIF's own
    relatedLocations/codeFlows model source-file locations, not abstract
    graph node/edge references, so surfacing this as typed `properties`
    (matching every other graph-derived field on this object) is the
    pragmatic fit here rather than forcing an artificial codeFlow —
    documented as a deliberate scope decision in ADR-048.
    """
    props: dict[str, Any] = {}
    if change.public_reachable:
        props["publicReachable"] = True
        if change.reachability_kind:
            props["reachabilityKind"] = change.reachability_kind
        if change.reachability_proof_path:
            props["reachabilityProofPath"] = change.reachability_proof_path
    if change.affected_public_roots:
        props["affectedPublicRoots"] = change.affected_public_roots
    if change.impact_proof_path:
        props["impactProofPath"] = change.impact_proof_path
    if change.impact_is_direct is not None:
        props["impactIsDirect"] = change.impact_is_direct
    return props


def _contract_properties(
    change: Change,
    relevance: Any,
    result: DiffResult,
    severity_config: Any,
) -> dict[str, Any]:
    """The per-finding ADR-049 contract fields, in reporter.py's canonical shape.

    ``contract_relevance_of`` returns ``None`` for a run that never opted into
    ``--contract``, which is what keeps this inert for every
    pre-existing SARIF report. Before this shape was unified, only the
    ``NOT_EVALUATED`` case set any of these, so an ``IN_CONTRACT`` /
    ``NOT_APPLICABLE`` finding under ``--contract`` carried no
    contract properties at all even though it has a real, stamped decision
    (CLI-audit P1).
    """
    if relevance is None:
        return {}
    props: dict[str, Any] = {}
    props["contractRelevance"] = relevance.value
    if change.contract_reason_code:
        props["contractReasonCode"] = change.contract_reason_code
    if change.contract_assurance is not None:
        props["contractAssurance"] = change.contract_assurance.value
    # evaluation_status_of always resolves to a real status once
    # `relevance` is known non-None (it falls back to deriving one from
    # the relevance itself -- see its own docstring), so there is no
    # reachable `None` branch to guard here -- `cast` tells mypy that
    # without adding one.
    status = cast(CompatibilityEvaluationStatus, evaluation_status_of(change))
    props["compatibilityEvaluationStatus"] = status.value
    decision = getattr(change, "compatibility_decision", None)
    props["compatibilityDecision"] = getattr(decision, "value", None)
    from abicheck.severity import gate_contribution_for_change

    props["gateContribution"] = gate_contribution_for_change(
        change,
        severity_config,
        policy=result.policy,
        policy_file=result.policy_file,
    )
    if change.contract_evidence_refs is not None:
        props["contractEvidenceRefs"] = list(change.contract_evidence_refs)
    return props


def _result_for(
    change: Change,
    result: DiffResult,
    severity_config: SeverityConfig | None = None,
    *,
    relevant_ids: frozenset[str] | None = None,
    evidence_status_override: EvidenceStatus | None = None,
    root_cause: tuple[str, str] | None = None,
    impact_root_cause: tuple[str, str] | None = None,
    impact_root_cause_evidence: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Produce a SARIF result object for a Change.

    *root_cause*, when given (``--report-mode root-cause``, G29 Phase 3
    slice 5, ADR-052), is this finding's ``(root_cause_id, root_display)``
    pair -- added as ``properties.rootCauseId``/``properties.rootCause`` so
    a SARIF consumer can group results the same way JSON/markdown root-cause
    mode does, without restructuring SARIF's one-result-per-finding shape.

    *impact_root_cause* (G29 Phase 3 follow-up) is the same kind of pair but
    feeds ``impactAssessment.root_cause_id``/``root_cause_display``/
    ``impact_group_id`` instead -- computed unconditionally (any
    *report_mode*), unlike *root_cause* above which stays exclusive to
    ``--report-mode root-cause``'s own top-level ``properties.rootCauseId``/
    ``rootCause`` fields. Kept as a separate parameter rather than reusing
    *root_cause* so that existing, tested behavior of those top-level
    properties can't shift when this field was added.

    *impact_root_cause_evidence* (G29 Phase 6 follow-up) is *change*'s own
    entry from :func:`~abicheck.impact.correlation.correlate_root_causes`,
    resolved once per document via
    :func:`~abicheck.reporter_markdown.root_cause_evidence_lookup_for_changes`
    — feeds ``impactAssessment.root_cause_evidence``, unconditionally (any
    *report_mode*), mirroring *impact_root_cause* above.

    *relevant_ids*, when not ``None``, means a ``--used-by``/``--required-symbol``
    gate is active: a change whose :func:`_finding_id` is absent from the set is
    not relevant to that gate, so its ``level`` is downgraded to ``"note"``
    (informational, never blocks the scoped gate) regardless of its own
    computed severity, and its ``properties.relevantToGate`` is set to
    ``false`` so a consumer can distinguish "not severe" from "out of scope"
    (CLI-audit P1: SARIF result levels must follow the scoped gate, not just
    the full-library verdict).

    *evidence_status_override*, when given, wins over the kind-derived
    :func:`evidence_status_for_result` — mirrors ``reporter._change_to_dict``'s
    own override, for a scoped-only finding (``PE_ORDINAL_RETARGETED``,
    ``CONSUMER_REQUIRED_SYMBOL_REMOVED``)
    proven by the real consumer's own import table, not by an
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
    properties.update(_change_detail_properties(change))
    properties.update(_reachability_and_impact_properties(change))
    # G29 Phase 3 slice 1 (ADR-052): same unified read view reporter.py's
    # JSON output gained -- reachabilityState always present (the tri-state
    # signal from PR #607, never surfaced in SARIF before this), and the
    # unified impactAssessment object when it carries more than the defaults.
    assessment = assess_change(
        change,
        root_cause=impact_root_cause,
        root_cause_evidence=impact_root_cause_evidence,
    )
    properties["reachabilityState"] = assessment.reachability_state.value
    if assessment.has_signal():
        properties["impactAssessment"] = assessment.to_dict()
    evidence_status = evidence_status_override or evidence_status_for_result(
        change, result.evidence_tiers
    )
    if evidence_status is not None:
        properties["evidenceStatus"] = evidence_status.value
    if root_cause is not None:
        root_cause_id, root_display = root_cause
        properties["rootCauseId"] = root_cause_id
        properties["rootCause"] = root_display

    # CLI-audit P1: bring per-finding contract fields to the same canonical
    # shape reporter.py's JSON output already has (contract_relevance/
    # contract_reason_code/contract_assurance/compatibility_evaluation_status/
    # compatibility_decision/gate_contribution/contract_evidence_refs) --
    # previously only the NOT_EVALUATED case below set any of these, so an
    # IN_CONTRACT/NOT_APPLICABLE finding under --contract carried
    # no contract properties at all in SARIF even though it has a real,
    # stamped decision. `contract_relevance_of` returns None for a run that
    # never opted into --contract, which is what keeps this
    # unconditional call inert for every pre-existing SARIF report.
    relevance = contract_relevance_of(change)
    properties.update(_contract_properties(change, relevance, result, severity_config))

    level = _severity(change, result, severity_config)
    # ADR-049 D1/D9: compatibility policy did not score this finding, so it
    # contributed nothing to the verdict or the exit code -- emitting
    # `level: "error"` for it published a SARIF run whose annotations say
    # "error" beside a `NO_CHANGE` verdict and a clean exit (Codex review,
    # reproduced with a proven-out-of-contract type-size change). Downgraded
    # rather than dropped: D9 requires every detector fact to land in exactly
    # one *visible* outcome, and the relevance/reason above says why it is a
    # note. The same shape the scoped-gate downgrade below already uses.
    if not is_evaluated(change):
        level = "note"
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
    gate: GateDecision,
    severity_config: SeverityConfig,
) -> dict[str, Any]:
    """Build a compact, auditable ``severityGate`` block for SARIF ``properties``.

    Mirrors the categories/exit-code contract of JSON's ``severity`` block
    (:func:`abicheck.reporter._build_severity_json`) so a SARIF consumer can
    tell *why* the invocation's exit code is what it is without
    cross-referencing the JSON report separately. *gate* is the caller's
    already-computed
    :func:`abicheck.policy.gate_decision.gate_decision_for_result` value --
    the same one JSON's block projects -- so ``exitCode``/``blocking``/
    ``blockingCategories`` can never independently drift apart from each
    other or from JSON's equivalent block, and this function itself makes
    no policy decision (ADR-061 D9).
    """
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
    label: str,
    gate_scope: str,
    severity_config: SeverityConfig | None,
    *,
    root_cause: tuple[str, str] | None = None,
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
    rule_id = missing_contract_kind(gate_scope)
    blocks = severity_config is None or missing_contract_exit_code(severity_config) != 0
    properties: dict[str, Any] = {
        "relevantToGate": True,
        "blocksGate": blocks,
        "missingContractMember": label,
        # G29 Phase 3 slice 1 (ADR-052, Codex review): a missing-contract
        # member has no backing Change for assess_change to read, but
        # reachabilityState is "always present" everywhere else this
        # slice touches (D3/D4) -- a missing symbol/version is a hard
        # absence, not a reachability question, so UNKNOWN (not proven
        # either way) is the honest, consistent value here.
        "reachabilityState": ReachabilityState.UNKNOWN.value,
    }
    if root_cause is not None:
        root_cause_id, root_display = root_cause
        properties["rootCauseId"] = root_cause_id
        properties["rootCause"] = root_display
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
        "properties": properties,
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
    relevant_in_changes = sum(
        1 for c in result.changes if _finding_id(c) in relevant_ids
    )
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


def _coverage_notifications(result: object) -> dict[str, object]:
    """The ``toolExecutionNotifications`` entry for this run's coverage ledger.

    Returns an empty mapping -- so the key is absent, not empty -- when the
    run built no contract context, which is every run without
    ``--contract``. A run whose domain *closed* yields an empty
    list, because "checked, nothing missing" and "never checked" are
    different states and a consumer must be able to tell them apart.

    ``level: "error"`` regardless of what the ledger contributed: the
    notification describes the evidence, not the gate. ``executionSuccessful``
    stays untouched (the tool ran to completion either way), but the
    invocation's ``exitCode`` *is* folded with the coverage floor now that
    ADR-049 Phase 7 applies it -- see :func:`to_sarif`.
    """
    from .contract_coverage_ledger import coverage_failures_for_context

    ctx = getattr(result, "contract_context", None)
    if ctx is None:
        return {}
    failures = coverage_failures_for_context(ctx)
    return {
        "toolExecutionNotifications": [
            {
                "level": "error",
                "message": {
                    "text": (
                        f"contract coverage: {f.provider} ({f.side}) could not "
                        f"close the {f.mode} domain ({f.reason})"
                    )
                },
                "descriptor": {"id": f"abicheck.coverage.{f.reason}"},
                "properties": {
                    "provider": f.provider,
                    "side": f.side,
                    "recordId": f.record_id,
                    "contractMode": f.mode,
                    # Stated per notification so a consumer reading one in
                    # isolation knows it cannot be waived by a suppression
                    # rule (plan Section 6.2).
                    "suppressible": False,
                },
            }
            for f in failures
        ]
    }


def to_sarif(
    result: DiffResult,
    *,
    show_only: str | None = None,
    report_mode: str = "full",
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

    *report_mode* ``"root-cause"`` (G29 Phase 3 slice 5, ADR-052) adds
    ``properties.rootCauseId``/``properties.rootCause`` to every result
    instead of changing SARIF's one-result-per-finding structure -- unlike
    JSON/markdown's dedicated grouped rendering, this keeps every existing
    SARIF/code-scanning consumer working unchanged while letting a
    root-cause-aware one group results by ``rootCauseId``. Any other value
    (including ``"leaf"``) renders as ``full``, unchanged from before this
    parameter existed.
    """
    tool_version = _tool_version()

    # Codex review: filtered so an expired rule -- which ReclassifyRule.
    # matches() would already refuse to apply -- isn't disclosed in
    # policyReclassify below as though it were still in effect.
    _active_reclassify_rules: list[Any] = []
    if result.policy_file and result.policy_file.reclassify:
        from .reclassify import active_reclassify_rules

        _active_reclassify_rules = active_reclassify_rules(result.policy_file.reclassify)

    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        changes = _suppress_dangling_correlation_notes(changes)

    # Collect unique rules used
    rules_seen: dict[str, dict[str, Any]] = {}
    sarif_results: list[dict[str, Any]] = []

    # Scoped-only changes: `scope_diff_to_app`/`scope_diff_to_required_symbols`
    # can synthesize a Change (e.g. PE_ORDINAL_RETARGETED) that is relevant to
    # the gate but was never added to `result.changes` -- without rendering
    # these too, a --used-by run that fails solely because of one of these
    # would report a nonzero gate exitCode with zero results to explain it
    # (Codex review). Run them through the same `--show-only` filter as
    # `result.changes` above -- otherwise a `--show-only additions` run would
    # still upload a scoped-only breaking result the user explicitly asked
    # to filter out, unlike the normal `result.changes` path (Codex review
    # follow-up). Computed up front (not just before its own results loop
    # below) so the root-cause referenced_causes set below sees the same
    # filtered set (Codex review: an unfiltered preview let a hidden,
    # filtered-out scoped-only change's caused_by_type still group two
    # unrelated *visible* findings, disagreeing with JSON/markdown root-cause
    # mode, which computes from the filtered set only).
    scoped_only_changes = list(getattr(result, "scoped_only_changes", ()) or ())
    if show_only and scoped_only_changes:
        scoped_only_changes = apply_show_only(
            scoped_only_changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )

    # G29 Phase 3 slice 5 (ADR-052): --report-mode root-cause adds
    # properties.rootCauseId/rootCause to every result rather than
    # restructuring SARIF's flat one-result-per-finding shape. referenced_causes
    # spans `changes` and `scoped_only_changes` (computed once, up front) so a
    # scoped-only change's own caused_by_type can still correlate with a
    # regular change the same run of _root_cause_key_and_display would see --
    # mirrors the identical computation in cli_compare_fold.py's JSON fold-in.
    root_cause_mode = report_mode == "root-cause"
    referenced_causes: frozenset[str] = frozenset()
    if root_cause_mode:
        referenced_causes = frozenset(
            c.caused_by_type for c in changes if c.caused_by_type
        ) | frozenset(c.caused_by_type for c in scoped_only_changes if c.caused_by_type)

    # G29 Phase 3 follow-up: impactAssessment.root_cause_id/impact_group_id
    # (unlike properties.rootCauseId/rootCause above) are computed
    # unconditionally -- independent of report_mode -- mirroring
    # reporter.py's JSON output. Spans `changes` and `scoped_only_changes`
    # together so a scoped-only change's caused_by_type can still correlate
    # with a regular change, same as referenced_causes above.
    _impact_rc_lookup = root_cause_lookup_for_changes(changes + scoped_only_changes)
    # G29 Phase 6 follow-up: RootCauseCorrelator's own evidence-ranked
    # groups, over the same combined change set -- mirrors reporter.py's
    # JSON wiring so a finding's impactAssessment.root_cause_evidence agrees
    # across formats.
    _impact_rc_evidence = root_cause_evidence_lookup_for_changes(
        changes + scoped_only_changes
    )

    def _root_cause_for(
        caused_by_type: str | None,
        symbol: str | None,
        kind_value: str,
        finding_id: str,
    ) -> tuple[str, str] | None:
        if not root_cause_mode:
            return None
        key, root_display = _root_cause_key_and_display(
            caused_by_type,
            symbol,
            kind_value,
            finding_id,
            referenced_causes=referenced_causes,
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], root_display

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
            _result_for(
                change,
                result,
                severity_config,
                relevant_ids=relevant_ids,
                root_cause=_root_cause_for(
                    change.caused_by_type,
                    change.symbol,
                    change.kind.value,
                    _finding_id(change),
                ),
                impact_root_cause=_impact_rc_lookup.get(_finding_id(change)),
                impact_root_cause_evidence=_impact_rc_evidence.get(_finding_id(change)),
            )
        )

    # "Reporting must survive suppression": a `--suppress` rule silences a
    # finding's contribution to the verdict/exit code, but a SARIF consumer
    # (e.g. GitHub code scanning) must still be able to see *what* was
    # withheld and *why* -- SARIF 2.1.0's own mechanism for exactly this is
    # the per-result `suppressions` array (§3.27.24: "used to suppress
    # results that would otherwise be reported"), which every conformant
    # consumer already knows to hide from the default active-alerts view
    # without abicheck reinventing that convention as an ad-hoc property.
    # Previously suppressed findings were dropped from `results` entirely and
    # only a bare `properties.suppressedCount` integer survived -- no rule
    # provenance, no way to tell an ABI break apart from a cosmetic note
    # among the suppressed set. Registered through the same `_result_for`
    # every other finding uses, so a suppressed result carries the identical
    # properties (reachability, evidence, contract decision, ...) as an
    # unsuppressed one -- only the `suppressions` array and its provenance
    # are new. Not run through `show_only`/root-cause grouping: those are
    # display filters for the *active* result set, and a suppressed finding
    # is definitionally outside it.
    for change in result.suppressed_changes:
        rule_id = change.kind.value
        if rule_id not in rules_seen:
            rules_seen[rule_id] = _rule_for(change.kind)
        suppressed_result = _result_for(change, result, severity_config)
        suppressed_result["suppressions"] = [
            {
                "kind": "external",
                "justification": (
                    f"suppressed by --suppress rule: {change.suppression_rule}"
                    if change.suppression_rule
                    else "suppressed by --suppress rule"
                ),
            }
        ]
        sarif_results.append(suppressed_result)

    for change in scoped_only_changes:
        rule_id = change.kind.value
        if rule_id not in rules_seen:
            rules_seen[rule_id] = _rule_for(change.kind)
        sarif_results.append(
            _result_for(
                change,
                result,
                severity_config,
                relevant_ids=relevant_ids,
                # Codex review: proven by the real consumer's own import
                # table/execution, not an artifact-level library diff --
                # mirrors reporter.appcompat_to_json's own override for this
                # exact finding shape.
                evidence_status_override=EvidenceStatus.CONSUMER_PROVEN,
                root_cause=_root_cause_for(
                    change.caused_by_type,
                    change.symbol,
                    change.kind.value,
                    _finding_id(change),
                ),
                impact_root_cause=_impact_rc_lookup.get(_finding_id(change)),
                impact_root_cause_evidence=_impact_rc_evidence.get(_finding_id(change)),
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
            if severity_config is None
            or missing_contract_exit_code(severity_config) != 0
            else "compatible"
        )
        show_only_severities = (
            ShowOnlyFilter.parse(show_only).severities if show_only else frozenset()
        )
        if not show_only_severities or missing_severity in show_only_severities:
            for label in getattr(result, "scoped_missing_labels", ()) or ():
                rule_id = missing_contract_kind(gate_scope)
                if rule_id not in rules_seen:
                    rules_seen[rule_id] = _missing_contract_rule(rule_id)
                sarif_results.append(
                    _missing_contract_result(
                        label,
                        gate_scope,
                        severity_config,
                        # A missing-contract label has no caused_by_type; its
                        # `symbol` (the label) only becomes a *grouping* key
                        # when some other finding's caused_by_type names it
                        # (see referenced_causes above). There is no real
                        # Change/finding_id to disambiguate an unreferenced
                        # label by, so the label itself fills that role.
                        root_cause=_root_cause_for(None, label, rule_id, label),
                    )
                )

    gate_decision = gate_decision_for_result(result, severity_config)
    severity_gate = (
        _severity_gate_properties(gate_decision, severity_config)
        if gate_decision is not None and severity_config is not None
        else None
    )
    scoped_gate = _scoped_gate_properties(result)
    scoped_exit_code = getattr(result, "scoped_exit_code", None)

    # ADR-049 Phase 7: the orthogonal contract-coverage floor, folded into the
    # invocation's exit code exactly as the process folds it. This block is
    # SARIF's own machine-readable exit contract, and the comment below states
    # it mirrors what the CLI really exits with -- so leaving it out published
    # `exitCode: 0` beside a process that exited 1, and a consumer reading the
    # artifact accepted a run its own notifications said was gated (Codex
    # review, reproduced). `max`, for the same reason the process uses it: the
    # axis raises a clean 0 and never lowers a real break's code.
    #
    # `executionSuccessful` stays True and is *not* folded: per the SARIF spec
    # it reports whether the tool ran to completion, not whether it found
    # blocking issues -- the spec's own example pairs `exitCode: 1` with
    # `executionSuccessful: true`. Incomplete evidence is a finding about the
    # comparison, not a failed execution.
    from .contract_coverage_exit import coverage_exit_floor

    _coverage_floor = coverage_exit_floor(result)
    _base_exit_code = (
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
    )
    _exit_code = max(_base_exit_code, _coverage_floor)
    _exit_description = (
        f"{scoped_gate['gateVerdict']} (scoped: {scoped_gate['gateScope']})"
        if scoped_gate is not None
        else f"{result.verdict.value} (severity-gated)"
        if severity_gate is not None
        else result.verdict.value
    )
    if _coverage_floor:
        # Names the axis rather than only moving the number, so a reader of
        # the artifact alone can tell a coverage floor from a gate decision.
        _exit_description += " + incomplete contract coverage (exit 1)"

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
                        # ADR-049 plan Section 6.1: "SARIF emits deterministic
                        # properties and a tool-level coverage notification."
                        # A contract-coverage failure is not a result -- it is
                        # the tool saying the evidence needed to decide was
                        # missing -- so it belongs in
                        # `toolExecutionNotifications`, which is SARIF's own
                        # channel for exactly that, rather than as another
                        # `results[]` entry a consumer would count as a
                        # finding. Absent (not empty) when the run computed no
                        # contract context, so an ordinary document is
                        # unchanged.
                        **_coverage_notifications(result),
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
                        "exitCode": _exit_code,
                        "exitCodeDescription": _exit_description,
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
                    # ADR-067 D3: the raw-versus-effective counts belong in
                    # every projection, SARIF included -- `changeCount` is the
                    # *displayed* result set and `suppressedCount` covers one
                    # disposition, so neither answers "how many changes were
                    # detected, and how many actually gated". Run-level
                    # properties rather than per-result ones: this is a
                    # property of the comparison, and SARIF's own per-result
                    # `suppressions` array above already carries the
                    # per-finding half.
                    "dispositionAudit": compute_disposition_audit(
                        result, severity_config
                    ).to_dict(),
                    **(
                        {"severityGate": severity_gate}
                        if severity_gate is not None
                        else {}
                    ),
                    **({"scopedGate": scoped_gate} if scoped_gate is not None else {}),
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
                    # Codex review: mirrors reporter.py's JSON
                    # `policy_reclassify` (report_schema_version 2.30) --
                    # the active reclassify: rule set, via the same
                    # ReclassifyRule.to_report_dict() so the two can't drift.
                    **(
                        {
                            "policyReclassify": [
                                rule.to_report_dict()
                                for rule in _active_reclassify_rules
                            ]
                        }
                        if _active_reclassify_rules
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


def to_sarif_not_comparable(
    library: str, old_version: str, new_version: str, kind: str, message: str
) -> dict[str, Any]:
    """Render an ADR-050 D2 comparability-gate hard failure as SARIF 2.1.0.

    ``checker.compare``'s gate raises before any ``DiffResult`` exists, so
    :func:`to_sarif` (which reads ``result.changes``/``result.policy``/etc.)
    has nothing to render. Unlike an ordinary verdict, this is not a "no
    findings" run — the *comparison itself* did not complete — so per the
    SARIF spec ``invocations[0].executionSuccessful`` is ``False`` (not
    ``True`` with zero results, which would read as "compared cleanly") and
    the reason rides in a ``toolExecutionNotification`` (the spec's own
    mechanism for a tool-level problem, distinct from an analysis
    ``result``), rather than fabricating a synthetic finding-shaped result
    for something that isn't a finding.
    """
    tool_version = _tool_version()
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
                        "rules": [],
                    }
                },
                "invocations": [
                    {
                        "executionSuccessful": False,
                        "exitCode": 16,
                        "exitCodeDescription": f"not_comparable ({kind})",
                        "toolExecutionNotifications": [
                            {
                                "descriptor": {"id": kind},
                                "level": "error",
                                "message": {
                                    "text": (
                                        f"'{library}' old={old_version!r} "
                                        f"new={new_version!r} are not comparable: "
                                        f"{message}"
                                    )
                                },
                            }
                        ],
                    }
                ],
                "results": [],
                "properties": {
                    "abiVerdict": None,
                    "notComparable": True,
                    "reason": {"kind": kind, "message": message},
                    "oldVersion": old_version,
                    "newVersion": new_version,
                    "library": library,
                },
            }
        ],
    }


def to_sarif_str(
    result: DiffResult,
    indent: int = 2,
    *,
    show_only: str | None = None,
    report_mode: str = "full",
    severity_config: SeverityConfig | None = None,
) -> str:
    """Serialize DiffResult to a SARIF JSON string (via ``report.render_json``)."""
    return render_mapping_as_json(
        to_sarif(
            result,
            show_only=show_only,
            report_mode=report_mode,
            severity_config=severity_config,
        ),
        indent=indent,
    )


def write_sarif(result: DiffResult, path: Path) -> None:
    """Write SARIF output to *path*."""
    path.write_text(to_sarif_str(result), encoding="utf-8")
