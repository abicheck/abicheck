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

"""Per-change JSON projection: one ``Change`` -> its report dict.

Extracted from ``reporter.py`` (ADR-061 report/ migration) so ``report.document_builder``
no longer reaches back into the legacy root module. Behaviour is unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from datetime import date

    from ..policy.severity import KindSets, SeverityConfig
from ..checker import Change, DiffResult, Verdict
from ..impact.engine import assess_change
from ..model.change_catalog.kinds import ChangeKind, HasKind
from ..model.evidence_status import EvidenceStatus
from ..policy.classification import (
    evidence_status_for_result,
    impact_for,
    policy_kind_sets as _policy_kind_sets,
)
from ..policy.disposition_ledger import RuleProvenance
from ..report_model import VERDICT_TO_SEVERITY_LABEL as _VERDICT_TO_SEVERITY_LABEL
from ..report_summary import build_summary
from .change_annotations import (
    change_annotation_fields as _change_annotation_fields,
    removed_instantiations_field,
)
from .change_operation import entity_for_change, operation_for_change
from .contract_fields import (
    add_contract_evaluation_fields as _add_contract_evaluation_fields,
)
from .view import finding_id


def kind_to_severity(kind: ChangeKind, policy: str) -> str:
    """Map a ChangeKind to its severity label under the given policy (FIX-G)."""
    breaking, api_break, compatible, risk = _policy_kind_sets(policy)
    if kind in breaking:
        return "breaking"
    if kind in api_break:
        return "api_break"
    if kind in risk:
        return "risk"
    if kind in compatible:
        return "compatible"
    return "unknown"


VERDICT_TO_RECOMMENDED_ACTION: dict[Verdict, str] = {
    Verdict.BREAKING: "recompile_and_relink_required",
    Verdict.API_BREAK: "recompile_required",
    Verdict.COMPATIBLE_WITH_RISK: "verify_deployment_compatibility",
}


#: Per-kind reviewer guidance for a COMPATIBLE addition, keyed by
#: ``ChangeKind.value``. Falls back to ``DEFAULT_ADDITION_REVIEWER_ACTION``
#: for any addition kind not listed here.
ADDITION_REVIEWER_ACTION: dict[str, str] = {
    # Old binaries are unaffected, but exhaustive `switch`/sentinel-value
    # patterns in *source* consumers can miss the new case silently.
    "enum_member_added": "review_exhaustive_switches",
    # A semantic addition with no new symbol: the API existed but was
    # unstable; graduating it is a documentation/support-contract change,
    # not a binary one.
    "experimental_graduated": "document_stable_replacement",
}


DEFAULT_ADDITION_REVIEWER_ACTION = "confirm_public_api_intent"


def recommended_action_for_change(
    c: object,
    *,
    policy: str | None,
    kind_sets: KindSets | None,
    policy_file: object | None,
    today: date | None = None,
) -> str:
    """Return a structured, machine-readable next step for *c* (schema 2.4).

    Derived from the same effective verdict/category resolution
    ``severity``/``operation``/``finding_id`` already use, so it can never
    disagree with them for the same finding:

    - ``BREAKING`` → ``recompile_and_relink_required`` (binary ABI break)
    - ``API_BREAK`` → ``recompile_required`` (source-level break only)
    - ``COMPATIBLE_WITH_RISK`` → ``verify_deployment_compatibility``
    - ``COMPATIBLE`` additions and informational outcomes → ``no_action_required``
    - ``COMPATIBLE`` quality issues → ``review_recommended``
    """
    from ..policy.severity import (
        IssueCategory,
        classify_effective_change,
        effective_verdict_for_change,
    )

    verdict = effective_verdict_for_change(
        cast(HasKind, c),
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
        today=today,
    )
    action = VERDICT_TO_RECOMMENDED_ACTION.get(verdict)
    if action is not None:
        return action
    # COMPATIBLE: distinguish a genuine addition (nothing to do) from a
    # quality issue (compatible, but worth a look) via the same category
    # classification the severity JSON block uses.
    category = classify_effective_change(
        cast(HasKind, c),
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
        today=today,
    )
    # An informational outcome (ADR-078) records that nothing changed.
    return (
        "no_action_required"
        if category in (IssueCategory.ADDITION, IssueCategory.INFORMATIONAL)
        else "review_recommended"
    )


def reviewer_action_for_change(
    c: object,
    *,
    policy: str | None,
    kind_sets: KindSets | None,
    policy_file: object | None,
    today: date | None = None,
) -> str | None:
    """Finer-grained reviewer guidance for a COMPATIBLE addition (additive).

    ``recommended_action`` collapses every addition to one value,
    ``no_action_required`` — accurate for the *old binary consumer*
    (nothing to recompile, nothing to relink), but a reviewer approving a
    new public export almost always has something to check: was it
    intentional, does it need a release note, do exhaustive switches need
    the new case. This field carries that reviewer-facing nuance without
    changing ``recommended_action``'s existing meaning or schema enum.
    Returns ``None`` for every non-addition finding, since those already
    have reviewer-actionable guidance via ``recommended_action`` itself.
    """
    from ..policy.severity import IssueCategory, classify_effective_change

    category = classify_effective_change(
        cast(HasKind, c),
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
        today=today,
    )
    if category != IssueCategory.ADDITION:
        return None
    kind = getattr(c, "kind", None)
    kind_val = kind.value if kind else ""
    return ADDITION_REVIEWER_ACTION.get(kind_val, DEFAULT_ADDITION_REVIEWER_ACTION)


def change_reachability_fields(c: Any) -> dict[str, Any]:
    """Structured reachability evidence and graph-impact data for one change.

    Machine-readable counterparts to what used to be description prose only --
    see the per-entry comments for which ADR each came from.
    """
    out: dict[str, Any] = {}
    # ADR-044 P1 item 4 — structured reachability evidence (previously
    # description-text-only): whether a suppression rule's reachability gate
    # tagged this change public-reachable, how (layout/call-graph/direct),
    # and the shortest proof path, so a machine consumer doesn't need to
    # parse the suppression_would_hide_public_break diagnostic's prose.
    if getattr(c, "public_reachable", False):
        out["public_reachable"] = True
        reach_kind = getattr(c, "reachability_kind", None)
        if reach_kind:
            out["reachability_kind"] = reach_kind
        proof_path = getattr(c, "reachability_proof_path", None)
        if proof_path:
            out["reachability_proof_path"] = proof_path
    # G31 Phase B B3 (ADR-048) — structured graph impact/proof-path data:
    # the machine-readable counterpart of reachability_proof_path's prose,
    # as a list of node/edge reference dicts, plus which public root(s) it
    # traces back to and whether the dependency is direct or transitive.
    affected_roots = getattr(c, "affected_public_roots", None)
    if affected_roots:
        out["affected_public_roots"] = affected_roots
    impact_path = getattr(c, "impact_proof_path", None)
    if impact_path:
        out["impact_proof_path"] = impact_path
    impact_direct = getattr(c, "impact_is_direct", None)
    if impact_direct is not None:
        out["impact_is_direct"] = impact_direct
    return out


def reclassified_by_for_change(
    c: object, policy_file: object | None, *, today: date | None = None
) -> str | None:
    """``reclassified_by`` audit value for *c*, or ``None`` -- shared by
    :func:`change_to_dict` and leaf mode's ``_leaf_entry`` (Codex review)
    so the two entry builders can't drift on this field. Falls back to
    ``rule.to_verdict.value`` rather than ``rule.to``, since a directly-
    constructed ``ReclassifyRule`` could leave the latter empty/mismatched
    -- see ``severity.reclassify_rule_for_change`` for the full precedence.
    *today*, forwarded there, keeps this agreeing with an already-frozen
    ``ReportEnvelope`` (Codex review, fresh evidence).
    """
    from .finding import reclassified_by_label

    return reclassified_by_label(c, policy_file, today)


def change_to_dict(
    c: object,
    *,
    policy: str = "strict_abi",
    kind_sets: tuple[
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
    ]
    | None = None,
    policy_file: object | None = None,
    evidence_status_override: EvidenceStatus | None = None,
    root_cause: tuple[str, str] | None = None,
    root_cause_evidence: dict[str, object] | None = None,
    impact_group_id: str | None = None,
    evidence_tiers: Sequence[str] = (),
    severity_config: SeverityConfig | None = None,
    today: date | None = None,
) -> dict[str, object]:
    """Convert a Change to a JSON-serializable dict with impact and metadata.

    ``evidence_status_override`` lets a caller assert a stronger epistemic
    status than the finding's own classification implies — e.g.
    ``appcompat_to_json`` (removed) marks every finding it already proved a specific
    consumer depends on as ``EvidenceStatus.CONSUMER_PROVEN``, regardless of
    the finding's own kind.

    ``root_cause`` (G29 Phase 3 follow-up, ADR-052), when given, is *c*'s
    ``(root_cause_id, root_display)`` pair — forwarded to
    :func:`~abicheck.impact.engine.assess_change` so
    ``impact_assessment.root_cause_id`` is populated. Callers resolve this
    once per report via
    :func:`~abicheck.report.root_cause_lookup.root_cause_lookup_for_changes` rather
    than recomputing it per change. ``root_cause_evidence`` (G29 Phase 6) is
    the analogous per-change entry from
    :func:`~abicheck.impact.correlation.correlate_root_causes`, resolved the
    same way.

    ``evidence_tiers`` is the owning comparison's ``DiffResult.evidence_tiers``
    (P0 evidence-provider audit) — when a caller has it (most do; the
    default ``()`` reads as "unknown", not "absent", matching
    :func:`~.checker_policy.has_binary_evidence`'s own convention), it
    downgrades an otherwise ``ARTIFACT_PROVEN`` status to ``UNATTRIBUTED``
    for a comparison positively known to have never examined a real binary.

    ``severity_config`` is the run's resolved gate configuration, when it has
    one — it decides ADR-049's per-finding ``gate_contribution`` (see the call
    to :func:`~abicheck.policy.gate_contribution.gate_contribution_for_change` below). ``None``
    means the legacy verdict-based scheme, not "no gate".

    ``today``, forwarded to every date-sensitive resolution below (verdict,
    reclassify attribution, recommended/reviewer action, gate contribution),
    keeps this agreeing with an already-frozen ``ReportEnvelope`` (Codex
    review, fresh evidence).
    """
    kind = getattr(c, "kind", None)
    reclassified_by: str | None = None
    if isinstance(kind, ChangeKind) and kind_sets:
        from ..policy.severity import effective_verdict_for_change

        verdict = effective_verdict_for_change(
            cast(HasKind, c),
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
            today=today,
        )
        severity = _VERDICT_TO_SEVERITY_LABEL.get(verdict, "unknown")
        # Per-change reclassify: disclosure (Codex review) -- see
        # reclassified_by_for_change's own docstring.
        reclassified_by = reclassified_by_for_change(c, policy_file, today=today)
    elif kind:
        severity = kind_to_severity(kind, policy)
    else:
        severity = "unknown"
    evidence_status = evidence_status_override
    if evidence_status is None and isinstance(kind, ChangeKind):
        evidence_status = evidence_status_for_result(cast(HasKind, c), evidence_tiers)
    d: dict[str, object] = {
        "kind": kind.value if kind else "",
        "symbol": getattr(c, "symbol", ""),
        "description": getattr(c, "description", ""),
        "old_value": getattr(c, "old_value", None),
        "new_value": getattr(c, "new_value", None),
        "review_evidence": getattr(c, "review_evidence", None),
        "severity": severity,
    }
    if reclassified_by:
        d["reclassified_by"] = reclassified_by
    # Two per-declaration blocks, omitted when absent (see their own field
    # docs on Change): `demangled_symbol`, a readable name for a finding whose
    # old-side declaration is export-table-only (`symbol`/`old_value` stay raw
    # mangled -- the "machine format" branch demangle.demangle_text
    # describes), and `surface_facts`, the three split surface facts emitted
    # whole, "unknown" included (report schema 4.4, model/surface_facts.py).
    if demangled_symbol := resolve_demangled_symbol(c):
        d["demangled_symbol"] = demangled_symbol
    if surface_facts := getattr(c, "surface_facts", None):
        d["surface_facts"] = dict(surface_facts)
    if isinstance(kind, ChangeKind):
        d["operation"] = operation_for_change(c, kind.value)
        d["entity"] = entity_for_change(c, kind.value)
        d["finding_id"] = finding_id(c)
        # Backend-independent sibling of finding_id (schema 2.36).
        from ..compare.finding_identity import report_canonical_finding_id

        d["canonical_finding_id"] = report_canonical_finding_id(c)
        d["recommended_action"] = recommended_action_for_change(
            c, policy=policy, kind_sets=kind_sets, policy_file=policy_file, today=today
        )
        reviewer_action = reviewer_action_for_change(
            c, policy=policy, kind_sets=kind_sets, policy_file=policy_file, today=today
        )
        if reviewer_action is not None:
            d["reviewer_action"] = reviewer_action
    if evidence_status is not None:
        d["evidence_status"] = evidence_status.value
    # Impact explanation. Finding C(ii): pass the already-computed
    # evidence_status so a downgraded (UNATTRIBUTED) finding's impact text
    # gets its evidence caveat rather than the same unconditional claim an
    # ARTIFACT_PROVEN finding of the same kind carries.
    if kind:
        impact = impact_for(kind, evidence_status)
        if impact:
            d["impact"] = impact
    d.update(_change_annotation_fields(c))
    d.update(change_reachability_fields(c))
    # G29 Phase 3 slice 1 (ADR-052): reachability_state has existed on Change
    # since PR #607 but was never serialized -- without it, a JSON consumer
    # cannot tell a PROVEN_UNREACHABLE finding apart from one the graph walk
    # never examined at all (UNKNOWN), since both leave public_reachable
    # False. impact_assessment is the unified read view over the scattered
    # reachability/impact fields above; only emitted when it carries
    # information beyond the all-defaults case, matching this function's own
    # convention of not padding every plain finding with an empty object.
    assessment = assess_change(
        c,
        root_cause=root_cause,
        root_cause_evidence=root_cause_evidence,
        impact_group_id=impact_group_id,
    )
    d["reachability_state"] = assessment.reachability_state.value
    if assessment.has_signal():
        d["impact_assessment"] = assessment.to_dict()
    # ADR-049 D1's `gate_contribution`, computed for a `changes` entry (the
    # only findings that reach a gate at all) from the same severity/legacy
    # scheme the run itself exits on -- see
    # `gate_contribution.gate_contribution_for_change`. `severity_config` is None on
    # the legacy scheme, where the contribution is the finding's own
    # verdict-to-exit mapping.
    from ..policy.gate_contribution import gate_contribution_for_change

    _add_contract_evaluation_fields(
        d,
        c,
        gate_contribution=(
            gate_contribution_for_change(
                cast(HasKind, c),
                severity_config,
                policy=policy,
                kind_sets=kind_sets,
                policy_file=policy_file,
                today=today,
            )
            if isinstance(kind, ChangeKind)
            else 0
        ),
    )
    return d


def suppressed_change_entry(
    c: Change,
    *,
    root_cause: tuple[str, str] | None = None,
    root_cause_evidence: dict[str, object] | None = None,
    impact_group_id: str | None = None,
    rule: RuleProvenance | None = None,
) -> dict[str, object]:
    """Minimal audit-trail entry for one suppressed change, plus the
    impact-assessment decision it was actually suppressed with (G29 Phase 3
    slice 1, ADR-052 follow-up, Codex review: this is the one call site that
    passes ``suppressed=True`` -- without it, ``decision.state:
    "suppressed"`` was advertised but never actually reachable from
    production reporting).

    ``root_cause``/``root_cause_evidence`` (G29 Phase 3/6 follow-ups): the
    caller resolves both from a lookup scoped to ``result.suppressed_changes``
    itself -- a suppressed finding's root cause is computed relative to other
    *suppressed* findings, not folded together with the kept ``changes[]``
    list's own grouping.

    ``rule`` (ADR-067 D3) is the provenance of the suppression that actually
    fired, looked up by object identity from the run's own ledger — not
    re-evaluated here, since the rule set is matched against fields that may
    have been enriched after the match, and a second evaluation could
    therefore name a different rule than the one that hid the finding.
    """
    entry: dict[str, object] = {
        "kind": c.kind.value,
        "symbol": c.symbol,
        "description": c.description,
    }
    if rule is not None:
        entry["rule"] = rule.to_dict()
    assessment = assess_change(
        c,
        suppressed=True,
        root_cause=root_cause,
        root_cause_evidence=root_cause_evidence,
        impact_group_id=impact_group_id,
    )
    entry["reachability_state"] = assessment.reachability_state.value
    if assessment.has_signal():
        entry["impact_assessment"] = assessment.to_dict()
    if getattr(c, "symbol_binding", None):
        entry["symbol_binding"] = c.symbol_binding
    removed_insts = removed_instantiations_field(c)
    if removed_insts is not None:
        entry["removed_instantiations"] = removed_insts
    # Codex review, fresh evidence: an elf_only-visibility removal's
    # demangled_symbol (schema 3.14) reaches every other machine-format
    # projection of this same Change -- a suppressed one must not lose it
    # just because it's rendered through this narrower audit-entry shape
    # instead of change_to_dict.
    demangled_symbol = resolve_demangled_symbol(c)
    if demangled_symbol:
        entry["demangled_symbol"] = demangled_symbol
    # ADR-049 Phase 3 (Codex review, fresh evidence): suppression is a
    # display/gate decision, not a reason to erase the contract-relevance
    # decision checker._apply_contract_evaluation_shadow already stamped on
    # this Change -- a no-op when contract_evaluation was never requested,
    # mirroring this helper's other callers.
    _add_contract_evaluation_fields(entry, c)
    return entry


def filtered_internal_entry(c: Change) -> dict[str, object]:
    entry: dict[str, object] = {
        "kind": c.kind.value,
        "symbol": c.symbol,
        "description": c.description,
    }
    # ADR-049 Phase 3 (Codex review, fresh evidence): result.out_of_surface_changes
    # is the same list _apply_contract_evaluation_shadow already stamps
    # (folded into all_changes alongside `kept`) -- this second, independent
    # serialization of the identical Change objects
    # (scope.filtered_internal_changes, distinct from
    # surface_scope.out_of_surface_changes above) never read the fields, so a
    # --contract consumer of this established ledger missed the decision even
    # though the sibling ledger already carried it.
    _add_contract_evaluation_fields(entry, c)
    return entry


def metadata_dict(meta: object | None) -> dict[str, object] | None:
    if meta is None:
        return None
    return {
        "path": getattr(meta, "path", ""),
        "sha256": getattr(meta, "sha256", ""),
        "size_bytes": getattr(meta, "size_bytes", 0),
    }


def scope_dict(result: DiffResult) -> dict[str, object] | None:
    """Machine-readable public-surface scoping block (ADR-024, issue #235).

    Only emitted when ``--scope-public-headers`` was requested, so default
    reports are unchanged. Records whether scoping resolved or fell back to the
    full export table (``manual_review_required``), the public additions count,
    and the audit ledger of findings filtered as internal/private.
    """
    if not result.scope_to_public_surface:
        return None
    summary = build_summary(result)
    return {
        "public_headers_applied": True,
        "resolved": result.scope_resolved,
        "fell_back": not result.scope_resolved,
        "manual_review_required": not result.scope_resolved,
        "public_additions": summary.compatible_additions,
        "filtered_internal_count": result.out_of_surface_count,
        "filtered_internal_changes": [
            filtered_internal_entry(c) for c in result.out_of_surface_changes
        ],
    }


def resolve_demangled_symbol(c: Any) -> str | None:
    """The demangled spelling of *c*'s symbol, for a machine projection.

    Plan slice 7o, deliverable 2: every machine format carries **both**
    names -- the exact mangled ``symbol`` it always carried, plus the
    readable one. Before this, ``demangled_symbol`` was emitted only for an
    ``ELF_ONLY``-visibility finding (``compare.elf_only_demangle``, the one
    case whose ``symbol``/``description`` were *unreadable* without it), so
    a JSON/SARIF consumer of an ordinary C++ removal had the mangled name
    and no way to show a human one. Reading a report and grepping the
    binary are then the same string, which is what makes the human side's
    automatic demangling lossless.

    Returns ``None`` when the symbol is not an Itanium-mangled name, or no
    demangler is available -- never a copy of ``symbol`` itself, so a
    consumer can treat the field's presence as "this differs from symbol".
    A finding that already carries one (the ``ELF_ONLY`` path) keeps it
    verbatim rather than being re-derived.
    """
    existing = getattr(c, "demangled_symbol", None)
    if existing:
        return str(existing)
    symbol = getattr(c, "symbol", "") or ""
    if not symbol:
        return None
    from ..demangle import demangle

    # ``accept_macho_prefix=True`` (Codex review, PR #1284): clang's own
    # ``mangledName`` carries the platform global-symbol prefix on macOS, so a
    # Mach-O finding's symbol can read ``__ZN3lib4goneEi``. The strict default
    # rejects that spelling *before* consulting the cache, so a Mach-O run
    # warmed the batch cache (``prewarm_change_demangling`` opts in) and then
    # emitted no ``demangled_symbol`` at all -- exactly the gap this field
    # exists to close. This is report-only resolution, which is the case
    # ``_is_itanium_mangled``'s own docstring names as the one that may opt
    # in; the correctness-critical symbol-matching callers keep the strict
    # default.
    demangled = demangle(symbol, accept_macho_prefix=True)
    return demangled if demangled and demangled != symbol else None


def prewarm_change_demangling(result: Any) -> None:
    """Batch-demangle every finding's symbol once, ahead of serialization.

    Plan slice 7o makes every finding's dict resolve a ``demangled_symbol``
    (:func:`resolve_demangled_symbol`), so one batched ``c++filt`` call up
    front is what keeps a host without the in-process ``cxxfilt`` package
    from forking a subprocess per distinct symbol -- the same prewarm
    ``appcompat_html.py`` (removed) already does for its own per-row demangling.

    Called from ``report.document_builder.build_report_document``, not from
    ``to_json`` (Codex review, PR #1284): the document builder is the one
    chokepoint *every* projection goes through -- ``to_json``, the
    ``ReportEnvelope`` path (``service_render.render_output``), and
    ``report.document_builder``'s own callers -- so prewarming at ``to_json`` alone
    left the envelope path, which is what the CLI actually renders
    through, forking per symbol before it was ever reached.
    """
    from ..demangle import prewarm_demangle_batch

    # Every collection whose entries reach `change_to_dict`, not only the
    # headline one. `scoped_only_changes` is the one that is easy to miss:
    # `apply_scoped_gate` synthesizes it *after* the document is built, for
    # a `--used-by`/`consumers.required_symbols` run, and serializes it through the
    # same path -- so leaving it out put every consumer-required C++ symbol
    # back on the per-symbol subprocess it is the point of this function to
    # avoid (Codex review, PR #1284).
    changes = list(getattr(result, "changes", ()) or ())
    for attr in (
        "suppressed_changes",
        "out_of_surface_changes",
        "scoped_only_changes",
        "reconciled_changes",
    ):
        changes += list(getattr(result, attr, ()) or ())
    if changes:
        prewarm_demangle_batch(changes, attrs=("symbol",))
