# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""Explicit cross-layer joins (evidence-entity-model plan, Phase 2, I2/I3).

A *join* relates two independently observed evidence layers -- the L0
export table and the L2 declarations, or the L1 debug types and the L2
header records -- under a stated rule. This module is the vocabulary every
join shares; the producers live in ``compare/`` (``export_join.py``,
``debug_type_join.py``).

**A join is evidence, never name equality (I2).** A declaration's own
linker name is not proof that it is exported: the ``declares_linker_name``
edge (``compare/surface_graph.py``) stays ``derived``. Only a lookup of that
spelling in the *observed* table is. Each subject on each side lands in
exactly one :class:`JoinState`, recorded with the candidates it met on the
other side, so an ambiguous or absent join is a first-class result rather
than a silently dropped one. Both orphans are valid: a public inline
declaration may have no export, and an export may have no declaration.

**Weaker evidence narrows conclusions.** When one side was never observed
(no export table captured, no debug section read), every subject is
:attr:`JoinState.UNKNOWN` and :attr:`CrossLayerJoin.complete` is ``False``
-- never "all unmatched".

**Evidence class (I3).** Every join edge kind is
:attr:`~abicheck.model.graph_evidence_class.EdgeEvidenceClass.RESOLVED_JOIN`
and is described by a :class:`JoinSpec` naming its producer, its inputs and
its recompute rule. Join results are recomputed from the persisted
observations on demand and never stored themselves (Phase 5's "persist
observed, derive the rest").
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .graph_evidence_class import EdgeEvidenceClass

__all__ = [
    "BINARY_SYMBOL_PREFIX",
    "DEBUG_TYPE_JOIN",
    "DEBUG_TYPE_PREFIX",
    "EDGE_KIND_DEBUG_TYPE_OF",
    "EDGE_KIND_EXPORTS",
    "EXPORT_JOIN",
    "JOIN_SPECS",
    "CrossLayerJoin",
    "JoinRecord",
    "JoinSpec",
    "JoinState",
    "binary_symbol_node_id",
    "debug_type_node_id",
    "resolve_join_records",
]

#: Node-id prefix of an *observed* export-table entry. Disjoint from
#: ``symbol://`` -- the ``derived`` projection of a declaration's own
#: linker name, which is never proof of an export.
BINARY_SYMBOL_PREFIX = "binary_symbol://"
#: Node-id prefix of an observed debug-info type occurrence.
DEBUG_TYPE_PREFIX = "debug_type://"

#: Observed export -> declaration. The name Phase 0 reserved for this join.
EDGE_KIND_EXPORTS = "exports"
#: Observed debug-info type -> header type (record or enum).
EDGE_KIND_DEBUG_TYPE_OF = "debug_type_of"


def binary_symbol_node_id(platform: str, spelling: str) -> str:
    """The node id of export-table entry *spelling* in *platform*'s table.

    Keyed by table as well as spelling: the same spelling in an ELF and a
    Mach-O table of one snapshot are two observations, and only one of them
    may be the one a declaration answers to."""
    return f"{BINARY_SYMBOL_PREFIX}{platform}/{spelling}"


def debug_type_node_id(fmt: str, kind: str, name: str, occurrence: int = 1) -> str:
    """The node id of one debug-info type occurrence. *occurrence* > 1 names
    a further, layout-distinct definition of the same name (an ODR
    conflict), which is never merged with the first."""
    base = f"{DEBUG_TYPE_PREFIX}{fmt}/{kind}/{name}"
    return base if occurrence == 1 else f"{base}#{occurrence}"


class JoinState(str, enum.Enum):
    """The one state a subject lands in for one join (I2)."""

    #: Exactly one candidate on the other side, under the join rule.
    MATCHED = "matched"
    #: Several candidates compete; all are listed, none is chosen.
    AMBIGUOUS = "ambiguous"
    #: The other side was observed and holds no candidate.
    UNMATCHED = "unmatched"
    #: The other side was not observed, so nothing can be concluded.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class JoinSpec:
    """What an I3 ``resolved_join`` edge kind is: who produces it, from
    which observations, and how it is recomputed."""

    edge_kind: str
    producer: str
    inputs: tuple[str, ...]
    recompute_rule: str
    evidence_class: EdgeEvidenceClass = EdgeEvidenceClass.RESOLVED_JOIN


EXPORT_JOIN = JoinSpec(
    edge_kind=EDGE_KIND_EXPORTS,
    producer="compare.export_join.join_exports",
    inputs=(
        "AbiSnapshot.elf/.pe/.macho export tables (model.export_index)",
        "AbiSnapshot.functions/.variables linker names",
    ),
    recompute_rule=(
        "exact linker-name membership in each observed table; on a Mach-O "
        "table only, a one-underscore shift (Phase 1's decoration alias) "
        "no other declaration owns exactly. Recomputed from the snapshot on "
        "demand, never persisted."
    ),
)

DEBUG_TYPE_JOIN = JoinSpec(
    edge_kind=EDGE_KIND_DEBUG_TYPE_OF,
    producer="compare.debug_type_join.join_debug_types",
    inputs=(
        "AbiSnapshot.dwarf structs/enums (DWARF, or BTF/CTF/PDB reduced to it)",
        "AbiSnapshot.dwarf.odr_conflicts",
        "AbiSnapshot.types/.enums qualified names and layouts",
    ),
    recompute_rule=(
        "exact qualified-name equality with a header record/enum of the same "
        "kind, corroborated by every layout fact both sides carry (union-ness, "
        "size, field offsets, enumerator values); an ODR conflict or a "
        "spelling several header entities share is ambiguous. Recomputed "
        "from the snapshot on demand, never persisted."
    ),
)

JOIN_SPECS: Mapping[str, JoinSpec] = MappingProxyType(
    {spec.edge_kind: spec for spec in (EXPORT_JOIN, DEBUG_TYPE_JOIN)}
)


@dataclass(frozen=True, slots=True)
class JoinRecord:
    """One subject's result for one join.

    *candidates* are the other side's node ids that satisfied the join rule,
    sorted: at least one for :attr:`JoinState.MATCHED` (one entity may be
    observed several times -- the same C function in an ELF and a Mach-O
    table -- without that being a competition), two or more for
    :attr:`JoinState.AMBIGUOUS`, none otherwise. Which candidate sets count
    as competing is the producer's stated rule (``ambiguous`` below). *rejected* lists node ids the
    name pointed at but whose evidence contradicted the join (a layout
    conflict), so a consumer can still report the contradiction. *reason* is
    a stable, machine-readable code."""

    subject: str
    state: JoinState
    candidates: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        n = len(self.candidates)
        expected = {
            JoinState.MATCHED: n >= 1,
            JoinState.AMBIGUOUS: n >= 2,
            JoinState.UNMATCHED: n == 0,
            JoinState.UNKNOWN: n == 0 and not self.rejected,
        }[self.state]
        if not expected:
            raise ValueError(
                f"join record for {self.subject!r}: state {self.state.value} "
                f"with {n} candidate(s)"
            )


def resolve_join_records(
    candidates_by_subject: Mapping[str, set[str]],
    *,
    reasons: Mapping[str, str] | None = None,
    rejected: Mapping[str, set[str]] | None = None,
    unmatched_reason: str,
    competing: Callable[[set[str]], bool] = lambda cands: len(cands) > 1,
) -> dict[str, JoinRecord]:
    """Turn a subject -> candidate-set mapping into one record per subject.

    The state follows from the candidates alone -- none is ``unmatched``, a
    set *competing* says is a competition is ``ambiguous``, anything else is
    ``matched`` -- so no producer can reach a state its own evidence does not
    support. *competing* defaults to "more than one candidate"."""
    reasons = reasons or {}
    rejected = rejected or {}
    out: dict[str, JoinRecord] = {}
    for subject, cands in candidates_by_subject.items():
        n = len(cands)
        state = (
            JoinState.UNMATCHED
            if n == 0
            else JoinState.AMBIGUOUS
            if n > 1 and competing(cands)
            else JoinState.MATCHED
        )
        out[subject] = JoinRecord(
            subject=subject,
            state=state,
            candidates=tuple(sorted(cands)),
            rejected=tuple(sorted(rejected.get(subject, ()))),
            reason=reasons.get(subject, "" if n else unmatched_reason),
        )
    return out


@dataclass(frozen=True)
class CrossLayerJoin:
    """Both sides of one join.

    ``left`` holds the entity side (declarations, header types) and
    ``right`` the observed side (export entries, debug types), each keyed by
    node id. ``complete`` is ``False`` when a side was not observed; every
    record is then :attr:`JoinState.UNKNOWN`."""

    spec: JoinSpec
    complete: bool
    left: Mapping[str, JoinRecord]
    right: Mapping[str, JoinRecord]
    #: Why the join is incomplete (``""`` when complete).
    incomplete_reason: str = ""

    def edges(self) -> Iterator[tuple[str, str]]:
        """``(right, left)`` pairs -- observed subject to entity -- for every
        candidate relation, matched or ambiguous. The edge direction is the
        one the graph uses (an export *exports* a declaration)."""
        for subject, rec in self.right.items():
            for cand in rec.candidates:
                yield subject, cand

    def state_counts(self) -> dict[str, dict[str, int]]:
        """``{"left"|"right": {state: count}}`` -- every state listed, zero
        included, so a report never has to guess an absent key."""
        out: dict[str, dict[str, int]] = {}
        for side, records in (("left", self.left), ("right", self.right)):
            counts = {s.value: 0 for s in JoinState}
            for rec in records.values():
                counts[rec.state.value] += 1
            out[side] = counts
        return out
