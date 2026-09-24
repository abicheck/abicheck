# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""The observed ``exports`` join: L0 export table <-> L2/L1 declarations
(evidence-entity-model plan, Phase 2).

**Sides.** The *left* side is every function/variable entity of the
snapshot, keyed by its invariant-I1 graph node id
(``model.graph_entity_identity.snapshot_identities``) -- so two records of
one entity (a Mach-O ``__Z...`` and ``_Z...`` spelling, a duplicate
declaration) are one subject. The *right* side is every observed
export-table entry, keyed by :func:`~abicheck.model.graph_join.
binary_symbol_node_id`. Export names come from ``model/export_index.py``
only: ELF entries through :func:`~abicheck.model.export_index.
all_export_names` with :func:`~abicheck.model.export_index.
default_versioned_names` marking which of them an unversioned link can
bind to, PE through :func:`~abicheck.model.export_index.
pe_export_ids_with_ordinal_placeholder`, Mach-O through ``all_export_names``
-- never a second read of ``snap.elf``/``.pe``/``.macho``.

**Rule.** A declaration's linker spelling is ``mangled``, or ``name`` when
the producer recorded none (a C symbol, a headerless PE ordinal placeholder).
It joins an export when the observed table *contains* that spelling. On a
Mach-O table only, Phase 1's decoration alias applies: one leading underscore
more or fewer than the trie's already-stripped name (clang keeps the Darwin
underscore; ``strip_macho_itanium_decoration`` is the same fact), refused
whenever another declaration owns the shifted spelling exactly -- the alias
exists for one entity spelled two ways, never to hand one declaration's
export to another. A PE (x86 ``_foo@8``) decoration carries no alias record,
so it does not join.

**States (I2).** An export with no declaration is ``unmatched``; with one
entity, ``matched``; with several, ``ambiguous`` listing them. A
declaration with no export is ``unmatched`` (a public inline function is an
ordinary member of the contract, not a missing export). A declaration
joining several entries is ``ambiguous`` only when two of them are in the
same table; one entry per table (``foo`` in ELF, ``foo`` in Mach-O) is one
entity observed twice. A declaration whose export another entity also claims
carries ``reason="export_contested"``. No export table at all makes the
join incomplete and every declaration ``unknown``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..model.export_index import (
    RawExportIndex,
    all_export_names,
    build_raw_export_indexes,
    default_versioned_names,
    pe_export_ids_with_ordinal_placeholder,
)
from ..model.graph_entity_identity import SnapshotIdentities, snapshot_identities
from ..model.graph_join import (
    EXPORT_JOIN,
    CrossLayerJoin,
    JoinRecord,
    JoinState,
    binary_symbol_node_id,
    resolve_join_records,
)

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "REASON_EXPORT_CONTESTED",
    "REASON_NO_DECLARATION",
    "REASON_NO_EXPORT",
    "REASON_NO_EXPORT_TABLE",
    "ExportEntry",
    "ExportJoin",
    "export_tables",
    "join_exports",
]

REASON_NO_EXPORT = "no_export"
REASON_NO_DECLARATION = "no_declaration"
REASON_EXPORT_CONTESTED = "export_contested"
REASON_NO_EXPORT_TABLE = "no_export_table"


@dataclass(frozen=True, slots=True)
class ExportEntry:
    """One observed export-table entry, as the join saw it."""

    platform: str
    spelling: str
    #: In :func:`~abicheck.model.export_index.default_versioned_names` --
    #: an unversioned link can bind to it. ``False`` for an ELF symbol that
    #: exists only as a non-default version (``foo@LIB_1``) and for a PE
    #: ordinal-only export.
    default_version: bool


def _table_entries(index: RawExportIndex) -> dict[str, bool]:
    """``spelling -> default_version`` for one table, from the shared
    projections only."""
    default = default_versioned_names(index)
    names = (
        pe_export_ids_with_ordinal_placeholder(index)
        if index.platform == "pe"
        else all_export_names(index)
    )
    return {name: name in default for name in names}


def export_tables(snap: AbiSnapshot) -> dict[str, dict[str, bool]]:
    """``platform -> {spelling: default_version}`` for every non-empty table
    *snap* carries (``{}`` when it carries none)."""
    out: dict[str, dict[str, bool]] = {}
    for index in build_raw_export_indexes(snap):
        entries = _table_entries(index)
        if entries:
            out.setdefault(index.platform, {}).update(entries)
    return out


@dataclass(frozen=True)
class ExportJoin:
    """The ``exports`` join of one snapshot."""

    join: CrossLayerJoin
    #: Export node id -> the entry it stands for.
    entries: Mapping[str, ExportEntry]
    #: Parallel to ``snap.functions``/``snap.variables``.
    identities: SnapshotIdentities
    #: Whether any table was captured at all (an empty one included).
    table_captured: bool = False
    _by_platform: dict[str, set[str]] = field(default_factory=dict, repr=False)

    @property
    def complete(self) -> bool:
        return self.join.complete

    def declaration(self, node_id: str) -> JoinRecord:
        return self.join.left[node_id]

    def export(self, platform: str, spelling: str) -> JoinRecord | None:
        return self.join.right.get(binary_symbol_node_id(platform, spelling))

    def exports_of(self, node_id: str) -> tuple[ExportEntry, ...]:
        """Every entry *node_id*'s declaration joined, matched or ambiguous."""
        rec = self.join.left.get(node_id)
        if rec is None:
            return ()
        return tuple(self.entries[c] for c in rec.candidates)

    def tables(self) -> dict[str, set[str]]:
        """``platform -> spellings`` over the join's whole export domain."""
        return {p: set(s) for p, s in self._by_platform.items()}

    def entries_in_state(self, *states: JoinState) -> tuple[ExportEntry, ...]:
        return tuple(
            self.entries[r.subject]
            for r in self.join.right.values()
            if r.state in states
        )


def _declaration_spellings(
    snap: AbiSnapshot, ids: SnapshotIdentities
) -> dict[str, set[str]]:
    """Entity node id -> every linker spelling its records carry."""
    out: dict[str, set[str]] = {}
    pairs: Iterable[tuple[str, str, str]] = (
        *(
            (f.mangled, f.name, i.node_id)
            for f, i in zip(snap.functions, ids.functions)
        ),
        *(
            (v.mangled, v.name, i.node_id)
            for v, i in zip(snap.variables, ids.variables)
        ),
    )
    for mangled, name, node_id in pairs:
        spellings = out.setdefault(node_id, set())
        if spelling := mangled or name:
            spellings.add(spelling)
    return out


def _macho_shifted(spelling: str) -> tuple[str, ...]:
    """One leading underscore fewer and one more -- the Mach-O decoration
    alias Phase 1 records (see this module's docstring)."""
    shorter = spelling[1:] if spelling.startswith("_") else ""
    return tuple(c for c in (shorter, "_" + spelling) if c)


def join_exports(
    snap: AbiSnapshot, identities: SnapshotIdentities | None = None
) -> ExportJoin:
    """Join *snap*'s observed export tables onto its declarations.

    *identities* may be passed when the caller already built the snapshot's
    identity table; it is otherwise computed here. The result is a pure
    function of the snapshot and independent of the order of its records.
    """
    ids = identities if identities is not None else snapshot_identities(snap)
    spellings = _declaration_spellings(snap, ids)
    captured = bool(build_raw_export_indexes(snap))
    tables = export_tables(snap)
    by_platform = {p: set(t) for p, t in tables.items()}
    if not captured:
        left = {
            node: JoinRecord(node, JoinState.UNKNOWN, reason=REASON_NO_EXPORT_TABLE)
            for node in spellings
        }
        join = CrossLayerJoin(
            EXPORT_JOIN,
            complete=False,
            left=left,
            right={},
            incomplete_reason=REASON_NO_EXPORT_TABLE,
        )
        return ExportJoin(join, {}, ids, False, by_platform)

    entries = {
        binary_symbol_node_id(p, s): ExportEntry(p, s, default)
        for p, table in tables.items()
        for s, default in table.items()
    }
    exact_owners = {
        s
        for node_spellings in spellings.values()
        for s in node_spellings
        if any(s in names for names in by_platform.values())
    }
    left_cands: dict[str, set[str]] = {}
    right_cands: dict[str, set[str]] = {eid: set() for eid in entries}
    for node, node_spellings in spellings.items():
        cands = left_cands.setdefault(node, set())
        for s in node_spellings:
            for platform, names in by_platform.items():
                if s in names:
                    cands.add(binary_symbol_node_id(platform, s))
                if platform == "macho":
                    cands.update(
                        binary_symbol_node_id(platform, c)
                        for c in _macho_shifted(s)
                        if c in names and c not in exact_owners
                    )
        for eid in cands:
            right_cands[eid].add(node)

    contested = {
        node
        for node, cands in left_cands.items()
        if any(len(right_cands[e]) > 1 for e in cands)
    }

    def _same_table_twice(cands: set[str]) -> bool:
        platforms = [entries[c].platform for c in cands]
        return len(platforms) != len(set(platforms))

    left = resolve_join_records(
        left_cands,
        reasons=dict.fromkeys(contested, REASON_EXPORT_CONTESTED),
        unmatched_reason=REASON_NO_EXPORT,
        competing=_same_table_twice,
    )
    right = resolve_join_records(right_cands, unmatched_reason=REASON_NO_DECLARATION)
    join = CrossLayerJoin(EXPORT_JOIN, complete=True, left=left, right=right)
    return ExportJoin(join, entries, ids, True, by_platform)
