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

"""The review digest's compute half (``--format review`` / ``to_review_digest``).

:func:`compute_review_digest` reads every decision the digest states from the
completed :class:`~abicheck.report.envelope.ReportEnvelope` -- the verdict,
merge-effect phrase and gate exit code through the shared outcome projection
(:mod:`abicheck.report.outcome_summary`) -- and returns
``render_review.ReviewDigest`` for ``review_digest_document`` to render.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from ..checker import (
    DiffResult,
    Verdict,
)
from ..model.contract_finding_relevance import is_evaluated
from . import (
    render_review as _review,
    review_compute as _review_compute,
)
from .disposition_audit import disposition_audit_reusing_document
from .outcome_summary import project_outcome
from .policy_facts import (
    release_recommendation_for,
    summary_for,
)
from .use_case_impact_render import use_cases_by_finding_for
from .view import finding_id

if TYPE_CHECKING:
    from .envelope import ReportEnvelope


def compute_review_digest(
    result: DiffResult,
    *,
    envelope: ReportEnvelope,
    review_groups: Sequence[dict[str, object]] | None = None,
    result_counts: dict[str, int] | None = None,
    gate_exit_code: int | None = None,
) -> _review.ReviewDigest:
    """The structured intermediate for :func:`to_review_digest`.

    *envelope* is the completed evaluation this digest projects (ADR-061 gap
    C). It supplies every decision the digest states: the merge-effect
    phrase reads its :class:`~abicheck.policy.severity.GateDecision` (or the
    raw verdict when no severity gate is configured), the disposition counts
    come from its shared document, and the impacted-symbols list reads its
    already-resolved per-finding verdicts. Nothing is re-resolved here.

    *review_groups*, *result_counts* and *gate_exit_code* are the
    shared-document values the caller already read off the same envelope;
    *gate_exit_code* is used only when no severity gate is configured.
    """
    # The envelope's own snapshot of the result: every finding it holds was
    # resolved against these change objects (build_report_envelope snapshots
    # its input), so the digest reads them rather than the caller's ``result``.
    result = envelope.result
    findings = envelope.findings
    summary = summary_for(result, findings=findings)
    outcome = project_outcome(result, envelope=envelope)

    # Manual-review banner: scoping requested but the public surface could not
    # be confirmed, so compatibility is unconfirmed (don't overclaim).
    manual_review_banner = bool(
        result.scope_to_public_surface and not result.scope_resolved
    )

    # Coverage-warning banner (Codex review): a clean verdict can still rest
    # on incomplete evidence -- e.g. compare.note_if_same_binary_compared's
    # byte-identical-inputs warning -- and this digest is exactly the
    # GitHub-facing summary a reviewer approves a merge from, so it must not
    # read as unconditionally clean when one of these is present.
    # ADR-062 D14: an analysis blocked as not comparable leaves the verdict
    # reading NO_CHANGE/COMPATIBLE; its assurance note leads the warnings so
    # the one-line summary never reads as a clean comparison.
    coverage_warnings = _review_compute.not_comparable_warnings(
        result
    ) + _review_compute.compact_coverage_warnings(result.coverage_warnings or ())

    scoped = result.scope_to_public_surface
    additions_label = "Public additions" if scoped else "Additions"

    rec = release_recommendation_for(result)

    # Top impacted symbols (breaking + API), capped for readability. Filters
    # by each change's *effective* verdict (policy.evaluate.effective_verdict)
    # rather than raw kind-set membership, so a per-finding override (A4
    # pattern-verdict modulation, frozen-namespace guard) is reflected here
    # the same way it already is in the counts table and merge-effect phrase
    # above, or this could list a finding the digest calls compatible.
    # ADR-049 D1: and over the findings compatibility policy actually scored,
    # for the same reason -- the merge-effect phrase above is derived from the
    # verdict, which a NOT_EVALUATED finding did not reach, so listing one
    # here printed "safe to merge" directly above the symbol it says is
    # impacted (Codex review); it keeps its own disclosed section elsewhere.
    from .surface_changes import compute_surface_changes

    uc = use_cases_by_finding_for(result)  # `compare --use-cases` only
    groups = tuple(
        review_groups or _review_compute.review_groups_for(findings, envelope)
    )
    impacted = [
        f.change
        for f in findings
        if is_evaluated(f.change)
        if f.verdict in (Verdict.BREAKING, Verdict.API_BREAK)
    ]

    return _review.ReviewDigest(
        library=result.library,
        old_version=result.old_version,
        new_version=result.new_version,
        verdict_emoji=outcome.verdict_emoji,
        verdict_label=outcome.verdict_label,
        effect=outcome.merge_effect,
        manual_review_banner=manual_review_banner,
        coverage_warnings=coverage_warnings,
        additions_label=additions_label,
        breaking_count=summary.breaking,
        source_breaks_count=summary.source_breaks,
        risk_count=summary.risk_count,
        # additions_count/quality_issues_count are two rows in the same
        # table, so they must not overlap. `summary.compatible_additions`
        # (report_schema_version 4.0) already counts only genuine additions
        # -- use it directly, no second `quality_issues` subtraction (that
        # is `pr_comment.py`'s own release-field derivation, a separate,
        # differently-scoped field -- `workflows/release_members.py`).
        additions_count=summary.compatible_additions,
        quality_issues_count=summary.quality_issues,
        informational_count=summary.informational,
        scoped=bool(scoped),
        out_of_surface_count=result.out_of_surface_count,
        bump_value=rec.bump.value,
        soname_value=rec.soname.value,
        impacted=tuple(
            _review.ImpactedSymbol(
                symbol=c.symbol or "?",
                kind=c.kind.value,
                use_cases=(uc or {}).get(finding_id(c), ()),
            )
            for c in impacted
        ),
        disposition_audit=disposition_audit_reusing_document(
            result, envelope.severity_config, envelope.document
        ),
        surface_changes=compute_surface_changes(
            result, findings, severity_config=envelope.severity_config
        ),
        env_matrix_source_sha256=result.env_matrix_source_sha256,
        pattern_modulations=tuple(getattr(result, "pattern_modulations", ()) or ()),
        review_groups=groups,
        review_group_use_cases=_review_compute.use_cases_by_group(groups, uc),
        result_counts=result_counts or {},
        policy=result.policy or "strict_abi",
        gate_exit_code=(
            outcome.gate_exit_code if outcome.gate is not None else gate_exit_code
        ),
        evidence_summary=_review_compute.compact_evidence_summary(result),
        show_release_recommendation=bool(
            result.policy_file is not None
            and getattr(result.policy_file, "versioning_stated", False)
        ),
        policy_acceptance=(
            None if rec.policy_acceptance is None else rec.policy_acceptance.to_dict()
        ),
    )
