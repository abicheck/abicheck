# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The ADR-050 D2 comparability-refusal report document.

``checker.compare``'s gate raises before any ``DiffResult`` exists, so this
document is not a projection of a workflow result — but it is still a report,
carries a report schema version, and is consumed by the same CI tooling, so
it is built and rendered here rather than assembled inline in a CLI frontend.
"""

from __future__ import annotations

from .document import ReportDocument
from .render_json import render_json


def not_comparable_document(
    library: str,
    old_version: str,
    new_version: str,
    kind: str,
    message: str,
    *,
    report_schema_version: str,
) -> ReportDocument:
    """Build the schema-conformant ``{"verdict": null, "reason": {...}}`` report.

    The schema version is a parameter rather than an import on purpose:
    ADR-061 D1 gives ``report`` ownership of *report* schemas, but
    ``abicheck.schemas`` is one package holding the report, aggregate, build
    evidence, and build-source-pack schemas together, so classifying it
    ``report`` would claim ownership of three schemas this layer must not own.
    Splitting that package is its own migration; until then the caller states
    the version it is emitting.
    """
    return ReportDocument.from_mapping(
        {
            "report_schema_version": report_schema_version,
            "library": library,
            "old_version": old_version,
            "new_version": new_version,
            "verdict": None,
            "reason": {"kind": kind, "message": message},
        }
    )


def render_not_comparable_json(
    library: str,
    old_version: str,
    new_version: str,
    kind: str,
    message: str,
    *,
    report_schema_version: str,
) -> str:
    """Render the refusal report as JSON."""
    return render_json(
        not_comparable_document(
            library,
            old_version,
            new_version,
            kind,
            message,
            report_schema_version=report_schema_version,
        )
    )
