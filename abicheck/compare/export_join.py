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
export to another. On a PE table, Phase 1's x86 calling-convention alias
applies the same way (:func:`~abicheck.model.graph_entity_identity.
pe_c_decoration_base`): an export ``_foo@8``/``@foo@8``/``foo@@8``/``_foo``
joins the declaration spelled ``foo`` -- only on the machine types that
decorate (32-bit x86; x64 for ``__vectorcall`` only), never for a
C++-mangled name, never on an unknown machine, and refused whenever another
declaration owns the decorated spelling exactly.

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
from ..model.graph_entity_identity import (
    GraphEntityIdentity,
    SnapshotIdentities,
    endpoint_key,
    pe_c_decoration_base,
)
from ..model.graph_join import (
    EXPORT_JOIN,
    CrossLayerJoin,
    JoinRecord,
    JoinState,
    binary_symbol_node_id,
    resolve_join_records,
)
from ..model.mangled_name import itanium_ctor_dtor_marker_span
from ..model.snapshot_identity_table import identities_for_snapshot

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
    for node_id, variants in _variant_spellings(ids).items():
        out.setdefault(node_id, set()).update(variants)
    return out


def _variant_spellings(ids: SnapshotIdentities) -> dict[str, set[str]]:
    """Constructor/destructor node id -> its variant family's spellings (its
    own key plus every alias, ``C1``/``C2``, ``D0``/``D1``/``D2`` --
    :mod:`abicheck.model.special_member_identity`). These are one entity's
    several exports, so matching more than one of them in one table is not a
    competition (:func:`join_exports`). Any other alias (a Mach-O
    decoration) keeps the ordinary one-export-per-table rule."""
    out: dict[str, set[str]] = {}
    for ident in ids.functions:
        if not (ident.resolved and ident.aliases):
            continue
        key = endpoint_key(ident)
        if itanium_ctor_dtor_marker_span(key) is None:
            continue
        out[ident.node_id] = {
            key,
            *(endpoint_key(GraphEntityIdentity(a, ident.state)) for a in ident.aliases),
        }
    return out


def _macho_shifted(spelling: str) -> tuple[str, ...]:
    """One leading underscore fewer and one more -- the Mach-O decoration
    alias Phase 1 records (see this module's docstring)."""
    shorter = spelling[1:] if spelling.startswith("_") else ""
    return tuple(c for c in (shorter, "_" + spelling) if c)


def _pe_decorated_by_base(snap: AbiSnapshot, names: set[str]) -> dict[str, set[str]]:
    """Undecorated C name -> the PE export spellings decorating it, on the
    machine *snap*'s PE header records (``{}`` with none or an unknown one)."""
    machine = snap.pe.machine if snap.pe is not None else ""
    out: dict[str, set[str]] = {}
    for name in names:
        if base := pe_c_decoration_base(name, machine):
            out.setdefault(base, set()).add(name)
    return out


def join_exports(
    snap: AbiSnapshot, identities: SnapshotIdentities | None = None
) -> ExportJoin:
    """Join *snap*'s observed export tables onto its declarations.

    *identities* may be passed when the caller already built the snapshot's
    identity table; it is otherwise computed here. The result is a pure
    function of the snapshot and independent of the order of its records.
    """
    ids = identities if identities is not None else identities_for_snapshot(snap)
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
    pe_by_base = _pe_decorated_by_base(snap, by_platform.get("pe", set()))
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
                if platform == "pe":
                    cands.update(
                        binary_symbol_node_id(platform, c)
                        for c in pe_by_base.get(s, ())
                        if c not in exact_owners
                    )
        for eid in cands:
            right_cands[eid].add(node)

    contested = {
        node
        for node, cands in left_cands.items()
        if any(len(right_cands[e]) > 1 for e in cands)
    }

    proven = {
        binary_symbol_node_id(platform, v)
        for variants in _variant_spellings(ids).values()
        for v in variants
        for platform, names in by_platform.items()
        if v in names
    }

    def _same_table_twice(cands: set[str]) -> bool:
        """Two entries of one table compete -- unless every entry sharing a
        table is a proven spelling of the entity itself (``C1`` and ``C2``
        of one constructor)."""
        per_platform: dict[str, list[str]] = {}
        for c in cands:
            per_platform.setdefault(entries[c].platform, []).append(c)
        return any(
            len(group) > 1 and not all(c in proven for c in group)
            for group in per_platform.values()
        )

    left = resolve_join_records(
        left_cands,
        reasons=dict.fromkeys(contested, REASON_EXPORT_CONTESTED),
        unmatched_reason=REASON_NO_EXPORT,
        competing=_same_table_twice,
    )
    right = resolve_join_records(right_cands, unmatched_reason=REASON_NO_DECLARATION)
    join = CrossLayerJoin(EXPORT_JOIN, complete=True, left=left, right=right)
    return ExportJoin(join, entries, ids, True, by_platform)
