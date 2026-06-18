# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Evidence-directed focusing — the points-of-interest work-list (ADR-035 D7).

Cross-source links are read in **two** directions (ADR-035 D7). The cross-check
engine (``crosscheck.py``, D4) reads them *after* a scan to emit findings; this
module reads the *same* cheap, already-computed facts *before* the expensive scan
to shrink its scope to a **points-of-interest (POI) set**. It is mechanically the
**reverse** of the ``explain-finding`` localization walk (export → decl → header
→ build option): instead of explaining a finding after the fact, the POI set is
computed up front and handed to ``source_replay`` scope selection and the
cross-check engine as a work-list, so a large project pays L4/L5 cost only on the
handful of entities the binary/header evidence already flagged.

The cheap inputs are:

- the directly-changed files/TUs (``--changed-path``/``--since``);
- the always-on **pattern pre-scan** escalation triggers (D2 → POI paths);
- the **L0/L1/L2 deltas** vs. a baseline (added/removed exports, exported
  symbols with no public declaration, exported template instantiations);
- the **risk score** (D3), which only *adds* further candidates.

**Floor (ADR-035 D7).** The POI set **always** includes the directly-changed
files/TUs unconditionally — the risk score and the delta walk only *add*
candidates, they can never *remove* a changed TU. So a mis-weighted ``risk_rules``
profile cannot drop an obviously-relevant changed translation unit.

Everything here is a pure function over in-memory snapshots and string sets — no
binaries are parsed and no external tools run — so the module is fully covered by
fast unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function, Variable
    from .risk import RiskScore

#: POI fact-schema version. Independent of every other buildsource schema
#: version (see ``buildsource/CLAUDE.md`` "Versioning").
POI_VERSION: int = 1


class POIKind(str, Enum):
    """What a point of interest names — the granularity ``source_replay`` consumes."""

    PATH = "path"  # a changed file / source path → narrows the replay scope
    SYMBOL = "symbol"  # an exported binary symbol → resolve its source decl/TU
    ENTITY = "entity"  # a named source entity (type/decl) → cross-check work-list


class POIReason(str, Enum):
    """Why a point of interest was flagged (drives the explainable report)."""

    CHANGED_PATH = "changed_path"  # floor: a directly-changed file/TU (unconditional)
    PATTERN_TRIGGER = "pattern_trigger"  # an S3 pattern pre-scan escalation trigger
    EXPORT_ADDED = "export_added"  # a symbol the new binary exports, old did not
    EXPORT_REMOVED = "export_removed"  # a symbol the old binary exported, new lacks
    EXPORTED_NO_DECL = "exported_no_decl"  # exported but no public declaration
    TEMPLATE_EXPORT = "template_export"  # exported template instantiation (seed TUs)
    RISK_ESCALATION = "risk_escalation"  # the risk score broadened the work-list


@dataclass(frozen=True)
class PointOfInterest:
    """One work-list entry: *what* to focus on and *why* (ADR-035 D7)."""

    key: str
    kind: POIKind
    reason: POIReason
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind.value,
            "reason": self.reason.value,
            "detail": self.detail,
        }


@dataclass
class PointsOfInterest:
    """The D7 work-list handed to ``source_replay`` scope selection + cross-checks.

    ``points`` is the deduplicated, deterministically-ordered set of POIs.
    ``changed_paths()`` is the path subset (the replay-scope seed, floor included);
    ``symbols()`` is the exported-symbol subset (link-back targets). A reader can
    always see *why* the scan looked where it did, never a bare "scanned source".
    """

    points: list[PointOfInterest] = field(default_factory=list)
    version: int = POI_VERSION

    def __bool__(self) -> bool:
        return bool(self.points)

    def changed_paths(self) -> list[str]:
        """The path POIs — the replay-scope seed (always includes the floor)."""
        seen: dict[str, None] = {}
        for p in self.points:
            if p.kind is POIKind.PATH:
                seen.setdefault(p.key, None)
        return list(seen)

    def symbols(self) -> list[str]:
        """The exported-symbol POIs — link-back targets for replay / cross-check."""
        seen: dict[str, None] = {}
        for p in self.points:
            if p.kind is POIKind.SYMBOL:
                seen.setdefault(p.key, None)
        return list(seen)

    def counts_by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.points:
            counts[p.reason.value] = counts.get(p.reason.value, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "total": len(self.points),
            "changed_paths": self.changed_paths(),
            "symbols": self.symbols(),
            "counts_by_reason": self.counts_by_reason(),
            "points": [p.to_dict() for p in self.points],
        }


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------


def build_points_of_interest(
    *,
    changed_paths: list[str] | tuple[str, ...] = (),
    risk: RiskScore | None = None,
    pattern_triggers: list[Any] | tuple[Any, ...] = (),
    baseline: AbiSnapshot | None = None,
    candidate: AbiSnapshot | None = None,
) -> PointsOfInterest:
    """Compute the D7 points-of-interest work-list from the cheap evidence.

    Inputs, all optional and all cheap (already computed by the time the scan
    decides what to parse):

    - ``changed_paths`` — the floor (ADR-035 D7): every directly-changed file is
      added unconditionally as a ``CHANGED_PATH`` POI **first**, so nothing below
      can drop it.
    - ``pattern_triggers`` — the S3 pattern pre-scan's per-kind
      :class:`~abicheck.buildsource.pattern_scan.EscalationTrigger` objects; each
      contributes its sample paths as ``PATTERN_TRIGGER`` POIs.
    - ``baseline`` / ``candidate`` — when both are given, the L0/L1/L2 export
      deltas are walked (reverse explain-finding): added/removed exports,
      exported symbols with no public declaration, and exported template
      instantiations become ``SYMBOL`` POIs that point the scan at the source
      declarations that emit them.
    - ``risk`` — the numeric risk score (D3): a positive score is recorded as a
      single ``RISK_ESCALATION`` marker POI (it only *adds*, never removes — the
      floor is already in place), keeping the work-list explainable.

    Deterministic for a fixed diff (ADR-035 D3/D7): the same inputs always yield
    the same ordered work-list.
    """
    points: list[PointOfInterest] = []
    # Dedup on (kind, key, reason): the floor's CHANGED_PATH entry is preserved
    # even if the same path is later flagged for another reason (each distinct
    # reason is its own explainable POI), while exact duplicates collapse.
    seen: set[tuple[str, str, str]] = set()

    def _add(key: str, kind: POIKind, reason: POIReason, detail: str = "") -> None:
        key = (key or "").strip()
        if not key:
            return
        ident = (kind.value, key, reason.value)
        if ident in seen:
            return
        seen.add(ident)
        points.append(PointOfInterest(key=key, kind=kind, reason=reason, detail=detail))

    # 1) Floor first — every changed path, unconditionally (ADR-035 D7).
    for path in changed_paths:
        _add(str(path), POIKind.PATH, POIReason.CHANGED_PATH, "directly changed")

    # 2) Pattern pre-scan escalation triggers → focus paths. Each trigger carries
    # one ``path:line`` sample location; the path component is the focus seed.
    for trig in pattern_triggers:
        kind = getattr(trig, "kind", "")
        kind_str = getattr(kind, "value", str(kind))
        sample = str(getattr(trig, "sample_location", "") or "")
        path = _path_of_location(sample)
        _add(
            path,
            POIKind.PATH,
            POIReason.PATTERN_TRIGGER,
            f"pattern: {kind_str}",
        )

    # 3) L0/L1/L2 export deltas vs. the baseline (reverse explain-finding walk).
    if baseline is not None and candidate is not None:
        _add_export_deltas(_add, baseline, candidate)

    # 4) Risk score — only *adds* a marker; the floor already protects changed TUs.
    if risk is not None and risk.total > 0:
        _add(
            f"risk:{risk.total}",
            POIKind.ENTITY,
            POIReason.RISK_ESCALATION,
            f"risk score {risk.total} (auto→{risk.recommended_method})",
        )

    return PointsOfInterest(points=points)


def _add_export_deltas(add: Any, baseline: AbiSnapshot, candidate: AbiSnapshot) -> None:
    """Walk the binary-export delta and the no-public-decl set into SYMBOL POIs."""
    old_exports = _exported_names(baseline)
    new_exports = _exported_names(candidate)

    for sym in sorted(new_exports - old_exports):
        add(
            sym,
            POIKind.SYMBOL,
            POIReason.EXPORT_ADDED,
            "new export — point the scan at the decl that emits it",
        )
        if _looks_template_instantiation(sym):
            add(
                sym,
                POIKind.SYMBOL,
                POIReason.TEMPLATE_EXPORT,
                "exported template instantiation — seed which TUs to replay",
            )
    for sym in sorted(old_exports - new_exports):
        add(
            sym,
            POIKind.SYMBOL,
            POIReason.EXPORT_REMOVED,
            "removed export — resolve its former source declaration",
        )

    # Exported-but-undeclared in the candidate: an export the new public headers
    # do not declare is exactly where the source scan should look (D7) — the same
    # signal ``exported_not_public`` reports after the fact.
    declared = _public_decl_symbols(candidate)
    if declared is not None:
        for sym in sorted(new_exports - declared):
            if sym in (new_exports - old_exports):
                continue  # already added as EXPORT_ADDED
            add(
                sym,
                POIKind.SYMBOL,
                POIReason.EXPORTED_NO_DECL,
                "exported with no public declaration — resolve its source decl",
            )


def _exported_names(snap: AbiSnapshot) -> set[str]:
    """Default/unversioned exported symbol names from a snapshot's export table.

    Mirrors :func:`crosscheck._exported_symbol_names` (default ELF versions only,
    Mach-O leading-underscore strip) but returns an empty set — not ``None`` —
    when there is no export table, so the delta walk degrades to "no POIs from
    exports" rather than raising.
    """
    if snap.elf is not None:
        return {s.name for s in snap.elf.symbols if s.name and s.is_default}
    if snap.pe is not None:
        return {e.name for e in snap.pe.exports if e.name}
    if snap.macho is not None:
        return {
            e.name[1:] if e.name.startswith("_") else e.name
            for e in snap.macho.exports
            if e.name
        }
    return set()


def _public_decl_symbols(snap: AbiSnapshot) -> set[str] | None:
    """Symbols a public header declares, or ``None`` when provenance is absent.

    Returns ``None`` (so the caller skips the exported-no-decl walk) unless the
    snapshot carries public-header AST — without it every decl is ``UNKNOWN`` and
    "no public declaration" cannot be told from "no provenance".
    """
    from ..model import ScopeOrigin

    if not snap.from_headers:
        return None
    syms: set[str] = set()
    decls: list[Function | Variable] = [*snap.functions, *snap.variables]
    for d in decls:
        if d.origin != ScopeOrigin.PUBLIC_HEADER:
            continue
        if d.mangled:
            syms.add(d.mangled)
        if d.name:
            syms.add(d.name)
    return syms


def _path_of_location(location: str) -> str:
    """The file path from a ``path:line`` sample location (drops the line number).

    A bare ``"42"`` (no path, scan over in-memory text) yields ``""`` and is
    dropped by ``_add``; a Windows ``C:\\x.h:3`` keeps the drive by splitting on
    the last colon only when the tail is all digits.
    """
    loc = (location or "").strip()
    if not loc:
        return ""
    head, sep, tail = loc.rpartition(":")
    if sep and tail.isdigit():
        return head
    # No colon: a bare line number ("7", from an in-memory scan) has no path.
    if not sep and loc.isdigit():
        return ""
    return loc


def _looks_template_instantiation(symbol: str) -> bool:
    """Whether a mangled symbol carries a template instantiation (Itanium ``I…E``).

    Conservative: Itanium template substitutions encode arguments between ``I``
    and ``E`` (``_Z…I…E…``); MSVC uses ``?$``. A false positive only over-seeds
    the work-list (still bounded), never drops a changed TU (the floor holds).
    """
    if symbol.startswith("_Z") and "I" in symbol and "E" in symbol:
        return True
    return "?$" in symbol


# ---------------------------------------------------------------------------
# symbol → translation-unit resolution (ADR-035 D7, the focusing half)
# ---------------------------------------------------------------------------


def resolve_symbol_tus(
    poi: PointsOfInterest, baseline: AbiSnapshot | None
) -> tuple[str, ...]:
    """Resolve the export-delta SYMBOL POIs to the source files that emit them.

    This is the consumer that turns ``poi.symbols()`` (the cheap L0↔L2 export
    delta, computed by :func:`build_points_of_interest`) into a **replay scope
    seed** — the missing half of ADR-035 D7's reverse explain-finding walk
    (export → decl → declaring file). It reads the **baseline's** cached L5
    source graph (the full-depth baseline of ADR-035 D9 carries it), so a changed
    export points the *new* scan at exactly the TU(s) that declare it instead of
    replaying the whole target.

    Pure and best-effort: returns ``()`` whenever the baseline carries no L5
    graph (the common shallow-baseline case) or no symbol resolves — never an
    error, so it only ever *adds* focus, never drops a changed TU (the floor in
    :class:`PointsOfInterest` already holds those).
    """
    symbols = set(poi.symbols())
    if not symbols or baseline is None:
        return ()
    pack = getattr(baseline, "build_source", None)
    graph = getattr(pack, "source_graph", None) if pack is not None else None
    if graph is None or not getattr(graph, "nodes", None):
        return ()

    # 1) binary_symbol nodes whose exported name is a POI symbol.
    sym_node_ids = {
        n.id
        for n in graph.nodes
        if getattr(n, "kind", "") == "binary_symbol" and n.label in symbols
    }
    if not sym_node_ids:
        return ()

    # 2) the source decls that map to those symbols (decl → symbol edge).
    decl_ids = {
        e.src
        for e in graph.edges
        if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL" and e.dst in sym_node_ids
    }
    if not decl_ids:
        return ()

    # 3) the file each decl is declared/defined in: a SOURCE_DECLARES edge from a
    # file node, or the decl node's own ``def_file``/source-location attr.
    node_by_id = {n.id: n for n in graph.nodes}
    files: dict[str, None] = {}
    for e in graph.edges:
        if e.kind == "SOURCE_DECLARES" and e.dst in decl_ids:
            fn = node_by_id.get(e.src)
            if fn is not None and fn.label:
                files.setdefault(fn.label, None)
    for did in decl_ids:
        dn = node_by_id.get(did)
        attrs = getattr(dn, "attrs", None) or {} if dn is not None else {}
        loc = attrs.get("def_file") or attrs.get("source_location")
        if loc:
            files.setdefault(_path_of_location(str(loc)), None)
    files.pop("", None)
    return tuple(files)
