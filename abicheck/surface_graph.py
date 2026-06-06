# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Indexed query layer over an :class:`~abicheck.model.AbiSnapshot` (ADR-025 D0).

The snapshot is, in effect, a *typed declaration graph*: functions reference
parameter/return types, records reference field/base/typedef types, every
declaration carries header provenance and visibility. The rest of the codebase
queries that graph one edge at a time (``surface.py`` walks the reachability
closure; ``internal_leak.py`` walks public→private). :class:`SurfaceGraph` is
the shared, read-only, *order-stable* index those one-edge call sites lack — it
owns no detection logic, only structure.

It is the substrate for the API-surface-intelligence capabilities in ADR-025:
surface metrics (A1), idiom recognition (A2), cross-library reasoning (A3), and
pattern-aware verdicts (A4). Building it is deterministic so every downstream
metric is reproducible and cache-keyable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .checker_policy import EvidenceTier
from .model import AbiSnapshot, Function, RecordType, ScopeOrigin, Visibility
from .surface import _type_identifiers


@dataclass(frozen=True)
class SurfaceGraph:
    """A read-only indexed view over one :class:`AbiSnapshot`.

    All mappings are built once at construction and are order-stable (sorted),
    so identical snapshots always yield identical graphs.
    """

    snapshot: AbiSnapshot
    # symbol name / mangled name -> Function (public and non-public alike)
    functions_by_name: Mapping[str, Function]
    # record name (and trailing ``::`` segment) -> RecordType
    types_by_name: Mapping[str, RecordType]
    # type name -> set of type names it references (fields, bases, typedef target)
    type_refs: Mapping[str, frozenset[str]]
    # type name -> public root symbols that transitively reach it
    reached_by: Mapping[str, frozenset[str]]
    # header path -> declaration names defined there
    by_header: Mapping[str, frozenset[str]]
    # public root symbol -> the type names its signature directly references
    _root_seed_types: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def public_roots(self) -> frozenset[str]:
        """Names of ``Visibility.PUBLIC`` functions and variables."""
        return frozenset(self._root_seed_types)

    def reachable_types(self, root: str) -> frozenset[str]:
        """Known types transitively reachable from public *root*'s signature."""
        seeds = self._root_seed_types.get(root)
        if not seeds:
            return frozenset()
        seen: set[str] = set()
        queue = list(seeds)
        while queue:
            name = queue.pop()
            if name in seen:
                continue
            seen.add(name)
            # Canonicalise an unqualified name (``A``) to the record's full key
            # (``ns::A``) before following its adjacency, so ``type_refs`` can
            # stay keyed only by the full name (no duplicated alias entries that
            # would inflate fan-in).
            rec = self.types_by_name.get(name)
            key = rec.name if rec is not None else name
            for ref in self.type_refs.get(key, frozenset()):
                if ref not in seen:
                    queue.append(ref)
        return frozenset(n for n in seen if n in self.types_by_name)

    def _aliases(self, type_name: str) -> frozenset[str]:
        """All names a reference may use for *type_name*: full + trailing segment.

        A field written ``A`` inside ``ns`` yields the ref ``A``; written
        ``ns::A`` it yields both ``ns::A`` and ``A``. Matching on either form
        keeps fan-in/out consistent with ``reachable_types``'s canonicalisation.
        """
        names = {type_name}
        rec = self.types_by_name.get(type_name)
        full = rec.name if rec is not None else type_name
        names.add(full)
        if "::" in full:
            names.add(full.rsplit("::", 1)[1])
        return frozenset(names)

    def fan_out(self, type_name: str) -> int:
        """Number of distinct types *type_name* directly references."""
        rec = self.types_by_name.get(type_name)
        key = rec.name if rec is not None else type_name
        return len(self.type_refs.get(key, frozenset()))

    def fan_in(self, type_name: str) -> int:
        """Number of distinct types that directly reference *type_name*."""
        aliases = self._aliases(type_name)
        count = 0
        for refs in self.type_refs.values():
            if aliases & refs:
                count += 1
        return count


def build_surface_graph(snap: AbiSnapshot) -> SurfaceGraph:
    """Construct the deterministic :class:`SurfaceGraph` for *snap*."""
    functions_by_name: dict[str, Function] = {}
    for fn in snap.functions:
        for key in (fn.name, fn.mangled):
            functions_by_name.setdefault(key, fn)

    types_by_name: dict[str, RecordType] = {}
    for rec in snap.types:
        types_by_name.setdefault(rec.name, rec)
        if "::" in rec.name:
            types_by_name.setdefault(rec.name.rsplit("::", 1)[1], rec)

    # Adjacency: a record references the types named in its fields and bases;
    # a typedef references the types named in its target.
    type_refs: dict[str, frozenset[str]] = {}
    for rec in snap.types:
        refs: set[str] = set()
        for f in rec.fields:
            refs |= _type_identifiers(f.type)
        for base in rec.bases:
            refs |= _type_identifiers(base)
        for base in rec.virtual_bases:
            refs |= _type_identifiers(base)
        # Keyed only by the canonical full name. An unqualified reference is
        # resolved to this key by reachable_types()/cohesion via types_by_name,
        # so there are no duplicate alias entries to double-count in fan-in.
        type_refs[rec.name] = frozenset(refs)
    for alias, target in snap.typedefs.items():
        type_refs.setdefault(alias, frozenset(_type_identifiers(target)))

    # Public roots and the types their signatures directly touch.
    root_seed_types: dict[str, frozenset[str]] = {}
    for fn in snap.functions:
        if fn.visibility != Visibility.PUBLIC:
            continue
        seeds = set(_type_identifiers(fn.return_type))
        for p in fn.params:
            seeds |= _type_identifiers(getattr(p, "type", None))
        # C++ overloads share a demangled name but reference different types;
        # union their seeds so an overload's types are never lost by overwrite.
        root_seed_types[fn.name] = root_seed_types.get(
            fn.name, frozenset()
        ) | frozenset(seeds)
    for var in snap.variables:
        if var.visibility != Visibility.PUBLIC:
            continue
        root_seed_types[var.name] = frozenset(_type_identifiers(var.type))

    by_header: dict[str, set[str]] = {}
    for fn in snap.functions:
        if fn.source_header:
            by_header.setdefault(fn.source_header, set()).add(fn.name)
    for var in snap.variables:
        if var.source_header:
            by_header.setdefault(var.source_header, set()).add(var.name)
    for rec in snap.types:
        if rec.source_header:
            by_header.setdefault(rec.source_header, set()).add(rec.name)
    for en in snap.enums:
        if en.source_header:
            by_header.setdefault(en.source_header, set()).add(en.name)

    graph = SurfaceGraph(
        snapshot=snap,
        functions_by_name=dict(sorted(functions_by_name.items())),
        types_by_name=dict(sorted(types_by_name.items())),
        type_refs=dict(sorted(type_refs.items())),
        reached_by={},  # filled below
        by_header={k: frozenset(v) for k, v in sorted(by_header.items())},
        _root_seed_types=dict(sorted(root_seed_types.items())),
    )

    # Inverse closure: type name -> roots that reach it.
    reached_by: dict[str, set[str]] = {}
    for root in graph.public_roots():
        for t in graph.reachable_types(root):
            reached_by.setdefault(t, set()).add(root)
    object.__setattr__(
        graph, "reached_by", {k: frozenset(v) for k, v in sorted(reached_by.items())}
    )
    return graph


# --------------------------------------------------------------------------- #
# A1 — surface structure & metrics (ADR-025 D1.1)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HeaderCoverage:
    """Per-header declared-vs-exported coverage."""

    header: str
    declared: int  # declarations physically defined in this header
    exported: int  # of those, ones that resolve to an exported symbol
    cohesion_clusters: int  # connected components of the declared type graph


@dataclass(frozen=True)
class SurfaceMetrics:
    """Structural facts about one snapshot's public surface (ADR-025 A1).

    Descriptive only — these never drive a verdict on their own.
    """

    library: str
    version: str
    evidence_tier: str
    public_functions: int
    public_variables: int
    public_types: int
    public_enums: int
    exported_symbols: int
    undocumented_exports: int  # exported symbols with EXPORT_ONLY origin
    undocumented_export_ratio: float
    top_fan_in: list[tuple[str, int]]  # (type_name, fan_in), highest first
    header_coverage: list[HeaderCoverage]

    def to_dict(self) -> dict[str, object]:
        return {
            "library": self.library,
            "version": self.version,
            "evidence_tier": self.evidence_tier,
            "public_functions": self.public_functions,
            "public_variables": self.public_variables,
            "public_types": self.public_types,
            "public_enums": self.public_enums,
            "exported_symbols": self.exported_symbols,
            "undocumented_exports": self.undocumented_exports,
            "undocumented_export_ratio": round(self.undocumented_export_ratio, 4),
            "top_fan_in": [{"type": n, "fan_in": c} for n, c in self.top_fan_in],
            "header_coverage": [
                {
                    "header": h.header,
                    "declared": h.declared,
                    "exported": h.exported,
                    "cohesion_clusters": h.cohesion_clusters,
                }
                for h in self.header_coverage
            ],
        }


def _evidence_tier(snap: AbiSnapshot) -> str:
    if snap.from_headers:
        return EvidenceTier.HEADER_AWARE.value
    if snap.dwarf is not None:
        return EvidenceTier.DWARF_AWARE.value
    return EvidenceTier.ELF_ONLY.value


def _public_type_counts(snap: AbiSnapshot) -> tuple[int, int]:
    """Count public record types and enums via the reachability+provenance closure.

    ``snap.types``/``snap.enums`` is the *full parsed universe* — when headers
    are scoped it includes private and transitively-included declarations, so
    raw ``len()`` would inflate the public-surface counts (ADR-025 A1). Reuse
    the ADR-024 public-surface resolver: when it cannot resolve a surface (no
    header-derived visibility, e.g. ELF-only), fall back to the raw counts,
    which are then correct because nothing was scoped.
    """
    from .surface import compute_public_surface

    psurf = compute_public_surface(snap)
    if not psurf.resolvable:
        return len(snap.types), len(snap.enums)

    def _in_surface(name: str) -> bool:
        # A public signature may name a namespaced record unqualified (``A`` for
        # ``ns::A``), so the closure can hold only the short spelling. Count the
        # record if *either* its canonical name or its trailing segment is in
        # the public surface.
        if name in psurf.public_types:
            return True
        return "::" in name and name.rsplit("::", 1)[1] in psurf.public_types

    public_records = sum(1 for r in snap.types if _in_surface(r.name))
    public_enums = sum(1 for e in snap.enums if _in_surface(e.name))
    return public_records, public_enums


def _header_cohesion_clusters(graph: SurfaceGraph, decls: frozenset[str]) -> int:
    """Connected components of the type-reference graph restricted to *decls*.

    A header that is really N unrelated modules shows N clusters; a cohesive
    header shows 1. Only the type declarations in the header participate.
    """
    nodes = {d for d in decls if d in graph.types_by_name}
    if not nodes:
        return 0
    parent: dict[str, str] = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for n in nodes:
        for ref in graph.type_refs.get(n, frozenset()):
            # A reference may use the unqualified spelling (``A`` for ``ns::A``);
            # canonicalise it through types_by_name to the record's full name
            # before testing membership, so a single ``ns::B -> ns::A`` component
            # is not split into two clusters (ADR-027 review).
            rec = graph.types_by_name.get(ref)
            canonical = rec.name if rec is not None else ref
            if canonical in nodes:
                union(n, canonical)
    return len({find(n) for n in nodes})


def compute_surface_metrics(snap: AbiSnapshot, *, top_n: int = 10) -> SurfaceMetrics:
    """Compute the A1 descriptive metrics for *snap*."""
    graph = build_surface_graph(snap)

    public_functions = sum(
        1 for f in snap.functions if f.visibility == Visibility.PUBLIC
    )
    public_variables = sum(
        1 for v in snap.variables if v.visibility == Visibility.PUBLIC
    )
    exported_symbols = public_functions + public_variables

    undocumented = sum(
        1
        for f in snap.functions
        if f.visibility == Visibility.PUBLIC and f.origin == ScopeOrigin.EXPORT_ONLY
    )
    undocumented += sum(
        1
        for v in snap.variables
        if v.visibility == Visibility.PUBLIC and v.origin == ScopeOrigin.EXPORT_ONLY
    )
    ratio = (undocumented / exported_symbols) if exported_symbols else 0.0

    # Iterate canonical record names (each real record once, by full name) so a
    # namespaced record is not listed twice under both ``ns::A`` and ``A``.
    canonical_type_names = sorted({rec.name for rec in snap.types})
    fan_in = sorted(
        ((name, graph.fan_in(name)) for name in canonical_type_names),
        key=lambda kv: (-kv[1], kv[0]),
    )
    top_fan_in = [(n, c) for n, c in fan_in[:top_n] if c > 0]

    # Per-header declared/exported counts with *overload-preserving* identity:
    # functions are counted by mangled name (so foo(int) and foo(double) count
    # as two), not by the collapsed set of demangled names in graph.by_header.
    declared_counts: dict[str, int] = {}
    exported_counts: dict[str, int] = {}

    def _bump(table: dict[str, int], header: str | None) -> None:
        if header:
            table[header] = table.get(header, 0) + 1

    for fn in snap.functions:
        _bump(declared_counts, fn.source_header)
        if fn.visibility == Visibility.PUBLIC:
            _bump(exported_counts, fn.source_header)
    for var in snap.variables:
        _bump(declared_counts, var.source_header)
        if var.visibility == Visibility.PUBLIC:
            _bump(exported_counts, var.source_header)
    for rec in snap.types:
        _bump(declared_counts, rec.source_header)
    for en in snap.enums:
        _bump(declared_counts, en.source_header)

    coverage: list[HeaderCoverage] = []
    for header in sorted(declared_counts):
        coverage.append(
            HeaderCoverage(
                header=header,
                declared=declared_counts[header],
                exported=exported_counts.get(header, 0),
                # Cohesion is over the *type* declarations in the header, which
                # do not overload, so the by_header name set is exact here.
                cohesion_clusters=_header_cohesion_clusters(
                    graph, graph.by_header.get(header, frozenset())
                ),
            )
        )

    public_types, public_enums = _public_type_counts(snap)

    return SurfaceMetrics(
        library=snap.library,
        version=snap.version,
        evidence_tier=_evidence_tier(snap),
        public_functions=public_functions,
        public_variables=public_variables,
        public_types=public_types,
        public_enums=public_enums,
        exported_symbols=exported_symbols,
        undocumented_exports=undocumented,
        undocumented_export_ratio=ratio,
        top_fan_in=top_fan_in,
        header_coverage=coverage,
    )
