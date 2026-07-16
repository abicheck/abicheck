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

"""Tests for ADR-031 D5 / phase 7 external graph backends (Kythe, CodeQL).

These ingest pre-captured exports (no Kythe/CodeQL required) into the
abicheck-owned graph schema."""

from __future__ import annotations

from abicheck.buildsource.graph_backends import (
    ingest_codeql_call_results,
    ingest_codeql_extends_results,
    ingest_kythe_entries,
)
from abicheck.buildsource.source_graph import SourceGraphSummary, _type_node_id


def test_kythe_call_and_ref_edges() -> None:
    g = SourceGraphSummary()
    added = ingest_kythe_entries(g, [
        {"edge_kind": "/kythe/edge/ref/call",
         "source": {"signature": "caller"}, "target": {"signature": "callee"}},
        {"edge_kind": "/kythe/edge/ref",
         "source": {"signature": "user"}, "target": {"signature": "type"}},
        {"edge_kind": "/kythe/edge/childof",  # not a ref edge → ignored
         "source": {"signature": "a"}, "target": {"signature": "b"}},
    ], ref="merged.kzip")
    assert added == 2
    kinds = {e.kind for e in g.edges}
    assert kinds == {"DECL_CALLS_DECL", "DECL_REFERENCES_DECL"}
    call = next(e for e in g.edges if e.kind == "DECL_CALLS_DECL")
    assert call.provenance == "kythe" and call.confidence == "reduced"
    assert call.attrs["resolution"] == "points_to"
    assert g.external_graph_refs == [
        {"backend": "kythe", "ref": "merged.kzip", "edges_ingested": 2, "confidence": "reduced"}
    ]


def test_kythe_uses_path_when_no_signature() -> None:
    g = SourceGraphSummary()
    ingest_kythe_entries(g, [
        {"edge_kind": "/kythe/edge/ref/call",
         "source": {"path": "a.cpp"}, "target": {"path": "b.cpp"}},
    ])
    assert any(n.label == "a.cpp" for n in g.nodes)


def test_kythe_skips_malformed_and_self_edges() -> None:
    g = SourceGraphSummary()
    added = ingest_kythe_entries(g, [
        "not a dict",
        {"edge_kind": "/kythe/edge/ref/call", "source": {}, "target": {"signature": "x"}},
        {"edge_kind": "/kythe/edge/ref/call",
         "source": {"signature": "s"}, "target": {"signature": "s"}},  # self
    ])
    assert added == 0


def test_kythe_extends_edge_maps_to_type_inherits() -> None:
    # ADR-041 P2 #4: Kythe's /kythe/edge/extends ("record extends record")
    # unambiguously matches TYPE_INHERITS -- src is the derived record,
    # target the base, exactly abicheck's own convention.
    g = SourceGraphSummary()
    added = ingest_kythe_entries(g, [
        {"edge_kind": "/kythe/edge/extends",
         "source": {"signature": "Derived"}, "target": {"signature": "Base"}},
    ], ref="merged.kzip")
    assert added == 1
    edge = next(e for e in g.edges if e.kind == "TYPE_INHERITS")
    assert edge.provenance == "kythe" and edge.confidence == "reduced"
    assert edge.attrs["role"] == "base"
    # Lands on the same type://.../record_type node scheme a standalone
    # type_graph.py replay uses, not decl:// -- so the two producers' nodes
    # for the same record merge instead of duplicating.
    assert edge.src == _type_node_id("Derived")
    assert edge.dst == _type_node_id("Base")
    node = next(n for n in g.nodes if n.id == _type_node_id("Derived"))
    assert node.kind == "record_type"


def test_kythe_extends_access_qualified_variant_also_maps() -> None:
    g = SourceGraphSummary()
    added = ingest_kythe_entries(g, [
        {"edge_kind": "/kythe/edge/extends/public",
         "source": {"signature": "Derived"}, "target": {"signature": "Base"}},
    ])
    assert added == 1
    assert g.edges[0].kind == "TYPE_INHERITS"


def test_kythe_edge_kind_merely_sharing_extends_prefix_is_not_matched() -> None:
    # Codex review: a plain startswith("/kythe/edge/extends") also accepted an
    # unrelated edge kind that merely shares the prefix textually.
    g = SourceGraphSummary()
    added = ingest_kythe_entries(g, [
        {"edge_kind": "/kythe/edge/extendsFoo",
         "source": {"signature": "Derived"}, "target": {"signature": "Base"}},
    ])
    assert added == 0


def test_codeql_tuples_with_string_and_label_cells() -> None:
    g = SourceGraphSummary()
    added = ingest_codeql_call_results(g, {"#select": {"tuples": [
        ["caller1", "callee1"],
        [{"label": "caller2"}, {"label": "callee2"}],
        ["x", "x"],            # self → skipped
        ["only-one"],          # too short → skipped
    ]}}, ref="codeql-db/")
    assert added == 2
    assert all(e.kind == "DECL_CALLS_DECL" and e.provenance == "codeql" for e in g.edges)
    assert g.external_graph_refs[0]["backend"] == "codeql"


def test_codeql_missing_select_is_empty() -> None:
    g = SourceGraphSummary()
    assert ingest_codeql_call_results(g, {"something": "else"}) == 0
    assert g.external_graph_refs[0]["edges_ingested"] == 0


def test_codeql_extends_results() -> None:
    # ADR-041 P2 #4: same raw tuple shape as the call-results ingester, but a
    # separate entry point since CodeQL's JSON carries no self-describing
    # relation kind -- the caller (not the shape) determines what a result
    # set means (a class-hierarchy query here, a call-graph query above).
    g = SourceGraphSummary()
    added = ingest_codeql_extends_results(g, {"#select": {"tuples": [
        ["Derived", "Base"],
        [{"label": "Derived2"}, {"label": "Base2"}],
        ["Same", "Same"],   # self -> skipped
    ]}}, ref="codeql-db/")
    assert added == 2
    assert all(e.kind == "TYPE_INHERITS" and e.provenance == "codeql" for e in g.edges)
    assert all(e.attrs["role"] == "base" for e in g.edges)
    assert g.external_graph_refs[0]["backend"] == "codeql"
    # Same node scheme as the Kythe path -- type://, not decl://.
    assert any(n.id == _type_node_id("Derived") and n.kind == "record_type" for n in g.nodes)


def test_backends_round_trip_through_summary() -> None:
    g = SourceGraphSummary()
    ingest_kythe_entries(g, [
        {"edge_kind": "/kythe/edge/ref/call",
         "source": {"signature": "a"}, "target": {"signature": "b"}},
    ], ref="k")
    restored = SourceGraphSummary.from_dict(g.finalize().to_dict())
    assert restored.external_graph_refs == g.external_graph_refs
    assert any(e.kind == "DECL_CALLS_DECL" for e in restored.edges)


# ── collect --kythe-entries / --codeql-results wiring ──────────────


def _cdb(tmp_path):
    import json

    src = tmp_path / "foo.cpp"
    src.write_text("int foo(){return 1;}\n")
    cdb = tmp_path / "cc.json"
    cdb.write_text(json.dumps([{
        "directory": str(tmp_path), "file": str(src), "command": f"c++ -c {src} -o foo.o",
    }]))
    return cdb


def test_collect_evidence_kythe_entries_folds_edges(tmp_path) -> None:
    import json

    from click.testing import CliRunner

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli import main

    kythe = tmp_path / "kythe.json"
    kythe.write_text(json.dumps([
        {"edge_kind": "/kythe/edge/ref/call",
         "source": {"signature": "_Za"}, "target": {"signature": "_Zb"}},
    ]))
    out = tmp_path / "ev"
    res = CliRunner().invoke(main, [
        "collect", "--compile-db", str(_cdb(tmp_path)),
        "--kythe-entries", str(kythe), "-o", str(out),
    ])
    assert res.exit_code == 0, res.output
    graph = BuildSourcePack.load(out).source_graph
    assert graph is not None
    assert any(e.kind == "DECL_CALLS_DECL" for e in graph.edges)
    assert graph.external_graph_refs and graph.external_graph_refs[0]["backend"] == "kythe"


def test_collect_evidence_codeql_results_folds_edges(tmp_path) -> None:
    import json

    from click.testing import CliRunner

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli import main

    codeql = tmp_path / "codeql.json"
    codeql.write_text(json.dumps({"#select": {"tuples": [["_Za", "_Zb"]]}}))
    out = tmp_path / "ev"
    res = CliRunner().invoke(main, [
        "collect", "--compile-db", str(_cdb(tmp_path)),
        "--codeql-results", str(codeql), "-o", str(out),
    ])
    assert res.exit_code == 0, res.output
    graph = BuildSourcePack.load(out).source_graph
    assert graph is not None and any(e.kind == "DECL_CALLS_DECL" for e in graph.edges)


def test_collect_evidence_codeql_extends_results_folds_edges(tmp_path) -> None:
    # ADR-041 P2 #4: a separate flag from --codeql-results since the raw
    # tuple shape carries no self-describing relation kind.
    import json

    from click.testing import CliRunner

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli import main

    codeql = tmp_path / "codeql-extends.json"
    codeql.write_text(json.dumps({"#select": {"tuples": [["Derived", "Base"]]}}))
    out = tmp_path / "ev"
    res = CliRunner().invoke(main, [
        "collect", "--compile-db", str(_cdb(tmp_path)),
        "--codeql-extends-results", str(codeql), "-o", str(out),
    ])
    assert res.exit_code == 0, res.output
    graph = BuildSourcePack.load(out).source_graph
    assert graph is not None and any(e.kind == "TYPE_INHERITS" for e in graph.edges)
    assert graph.external_graph_refs and graph.external_graph_refs[0]["backend"] == "codeql"


def test_collect_evidence_codeql_extends_non_object_records_failed_extractor(
    tmp_path,
) -> None:
    # Codex review: valid JSON that isn't a top-level object (e.g. a bare
    # array) used to leave no ExtractorRecord at all, silently hiding that
    # the requested backend was never ingested.
    import json

    from click.testing import CliRunner

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli import main

    codeql = tmp_path / "codeql-extends.json"
    codeql.write_text(json.dumps(["not", "an", "object"]))
    out = tmp_path / "ev"
    res = CliRunner().invoke(main, [
        "collect", "--compile-db", str(_cdb(tmp_path)),
        "--codeql-extends-results", str(codeql), "-o", str(out),
    ])
    assert res.exit_code == 0, res.output
    pack = BuildSourcePack.load(out)
    record = next(
        e for e in pack.manifest.extractors if e.name == "graph_backend:codeql_extends"
    )
    assert record.status == "failed"


def test_collect_evidence_malformed_backend_export_degrades(tmp_path) -> None:
    from click.testing import CliRunner

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli import main

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    out = tmp_path / "ev"
    res = CliRunner().invoke(main, [
        "collect", "--compile-db", str(_cdb(tmp_path)),
        "--kythe-entries", str(bad), "-o", str(out),
    ])
    # Malformed export must not abort collection; the pack is still written.
    assert res.exit_code == 0, res.output
    assert BuildSourcePack.load(out).source_graph is not None


def test_collect_evidence_kythe_implied_graph_still_records_bazel_inputs(
    tmp_path,
) -> None:
    # Codex review: --kythe-entries/--codeql-results with the default
    # --source-graph off implicitly promotes to "summary" *inside*
    # _collect_source_graph, after --from bazel-aquery has already run. The
    # record_bazel_inputs decision (made before that promotion) must
    # anticipate it — otherwise a Bazel/aquery build's include-graph fold
    # (now automatic whenever --source-abi is given) finds no recorded
    # inputs and falls back to a live `clang -M` pass that cannot run
    # outside the execroot.
    import json

    from click.testing import CliRunner

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli import main
    from tests.test_bazel_adapter import AQUERY

    aquery_file = tmp_path / "aquery.json"
    aquery_file.write_text(AQUERY)
    kythe = tmp_path / "kythe.json"
    kythe.write_text(json.dumps([]))
    out = tmp_path / "ev"
    res = CliRunner().invoke(
        main,
        [
            "collect",
            "--from",
            f"bazel-aquery={aquery_file}",
            "--source-abi",
            "--source-abi-scope",
            "off",
            "--kythe-entries",
            str(kythe),
            "-o",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    pack = BuildSourcePack.load(out)
    assert pack.source_graph is not None
    assert any(
        e.name == "include_graph:recorded_inputs" and e.status == "ok"
        for e in pack.manifest.extractors
    )
