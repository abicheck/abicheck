"""Invariant A: absence typing.

A comparison whose analysis assurance is ``partial``, ``failed`` or
``not_comparable`` lacks evidence the verdict would need. Such a run must not
state a compatibility claim (``NO_CHANGE``, ``COMPATIBLE``,
``COMPATIBLE_WITH_RISK``). ``not_requested`` is not an absence: the depth was
never asked for, so nothing was lost.
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


def absence_violations(report: Mapping[str, Any]) -> list[str]:
    """Violations of invariant A in one compare-report JSON document."""
    verdict = report.get("verdict")
    status = (report.get("analysis_assurance") or {}).get("status")
    if verdict in COMPATIBILITY_CLAIMS and status in ABSENT_EVIDENCE_STATUSES:
        return [
            f"verdict {verdict} asserts compatibility while "
            f"analysis_assurance.status is {status!r}"
        ]
    return []


#: Renderer phrases that state acceptance or a clean compatibility result.
#: Checked on rendered output so a headline cannot claim acceptance while the
#: JSON says the evidence was partial.
CLEAN_CLAIM_PHRASES: tuple[str, ...] = (
    "Gate: ACCEPTED",
    "Compatibility: NO_CHANGE",
    "✅ `NO_CHANGE`",
)


def rendered_absence_violations(
    rendered: Mapping[str, str],
    status: str | None,
) -> list[str]:
    """Renderers that state a clean claim while analysis assurance is absent.

    *rendered* maps a renderer name (``"terminal"``, ``"markdown"``) to its
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
