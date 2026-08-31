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

"""The report-level blocks every JSON path attaches after the findings.

Split out of :mod:`abicheck.reporter`, which is at the 2000-line hard cap --
same reason ``reporter_markdown``/``report_summary`` live beside it. Every
import here is function-local for the reason the original site documented:
``contract_evidence``/``contract_context_io`` reach
``compatibility_evaluation_config`` -> ``checker_policy``, which a module-level
import would pull into every consumer of a ``DiffResult``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .impact.use_case_impact import add_use_case_impact
from .report.document import ReportDocument
from .report.render_json import render_json

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .checker_types import Change, DiffResult
    from .severity import SeverityConfig


def add_contract_context(
    d: dict[str, Any],
    result: DiffResult,
    displayed: Sequence[Change] | None = None,
    *,
    require_complete_analysis: bool = False,
    severity_config: SeverityConfig | None = None,
    include_exit_decision: bool = True,
) -> None:
    """ADR-049 Phase 4's persisted contract blocks, plus P0.4's
    ``analysis_assurance``/``analysis_assurance_exit_contribution`` and CLI
    cleanup phase two PR E's ``annotations`` (schema 2.43; see
    :func:`add_annotations`) -- all piggybacked here, unguarded below, to
    stay under the file-size cap, exactly the reason ``analysis_assurance``
    already gives.
    ``contract_context`` itself stays opt-in
    (``compare(..., contract_evaluation=True)``), serialized via
    :mod:`abicheck.contract_context_io` to match
    :func:`~abicheck.contract_replay.replay_original_decisions`. Called from
    all three JSON paths, same as ``_add_surface_scope``/``_add_reconciled``.
    """
    from .analysis_assurance import (
        analysis_assurance_exit_contribution,
        analysis_assurance_report_dict,
    )

    if (block := analysis_assurance_report_dict(result)) is not None:
        d["analysis_assurance"] = block
        # Persisted alongside the block itself, not unconditionally: a
        # `result` carrying no real `AnalysisAssurance` (a hand-built
        # object, e.g. in a test) has nothing to report a contribution
        # *for* either. Read by `aggregate.py`'s `_analysis_assurance_exit`
        # via the same document-shape traversal `contract_coverage_exit_
        # contribution` already uses -- without persisting this, a compare
        # report whose severity/compatibility gate read a clean 0 while
        # this axis independently floored the *real* exit to 1 fed
        # `abicheck aggregate` a green result for that report, since
        # neither `GateInfo.from_report_data` nor `from_scan_report` reads
        # this orthogonal axis (Codex review, PR #780). `0` covers both
        # "the flag was never given" and "given but already complete".
        d["analysis_assurance_exit_contribution"] = (
            analysis_assurance_exit_contribution(
                result, require_complete=require_complete_analysis
            )
        )
    # CLI cleanup phase two, PR G1: the same canonical `ExitDecision`
    # `cli._exit_with_severity_or_verdict` resolves for the real process
    # exit, persisted here so a report reader doesn't have to re-derive it
    # from `severity.exit_code`/`contract_coverage_exit_contribution`/
    # `analysis_assurance_exit_contribution` separately. `severity_config
    # is not None` is the same signal every other block in this module and
    # in `reporter.py` already uses to mean "the severity-aware scheme is in
    # effect" (`cli_compare_helpers.report_severity` is `None` whenever
    # `resolved_cfg.exit_code_scheme != "severity"`), so this reproduces
    # `_exit_with_severity_or_verdict`'s own scheme selection rather than
    # guessing at a new one. Unconditional for a native `compare` call --
    # unlike `contract_context` below, every comparison has a compatibility
    # contribution, so there is always a decision to report, not just under
    # `--contract`. `include_exit_decision=False` (only `compat/cli.py`
    # passes this) skips it entirely: `compat check`'s own process exit
    # follows an unrelated 0/1/2 ABICC-style scheme, so this block's
    # native-scheme `code` would disagree with the real compat exit for the
    # same run (Codex review).
    scheme = "severity" if severity_config is not None else "legacy"
    if include_exit_decision:
        from .exit_decision import resolve_compare_exit_decision

        d["exit"] = resolve_compare_exit_decision(
            result,
            severity_config,
            scheme,
            require_complete_analysis=require_complete_analysis,
        ).to_dict()
    add_annotations(d, result, severity_config=severity_config)
    add_use_case_impact(d, result, displayed)
    # Same `include_exit_decision` gate as the `exit` block above, for the
    # identical reason (Codex review, PR #803, fresh evidence): the digest's
    # `gate.exit_code_scheme`/`gate.severity.*` axes describe the *native*
    # legacy/severity scheme, and carry no representation of `compat
    # check`'s own transform/gate options (`-strict`, `-source`/`-binary`,
    # `-warn-newsym`, ...) -- which are a materially different option
    # vocabulary belonging to a different front end (`compat/cli.py`), not a
    # gap in this digest's own field set to widen. Emitting it anyway would
    # let two `compat check --report-format json` reports that differ only
    # by `-strict` (a real verdict-changing transform) carry the identical
    # digest, silently claiming "same effective configuration" the same way
    # the `exit` block would if it weren't already skipped here.
    if include_exit_decision:
        add_effective_config_digest(
            d,
            result,
            severity_config=severity_config,
            exit_code_scheme=scheme,
            require_complete_analysis=require_complete_analysis,
        )

    ctx = result.contract_context
    if ctx is None:
        return
    from .contract_context_io import persisted_context_to_dict
    from .contract_evidence import PersistedContractContext

    # `DiffResult.contract_context` is typed `object` (its real type reaches
    # `compatibility_evaluation_config` -> `checker_policy`, which every
    # consumer of `DiffResult` would then import), so narrow it here rather
    # than suppressing the argument type -- an `isinstance` check is also a
    # real guard against a caller having stuffed something else into an
    # untyped field (CodeRabbit review).
    if not isinstance(ctx, PersistedContractContext):
        return
    d["contract_context"] = persisted_context_to_dict(ctx)
    # ADR-049 Phase 5's *sibling* ledger (plan Section 6.1). It sits beside
    # the findings, not among them, because that is what makes it
    # unsuppressible: a coverage failure is not a `Change`, so
    # `checker._filter_suppressed_changes` -- the one place suppression is
    # applied -- can never see one, and "ordinary change suppressions ...
    # cannot suppress a provider/domain coverage failure" (Section 6.2) is a
    # structural fact rather than a rule something has to remember to
    # enforce. Emitted as `[]` rather than omitted when there are none: an
    # empty ledger is the real, checkable answer "this domain closed", which
    # an absent key could not distinguish from "not computed".
    from .contract_coverage_exit import coverage_exit_for_context
    from .contract_coverage_ledger import coverage_failures_for_context

    failures = coverage_failures_for_context(ctx)
    d["contract_coverage_failures"] = [f.to_dict() for f in failures]
    # ADR-049 Phase 7: what the ledger contributes to the exit code, now
    # actually applied rather than merely stated. Derived by the same
    # function the exit path uses, so the number a user reads is the one
    # that gated them -- including `contract.unresolved=warn` zeroing it
    # while the failures above stay listed, which is what accepting
    # incomplete assurance means as opposed to hiding it.
    d["contract_coverage_exit_contribution"] = coverage_exit_for_context(ctx)


def add_effective_config_digest(
    d: dict[str, Any],
    result: DiffResult,
    *,
    severity_config: SeverityConfig | None = None,
    exit_code_scheme: str | None = None,
    require_complete_analysis: bool = False,
) -> None:
    """CLI cleanup phase two, PR B: the effective-configuration digest --
    "one effective configuration ... with the same effective-config digest
    recorded in every report", computed identically for `compare` (via
    :func:`add_contract_context`), the directory/package release fan-out
    (same call), :func:`~abicheck.reporter.to_stat_json` (which bypasses
    :func:`add_contract_context` entirely, so it calls this directly), and
    `scan --against` (:mod:`abicheck.cli_scan_baseline`, same function).
    :func:`~abicheck.effective_config_digest.effective_config_fields` itself
    picks the richest tier this comparison actually resolved (a full
    ``CompatibilityEvaluationConfig`` under ``--contract``/``--pack``, else
    the policy/gate fields every comparison resolves regardless). The
    caller (:func:`add_contract_context`) gates this the same way it gates
    the ``exit`` block -- skipped when ``include_exit_decision=False``
    (``compat/cli.py``'s ``compat check --report-format json``, Codex
    review, PR #803, fresh evidence): the digest's gate axes describe the
    *native* legacy/severity scheme and carry no representation of
    ``compat check``'s own transform options (``-strict``, ``-source``/
    ``-binary``, ...), so emitting it there would let two behaviorally
    different compat reports claim the identical effective configuration.
    Both fields are schema-optional for exactly this reason, mirroring
    ``exit``'s own optional status.

    *exit_code_scheme*, when given, is the caller's own already-resolved
    scheme (e.g. `scan --against`'s ``exit_scheme``, which additionally
    depends on its own ``exit_code_scheme`` parameter, not just whether
    *severity_config* is set) -- reused rather than re-derived, so the
    digest can never disagree with the ``exit`` block it sits beside. The
    default (``None``) reproduces the same ``"severity" if severity_config
    is not None else "legacy"`` derivation :func:`add_contract_context`
    already uses for that block.

    *require_complete_analysis* mirrors the identically-named CLI/API flag
    (P0.4's analysis-completeness gate, `--require-complete-analysis`) --
    it genuinely changes gating behavior (an otherwise-identical
    incomplete-evidence result exits 0 vs. 1 depending on it) but is not a
    D7 ``CompatibilityEvaluationConfig`` namespace field, so it is threaded
    here the same way *severity_config*/*exit_code_scheme* already are
    rather than silently absent from the fingerprint (Codex review,
    PR #803, fresh evidence).
    """
    from .effective_config_digest import (
        effective_config_digest,
        effective_config_fields,
    )

    scheme = exit_code_scheme or (
        "severity" if severity_config is not None else "legacy"
    )
    ec_fields = effective_config_fields(
        result,
        severity_config=severity_config,
        exit_code_scheme=scheme,
        require_complete_analysis=require_complete_analysis,
    )
    d["effective_config_digest"] = effective_config_digest(ec_fields)
    d["effective_config_fields"] = ec_fields


def suppression_rule_label(rule: Any, index: int) -> str:
    """A human-readable identifier for a suppression rule with no index of
    its own (``SuppressionAudit``'s per-bucket lists don't carry the rule's
    position in the original file, so *index* is only this bucket's own
    position -- misleading as a rule identifier, e.g. the second rule in
    the file being the only stale one renders as ``rule#0`` -- Codex/
    CodeRabbit review, fresh evidence).

    Always appends the rule's own matching selectors (every populated one,
    not just the first -- ``Suppression`` selectors combine conjunctively)
    alongside ``label``/``reason`` when either is set, not only as a
    fallback for an unlabeled rule (Codex review, fresh evidence, third
    round): a ``label``/``reason`` is a free-form grouping tag with no
    uniqueness guarantee, so two distinct rules sharing one would otherwise
    still render as the identical, ambiguous identifier. Falls back to
    *index* only for a rule with none of label/reason/any selector set at
    all.

    ADR-061 Phase 2 item 5: shared by :func:`add_suppression_audit` below
    (the JSON ``suppression_audit`` key) and
    ``cli_compare_fold._fold_suppression_audit_into_text``'s markdown/text/
    review append -- lives here (not ``reporter.py``, which is at its own
    debt-ledger no-growth cap) so both a ``report``-classified caller
    (``reporter.py``) and a ``frontends``-classified caller
    (``cli_compare_fold.py``, which may import ``report`` but not the
    reverse) can reach it from one place.
    """
    label: str | None = getattr(rule, "label", None) or getattr(rule, "reason", None)
    parts = [
        f"{field}={value}"
        for field in (
            "symbol",
            "symbol_pattern",
            "type_pattern",
            "member_name",
            "change_kind",
            "source_location",
            "namespace",
            "entity_namespace",
            "cause_namespace",
            # Canonical (backend-independent) identity selector (Codex
            # review, fresh evidence, PR #753): a finding_id-only rule with
            # no label/reason previously rendered as a bare `rule#<index>`,
            # indistinguishable from every other unlabeled rule in the same
            # bucket -- the exact ambiguity every other selector here
            # already avoids.
            "finding_id",
            # Symbol-linkage selector (Codex review, fresh evidence): two
            # rules sharing every other selector but differing on `binding`
            # (e.g. one `weak`, one `global`) match disjoint findings and
            # must not render identically -- same reasoning as every other
            # selector in this tuple.
            "binding",
            # ADR-044 D2 reachability gates (Codex review, fresh evidence):
            # these affect which findings a rule matches exactly like the
            # selectors above -- two rules sharing every selector but
            # differing on reachability (e.g. "public-only" vs.
            # "unreachable-only") match disjoint findings and must not
            # render identically. allow_public_break/allow_unknown_reachability
            # default False, so they're omitted (same "if truthy" convention
            # as every other field here) unless a rule actually opted in.
            "reachability",
            "allow_public_break",
            "allow_unknown_reachability",
        )
        if (value := getattr(rule, field, None))
    ]
    # expires (Codex review, fresh evidence) is not a matching selector --
    # two rules with identical selectors but different expiry dates match
    # the exact same findings -- but it's exactly what distinguishes them
    # in the expired_rules/near_expiry_rules buckets themselves, where an
    # otherwise-identical pair would otherwise render as the same label
    # with no way to tell which deadline belongs to which rule.
    expires = getattr(rule, "expires", None)
    if expires is not None:
        parts.append(f"expires={expires.isoformat()}")
    selectors = ", ".join(parts)
    if label and selectors:
        return f"{label} ({selectors})"
    if label:
        return label
    if selectors:
        return selectors
    return f"rule#{index}"


def add_suppression_audit(d: dict[str, Any], result: DiffResult) -> None:
    """Add the ``--audit-suppressions`` ledger as a real JSON key, when present.

    ADR-061 Phase 2 item 5 (post-render mutation): ``result.suppression_
    audit`` is attached by ``cli_compare_helpers._attach_suppression_audit``
    before rendering -- this reads it directly during document
    construction, so the key is part of the JSON the first time it is
    rendered rather than ``cli_compare_fold._fold_suppression_audit_into_
    text`` re-parsing already-rendered JSON text to splice it in
    afterwards. ``None`` (the default, and every run that did not pass
    ``--audit-suppressions``) omits the key entirely, matching the
    pre-existing behavior exactly.
    """
    from .suppression import SuppressionAudit

    audit = result.suppression_audit
    if not isinstance(audit, SuppressionAudit):
        return
    d["suppression_audit"] = {
        "total_rules": audit.total_rules,
        "stale_rules": [
            suppression_rule_label(r, i) for i, r in enumerate(audit.stale_rules)
        ],
        "high_risk_matches": [
            {
                "rule": suppression_rule_label(rule, i),
                "kind": change.kind.value,
                "symbol": change.symbol,
            }
            for i, (rule, change) in enumerate(audit.high_risk_matches)
        ],
        "expired_rules": [
            suppression_rule_label(r, i) for i, r in enumerate(audit.expired_rules)
        ],
        "near_expiry_rules": [
            suppression_rule_label(r, i)
            for i, r in enumerate(audit.near_expiry_rules)
        ],
    }


def add_evidence_depth(d: dict[str, Any], result: DiffResult) -> None:
    """Add ``old_evidence_depth``/``new_evidence_depth``, when resolved.

    ADR-061 Phase 2 item 5 (post-render mutation): ``result.old_evidence_
    depth``/``result.new_evidence_depth`` are resolved once by
    ``cli_compare_helpers`` (mirroring ``analysis_assurance``'s identical
    out-of-band-pack resolution) and attached before rendering -- this
    reads them directly during document construction instead of
    ``cli_compare_helpers._fold_evidence_depth_into_json`` re-parsing
    already-rendered JSON text afterwards. ``None`` (the default -- every
    caller other than ``compare``'s own JSON path) omits both keys
    entirely, matching the pre-existing behavior exactly.
    """
    if result.old_evidence_depth is not None:
        d["old_evidence_depth"] = result.old_evidence_depth
    if result.new_evidence_depth is not None:
        d["new_evidence_depth"] = result.new_evidence_depth


def render_json_with_side_facts(
    d: dict[str, Any], result: DiffResult, *, indent: int
) -> str:
    """The one shared tail of every ``reporter.py`` JSON builder: fold in
    the two ADR-061 Phase 2 item 5 facts (:func:`add_suppression_audit`,
    :func:`add_evidence_depth`), then render.

    ``reporter.py`` itself is on the architecture debt ledger's no-growth
    list (see this module's own docstring) -- its three JSON builders
    (``to_json``, ``_to_json_leaf``, ``_to_json_root_cause``) each already
    end with ``return render_json(ReportDocument.from_mapping(d),
    indent=indent)``; swapping that line for ``return
    render_json_with_side_facts(d, result, indent=indent)`` adds the two
    facts to all three without growing the file by a net line, instead of
    each site carrying its own two-line call (which would).
    """
    add_suppression_audit(d, result)
    add_evidence_depth(d, result)
    return render_json(ReportDocument.from_mapping(d), indent=indent)


def add_annotations(
    d: dict[str, Any],
    result: DiffResult,
    *,
    severity_config: SeverityConfig | None = None,
) -> None:
    """CLI cleanup phase two, PR E: persist ``annotations`` (schema 2.43).

    The persistence prerequisite the plan's own PR 1b section names before
    ``--annotate``/``--annotate-additions`` can move to the Action: a
    rendering front end must read an already-classified, already-formatted
    answer here rather than re-deriving one from stderr or a re-run
    comparison. See :func:`~abicheck.annotations.annotation_report_entries`
    for what each entry contains and why the superset (``annotate_
    additions=True``) is always computed regardless of what this *process*
    was asked to render.

    Unconditional, mirroring ``exit``'s own presence rule (PR G1): every
    real comparison has *some* answer to "what would --annotate show", even
    when that answer is ``[]``, so there's no opt-in flag gating this key
    the way `contract_context` stays opt-in on `--contract`.
    """
    from .annotations import annotation_report_entries

    d["annotations"] = annotation_report_entries(
        result, severity_config=severity_config
    )
