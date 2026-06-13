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

"""Tests for ADR-031 L5 source graph: schema round-trip, the build-evidence
graph builder (Phase 2), the structural diff (Phase 5 seed), and pack +
CLI wiring."""

from __future__ import annotations

import json

from click.testing import CliRunner

from abicheck.buildsource.build_evidence import (
    BuildEvidence,
    CompileUnit,
    Confidence,
    Target,
    TargetKind,
)
from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerConfidence
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import (
    SourceAbiSurface,
    SourceEntity,
    SourceLocation,
)
from abicheck.buildsource.source_graph import (
    EDGE_KINDS,
    EVIDENCE_TIER_L5,
    NODE_KINDS,
    SOURCE_GRAPH_VERSION,
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    build_source_graph,
    diff_source_graph,
    diff_source_graph_findings,
)
from abicheck.checker_policy import RISK_KINDS, ChangeKind
from abicheck.cli import main


def _sample_build() -> BuildEvidence:
    b = BuildEvidence(generated_files=["gen/config.h"])
    b.targets.append(Target(
        id="target://libfoo", name="foo", kind=TargetKind.SHARED_LIBRARY,
        source_files=["src/foo.cpp", "gen/config.h"],
        public_headers=["include/foo.h"],
        dependencies=["target://libbar", "sys://pthread"],
        confidence=Confidence.HIGH,
    ))
    b.targets.append(Target(id="target://libbar", name="bar"))
    b.compile_units.append(CompileUnit(
        id="cu://foo", source="src/foo.cpp", output="foo.o",
        target_id="target://libfoo",
        abi_relevant_flags=["-fvisibility=hidden", "-std=c++20"],
    ))
    return b


# ── Phase 2: build_source_graph ────────────────────────────────────────────


def test_build_graph_emits_expected_nodes_and_edges() -> None:
    g = build_source_graph(_sample_build())
    kinds = {n.kind for n in g.nodes}
    assert "target" in kinds
    assert "source" in kinds
    assert "header" in kinds
    assert "compile_unit" in kinds
    assert "build_option" in kinds
    # gen/config.h is in generated_files → typed generated_file, not source.
    assert "generated_file" in kinds
    # A dependency that is not one of our own targets is an external_dependency.
    assert "external_dependency" in kinds

    edge_kinds = {e.kind for e in g.edges}
    assert "TARGET_HAS_SOURCE" in edge_kinds
    assert "TARGET_HAS_PUBLIC_HEADER" in edge_kinds
    assert "TARGET_DEPENDS_ON" in edge_kinds
    assert "COMPILE_UNIT_BUILDS_SOURCE" in edge_kinds
    assert "COMPILE_UNIT_USES_OPTION" in edge_kinds


def test_build_graph_node_and_edge_kinds_are_in_schema() -> None:
    g = build_source_graph(_sample_build())
    assert all(n.kind in NODE_KINDS for n in g.nodes)
    assert all(e.kind in EDGE_KINDS for e in g.edges)


def test_generated_source_typed_generated_file_not_source() -> None:
    g = build_source_graph(_sample_build())
    config = next(n for n in g.nodes if n.label == "gen/config.h")
    assert config.kind == "generated_file"
    assert config.attrs.get("generated") is True


def test_compile_unit_option_edges_match_flags() -> None:
    g = build_source_graph(_sample_build())
    opt_edges = [e for e in g.edges if e.kind == "COMPILE_UNIT_USES_OPTION"]
    targets = {e.dst for e in opt_edges}
    assert "build_option://-fvisibility=hidden" in targets
    assert "build_option://-std=c++20" in targets
    # Option edges carry high confidence (derived from exact argv).
    assert all(e.confidence == "high" for e in opt_edges)


def test_coverage_counts_populated() -> None:
    g = build_source_graph(_sample_build())
    assert g.coverage["targets"] == 2
    assert g.coverage["compile_units"] == 1
    # No call/include extraction in Phase 2 — explicitly marked not-collected.
    assert g.coverage["call_edges"]["collected"] is False
    assert g.coverage["include_edges"]["collected"] is False


def test_build_graph_is_deterministic() -> None:
    b = _sample_build()
    assert build_source_graph(b).graph_id == build_source_graph(b).graph_id


def test_empty_build_yields_empty_graph() -> None:
    g = build_source_graph(BuildEvidence())
    assert g.nodes == []
    assert g.edges == []
    assert g.coverage["targets"] == 0


def test_target_confidence_maps_onto_node_and_edges() -> None:
    b = BuildEvidence()
    b.targets.append(Target(
        id="target://red", source_files=["a.cpp"], confidence=Confidence.REDUCED,
    ))
    b.targets.append(Target(
        id="target://unk", source_files=["b.cpp"], confidence=Confidence.UNKNOWN,
    ))
    g = build_source_graph(b)
    by_id = {n.id: n for n in g.nodes}
    assert by_id["target://red"].confidence == "reduced"
    assert by_id["target://unk"].confidence == "unknown"


def test_blank_source_path_is_skipped() -> None:
    # A degenerate empty path in source_files must not create a stray "" node.
    b = BuildEvidence()
    b.targets.append(Target(id="target://t", source_files=["", "real.cpp"]))
    g = build_source_graph(b)
    assert not any(n.id == "source://" for n in g.nodes)
    assert any(n.label == "real.cpp" for n in g.nodes)


def test_compile_unit_without_source_emits_no_source_edge() -> None:
    b = BuildEvidence()
    b.compile_units.append(CompileUnit(id="cu://nosrc", source=""))
    g = build_source_graph(b)
    assert any(n.id == "cu://nosrc" for n in g.nodes)
    assert not any(e.kind == "COMPILE_UNIT_BUILDS_SOURCE" for e in g.edges)


# ── Phases 3-4: enrich from the L4 source surface ───────────────────────────


def _entity(qn: str, kind: str, *, mangled: str = "", path: str = "include/foo.h",
            origin: str = "PUBLIC_HEADER",
            conf: LayerConfidence = LayerConfidence.HIGH) -> SourceEntity:
    return SourceEntity(
        id=qn, kind=kind, qualified_name=qn, mangled_name=mangled,
        source_location=SourceLocation(path=path, line=1, origin=origin),
        visibility="public_header", confidence=conf,
    )


def _sample_surface() -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    s.reachable_declarations.append(_entity("foo::bar", "function", mangled="_ZN3foo3barEv"))
    s.reachable_types.append(_entity("foo::Widget", "record"))
    s.reachable_types.append(_entity("foo::Color", "enum"))
    s.reachable_types.append(_entity("foo::Alias", "typedef"))
    s.reachable_macros.append(_entity("FOO_VERSION", "macro", conf=LayerConfidence.REDUCED))
    # Keyed by entity identity (the mangled name for C++), exactly as
    # link_source_abi/relink_surface_exports persist it — not by qualified_name.
    s.mappings["source_decl_to_binary_symbol"] = {"_ZN3foo3barEv": "_ZN3foo3barEv"}
    s.mappings["source_type_to_debug_type"] = {"foo::Widget": "struct foo::Widget"}
    return s


def test_source_abi_builds_public_reachability_slice() -> None:
    b = BuildEvidence()
    b.targets.append(Target(
        id="target://libfoo", public_headers=["include/foo.h"], confidence=Confidence.HIGH,
    ))
    g = build_source_graph(b, source_abi=_sample_surface())
    edge_kinds = {e.kind for e in g.edges}
    # target -> header -> decl -> exported symbol, plus target -> symbol.
    assert "TARGET_HAS_PUBLIC_HEADER" in edge_kinds
    assert "SOURCE_DECLARES" in edge_kinds
    assert "SOURCE_DECL_MAPS_TO_SYMBOL" in edge_kinds
    assert "BINARY_EXPORTS_SYMBOL" in edge_kinds
    assert "SOURCE_TYPE_MAPS_TO_DEBUG_TYPE" in edge_kinds
    assert all(e.kind in EDGE_KINDS for e in g.edges)
    assert all(n.kind in NODE_KINDS for n in g.nodes)


def test_cpp_decl_maps_to_symbol_with_identity_keyed_mapping() -> None:
    # Regression (Codex): the persisted source_decl_to_binary_symbol map is keyed
    # by entity identity (mangled name for C++), so build_source_graph must look
    # it up by identity, not qualified_name, or the decl->symbol edge is dropped
    # for every C++ symbol (qualified_name != mangled name).
    g = build_source_graph(BuildEvidence(), source_abi=_sample_surface())
    map_edges = [e for e in g.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"]
    assert len(map_edges) == 1
    decl_ids = {n.id for n in g.nodes if n.kind == "source_decl"}
    sym_ids = {n.id for n in g.nodes if n.kind == "binary_symbol"}
    assert map_edges[0].src in decl_ids
    assert map_edges[0].dst in sym_ids


def test_source_abi_type_kind_dispatch() -> None:
    g = build_source_graph(BuildEvidence(), source_abi=_sample_surface())
    kinds = {n.label: n.kind for n in g.nodes}
    assert kinds["foo::Widget"] == "record_type"
    assert kinds["foo::Color"] == "enum_type"
    assert kinds["foo::Alias"] == "typedef"
    assert kinds["FOO_VERSION"] == "macro"


def test_source_abi_coverage_counts_decls_and_mappings() -> None:
    g = build_source_graph(BuildEvidence(), source_abi=_sample_surface())
    assert g.coverage["source_decls"] == 1
    assert g.coverage["binary_symbol_mappings"] == 1


def test_source_abi_decl_without_symbol_has_no_mapping_edge() -> None:
    s = SourceAbiSurface(library="l", target_id="target://t")
    s.reachable_declarations.append(_entity("foo::unshipped", "function"))
    # no entry in source_decl_to_binary_symbol
    g = build_source_graph(BuildEvidence(), source_abi=s)
    assert not any(e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL" for e in g.edges)
    assert any(n.kind == "source_decl" for n in g.nodes)


def test_source_abi_materializes_missing_target() -> None:
    # The surface names a target the (empty) build evidence never enumerated.
    g = build_source_graph(BuildEvidence(), source_abi=_sample_surface())
    target = next((n for n in g.nodes if n.id == "target://libfoo"), None)
    assert target is not None
    assert target.kind == "target"
    assert target.provenance == "source_abi"


def test_source_abi_edges_carry_source_provenance() -> None:
    g = build_source_graph(BuildEvidence(), source_abi=_sample_surface())
    src_edges = [e for e in g.edges if e.kind == "SOURCE_DECLARES"]
    assert src_edges
    assert all(e.provenance == "source_abi" for e in src_edges)


def test_source_abi_degenerate_inputs_handled() -> None:
    # No target_id (so no BINARY_EXPORTS_SYMBOL owner), a decl with no source
    # location (so no SOURCE_DECLARES edge), and a blank symbol mapping value
    # (skipped) must all be tolerated without error.
    s = SourceAbiSurface(library="l", target_id="")
    s.reachable_declarations.append(SourceEntity(
        id="d", kind="function", qualified_name="loose", source_location=None,
        confidence=LayerConfidence.UNKNOWN,
    ))
    s.mappings["source_decl_to_binary_symbol"] = {"loose": "", "other": "_Zsym"}
    g = build_source_graph(BuildEvidence(), source_abi=s)
    assert not any(e.kind == "SOURCE_DECLARES" for e in g.edges)
    assert not any(e.kind == "BINARY_EXPORTS_SYMBOL" for e in g.edges)
    # The blank mapping value is skipped; the real one becomes a symbol node.
    assert any(n.kind == "binary_symbol" and n.label == "_Zsym" for n in g.nodes)


def test_build_graph_without_surface_is_phase2_only() -> None:
    g = build_source_graph(_sample_build())
    assert not any(n.kind == "source_decl" for n in g.nodes)
    assert not any(e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL" for e in g.edges)


def test_source_abi_round_trip_and_determinism() -> None:
    s = _sample_surface()
    g = build_source_graph(BuildEvidence(), source_abi=s)
    assert SourceGraphSummary.from_dict(g.to_dict()).compute_graph_id() == g.compute_graph_id()
    assert build_source_graph(BuildEvidence(), source_abi=s).graph_id == g.graph_id


# ── Phase 5: graph-derived risk findings (D6) ───────────────────────────────


def _surface_with(decls, mapping, *, generated_header=None,
                  target="target://libfoo") -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id=target)
    for qn, path in decls:
        s.reachable_declarations.append(SourceEntity(
            id=qn, kind="function", qualified_name=qn,
            source_location=SourceLocation(path=path, line=1, origin="PUBLIC_HEADER"),
            visibility="public_header", confidence=LayerConfidence.HIGH,
        ))
    s.mappings["source_decl_to_binary_symbol"] = dict(mapping)
    return s


def _build_with_public_header(headers=("inc/foo.h",), generated=()) -> BuildEvidence:
    b = BuildEvidence(generated_files=list(generated))
    b.targets.append(Target(
        id="target://libfoo", public_headers=list(headers), confidence=Confidence.HIGH,
    ))
    return b


def test_all_three_graph_kinds_are_risk() -> None:
    for k in (ChangeKind.PUBLIC_REACHABILITY_CHANGED,
              ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED,
              ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API):
        assert k in RISK_KINDS


def test_findings_mapping_changed_for_persisting_decl() -> None:
    b = _build_with_public_header()
    old = build_source_graph(b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb"}))
    new = build_source_graph(b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb2"}))
    findings = diff_source_graph_findings(old, new)
    assert len(findings) == 1
    c = findings[0]
    assert c.kind == ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED
    assert c.old_value == "_Zb" and c.new_value == "_Zb2"
    assert c.source_location == f"[{EVIDENCE_TIER_L5}]"


def test_findings_reachability_entered_and_left() -> None:
    b = _build_with_public_header()
    old = build_source_graph(b, source_abi=_surface_with(
        [("foo::a", "inc/foo.h"), ("foo::gone", "inc/foo.h")], {"foo::a": "_Za"}))
    new = build_source_graph(b, source_abi=_surface_with(
        [("foo::a", "inc/foo.h"), ("foo::new", "inc/foo.h")], {"foo::a": "_Za"}))
    kinds_syms = {(c.kind, c.symbol) for c in diff_source_graph_findings(old, new)}
    assert (ChangeKind.PUBLIC_REACHABILITY_CHANGED, "foo::new") in kinds_syms
    assert (ChangeKind.PUBLIC_REACHABILITY_CHANGED, "foo::gone") in kinds_syms


def test_findings_empty_baseline_does_not_spam_reachability() -> None:
    # An empty old graph must not flag every new declaration as "entered".
    b = _build_with_public_header()
    new = build_source_graph(b, source_abi=_surface_with([("foo::a", "inc/foo.h")], {"foo::a": "_Za"}))
    findings = diff_source_graph_findings(SourceGraphSummary(), new)
    assert not any(c.kind == ChangeKind.PUBLIC_REACHABILITY_CHANGED for c in findings)


def test_findings_generated_header_reaches_public_api() -> None:
    # A public header that is also a generated file → reaches public API.
    old = build_source_graph(_build_with_public_header(headers=("inc/foo.h",)))
    new = build_source_graph(_build_with_public_header(
        headers=("inc/foo.h", "gen/config.h"), generated=("gen/config.h",)))
    findings = diff_source_graph_findings(old, new)
    gen = [c for c in findings if c.kind == ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API]
    assert len(gen) == 1
    assert "gen/config.h" in gen[0].symbol


def test_findings_identical_graphs_yield_nothing() -> None:
    b = _build_with_public_header()
    g = build_source_graph(b, source_abi=_surface_with([("foo::a", "inc/foo.h")], {"foo::a": "_Za"}))
    assert diff_source_graph_findings(g, g) == []


def test_compare_graph_cli_surfaces_findings(tmp_path) -> None:
    b = _build_with_public_header()
    old = build_source_graph(b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb"}))
    new = build_source_graph(b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb2"}))
    op, np = tmp_path / "o.json", tmp_path / "n.json"
    op.write_text(json.dumps(old.to_dict()))
    np.write_text(json.dumps(new.to_dict()))

    res = CliRunner().invoke(main, ["compare-graph", str(op), str(np)])
    assert res.exit_code == 0, res.output
    assert "Graph-derived risk findings" in res.output
    assert "source_to_binary_mapping_changed" in res.output

    res_json = CliRunner().invoke(main, ["compare-graph", str(op), str(np), "--format", "json"])
    payload = json.loads(res_json.output)
    assert payload["findings"][0]["kind"] == "source_to_binary_mapping_changed"


# ── Finalize: build-option→symbol flow, include drift, localization ─────────


def test_build_option_reaches_public_symbol_edges_and_finding() -> None:
    def _build(flags):
        b = BuildEvidence()
        b.targets.append(Target(id="target://libfoo", public_headers=["inc/foo.h"],
                                confidence=Confidence.HIGH))
        b.compile_units.append(CompileUnit(
            id="cu://foo", source="src/foo.cpp", target_id="target://libfoo",
            abi_relevant_flags=flags))
        return b

    surf = _surface_with([("foo::a", "inc/foo.h")], {"foo::a": "_Za"})
    old = build_source_graph(_build(["-std=c++20"]), source_abi=surf)
    new = build_source_graph(_build(["-std=c++20", "-fvisibility=hidden"]), source_abi=surf)
    assert any(e.kind == "BUILD_OPTION_AFFECTS_SYMBOL" for e in new.edges)
    bo = [c for c in diff_source_graph_findings(old, new)
          if c.kind == ChangeKind.BUILD_OPTION_REACHES_PUBLIC_SYMBOL]
    assert len(bo) == 1
    assert "-fvisibility=hidden" in bo[0].symbol
    assert bo[0].source_location == f"[{EVIDENCE_TIER_L5}]"


def test_build_option_reaches_public_symbol_ignores_reused_flag_on_new_target() -> None:
    # A new target reusing a pre-existing flag must NOT raise the finding — that
    # is symbol-level churn, not flag drift (only a *new* flag is interesting).
    def _build(targets):
        b = BuildEvidence()
        for tid, hdr in targets:
            b.targets.append(Target(id=tid, public_headers=[hdr], confidence=Confidence.HIGH))
            b.compile_units.append(CompileUnit(
                id=f"cu://{tid}", source=f"src/{tid}.cpp", target_id=tid,
                abi_relevant_flags=["-std=c++20"]))
        return b

    old_surf = _surface_with([("foo::a", "inc/foo.h")], {"foo::a": "_Za"}, target="target://foo")
    new_surf = _surface_with([("bar::b", "inc/bar.h")], {"bar::b": "_Zb"}, target="target://bar")
    old = build_source_graph(_build([("target://foo", "inc/foo.h")]), source_abi=old_surf)
    new = build_source_graph(
        _build([("target://foo", "inc/foo.h"), ("target://bar", "inc/bar.h")]), source_abi=new_surf)
    bo = [c for c in diff_source_graph_findings(old, new)
          if c.kind == ChangeKind.BUILD_OPTION_REACHES_PUBLIC_SYMBOL]
    # -std=c++20 already existed in the old graph → no flag-drift finding.
    assert bo == []


def test_include_graph_public_header_drift_finding() -> None:
    from abicheck.buildsource.include_graph import augment_graph_with_includes

    b = BuildEvidence()
    b.targets.append(Target(id="target://libfoo", public_headers=["inc/foo.h"],
                            confidence=Confidence.HIGH))
    b.compile_units.append(CompileUnit(id="cu://foo", source="src/foo.cpp",
                                       target_id="target://libfoo"))
    old = build_source_graph(b)
    new = build_source_graph(b)
    augment_graph_with_includes(new, {"cu://foo": ["inc/foo.h"]})
    new.finalize()
    inc = [c for c in diff_source_graph_findings(old, new)
           if c.kind == ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT]
    assert len(inc) == 1
    assert inc[0].symbol == "inc/foo.h"


def test_localize_symbol_walks_the_graph() -> None:
    from abicheck.buildsource.source_graph import localize_symbol

    b = BuildEvidence()
    b.targets.append(Target(id="target://libfoo", public_headers=["include/foo.h"],
                            confidence=Confidence.HIGH))
    g = build_source_graph(b, source_abi=_sample_surface())
    result = localize_symbol(g, "_ZN3foo3barEv")
    assert result["found"] is True
    assert "target://libfoo" in result["exported_by_targets"]
    assert "foo::bar" in result["source_declarations"]
    assert any("foo.h" in h for h in result["declared_in_headers"])


def test_localize_symbol_absent_returns_empty() -> None:
    from abicheck.buildsource.source_graph import localize_symbol

    result = localize_symbol(build_source_graph(BuildEvidence()), "_Zmissing")
    assert result["found"] is False
    assert result["exported_by_targets"] == []


def test_explain_finding_cli(tmp_path) -> None:
    b = BuildEvidence()
    b.targets.append(Target(id="target://libfoo", public_headers=["include/foo.h"],
                            confidence=Confidence.HIGH))
    g = build_source_graph(b, source_abi=_sample_surface())
    graph_json = tmp_path / "g.json"
    graph_json.write_text(json.dumps(g.to_dict()))

    res = CliRunner().invoke(main, [
        "explain-finding", "--sources", str(graph_json), "--symbol", "_ZN3foo3barEv",
    ])
    assert res.exit_code == 0, res.output
    assert "target://libfoo" in res.output
    assert "foo::bar" in res.output

    res_json = CliRunner().invoke(main, [
        "explain-finding", "--sources", str(graph_json),
        "--symbol", "_ZN3foo3barEv", "--format", "json",
    ])
    payload = json.loads(res_json.output)
    assert payload["found"] is True
    assert "foo::bar" in payload["source_declarations"]


def test_explain_finding_resolves_symbol_from_report(tmp_path) -> None:
    b = BuildEvidence()
    b.targets.append(Target(id="target://libfoo", public_headers=["include/foo.h"],
                            confidence=Confidence.HIGH))
    g = build_source_graph(b, source_abi=_sample_surface())
    graph_json = tmp_path / "g.json"
    graph_json.write_text(json.dumps(g.to_dict()))
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"changes": [{"symbol": "_ZN3foo3barEv"}]}))

    res = CliRunner().invoke(main, [
        "explain-finding", "--sources", str(graph_json),
        "--report", str(report), "--finding-id", "0", "--format", "json",
    ])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["symbol"] == "_ZN3foo3barEv"


def test_explain_finding_requires_a_symbol(tmp_path) -> None:
    g = build_source_graph(BuildEvidence())
    graph_json = tmp_path / "g.json"
    graph_json.write_text(json.dumps(g.to_dict()))
    res = CliRunner().invoke(main, ["explain-finding", "--sources", str(graph_json)])
    assert res.exit_code != 0
    assert "No symbol to explain" in res.output


def test_resolve_symbol_from_report_variants(tmp_path) -> None:
    from abicheck.cli_buildsource import _resolve_symbol_from_report

    report = tmp_path / "r.json"
    report.write_text(json.dumps({"changes": [
        {"symbol": "_ZN3foo3barEv"}, {"symbol": "_ZN3foo3bazEv"},
    ]}))
    # index lookup
    assert _resolve_symbol_from_report(report, "1") == "_ZN3foo3bazEv"
    # substring match
    assert _resolve_symbol_from_report(report, "bar") == "_ZN3foo3barEv"
    # out-of-range index → empty
    assert _resolve_symbol_from_report(report, "9") == ""
    # no match → empty
    assert _resolve_symbol_from_report(report, "nope") == ""


def test_resolve_symbol_from_report_unreadable(tmp_path) -> None:
    import click
    import pytest

    from abicheck.cli_buildsource import _resolve_symbol_from_report

    with pytest.raises(click.ClickException):
        _resolve_symbol_from_report(tmp_path / "missing.json", "0")


# ── Phase 1: schema round-trip + content addressing ─────────────────────────


def test_round_trip_preserves_graph_id() -> None:
    g = build_source_graph(_sample_build())
    restored = SourceGraphSummary.from_dict(g.to_dict())
    assert restored.compute_graph_id() == g.compute_graph_id()
    assert len(restored.nodes) == len(g.nodes)
    assert len(restored.edges) == len(g.edges)


def test_graph_id_order_independent() -> None:
    a = SourceGraphSummary()
    a.add_node(GraphNode(id="x", kind="target"))
    a.add_node(GraphNode(id="y", kind="source"))
    a.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    b = SourceGraphSummary()
    b.add_node(GraphNode(id="y", kind="source"))
    b.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    b.add_node(GraphNode(id="x", kind="target"))
    assert a.compute_graph_id() == b.compute_graph_id()


def test_add_node_and_edge_dedupe() -> None:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    g.add_node(GraphNode(id="x", kind="target"))
    g.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    g.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    assert len(g.nodes) == 1
    assert len(g.edges) == 1


def test_from_dict_forward_compatible_with_unknown_fields() -> None:
    # A hand-edited / newer summary with an unknown node kind and extra keys
    # must load, not abort (evidence/CLAUDE.md forward-compat rule).
    data = {
        "schema_version": SOURCE_GRAPH_VERSION + 99,
        "nodes": [{"id": "n1", "kind": "future_kind", "future_attr": 1}],
        "edges": [{"edge": "FUTURE_EDGE", "src": "n1", "dst": "n2"}],
        "unknown_top_level": True,
    }
    g = SourceGraphSummary.from_dict(data)
    assert g.nodes[0].kind == "future_kind"
    assert g.edges[0].kind == "FUTURE_EDGE"


def test_indexes_localize_by_target_and_file() -> None:
    g = build_source_graph(_sample_build())
    idx = g.to_dict()["indexes"]
    assert "target://libfoo" in idx["by_target"]
    assert any(k.startswith("header://") for k in idx["by_file"])


def test_indexes_cover_forward_looking_symbol_and_decl_kinds() -> None:
    # Phases 3-4 will emit binary_symbol / source_decl nodes; the index already
    # localizes by them so a finding can be traced once those land.
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="decl://foo", kind="source_decl"))
    g.add_node(GraphNode(id="sym://_Z3foov", kind="binary_symbol"))
    g.add_edge(GraphEdge(src="decl://foo", dst="sym://_Z3foov",
                         kind="SOURCE_DECL_MAPS_TO_SYMBOL"))
    idx = g.indexes()
    assert "sym://_Z3foov" in idx["by_binary_symbol"]
    assert "decl://foo" in idx["by_source_decl"]


def test_to_dict_fills_graph_id_when_unset() -> None:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    assert g.graph_id == ""               # not finalized
    assert g.to_dict()["graph_id"].startswith("sha256:")


# ── Phase 5 seed: structural diff ───────────────────────────────────────────


def test_diff_detects_added_and_removed() -> None:
    old = build_source_graph(_sample_build())
    b2 = _sample_build()
    b2.targets.append(Target(id="target://libbaz", name="baz"))
    new = build_source_graph(b2)
    delta = diff_source_graph(old, new)
    assert delta.changed
    assert any(n.id == "target://libbaz" for n in delta.added_nodes)
    assert not delta.removed_nodes


def test_diff_identical_graphs_no_change() -> None:
    g = build_source_graph(_sample_build())
    delta = diff_source_graph(g, g)
    assert not delta.changed
    assert delta.to_dict()["counts"]["added_nodes"] == 0


# ── Pack + CLI wiring ───────────────────────────────────────────────────────


def test_pack_round_trips_source_graph(tmp_path) -> None:
    pack = BuildSourcePack.empty(tmp_path / "p.evidence")
    pack.source_graph = build_source_graph(_sample_build())
    pack.write()
    loaded = BuildSourcePack.load(tmp_path / "p.evidence")
    assert loaded.source_graph is not None
    assert loaded.source_graph.graph_id == pack.source_graph.graph_id


def test_pack_drops_stale_graph_when_recollected(tmp_path) -> None:
    root = tmp_path / "p.evidence"
    pack = BuildSourcePack.empty(root)
    pack.source_graph = build_source_graph(_sample_build())
    pack.write()
    # Re-write without a graph: the stale file must be removed.
    pack2 = BuildSourcePack.load(root)
    pack2.source_graph = None
    pack2.write()
    assert not (root / "graph" / "source_graph_summary.json").is_file()
    assert BuildSourcePack.load(root).source_graph is None


def test_collect_evidence_summary_writes_graph_and_coverage(tmp_path) -> None:
    cdb = tmp_path / "compile_commands.json"
    src = tmp_path / "foo.cpp"
    src.write_text("int foo(){return 1;}\n")
    cdb.write_text(json.dumps([{
        "directory": str(tmp_path), "file": str(src),
        "command": f"c++ -std=c++20 -fvisibility=hidden -c {src} -o foo.o",
    }]))
    out = tmp_path / "out.evidence"
    res = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb),
        "--source-graph", "summary", "-o", str(out),
    ])
    assert res.exit_code == 0, res.output
    assert (out / "graph" / "source_graph_summary.json").is_file()
    pack = BuildSourcePack.load(out)
    assert pack.source_graph is not None
    l5 = pack.manifest.coverage_for(DataLayer.L5_SOURCE_GRAPH)
    assert l5 is not None
    assert l5.status == CoverageStatus.PRESENT


def test_compare_graph_cli_reports_diff(tmp_path) -> None:
    old = SourceGraphSummary()
    old.add_node(GraphNode(id="target://a", kind="target", label="a"))
    new = build_source_graph(_sample_build())
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(json.dumps(old.to_dict()))
    new_path.write_text(json.dumps(new.to_dict()))

    res = CliRunner().invoke(main, ["compare-graph", str(old_path), str(new_path)])
    assert res.exit_code == 0, res.output
    assert "structural diff" in res.output

    res_json = CliRunner().invoke(
        main, ["compare-graph", str(old_path), str(new_path), "--format", "json"]
    )
    assert res_json.exit_code == 0
    counts = json.loads(res_json.output)["counts"]
    assert counts["added_nodes"] >= 1


def test_compare_graph_identical(tmp_path) -> None:
    g = build_source_graph(_sample_build())
    p = tmp_path / "g.json"
    p.write_text(json.dumps(g.to_dict()))
    res = CliRunner().invoke(main, ["compare-graph", str(p), str(p)])
    assert res.exit_code == 0
    assert "structurally identical" in res.output


def test_compare_graph_missing_graph_errors(tmp_path) -> None:
    res = CliRunner().invoke(main, ["compare-graph", str(tmp_path / "nope.json"), str(tmp_path / "nope.json")])
    assert res.exit_code != 0


def _collect_pack(tmp_path, name: str, *, two_units: bool = False) -> str:
    """Run `collect --source-graph summary` and return the pack dir."""
    src = tmp_path / f"{name}.cpp"
    src.write_text("int x(){return 1;}\n")
    entries = [{
        "directory": str(tmp_path), "file": str(src),
        "command": f"c++ -std=c++20 -fvisibility=hidden -c {src} -o {name}.o",
    }]
    if two_units:
        src2 = tmp_path / f"{name}2.cpp"
        src2.write_text("int y(){return 2;}\n")
        entries.append({
            "directory": str(tmp_path), "file": str(src2),
            "command": f"c++ -std=c++20 -c {src2} -o {name}2.o",
        })
    cdb = tmp_path / f"{name}_cc.json"
    cdb.write_text(json.dumps(entries))
    out = tmp_path / f"{name}.evidence"
    res = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb),
        "--source-graph", "summary", "-o", str(out),
    ])
    assert res.exit_code == 0, res.output
    return str(out)


def test_compare_graph_accepts_pack_directories_and_shows_removals(tmp_path) -> None:
    # The richer pack as OLD and the smaller as NEW exercises the removed-node /
    # removed-edge rendering branches of the text output.
    big = _collect_pack(tmp_path, "big", two_units=True)
    small = _collect_pack(tmp_path, "small", two_units=False)
    res = CliRunner().invoke(main, ["compare-graph", big, small])
    assert res.exit_code == 0, res.output
    assert "- node" in res.output or "- edge" in res.output


def test_compare_graph_pack_without_graph_errors(tmp_path) -> None:
    # A pack collected without --source-graph has no L5 graph → actionable error.
    cdb = tmp_path / "cc.json"
    src = tmp_path / "z.cpp"
    src.write_text("int z(){return 0;}\n")
    cdb.write_text(json.dumps([{
        "directory": str(tmp_path), "file": str(src),
        "command": f"c++ -c {src} -o z.o",
    }]))
    out = tmp_path / "nograph.evidence"
    assert CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb), "-o", str(out),
    ]).exit_code == 0
    res = CliRunner().invoke(main, ["compare-graph", str(out), str(out)])
    assert res.exit_code != 0
    assert "no L5 source graph" in res.output


def test_compare_graph_malformed_json_errors(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    res = CliRunner().invoke(main, ["compare-graph", str(bad), str(bad)])
    assert res.exit_code != 0
    assert "Cannot read source graph" in res.output


def test_compare_graph_non_object_json_errors(tmp_path) -> None:
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]")
    res = CliRunner().invoke(main, ["compare-graph", str(arr), str(arr)])
    assert res.exit_code != 0
    assert "must contain a JSON object" in res.output


def test_compare_graph_rejects_non_graph_json_object(tmp_path) -> None:
    # An unrelated JSON object (e.g. a pack manifest) must fail with an
    # actionable error, not be read as an empty graph (CodeRabbit review).
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"build_source_pack_version": 1, "coverage": []}))
    res = CliRunner().invoke(main, ["compare-graph", str(manifest), str(manifest)])
    assert res.exit_code != 0
    assert "not a source graph summary" in res.output


def test_collect_evidence_summary_without_build_is_partial(tmp_path) -> None:
    # --source-graph summary with no build adapter inputs yields an empty graph;
    # the L5 coverage row must read PARTIAL (ran, produced nothing), not PRESENT.
    out = tmp_path / "empty.evidence"
    res = CliRunner().invoke(main, [
        "collect", "--source-graph", "summary", "-o", str(out),
    ])
    assert res.exit_code == 0, res.output
    pack = BuildSourcePack.load(out)
    assert pack.source_graph is not None
    l5 = pack.manifest.coverage_for(DataLayer.L5_SOURCE_GRAPH)
    assert l5 is not None
    assert l5.status == CoverageStatus.PARTIAL
