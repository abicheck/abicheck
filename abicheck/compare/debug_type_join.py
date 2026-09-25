# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""The L1 debug-type join: observed debug types <-> L2 header types
(evidence-entity-model plan, Phase 2).

**Sides.** The *left* side is every header record/enum entity, keyed by its
invariant-I1 graph node id (``model.graph_entity_identity.
snapshot_identities``). The *right* side is every debug-info type
occurrence in ``AbiSnapshot.dwarf`` -- DWARF, or BTF/CTF/PDB reduced to the
same ``DwarfMetadata`` shape -- keyed by :func:`~abicheck.model.graph_join.
debug_type_node_id`. A further, layout-distinct definition of one name
(``DwarfMetadata.struct_odr_conflicts``, an ODR conflict across CUs) is its
own occurrence and is never merged with the first.

**Rule.** (Record half owned by ``model/debug_type_match.py``, which the
dump-time layout backfill shares.) An occurrence's candidates are the header entities of the same
kind (struct/class/union -> record, enum -> enum) whose qualified spelling
(``qualified_name or name``) *equals* the debug name, and whose layout does
not contradict it on any fact both sides carry: union-ness, total size, the
offset of every field both name, the value of every enumerator both name.
A contradicted candidate is listed under ``rejected`` rather than dropped,
since "the header and the binary disagree about this type" is itself the
evidence a consumer may want. No other spelling tolerance applies: castxml
drops an inline-namespace segment clang and DWARF keep (``ns::S`` vs
``ns::v1::S``), and per G15 the two stay separate without further evidence.

**States (I2).** An occurrence with one surviving candidate is ``matched``
(``reason`` says whether layout corroborated it or was unavailable); with
several (two header entities sharing a spelling, which Phase 1 already keeps
as separate ``unresolved`` nodes) it is ``ambiguous``. A header entity is
``ambiguous`` when several occurrences survive -- an ODR conflict the layout
could not resolve -- and ``unmatched`` when none does (castxml's
inline-namespace drop, a type the binary never instantiated). No debug info
at all makes the join incomplete and every header entity ``unknown``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..model.debug_type_match import (
    DebugRecordFacts,
    header_type_key,
    record_candidates,
)
from ..model.dwarf_facts import EnumInfo, StructLayout
from ..model.graph_entity_identity import SnapshotIdentities, snapshot_identities
from ..model.graph_join import (
    DEBUG_TYPE_JOIN,
    CrossLayerJoin,
    JoinRecord,
    JoinState,
    debug_type_node_id,
    resolve_join_records,
)

if TYPE_CHECKING:
    from ..model.dwarf_facts import DwarfMetadata
    from ..model.entities import EnumType, RecordType
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "REASON_LAYOUT_CONFLICT",
    "REASON_LAYOUT_CORROBORATED",
    "REASON_LAYOUT_UNAVAILABLE",
    "REASON_NO_DEBUG_INFO",
    "REASON_NO_DEBUG_TYPE",
    "REASON_NO_HEADER_TYPE",
    "REASON_ODR_CONFLICT",
    "DebugTypeJoin",
    "DebugTypeOccurrence",
    "join_debug_types",
]

REASON_LAYOUT_CORROBORATED = "layout_corroborated"
REASON_LAYOUT_UNAVAILABLE = "layout_unavailable"
REASON_LAYOUT_CONFLICT = "layout_conflict"
REASON_NO_HEADER_TYPE = "no_header_type"
REASON_NO_DEBUG_TYPE = "no_debug_type"
REASON_NO_DEBUG_INFO = "no_debug_info"
REASON_ODR_CONFLICT = "odr_conflict"

_KIND_RECORD = "record"
_KIND_ENUM = "enum"

_Layout = StructLayout | EnumInfo


@dataclass(frozen=True, slots=True)
class DebugTypeOccurrence:
    """One observed debug-info type definition."""

    kind: str
    name: str
    #: 1 for the definition ``DwarfMetadata.structs``/``enums`` keeps; 2.. for
    #: each further, layout-distinct one (an ODR conflict).
    occurrence: int


@dataclass(frozen=True)
class DebugTypeJoin:
    """The debug-type join of one snapshot."""

    join: CrossLayerJoin
    occurrences: Mapping[str, DebugTypeOccurrence]
    identities: SnapshotIdentities
    #: Whether the debug producer looked for ODR conflicts at all. ``False``
    #: (BTF/CTF/PDB, a pre-v51 snapshot) means a ``matched`` name could still
    #: have had a conflicting definition nobody recorded.
    odr_observed: bool = False

    @property
    def complete(self) -> bool:
        return self.join.complete

    def header(self, node_id: str) -> JoinRecord:
        return self.join.left[node_id]

    def debug_names_joined(self, *states: JoinState) -> frozenset[str]:
        """Debug-type names with at least one occurrence in one of *states*
        (default: matched or ambiguous -- the ones that joined a header type)."""
        wanted = states or (JoinState.MATCHED, JoinState.AMBIGUOUS)
        return frozenset(
            self.occurrences[r.subject].name
            for r in self.join.right.values()
            if r.state in wanted
        )


def _enum_layout_verdict(en: EnumType, info: EnumInfo) -> bool | None:
    compared = False
    for m in en.members:
        if m.name in info.members:
            if info.members[m.name] != m.value:
                return False
            compared = True
    return True if compared else None


def _debug_occurrences(
    dwarf: DwarfMetadata,
) -> Iterable[tuple[str, DebugTypeOccurrence, _Layout]]:
    for kind, first, conflicts in (
        (_KIND_RECORD, dwarf.structs, dwarf.struct_odr_conflicts),
        (_KIND_ENUM, dwarf.enums, dwarf.enum_odr_conflicts),
    ):
        for name, layout in first.items():
            variants = [layout, *conflicts.get(name, ())]
            for n, variant in enumerate(variants, start=1):
                occ = DebugTypeOccurrence(kind, name, n)
                yield debug_type_node_id("debug", kind, name, n), occ, variant


def _candidate_verdicts(
    occ: DebugTypeOccurrence,
    layout: _Layout,
    records: Mapping[str, list[tuple[str, RecordType]]],
    enums: Mapping[str, list[tuple[str, EnumType]]],
) -> Iterable[tuple[str, bool | None]]:
    """``(header node id, layout verdict)`` for every same-kind header entity
    spelled exactly like *occ*."""
    if occ.kind == _KIND_RECORD:
        assert isinstance(layout, StructLayout)
        yield from record_candidates(
            DebugRecordFacts.from_struct_layout(layout), records
        )
    else:
        assert isinstance(layout, EnumInfo)
        for node, en in enums.get(occ.name, ()):
            yield node, _enum_layout_verdict(en, layout)


def join_debug_types(
    snap: AbiSnapshot, identities: SnapshotIdentities | None = None
) -> DebugTypeJoin:
    """Join *snap*'s observed debug types onto its header records/enums.

    A pure function of the snapshot, independent of the order of its
    records."""
    ids = identities if identities is not None else snapshot_identities(snap)
    records: dict[str, list[tuple[str, RecordType]]] = {}
    enums: dict[str, list[tuple[str, EnumType]]] = {}
    left_ids: list[str] = []
    for rec, ident in zip(snap.types, ids.records):
        records.setdefault(header_type_key(rec), []).append((ident.node_id, rec))
        left_ids.append(ident.node_id)
    for en, ident in zip(snap.enums, ids.enums):
        enums.setdefault(header_type_key(en), []).append((ident.node_id, en))
        left_ids.append(ident.node_id)

    dwarf = snap.dwarf
    if dwarf is None or not dwarf.has_dwarf:
        left = {
            node: JoinRecord(node, JoinState.UNKNOWN, reason=REASON_NO_DEBUG_INFO)
            for node in left_ids
        }
        join = CrossLayerJoin(
            DEBUG_TYPE_JOIN,
            complete=False,
            left=left,
            right={},
            incomplete_reason=REASON_NO_DEBUG_INFO,
        )
        return DebugTypeJoin(join, {}, ids, False)

    occurrences: dict[str, DebugTypeOccurrence] = {}
    right_cands: dict[str, set[str]] = {}
    right_rejected: dict[str, set[str]] = {}
    right_reasons: dict[str, str] = {}
    left_cands: dict[str, set[str]] = {node: set() for node in left_ids}
    for occ_id, occ, layout in _debug_occurrences(dwarf):
        occurrences[occ_id] = occ
        cands: set[str] = set()
        rejected: set[str] = set()
        verdicts: list[bool | None] = []
        for node, verdict in _candidate_verdicts(occ, layout, records, enums):
            if verdict is False:
                rejected.add(node)
            else:
                cands.add(node)
                verdicts.append(verdict)
                left_cands[node].add(occ_id)
        right_cands[occ_id] = cands
        if rejected:
            right_rejected[occ_id] = rejected
        if occ.name in (
            dwarf.struct_odr_conflicts
            if occ.kind == _KIND_RECORD
            else dwarf.enum_odr_conflicts
        ):
            right_reasons[occ_id] = REASON_ODR_CONFLICT
        elif cands:
            right_reasons[occ_id] = (
                REASON_LAYOUT_CORROBORATED
                if all(verdicts)
                else REASON_LAYOUT_UNAVAILABLE
            )
        elif rejected:
            right_reasons[occ_id] = REASON_LAYOUT_CONFLICT

    right = resolve_join_records(
        right_cands,
        reasons=right_reasons,
        rejected=right_rejected,
        unmatched_reason=REASON_NO_HEADER_TYPE,
    )
    # A header entity whose name some CU defined differently keeps that on
    # record even when layout singled out one definition.
    odr_nodes = {
        node
        for occ_id, reason in right_reasons.items()
        if reason == REASON_ODR_CONFLICT
        for node in right_cands[occ_id] | right_rejected.get(occ_id, set())
    }
    left = resolve_join_records(
        left_cands,
        reasons=dict.fromkeys(odr_nodes, REASON_ODR_CONFLICT),
        unmatched_reason=REASON_NO_DEBUG_TYPE,
    )
    join = CrossLayerJoin(DEBUG_TYPE_JOIN, complete=True, left=left, right=right)
    return DebugTypeJoin(join, occurrences, ids, dwarf.odr_conflicts_observed)
