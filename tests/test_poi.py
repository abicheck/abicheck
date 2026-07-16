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

"""Tests for the ADR-035 D7 points-of-interest work-list (G19.5, Phase 3b).

Pure tests over in-memory snapshots and string sets — the cheap evidence the POI
builder consumes to focus the expensive scan. Default lane.
"""

from __future__ import annotations

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.pattern_scan import (
    EscalationTrigger,
    PatternCategory,
    PatternKind,
)
from abicheck.buildsource.poi import (
    POIKind,
    PointOfInterest,
    PointsOfInterest,
    POIReason,
    build_points_of_interest,
    resolve_changed_paths_public_impact,
    resolve_symbol_tus,
)
from abicheck.buildsource.risk import score_changed_paths
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    ScopeOrigin,
    Visibility,
)


def _snap(
    *sym_names: str, decls: list[Function] | None = None, from_headers: bool = False
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=from_headers,
        functions=list(decls or []),
        elf=ElfMetadata(symbols=[ElfSymbol(name=n) for n in sym_names]),
    )


def _pub_func(name: str, mangled: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def test_floor_includes_every_changed_path_unconditionally() -> None:
    poi = build_points_of_interest(changed_paths=["src/a.cpp", "include/b.h"])
    assert set(poi.changed_paths()) == {"src/a.cpp", "include/b.h"}
    assert all(p.reason is POIReason.CHANGED_PATH for p in poi.points)


def test_risk_score_only_adds_never_removes_floor() -> None:
    # A docs-only (negative) risk score must not drop a real changed TU (floor).
    risk = score_changed_paths(["docs/x.md"])
    assert risk.total < 0
    poi = build_points_of_interest(changed_paths=["src/a.cpp"], risk=risk)
    assert "src/a.cpp" in poi.changed_paths()
    # Negative score adds no risk-escalation marker.
    assert POIReason.RISK_ESCALATION.value not in poi.counts_by_reason()


def test_positive_risk_adds_escalation_marker_entity() -> None:
    risk = score_changed_paths(["include/foo.h"])
    assert risk.total > 0
    poi = build_points_of_interest(changed_paths=["include/foo.h"], risk=risk)
    markers = [p for p in poi.points if p.reason is POIReason.RISK_ESCALATION]
    assert len(markers) == 1
    assert markers[0].kind is POIKind.ENTITY


def test_pattern_triggers_contribute_focus_paths() -> None:
    trig = EscalationTrigger(
        kind=PatternKind.PRAGMA_PACK,
        category=PatternCategory.LAYOUT,
        recommended_method="s5",
        count=3,
        sample_location="include/packed.h:7",
        reason="pragma pack",
    )
    poi = build_points_of_interest(changed_paths=[], pattern_triggers=[trig])
    assert "include/packed.h" in poi.changed_paths()
    assert poi.counts_by_reason()[POIReason.PATTERN_TRIGGER.value] == 1


def test_pattern_trigger_without_path_is_dropped() -> None:
    # An in-memory scan yields a bare line number ("7"), no path → no POI.
    trig = EscalationTrigger(
        kind=PatternKind.PRAGMA_PACK,
        category=PatternCategory.LAYOUT,
        recommended_method="s5",
        count=1,
        sample_location="7",
        reason="pragma pack",
    )
    poi = build_points_of_interest(pattern_triggers=[trig])
    assert poi.changed_paths() == []


def test_export_delta_flags_added_and_removed_symbols() -> None:
    old = _snap("_Z3foov", "_Z3barv")
    new = _snap("_Z3foov", "_Z3bazv")
    poi = build_points_of_interest(baseline=old, candidate=new)
    syms = poi.symbols()
    assert "_Z3bazv" in syms  # added
    assert "_Z3barv" in syms  # removed
    reasons = poi.counts_by_reason()
    assert reasons.get(POIReason.EXPORT_ADDED.value) == 1
    assert reasons.get(POIReason.EXPORT_REMOVED.value) == 1


def test_exported_no_decl_flagged_when_provenance_present() -> None:
    # New exports _Z3foov (declared) and _Z6secretv (no public decl).
    new = _snap(
        "_Z3foov",
        "_Z6secretv",
        decls=[_pub_func("foo", "_Z3foov")],
        from_headers=True,
    )
    old = _snap("_Z3foov", "_Z6secretv", from_headers=True)
    poi = build_points_of_interest(baseline=old, candidate=new)
    # No added/removed exports (same set), so _Z6secretv is flagged as no-decl.
    assert POIReason.EXPORTED_NO_DECL.value in poi.counts_by_reason()
    assert "_Z6secretv" in poi.symbols()


def test_template_export_seed_on_added_instantiation() -> None:
    old = _snap("_Z3foov")
    new = _snap("_Z3foov", "_Z3barIiEvv")  # added template instantiation
    poi = build_points_of_interest(baseline=old, candidate=new)
    reasons = poi.counts_by_reason()
    assert reasons.get(POIReason.TEMPLATE_EXPORT.value) == 1


def test_deterministic_for_fixed_inputs() -> None:
    args = dict(
        changed_paths=["src/a.cpp", "src/b.cpp"],
        risk=score_changed_paths(["src/a.cpp"]),
    )
    a = build_points_of_interest(**args).to_dict()
    b = build_points_of_interest(**args).to_dict()
    assert a == b


def test_empty_inputs_yield_empty_worklist() -> None:
    poi = build_points_of_interest()
    assert not poi
    assert poi.to_dict()["total"] == 0


# --------------------------------------------------------------------------- #
# resolve_symbol_tus — the focusing half (symbol POI → declaring TU)
# --------------------------------------------------------------------------- #


def _sym_poi(*symbols: str) -> PointsOfInterest:
    return PointsOfInterest(
        points=[
            PointOfInterest(s, POIKind.SYMBOL, POIReason.EXPORT_ADDED) for s in symbols
        ]
    )


def _graph_baseline(graph: SourceGraphSummary | None) -> AbiSnapshot:
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    if graph is not None:
        snap.build_source = BuildSourcePack(root="", source_graph=graph)
    return snap


def test_resolve_symbol_tus_maps_export_to_declaring_file() -> None:
    # Changed export `_Z3barv` → its source decl (SOURCE_DECL_MAPS_TO_SYMBOL) →
    # the file that declares it (SOURCE_DECLARES). That file is the focused TU.
    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="binary_symbol://_Z3barv", kind="binary_symbol", label="_Z3barv"
            ),
            GraphNode(id="decl://bar", kind="source_decl", label="bar"),
            GraphNode(id="header://src/bar.cpp", kind="header", label="src/bar.cpp"),
        ],
        edges=[
            GraphEdge(
                src="decl://bar",
                dst="binary_symbol://_Z3barv",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            ),
            GraphEdge(
                src="header://src/bar.cpp", dst="decl://bar", kind="SOURCE_DECLARES"
            ),
        ],
    )
    tus = resolve_symbol_tus(_sym_poi("_Z3barv"), _graph_baseline(graph))
    assert tus == ("src/bar.cpp",)


def test_resolve_symbol_tus_uses_decl_def_file_attr() -> None:
    # A call-graph-style decl with no SOURCE_DECLARES edge still carries its file
    # in def_file; the resolver falls back to that.
    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="binary_symbol://_Z3barv", kind="binary_symbol", label="_Z3barv"
            ),
            GraphNode(
                id="decl://bar",
                kind="source_decl",
                label="bar",
                attrs={"def_file": "/work/src/bar.cpp"},
            ),
        ],
        edges=[
            GraphEdge(
                src="decl://bar",
                dst="binary_symbol://_Z3barv",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            ),
        ],
    )
    tus = resolve_symbol_tus(_sym_poi("_Z3barv"), _graph_baseline(graph))
    assert tus == ("/work/src/bar.cpp",)


def test_resolve_symbol_tus_unknown_symbol_resolves_nothing() -> None:
    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="binary_symbol://_Z3foov", kind="binary_symbol", label="_Z3foov"
            ),
        ],
        edges=[],
    )
    assert resolve_symbol_tus(_sym_poi("_Z3barv"), _graph_baseline(graph)) == ()


def test_resolve_symbol_tus_symbol_node_without_decl_mapping() -> None:
    # The export's binary_symbol node exists, but nothing maps a decl to it (no
    # SOURCE_DECL_MAPS_TO_SYMBOL edge) → no TU to focus, clean empty tuple.
    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="binary_symbol://_Z3barv", kind="binary_symbol", label="_Z3barv"
            ),
        ],
        edges=[],
    )
    assert resolve_symbol_tus(_sym_poi("_Z3barv"), _graph_baseline(graph)) == ()


def test_resolve_symbol_tus_ignores_dangling_declares_edge() -> None:
    # A SOURCE_DECLARES edge whose file node is absent (dangling src) is skipped,
    # not crashed; the def_file fallback still resolves the TU.
    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="binary_symbol://_Z3barv", kind="binary_symbol", label="_Z3barv"
            ),
            GraphNode(
                id="decl://bar",
                kind="source_decl",
                label="bar",
                attrs={"def_file": "src/bar.cpp"},
            ),
        ],
        edges=[
            GraphEdge(
                src="decl://bar",
                dst="binary_symbol://_Z3barv",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            ),
            # Points at a header node that does not exist → fn is None, skipped.
            GraphEdge(
                src="header://gone.cpp", dst="decl://bar", kind="SOURCE_DECLARES"
            ),
        ],
    )
    assert resolve_symbol_tus(_sym_poi("_Z3barv"), _graph_baseline(graph)) == (
        "src/bar.cpp",
    )


def test_resolve_symbol_tus_degrades_without_graph_or_baseline() -> None:
    # No baseline, no graph, no symbols → always a clean empty tuple (never raises),
    # so a shallow baseline simply contributes no extra focus (ADR-035 D7).
    assert resolve_symbol_tus(_sym_poi("_Z3barv"), None) == ()
    assert resolve_symbol_tus(_sym_poi("_Z3barv"), _graph_baseline(None)) == ()
    assert (
        resolve_symbol_tus(_sym_poi("_Z3barv"), _graph_baseline(SourceGraphSummary()))
        == ()
    )
    assert resolve_symbol_tus(PointsOfInterest(), _graph_baseline(None)) == ()


def test_resolve_symbol_tus_end_to_end_from_export_delta() -> None:
    # The full D7 cheap→target chain: build_points_of_interest derives the SYMBOL
    # POI from the L0 export delta, and resolve_symbol_tus turns it into the TU.
    old = AbiSnapshot(library="libfoo.so", version="1", elf=ElfMetadata(symbols=[]))
    new = AbiSnapshot(
        library="libfoo.so",
        version="2",
        elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3barv", is_default=True)]),
    )
    poi = build_points_of_interest(baseline=old, candidate=new)
    assert "_Z3barv" in poi.symbols()

    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="binary_symbol://_Z3barv", kind="binary_symbol", label="_Z3barv"
            ),
            GraphNode(id="decl://bar", kind="source_decl", label="bar"),
            GraphNode(id="header://src/bar.cpp", kind="header", label="src/bar.cpp"),
        ],
        edges=[
            GraphEdge(
                src="decl://bar",
                dst="binary_symbol://_Z3barv",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            ),
            GraphEdge(
                src="header://src/bar.cpp", dst="decl://bar", kind="SOURCE_DECLARES"
            ),
        ],
    )
    assert resolve_symbol_tus(poi, _graph_baseline(graph)) == ("src/bar.cpp",)


# ── resolve_changed_paths_public_impact (ADR-041 P1 #3) ─────────────────────


def test_changed_paths_impact_finds_public_entry_reaching_changed_internal() -> None:
    # `api` (public) calls `helper` (internal, declared in src/detail/cache.cpp).
    # A change to that file should flag `api` as impacted.
    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="decl://api",
                kind="source_decl",
                label="api",
                attrs={"visibility": "public_header"},
            ),
            GraphNode(
                id="decl://helper",
                kind="source_decl",
                label="helper",
                attrs={"visibility": "private_header"},
            ),
            GraphNode(
                id="header://src/detail/cache.cpp",
                kind="header",
                label="src/detail/cache.cpp",
            ),
        ],
        edges=[
            GraphEdge(src="decl://api", dst="decl://helper", kind="DECL_CALLS_DECL"),
            GraphEdge(
                src="header://src/detail/cache.cpp",
                dst="decl://helper",
                kind="SOURCE_DECLARES",
            ),
        ],
    )
    impacted = resolve_changed_paths_public_impact(
        ["src/detail/cache.cpp"], graph
    )
    assert impacted == frozenset({"decl://api"})


def test_changed_paths_impact_matches_by_suffix() -> None:
    # Graph node labels carry an absolute build path; the caller passes a
    # repo-relative changed path — suffix matching bridges the two spellings.
    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="decl://api",
                kind="source_decl",
                label="api",
                attrs={"visibility": "public_header"},
            ),
            GraphNode(
                id="decl://helper",
                kind="source_decl",
                label="helper",
                attrs={"def_file": "/work/src/detail/cache.cpp"},
            ),
        ],
        edges=[
            GraphEdge(src="decl://api", dst="decl://helper", kind="DECL_CALLS_DECL"),
        ],
    )
    impacted = resolve_changed_paths_public_impact(
        ["src/detail/cache.cpp"], graph
    )
    assert impacted == frozenset({"decl://api"})


def test_changed_paths_impact_includes_entry_declared_in_changed_file_itself() -> None:
    # A public entry directly declared in a changed file is trivially impacted,
    # even with no outgoing dependency edge at all.
    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="decl://api",
                kind="source_decl",
                label="api",
                attrs={"visibility": "public_header"},
            ),
            GraphNode(id="header://api.hpp", kind="header", label="api.hpp"),
        ],
        edges=[
            GraphEdge(src="header://api.hpp", dst="decl://api", kind="SOURCE_DECLARES"),
        ],
    )
    assert resolve_changed_paths_public_impact(["api.hpp"], graph) == frozenset(
        {"decl://api"}
    )


def test_changed_paths_impact_unrelated_change_yields_nothing() -> None:
    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="decl://api",
                kind="source_decl",
                label="api",
                attrs={"visibility": "public_header"},
            ),
            GraphNode(
                id="decl://helper",
                kind="source_decl",
                label="helper",
                attrs={"def_file": "src/detail/cache.cpp"},
            ),
        ],
        edges=[
            GraphEdge(src="decl://api", dst="decl://helper", kind="DECL_CALLS_DECL"),
        ],
    )
    assert resolve_changed_paths_public_impact(["src/unrelated.cpp"], graph) == (
        frozenset()
    )


def test_changed_paths_impact_degrades_without_graph_or_paths() -> None:
    assert resolve_changed_paths_public_impact([], SourceGraphSummary()) == frozenset()
    assert resolve_changed_paths_public_impact(["a.cpp"], None) == frozenset()
    assert (
        resolve_changed_paths_public_impact(["a.cpp"], SourceGraphSummary())
        == frozenset()
    )
