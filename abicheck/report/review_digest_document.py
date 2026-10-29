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

"""The ``-o review=...`` digest, as a document and its projection.

Split out of ``render_markdown_document.py``, which carries a ``no_growth``
baseline: the digest is a *different report* from the full Markdown one --
its own builder, its own mapping round trip and its own renderer -- and the
file it lived in already separated it with a section rule. Moving it is what
the repository's own debt rule asks for ("move responsibility out to a
properly-owned module, never trim the file to fit") rather than raising that
baseline an eighth time on this branch.

The round trip through a mapping is the thing to be careful with here, and
the reason this module exists as a unit: the digest dataclass is rebuilt from
a plain mapping, so a field added to :class:`ReviewDigest` that is not also
written by :func:`build_review_digest_document` *and* read by
:func:`_review_digest_from_mapping` is silently dropped -- which is exactly
how ``-o review=...`` came to render an accepted result naming neither the
rule that demoted its findings nor the reason (Codex review, PR #1284).
All three halves are in this file so the next such field is harder to
half-wire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .disposition_audit import DispositionAudit
from .document import ReportDocument
from .envelope import ReportEnvelope
from .render_review import (
    ImpactedSymbol,
    ReviewDigest,
    render_review_digest,
    render_terminal_digest,
)
from .review_digest_compute import compute_review_digest
from .surface_changes import SurfaceChangeSection

if TYPE_CHECKING:
    from collections.abc import Mapping


def build_review_digest_document(
    result: Any,
    *,
    envelope: ReportEnvelope,
) -> ReportDocument:
    """The ``-o review=...`` digest as a ``ReportDocument``.

    *envelope* is the completed ``ReportEnvelope`` this render projects
    (ADR-061 gap C). Its shared ``report_mode="full"`` document supplies the
    disposition audit, review groups, result counts and exit code, and the
    envelope itself supplies the gate and per-finding verdicts; see
    :func:`~abicheck.report.review_digest_compute.compute_review_digest` for what each
    one drives.
    """
    shared_mapping = envelope.document.to_mapping()
    exit_value = shared_mapping.get("exit")
    exit_code = exit_value.get("code") if isinstance(exit_value, dict) else None
    digest = compute_review_digest(
        result,
        envelope=envelope,
        review_groups=cast("Any", shared_mapping.get("review_groups")),
        result_counts=cast("Any", shared_mapping.get("result_counts")),
        gate_exit_code=cast("Any", exit_code),
    )
    d: dict[str, object] = {
        "library": digest.library,
        "old_version": digest.old_version,
        "new_version": digest.new_version,
        "verdict_emoji": digest.verdict_emoji,
        "verdict_label": digest.verdict_label,
        "effect": digest.effect,
        "manual_review_banner": digest.manual_review_banner,
        "coverage_warnings": list(digest.coverage_warnings),
        "additions_label": digest.additions_label,
        "breaking_count": digest.breaking_count,
        "source_breaks_count": digest.source_breaks_count,
        "risk_count": digest.risk_count,
        "additions_count": digest.additions_count,
        "quality_issues_count": digest.quality_issues_count,
        "informational_count": digest.informational_count,
        "scoped": digest.scoped,
        "out_of_surface_count": digest.out_of_surface_count,
        "bump_value": digest.bump_value,
        "soname_value": digest.soname_value,
        "impacted": [
            {"symbol": s.symbol, "kind": s.kind}
            | ({"use_cases": list(s.use_cases)} if s.use_cases else {})
            for s in digest.impacted
        ],
        "disposition_audit": (
            None
            if digest.disposition_audit is None
            else digest.disposition_audit.to_dict()
        ),
        "surface_changes": (
            None if digest.surface_changes is None else digest.surface_changes.to_dict()
        ),
        "env_matrix_source_sha256": digest.env_matrix_source_sha256,
        # ADR-067: the digest is the summary a reviewer approves a merge
        # from, so a rule that demoted the findings must survive the round
        # trip through this mapping -- the digest carried the field and the
        # mapping dropped it, so `-o review=...` still showed an accepted
        # result with no rule and no reason (Codex review, PR #1284).
        "pattern_modulations": list(digest.pattern_modulations),
        "review_groups": list(digest.review_groups),
        "policy": digest.policy,
        "gate_exit_code": digest.gate_exit_code,
        "result_counts": digest.result_counts,
        "evidence_summary": digest.evidence_summary,
        "show_release_recommendation": digest.show_release_recommendation,
        "policy_acceptance": digest.policy_acceptance,
        **(
            {
                "review_group_use_cases": {
                    k: list(v) for k, v in digest.review_group_use_cases.items()
                }
            }
            if digest.review_group_use_cases
            else {}
        ),
    }
    return ReportDocument.from_mapping(d)


def _review_digest_from_mapping(d: Mapping[str, Any]) -> ReviewDigest:
    impacted = tuple(
        ImpactedSymbol(
            symbol=item["symbol"],
            kind=item["kind"],
            use_cases=tuple(item.get("use_cases", ())),
        )
        for item in d["impacted"]
    )
    return ReviewDigest(
        library=d["library"],
        old_version=d["old_version"],
        new_version=d["new_version"],
        verdict_emoji=d["verdict_emoji"],
        verdict_label=d["verdict_label"],
        effect=d["effect"],
        manual_review_banner=d["manual_review_banner"],
        coverage_warnings=tuple(d["coverage_warnings"]),
        additions_label=d["additions_label"],
        breaking_count=d["breaking_count"],
        source_breaks_count=d["source_breaks_count"],
        risk_count=d["risk_count"],
        additions_count=d["additions_count"],
        quality_issues_count=d.get("quality_issues_count", 0),
        informational_count=d.get("informational_count", 0),
        scoped=d["scoped"],
        out_of_surface_count=d["out_of_surface_count"],
        bump_value=d["bump_value"],
        soname_value=d["soname_value"],
        impacted=impacted,
        disposition_audit=(
            None
            if d.get("disposition_audit") is None
            else DispositionAudit.from_dict(d["disposition_audit"])
        ),
        surface_changes=(
            None
            if d.get("surface_changes") is None
            else SurfaceChangeSection.from_dict(d["surface_changes"])
        ),
        env_matrix_source_sha256=d.get("env_matrix_source_sha256"),
        pattern_modulations=tuple(d.get("pattern_modulations") or ()),
        review_groups=tuple(d.get("review_groups") or ()),
        policy=d.get("policy", "strict_abi"),
        gate_exit_code=d.get("gate_exit_code"),
        result_counts=dict(d.get("result_counts") or {}),
        evidence_summary=d.get("evidence_summary", ""),
        review_group_use_cases={
            k: tuple(v) for k, v in (d.get("review_group_use_cases") or {}).items()
        },
        show_release_recommendation=bool(d.get("show_release_recommendation", False)),
        policy_acceptance=d.get("policy_acceptance"),
    )


def render_review_digest_document(doc: ReportDocument) -> str:
    """Project a review-digest ``ReportDocument`` to its Markdown text."""
    return render_review_digest(_review_digest_from_mapping(doc.to_mapping()))


def render_terminal_digest_document(doc: ReportDocument) -> str:
    return render_terminal_digest(_review_digest_from_mapping(doc.to_mapping()))
