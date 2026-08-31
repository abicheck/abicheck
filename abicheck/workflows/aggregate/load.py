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
    _SCAN_EVIDENCE_CONTRACT_ERROR_VERDICT,
    DEFAULT_REPORT_PREFIX,
    GateInfo,
)
from .gate import (
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


def _incomplete_findings(data: dict[str, Any]) -> ReportFindings:
    """Whatever findings *data* lists, explicitly not accounted as exhaustive.

    For a report whose run did not finish cleanly. `parse_report_findings`
    already refuses completeness to every release-shaped document, so this
    is belt-and-braces rather than the only guard — but it states the
    intent at the call site, where the reason (the run errored) actually
    lives, instead of relying on a property of the shape it happens to have.
    """
    return replace(parse_report_findings(data), complete=False)


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
    # depth's evidence-contract violation -- but must still gate like the
    # operational failure above, not fall through as an unavailable/
    # verdictless report a required-target policy could silently tolerate
    # (Codex review, fresh evidence: neither string is a `Verdict` member,
    # so this function never even reached `GateInfo.from_scan_report` for
    # these before this branch existed). The gate's own `exit_code` is
    # `COVERAGE_INCOMPLETE_EXIT` (1), never scan's raw private code (5 for
    # budget overflow) -- `GateInfo.from_scan_report` already normalizes
    # every scan exit outside {0, 2, 4} to this same value, and the
    # aggregate's own published contract has no exit 5 (Codex review, fresh
    # evidence: this branch bypassed that reader and leaked scan's own
    # numbering into `AggregateResult.exit_code`).
    _scan_abort_categories = {
        _SCAN_BUDGET_OVERFLOW_VERDICT: "budget_overflow",
        _SCAN_EVIDENCE_CONTRACT_ERROR_VERDICT: "evidence_contract_error",
    }
    raw_scan_verdict = data.get("verdict")
    scan_abort_category = (
        _scan_abort_categories.get(raw_scan_verdict)
        if isinstance(raw_scan_verdict, str)
        else None
    )
    if scan_abort_category is not None:
        return _LoadedReport(
            target_id=target_id,
            verdict=Verdict.BREAKING,
            gate=GateInfo(
                exit_code=COVERAGE_INCOMPLETE_EXIT,
                blocking=True,
                blocking_categories=(scan_abort_category,),
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
            # No comparison ran at all -- unlike the operational-error branch
            # above, there are no partial findings to preserve.
            findings=_incomplete_findings(data),
            effective_config_digest=effective_config_digest,
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
            # A ``compare`` severity block wins; else a ``scan`` top-level gate;
            # else the legacy verdict mapping. Only an *absent* gate block
            # legacy-falls-back — a *malformed* one fails closed below.
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
