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


# ── engine wiring (was `collect --kythe-entries` / `--codeql-results`) ──────
#
# `collect` was deleted in the ADR-043 CLI reset, but the engine it drove is
# unchanged: `cli_buildsource_helpers._collect_source_graph` is the exact
# function the deleted Click command called to build the L5 graph and fold in
# Kythe/CodeQL exports, so these exercise it directly against a `BuildEvidence`
# built the same way (`_run_adapters` over a compile DB).


def _cdb(tmp_path):
    import json

    src = tmp_path / "foo.cpp"
    src.write_text("int foo(){return 1;}\n")
    cdb = tmp_path / "cc.json"
    cdb.write_text(json.dumps([{
        "directory": str(tmp_path), "file": str(src), "command": f"c++ -c {src} -o foo.o",
    }]))
    return cdb


def _merged_from_compile_db(tmp_path):
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.model import ExtractorRecord
    from abicheck.cli_buildsource_helpers import _run_adapters

    merged = BuildEvidence()
    extractors: list[ExtractorRecord] = []
    _run_adapters(
        merged,
        extractors,
        compile_db=_cdb(tmp_path),
        build_dir=None,
        cmake=False,
        ninja=False,
        ninja_compdb=None,
        bazel_cquery=None,
        bazel_aquery=None,
        make_dry_run=None,
        binary=None,
        read_compiler_record=False,
        build_system="generic",
        record_bazel_inputs=False,
        verbose=False,
    )
    return merged, extractors


def test_collect_evidence_kythe_entries_folds_edges(tmp_path) -> None:
    import json

    from abicheck.cli_buildsource_helpers import _collect_source_graph

    kythe = tmp_path / "kythe.json"
    kythe.write_text(json.dumps([
        {"edge_kind": "/kythe/edge/ref/call",
         "source": {"signature": "_Za"}, "target": {"signature": "_Zb"}},
    ]))
    merged, extractors = _merged_from_compile_db(tmp_path)
    # --source-graph defaults to "off"; --kythe-entries alone implicitly
    # promotes it to "summary" inside _collect_source_graph.
    graph, _detail = _collect_source_graph(
        merged, extractors,
        source_graph="off", changed_paths=(),
        kythe_entries=kythe, codeql_results=None,
        codeql_extends_results=None,
        surface=None, clang_bin="clang",
    )
    assert graph is not None
    assert any(e.kind == "DECL_CALLS_DECL" for e in graph.edges)
    assert graph.external_graph_refs and graph.external_graph_refs[0]["backend"] == "kythe"


def test_collect_evidence_codeql_results_folds_edges(tmp_path) -> None:
    import json

    from abicheck.cli_buildsource_helpers import _collect_source_graph

    codeql = tmp_path / "codeql.json"
    codeql.write_text(json.dumps({"#select": {"tuples": [["_Za", "_Zb"]]}}))
    merged, extractors = _merged_from_compile_db(tmp_path)
    graph, _detail = _collect_source_graph(
        merged, extractors,
        source_graph="off", changed_paths=(),
        kythe_entries=None, codeql_results=codeql,
        codeql_extends_results=None,
        surface=None, clang_bin="clang",
    )
    assert graph is not None and any(e.kind == "DECL_CALLS_DECL" for e in graph.edges)


def test_collect_evidence_codeql_extends_results_folds_edges(tmp_path) -> None:
    # ADR-041 P2 #4: a separate flag from --codeql-results since the raw
    # tuple shape carries no self-describing relation kind. ADR-043: `collect`
    # is gone, so this drives the surviving library function directly instead
    # of the deleted CLI command.
    import json

    from abicheck.cli_buildsource_helpers import _collect_source_graph

    codeql = tmp_path / "codeql-extends.json"
    codeql.write_text(json.dumps({"#select": {"tuples": [["Derived", "Base"]]}}))
    merged, extractors = _merged_from_compile_db(tmp_path)
    graph, _detail = _collect_source_graph(
        merged, extractors,
        source_graph="off", changed_paths=(),
        kythe_entries=None, codeql_results=None,
        codeql_extends_results=codeql,
        surface=None, clang_bin="clang",
    )
    assert graph is not None and any(e.kind == "TYPE_INHERITS" for e in graph.edges)
    assert graph.external_graph_refs and graph.external_graph_refs[0]["backend"] == "codeql"


def test_collect_evidence_codeql_extends_non_object_records_failed_extractor(
    tmp_path,
) -> None:
    # Codex review: valid JSON that isn't a top-level object (e.g. a bare
    # array) used to leave no ExtractorRecord at all, silently hiding that
    # the requested backend was never ingested.
    import json

    from abicheck.cli_buildsource_helpers import _collect_source_graph

    codeql = tmp_path / "codeql-extends.json"
    codeql.write_text(json.dumps(["not", "an", "object"]))
    merged, extractors = _merged_from_compile_db(tmp_path)
    _collect_source_graph(
        merged, extractors,
        source_graph="off", changed_paths=(),
        kythe_entries=None, codeql_results=None,
        codeql_extends_results=codeql,
        surface=None, clang_bin="clang",
    )
    record = next(
        e for e in extractors if e.name == "graph_backend:codeql_extends"
    )
    assert record.status == "failed"


def test_collect_evidence_malformed_backend_export_degrades(tmp_path) -> None:
    from abicheck.cli_buildsource_helpers import _collect_source_graph

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    merged, extractors = _merged_from_compile_db(tmp_path)
    # Malformed export must not abort collection; a graph is still produced.
    graph, _detail = _collect_source_graph(
        merged, extractors,
        source_graph="off", changed_paths=(),
        kythe_entries=bad, codeql_results=None,
        codeql_extends_results=None,
        surface=None, clang_bin="clang",
    )
    assert graph is not None


def test_collect_evidence_kythe_implied_graph_still_records_bazel_inputs(
    tmp_path,
) -> None:
    # Codex review: --kythe-entries/--codeql-results with the default
    # --source-graph off implicitly promotes to "summary" *inside*
    # _collect_source_graph, after a Bazel/aquery collection has already run.
    # The record_bazel_inputs decision (made before that promotion, by the
    # now-deleted collect_cmd) had to anticipate it — otherwise a Bazel/aquery
    # build's include-graph fold (automatic whenever source-abi evidence is
    # present) finds no recorded inputs and falls back to a live `clang -M`
    # pass that cannot run outside the execroot. Reproduced directly: passing
    # record_bazel_inputs=True to _run_adapters (as the deleted CLI would once
    # --kythe-entries is truthy, regardless of --source-abi-scope) must still
    # let the include-graph fold pick up the *recorded* inputs rather than
    # attempting a live clang pass.
    import json

    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_buildsource_helpers import _collect_source_graph, _run_adapters
    from tests.test_bazel_adapter import AQUERY

    aquery_file = tmp_path / "aquery.json"
    aquery_file.write_text(AQUERY)
    kythe = tmp_path / "kythe.json"
    kythe.write_text(json.dumps([]))

    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.model import ExtractorRecord

    merged = BuildEvidence()
    extractors: list[ExtractorRecord] = []
    _run_adapters(
        merged,
        extractors,
        compile_db=None,
        build_dir=None,
        cmake=False,
        ninja=False,
        ninja_compdb=None,
        bazel_cquery=None,
        bazel_aquery=aquery_file,
        make_dry_run=None,
        binary=None,
        read_compiler_record=False,
        build_system="generic",
        record_bazel_inputs=True,
        verbose=False,
    )
    # Stand-in for `--source-abi --source-abi-scope off`: a no-op replay scope
    # still produces an (empty) SourceAbiSurface, which is what makes
    # _collect_source_graph fold the include graph in below.
    surface = SourceAbiSurface(library="", target_id="")

    graph, _detail = _collect_source_graph(
        merged, extractors,
        source_graph="off", changed_paths=(),
        kythe_entries=kythe, codeql_results=None,
        codeql_extends_results=None,
        surface=surface, clang_bin="clang",
    )
    assert graph is not None
    assert any(
        e.name == "include_graph:recorded_inputs" and e.status == "ok"
        for e in extractors
    )
