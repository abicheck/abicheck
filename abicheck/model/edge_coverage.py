# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""Typed absence (evidence-entity-model plan, Phase 4, invariant I4).

A query for edge kind *K* in scope *S* about a subject answers exactly one of
three things (:class:`EdgeAnswer`): the edge is ``present``, it is
``proven_absent``, or it is ``unknown``. "No edge" is never read as
"absent" on its own: ``proven_absent`` requires the producer of *K* to have
covered *S*, and the answer carries the :class:`CoverageRecord` rows it rests
on so a report or a replay can say *why*.

This module is the vocabulary only -- the answer, the per-producer coverage
record and the query result. Evaluation lives in ``compare/edge_query.py``
(``model`` depends on nothing).

**Scope units.** A producer is responsible for a set of *scope units* and
states how much of it it covered. A unit is an opaque string whose meaning
belongs to the edge kind: an export-table platform (``"elf"``), the debug
section (``"debug"``), the header AST (``"headers"``), a compile unit path
for an L5 pass. A query's scope is a set of units; ``None`` means "every unit
some producer of *K* is responsible for". :data:`ALL_UNITS` in a record's
units means "every unit" -- a whole-project L5 pass is responsible for every
compile unit, including ones no query has named yet.

**What each run status covers.** :attr:`ProducerRun.RAN` covers every unit it
is responsible for; :attr:`ProducerRun.PARTIAL` covers only
:attr:`CoverageRecord.covered`; :attr:`ProducerRun.NOT_RUN` and
:attr:`ProducerRun.FAILED` cover nothing. A failed extractor is therefore an
explicit record that covers nothing -- never an empty surface that reads as
"absent" (AGENTS.md: "a failed extractor is an error or an explicit FAILED
fact, never an empty surface"). An edge a partial or failed producer *did*
emit is still real: presence never depends on coverage.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ALL_UNITS",
    "ANSWER_REASONS",
    "CoverageRecord",
    "EdgeAnswer",
    "EdgeQuery",
    "EdgeQueryResult",
    "ProducerRun",
    "REASON_EDGE_OBSERVED",
    "REASON_NO_PRODUCER",
    "REASON_PRODUCER_COVERED_SCOPE",
    "REASON_SCOPE_NOT_COVERED",
    "REASON_SUBJECT_UNKNOWN",
]


class EdgeAnswer(str, enum.Enum):
    """I4's three-valued answer."""

    #: The producer observed (or, for a ``derived`` kind, the records
    #: project) the edge.
    PRESENT = "present"
    #: The producer covered the whole queried scope and emitted no such edge.
    PROVEN_ABSENT = "proven_absent"
    #: No edge, and no producer covered the whole queried scope.
    UNKNOWN = "unknown"


class ProducerRun(str, enum.Enum):
    """How far one producer got over the units it is responsible for."""

    #: Ran over every unit it is responsible for, without diagnostics.
    RAN = "ran"
    #: Ran over :attr:`CoverageRecord.covered` only (a narrowed pass).
    PARTIAL = "partial"
    #: Never ran: the input it reads was not collected.
    NOT_RUN = "not_run"
    #: Ran and failed or degraded: what it emitted is real, what it did not
    #: emit proves nothing.
    FAILED = "failed"


#: The wildcard unit: a record responsible for (or covering) every unit.
ALL_UNITS = "*"

#: The answer was ``present``: an edge was observed.
REASON_EDGE_OBSERVED = "edge_observed"
#: ``proven_absent``: a producer covered the queried scope.
REASON_PRODUCER_COVERED_SCOPE = "producer_covered_scope"
#: ``unknown``: some queried unit no record covered.
REASON_SCOPE_NOT_COVERED = "scope_not_covered"
#: ``unknown``: no producer of the edge kind is on record at all.
REASON_NO_PRODUCER = "no_producer"
#: ``unknown``: the subject is not an entity this evidence knows about, so
#: no producer could have emitted an edge for it.
REASON_SUBJECT_UNKNOWN = "subject_unknown"

ANSWER_REASONS: frozenset[str] = frozenset(
    {
        REASON_EDGE_OBSERVED,
        REASON_PRODUCER_COVERED_SCOPE,
        REASON_SCOPE_NOT_COVERED,
        REASON_NO_PRODUCER,
        REASON_SUBJECT_UNKNOWN,
    }
)


@dataclass(frozen=True, slots=True)
class CoverageRecord:
    """What one producer of one edge kind covered.

    *units* are the scope units the producer is responsible for; *covered*
    the subset a :attr:`ProducerRun.PARTIAL` run examined (ignored for the
    other statuses, see :meth:`covered_units`). *source* names the snapshot
    field the record was read from, and *reason* a stable code for a status
    other than ``ran`` -- together they are what a report shows and a replay
    re-reads."""

    edge_kind: str
    producer: str
    run: ProducerRun
    units: frozenset[str]
    covered: frozenset[str] = field(default_factory=frozenset)
    reason: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if (
            self.run is ProducerRun.PARTIAL
            and ALL_UNITS not in self.units
            and not self.covered <= self.units
        ):
            raise ValueError(
                f"coverage record {self.producer!r}: partial coverage "
                f"{sorted(self.covered - self.units)} outside its units"
            )

    def covered_units(self) -> frozenset[str]:
        """The units this record actually vouches for."""
        if self.run is ProducerRun.RAN:
            return self.units
        if self.run is ProducerRun.PARTIAL:
            return self.covered
        return frozenset()

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_kind": self.edge_kind,
            "producer": self.producer,
            "run": self.run.value,
            "units": sorted(self.units),
            "covered": sorted(self.covered_units()),
            "reason": self.reason,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class EdgeQuery:
    """ "Does *subject* have a *edge_kind* edge (to *target*) in *scope*?"

    *subject* is a graph node id. *target*, when set, narrows the question
    to one edge; otherwise any edge of the kind counts. *scope* is a set of
    scope units, ``None`` for every unit a producer is responsible for."""

    edge_kind: str
    subject: str
    target: str | None = None
    scope: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class EdgeQueryResult:
    """An :class:`EdgeAnswer` and the coverage records it rests on."""

    query: EdgeQuery
    answer: EdgeAnswer
    records: tuple[CoverageRecord, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.reason not in ANSWER_REASONS:
            raise ValueError(f"unknown edge-query reason {self.reason!r}")
        if self.answer is EdgeAnswer.PROVEN_ABSENT and not any(
            r.covered_units() for r in self.records
        ):
            raise ValueError("proven_absent needs a covering coverage record")

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_kind": self.query.edge_kind,
            "subject": self.query.subject,
            "target": self.query.target,
            "scope": None if self.query.scope is None else sorted(self.query.scope),
            "answer": self.answer.value,
            "reason": self.reason,
            "records": [r.to_dict() for r in self.records],
        }
