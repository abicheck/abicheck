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
from abicheck.policy.outcome import (
    OperationalStatus,
    PolicyGateDecision,
    fold_gate_and_operational,
)

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
    _has_valid_run_outcome_block,
    _is_valid_contribution,
    _MalformedGate,
    _run_outcome_blocking_categories,
    _run_outcome_compatibility_verdict,
    _run_outcome_gate_and_operational,
    _run_outcome_gate_exit_and_category,
    contract_coverage_blocks,
)
from .reconcile import ReportFindings, parse_report_findings
from .scope_axis import (
    declares_null_compatibility,
    scope_completeness_exit,
    scope_completeness_incomplete,
)


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
    #: the report's own ``contract_coverage_exit_contribution`` (schema 2.26);
    #: ``0`` for a report that carries none (no ``--contract`` domain).
    contract_coverage_exit: int = 0
    #: Whether the report listed any coverage failure at all -- true even
    #: when ``contract.unresolved=warn`` zeroed the contribution above.
    contract_coverage_incomplete: bool = False
    #: Whether the report stated a usable contribution at all -- see
    #: :func:`_contract_coverage_declared`.
    contract_coverage_declared: bool = False
    #: ``None`` on every failure branch below (none establishes what the
    #: comparison found); otherwise ``parse_report_findings``'s result.
    findings: ReportFindings | None = None
    #: P0.4's orthogonal analysis-assurance contribution, read off the
    #: report's own ``analysis_assurance_exit_contribution``; ``0`` for a run
    #: without ``--require-complete-analysis``.
    analysis_assurance_exit: int = 0
    #: ADR-065's scope-completeness contribution (``scope_axis``); ``0`` for
    #: every scalar comparison and every complete release.
    scope_completeness_exit: int = 0
    #: Whether the report recorded an incomplete scope, gating or accepted.
    scope_completeness_incomplete: bool = False
    #: Phase 0 item 6: the report's own ``effective_config_digest``, never
    #: recomputed; ``None`` when it carries none (fail-open like the above).
    effective_config_digest: str | None = None


def _malformed_gate_report(
    target_id: str, library: str | None, head_sha: str | None, path: Path, reason: str
) -> _LoadedReport:
    """The shared "gate decision is malformed" unavailable shape every
    fail-closed branch here returns, apart from *reason*."""
    return _LoadedReport(
        target_id=target_id,
        verdict=None,
        gate=None,
        library=library,
        head_sha=head_sha,
        reason=reason,
        path=path,
    )


def _not_comparable_contradiction_reason(
    run_outcome_pair: tuple[PolicyGateDecision, OperationalStatus] | None,
) -> str | None:
    """``None`` unless *run_outcome_pair* is a schema-valid ``run_outcome``
    whose ``operational`` contradicts a not-comparable refusal (e.g. `gate:
    none`/`operational: none`) -- trusting it would let a real refusal read
    as safe. Shared by the release lowercase ``"not_comparable"`` and
    native null-verdict/``reason.kind`` branches.
    """
    if (
        run_outcome_pair is None
        or run_outcome_pair[1] is OperationalStatus.NOT_COMPARABLE
    ):
        return None
    return (
        "report gate decision is malformed: run_outcome.operational "
        f"({run_outcome_pair[1].value!r}) contradicts the report's own "
        "not-comparable refusal"
    )


def _analysis_assurance_exit(data: Mapping[str, Any]) -> int:
    """The report's own P0.4 analysis-assurance contribution (``0``/``1``):
    read, not recomputed (``GateInfo.from_scan_report`` reads only the
    nested compatibility gate -- Codex review); fails open like its sibling
    :func:`_contract_coverage_exit`, over the same block traversal."""
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

    Reuses :func:`contract_coverage_blocks`'s shape-aware traversal (root
    for ``compare``/release, ``diff``/``report.diff`` for a scan). A value
    not matching :data:`_EFFECTIVE_CONFIG_DIGEST_RE` reads as absent.
    """
    for block in contract_coverage_blocks(data):
        raw = block.get("effective_config_digest")
        if isinstance(raw, str) and _EFFECTIVE_CONFIG_DIGEST_RE.match(raw):
            return raw
    return None


#: The four synthetic ``verdict`` strings a native `scan` abort report (single
#: binary or set-level) can carry at its own root, mapped to the aggregate
#: gate's blocking-category label -- shared between the root-level check
#: below and :func:`_member_abort_categories`, which needs the same mapping
#: for a `scan --artifact-set` *member* whose own abort verdict was folded
#: away by `_aggregate_scan_set_verdict`'s stronger-verdict-wins rule. The
#: category strings match `OperationalStatus`'s own values on purpose --
#: this dict predates `RunOutcome` and reads the legacy sentinel string
#: directly rather than importing `policy`, but the label it produces is
#: exactly what `run_outcome.operational` would say for the same report.
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
    outcome into exactly one root ``verdict`` string. Two members can abort
    for *different* reasons at once, or one member can abort while another's
    real break wins the root string -- either way the root alone cannot name
    every category, so both callers below union this function's result into
    their gate rather than trusting the root alone. Reads each member's own
    bare ``verdict`` field directly -- ``ScanArtifactResult.to_dict()``
    flattens it to the member dict's own top level, not nested under
    ``report`` -- rather than :func:`_scan_abort_exit_blocks`'s ``exit``
    blocks, which a member that aborted before producing a decision may not
    carry at all.
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
    producers can leave behind, all recognized here: ``diff.exit`` (the
    single-binary native CLI's abort envelope), ``report.exit`` (the typed
    API's own ``ScanResult.to_dict()`` root shape), and
    ``per_artifact[i].report.exit`` (a ``scan --artifact-set`` abort
    report's per-member decision).

    A fourth case has no ``exit`` block at all: when a set-level abort fires
    *after* every member already finished normally, each completed member's
    real compatibility result lives only in its own top-level ``exit_code``
    (``0``/``1``/``2``/``4``, :data:`_VALID_GATE_EXIT`'s scheme) -- reading
    only the three real blocks above silently dropped this case's real
    member results. Synthesized here as a minimal block (just
    ``compatibility_contribution``) so it folds through the same ``max()``
    machinery as a real one.

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
    fields even though none of them decided ``code`` there. Reading them
    here is what lets this target's forced gate still reflect a real
    ABI/API break already found before the abort fired, instead of
    downgrading it to a bare coverage-incomplete ``1``.
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
    :attr:`_LoadedReport.analysis_assurance_exit`), since folding them into
    the gate alone left those two readers seeing ``0`` for a late abort that
    preserved a real ``1`` here (they only read the older, differently-named
    fields a scan-abort payload never carries).
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
    # "ERROR"`` (a library failed to dump/extract/compare) — floors its exit
    # to 4. "ERROR" is not a ``Verdict`` member, so preserve it as a
    # blocking gate: otherwise it falls through as a verdictless
    # (unavailable) report a warn/optional/unexpected policy could pass.
    if data.get("verdict") == _OPERATIONAL_ERROR_VERDICT:
        # A release's `run_outcome.compatibility` may already carry another
        # library's real, completed verdict (`ERROR` names only the
        # OPERATIONALLY failed one) -- read it rather than always
        # fabricating `Verdict.BREAKING`. Falls back to that synthetic
        # verdict only when no *valid* run_outcome block is present (a
        # pre-2.48 report); a legitimately `null` compatibility must NOT
        # read as absent, and a present-but-schema-invalid block fails
        # closed instead. The gate's own `exit_code` floors at 4
        # unconditionally; a recovered `gate` folds into `blocking_
        # categories` too, so the real blocker isn't hidden behind only
        # `operational_error`.
        try:
            _, error_gate_category = _run_outcome_gate_exit_and_category(data)
        except _MalformedGate as exc:
            return _malformed_gate_report(
                target_id,
                data.get("library"),
                head_sha,
                path,
                f"report gate decision is malformed: {exc}",
            )
        error_compat_verdict = _run_outcome_compatibility_verdict(data)
        error_has_valid_run_outcome = _has_valid_run_outcome_block(data)
        error_blocking_categories = frozenset({"operational_error"}) | (
            {error_gate_category} if error_gate_category is not None else set()
        )
        return _LoadedReport(
            target_id=target_id,
            verdict=(
                error_compat_verdict
                if error_compat_verdict is not None or error_has_valid_run_outcome
                else Verdict.BREAKING
            ),
            gate=GateInfo(
                exit_code=4,
                blocking=True,
                blocking_categories=tuple(sorted(error_blocking_categories)),
                from_report=True,
            ),
            library=data.get("library"),
            head_sha=head_sha,
            reason=(
                None
                if error_compat_verdict is not None or not error_has_valid_run_outcome
                else "operational error (library extraction/comparison failed; "
                "no comparison completed)"
            ),
            path=path,
            contract_coverage_exit=_contract_coverage_exit(data),
            contract_coverage_incomplete=_contract_coverage_incomplete(data),
            contract_coverage_declared=_contract_coverage_declared(data),
            analysis_assurance_exit=_analysis_assurance_exit(data),
            scope_completeness_exit=scope_completeness_exit(data),
            scope_completeness_incomplete=scope_completeness_incomplete(data),
            # An operational ERROR means *a* library failed, not that nothing
            # was compared: real `bundle_findings`/`matrix_findings` from
            # whatever did complete are worth keeping, but never complete --
            # a run that errored cannot account for everything.
            findings=_incomplete_findings(data),
            effective_config_digest=effective_config_digest,
        )
    # `scan`'s own four abort verdicts carry no comparison at all -- a
    # budget overflow, a pinned depth's evidence-contract violation, a
    # comparability refusal, or an incomplete bundle audit -- but must
    # still gate, not fall through as an unavailable/verdictless report a
    # required-target policy could silently tolerate (none of the four is a
    # `Verdict` member).
    #
    # `verdict` stays `None` here rather than a synthetic `Verdict.
    # BREAKING`: a scan that aborted before comparing never produced an
    # ABI-break finding, so forcing one invents an "analyzed" target count
    # for a comparison that never ran. The gate is still attached and still
    # counts toward `AggregateResult.exit_code()`/`blocking_targets`
    # (`_forced_gate_targets`). The gate's own `exit_code` is
    # `max(COVERAGE_INCOMPLETE_EXIT, prior contribution, run_outcome
    # contribution)`, never scan's raw private code -- `GateInfo.from_
    # scan_report` already normalizes every scan exit outside {0, 2, 4} to
    # `COVERAGE_INCOMPLETE_EXIT`, but a *late* `_BudgetOverflow` preserves
    # whatever gate/coverage/assurance decision already existed, so a real
    # break found before the abort isn't hidden.
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
        # A valid `run_outcome.gate` can preserve a completed break the
        # legacy `diff.exit`/member blocks are absent/stale for -- fold it
        # in too. A present-but-invalid block fails closed like every other
        # structured reader here: silently discarding it could hide a real
        # ABI break behind a bare coverage-incomplete floor.
        try:
            run_outcome_gate_exit, run_outcome_gate_category = (
                _run_outcome_gate_exit_and_category(data)
            )
        except _MalformedGate as exc:
            return _malformed_gate_report(
                target_id,
                data.get("library"),
                head_sha,
                path,
                f"report gate decision is malformed: {exc}",
            )
        blocking_categories = frozenset(
            {scan_abort_category}
        ) | _member_abort_categories(data)
        if run_outcome_gate_category is not None:
            blocking_categories = blocking_categories | {run_outcome_gate_category}
        # `BUNDLE_INCOMPLETE` typically has a real completed comparison; a
        # *late* abort can too -- read unconditionally for all four sentinels.
        compat_verdict = _run_outcome_compatibility_verdict(data)
        return _LoadedReport(
            target_id=target_id,
            verdict=compat_verdict,
            gate=GateInfo(
                exit_code=max(
                    COVERAGE_INCOMPLETE_EXIT,
                    _scan_abort_prior_exit(data),
                    run_outcome_gate_exit,
                ),
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
            scope_completeness_exit=scope_completeness_exit(data),
            scope_completeness_incomplete=scope_completeness_incomplete(data),
            # No comparison ran at all for a true abort -- no partial finding
            # set to preserve. `BUNDLE_INCOMPLETE` (compat_verdict resolved
            # above) is the exception: its members did complete.
            findings=(
                _incomplete_findings(data) if compat_verdict is not None else None
            ),
            effective_config_digest=(
                effective_config_digest if compat_verdict is not None else None
            ),
        )
    # ADR-050 D2: a `compare-release` summary's own lowercase
    # `"not_comparable"` sentinel is a REAL string (not JSON `null`) --
    # distinct from `scan`'s uppercase `NOT_COMPARABLE` handled above, and
    # from a native `compare`'s `verdict: null` + `reason.kind` shape
    # handled below -- so it is neither a `Verdict` member nor caught by
    # either of those two branches; it must not fall through to the generic
    # "report carried no ABI verdict" unavailable reading further below.
    # `GateInfo.from_report_data` already reads this shape's `run_outcome`/
    # `severity` correctly, so it is reused rather than hand-rolling a
    # second reader.
    if data.get("verdict") == "not_comparable":
        try:
            release_gate = GateInfo.from_report_data(data)
            run_outcome_pair = _run_outcome_gate_and_operational(data)
        except _MalformedGate as exc:
            return _malformed_gate_report(
                target_id,
                data.get("library"),
                head_sha,
                path,
                f"report gate decision is malformed: {exc}",
            )
        contradiction = _not_comparable_contradiction_reason(run_outcome_pair)
        if contradiction is not None:
            return _malformed_gate_report(
                target_id, data.get("library"), head_sha, path, contradiction
            )
        if release_gate is None:
            # Pre-2.48 legacy report (no `severity`/`run_outcome`) that still
            # refused: `None` here must not read as gate-less/unavailable.
            release_gate = GateInfo(
                exit_code=4,
                blocking=True,
                blocking_categories=("not_comparable",),
                from_report=True,
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
            scope_completeness_exit=scope_completeness_exit(data),
            scope_completeness_incomplete=scope_completeness_incomplete(data),
            # A completed sibling/global comparison still leaves real
            # bundle_findings/matrix_findings even though this library
            # refused -- mirrors the ERROR/scan-abort branches.
            findings=_incomplete_findings(data) if compat_verdict is not None else None,
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
    # Historically mapped to the same forced-blocking shape the operational-
    # error branch above uses -- a synthetic BREAKING verdict with a gate
    # bypassing the legacy/report gate lookup entirely, kept below as the
    # fallback for a report predating `run_outcome` (schema < 2.48). Codex
    # review, fresh evidence: `report.not_comparable.not_comparable_
    # document()` now always writes a top-level `run_outcome` for this exact
    # shape (`compatibility: null`, `gate: none`, `operational: not_
    # comparable`) -- read it directly when present, the same way the
    # release's own lowercase `"not_comparable"` branch above already does,
    # rather than fabricating `Verdict.BREAKING`/exit 4 unconditionally. The
    # orthogonal fold (`fold_gate_and_operational(NONE, NOT_COMPARABLE)`)
    # floors at exit 1 -- "only the operational axis blocks" -- consistent
    # with every other operational-failure sentinel in this module rather
    # than a fabricated compatibility-axis 4.
    if "verdict" in data and data.get("verdict") is None:
        reason_obj = data.get("reason")
        if isinstance(reason_obj, dict) and isinstance(reason_obj.get("kind"), str):
            kind = reason_obj["kind"]
            message = reason_obj.get("message")
            detail = f": {message}" if isinstance(message, str) and message else ""
            # `_run_outcome_gate_and_operational` fails closed with a raised
            # `_MalformedGate` for a PRESENT but schema-invalid `run_outcome`
            # (distinct from a genuinely absent one, which returns `None`) --
            # every other branch that calls it wraps the call in exactly this
            # try/except, so a corrupt block here must land the target
            # unavailable too, not abort the whole aggregation command
            # (Codex review, fresh evidence).
            try:
                run_outcome = _run_outcome_gate_and_operational(data)
            except _MalformedGate as exc:
                return _malformed_gate_report(
                    target_id,
                    data.get("library"),
                    head_sha,
                    path,
                    f"report gate decision is malformed: {exc}",
                )
            contradiction = _not_comparable_contradiction_reason(run_outcome)
            if contradiction is not None:
                return _malformed_gate_report(
                    target_id, data.get("library"), head_sha, path, contradiction
                )
            if run_outcome is not None:
                refusal_gate_dec, refusal_operational = run_outcome
                refusal_exit_code = fold_gate_and_operational(
                    refusal_gate_dec, refusal_operational
                )
                refusal_gate = GateInfo(
                    exit_code=refusal_exit_code,
                    blocking=refusal_exit_code != 0,
                    blocking_categories=_run_outcome_blocking_categories(
                        refusal_gate_dec, refusal_operational
                    ),
                    from_report=True,
                )
                refusal_verdict = _run_outcome_compatibility_verdict(data)
            else:
                refusal_gate = GateInfo(
                    exit_code=4,
                    blocking=True,
                    blocking_categories=("not_comparable",),
                    from_report=True,
                )
                refusal_verdict = Verdict.BREAKING
            return _LoadedReport(
                target_id=target_id,
                verdict=refusal_verdict,
                gate=refusal_gate,
                library=data.get("library"),
                head_sha=head_sha,
                reason=f"not comparable ({kind}){detail}",
                path=path,
                contract_coverage_exit=_contract_coverage_exit(data),
                contract_coverage_incomplete=_contract_coverage_incomplete(data),
                contract_coverage_declared=_contract_coverage_declared(data),
                analysis_assurance_exit=_analysis_assurance_exit(data),
                scope_completeness_exit=scope_completeness_exit(data),
                scope_completeness_incomplete=scope_completeness_incomplete(data),
                effective_config_digest=effective_config_digest,
            )
    verdict = parse_report_verdict(data)
    gate: GateInfo | None = None
    if verdict is not None:
        try:
            # A scan-shaped document goes to `GateInfo.from_scan_report`
            # FIRST: it carries a root `run_outcome` but no root `severity`
            # (a severity-scheme `scan --against` nests its gate at
            # `diff.severity`), and only `from_scan_report` cross-checks that
            # nested gate against the root block (Codex review: a nested exit
            # 4 beside a root `gate: "none"` once read as nonblocking). It
            # folds the root `run_outcome` itself, so nothing is lost.
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
            return _malformed_gate_report(
                target_id,
                data.get("library"),
                head_sha,
                path,
                f"report gate decision is malformed: {exc}",
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
    if verdict is not None and declares_null_compatibility(data):
        # ADR-065 D7: the gate above still folds `operational`/the scope
        # axis; only the legacy root verdict is not a real result.
        verdict = None
        unavailable_reason = (
            "no comparison completed (run_outcome.compatibility is null)"
        )
    elif verdict is not None:
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
        scope_completeness_exit=scope_completeness_exit(data),
        scope_completeness_incomplete=scope_completeness_incomplete(data),
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
