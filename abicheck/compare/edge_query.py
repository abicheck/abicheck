# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""Coverage-aware edge queries (evidence-entity-model plan, Phase 4, I4).

:meth:`EdgeEvidence.query` answers "does *subject* have an edge of kind *K*
(to *target*) in scope *S*?" with an
:class:`~abicheck.model.edge_coverage.EdgeAnswer` -- ``present``,
``proven_absent`` or ``unknown`` -- and the coverage records the answer
rests on. It is the one place an edge's *absence* becomes a conclusion:
``proven_absent`` needs a producer of *K* whose coverage includes every
queried unit (:func:`decide`), so an extractor that never ran, ran over part
of *S*, or failed can only ever yield ``unknown``.

**Edge kinds.** Every kind in ``compare.surface_graph.EDGE_EVIDENCE_CLASS``
and the L5 source-graph kinds (``model.source_graph_coverage.L5_EDGE_KINDS``):

======================== ================= =================================
kind                     class             producer the absence rests on
======================== ================= =================================
``exports``              resolved_join     every export table the snapshot
                                           owes (declaration subject); the
                                           header AST (export subject)
``debug_type_of``        resolved_join     the debug section (header type
                                           subject); the header AST (debug
                                           occurrence subject)
``declares``             observed          the header AST
``references``           resolved_join     the header AST, and an
                                           unambiguous type spelling index
``declares_linker_name`` derived           the snapshot records it projects
L5 kinds                 observed          the source-graph pass flags
======================== ================= =================================

A subject matches either endpoint of an edge; *target*, when given, must be
the other one. Join and ``derived`` edges are recomputed from the snapshot's
records, never read from a persisted graph (I3).

**Scope units.** ``exports``: an export-table platform (``elf``/``pe``/
``macho``) for a declaration subject; ``headers`` or ``dependency_headers``
(toolchain/system headers, which a default dump filters out) for an export
subject. ``debug_type_of``: ``debug``; ``headers`` for an occurrence subject.
``declares``/``references``: ``headers`` (default) or
``dependency_headers``. L5: compile-unit paths, with
:data:`~abicheck.model.edge_coverage.ALL_UNITS` for the whole project.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING

from ..model.edge_coverage import (
    ALL_UNITS,
    REASON_EDGE_OBSERVED,
    REASON_NO_PRODUCER,
    REASON_PRODUCER_COVERED_SCOPE,
    REASON_SCOPE_NOT_COVERED,
    REASON_SUBJECT_UNKNOWN,
    CoverageRecord,
    EdgeAnswer,
    EdgeQuery,
    EdgeQueryResult,
    ProducerRun,
)
from ..model.export_index import build_raw_export_indexes
from ..model.graph_join import (
    BINARY_SYMBOL_PREFIX,
    DEBUG_TYPE_PREFIX,
    EDGE_KIND_DEBUG_TYPE_OF,
    EDGE_KIND_EXPORTS,
    JoinRecord,
    JoinState,
)
from ..model.source_graph_coverage import L5_EDGE_KINDS, pass_coverage_records
from .debug_type_join import DebugTypeJoin, join_debug_types
from .export_join import ExportJoin, join_exports
from .surface_graph import (
    EDGE_EVIDENCE_CLASS,
    EDGE_KIND_DECLARES,
    EDGE_KIND_DECLARES_LINKER_NAME,
    EDGE_KIND_REFERENCES,
    ReferencedIdentifiers,
    referenced_identifiers_by_node,
    type_spelling_index,
)

if TYPE_CHECKING:
    from ..model.graph_entity_identity import SnapshotIdentities
    from ..model.snapshot import AbiSnapshot
    from ..model.source_graph import SourceGraphSummary

__all__ = [
    "PRODUCER_DEBUG_SECTION",
    "PRODUCER_EXPORT_TABLE",
    "PRODUCER_HEADER_AST",
    "PRODUCER_SNAPSHOT_RECORDS",
    "PRODUCER_TYPE_SPELLINGS",
    "QUERYABLE_EDGE_KINDS",
    "UNIT_DEBUG",
    "UNIT_DEPENDENCY_HEADERS",
    "UNIT_HEADERS",
    "UNIT_RECORDS",
    "EdgeEvidence",
    "decide",
    "debug_coverage_record",
    "export_coverage_records",
    "export_table_covered",
    "header_coverage_record",
    "is_toolchain_symbol",
]

PRODUCER_EXPORT_TABLE = "export_table"
PRODUCER_DEBUG_SECTION = "debug_section"
PRODUCER_HEADER_AST = "header_ast"
PRODUCER_TYPE_SPELLINGS = "type_spelling_index"
PRODUCER_SNAPSHOT_RECORDS = "snapshot_records"

UNIT_HEADERS = "headers"
UNIT_DEPENDENCY_HEADERS = "dependency_headers"
UNIT_DEBUG = "debug"
UNIT_RECORDS = "records"

_PLATFORMS = ("elf", "pe", "macho")

#: Every edge kind :meth:`EdgeEvidence.query` answers.
QUERYABLE_EDGE_KINDS: frozenset[str] = frozenset(EDGE_EVIDENCE_CLASS) | L5_EDGE_KINDS

#: Demangled prefixes of toolchain/runtime entities, whose declarations a
#: default (``dependency_scope="filtered"``) dump drops with the system
#: headers that declare them.
_TOOLCHAIN_PREFIXES = ("std::", "__gnu_cxx::", "__cxxabiv1::", "__gnu_debug::")


def decide(
    observed: bool,
    records: Iterable[CoverageRecord],
    scope: frozenset[str] | None,
) -> tuple[EdgeAnswer, str]:
    """I4's rule. An observed edge is ``present`` whatever the coverage. With
    none, ``proven_absent`` needs every queried unit -- *scope*, or every
    unit some record is responsible for -- inside the union of what the
    records covered; otherwise ``unknown``."""
    if observed:
        return EdgeAnswer.PRESENT, REASON_EDGE_OBSERVED
    records = tuple(records)
    if not records:
        return EdgeAnswer.UNKNOWN, REASON_NO_PRODUCER
    wanted = (
        scope if scope is not None else frozenset().union(*(r.units for r in records))
    )
    covered = frozenset().union(*(r.covered_units() for r in records))
    if wanted and (ALL_UNITS in covered or wanted <= covered):
        return EdgeAnswer.PROVEN_ABSENT, REASON_PRODUCER_COVERED_SCOPE
    return EdgeAnswer.UNKNOWN, REASON_SCOPE_NOT_COVERED


# ---------------------------------------------------------------------------
# Producer coverage, read off the snapshot
# ---------------------------------------------------------------------------


def _table_read(meta: object) -> bool:
    """Whether a platform block records that its binary was actually parsed.

    A symbol list alone cannot say so: a default, header-only or
    parse-failed ``ElfMetadata()`` and a library exporting nothing both
    leave it empty. The header fields a real parse always sets are the
    repository's existing capture signal (``compare/undeclared_exports.py``,
    ``diff_platform_elf_dynamic``): ELF/PE ``machine``, Mach-O ``filetype``
    or ``cpu_type``."""
    return any(getattr(meta, attr, "") for attr in ("machine", "filetype", "cpu_type"))


def export_coverage_records(snap: AbiSnapshot) -> tuple[CoverageRecord, ...]:
    """One record per export table *snap* owes: every table it carries, plus
    the one its own ``platform`` names.

    A carried table is ``ran`` when it holds an entry or its header fields
    show the binary was parsed (:func:`_table_read`) -- a parsed library
    that exports nothing is a real, covered empty table. A carried block
    with neither is ``failed``: a default or parse-failed platform block,
    which reading as "exports nothing" would prove every declaration
    unexported. A table owed but not carried is ``not_run``; no table owed
    at all is one ``not_run`` record."""
    metas = {"elf": snap.elf, "pe": snap.pe, "macho": snap.macho}
    carried: dict[str, bool] = {}
    for index in build_raw_export_indexes(snap):
        read = bool(index.entries) or _table_read(metas[index.platform])
        carried[index.platform] = carried.get(index.platform, False) or read
    owed = set(carried)
    if snap.platform in _PLATFORMS:
        owed.add(snap.platform)
    if not owed:
        reason = "header_only_no_binary" if snap.header_only else "no_export_table"
        return (
            CoverageRecord(
                EDGE_KIND_EXPORTS, PRODUCER_EXPORT_TABLE, ProducerRun.NOT_RUN,
                frozenset({"export_table"}), reason=reason, source="AbiSnapshot",
            ),
        )  # fmt: skip
    out: list[CoverageRecord] = []
    for platform in sorted(owed):
        if platform not in carried:
            run, reason = ProducerRun.NOT_RUN, "no_export_table"
        elif not carried[platform]:
            run, reason = ProducerRun.FAILED, "export_table_not_read"
        else:
            run, reason = ProducerRun.RAN, ""
        out.append(
            CoverageRecord(
                EDGE_KIND_EXPORTS,
                PRODUCER_EXPORT_TABLE,
                run,
                frozenset({platform}),
                reason=reason,
                source=f"AbiSnapshot.{platform}",
            )  # fmt: skip
        )
    return tuple(out)


def export_table_covered(snap: AbiSnapshot, platform: str) -> bool:
    """Whether *snap*'s *platform* export table was read, so a spelling
    missing from it is proven absent (I4). The predicate every
    "not in the export table" reader asks before concluding absence."""
    return any(
        r.run is ProducerRun.RAN and platform in r.units
        for r in export_coverage_records(snap)
    )


def debug_coverage_record(snap: AbiSnapshot) -> CoverageRecord:
    """The debug section: ``ran`` when *snap* carries debug info (DWARF, or
    BTF/CTF/PDB reduced to it), ``not_run`` otherwise -- a stripped binary
    never proves a debug type absent."""
    ran = snap.dwarf is not None and snap.dwarf.has_dwarf
    return CoverageRecord(
        EDGE_KIND_DEBUG_TYPE_OF,
        PRODUCER_DEBUG_SECTION,
        ProducerRun.RAN if ran else ProducerRun.NOT_RUN,
        frozenset({UNIT_DEBUG}),
        reason="" if ran else "no_debug_info",
        source="AbiSnapshot.dwarf",
    )


def header_coverage_record(snap: AbiSnapshot, edge_kind: str) -> CoverageRecord:
    """The header AST, as the producer of the declaration side.

    ``ran`` over both the library's own and the dependency headers only when
    *snap* was parsed from headers keeping system declarations
    (``dependency_scope="full"``); ``partial`` over the library's own
    headers when they were filtered out (the default) or the scope was not
    recorded; ``not_run`` when *snap* has no header AST at all (a binary- or
    DWARF-only dump), or when ``from_headers`` was only inferred for a legacy
    snapshot."""
    units = frozenset({UNIT_HEADERS, UNIT_DEPENDENCY_HEADERS})
    source = "AbiSnapshot.from_headers"
    if not snap.from_headers or snap.from_headers_inferred:
        reason = "from_headers_inferred" if snap.from_headers else "no_header_ast"
        return CoverageRecord(
            edge_kind, PRODUCER_HEADER_AST, ProducerRun.NOT_RUN, units,
            reason=reason, source=source,
        )  # fmt: skip
    if snap.dependency_scope == "full":
        return CoverageRecord(
            edge_kind, PRODUCER_HEADER_AST, ProducerRun.RAN, units, source=source
        )
    return CoverageRecord(
        edge_kind, PRODUCER_HEADER_AST, ProducerRun.PARTIAL, units,
        covered=frozenset({UNIT_HEADERS}),
        reason="dependency_headers_excluded", source="AbiSnapshot.dependency_scope",
    )  # fmt: skip


def is_toolchain_symbol(spelling: str) -> bool:
    """Whether the export *spelling* names a toolchain/runtime entity."""
    from ..demangle import demangle

    text = demangle(spelling) or spelling
    return text.startswith(_TOOLCHAIN_PREFIXES) or spelling.startswith(
        ("_ZNSt", "_ZSt", "_ZNKSt", "_ZN9__gnu_cxx", "_ZN10__cxxabiv1")
    )


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------


def _join_observed(rec: JoinRecord | None, target: str | None) -> bool:
    if rec is None or rec.state not in (JoinState.MATCHED, JoinState.AMBIGUOUS):
        return False
    return target is None or target in rec.candidates


@dataclass
class EdgeEvidence:
    """Every edge query over one snapshot (and, for L5 kinds, its source
    graph). Joins and projections are computed once, on first use."""

    snap: AbiSnapshot
    source_graph: SourceGraphSummary | None = None
    identities: SnapshotIdentities | None = None
    _surface_edges: dict[str, dict[str, set[tuple[str, str]]]] | None = field(
        default=None, repr=False
    )

    # -- lazily computed evidence ------------------------------------------

    @cached_property
    def exports(self) -> ExportJoin:
        return join_exports(self.snap, self.identities)

    @cached_property
    def debug_types(self) -> DebugTypeJoin:
        return join_debug_types(self.snap, self.identities)

    @cached_property
    def export_records(self) -> tuple[CoverageRecord, ...]:
        return export_coverage_records(self.snap)

    @cached_property
    def _refs(self) -> ReferencedIdentifiers:
        return referenced_identifiers_by_node(self.snap)

    @cached_property
    def _entity_nodes(self) -> dict[str, str]:
        """Node id -> ``function``/``variable``/``record``/``enum``/``typedef``."""
        ids = self._refs.ids
        out: dict[str, str] = {}
        for kind, idents in (
            ("function", ids.functions),
            ("variable", ids.variables),
            ("record", ids.records),
            ("enum", ids.enums),
            ("typedef", tuple(ids.typedefs.values())),
        ):
            for ident in idents:
                out.setdefault(ident.node_id, kind)
        return out

    @cached_property
    def _projection(self) -> dict[str, set[tuple[str, str]]]:
        """``edge kind -> {(src, dst)}`` of the public-surface builder's own
        ``declares``/``references``/``declares_linker_name`` edges, recomputed
        from the snapshot (never read from a persisted graph)."""
        from ..model.source_graph import SourceGraphSummary
        from .surface_graph import build_public_surface_facts

        graph = SourceGraphSummary()
        build_public_surface_facts(self.snap, graph)
        wanted = {
            EDGE_KIND_DECLARES,
            EDGE_KIND_REFERENCES,
            EDGE_KIND_DECLARES_LINKER_NAME,
        }
        out: dict[str, set[tuple[str, str]]] = {k: set() for k in wanted}
        for e in graph.edges:
            if e.kind in wanted:
                out[e.kind].add((e.src, e.dst))
        return out

    @cached_property
    def _ambiguous_spellings(self) -> dict[str, frozenset[str]]:
        """Type spelling -> every node id it could name, for spellings the
        reference index drops as ambiguous."""
        return type_spelling_index(self.snap, self._refs.ids).ambiguous

    # -- public API ----------------------------------------------------------

    def coverage_records(self, edge_kind: str) -> tuple[CoverageRecord, ...]:
        """Every producer record for *edge_kind*, whatever the subject."""
        if edge_kind == EDGE_KIND_EXPORTS:
            return (
                *self.export_records,
                header_coverage_record(self.snap, edge_kind),
            )
        if edge_kind == EDGE_KIND_DEBUG_TYPE_OF:
            return (
                debug_coverage_record(self.snap),
                header_coverage_record(self.snap, edge_kind),
            )
        if edge_kind in (EDGE_KIND_DECLARES, EDGE_KIND_REFERENCES):
            return (header_coverage_record(self.snap, edge_kind),)
        if edge_kind == EDGE_KIND_DECLARES_LINKER_NAME:
            return (self._records_record(),)
        if edge_kind in L5_EDGE_KINDS:
            if self.source_graph is None:
                return (_no_source_graph(edge_kind),)
            return pass_coverage_records(self.source_graph, edge_kind)
        raise KeyError(f"not a queryable edge kind: {edge_kind!r}")

    def query(
        self,
        edge_kind: str,
        subject: str,
        *,
        target: str | None = None,
        scope: frozenset[str] | None = None,
    ) -> EdgeQueryResult:
        """I4's query: ``present``, ``proven_absent`` or ``unknown``, and the
        coverage records the answer rests on."""
        if edge_kind not in QUERYABLE_EDGE_KINDS:
            raise KeyError(f"not a queryable edge kind: {edge_kind!r}")
        query = EdgeQuery(edge_kind, subject, target, scope)
        resolved = self._resolve(edge_kind, subject, target)
        if resolved is None:
            return EdgeQueryResult(
                query,
                EdgeAnswer.UNKNOWN,
                self.coverage_records(edge_kind),
                REASON_SUBJECT_UNKNOWN,
            )
        observed, records, default_scope = resolved
        effective = scope if scope is not None else default_scope
        answer, reason = decide(observed, records, effective)
        return EdgeQueryResult(query, answer, records, reason)

    # -- per-kind resolution -------------------------------------------------

    def _resolve(
        self, edge_kind: str, subject: str, target: str | None
    ) -> tuple[bool, tuple[CoverageRecord, ...], frozenset[str] | None] | None:
        """``(observed, records, default scope)``, or ``None`` when *subject*
        is not an entity the evidence knows about."""
        if edge_kind == EDGE_KIND_EXPORTS:
            return self._resolve_exports(subject, target)
        if edge_kind == EDGE_KIND_DEBUG_TYPE_OF:
            return self._resolve_debug(subject, target)
        if edge_kind in (EDGE_KIND_DECLARES, EDGE_KIND_REFERENCES):
            return self._resolve_header_edge(edge_kind, subject, target)
        if edge_kind == EDGE_KIND_DECLARES_LINKER_NAME:
            return self._resolve_linker_name(subject, target)
        return self._resolve_l5(edge_kind, subject, target)

    def _resolve_exports(
        self, subject: str, target: str | None
    ) -> tuple[bool, tuple[CoverageRecord, ...], frozenset[str] | None] | None:
        join = self.exports.join
        if subject.startswith(BINARY_SYMBOL_PREFIX):
            rec = join.right.get(subject)
            if rec is None:
                return None
            entry = self.exports.entries[subject]
            unit = (
                UNIT_DEPENDENCY_HEADERS
                if is_toolchain_symbol(entry.spelling)
                else UNIT_HEADERS
            )
            records = (header_coverage_record(self.snap, EDGE_KIND_EXPORTS),)
            return _join_observed(rec, target), records, frozenset({unit})
        if subject not in join.left:
            return None
        return _join_observed(join.left[subject], target), self.export_records, None

    def _resolve_debug(
        self, subject: str, target: str | None
    ) -> tuple[bool, tuple[CoverageRecord, ...], frozenset[str] | None] | None:
        join = self.debug_types.join
        if subject.startswith(DEBUG_TYPE_PREFIX):
            rec = join.right.get(subject)
            if rec is None:
                return None
            records = (header_coverage_record(self.snap, EDGE_KIND_DEBUG_TYPE_OF),)
            return _join_observed(rec, target), records, frozenset({UNIT_HEADERS})
        if subject not in join.left:
            return None
        return (
            _join_observed(join.left[subject], target),
            (debug_coverage_record(self.snap),),
            None,
        )

    def _resolve_header_edge(
        self, edge_kind: str, subject: str, target: str | None
    ) -> tuple[bool, tuple[CoverageRecord, ...], frozenset[str] | None] | None:
        edges = self._projection[edge_kind]
        is_header = subject.startswith("header://")
        if not is_header and subject not in self._entity_nodes:
            return None
        observed = _incident(edges, subject, target)
        header = header_coverage_record(self.snap, edge_kind)
        records: tuple[CoverageRecord, ...] = (header,)
        if edge_kind == EDGE_KIND_REFERENCES and not observed:
            records = (self._references_record(header, subject, target),)
        return observed, records, frozenset({UNIT_HEADERS})

    def _references_record(
        self, header: CoverageRecord, subject: str, target: str | None
    ) -> CoverageRecord:
        """The header record, unless one of *subject*'s own type spellings is
        one the index dropped as ambiguous and could name *target* (any
        snapshot type, with no target): that reference was never resolved,
        so its absence proves nothing."""
        idents = self._refs.by_node.get(subject, ())
        blocked = sorted(
            ident
            for ident in idents
            if ident in self._ambiguous_spellings
            and (target is None or target in self._ambiguous_spellings[ident])
        )
        if not blocked:
            return header
        return CoverageRecord(
            EDGE_KIND_REFERENCES, PRODUCER_TYPE_SPELLINGS, ProducerRun.FAILED,
            header.units, reason="ambiguous_type_spelling:" + ",".join(blocked),
            source="compare.surface_graph.type_spelling_index",
        )  # fmt: skip

    def _records_record(self) -> CoverageRecord:
        return CoverageRecord(
            EDGE_KIND_DECLARES_LINKER_NAME, PRODUCER_SNAPSHOT_RECORDS,
            ProducerRun.RAN, frozenset({UNIT_RECORDS}),
            source="AbiSnapshot.functions/.variables",
        )  # fmt: skip

    def _resolve_linker_name(
        self, subject: str, target: str | None
    ) -> tuple[bool, tuple[CoverageRecord, ...], frozenset[str] | None] | None:
        edges = self._projection[EDGE_KIND_DECLARES_LINKER_NAME]
        is_symbol = subject.startswith("symbol://")
        kind = self._entity_nodes.get(subject)
        if not is_symbol and kind not in ("function", "variable"):
            return None
        return _incident(edges, subject, target), (self._records_record(),), None

    def _resolve_l5(
        self, edge_kind: str, subject: str, target: str | None
    ) -> tuple[bool, tuple[CoverageRecord, ...], frozenset[str] | None] | None:
        graph = self.source_graph
        if graph is None:
            return False, (_no_source_graph(edge_kind),), None
        if not any(n.id == subject for n in graph.nodes):
            return None
        edges = {(e.src, e.dst) for e in graph.edges if e.kind == edge_kind}
        return (
            _incident(edges, subject, target),
            pass_coverage_records(graph, edge_kind),
            None,
        )


def _incident(
    edges: Iterable[tuple[str, str]], subject: str, target: str | None
) -> bool:
    for src, dst in edges:
        if src == subject and (target is None or dst == target):
            return True
        if dst == subject and (target is None or src == target):
            return True
    return False


def _no_source_graph(edge_kind: str) -> CoverageRecord:
    return CoverageRecord(
        edge_kind, "source_graph", ProducerRun.NOT_RUN, frozenset({ALL_UNITS}),
        reason="no_source_graph", source="AbiSnapshot.build_source",
    )  # fmt: skip


def query_edges(
    evidence: EdgeEvidence,
    edge_kind: str,
    subjects: Iterable[str],
    *,
    scope: frozenset[str] | None = None,
) -> Mapping[str, EdgeQueryResult]:
    """:meth:`EdgeEvidence.query` for many subjects at once."""
    return {s: evidence.query(edge_kind, s, scope=scope) for s in subjects}
