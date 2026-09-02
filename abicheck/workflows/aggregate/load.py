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
"""Load and validate one aggregate input report.

File and JSON-shape interpretation live here; gate folding belongs to fold.py.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from abicheck.change_registry_types import Verdict

from .contracts import (
    _BOOTSTRAP_VERDICT,
    _NEW_TARGET_VERDICT,
    _OPERATIONAL_ERROR_VERDICT,
    _SCAN_BUDGET_OVERFLOW_VERDICT,
    _SCAN_BUNDLE_INCOMPLETE_VERDICT,
    _SCAN_EVIDENCE_CONTRACT_ERROR_VERDICT,
    _SCAN_NOT_COMPARABLE_VERDICT,
    DEFAULT_REPORT_PREFIX,
    GateInfo,
)
from .gate import (
    _VALID_GATE_EXIT,
    COVERAGE_INCOMPLETE_EXIT,
    _contract_coverage_declared,
    _contract_coverage_exit,
    _contract_coverage_incomplete,
    _is_valid_contribution,
    _MalformedGate,
    contract_coverage_blocks,
)
from .reconcile import ReportFindings, parse_report_findings


def parse_report_verdict(data: Mapping[str, Any]) -> Verdict | None:
    """Extract the compatibility verdict from a parsed report."""
    raw = data.get("verdict")
    if not isinstance(raw, str):
        return None
    try:
        return Verdict(raw)
    except ValueError:
        return None


def target_id_from_path(path: Path, *, prefix: str = DEFAULT_REPORT_PREFIX) -> str:
    """Derive a target id from a report file's stem (convenience fallback)."""
    stem = path.stem
    if prefix and stem.startswith(prefix):
        stem = stem[len(prefix) :]
    return stem


@dataclass(frozen=True)
class _LoadedReport:
    target_id: str
    verdict: Verdict | None
    gate: GateInfo | None
    library: str | None
    head_sha: str | None
    reason: str | None
    path: Path
    #: ADR-049 Phase 7's orthogonal contract-coverage contribution, read off
    #: the report's own ``contract_coverage_exit_contribution`` (schema 2.26).
    #: ``0`` for every report that carries none -- a run without
    #: ``--contract`` selected no contract domain, so it cannot be
    #: short of evidence for one.
    contract_coverage_exit: int = 0
    #: Whether the report listed any coverage failure at all -- true even
    #: when the contribution above is ``0`` because ``contract.unresolved=
    #: warn`` accepted it. Reported, never folded into an exit code.
    contract_coverage_incomplete: bool = False
    #: Whether the report stated a usable contribution at all -- see
    #: :func:`_contract_coverage_declared`. Distinguishes a real
    #: ``contract.unresolved=warn`` acceptance from a silent or malformed
    #: one, so prose about the run cannot claim a policy it never set.
    contract_coverage_declared: bool = False
    #: ``None`` on every failure branch below (unreadable, malformed gate,
    #: operational error, not comparable), since none of those establish what
    #: the comparison did or did not find. Otherwise the report's own
    #: :func:`~abicheck.aggregate_findings.parse_report_findings` result.
    findings: ReportFindings | None = None
    #: P0.4's orthogonal analysis-assurance contribution, read off the
    #: report's own ``analysis_assurance_exit_contribution``. The exact
    #: sibling of :attr:`contract_coverage_exit` above, for the same reason:
    #: a run without ``--require-complete-analysis`` never floors its exit
    #: on this axis, so ``0`` is the honest default rather than a fallback.
    analysis_assurance_exit: int = 0
    #: Phase 0 item 6 ("every effective evaluation carries a digest"): read
    #: straight off the per-target report's own ``effective_config_digest``
    #: — never recomputed here. ``None`` for a report that carries none (a
    #: pre-digest report, or one written with ``include_exit_decision=False``
    #: — same fail-open default as the coverage/assurance fields above.
    effective_config_digest: str | None = None


def _analysis_assurance_exit(data: Mapping[str, Any]) -> int:
    """The report's own P0.4 analysis-assurance contribution (``0``/``1``).

    Sibling of :func:`_contract_coverage_exit`: read, not recomputed, since
    ``GateInfo.from_scan_report`` reads only the nested compatibility gate,
    never this orthogonal axis (Codex review). Fails open like its sibling,
    reusing :func:`contract_coverage_blocks`' document-shape traversal.
    """
    for block in contract_coverage_blocks(data):
        raw = block.get("analysis_assurance_exit_contribution")
        if _is_valid_contribution(raw):
            return raw
    return 0


#: Same shape ``effective_config_digest()``/the aggregate schema's own
#: pattern produce -- validated, not trusted, since this reads an on-disk
#: report this module doesn't control the writer of.
_EFFECTIVE_CONFIG_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _effective_config_digest(data: Mapping[str, Any]) -> str | None:
    """The report's own ``effective_config_digest`` (Phase 0 item 6 of
    docs/contribute/plans/duplication-and-convergence-assessment.md).

    Reuses :func:`contract_coverage_blocks`'s shape-aware traversal (a
    ``compare``/release report carries this at the document root; a
    *scan* report nests it under ``diff``/``report.diff`` -- the same
    distinction :func:`_analysis_assurance_exit` already accounts for)
    rather than a second, root-only lookup. A value not matching
    :data:`_EFFECTIVE_CONFIG_DIGEST_RE` reads as absent, not passed through.
    """
    for block in contract_coverage_blocks(data):
        raw = block.get("effective_config_digest")
        if isinstance(raw, str) and _EFFECTIVE_CONFIG_DIGEST_RE.match(raw):
            return raw
    return None


def _run_outcome_compatibility_verdict(data: Mapping[str, Any]) -> Verdict | None:
    """The report's own top-level ``run_outcome.compatibility``, parsed as a
    real :class:`Verdict`, or ``None``.

    Distinct from :func:`parse_report_verdict`, which reads the sibling
    top-level ``verdict`` key: for a report whose own root ``verdict`` is
    itself a non-``Verdict`` sentinel string (a `scan --artifact-set`'s
    ``BUNDLE_INCOMPLETE``, a `compare-release` summary's lowercase
    ``"not_comparable"``), ``run_outcome.compatibility`` may still carry a
    real, already-established completed-comparison result the sentinel
    string itself discards (Codex review, fresh evidence -- see the two call
    sites below).
    """
    run_outcome = data.get("run_outcome")
    if not isinstance(run_outcome, Mapping):
        return None
    raw = run_outcome.get("compatibility")
    if not isinstance(raw, str):
        return None
    try:
        return Verdict(raw)
    except ValueError:
        return None


#: The four synthetic ``verdict`` strings a native `scan` abort report (single
#: binary or set-level) can carry at its own root, mapped to the aggregate
#: gate's blocking-category label -- shared between the root-level check
#: below and :func:`_member_abort_categories`, which needs the same mapping
#: for a `scan --artifact-set` *member* whose own abort verdict was folded
#: away by `_aggregate_scan_set_verdict`'s stronger-verdict-wins rule. The
#: category strings match `OperationalStatus`'s own values (`policy/
#: outcome.py`) on purpose -- this dict predates `RunOutcome` and reads the
#: legacy sentinel string directly rather than importing `policy`, but the
#: label it produces is exactly what `run_outcome.operational` would say for
#: the same report, so a reader can't tell which path computed it (Codex
#: review, fresh evidence: `NOT_COMPARABLE`/`BUNDLE_INCOMPLETE` were missing
#: here, so `_load_report_file`'s `if verdict is not None:` guard -- neither
#: is a `Verdict` member -- skipped `GateInfo.from_report_data`/
#: `from_scan_report` entirely and silently discarded a blocking
#: `run_outcome.operational` for both).
_scan_abort_categories = {
    _SCAN_BUDGET_OVERFLOW_VERDICT: "budget_overflow",
    _SCAN_EVIDENCE_CONTRACT_ERROR_VERDICT: "evidence_contract_error",
    _SCAN_NOT_COMPARABLE_VERDICT: "not_comparable",
    _SCAN_BUNDLE_INCOMPLETE_VERDICT: "extraction_error",
}


def _member_abort_categories(data: Mapping[str, Any]) -> frozenset[str]:
    """Abort categories from every ``per_artifact`` member, independent of
    which single category the set-level ``verdict`` string names.

    ``_aggregate_scan_set_verdict`` (ADR-056 D3) collapses a whole set's
    outcome into exactly one root ``verdict`` string, picking one of: any
    member's ``BUDGET_OVERFLOW`` (dominates unconditionally), else the
    worst real compatibility verdict, else a member's own
    ``EVIDENCE_CONTRACT_ERROR``. Two members can abort for *different*
    reasons at once (one budget-starved, another evidence-incomplete), or
    one member can abort while another's real break wins the root string --
    either way the root alone cannot name every category, so both callers
    below (the root-abort branch, whose own ``scan_abort_category`` is only
    ever the *one* string that won, and the normal-verdict branch, where a
    real break at the root hides an aborted member entirely) union this
    function's result into their gate rather than trusting the root alone
    (Codex review, fresh evidence for both). Reads each member's own bare
    ``verdict`` field directly -- ``ScanArtifactResult.to_dict()`` flattens
    it to the member dict's own top level, not nested under ``report`` --
    rather than :func:`_scan_abort_exit_blocks`'s ``exit`` blocks, which a
    member that aborted before producing a decision may not carry at all.
    """
    per_artifact = data.get("per_artifact")
    if not isinstance(per_artifact, list):
        return frozenset()
    return frozenset(
        _scan_abort_categories[member["verdict"]]
        for member in per_artifact
        if isinstance(member, dict) and member.get("verdict") in _scan_abort_categories
    )


def _incomplete_findings(data: dict[str, Any]) -> ReportFindings:
    """Whatever findings *data* lists, explicitly not accounted as exhaustive.

    For a report whose run did not finish cleanly. `parse_report_findings`
    already refuses completeness to every release-shaped document, so this
    is belt-and-braces rather than the only guard — but it states the
    intent at the call site, where the reason (the run errored) actually
    lives, instead of relying on a property of the shape it happens to have.
    """
    return replace(parse_report_findings(data), complete=False)


def _scan_abort_exit_blocks(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every real severity signal a scan abort's report may preserve,
    normalized to ``ExitDecision.to_dict()``-shaped ``exit`` blocks (or a
    minimal stand-in carrying just ``compatibility_contribution``).

    Three shapes of *already-computed* decision this codebase's own report
    producers can leave behind, all recognized here:

    * ``diff.exit`` -- the single-binary native CLI's ``scan``/
      ``ScanOutcome`` abort envelope.
    * ``report.exit`` -- the typed API's own ``ScanResult.to_dict()`` root
      shape (a caller that dumps that dict directly, rather than going
      through the CLI, never gets ``diff`` at all) (Codex review, fresh
      evidence).
    * ``per_artifact[i].report.exit`` -- a ``scan --artifact-set``/
      ``ScanSetResult`` abort report's per-member decision
      (``ScanArtifactResult.to_dict()`` wrapping a *member's own*
      typed-API envelope).

    A fourth case has no ``exit`` block to read at all: when a set-level
    abort fires *after* every member already finished normally (no member
    itself aborted, e.g. the shared budget expires during the bundle
    audit that runs after all members), each completed member's real
    compatibility result lives only in its own top-level ``exit_code``
    (``0``/``1``/``2``/``4``, the exact scheme :data:`_VALID_GATE_EXIT`
    validates) -- there is no nested decision to find, only that bare
    scalar (Codex review, fresh evidence). Reading only the three real
    blocks above silently dropped this case's real member results,
    downgrading e.g. a completed member's exit `2` to the generic abort
    floor. Synthesized here as a minimal block (just
    ``compatibility_contribution``) so it folds through the exact same
    ``max()`` machinery as a real one, rather than a separate code path.

    Shared lookup for :func:`_scan_abort_prior_exit` and
    :func:`_scan_abort_exit_axis` below, both of which fold ``max()``
    across every block this returns.
    """
    diff = data.get("diff")
    diff_exit = diff.get("exit") if isinstance(diff, dict) else None
    report = data.get("report")
    root_exit = report.get("exit") if isinstance(report, dict) else None
    blocks: list[Mapping[str, Any]] = [
        b for b in (diff_exit, root_exit) if isinstance(b, dict)
    ]
    per_artifact = data.get("per_artifact")
    if isinstance(per_artifact, list):
        for member in per_artifact:
            if not isinstance(member, dict):
                continue
            member_report = member.get("report")
            member_exit = (
                member_report.get("exit") if isinstance(member_report, dict) else None
            )
            if isinstance(member_exit, dict):
                blocks.append(member_exit)
                continue
            # No nested decision (the member completed normally, without
            # aborting) -- fall back to its own bare exit_code.
            code = member.get("exit_code")
            if (
                isinstance(code, int)
                and not isinstance(code, bool)
                and code in _VALID_GATE_EXIT
            ):
                blocks.append({"compatibility_contribution": code})
    return blocks


def _scan_abort_prior_exit(data: Mapping[str, Any]) -> int:
    """The largest PR-G1 contribution preserved across a scan abort's own
    ``exit`` block(s) (see :func:`_scan_abort_exit_blocks`), or ``0``.

    A *late* ``_BudgetOverflow``/``_EvidenceContractError``
    (``attach_prior_on_budget_overflow``) carries the ordinary
    compatibility/contract-coverage/analysis-assurance/crosscheck-promotion
    contributions through into an ``exit`` block's own ``*_contribution``
    fields even though none of them decided ``code`` there (``code`` is
    always the dominant budget/evidence-contract-error code, chosen large
    enough to exceed them -- see ``ExitDecision``'s own docstring). Reading
    them here is what lets this target's forced gate still reflect a real
    ABI/API break already found before the abort fired, instead of
    downgrading it to a bare coverage-incomplete ``1`` (Codex review, fresh
    evidence).
    """
    best = 0
    for exit_block in _scan_abort_exit_blocks(data):
        for key in (
            "compatibility_contribution",
            "contract_coverage_contribution",
            "analysis_assurance_contribution",
            "crosscheck_promotion_contribution",
        ):
            raw = exit_block.get(key)
            if (
                isinstance(raw, int)
                and not isinstance(raw, bool)
                and raw in _VALID_GATE_EXIT
            ):
                best = max(best, raw)
    return best


def _scan_abort_exit_axis(data: Mapping[str, Any], key: str) -> tuple[int, bool]:
    """A single preserved ``0``/``1`` PR-G1 axis from a scan abort's own
    ``diff.exit`` (*key* is ``"contract_coverage_contribution"`` or
    ``"analysis_assurance_contribution"``) as ``(value, declared)``.

    Sibling of :func:`_scan_abort_prior_exit`, which folds every preserved
    axis into one gate-exit ceiling -- these two are reported separately
    instead (as :attr:`_LoadedReport.contract_coverage_exit`/
    :attr:`_LoadedReport.analysis_assurance_exit`, ADR-049 Phase 7/P0.4's
    own orthogonal axes), so folding them into the gate alone left
    :func:`_contract_coverage_exit`/:func:`_analysis_assurance_exit` reading
    ``0`` with an empty target list for a late abort that preserved a real
    ``1`` here -- those two only read the older, differently-named
    ``contract_coverage_exit_contribution``/``analysis_assurance_exit_
    contribution`` fields a scan-abort payload never carries at all (Codex
    review, fresh evidence).
    """
    best = 0
    declared = False
    for exit_block in _scan_abort_exit_blocks(data):
        raw = exit_block.get(key)
        if _is_valid_contribution(raw):
            best = max(best, raw)
            declared = True
    return best, declared


def _load_report_file(path: Path, *, prefix: str) -> _LoadedReport:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return _LoadedReport(
            target_id_from_path(path, prefix=prefix),
            None,
            None,
            None,
            None,
            f"unreadable report ({type(exc).__name__})",
            path,
        )
    if not isinstance(data, dict):
        return _LoadedReport(
            target_id_from_path(path, prefix=prefix),
            None,
            None,
            None,
            None,
            "report is not a JSON object",
            path,
        )
    # Prefer the report's own self-identified target id; fall back to filename.
    own_id = data.get("target_id")
    target_id = (
        str(own_id)
        if isinstance(own_id, str) and own_id
        else target_id_from_path(path, prefix=prefix)
    )
    head_sha_raw = data.get("head_sha")
    head_sha = str(head_sha_raw) if isinstance(head_sha_raw, str) else None
    effective_config_digest = _effective_config_digest(data)
    # A compare-release *operational* failure carries top-level ``verdict:
    # "ERROR"`` (a library failed to dump/extract/compare) — the release path
    # ranks it above BREAKING and floors its exit to 4. "ERROR" is not a
    # ``Verdict`` enum member, so preserve it here as a blocking gate: otherwise
    # it falls through as a verdictless (unavailable) report that a warn /
    # optional / unexpected policy could let pass, silently downgrading a hard
    # operational failure to a coverage gap.
    if data.get("verdict") == _OPERATIONAL_ERROR_VERDICT:
        return _LoadedReport(
            target_id=target_id,
            verdict=Verdict.BREAKING,
            gate=GateInfo(
                exit_code=4,
                blocking=True,
                blocking_categories=("operational_error",),
                from_report=True,
            ),
            library=data.get("library"),
            head_sha=head_sha,
            reason=None,
            path=path,
            contract_coverage_exit=_contract_coverage_exit(data),
            contract_coverage_incomplete=_contract_coverage_incomplete(data),
            contract_coverage_declared=_contract_coverage_declared(data),
            analysis_assurance_exit=_analysis_assurance_exit(data),
            # An operational ERROR means *a* library failed, not that nothing
            # was compared: `_format_release_json` emits `bundle_findings`/
            # `matrix_findings` from whatever did complete, regardless of the
            # top-level verdict (Codex review). Dropping them would lose real
            # evidence from the one profile most likely to differ. Never
            # complete, though — a run that errored cannot account for
            # everything, so these findings can convict their own profile and
            # clear no other.
            findings=_incomplete_findings(data),
            effective_config_digest=effective_config_digest,
        )
    # `scan`'s own two abort verdicts (ADR-064 stage 1b's native-CLI abort
    # report) carry no comparison at all -- a budget overflow or a pinned
    # depth's evidence-contract violation -- but must still gate, not fall
    # through as an unavailable/verdictless report a required-target policy
    # could silently tolerate (Codex review, fresh evidence: neither string
    # is a `Verdict` member, so this function never even reached
    # `GateInfo.from_scan_report` for these before this branch existed).
    #
    # Unlike the operational-error branch above, `verdict` stays `None` here
    # rather than a synthetic `Verdict.BREAKING`: a scan that aborted before
    # comparing never produced an ABI-break finding, so forcing one invents
    # both a compatibility verdict and an "analyzed" target count for a
    # comparison that never ran (Codex review, fresh evidence --
    # `AggregateResult.to_dict()` reported `compatibility.verdict:
    # "BREAKING"` and complete `analyzed_targets`/required-coverage for this
    # exact case). The gate is still attached and still counts toward
    # `AggregateResult.exit_code()`/`blocking_targets` regardless of the
    # target's own required/optional declaration --
    # `AggregateResult._forced_gate_targets` folds in exactly this shape
    # (unavailable, but carrying a non-`None` gate) alongside the analyzed
    # targets, the same way the now-removed synthetic verdict used to. The
    # gate's own `exit_code` is `max(COVERAGE_INCOMPLETE_EXIT, prior
    # contribution)`, never scan's raw private code (5 for budget overflow)
    # -- `GateInfo.from_scan_report` already normalizes every scan exit
    # outside {0, 2, 4} to `COVERAGE_INCOMPLETE_EXIT`, and the aggregate's
    # own published contract has no exit 5 -- but a *late* `_BudgetOverflow`
    # (`attach_prior_on_budget_overflow`) preserves whatever gate/coverage/
    # assurance/crosscheck decision already existed in `diff.exit`'s own
    # `*_contribution` fields, and downgrading a real ABI/API break already
    # found before the abort to a bare coverage-incomplete `1` would hide it
    # from a severity-aware consumer (Codex review, fresh evidence).
    raw_scan_verdict = data.get("verdict")
    scan_abort_category = (
        _scan_abort_categories.get(raw_scan_verdict)
        if isinstance(raw_scan_verdict, str)
        else None
    )
    if scan_abort_category is not None:
        contract_axis, contract_declared = _scan_abort_exit_axis(
            data, "contract_coverage_contribution"
        )
        assurance_axis, _ = _scan_abort_exit_axis(
            data, "analysis_assurance_contribution"
        )
        blocking_categories = frozenset(
            {scan_abort_category}
        ) | _member_abort_categories(data)
        # `BUNDLE_INCOMPLETE` is the one sentinel of the four where a real
        # comparison DID complete (Codex review, fresh evidence): unlike a
        # true abort (`BUDGET_OVERFLOW`/`EVIDENCE_CONTRACT_ERROR`/
        # `NOT_COMPARABLE`, where nothing was ever compared), it fires only
        # after every member scanned cleanly and just the cross-library
        # bundle audit itself never ran -- `run_outcome_dict_for_scan`
        # already preserves the worst completed member's real compatibility
        # verdict at `run_outcome.compatibility` (`service_scan.run_scan_
        # set`'s own `member_verdicts=` wiring). Forcing `verdict=None` here
        # the same way the other three sentinels do would discard that real,
        # already-established result and wrongly report the target as
        # unavailable/unanalyzed even though the operational `extraction_
        # error` axis alone already accounts for the skipped audit.
        compat_verdict = (
            _run_outcome_compatibility_verdict(data)
            if raw_scan_verdict == _SCAN_BUNDLE_INCOMPLETE_VERDICT
            else None
        )
        return _LoadedReport(
            target_id=target_id,
            verdict=compat_verdict,
            gate=GateInfo(
                exit_code=max(COVERAGE_INCOMPLETE_EXIT, _scan_abort_prior_exit(data)),
                blocking=True,
                blocking_categories=tuple(sorted(blocking_categories)),
                from_report=True,
            ),
            library=data.get("library"),
            head_sha=head_sha,
            reason=(
                None
                if compat_verdict is not None
                else f"scan aborted before completing a comparison ({scan_abort_category})"
            ),
            path=path,
            contract_coverage_exit=max(_contract_coverage_exit(data), contract_axis),
            contract_coverage_incomplete=(
                _contract_coverage_incomplete(data) or contract_axis == 1
            ),
            contract_coverage_declared=(
                _contract_coverage_declared(data) or contract_declared
            ),
            analysis_assurance_exit=max(_analysis_assurance_exit(data), assurance_axis),
            # No comparison ran at all for a true abort -- there is no
            # partial finding set to preserve, and this target is not
            # "analyzed" for the finding matrix either. `BUNDLE_INCOMPLETE`
            # (compat_verdict resolved above) is the one exception: its
            # members did complete, so their findings are real, just not
            # exhaustive (the bundle audit itself never ran).
            findings=(
                _incomplete_findings(data) if compat_verdict is not None else None
            ),
            effective_config_digest=(
                effective_config_digest if compat_verdict is not None else None
            ),
        )
    # ADR-050 D2 (Codex review, fresh evidence): a `compare-release` summary's
    # own lowercase `"not_comparable"` sentinel is a REAL string (not JSON
    # `null`) -- distinct from `scan`'s uppercase `NOT_COMPARABLE` handled
    # above, and from a native `compare`'s `verdict: null` + `reason.kind`
    # shape handled below -- so it is neither a `Verdict` member nor caught
    # by either of those two branches. It previously fell through to the
    # generic "report carried no ABI verdict" unavailable reading further
    # below, silently discarding `run_outcome.operational: "not_comparable"`
    # and letting a warn/optional/tolerated-unexpected target policy pass a
    # refused release comparison. `_format_release_json`/`resolve_release_
    # exit_decision_for_report` already compute a correct top-level
    # `run_outcome` for this sentinel, and this shape carries no root
    # `severity` key unless a severity scheme was active -- either way
    # `GateInfo.from_report_data` already reads both correctly, so it is
    # reused here rather than hand-rolling a second run_outcome reader.
    if data.get("verdict") == "not_comparable":
        try:
            release_gate = GateInfo.from_report_data(data)
        except _MalformedGate as exc:
            return _LoadedReport(
                target_id=target_id,
                verdict=None,
                gate=None,
                library=data.get("library"),
                head_sha=head_sha,
                reason=f"report gate decision is malformed: {exc}",
                path=path,
            )
        compat_verdict = _run_outcome_compatibility_verdict(data)
        return _LoadedReport(
            target_id=target_id,
            verdict=compat_verdict,
            gate=release_gate,
            library=data.get("library"),
            head_sha=head_sha,
            reason=(
                None
                if compat_verdict is not None
                else "not comparable (release refused comparison)"
            ),
            path=path,
            contract_coverage_exit=_contract_coverage_exit(data),
            contract_coverage_incomplete=_contract_coverage_incomplete(data),
            contract_coverage_declared=_contract_coverage_declared(data),
            analysis_assurance_exit=_analysis_assurance_exit(data),
            effective_config_digest=(
                effective_config_digest if compat_verdict is not None else None
            ),
        )
    # ADR-050 D2: a native compare/compare-release not_comparable report
    # carries a real ``verdict: null`` (JSON null, not a missing key) plus a
    # structured ``reason: {kind, message}`` (schema 2.17) -- distinct from
    # an ordinary verdictless/malformed report (which falls through to the
    # generic "carried no ABI verdict" reason below), and must never be
    # silently folded into the same "unavailable" bucket a report that
    # simply never arrived gets: unavailable is a *coverage* gap, gated only
    # for required targets and skipped entirely under
    # --on-missing-required warn / discovered-only mode, while a genuine
    # not_comparable result is exactly the "we don't actually know if this
    # is safe" case this whole ADR exists to never treat as silently OK.
    # Mapped to the same forced-blocking shape the operational-error branch
    # above already uses -- a synthetic BREAKING verdict with a gate that
    # bypasses the legacy/report gate lookup entirely -- so it is folded
    # into exit_code() unconditionally, coverage settings notwithstanding.
    if "verdict" in data and data.get("verdict") is None:
        reason_obj = data.get("reason")
        if isinstance(reason_obj, dict) and isinstance(reason_obj.get("kind"), str):
            kind = reason_obj["kind"]
            message = reason_obj.get("message")
            detail = f": {message}" if isinstance(message, str) and message else ""
            return _LoadedReport(
                target_id=target_id,
                verdict=Verdict.BREAKING,
                gate=GateInfo(
                    exit_code=4,
                    blocking=True,
                    blocking_categories=("not_comparable",),
                    from_report=True,
                ),
                library=data.get("library"),
                head_sha=head_sha,
                reason=f"not comparable ({kind}){detail}",
                path=path,
                contract_coverage_exit=_contract_coverage_exit(data),
                contract_coverage_incomplete=_contract_coverage_incomplete(data),
                contract_coverage_declared=_contract_coverage_declared(data),
                analysis_assurance_exit=_analysis_assurance_exit(data),
                effective_config_digest=effective_config_digest,
            )
    verdict = parse_report_verdict(data)
    gate: GateInfo | None = None
    if verdict is not None:
        try:
            # A scan-shaped document (``scan_schema_version`` present) is
            # dispatched to `GateInfo.from_scan_report` FIRST, not
            # `from_report_data` -- a native `scan` report carries its own
            # top-level `run_outcome` (ADR-063 Phase 7) but no top-level
            # `severity` block (a severity-scheme `scan --against` nests its
            # gate at `diff.severity` instead), so `from_report_data`'s own
            # "no severity -> read run_outcome alone" branch previously
            # returned a `GateInfo` straight from the root `run_outcome`
            # without ever reaching `from_scan_report`, which is the only
            # reader that validates/cross-checks the nested `diff.severity`
            # gate against it (Codex review, fresh evidence: a nested
            # severity exit 4 paired with a root `run_outcome.gate: "none"`
            # was accepted as nonblocking instead of failing closed).
            # `from_scan_report` itself already folds the root `run_outcome`
            # into whichever nested/legacy gate it finds
            # (`_fold_top_level_run_outcome`), so this reordering loses no
            # coverage for either shape -- only a non-scan report still
            # prefers its own `severity` block first.
            if "scan_schema_version" in data:
                gate = GateInfo.from_scan_report(data)
                if gate is None:
                    gate = GateInfo.from_report_data(data)
            else:
                # A ``compare`` severity block wins; else a legacy-scheme
                # ``scan`` top-level gate; else the legacy verdict mapping.
                # Only an *absent* gate block legacy-falls-back — a
                # *malformed* one fails closed below.
                gate = GateInfo.from_report_data(data)
                if gate is None:
                    gate = GateInfo.from_scan_report(data)
        except _MalformedGate as exc:
            # Fail closed: a present-but-corrupt gate makes the target
            # unavailable (unknown), never silently the greener legacy path.
            return _LoadedReport(
                target_id=target_id,
                verdict=None,
                gate=None,
                library=data.get("library"),
                head_sha=head_sha,
                reason=f"report gate decision is malformed: {exc}",
                path=path,
            )
        if gate is None:
            gate = GateInfo.legacy_from_verdict(verdict)
        extra_categories = _member_abort_categories(data) - set(
            gate.blocking_categories
        )
        if extra_categories:
            gate = replace(
                gate,
                blocking_categories=tuple(
                    sorted(set(gate.blocking_categories) | extra_categories)
                ),
            )
    raw_verdict = data.get("verdict")
    if verdict is not None:
        unavailable_reason = None
    elif raw_verdict == _BOOTSTRAP_VERDICT:
        unavailable_reason = "no baseline published yet (bootstrap)"
    elif raw_verdict == _NEW_TARGET_VERDICT:
        unavailable_reason = "target not yet in this baseline-set (new_target)"
    else:
        unavailable_reason = "report carried no ABI verdict"
    return _LoadedReport(
        target_id=target_id,
        verdict=verdict,
        gate=gate,
        library=data.get("library"),
        head_sha=head_sha,
        reason=unavailable_reason,
        path=path,
        contract_coverage_exit=_contract_coverage_exit(data),
        contract_coverage_incomplete=_contract_coverage_incomplete(data),
        contract_coverage_declared=_contract_coverage_declared(data),
        analysis_assurance_exit=_analysis_assurance_exit(data),
        # Only a report that produced a real verdict has a finding set worth
        # reading: a verdictless one is unavailable, and its `changes` array
        # (if any) describes a comparison that never reached a conclusion.
        findings=parse_report_findings(data) if verdict is not None else None,
        # Same reasoning -- `analyzed` is `compatibility_verdict is not
        # None` -- so a BOOTSTRAP/NEW_TARGET/malformed-verdict report must
        # not carry a digest through either, even if one was stamped on it.
        effective_config_digest=(
            effective_config_digest if verdict is not None else None
        ),
    )
