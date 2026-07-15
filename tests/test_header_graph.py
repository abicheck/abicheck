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

"""Tests for the header-only (L2) semantic graph builder (ADR-041 addendum).

Exercises ``build_header_only_graph`` against hand-built ``AbiSnapshot``
objects and (optionally) a hand-built ``clang -ast-dump=json`` tree — no
compiler or build integration required, mirroring ``test_type_graph.py``'s
"pure function, unit-tested without a compiler" discipline.
"""

from __future__ import annotations

from abicheck.buildsource.header_graph import (
    HEADER_CALL_GRAPH_PASS,
    HEADER_TYPE_GRAPH_PASS,
    ClangHeaderIncludeExtractor,
    build_header_only_graph,
)
from abicheck.buildsource.include_graph import augment_graph_with_includes
from abicheck.buildsource.source_graph import (
    is_internal_dependency_node,
    is_public_dependency_node,
)
from abicheck.model import AbiSnapshot, Function, ScopeOrigin, Variable

PUBLIC_HEADER = "/proj/include/pub.h"
PRIVATE_HEADER = "/proj/include/detail/impl.h"


def _snapshot(
    functions: list[Function] | None = None, variables: list[Variable] | None = None
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=functions or [],
        variables=variables or [],
    )


def _loc(file: str) -> dict:
    return {"file": file}


def _field(name: str, qual_type: str) -> dict:
    return {"kind": "FieldDecl", "name": name, "type": {"qualType": qual_type}}


def _record(
    name: str,
    *,
    file: str,
    bases: list[dict] | None = None,
    inner: list[dict] | None = None,
) -> dict:
    d: dict = {
        "kind": "CXXRecordDecl",
        "name": name,
        "loc": _loc(file),
        "inner": inner or [],
    }
    if bases is not None:
        d["bases"] = bases
    return d


def _base(qual_type: str) -> dict:
    return {"type": {"qualType": qual_type}, "writtenAccess": "public"}


def _tu(*decls: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(decls)}


# ── decl-node seeding (no ast_root needed) ──────────────────────────────────


def test_seeds_public_and_private_function_decls_with_visibility() -> None:
    public_fn = Function(
        name="pub_api",
        mangled="_Z7pub_apiv",
        return_type="void",
        source_location=f"{PUBLIC_HEADER}:10",
        source_header=PUBLIC_HEADER,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    private_fn = Function(
        name="helper",
        mangled="_ZN6detail6helperEv",
        return_type="void",
        source_location=f"{PRIVATE_HEADER}:5",
        source_header=PRIVATE_HEADER,
        origin=ScopeOrigin.PRIVATE_HEADER,
    )
    snap = _snapshot(functions=[public_fn, private_fn])
    graph = build_header_only_graph(snap)

    node_by_id = {n.id: n for n in graph.nodes}
    pub_id = "decl://_Z7pub_apiv"
    priv_id = "decl://_ZN6detail6helperEv"
    assert node_by_id[pub_id].attrs["visibility"] == "public_header"
    assert node_by_id[priv_id].attrs["visibility"] == "private_header"
    assert any(e.kind == "SOURCE_DECLARES" and e.dst == pub_id for e in graph.edges)
    # No AST supplied: no type/call edges, no extractor passes stamped.
    assert graph.extractor_passes == {}
    assert not any(e.kind in ("DECL_CALLS_DECL", "TYPE_INHERITS") for e in graph.edges)


def test_unknown_origin_when_no_public_header_set_supplied() -> None:
    fn = Function(name="f", mangled="_Z1fv", return_type="void")
    graph = build_header_only_graph(_snapshot(functions=[fn]))
    node = next(n for n in graph.nodes if n.id == "decl://_Z1fv")
    assert "visibility" not in node.attrs


def test_variable_decl_seeded_the_same_way() -> None:
    var = Variable(
        name="g_count",
        mangled="g_count",
        type="int",
        source_header=PUBLIC_HEADER,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    graph = build_header_only_graph(_snapshot(variables=[var]))
    node = next(n for n in graph.nodes if n.id == "decl://g_count")
    assert node.attrs["visibility"] == "public_header"


# ── type-node + edge folding (ast_root supplied) ────────────────────────────


def _headline_ast() -> dict:
    """The ADR's own motivating example: a public struct with a private field
    type, and a public function taking a private parameter type."""
    return _tu(
        {
            "kind": "NamespaceDecl",
            "name": "detail",
            "inner": [_record("Impl", file=PRIVATE_HEADER)],
        },
        _record(
            "Public",
            file=PUBLIC_HEADER,
            inner=[_field("p", "detail::Impl *")],
        ),
    )


def test_public_struct_with_private_field_type_classifies_correctly() -> None:
    ast = _headline_ast()
    graph = build_header_only_graph(
        _snapshot(),
        ast,
        public_header_paths=[PUBLIC_HEADER],
    )

    node_by_id = {n.id: n for n in graph.nodes}
    public_id = "type://Public"
    private_id = "type://detail::Impl"
    assert node_by_id[public_id].attrs["visibility"] == "public_header"
    assert node_by_id[private_id].attrs["visibility"] == "private_header"
    assert any(
        e.kind == "TYPE_HAS_FIELD_TYPE" and e.src == public_id and e.dst == private_id
        for e in graph.edges
    )

    # The exact classification crosscheck.py's public_to_internal_dependency
    # and source_graph_findings' version diff both rely on.
    exported: set[str] = set()
    assert is_public_dependency_node(public_id, node_by_id, exported)
    assert is_internal_dependency_node(private_id, node_by_id, exported, {})
    assert not is_internal_dependency_node(public_id, node_by_id, exported, {})


def test_extractor_passes_stamped_when_ast_supplied() -> None:
    ast = _headline_ast()
    graph = build_header_only_graph(_snapshot(), ast)
    assert graph.extractor_passes[HEADER_TYPE_GRAPH_PASS] is True
    assert graph.extractor_passes[HEADER_CALL_GRAPH_PASS] is True
    # finalize()'s coverage recognizes the header-only type-graph pass for
    # the *structural* kinds — a header-only pass has true project-wide
    # visibility of base classes/field/parameter types.
    assert graph.coverage["type_edges"]["collected"] is True


def test_coverage_never_credits_body_dependent_kinds_from_header_pass_alone() -> None:
    # Codex review: a header-only pass cannot see out-of-line calls/
    # references, so its "ran" must not mark call_edges/reference_edges
    # collected when zero such edges were actually found — only the
    # structural type_edges bucket may be granted from the header-only
    # pass name alone.
    ast = _tu(_record("Widget", file=PUBLIC_HEADER))
    graph = build_header_only_graph(
        _snapshot(), ast, public_header_paths=[PUBLIC_HEADER]
    )
    assert graph.extractor_passes[HEADER_CALL_GRAPH_PASS] is True
    assert graph.extractor_passes[HEADER_TYPE_GRAPH_PASS] is True
    assert graph.coverage["call_edges"]["collected"] is False
    assert graph.coverage["reference_edges"]["collected"] is False


def test_base_class_edge_from_headers_alone() -> None:
    ast = _tu(
        _record("Base", file=PRIVATE_HEADER),
        _record("Derived", file=PUBLIC_HEADER, bases=[_base("Base")]),
    )
    graph = build_header_only_graph(
        _snapshot(),
        ast,
        public_header_paths=[PUBLIC_HEADER],
    )
    node_by_id = {n.id: n for n in graph.nodes}
    assert node_by_id["type://Base"].attrs["visibility"] == "private_header"
    assert any(
        e.kind == "TYPE_INHERITS"
        and e.src == "type://Derived"
        and e.dst == "type://Base"
        for e in graph.edges
    )


def test_no_ast_root_yields_no_type_nodes_or_edges() -> None:
    graph = build_header_only_graph(_snapshot())
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.extractor_passes == {}


# ── header_paths pre-seeding ─────────────────────────────────────────────────


def test_header_paths_preseeded_even_without_declarations() -> None:
    # A pure #include-only umbrella header declares nothing itself, but is
    # still a real public entry point — it must get a node (and visibility)
    # so a later include-graph edge has a valid source to attach to.
    graph = build_header_only_graph(
        _snapshot(),
        header_paths=[PUBLIC_HEADER],
        public_header_paths=[PUBLIC_HEADER],
    )
    node = next(n for n in graph.nodes if n.id == f"header://{PUBLIC_HEADER}")
    assert node.attrs["visibility"] == "public_header"


def test_header_node_visibility_classified_from_declarations_too() -> None:
    fn = Function(
        name="f",
        mangled="_Z1fv",
        return_type="void",
        source_header=PRIVATE_HEADER,
        origin=ScopeOrigin.PRIVATE_HEADER,
    )
    graph = build_header_only_graph(
        _snapshot(functions=[fn]), public_header_paths=[PUBLIC_HEADER]
    )
    node = next(n for n in graph.nodes if n.id == f"header://{PRIVATE_HEADER}")
    assert node.attrs["visibility"] == "private_header"


# ── ClangHeaderIncludeExtractor ──────────────────────────────────────────────


def test_header_include_extractor_returns_empty_without_clang(monkeypatch) -> None:
    import abicheck.buildsource.include_graph as ig

    monkeypatch.setattr(ig.shutil, "which", lambda _b: None)
    include_map, diags = ClangHeaderIncludeExtractor().extract(
        ["pub.h"], ["/proj/include"]
    )
    assert include_map == {}
    assert diags


def test_header_include_extractor_parses_mocked_clang(tmp_path, monkeypatch) -> None:
    import abicheck.buildsource.include_graph as ig

    pub = tmp_path / "pub.h"
    pub.write_text('#include "detail/impl.h"\n')
    impl = tmp_path / "detail" / "impl.h"
    impl.parent.mkdir()
    impl.write_text("struct Impl {};\n")

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")

    class _Proc:
        stdout = f"pub.o: {pub} {impl}"
        stderr = ""

    monkeypatch.setattr(ig.subprocess, "run", lambda *a, **k: _Proc())

    include_map, diags = ClangHeaderIncludeExtractor().extract(
        [str(pub)], [str(tmp_path)]
    )
    assert diags == []
    # The header's own path is filtered out (clang -M lists the "source" —
    # here the header itself — as the first prerequisite); only the real
    # included file remains.
    assert include_map == {f"header://{pub}": [str(impl)]}


def test_header_include_extractor_forwards_gcc_options(tmp_path, monkeypatch) -> None:
    # Codex review: --gcc-options flags (e.g. a define gating an #include)
    # must reach this pass exactly like the AST pass, not just the deferred
    # gcc_option_tokens.
    import abicheck.buildsource.include_graph as ig

    pub = tmp_path / "pub.h"
    pub.write_text("void f();\n")
    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    seen_argv = {}

    def _fake_run(cmd, **_kwargs):
        seen_argv["cmd"] = cmd

        class _Proc:
            stdout = f"pub.o: {pub}"
            stderr = ""

        return _Proc()

    monkeypatch.setattr(ig.subprocess, "run", _fake_run)
    ClangHeaderIncludeExtractor().extract([str(pub)], [], gcc_options="-DFOO=1")
    assert "-DFOO=1" in seen_argv["cmd"]


def test_header_include_extractor_folds_into_graph(tmp_path, monkeypatch) -> None:
    import abicheck.buildsource.include_graph as ig

    pub = tmp_path / "pub.h"
    pub.write_text('#include "detail/impl.h"\n')
    impl = tmp_path / "detail" / "impl.h"
    impl.parent.mkdir()
    impl.write_text("struct Impl {};\n")

    graph = build_header_only_graph(
        _snapshot(), header_paths=[str(pub)], public_header_paths=[str(pub)]
    )

    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")

    class _Proc:
        stdout = f"pub.o: {pub} {impl}"
        stderr = ""

    monkeypatch.setattr(ig.subprocess, "run", lambda *a, **k: _Proc())

    include_map, _diags = ClangHeaderIncludeExtractor().extract(
        [str(pub)], [str(tmp_path)]
    )
    added = augment_graph_with_includes(graph, include_map)
    graph.finalize()

    assert added == 1
    pub_id = f"header://{pub}"
    assert any(
        e.kind == "COMPILE_UNIT_INCLUDES_FILE" and e.src == pub_id for e in graph.edges
    )
    assert graph.coverage["include_edges"]["collected"] is True


def test_ast_only_reference_target_gets_visibility_even_when_unseeded() -> None:
    # Codex review: a private declaration referenced only via
    # DECL_REFERENCES_DECL (e.g. an EnumConstantDecl) has no equivalent
    # entity in the flat AbiSnapshot model to seed from
    # snapshot.functions/snapshot.variables — it must still get visibility
    # from its own edge's declaring file, or is_internal_dependency_node
    # treats it as third-party/system and the public_to_internal_dependency
    # finding never fires.
    ast = _tu(
        # The real, top-level declaration — this is what
        # `_index_declared_entities` indexes into `decl_file`, giving the
        # reference stub below something to resolve its file against (clang
        # commonly emits an incomplete referencedDecl stub with no `loc` of
        # its own).
        {
            "kind": "EnumDecl",
            "name": "Color",
            "loc": _loc(PRIVATE_HEADER),
            "inner": [
                {
                    "kind": "EnumConstantDecl",
                    "name": "RED",
                    "mangledName": "_ZN5Color3REDE",
                    "loc": _loc(PRIVATE_HEADER),
                },
            ],
        },
        {
            "kind": "FunctionDecl",
            "name": "f",
            "mangledName": "_Z1fv",
            "loc": _loc(PUBLIC_HEADER),
            "inner": [
                {
                    "kind": "CompoundStmt",
                    "inner": [
                        {
                            "kind": "DeclRefExpr",
                            "referencedDecl": {
                                "kind": "EnumConstantDecl",
                                "name": "RED",
                                "mangledName": "_ZN5Color3REDE",
                            },
                        }
                    ],
                }
            ],
        },
    )
    graph = build_header_only_graph(
        _snapshot(), ast, public_header_paths=[PUBLIC_HEADER]
    )
    node_by_id = {n.id: n for n in graph.nodes}
    target_id = "decl://_ZN5Color3REDE"
    assert target_id in node_by_id
    assert node_by_id[target_id].attrs["visibility"] == "private_header"
    assert any(
        e.kind == "DECL_REFERENCES_DECL" and e.dst == target_id for e in graph.edges
    )
    exported: set[str] = set()
    assert is_internal_dependency_node(target_id, node_by_id, exported, {})


def test_header_include_extractor_forwards_sysroot_and_nostdinc(
    tmp_path, monkeypatch
) -> None:
    import abicheck.buildsource.include_graph as ig

    pub = tmp_path / "pub.h"
    pub.write_text("void f();\n")
    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    seen_argv = {}

    def _fake_run(cmd, **_kwargs):
        seen_argv["cmd"] = cmd

        class _Proc:
            stdout = f"pub.o: {pub}"
            stderr = ""

        return _Proc()

    monkeypatch.setattr(ig.subprocess, "run", _fake_run)
    ClangHeaderIncludeExtractor().extract(
        [str(pub)], [], sysroot="/opt/cross-sysroot", nostdinc=True
    )
    assert "--sysroot=/opt/cross-sysroot" in seen_argv["cmd"]
    assert "-nostdinc" in seen_argv["cmd"]
