"""Invariant A: absence typing.

A comparison whose analysis assurance is ``partial``, ``failed`` or
``not_comparable`` lacks evidence the verdict would need. Such a run must not
state a compatibility claim (``NO_CHANGE``, ``COMPATIBLE``,
``COMPATIBLE_WITH_RISK``), recommend a release bump that presumes one
(``none``, ``patch``), pass the gate with exit 0, or word its rendered
headline as a clean result. ``not_requested`` is not an absence: the depth was
never asked for, so nothing was lost.

ADR-090 (proposed) is the contract these checks express for an unresolved
surface without type-level evidence: verdict ``UNCONFIRMED``, version bump
``undetermined``, exit 1. Each of those satisfies the oracle; the current
``NO_CHANGE``/``none``/0 does not.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Verdicts that assert the observed change set is compatible.
COMPATIBILITY_CLAIMS: frozenset[str] = frozenset(
    {"NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK"}
)

#: ``analysis_assurance.status`` values that mean evidence was missing.
ABSENT_EVIDENCE_STATUSES: frozenset[str] = frozenset(
    {"partial", "failed", "not_comparable"}
)


#: ``release_recommendation.version_bump`` values that presume the change set
#: is compatible, so they are claims a run without the evidence cannot make.
COMPATIBLE_VERSION_BUMPS: frozenset[str] = frozenset({"none", "patch"})


def absence_violations(report: Mapping[str, Any]) -> list[str]:
    """Violations of invariant A in one compare-report JSON document."""
    return [*verdict_violations(report), *version_bump_violations(report)]


def verdict_violations(report: Mapping[str, Any]) -> list[str]:
    """A compatibility verdict on a run whose evidence was absent."""
    status = (report.get("analysis_assurance") or {}).get("status")
    verdict = report.get("verdict")
    if status in ABSENT_EVIDENCE_STATUSES and verdict in COMPATIBILITY_CLAIMS:
        return [
            f"verdict {verdict} asserts compatibility while "
            f"analysis_assurance.status is {status!r}"
        ]
    return []


def version_bump_violations(report: Mapping[str, Any]) -> list[str]:
    """A release recommendation that presumes compatibility on absent evidence."""
    status = (report.get("analysis_assurance") or {}).get("status")
    bump = (report.get("release_recommendation") or {}).get("version_bump")
    if status in ABSENT_EVIDENCE_STATUSES and bump in COMPATIBLE_VERSION_BUMPS:
        return [
            f"release_recommendation.version_bump {bump!r} presumes compatibility "
            f"while analysis_assurance.status is {status!r}"
        ]
    return []


def gate_violations(exit_code: int, status: str | None) -> list[str]:
    """A run without the evidence it needed must not pass the gate with exit 0."""
    if status in ABSENT_EVIDENCE_STATUSES and exit_code == 0:
        return [f"exit 0 (gate passed) while analysis_assurance.status is {status!r}"]
    return []


#: Renderer phrases that state acceptance or a clean compatibility result.
#: Checked on rendered output so a headline cannot claim acceptance while the
#: JSON says the evidence was partial.
CLEAN_CLAIM_PHRASES: tuple[str, ...] = (
    "Gate: ACCEPTED",
    "**Gate:** PASS",
    "safe to merge",
    "Compatibility: NO_CHANGE",
    "Compatibility: COMPATIBLE",
    "`NO_CHANGE`",
    "`COMPATIBLE`",
    "`COMPATIBLE_WITH_RISK`",
)


def rendered_absence_violations(
    rendered: Mapping[str, str],
    status: str | None,
) -> list[str]:
    """Renderers that state a clean claim while analysis assurance is absent.

    *rendered* maps a renderer name (``"terminal"``, ``"markdown"``,
    ``"review"``, ``"html"``) to its
    output text. Only runs whose assurance is ``partial``, ``failed`` or
    ``not_comparable`` are judged.
    """
    if status not in ABSENT_EVIDENCE_STATUSES:
        return []
    return [
        f"{name} states a clean claim ({phrase!r}) while analysis_assurance.status is {status!r}"
        for name, text in sorted(rendered.items())
        for phrase in CLEAN_CLAIM_PHRASES
        if phrase in text
    ]
