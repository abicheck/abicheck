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

"""Tests for ADR-031 phase 6: the Clang direct-call AST parser, graph
augmentation, the call-reachability finding, and graceful clang-absent degrade.

The parser is exercised against hand-built ``clang -ast-dump=json`` trees so no
compiler is required; the live subprocess path is integration-only."""

from __future__ import annotations

from abicheck.checker_policy import COMPATIBLE_KINDS, ChangeKind
from abicheck.evidence.build_evidence import BuildEvidence, CompileUnit
from abicheck.evidence.call_graph import (
    CALL_KIND_DIRECT,
    CALL_KIND_FUNCTION_POINTER,
    CALL_KIND_VIRTUAL,
    RESOLUTION_EXACT,
    RESOLUTION_OVERAPPROX,
    RESOLUTION_UNKNOWN,
    CallEdge,
    ClangCallGraphExtractor,
    augment_graph_with_calls,
    parse_clang_ast_calls,
)
from abicheck.evidence.source_graph import (
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    diff_source_graph_findings,
)


def _ref(kind: str, name: str, mangled: str = "", *, virtual: bool = False) -> dict:
    d: dict = {"kind": kind, "name": name}
    if mangled:
        d["mangledName"] = mangled
    if virtual:
        d["virtual"] = True
    return d


def _direct_call(callee: dict) -> dict:
    return {"kind": "CallExpr", "inner": [
        {"kind": "ImplicitCastExpr", "inner": [
            {"kind": "DeclRefExpr", "referencedDecl": callee}]}]}


def _member_call(member: dict) -> dict:
    return {"kind": "CXXMemberCallExpr", "inner": [
        {"kind": "MemberExpr", "referencedMemberDecl": member}]}


def _func(name: str, mangled: str, body: list[dict]) -> dict:
    return {"kind": "FunctionDecl", "name": name, "mangledName": mangled,
            "inner": [{"kind": "CompoundStmt", "inner": body}]}


# ── parser ──────────────────────────────────────────────────────────────────


def test_parse_direct_call() -> None:
    ast = {"kind": "TranslationUnitDecl", "inner": [
        _func("caller", "_Zcaller", [_direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))]),
    ]}
    edges = parse_clang_ast_calls(ast)
    assert edges == [CallEdge("_Zcaller", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)]


def test_parse_virtual_call_is_overapprox() -> None:
    ast = {"kind": "TranslationUnitDecl", "inner": [
        _func("c", "_Zc", [_member_call(_ref("CXXMethodDecl", "v", "_Zv", virtual=True))]),
    ]}
    e = parse_clang_ast_calls(ast)[0]
    assert e.call_kind == CALL_KIND_VIRTUAL
    assert e.resolution == RESOLUTION_OVERAPPROX
    assert e.confidence() == "reduced"


def test_parse_function_pointer_call_is_unknown() -> None:
    ast = {"kind": "TranslationUnitDecl", "inner": [
        _func("c", "_Zc", [_direct_call(_ref("ParmVarDecl", "fp"))]),
    ]}
    e = parse_clang_ast_calls(ast)[0]
    assert e.call_kind == CALL_KIND_FUNCTION_POINTER
    assert e.resolution == RESOLUTION_UNKNOWN
    assert e.callee == "fp"


def test_parse_unresolved_callee_dropped() -> None:
    # A CallExpr with no referenced decl (e.g. through a complex expression).
    ast = {"kind": "TranslationUnitDecl", "inner": [
        _func("c", "_Zc", [{"kind": "CallExpr", "inner": [{"kind": "ParenExpr"}]}]),
    ]}
    assert parse_clang_ast_calls(ast) == []


def test_parse_tolerates_non_dict_inner_nodes() -> None:
    # A malformed AST with non-dict entries in `inner` must not crash.
    ast = {"kind": "TranslationUnitDecl", "inner": [
        None, "stray",
        {"kind": "FunctionDecl", "name": "c", "mangledName": "_Zc", "inner": [
            None, _direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))]},
    ]}
    assert parse_clang_ast_calls(ast) == [CallEdge("_Zc", "_Zcallee")]


def test_parse_finds_ref_in_later_sibling() -> None:
    # First child subtree has no referenced decl; the callee is in a later one.
    call = {"kind": "CallExpr", "inner": [
        {"kind": "ParenExpr", "inner": [{"kind": "IntegerLiteral"}]},
        {"kind": "DeclRefExpr", "referencedDecl": _ref("FunctionDecl", "callee", "_Zcallee")},
    ]}
    ast = {"kind": "TranslationUnitDecl", "inner": [_func("c", "_Zc", [call])]}
    assert parse_clang_ast_calls(ast) == [CallEdge("_Zc", "_Zcallee")]


def test_parse_call_outside_function_ignored() -> None:
    # A call not nested in any function decl has no caller → dropped.
    ast = {"kind": "TranslationUnitDecl", "inner": [
        _direct_call(_ref("FunctionDecl", "callee", "_Zcallee")),
    ]}
    assert parse_clang_ast_calls(ast) == []


def test_parse_dedupes_repeated_edges() -> None:
    call = _direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))
    ast = {"kind": "TranslationUnitDecl", "inner": [_func("c", "_Zc", [call, call])]}
    assert len(parse_clang_ast_calls(ast)) == 1


def test_parse_uses_name_when_no_mangled() -> None:
    ast = {"kind": "TranslationUnitDecl", "inner": [
        {"kind": "FunctionDecl", "name": "caller", "inner": [
            {"kind": "CompoundStmt", "inner": [_direct_call(_ref("FunctionDecl", "callee"))]}]},
    ]}
    e = parse_clang_ast_calls(ast)[0]
    assert e.caller == "caller" and e.callee == "callee"


def test_parse_self_recursive_call_skipped() -> None:
    ast = {"kind": "TranslationUnitDecl", "inner": [
        _func("rec", "_Zrec", [_direct_call(_ref("FunctionDecl", "rec", "_Zrec"))]),
    ]}
    assert parse_clang_ast_calls(ast) == []


def test_parse_referenced_decl_without_name_dropped() -> None:
    # A referenced decl with no name/mangled yields an empty callee → dropped.
    ast = {"kind": "TranslationUnitDecl", "inner": [
        _func("c", "_Zc", [_direct_call({"kind": "FunctionDecl"})]),
    ]}
    assert parse_clang_ast_calls(ast) == []


def test_call_edge_confidence_labels() -> None:
    assert CallEdge("a", "b", CALL_KIND_DIRECT, RESOLUTION_EXACT).confidence() == "high"
    assert CallEdge("a", "b", CALL_KIND_VIRTUAL, RESOLUTION_OVERAPPROX).confidence() == "reduced"
    assert CallEdge("a", "b", CALL_KIND_FUNCTION_POINTER, RESOLUTION_UNKNOWN).confidence() == "unknown"


# ── graph augmentation ──────────────────────────────────────────────────────


def test_augment_adds_decl_calls_decl_edges_with_labels() -> None:
    g = SourceGraphSummary()
    added = augment_graph_with_calls(g, [
        CallEdge("_Za", "_Zb", CALL_KIND_VIRTUAL, RESOLUTION_OVERAPPROX),
    ])
    assert added == 1
    edge = next(e for e in g.edges if e.kind == "DECL_CALLS_DECL")
    assert edge.attrs == {"call_kind": "virtual", "resolution": "overapprox"}
    assert edge.confidence == "reduced"
    assert all(n.kind == "source_decl" for n in g.nodes)


def test_augment_merges_with_existing_decl_node() -> None:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="decl://_Zb", kind="source_decl", label="b", provenance="source_abi"))
    augment_graph_with_calls(g, [CallEdge("_Za", "_Zb")])
    # The callee reuses the existing decl node rather than duplicating it.
    assert sum(1 for n in g.nodes if n.id == "decl://_Zb") == 1


def test_augment_dedupes_edges() -> None:
    g = SourceGraphSummary()
    augment_graph_with_calls(g, [CallEdge("_Za", "_Zb")])
    added = augment_graph_with_calls(g, [CallEdge("_Za", "_Zb")])
    assert added == 0


# ── call-reachability finding (D6, quality) ─────────────────────────────────


def _graph_with_calls(entry_symbol: str, calls: list[tuple[str, str]]) -> SourceGraphSummary:
    g = SourceGraphSummary()
    # entry decl backs an exported symbol → it is a public entry point.
    g.add_node(GraphNode(id="decl://entry", kind="source_decl", label="entry"))
    g.add_node(GraphNode(id=f"binary_symbol://{entry_symbol}", kind="binary_symbol", label=entry_symbol))
    g.add_edge(GraphEdge(src="decl://entry", dst=f"binary_symbol://{entry_symbol}",
                         kind="SOURCE_DECL_MAPS_TO_SYMBOL"))
    augment_graph_with_calls(g, [CallEdge(c, d) for c, d in calls])
    return g.finalize()


def test_call_reachability_change_emits_quality_finding() -> None:
    old = _graph_with_calls("_Zentry", [("entry", "_Zimpl1")])
    new = _graph_with_calls("_Zentry", [("entry", "_Zimpl1"), ("_Zimpl1", "_Zimpl2")])
    findings = diff_source_graph_findings(old, new)
    cg = [c for c in findings if c.kind == ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED]
    assert len(cg) == 1
    assert cg[0].source_location == "[L5_SOURCE_GRAPH]"
    assert ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED in COMPATIBLE_KINDS


def test_no_call_edges_means_no_call_finding() -> None:
    # Graphs without DECL_CALLS_DECL edges must not emit the call finding.
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="decl://entry", kind="source_decl"))
    assert not any(
        c.kind == ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED
        for c in diff_source_graph_findings(g, g)
    )


# ── live extractor degrades gracefully ──────────────────────────────────────


def test_extractor_missing_clang_returns_empty() -> None:
    ext = ClangCallGraphExtractor(clang_bin="definitely-not-a-real-clang-xyz")
    assert ext.available() is False
    assert ext.extract_from_args(["foo.cpp"]) == []
    assert ext.extract_from_build(BuildEvidence(compile_units=[CompileUnit(id="cu://x", source="x.cpp")])) == []
    assert ext.diagnostics  # a reason was recorded


class _FakeProc:
    def __init__(self, stdout: str, stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr


def _patch_clang(monkeypatch, *, available: bool = True, proc=None, raises=None) -> None:
    import abicheck.evidence.call_graph as cg

    monkeypatch.setattr(cg.shutil, "which", lambda _b: "/usr/bin/clang++" if available else None)

    def fake_run(*_a, **_k):
        if raises is not None:
            raise raises
        return proc

    monkeypatch.setattr(cg.subprocess, "run", fake_run)


def test_extract_from_args_parses_mocked_clang(monkeypatch) -> None:
    import json as _json
    ast = {"kind": "TranslationUnitDecl", "inner": [
        _func("c", "_Zc", [_direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))]),
    ]}
    _patch_clang(monkeypatch, proc=_FakeProc(_json.dumps(ast)))
    edges = ClangCallGraphExtractor().extract_from_args(["x.cpp"])
    assert edges == [CallEdge("_Zc", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)]


def test_extract_from_args_empty_stdout(monkeypatch) -> None:
    _patch_clang(monkeypatch, proc=_FakeProc("", stderr="boom"))
    ext = ClangCallGraphExtractor()
    assert ext.extract_from_args(["x.cpp"]) == []
    assert any("no AST" in d for d in ext.diagnostics)


def test_extract_from_args_bad_json(monkeypatch) -> None:
    _patch_clang(monkeypatch, proc=_FakeProc("{not json"))
    ext = ClangCallGraphExtractor()
    assert ext.extract_from_args(["x.cpp"]) == []
    assert any("could not parse" in d for d in ext.diagnostics)


def test_extract_from_args_subprocess_error(monkeypatch) -> None:
    _patch_clang(monkeypatch, raises=OSError("no exec"))
    ext = ClangCallGraphExtractor()
    assert ext.extract_from_args(["x.cpp"]) == []
    assert any("invocation failed" in d for d in ext.diagnostics)


def test_extract_from_build_dedupes_across_units(monkeypatch) -> None:
    import json as _json
    ast = {"kind": "TranslationUnitDecl", "inner": [
        _func("c", "_Zc", [_direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))]),
    ]}
    _patch_clang(monkeypatch, proc=_FakeProc(_json.dumps(ast)))
    build = BuildEvidence(compile_units=[
        CompileUnit(id="cu://a", source="a.cpp", argv=["a.cpp"]),
        CompileUnit(id="cu://b", source="b.cpp", argv=["b.cpp"]),
        CompileUnit(id="cu://nosrc", source=""),  # skipped (no source)
    ])
    edges = ClangCallGraphExtractor().extract_from_build(build)
    assert edges == [CallEdge("_Zc", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)]


# ── collect-evidence --call-graph wiring (_collect_call_graph) ───────────────


class _FakeExtractor:
    """Stand-in for ClangCallGraphExtractor with a controllable result."""

    def __init__(self, *, available: bool, edges: list[CallEdge] | None = None,
                 clang_bin: str = "clang++") -> None:
        self.clang_bin = clang_bin
        self._available = available
        self._edges = edges or []
        self.diagnostics: list[str] = []

    def available(self) -> bool:
        return self._available

    def extract_from_build(self, _build: BuildEvidence) -> list[CallEdge]:
        return self._edges


def _patch_extractor(monkeypatch, fake: _FakeExtractor) -> None:
    import abicheck.evidence.call_graph as cg

    monkeypatch.setattr(cg, "ClangCallGraphExtractor", lambda **_k: fake)


def test_collect_call_graph_folds_edges_and_refinalizes(monkeypatch) -> None:
    from abicheck.cli_evidence import _collect_call_graph
    from abicheck.evidence.model import ExtractorRecord
    from abicheck.evidence.source_graph import build_source_graph

    _patch_extractor(monkeypatch, _FakeExtractor(available=True, edges=[CallEdge("_Za", "_Zb")]))
    graph = build_source_graph(BuildEvidence())
    records: list[ExtractorRecord] = []
    _collect_call_graph(graph, BuildEvidence(), records, clang_bin="clang")
    assert any(e.kind == "DECL_CALLS_DECL" for e in graph.edges)
    # coverage was re-finalized so the call-edge count is reflected.
    assert graph.coverage["call_edges"]["count"] == 1
    assert records[-1].name == "call_graph:clang" and records[-1].status == "ok"


def test_collect_call_graph_missing_clang_records_failure(monkeypatch) -> None:
    from abicheck.cli_evidence import _collect_call_graph
    from abicheck.evidence.model import ExtractorRecord
    from abicheck.evidence.source_graph import build_source_graph

    _patch_extractor(monkeypatch, _FakeExtractor(available=False))
    graph = build_source_graph(BuildEvidence())
    records: list[ExtractorRecord] = []
    _collect_call_graph(graph, BuildEvidence(), records, clang_bin="clang")
    assert not any(e.kind == "DECL_CALLS_DECL" for e in graph.edges)
    assert records[-1].status == "failed"


def test_collect_evidence_call_graph_flag_end_to_end(monkeypatch, tmp_path) -> None:
    # --call-graph implies --source-graph summary and folds call edges in.
    import json as _json

    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.evidence.pack import EvidencePack

    src = tmp_path / "foo.cpp"
    src.write_text("int foo(){return 1;}\n")
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(_json.dumps([{
        "directory": str(tmp_path), "file": str(src),
        "command": f"c++ -c {src} -o foo.o",
    }]))
    _patch_extractor(monkeypatch, _FakeExtractor(available=True, edges=[CallEdge("_Za", "_Zb")]))

    out = tmp_path / "out.evidence"
    res = CliRunner().invoke(main, [
        "collect-evidence", "--compile-db", str(cdb), "--call-graph", "-o", str(out),
    ])
    assert res.exit_code == 0, res.output
    pack = EvidencePack.load(out)
    assert pack.source_graph is not None
    assert any(e.kind == "DECL_CALLS_DECL" for e in pack.source_graph.edges)
