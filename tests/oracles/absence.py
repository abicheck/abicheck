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
