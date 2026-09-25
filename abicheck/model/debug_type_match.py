# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""The one header-record <-> debug-record matching rule (evidence-entity-model
plan, Phase 2).

Two consumers relate a header-parsed ``RecordType`` to a debug-info record
definition: the snapshot-level L1 debug-type join
(``compare/debug_type_join.py``), which reports the relation, and the
dump-time layout backfill (``dumper_layout_backfill.py``), which copies a
layout-blind header backend's missing size/offsets from the debug record it
matched. Both must agree on *which* debug record a header record is, so the
rule lives here, in ``model`` -- the one layer both an ``extract``-side
dump step (no snapshot exists yet) and a ``compare``-side join can import.

**Rule.** A debug record's candidates are the header records whose
qualified spelling (:func:`header_type_key`: ``qualified_name or name``)
*equals* the debug name, and whose layout does not contradict it on any
fact both sides carry (:func:`record_layout_verdict`: union-ness, total
size, the offset of every non-bitfield field both name, and the direct
non-virtual/virtual base lists when both producers established them). No other spelling
tolerance applies -- no bare-name or last-``::``-segment fallback: two
records sharing a leaf name in different scopes are different entities, and
castxml's dropped inline-namespace segment stays a non-match (G15).

**One-to-one (I2).** :func:`match_header_records` resolves both sides with
:func:`~abicheck.model.graph_join.resolve_join_records`; a header record is
paired with a debug record only when each is the other's *sole* surviving
candidate. Two header records sharing a spelling, or two debug definitions
of one name, are ``ambiguous`` -- never resolved by picking one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from .availability import FactStatus
from .graph_join import JoinState, resolve_join_records

if TYPE_CHECKING:
    from .dwarf_facts import StructLayout
    from .entities import EnumType, RecordType
    from .fact import Fact

__all__ = [
    "DebugRecordFacts",
    "HeaderRecordMatch",
    "header_type_key",
    "match_header_records",
    "record_candidates",
    "record_layout_verdict",
]


@dataclass(frozen=True, slots=True)
class DebugRecordFacts:
    """The layout facts of one debug-info record definition the matcher
    compares, independent of the producer's own shape (``StructLayout`` from
    ``DwarfMetadata``, or a DWARF-built ``RecordType``)."""

    name: str
    is_union: bool
    #: ``None`` when the producer recorded no (non-zero) size.
    size_bits: int | None
    #: Offset of every named, non-bitfield field.
    field_offsets_bits: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    #: Direct non-virtual / virtual base names, ``None`` unless the producer
    #: established them (``Fact`` status ``PRESENT``) -- an unknown base list
    #: is never compared.
    base_names: tuple[str, ...] | None = None
    virtual_base_names: tuple[str, ...] | None = None

    @classmethod
    def from_struct_layout(cls, layout: StructLayout) -> DebugRecordFacts:
        return cls(
            name=layout.name,
            is_union=layout.is_union,
            size_bits=layout.byte_size * 8 if layout.byte_size else None,
            field_offsets_bits=MappingProxyType(
                {
                    f.name: f.byte_offset * 8
                    for f in layout.fields
                    if f.name and not f.bit_size
                }
            ),
        )

    @classmethod
    def from_record_type(cls, rec: RecordType) -> DebugRecordFacts:
        return cls(
            name=rec.name,
            is_union=rec.is_union,
            size_bits=rec.size_bits or None,
            field_offsets_bits=MappingProxyType(
                {
                    f.name: f.offset_bits
                    for f in rec.fields
                    if f.name and not f.is_bitfield and f.offset_bits is not None
                }
            ),
            base_names=_known_bases(rec.bases_fact),
            virtual_base_names=_known_bases(rec.virtual_bases_fact),
        )


def _known_bases(fact: Fact[list[str]] | None) -> tuple[str, ...] | None:
    if fact is None or fact.status is not FactStatus.PRESENT or fact.value is None:
        return None
    return tuple(fact.value)


def _base_segments(name: str) -> list[str]:
    spelled = " ".join(name.split())
    for prefix in ("struct ", "class ", "union "):
        if spelled.startswith(prefix):
            spelled = spelled[len(prefix) :]
    return spelled.lstrip(":").split("::")


def _same_base(a: str, b: str) -> bool:
    """Whether two producers' spellings can name one base: equal on the
    shorter spelling's complete ``::`` segments (``ns::B`` vs ``B``), so a
    qualification difference is not a contradiction while ``a::B`` vs
    ``b::B`` or ``B`` vs ``XB`` is."""
    sa, sb = _base_segments(a), _base_segments(b)
    k = min(len(sa), len(sb))
    return sa[-k:] == sb[-k:]


def _bases_verdict(
    header: tuple[str, ...] | None, debug: tuple[str, ...] | None
) -> bool | None:
    """``False`` when two known base lists cannot name the same classes (a
    different count, or a base with no compatible spelling on the other
    side), ``True`` when they can, ``None`` when either is unknown.

    A base whose spelling is a template/alias the other producer resolved
    differently is indistinguishable here from a real difference only when
    no partner spelling survives; a template-id spelling (``<``) is treated
    as unresolvable and never contradicts."""
    if header is None or debug is None:
        return None
    if len(header) != len(debug):
        return False
    remaining = list(debug)
    for h in header:
        hit = next((d for d in remaining if _same_base(h, d)), None)
        if hit is None:
            if "<" in h or any("<" in d for d in remaining):
                return None
            return False
        remaining.remove(hit)
    return True


def header_type_key(entity: RecordType | EnumType) -> str:
    """The spelling a header record/enum is joined on."""
    return entity.qualified_name or entity.name


def record_layout_verdict(rec: RecordType, debug: DebugRecordFacts) -> bool | None:
    """``False`` on a contradiction, ``True`` when at least one layout fact
    was compared and agreed, ``None`` when no fact was comparable."""
    if rec.is_union != debug.is_union:
        return False
    compared = False
    if rec.size_bits is not None and debug.size_bits is not None:
        if rec.size_bits != debug.size_bits:
            return False
        compared = True
    for header_bases, debug_bases in (
        (_known_bases(rec.bases_fact), debug.base_names),
        (_known_bases(rec.virtual_bases_fact), debug.virtual_base_names),
    ):
        base_verdict = _bases_verdict(header_bases, debug_bases)
        if base_verdict is False:
            return False
        compared = compared or base_verdict is True
    for f in rec.fields:
        if (
            f.is_bitfield
            or f.offset_bits is None
            or f.name not in debug.field_offsets_bits
        ):
            continue
        if f.offset_bits != debug.field_offsets_bits[f.name]:
            return False
        compared = True
    return True if compared else None


def record_candidates(
    debug: DebugRecordFacts,
    header_index: Mapping[str, Sequence[tuple[str, RecordType]]],
) -> Iterable[tuple[str, bool | None]]:
    """``(header id, layout verdict)`` for every header record in
    *header_index* (keyed by :func:`header_type_key`) spelled exactly like
    *debug*."""
    for node, rec in header_index.get(debug.name, ()):
        yield node, record_layout_verdict(rec, debug)


@dataclass(frozen=True, slots=True)
class HeaderRecordMatch:
    """How one header record resolved against the debug records."""

    state: JoinState
    #: Index into the debug sequence, set only when ``state`` is ``MATCHED``.
    debug_index: int | None = None
    #: Whether a same-spelled debug record was rejected on layout.
    layout_conflict: bool = False


def match_header_records(
    header: Sequence[RecordType], debug: Sequence[DebugRecordFacts]
) -> list[HeaderRecordMatch]:
    """Resolve every header record against *debug* under the module rule;
    one result per header record, in order. ``MATCHED`` only when the pair
    is mutually unique (see the module docstring)."""
    index: dict[str, list[tuple[str, RecordType]]] = {}
    for i, header_rec in enumerate(header):
        index.setdefault(header_type_key(header_rec), []).append((str(i), header_rec))
    left: dict[str, set[str]] = {str(i): set() for i in range(len(header))}
    right: dict[str, set[str]] = {}
    conflicted: set[str] = set()
    for j, facts in enumerate(debug):
        occ = str(j)
        right[occ] = set()
        for node, verdict in record_candidates(facts, index):
            if verdict is False:
                conflicted.add(node)
            else:
                right[occ].add(node)
                left[node].add(occ)
    left_records = resolve_join_records(left, unmatched_reason="")
    right_records = resolve_join_records(right, unmatched_reason="")
    out: list[HeaderRecordMatch] = []
    for i in range(len(header)):
        rec = left_records[str(i)]
        if rec.state is JoinState.MATCHED:
            (occ,) = rec.candidates
            if right_records[occ].state is JoinState.MATCHED:
                out.append(HeaderRecordMatch(JoinState.MATCHED, int(occ)))
            else:
                out.append(HeaderRecordMatch(JoinState.AMBIGUOUS))
        else:
            out.append(
                HeaderRecordMatch(rec.state, layout_conflict=str(i) in conflicted)
            )
    return out
