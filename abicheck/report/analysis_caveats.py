# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The facts that make a run *incomplete*, read off the shared report document.

AGENTS.md "Weaker evidence narrows conclusions": a failed extractor is an
explicit ``FAILED`` fact, never an empty surface, and missing evidence never
upgrades to a clean compatibility claim. The extractors and the assurance
layer already record those facts in the canonical ``ReportDocument`` (the
JSON report): a snapshot section whose ``section_availability`` status is
``failed``/``blocked``/``not_collected``, an analysis forced through as not
comparable, a finding contract evaluation left ``NOT_EVALUATED``, a public
surface that could not be resolved, an incomplete contract coverage.

Before this module only JSON carried all of them; Markdown, HTML, SARIF,
JUnit, ``--stat`` and the Action summary each showed a different subset and
the review digest's headline read "safe to merge" over any of them. Each
format now reads the same list from here, so one fact cannot be visible in
one format and missing in another.

:func:`analysis_caveats` is a pure function of the JSON-shaped document
mapping, so it serves the in-process formats (through the envelope's shared
document) and the Action summary (which reads the written JSON report) with
one definition. It decides nothing new: every caveat restates a fact the
document already holds, with the document's own wording where it has one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from .envelope import ReportEnvelope

__all__ = [
    "CAVEAT_HEADING",
    "CAVEAT_KEYS",
    "AnalysisCaveat",
    "analysis_caveats",
    "analysis_caveats_for",
    "caveat_dicts",
]

#: The one phrase every format prefixes its caveat block with, so a reader --
#: or a test -- can find the qualification next to any clean-looking verdict.
CAVEAT_HEADING = "Analysis incomplete"

#: Section statuses that stand in for evidence that does not exist
#: (``model.section_availability.SectionStatus.narrows``). ``unrecorded``
#: falls back to the pre-table reading and does not narrow the comparison.
_NARROWING_SECTION_STATUSES = frozenset({"failed", "blocked", "not_collected"})

#: Every caveat key, in the order a format lists them.
CAVEAT_KEYS: tuple[str, ...] = (
    "section_unavailable",
    "not_comparable",
    "contract_coverage_partial",
    "unresolved_relevance",
    "scope_unresolved",
)


@dataclass(frozen=True)
class AnalysisCaveat:
    """One fact that leaves this run's result incomplete.

    *key* is one of :data:`CAVEAT_KEYS` (machine-readable formats emit it);
    *message* is the human sentence every human format prints verbatim.
    """

    key: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "message": self.message}


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, (list, tuple)) else ()


def _section_caveats(assurance: Mapping[str, object]) -> list[AnalysisCaveat]:
    caveats = []
    for raw in _sequence(assurance.get("section_availability")):
        entry = _mapping(raw)
        status = str(entry.get("status", ""))
        if status not in _NARROWING_SECTION_STATUSES:
            continue
        diagnostics = [str(d) for d in _sequence(entry.get("diagnostics")) if d]
        detail = f": {'; '.join(diagnostics)}" if diagnostics else ""
        caveats.append(
            AnalysisCaveat(
                "section_unavailable",
                f"section '{entry.get('section', '?')}' of the "
                f"{entry.get('side', '?')} snapshot is {status}{detail}; every "
                "analysis reading it was not evaluated",
            )
        )
    return caveats


def _not_comparable_caveat(document: Mapping[str, object]) -> AnalysisCaveat | None:
    assurance = _mapping(document.get("analysis_assurance"))
    forced = document.get("assurance") == "none"
    if assurance.get("status") != "not_comparable" and not forced:
        return None
    dims = _mapping(document.get("comparability_assurance"))
    weak = sorted(k for k, v in dims.items() if v != "trusted")
    detail = f" (unverified: {', '.join(weak)})" if weak else ""
    return AnalysisCaveat(
        "not_comparable",
        "old and new snapshots are not comparable"
        f"{detail}; the comparison was forced through and its result is not "
        "a compatibility claim",
    )


def _unresolved_relevance_caveat(
    document: Mapping[str, object],
) -> AnalysisCaveat | None:
    counts = _mapping(_mapping(document.get("disposition_audit")).get("counts"))
    unresolved = counts.get("unresolved_relevance", 0)
    if not isinstance(unresolved, int) or unresolved <= 0:
        return None
    return AnalysisCaveat(
        "unresolved_relevance",
        f"{unresolved} finding(s) NOT_EVALUATED: contract relevance could not be "
        "resolved, so compatibility policy never scored them",
    )


def analysis_caveats(document: Mapping[str, object]) -> tuple[AnalysisCaveat, ...]:
    """Every incomplete-analysis fact *document* (a JSON report mapping) holds,
    in :data:`CAVEAT_KEYS` order. Empty for a complete run."""
    caveats = _section_caveats(_mapping(document.get("analysis_assurance")))
    if (nc := _not_comparable_caveat(document)) is not None:
        caveats.append(nc)
    if document.get("contract_coverage") == "partial":
        caveats.append(
            AnalysisCaveat(
                "contract_coverage_partial",
                "contract coverage is partial: the declared contract could not be "
                "resolved on every side, so findings outside it were not checked",
            )
        )
    if (ur := _unresolved_relevance_caveat(document)) is not None:
        caveats.append(ur)
    if _mapping(document.get("scope")).get("manual_review_required") is True:
        caveats.append(
            AnalysisCaveat(
                "scope_unresolved",
                "public-header scoping could not resolve the public surface and "
                "fell back to the full export table; manual review required",
            )
        )
    return tuple(caveats)


def analysis_caveats_for(
    result: DiffResult, envelope: ReportEnvelope | None = None
) -> tuple[AnalysisCaveat, ...]:
    """:func:`analysis_caveats` of the envelope's shared document, or, for a
    direct caller with no envelope, of the same report fields built from
    *result* by the producers the JSON report itself calls (a whole second
    document would be a second report build per render)."""
    if envelope is not None:
        return analysis_caveats(envelope.document.to_mapping())
    from ..policy.analysis_assurance import analysis_assurance_report_dict
    from .change_json import scope_dict
    from .disposition_audit import disposition_audit_dict_reusing_document

    return analysis_caveats(
        {
            "analysis_assurance": analysis_assurance_report_dict(result),
            "assurance": result.assurance,
            "comparability_assurance": result.comparability_assurance,
            "contract_coverage": result.contract_coverage,
            "disposition_audit": disposition_audit_dict_reusing_document(
                result, None, None
            ),
            "scope": scope_dict(result),
        }
    )


def caveat_dicts(caveats: Sequence[AnalysisCaveat]) -> list[dict[str, str]]:
    """JSON-safe form, for a format that folds them into its own document."""
    return [c.to_dict() for c in caveats]
