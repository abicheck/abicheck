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

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.call_graph import (
    CALL_KIND_DIRECT,
    CALL_KIND_FUNCTION_POINTER,
    CALL_KIND_VIRTUAL,
    RESOLUTION_EXACT,
    RESOLUTION_OVERAPPROX,
    RESOLUTION_UNKNOWN,
    CallEdge,
    ClangCallGraphExtractor,
    _call_graph_jobs,
    augment_graph_with_calls,
    parse_clang_ast_calls,
)
from abicheck.buildsource.source_graph import (
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    diff_source_graph_findings,
)
from abicheck.checker_policy import COMPATIBLE_KINDS, ChangeKind


def _ref(kind: str, name: str, mangled: str = "", *, virtual: bool = False) -> dict:
    d: dict = {"kind": kind, "name": name}
    if mangled:
        d["mangledName"] = mangled
    if virtual:
        d["virtual"] = True
    return d


def _direct_call(callee: dict) -> dict:
    return {
        "kind": "CallExpr",
        "inner": [
            {
                "kind": "ImplicitCastExpr",
                "inner": [{"kind": "DeclRefExpr", "referencedDecl": callee}],
            }
        ],
    }


def _member_call(member: dict) -> dict:
    return {
        "kind": "CXXMemberCallExpr",
        "inner": [{"kind": "MemberExpr", "referencedMemberDecl": member}],
    }


def _func(name: str, mangled: str, body: list[dict]) -> dict:
    return {
        "kind": "FunctionDecl",
        "name": name,
        "mangledName": mangled,
        "inner": [{"kind": "CompoundStmt", "inner": body}],
    }


# ── parser ──────────────────────────────────────────────────────────────────


def _real_ref(node_id: str, name: str, qualtype: str) -> dict:
    """A *realistic* compact ``referencedDecl`` stub -- no ``mangledName``,
    matching real Clang 17/18 ``-ast-dump=json`` output (verified against a
    live compile of an overloaded ``int f(int)``/``double f(double)`` pair,
    latest-main Clang plugin review PR1b). Unlike ``_ref()`` (used by the
    other parser tests), this never attaches a mangled name, so it exercises
    the id-index fallback rather than the stub's own identity."""
    return {
        "kind": "FunctionDecl",
        "id": node_id,
        "name": name,
        "type": {"qualType": qualtype},
    }


def _call_via_ref(ref: dict) -> dict:
    return {
        "kind": "CallExpr",
        "inner": [
            {
                "kind": "ImplicitCastExpr",
                "inner": [{"kind": "DeclRefExpr", "referencedDecl": ref}],
            }
        ],
    }


def test_parse_resolves_overloaded_callee_via_id_index() -> None:
    """A real (mangledName-less) referencedDecl stub must resolve to the
    correct overload's mangled identity via the id-index built from the full
    FunctionDecl nodes elsewhere in the AST, not collapse both overloads onto
    the shared bare name "f" (PR1b)."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "id": "0x1",
                "name": "f",
                "mangledName": "_Z1fi",
                "type": {"qualType": "int (int)"},
            },
            {
                "kind": "FunctionDecl",
                "id": "0x2",
                "name": "f",
                "mangledName": "_Z1fd",
                "type": {"qualType": "double (double)"},
            },
            _func(
                "g",
                "_Z1gv",
                [
                    _call_via_ref(_real_ref("0x1", "f", "int (int)")),
                    _call_via_ref(_real_ref("0x2", "f", "double (double)")),
                ],
            ),
        ],
    }
    edges = parse_clang_ast_calls(ast)
    assert {e.callee for e in edges} == {"_Z1fi", "_Z1fd"}


def test_parse_resolves_via_prototype_seen_before_definition() -> None:
    """The id-index must resolve through a pure prototype (no body), not only
    a full definition -- a forward-declared function called before its own
    definition appears later in the TU."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "id": "0x1",
                "name": "helper",
                "mangledName": "_Z6helperi",
                "type": {"qualType": "int (int)"},
            },  # prototype only, no CompoundStmt
            _func(
                "caller",
                "_Zcaller",
                [_call_via_ref(_real_ref("0x1", "helper", "int (int)"))],
            ),
        ],
    }
    edges = parse_clang_ast_calls(ast)
    assert edges == [CallEdge("_Zcaller", "_Z6helperi")]


def test_parse_falls_back_to_bare_name_when_id_unindexed() -> None:
    """A referencedDecl whose id was never seen elsewhere in this AST (e.g. a
    system/library declaration clang did not include in full) still resolves
    to *something* -- the documented best-effort fallback -- rather than
    silently dropping the edge."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func(
                "caller",
                "_Zcaller",
                [_call_via_ref(_real_ref("0xdeadbeef", "puts", "int (const char *)"))],
            ),
        ],
    }
    edges = parse_clang_ast_calls(ast)
    assert edges == [CallEdge("_Zcaller", "puts")]


def test_parse_direct_call() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func(
                "caller",
                "_Zcaller",
                [_direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))],
            ),
        ],
    }
    edges = parse_clang_ast_calls(ast)
    assert edges == [
        CallEdge("_Zcaller", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)
    ]


def test_parse_virtual_call_is_overapprox() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func(
                "c",
                "_Zc",
                [_member_call(_ref("CXXMethodDecl", "v", "_Zv", virtual=True))],
            ),
        ],
    }
    e = parse_clang_ast_calls(ast)[0]
    assert e.call_kind == CALL_KIND_VIRTUAL
    assert e.resolution == RESOLUTION_OVERAPPROX
    assert e.confidence() == "reduced"


def test_parse_function_pointer_call_is_unknown() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func("c", "_Zc", [_direct_call(_ref("ParmVarDecl", "fp"))]),
        ],
    }
    e = parse_clang_ast_calls(ast)[0]
    assert e.call_kind == CALL_KIND_FUNCTION_POINTER
    assert e.resolution == RESOLUTION_UNKNOWN
    assert e.callee == "fp"


def test_parse_unresolved_callee_dropped() -> None:
    # A CallExpr with no referenced decl (e.g. through a complex expression).
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func("c", "_Zc", [{"kind": "CallExpr", "inner": [{"kind": "ParenExpr"}]}]),
        ],
    }
    assert parse_clang_ast_calls(ast) == []


def test_parse_tolerates_non_dict_inner_nodes() -> None:
    # A malformed AST with non-dict entries in `inner` must not crash.
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            None,
            "stray",
            {
                "kind": "FunctionDecl",
                "name": "c",
                "mangledName": "_Zc",
                "inner": [
                    None,
                    _direct_call(_ref("FunctionDecl", "callee", "_Zcallee")),
                ],
            },
        ],
    }
    assert parse_clang_ast_calls(ast) == [CallEdge("_Zc", "_Zcallee")]


def test_parse_finds_ref_in_later_sibling() -> None:
    # First child subtree has no referenced decl; the callee is in a later one.
    call = {
        "kind": "CallExpr",
        "inner": [
            {"kind": "ParenExpr", "inner": [{"kind": "IntegerLiteral"}]},
            {
                "kind": "DeclRefExpr",
                "referencedDecl": _ref("FunctionDecl", "callee", "_Zcallee"),
            },
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [_func("c", "_Zc", [call])]}
    assert parse_clang_ast_calls(ast) == [CallEdge("_Zc", "_Zcallee")]


def test_parse_call_outside_function_ignored() -> None:
    # A call not nested in any function decl has no caller → dropped.
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _direct_call(_ref("FunctionDecl", "callee", "_Zcallee")),
        ],
    }
    assert parse_clang_ast_calls(ast) == []


def test_parse_dedupes_repeated_edges() -> None:
    call = _direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))
    ast = {"kind": "TranslationUnitDecl", "inner": [_func("c", "_Zc", [call, call])]}
    assert len(parse_clang_ast_calls(ast)) == 1


def test_parse_uses_name_when_no_mangled() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "caller",
                "inner": [
                    {
                        "kind": "CompoundStmt",
                        "inner": [_direct_call(_ref("FunctionDecl", "callee"))],
                    }
                ],
            },
        ],
    }
    e = parse_clang_ast_calls(ast)[0]
    assert e.caller == "caller" and e.callee == "callee"


def test_parse_extern_c_caller_identity_matches_source_entity_fallback() -> None:
    # ADR-041 P1 #5 (Codex review): clang reports mangledName == name for an
    # extern "C"/C-linkage function -- SourceEntity.identity() treats that as
    # "no distinguishing mangled name" and falls back to
    # qualified_name#signature_hash instead of the bare name, so the
    # AST-replay caller identity must match rather than keying on the bare
    # name (which used to land the call edge on a different decl:// node
    # than the L4 surface's own SOURCE_DECLARES node for the same function).
    import hashlib

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "api",
                "mangledName": "api",
                "type": {"qualType": "void (void)"},
                "inner": [
                    {
                        "kind": "CompoundStmt",
                        "inner": [_direct_call(_ref("FunctionDecl", "helper"))],
                    }
                ],
            },
        ],
    }
    e = parse_clang_ast_calls(ast)[0]
    expected_hash = hashlib.sha256(b"sig\x00void (void)").hexdigest()
    assert e.caller == f"api#sha256:{expected_hash}"


def test_parse_namespaced_extern_c_style_caller_is_qualified() -> None:
    # Scope tracking (new in this fix) must qualify the fallback identity the
    # same way type_graph.py's own scope walk already does for types.
    import hashlib

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "NamespaceDecl",
                "name": "detail",
                "inner": [
                    {
                        "kind": "FunctionDecl",
                        "name": "api",
                        "mangledName": "api",
                        "type": {"qualType": "void (void)"},
                        "inner": [
                            {
                                "kind": "CompoundStmt",
                                "inner": [_direct_call(_ref("FunctionDecl", "helper"))],
                            }
                        ],
                    }
                ],
            },
        ],
    }
    e = parse_clang_ast_calls(ast)[0]
    expected_hash = hashlib.sha256(b"sig\x00void (void)").hexdigest()
    assert e.caller == f"detail::api#sha256:{expected_hash}"


def test_parse_self_recursive_call_skipped() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func("rec", "_Zrec", [_direct_call(_ref("FunctionDecl", "rec", "_Zrec"))]),
        ],
    }
    assert parse_clang_ast_calls(ast) == []


def test_parse_referenced_decl_without_name_dropped() -> None:
    # A referenced decl with no name/mangled yields an empty callee → dropped.
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func("c", "_Zc", [_direct_call({"kind": "FunctionDecl"})]),
        ],
    }
    assert parse_clang_ast_calls(ast) == []


def test_call_edge_confidence_labels() -> None:
    assert CallEdge("a", "b", CALL_KIND_DIRECT, RESOLUTION_EXACT).confidence() == "high"
    assert (
        CallEdge("a", "b", CALL_KIND_VIRTUAL, RESOLUTION_OVERAPPROX).confidence()
        == "reduced"
    )
    assert (
        CallEdge("a", "b", CALL_KIND_FUNCTION_POINTER, RESOLUTION_UNKNOWN).confidence()
        == "unknown"
    )


# ── graph augmentation ──────────────────────────────────────────────────────


def test_augment_adds_decl_calls_decl_edges_with_labels() -> None:
    g = SourceGraphSummary()
    added = augment_graph_with_calls(
        g,
        [
            CallEdge("_Za", "_Zb", CALL_KIND_VIRTUAL, RESOLUTION_OVERAPPROX),
        ],
    )
    assert added == 1
    edge = next(e for e in g.edges if e.kind == "DECL_CALLS_DECL")
    assert edge.attrs == {"call_kind": "virtual", "resolution": "overapprox"}
    assert edge.confidence == "reduced"
    assert all(n.kind == "source_decl" for n in g.nodes)


def test_augment_merges_with_existing_decl_node() -> None:
    g = SourceGraphSummary()
    g.add_node(
        GraphNode(
            id="decl://_Zb", kind="source_decl", label="b", provenance="source_abi"
        )
    )
    augment_graph_with_calls(g, [CallEdge("_Za", "_Zb")])
    # The callee reuses the existing decl node rather than duplicating it.
    assert sum(1 for n in g.nodes if n.id == "decl://_Zb") == 1


def test_augment_dedupes_edges() -> None:
    g = SourceGraphSummary()
    augment_graph_with_calls(g, [CallEdge("_Za", "_Zb")])
    added = augment_graph_with_calls(g, [CallEdge("_Za", "_Zb")])
    assert added == 0


# ── call-reachability finding (D6, quality) ─────────────────────────────────


def _graph_with_calls(
    entry_symbol: str, calls: list[tuple[str, str]]
) -> SourceGraphSummary:
    g = SourceGraphSummary()
    # entry decl backs an exported symbol → it is a public entry point.
    g.add_node(GraphNode(id="decl://entry", kind="source_decl", label="entry"))
    g.add_node(
        GraphNode(
            id=f"binary_symbol://{entry_symbol}",
            kind="binary_symbol",
            label=entry_symbol,
        )
    )
    g.add_edge(
        GraphEdge(
            src="decl://entry",
            dst=f"binary_symbol://{entry_symbol}",
            kind="SOURCE_DECL_MAPS_TO_SYMBOL",
        )
    )
    augment_graph_with_calls(g, [CallEdge(c, d) for c, d in calls])
    return g.finalize()


def test_call_reachability_change_emits_quality_finding() -> None:
    old = _graph_with_calls("_Zentry", [("entry", "_Zimpl1")])
    new = _graph_with_calls("_Zentry", [("entry", "_Zimpl1"), ("_Zimpl1", "_Zimpl2")])
    findings = diff_source_graph_findings(old, new)
    cg = [
        c
        for c in findings
        if c.kind == ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED
    ]
    assert len(cg) == 1
    assert cg[0].source_location == "[L5_SOURCE_GRAPH]"
    assert ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED in COMPATIBLE_KINDS


def test_call_reachability_change_shrinks_with_no_example_path() -> None:
    # A call removed (reachability shrinks, nothing added): the "graph explain
    # proof path" (ADR-041 P0 item 3) only has an example to show for a newly
    # *added* callee, so the description carries no "Example newly-reachable
    # path" suffix when the change is a pure removal.
    old = _graph_with_calls("_Zentry", [("entry", "_Zimpl1"), ("_Zimpl1", "_Zimpl2")])
    new = _graph_with_calls("_Zentry", [("entry", "_Zimpl1")])
    findings = diff_source_graph_findings(old, new)
    cg = [
        c
        for c in findings
        if c.kind == ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED
    ]
    assert len(cg) == 1
    assert "Example newly-reachable path" not in cg[0].description


def test_call_reachability_change_names_example_path() -> None:
    # The positive case: a newly-added callee's description names the concrete
    # call chain proving it, not just the before/after counts.
    old = _graph_with_calls("_Zentry", [("entry", "_Zimpl1")])
    new = _graph_with_calls("_Zentry", [("entry", "_Zimpl1"), ("_Zimpl1", "_Zimpl2")])
    findings = diff_source_graph_findings(old, new)
    cg = [
        c
        for c in findings
        if c.kind == ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED
    ]
    assert len(cg) == 1
    assert (
        "Example newly-reachable path: entry --[DECL_CALLS_DECL]--> _Zimpl1 --[DECL_CALLS_DECL]--> _Zimpl2."
        in cg[0].description
    )


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
    assert (
        ext.extract_from_build(
            BuildEvidence(compile_units=[CompileUnit(id="cu://x", source="x.cpp")])
        )
        == []
    )
    assert ext.diagnostics  # a reason was recorded


class _FakeProc:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_clang(
    monkeypatch, *, available: bool = True, proc=None, raises=None
) -> None:
    import abicheck.buildsource.call_graph as cg

    monkeypatch.setattr(
        cg.shutil, "which", lambda _b: "/usr/bin/clang++" if available else None
    )

    def fake_run(*_a, **_k):
        if raises is not None:
            raise raises
        return proc

    monkeypatch.setattr(cg.deadline, "run_bounded", fake_run)


def test_extract_from_args_parses_mocked_clang(monkeypatch) -> None:
    import json as _json

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func(
                "c", "_Zc", [_direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))]
            ),
        ],
    }
    _patch_clang(monkeypatch, proc=_FakeProc(_json.dumps(ast)))
    edges = ClangCallGraphExtractor().extract_from_args(["x.cpp"])
    assert edges == [CallEdge("_Zc", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)]


def test_extract_from_args_reconstructs_safe_parse_command(
    monkeypatch, tmp_path
) -> None:
    import json as _json

    import abicheck.buildsource.call_graph as cg

    ast = {"kind": "TranslationUnitDecl", "inner": []}
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(cg.shutil, "which", lambda _b: "/usr/bin/clang++")

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return _FakeProc(_json.dumps(ast))

    monkeypatch.setattr(cg.deadline, "run_bounded", fake_run)
    src = tmp_path / "victim.cpp"
    src.write_text("int main() { return 0; }", encoding="utf-8")

    ClangCallGraphExtractor().extract_from_args(
        [
            "/usr/bin/g++",
            "-Xclang",
            "-load",
            "-Xclang",
            "./evil.so",
            "-fplugin=./evil.so",
            "-I",
            "include",
            "-D",
            "FEATURE=1",
            "-std=c++20",
            str(src),
        ],
        cwd=str(tmp_path),
    )

    cmd = captured["cmd"]
    assert "-fplugin=./evil.so" not in cmd
    assert "-load" not in cmd
    assert "./evil.so" not in cmd
    assert "-I" in cmd and str(tmp_path / "include") in cmd
    assert "-DFEATURE=1" in cmd
    assert "-std=c++20" in cmd
    assert cmd[-2:] == ["--", str(src)]


def test_extract_from_build_ignores_compile_unit_raw_argv(monkeypatch) -> None:
    import json as _json

    import abicheck.buildsource.call_graph as cg

    ast = {"kind": "TranslationUnitDecl", "inner": []}
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(cg.shutil, "which", lambda _b: "/usr/bin/clang++")

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return _FakeProc(_json.dumps(ast))

    monkeypatch.setattr(cg.deadline, "run_bounded", fake_run)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(
                id="cu://x",
                source="victim.cpp",
                argv=["/usr/bin/g++", "-fplugin=./evil.so", "victim.cpp"],
                language="CXX",
                standard="c++17",
                defines={"FEATURE": "1"},
                include_paths=["include"],
                abi_relevant_flags=["-fvisibility=hidden"],
            )
        ]
    )

    ClangCallGraphExtractor().extract_from_build(build)

    cmd = captured["cmd"]
    assert "-fplugin=./evil.so" not in cmd
    assert "-x" in cmd and "c++" in cmd
    assert "-std=c++17" in cmd
    assert "-DFEATURE=1" in cmd
    assert "-I" in cmd and "include" in cmd
    assert "-fvisibility=hidden" in cmd
    assert cmd[-2:] == ["--", "victim.cpp"]


def test_extract_from_args_empty_stdout(monkeypatch) -> None:
    _patch_clang(monkeypatch, proc=_FakeProc("", stderr="boom"))
    ext = ClangCallGraphExtractor()
    assert ext.extract_from_args(["x.cpp"]) == []
    assert any("no AST" in d for d in ext.diagnostics)


def test_extract_from_args_nonzero_exit_records_diagnostic_but_salvages_edges(
    monkeypatch,
) -> None:
    # Ninth Codex review: clang can exit non-zero (real compile errors in the
    # necessarily-approximate replayed flags) while still printing a partial,
    # error-recovered AST dump. Edges are still salvaged (best effort), but a
    # diagnostic must be recorded regardless — extractor_pass_fully_covered
    # relies on `diagnostics` being non-empty to disqualify confirmed pass
    # coverage for this TU.
    import json as _json

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func(
                "c", "_Zc", [_direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))]
            ),
        ],
    }
    _patch_clang(
        monkeypatch,
        proc=_FakeProc(_json.dumps(ast), stderr="error: bad thing", returncode=1),
    )
    ext = ClangCallGraphExtractor()
    edges = ext.extract_from_args(["x.cpp"])
    assert edges == [CallEdge("_Zc", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)]
    assert any("exited 1" in d for d in ext.diagnostics)


def test_extract_from_args_zero_exit_records_no_diagnostic(monkeypatch) -> None:
    import json as _json

    ast = {"kind": "TranslationUnitDecl", "inner": []}
    _patch_clang(monkeypatch, proc=_FakeProc(_json.dumps(ast), returncode=0))
    ext = ClangCallGraphExtractor()
    assert ext.extract_from_args(["x.cpp"]) == []
    assert ext.diagnostics == []


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

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func(
                "c", "_Zc", [_direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))]
            ),
        ],
    }
    _patch_clang(monkeypatch, proc=_FakeProc(_json.dumps(ast)))
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp", argv=["a.cpp"]),
            CompileUnit(id="cu://b", source="b.cpp", argv=["b.cpp"]),
            CompileUnit(id="cu://nosrc", source=""),  # skipped (no source)
        ]
    )
    edges = ClangCallGraphExtractor().extract_from_build(build)
    assert edges == [CallEdge("_Zc", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)]


def test_call_graph_jobs_env_override_is_bounded(monkeypatch) -> None:
    import abicheck.buildsource.call_graph as cg

    monkeypatch.setenv("ABICHECK_CALL_GRAPH_JOBS", "2")
    # Pin the RAM probe high so the memory clamp never interferes with the
    # CPU/oversubscription bounds this test asserts.
    monkeypatch.setattr(cg, "_call_graph_mem_cap", lambda: None)
    assert _call_graph_jobs(120) == 2
    monkeypatch.setenv("ABICHECK_CALL_GRAPH_JOBS", "9999")
    assert 1 <= _call_graph_jobs(120) <= 120
    monkeypatch.setenv("ABICHECK_CALL_GRAPH_JOBS", "nope")
    assert _call_graph_jobs(120) == 1


def test_call_graph_jobs_clamped_by_available_memory(monkeypatch) -> None:
    """The L5 call-graph pass shares the L4 RAM clamp (OOM guard parity).

    A low-memory host that would OOM under N concurrent multi-GiB clang ASTs must
    reduce the worker count on the call-graph pass just as it does on the L4
    replay — both shell out to the same ``clang -ast-dump=json``.
    """
    import abicheck.buildsource.call_graph as cg

    monkeypatch.delenv("ABICHECK_CALL_GRAPH_JOBS", raising=False)
    # Pretend the host/cgroup only has room for one heavy clang worker.
    monkeypatch.setattr(cg, "_call_graph_mem_cap", lambda: 1)
    assert _call_graph_jobs(120) == 1

    # An explicit override is clamped too — memory wins over the requested count,
    # mirroring source_replay._l4_jobs.
    monkeypatch.setenv("ABICHECK_CALL_GRAPH_JOBS", "8")
    assert _call_graph_jobs(120) == 1

    # When the RAM probe can't read memory (None), the CPU bound stands.
    monkeypatch.setattr(cg, "_call_graph_mem_cap", lambda: None)
    assert _call_graph_jobs(120) == 8

    # The clamp only ever *reduces*: a generous mem_cap leaves the CPU bound.
    monkeypatch.setattr(cg, "_call_graph_mem_cap", lambda: 1000)
    assert _call_graph_jobs(120) == 8


def test_call_graph_mem_cap_shares_l4_budget(monkeypatch) -> None:
    """_call_graph_mem_cap delegates to the L4 cap and never raises."""
    import abicheck.buildsource.call_graph as cg
    import abicheck.buildsource.source_replay as sr

    monkeypatch.setattr(sr, "_l4_mem_cap", lambda: 3)
    assert cg._call_graph_mem_cap() == 3

    def _boom() -> int:
        raise RuntimeError("RAM probe failed")

    monkeypatch.setattr(sr, "_l4_mem_cap", _boom)
    assert cg._call_graph_mem_cap() is None


def test_extract_from_build_parallelizes_and_dedupes(monkeypatch) -> None:
    import abicheck.buildsource.call_graph as cg

    monkeypatch.setenv("ABICHECK_CALL_GRAPH_JOBS", "2")
    # This test exercises parallel extraction, not the independently tested
    # host-memory clamp. Pin the cap so low-RAM CI/dev hosts remain deterministic.
    monkeypatch.setattr(cg, "_call_graph_mem_cap", lambda: 2)
    monkeypatch.setattr(cg.shutil, "which", lambda _b: "/usr/bin/clang++")
    seen_sources: list[str] = []

    def fake_extract(self, argv, cwd=None):
        del self, cwd
        seen_sources.append(argv[-1])
        return [CallEdge("caller", "callee")]

    monkeypatch.setattr(
        cg.ClangCallGraphExtractor, "_extract_from_safe_args", fake_extract
    )
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp"),
            CompileUnit(id="cu://b", source="b.cpp"),
            CompileUnit(id="cu://c", source="c.cpp"),
        ]
    )

    ext = ClangCallGraphExtractor()
    edges = ext.extract_from_build(build)

    assert ext.last_jobs == 2
    assert ext.last_elapsed_s >= 0.0
    assert sorted(seen_sources) == ["a.cpp", "b.cpp", "c.cpp"]
    assert edges == [CallEdge("caller", "callee")]


def test_extract_from_build_propagates_deadline_into_pool_workers(monkeypatch) -> None:
    """Codex review (PR #591): contextvars don't cross a ThreadPoolExecutor
    boundary, so a worker submitted from inside deadline.deadline_scope()
    used to see no active deadline at all — each clang subprocess call
    inside it would run to its full fixed 120s regardless of --budget."""
    import abicheck.buildsource.call_graph as cg
    from abicheck import deadline

    monkeypatch.setenv("ABICHECK_CALL_GRAPH_JOBS", "2")
    monkeypatch.setattr(cg, "_call_graph_mem_cap", lambda: 2)
    monkeypatch.setattr(cg.shutil, "which", lambda _b: "/usr/bin/clang++")
    seen_remaining: list[float | None] = []

    def fake_extract(self, argv, cwd=None):
        del self, cwd
        seen_remaining.append(deadline.remaining())
        return []

    monkeypatch.setattr(
        cg.ClangCallGraphExtractor, "_extract_from_safe_args", fake_extract
    )
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp"),
            CompileUnit(id="cu://b", source="b.cpp"),
            CompileUnit(id="cu://c", source="c.cpp"),
        ]
    )
    ext = ClangCallGraphExtractor()
    with deadline.deadline_scope(30.0):
        ext.extract_from_build(build)
    assert len(seen_remaining) == 3
    assert all(r is not None for r in seen_remaining), (
        "pool worker saw no active deadline (remaining()=None) — the scan "
        "deadline did not cross the executor boundary"
    )
    assert all(0 < r <= 30.0 for r in seen_remaining)


def test_extract_from_args_deadline_exceeded_degrades_to_diagnostic(
    monkeypatch,
) -> None:
    # Codex review (PR #591): this pass is advisory (ADR-028 D3) — a
    # DeadlineExceeded from the now-bounded clang subprocess must degrade to
    # the same diagnostic+[] contract as any other probe failure, not
    # propagate and abort the whole L5 call-graph fold.
    import abicheck.buildsource.call_graph as cg
    from abicheck import deadline

    monkeypatch.setattr(cg.shutil, "which", lambda _b: "/usr/bin/clang++")

    def _raise(*_a, **_k):
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(cg.deadline, "run_bounded", _raise)
    ext = ClangCallGraphExtractor()
    edges = ext.extract_from_args(["x.cpp"])
    assert edges == []
    assert any("clang invocation failed" in d for d in ext.diagnostics)


def test_extract_from_args_bounded_by_local_cap_not_full_scan_budget(
    monkeypatch,
) -> None:
    """Codex review (PR #591), round 8: deadline.run_bounded() honors an
    active outer deadline verbatim (not min(timeout, left)), so a bare
    timeout=120 on this L5 clang call alone did nothing once a scan
    --budget was active: the call stayed bound by the FULL remaining scan
    budget instead of this pass's own 120s local cap. A hung per-TU clang
    call under a generous --budget could therefore eat the whole remaining
    scan instead of degrading after 120s. Assert the ContextVar deadline
    observed inside run_bounded is capped near the local cap, not the much
    larger outer scan budget."""
    import abicheck.buildsource.call_graph as cg
    from abicheck import deadline

    monkeypatch.setattr(cg.shutil, "which", lambda _b: "/usr/bin/clang++")
    seen_remaining: list[float | None] = []

    def fake_run_bounded(*_a, **_k):
        seen_remaining.append(deadline.remaining())
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(cg.deadline, "run_bounded", fake_run_bounded)
    ext = ClangCallGraphExtractor()
    with deadline.deadline_scope(1800.0):  # a generous 30-minute --budget
        ext.extract_from_args(["x.cpp"])

    assert seen_remaining
    assert seen_remaining[0] is not None and seen_remaining[0] <= 120.5


def test_extract_from_args_rechecks_deadline_before_parsing_ast(monkeypatch) -> None:
    """Codex review (PR #591): the same post-subprocess gap as the L2/L4
    clang paths — clang can exit successfully right as the budget expires,
    but json.loads()+parse_clang_ast_calls() used to run unbounded. Must
    degrade to the advisory diagnostic+[] contract (ADR-028 D3), not raise."""
    import json as _json
    import time

    import abicheck.buildsource.call_graph as cg
    from abicheck import deadline

    monkeypatch.setattr(cg.shutil, "which", lambda _b: "/usr/bin/clang++")
    ast = {"kind": "TranslationUnitDecl", "inner": []}

    def fake_run(*_a, **_k):
        # Simulate the budget running out while clang was still parsing: by
        # the time it exits successfully, the deadline has already passed.
        time.sleep(0.05)
        return _FakeProc(_json.dumps(ast))

    monkeypatch.setattr(cg.deadline, "run_bounded", fake_run)
    ext = ClangCallGraphExtractor()
    with deadline.deadline_scope(0.03):
        edges = ext.extract_from_args(["x.cpp"])
    assert edges == []
    assert any(
        "scan deadline exceeded before parsing clang AST" in d for d in ext.diagnostics
    )


def test_extract_from_args_rechecks_deadline_before_walking_ast(monkeypatch) -> None:
    """Codex review (PR #591, round 4): json.loads() on a huge L5 call-graph
    AST can itself consume the rest of the budget -- the existing pre-load
    deadline.check() doesn't catch that; must re-check again after the load,
    before the recursive parse_clang_ast_calls() walk."""
    import json as _json
    import time

    import abicheck.buildsource.call_graph as cg
    from abicheck import deadline

    monkeypatch.setattr(cg.shutil, "which", lambda _b: "/usr/bin/clang++")
    ast = {"kind": "TranslationUnitDecl", "inner": []}

    def fake_run(*_a, **_k):
        return _FakeProc(_json.dumps(ast))

    monkeypatch.setattr(cg.deadline, "run_bounded", fake_run)
    real_loads = cg.json.loads

    def _slow_loads(text):
        time.sleep(0.05)
        return real_loads(text)

    monkeypatch.setattr(cg.json, "loads", _slow_loads)
    ext = ClangCallGraphExtractor()
    with deadline.deadline_scope(0.03):
        edges = ext.extract_from_args(["x.cpp"])
    assert edges == []
    assert any(
        "scan deadline exceeded before walking clang AST" in d for d in ext.diagnostics
    )


# ── collect: call-graph folds automatically (inline_graph_fold.fold_call_graph) ──
#
# `collect`'s call/type/include-graph folding is the exact same
# `inline_graph_fold.fold_call_graph`/`fold_type_graph`/`fold_include_graph`
# the inline `dump --sources` path uses (no more separate
# `cli_buildsource_helpers._collect_call_graph` near-duplicate) — the
# pass-ran/degraded/empty-build/missing-clang scenarios for that shared
# function are exercised in `tests/test_inline_changed_paths.py`
# (`test_inline_graph_folds_call_edges_for_l4_l5_mode`,
# `test_inline_graph_no_call_edges_when_clang_absent`, etc.). Only the
# `collect`-specific end-to-end wiring is tested here: no `--call-graph`
# flag exists any more — `--source-abi` + `--source-graph summary` together
# fold call edges in automatically, mirroring `dump --sources`.


class _FakeExtractor:
    """Stand-in for ClangCallGraphExtractor with a controllable result."""

    def __init__(
        self,
        *,
        available: bool,
        edges: list[CallEdge] | None = None,
        clang_bin: str = "clang++",
    ) -> None:
        self.clang_bin = clang_bin
        self._available = available
        self._edges = edges or []
        self.diagnostics: list[str] = []
        self.last_jobs = 1
        self.last_elapsed_s = 0.0

    def available(self) -> bool:
        return self._available

    def extract_from_build(self, _build: BuildEvidence) -> list[CallEdge]:
        return self._edges


def _patch_extractor(monkeypatch, fake: _FakeExtractor) -> None:
    import abicheck.buildsource.call_graph as cg

    monkeypatch.setattr(cg, "ClangCallGraphExtractor", lambda **_k: fake)


def _write_call_graph_source_tree(tmp_path):
    import json as _json

    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "foo.cpp").write_text("int foo(){return 1;}\n")
    (tree / "compile_commands.json").write_text(
        _json.dumps(
            [
                {
                    "directory": str(tree),
                    "file": "foo.cpp",
                    "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
                }
            ]
        )
    )
    return tree


def test_collect_evidence_call_graph_automatic_with_source_abi_and_graph(
    monkeypatch, tmp_path
) -> None:
    # `collect --source-abi --source-graph summary` (no separate --call-graph
    # flag existed even before the ADR-043 CLI reset deleted `collect`
    # outright) folded call edges in automatically. The exact same automatic
    # gate (inline.collect_inline_pack: with_call_graph = "L5" in layers and
    # "L4" in layers) drives `dump --sources ... --depth source`, which is now
    # the one public surface for L4+L5 collection — so this exercises that.
    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.serialization import load_snapshot

    tree = _write_call_graph_source_tree(tmp_path)
    _patch_extractor(
        monkeypatch, _FakeExtractor(available=True, edges=[CallEdge("_Za", "_Zb")])
    )

    out = tmp_path / "out.json"
    res = CliRunner().invoke(
        main,
        ["dump", "--sources", str(tree), "--depth", "source", "-o", str(out)],
    )
    assert res.exit_code == 0, res.output
    bs = load_snapshot(out).build_source
    assert bs is not None and bs.source_graph is not None
    assert any(e.kind == "DECL_CALLS_DECL" for e in bs.source_graph.edges)


def test_collect_evidence_source_graph_alone_does_not_fold_call_graph(
    monkeypatch, tmp_path
) -> None:
    # An L5 graph collected WITHOUT L4 stays structural-only — the semantic
    # passes are gated on L4 also being requested (with_call_graph = "L5" in
    # layers and "L4" in layers, inline.collect_inline_pack). The public
    # `--depth` ladder has no rung that requests L5 without L4 (that internal
    # "graph-build" CI mode is not user-reachable per
    # abicheck.buildsource.scan_levels.EvidenceDepth/USER_DEPTHS), so this
    # drives collect_inline_pack directly with layers=("L3", "L5") instead of
    # going through a CLI invocation that cannot express this state.
    from abicheck.buildsource.inline import collect_inline_pack

    tree = _write_call_graph_source_tree(tmp_path)
    _patch_extractor(
        monkeypatch, _FakeExtractor(available=True, edges=[CallEdge("_Za", "_Zb")])
    )

    pack = collect_inline_pack(sources=tree, build_info=None, layers=("L3", "L5"))
    assert pack is not None
    assert pack.source_graph is not None
    assert not any(e.kind == "DECL_CALLS_DECL" for e in pack.source_graph.edges)


# ── source-location provenance (defined_in_project) ───────────────────────────


def _func_in(name: str, mangled: str, body: list[dict], file: str) -> dict:
    # A FunctionDecl carrying a source file on its loc (clang sticky-file form).
    return {
        "kind": "FunctionDecl",
        "name": name,
        "mangledName": mangled,
        "loc": {"file": file, "line": 1},
        "inner": [{"kind": "CompoundStmt", "inner": body}],
    }


def test_parse_captures_caller_file() -> None:
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func_in(
                "caller",
                "_Zcaller",
                [_direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))],
                "/work/src/impl.cc",
            )
        ],
    }
    edge = parse_clang_ast_calls(ast)[0]
    assert edge.caller_file == "/work/src/impl.cc"


def test_parse_threads_sticky_file_across_top_level_siblings() -> None:
    # Codex review: clang emits `loc.file` only when it *changes* between
    # consecutive nodes in the pre-order dump. A second top-level sibling
    # declaration with no loc/range of its own (declared in the same header
    # as the previous sibling) must still see that previous sibling's file --
    # the parent loop used to re-walk every child with the *stale* cur_file
    # from before the first sibling ran, discarding what it discovered.
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func_in("helper1", "_Zhelper1", [], "/work/include/helper.hpp"),
            {
                "kind": "FunctionDecl",
                "name": "helper2",
                "mangledName": "_Zhelper2",
                # No loc/range at all -- sticky, same file as helper1.
                "inner": [
                    {
                        "kind": "CompoundStmt",
                        "inner": [
                            _direct_call(_ref("FunctionDecl", "callee", "_Zcallee"))
                        ],
                    }
                ],
            },
        ],
    }
    edges = parse_clang_ast_calls(ast)
    edge = next(e for e in edges if e.caller == "_Zhelper2")
    assert edge.caller_file == "/work/include/helper.hpp"


def test_augment_marks_defined_in_project_from_source_file() -> None:
    # A caller whose body is in a project compile-unit source is defined_in_project;
    # a callee that is never a project-file caller (extern / third-party) is not.
    g = SourceGraphSummary()
    edges = [
        CallEdge("_Zhelper", "_Zmalloc", caller_file="/work/src/impl.cc"),
    ]
    augment_graph_with_calls(g, edges, frozenset({"src/impl.cc"}))
    by_id = {n.id: n for n in g.nodes}
    assert by_id["decl://_Zhelper"].attrs.get("defined_in_project") is True
    # malloc is only ever a callee (no project-file body) → not marked.
    assert not by_id["decl://_Zmalloc"].attrs.get("defined_in_project")


def test_augment_thirdparty_header_caller_not_project() -> None:
    # An inline third-party header function whose body makes a call appears as a
    # caller, but its file is a header outside the project sources → not marked.
    g = SourceGraphSummary()
    edges = [
        CallEdge("_Zboost", "_Zinner", caller_file="/usr/include/boost/x.hpp"),
    ]
    augment_graph_with_calls(g, edges, frozenset({"src/impl.cc"}))
    by_id = {n.id: n for n in g.nodes}
    assert not by_id["decl://_Zboost"].attrs.get("defined_in_project")


def test_augment_marks_leaf_callee_from_callee_file() -> None:
    # A leaf helper appears only as a callee (no outgoing calls); its declaration
    # file (callee_file) earns it project provenance (Codex review).
    g = SourceGraphSummary()
    edges = [
        CallEdge(
            "_Zpub",
            "_Zleaf",
            caller_file="/work/src/api.cc",
            callee_file="/work/src/util.cc",
        ),
    ]
    augment_graph_with_calls(g, edges, frozenset({"src/api.cc", "src/util.cc"}))
    by_id = {n.id: n for n in g.nodes}
    assert by_id["decl://_Zleaf"].attrs.get("defined_in_project") is True
    assert by_id["decl://_Zleaf"].attrs.get("def_file") == "/work/src/util.cc"


def test_parse_fills_callee_file_from_sibling_functiondecl() -> None:
    # A leaf helper defined in the TU is referenced by a caller; the call's
    # referencedDecl carries no loc.file, so callee_file is resolved from the
    # helper's own FunctionDecl definition (Codex review).
    ast_tree = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func_in("helper", "_Zhelper", [], "/work/src/util.cc"),
            _func_in(
                "api",
                "_Zapi",
                [
                    _direct_call(
                        {
                            "kind": "FunctionDecl",
                            "name": "helper",
                            "mangledName": "_Zhelper",
                        }
                    )
                ],
                "/work/src/api.cc",
            ),
        ],
    }
    edges = parse_clang_ast_calls(ast_tree)
    edge = next(e for e in edges if e.callee == "_Zhelper")
    assert edge.callee_file == "/work/src/util.cc"


def test_parse_fills_callee_file_from_declaration_only_sibling() -> None:
    # Codex review, PR #555: a helper only *declared* in this TU (e.g. a
    # private header this TU includes, with its body compiled in a separate
    # TU never present in this AST) previously left callee_file empty --
    # _enter_function_scope only recorded a file when the sibling node had a
    # body. The declaration's own file is exactly the private-header
    # provenance a Flow-2 source_edges-only graph needs to mark the callee
    # defined_in_project.
    ast_tree = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "helper",
                "mangledName": "_Zhelper",
                "loc": {"file": "include/detail/helper.h", "line": 3},
                # No CompoundStmt inner -- a bare prototype, no body in this TU.
            },
            _func_in(
                "api",
                "_Zapi",
                [
                    _direct_call(
                        {
                            "kind": "FunctionDecl",
                            "name": "helper",
                            "mangledName": "_Zhelper",
                        }
                    )
                ],
                "/work/src/api.cc",
            ),
        ],
    }
    edges = parse_clang_ast_calls(ast_tree)
    edge = next(e for e in edges if e.callee == "_Zhelper")
    assert edge.callee_file == "include/detail/helper.h"


def test_parse_prefers_body_file_over_earlier_declaration_only_sighting() -> None:
    # A body seen after an earlier declaration-only sighting of the same
    # identity must upgrade decl_files to the (more authoritative)
    # definition file, not stay pinned to the first bare declaration.
    ast_tree = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "helper",
                "mangledName": "_Zhelper",
                "loc": {"file": "include/detail/helper.h", "line": 3},
            },
            _func_in("helper", "_Zhelper", [], "/work/src/util.cc"),
            _func_in(
                "api",
                "_Zapi",
                [
                    _direct_call(
                        {
                            "kind": "FunctionDecl",
                            "name": "helper",
                            "mangledName": "_Zhelper",
                        }
                    )
                ],
                "/work/src/api.cc",
            ),
        ],
    }
    edges = parse_clang_ast_calls(ast_tree)
    edge = next(e for e in edges if e.callee == "_Zhelper")
    assert edge.callee_file == "/work/src/util.cc"


def test_project_source_files_includes_private_headers_not_public() -> None:
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit, Target
    from abicheck.buildsource.call_graph import project_source_files

    build = BuildEvidence(
        compile_units=[CompileUnit(id="cu://a", source="src/a.cc")],
        targets=[
            Target(
                id="t",
                name="t",
                public_headers=["include/api.h"],
                private_headers=["src/detail.h"],
            )
        ],
    )
    pf = project_source_files(build)
    assert "src/a.cc" in pf
    assert "src/detail.h" in pf  # private header → internal provenance
    assert "include/api.h" not in pf  # public header excluded (public surface)
