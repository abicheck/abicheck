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

from abicheck.cli import main
from abicheck.evidence.build_evidence import (
    BuildEvidence,
    CompileUnit,
    Confidence,
    Target,
    TargetKind,
)
from abicheck.evidence.model import CoverageStatus, EvidenceLayer
from abicheck.evidence.pack import EvidencePack
from abicheck.evidence.source_graph import (
    EDGE_KINDS,
    NODE_KINDS,
    SOURCE_GRAPH_VERSION,
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    build_source_graph,
    diff_source_graph,
)


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
    pack = EvidencePack.empty(tmp_path / "p.evidence")
    pack.source_graph = build_source_graph(_sample_build())
    pack.write()
    loaded = EvidencePack.load(tmp_path / "p.evidence")
    assert loaded.source_graph is not None
    assert loaded.source_graph.graph_id == pack.source_graph.graph_id


def test_pack_drops_stale_graph_when_recollected(tmp_path) -> None:
    root = tmp_path / "p.evidence"
    pack = EvidencePack.empty(root)
    pack.source_graph = build_source_graph(_sample_build())
    pack.write()
    # Re-write without a graph: the stale file must be removed.
    pack2 = EvidencePack.load(root)
    pack2.source_graph = None
    pack2.write()
    assert not (root / "graph" / "source_graph_summary.json").is_file()
    assert EvidencePack.load(root).source_graph is None


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
        "collect-evidence", "--compile-db", str(cdb),
        "--source-graph", "summary", "-o", str(out),
    ])
    assert res.exit_code == 0, res.output
    assert (out / "graph" / "source_graph_summary.json").is_file()
    pack = EvidencePack.load(out)
    assert pack.source_graph is not None
    l5 = pack.manifest.coverage_for(EvidenceLayer.L5_SOURCE_GRAPH)
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
    """Run `collect-evidence --source-graph summary` and return the pack dir."""
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
        "collect-evidence", "--compile-db", str(cdb),
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
        "collect-evidence", "--compile-db", str(cdb), "-o", str(out),
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


def test_collect_evidence_summary_without_build_is_partial(tmp_path) -> None:
    # --source-graph summary with no build adapter inputs yields an empty graph;
    # the L5 coverage row must read PARTIAL (ran, produced nothing), not PRESENT.
    out = tmp_path / "empty.evidence"
    res = CliRunner().invoke(main, [
        "collect-evidence", "--source-graph", "summary", "-o", str(out),
    ])
    assert res.exit_code == 0, res.output
    pack = EvidencePack.load(out)
    assert pack.source_graph is not None
    l5 = pack.manifest.coverage_for(EvidenceLayer.L5_SOURCE_GRAPH)
    assert l5 is not None
    assert l5.status == CoverageStatus.PARTIAL
