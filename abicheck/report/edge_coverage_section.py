# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""The "Relationship coverage" report section's compute half
(evidence-entity-model Phase 4, invariant I4).

Reads ``DiffResult.edge_coverage`` (``compare.edge_query.
edge_coverage_report``) and keeps, per side, the edge kinds whose absence
could not be proven everywhere: a producer that did not run, ran over part
of its scope or failed, or a join with ``unknown`` subjects. What is kept is
a small frozen struct of plain values; ``report/render_edge_coverage.py``
formats it and decides nothing. ``None`` means the result carries no
coverage summary at all (a hand-built result), which renders no section --
not the same as a section with nothing to report.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "EdgeCoverageRow",
    "EdgeCoverageSection",
    "compute_edge_coverage_section",
    "edge_coverage_section_from_mapping",
]


@dataclass(frozen=True, slots=True)
class EdgeCoverageRow:
    side: str
    edge_kind: str
    evidence_class: str
    #: ``"producer[units]: run (reason)"`` per coverage record.
    producers: tuple[str, ...]
    #: ``(population, present, proven_absent, unknown)`` per counted side of
    #: a join; empty for a kind whose subjects are not enumerated.
    answers: tuple[tuple[str, int, int, int], ...]


@dataclass(frozen=True, slots=True)
class EdgeCoverageSection:
    rows: tuple[EdgeCoverageRow, ...]
    #: Every edge kind reported, on both sides -- so "all covered" is stated
    #: rather than inferred from an empty table.
    kinds_checked: int


def _producer_label(rec: Mapping[str, Any]) -> str:
    units = ",".join(rec.get("units", ()))
    label = f"{rec.get('producer', '?')}[{units}]: {rec.get('run', '?')}"
    reason = rec.get("reason")
    return f"{label} ({reason})" if reason else label


def compute_edge_coverage_section(result: Any) -> EdgeCoverageSection | None:
    coverage = getattr(result, "edge_coverage", None)
    if not coverage:
        return None
    rows: list[EdgeCoverageRow] = []
    checked = 0
    for side in ("old", "new"):
        summary = coverage.get(side)
        if not isinstance(summary, Mapping):
            continue
        for kind in sorted(summary):
            entry = summary[kind]
            checked += 1
            records: Sequence[Mapping[str, Any]] = entry.get("records", ())
            answers_map: Mapping[str, Mapping[str, int]] = entry.get("answers") or {}
            answers = tuple(
                (
                    population,
                    counts.get("present", 0),
                    counts.get("proven_absent", 0),
                    counts.get("unknown", 0),
                )
                for population, counts in sorted(answers_map.items())
            )
            if entry.get("absence") != "unknown":
                continue
            rows.append(
                EdgeCoverageRow(
                    side=side,
                    edge_kind=kind,
                    evidence_class=str(entry.get("evidence_class", "")),
                    producers=tuple(_producer_label(r) for r in records),
                    answers=answers,
                )
            )
    return EdgeCoverageSection(rows=tuple(rows), kinds_checked=checked)


def edge_coverage_section_from_mapping(
    d: Mapping[str, Any] | None,
) -> EdgeCoverageSection | None:
    """Rebuild the section from its ``ReportDocument`` (JSON) round trip."""
    if d is None:
        return None
    return EdgeCoverageSection(
        rows=tuple(
            EdgeCoverageRow(
                side=r["side"],
                edge_kind=r["edge_kind"],
                evidence_class=r["evidence_class"],
                producers=tuple(r["producers"]),
                answers=tuple(tuple(a) for a in r["answers"]),  # type: ignore[misc]
            )
            for r in d["rows"]
        ),
        kinds_checked=d["kinds_checked"],
    )
