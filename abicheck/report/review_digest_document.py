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

import abicheck.reporter_markdown as _reporter_markdown_module

from .disposition_audit import DispositionAudit
from .document import ReportDocument
from .envelope import ReportEnvelope, resolved_document
from .render_markdown import (
    ImpactedSymbol,
    ReviewDigest,
    render_review_digest,
)
from .surface_changes import SurfaceChangeSection

if TYPE_CHECKING:
    from collections.abc import Mapping


def _reporter_markdown() -> Any:
    """The ``reporter_markdown`` module, as this file's compute side.

    The same accessor ``render_markdown_document`` keeps, duplicated here
    rather than imported from it, so this module does not import back into
    the file it was split out of.
    """
    return _reporter_markdown_module


def build_review_digest_document(
    result: Any,
    *,
    severity_config: Any = None,
    report_document: ReportDocument | None = None,
    envelope: ReportEnvelope | None = None,
) -> ReportDocument:
    """The ``-o review=...`` digest as a ``ReportDocument``.

    *severity_config* is forwarded to :func:`~abicheck.reporter_markdown.
    compute_review_digest` unchanged -- see that function's own docstring
    for what it drives (the merge-effect phrase).

    *report_document* (ADR-061 gap C), when given, is the one canonical
    ``report_mode="full"`` document ``report.build.build_report_document``
    already built for this render -- ``service_render.render_output``'s
    ``review`` branch builds it once and threads it down through
    ``to_review_digest``. Its ``disposition_audit`` field is reused verbatim
    here instead of a second, independently-resolved call to
    ``compute_disposition_audit`` (the same ledger, the same arguments --
    calling it twice cannot disagree, but building through the one shared
    choke point is the point of this closure, not just its safety). A direct
    caller with no such document (an existing Tier-2/test call site) keeps
    the prior behaviour by passing nothing.

    *envelope* (ADR-061 gap C) is the completed ``ReportEnvelope`` that
    document belongs to. Beyond supplying the document, it carries the
    already-resolved per-finding verdicts the digest's "impacted symbols"
    list used to re-resolve through its own ``report_findings_for`` call --
    the same canonical primitive, but a second resolution of a decision this
    render had already made -- and its already-resolved ``GateDecision``,
    which the merge-effect phrase now projects instead of a second
    ``compute_exit_code`` call of its own (CodeRabbit review).
    """
    shared_document = resolved_document(envelope, report_document)
    shared_disposition_audit = (
        DispositionAudit.from_dict(
            cast("Mapping[str, Any]", shared_document.to_mapping()["disposition_audit"])
        )
        if shared_document is not None
        else None
    )
    digest = _reporter_markdown().compute_review_digest(
        result,
        severity_config=severity_config,
        disposition_audit=shared_disposition_audit,
        findings=None if envelope is None else envelope.findings,
        gate=None if envelope is None else envelope.gate,
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
        "scoped": digest.scoped,
        "out_of_surface_count": digest.out_of_surface_count,
        "bump_value": digest.bump_value,
        "soname_value": digest.soname_value,
        "impacted": [{"symbol": s.symbol, "kind": s.kind} for s in digest.impacted],
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
    }
    return ReportDocument.from_mapping(d)


def _review_digest_from_mapping(d: Mapping[str, Any]) -> ReviewDigest:
    impacted = tuple(ImpactedSymbol(**item) for item in d["impacted"])
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
    )


def render_review_digest_document(doc: ReportDocument) -> str:
    """Project a review-digest ``ReportDocument`` to its Markdown text."""
    return render_review_digest(_review_digest_from_mapping(doc.to_mapping()))
