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
from abicheck.fact_provenance import func_fact_key, var_fact_key
from abicheck.model import (
    AbiSnapshot,
    EnumType,
    Function,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
)

PUBLIC_HEADER = "/proj/include/pub.h"
PRIVATE_HEADER = "/proj/include/detail/impl.h"


def _snapshot(
    functions: list[Function] | None = None,
    variables: list[Variable] | None = None,
    types: list[RecordType] | None = None,
    enums: list[EnumType] | None = None,
    scope_fallback: str | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        enums=enums or [],
        scope_fallback=scope_fallback,
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
    # No AST supplied: no call edges/pass — the flat-model structural pass
    # still runs unconditionally (no clang needed), but there are no types
    # in this snapshot for it to find anything about.
    assert "header_call_graph" not in graph.extractor_passes
    assert graph.extractor_passes == {"header_type_graph": True}
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


# ── hybrid-graph provenance tagging (G31 Phase C) ───────────────────────────


def test_fact_provenance_stamps_visibility_provenance_on_function_node() -> None:
    fn = Function(
        name="pub_api",
        mangled="_Z7pub_apiv",
        return_type="void",
        source_header=PUBLIC_HEADER,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    graph = build_header_only_graph(
        _snapshot(functions=[fn]),
        fact_provenance={func_fact_key("_Z7pub_apiv", "visibility"): "clang"},
    )
    node = next(n for n in graph.nodes if n.id == "decl://_Z7pub_apiv")
    assert node.attrs["visibility_provenance"] == "clang"
    # The value itself is unaffected -- only the new, additive attr appears.
    assert node.attrs["visibility"] == "public_header"


def test_fact_provenance_stamps_visibility_provenance_on_variable_node() -> None:
    var = Variable(
        name="g_count",
        mangled="g_count",
        type="int",
        source_header=PUBLIC_HEADER,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    graph = build_header_only_graph(
        _snapshot(variables=[var]),
        fact_provenance={var_fact_key("g_count", "visibility"): "castxml"},
    )
    node = next(n for n in graph.nodes if n.id == "decl://g_count")
    assert node.attrs["visibility_provenance"] == "castxml"


def test_no_fact_provenance_leaves_attr_absent() -> None:
    """No provenance map (a plain, non-hybrid snapshot's real call shape) is
    a pure no-op -- the additive attr must never appear from nothing."""
    fn = Function(name="f", mangled="_Z1fv", return_type="void")
    graph = build_header_only_graph(_snapshot(functions=[fn]))
    node = next(n for n in graph.nodes if n.id == "decl://_Z1fv")
    assert "visibility_provenance" not in node.attrs


def test_empty_fact_provenance_dict_leaves_attr_absent() -> None:
    """An empty dict (every non-hybrid AbiSnapshot.fact_provenance default)
    must behave identically to None, not raise or stamp a spurious attr."""
    fn = Function(name="f", mangled="_Z1fv", return_type="void")
    graph = build_header_only_graph(_snapshot(functions=[fn]), fact_provenance={})
    node = next(n for n in graph.nodes if n.id == "decl://_Z1fv")
    assert "visibility_provenance" not in node.attrs


def test_fact_provenance_with_no_matching_key_leaves_attr_absent() -> None:
    """A provenance map that simply doesn't name this declaration (e.g. it
    names a DIFFERENT mangled symbol) must not stamp anything either."""
    fn = Function(name="f", mangled="_Z1fv", return_type="void")
    graph = build_header_only_graph(
        _snapshot(functions=[fn]),
        fact_provenance={func_fact_key("_Z9unrelatedv", "visibility"): "clang"},
    )
    node = next(n for n in graph.nodes if n.id == "decl://_Z1fv")
    assert "visibility_provenance" not in node.attrs


def test_fact_provenance_with_no_mangled_name_leaves_attr_absent() -> None:
    """A declaration with no recorded mangled symbol still seeds a node --
    _decl_identity falls back to the bare name -- but there's no mangled key
    to look up in fact_provenance, so the lookup must be skipped entirely
    rather than raising or matching on an empty string."""
    fn = Function(name="f", mangled="", return_type="void")
    graph = build_header_only_graph(
        _snapshot(functions=[fn]),
        fact_provenance={func_fact_key("", "visibility"): "clang"},
    )
    node = next(n for n in graph.nodes if n.id == "decl://f")
    assert "visibility_provenance" not in node.attrs


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


def test_no_ast_root_yields_no_call_pass_on_an_empty_snapshot() -> None:
    # An empty snapshot has no declarations/types for either path to find
    # anything about, but the flat-model structural pass still runs
    # unconditionally (no clang needed) — only the call-graph pass requires
    # an AST and is genuinely absent here.
    graph = build_header_only_graph(_snapshot())
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.extractor_passes == {"header_type_graph": True}


# ── flat-model structural edges (no AST/clang at all) ───────────────────────


def test_flat_model_public_struct_private_field_type() -> None:
    # The ADR's own headline example, reachable with zero clang dependency:
    # castxml (the default L2 backend) already parses RecordType.fields, so
    # the private field-type dependency is visible without a second AST pass.
    public = RecordType(
        name="Public",
        kind="struct",
        fields=[TypeField(name="p", type="Private*")],
        origin=ScopeOrigin.PUBLIC_HEADER,
        source_header=PUBLIC_HEADER,
    )
    private = RecordType(
        name="Private",
        kind="struct",
        origin=ScopeOrigin.PRIVATE_HEADER,
        source_header=PRIVATE_HEADER,
    )
    graph = build_header_only_graph(_snapshot(types=[public, private]))
    edge = next(e for e in graph.edges if e.kind == "TYPE_HAS_FIELD_TYPE")
    assert edge.src == "type://Public"
    assert edge.dst == "type://Private"
    assert edge.attrs["resolution"] == "unique_candidate"
    node_by_id = {n.id: n for n in graph.nodes}
    assert node_by_id["type://Private"].attrs["visibility"] == "private_header"
    assert graph.extractor_passes == {"header_type_graph": True}


def test_flat_model_type_inherits_base() -> None:
    base = RecordType(name="Base", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER)
    derived = RecordType(
        name="Derived",
        kind="struct",
        bases=["Base"],
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    graph = build_header_only_graph(_snapshot(types=[base, derived]))
    edge = next(e for e in graph.edges if e.kind == "TYPE_INHERITS")
    assert edge.src == "type://Derived"
    assert edge.dst == "type://Base"


def test_flat_model_function_return_and_param_types() -> None:
    private = RecordType(
        name="Private", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER
    )
    fn = Function(
        name="f",
        mangled="_Z1fv",
        return_type="Private",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    graph = build_header_only_graph(_snapshot(functions=[fn], types=[private]))
    edge = next(e for e in graph.edges if e.kind == "DECL_HAS_TYPE")
    assert edge.src == "decl://_Z1fv"
    assert edge.dst == "type://Private"
    assert edge.attrs["role"] == "return"


def test_flat_model_enum_type_node_kind() -> None:
    en = EnumType(
        name="Color", origin=ScopeOrigin.PUBLIC_HEADER, source_header=PUBLIC_HEADER
    )
    graph = build_header_only_graph(_snapshot(enums=[en]))
    node = next(n for n in graph.nodes if n.id == "type://Color")
    assert node.kind == "enum_type"
    assert node.attrs["visibility"] == "public_header"


def test_flat_model_builtin_and_pointer_types_excluded() -> None:
    fn = Function(
        name="f", mangled="_Z1fv", return_type="int", origin=ScopeOrigin.PUBLIC_HEADER
    )
    graph = build_header_only_graph(_snapshot(functions=[fn]))
    assert not any(e.kind == "DECL_HAS_TYPE" for e in graph.edges)


def test_flat_model_ambiguous_bare_name_edge_skipped_entirely() -> None:
    # Two distinct types share the bare name "Impl" (e.g. from different,
    # unrecorded namespaces) — the flat model has no scope info to
    # disambiguate, so a reference to "Impl" must not guess which one. In
    # particular it must NOT emit an edge to the shared, collapsed
    # `type://Impl` node at all: that node's visibility is whichever
    # same-named declaration happened to be seeded first, so an edge to it
    # could misattribute a reference to the wrong one's visibility —
    # reporting (or hiding) a public-to-internal dependency that may not
    # actually exist (Codex review; the fix that only labelled the edge
    # "unresolved" without skipping it still let this happen).
    impl_public = RecordType(
        name="Impl", kind="struct", origin=ScopeOrigin.PUBLIC_HEADER
    )
    impl_private = RecordType(
        name="Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER
    )
    public = RecordType(
        name="Public",
        kind="struct",
        fields=[TypeField(name="p", type="Impl*")],
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    graph = build_header_only_graph(
        _snapshot(types=[public, impl_public, impl_private])
    )
    assert not any(e.kind == "TYPE_HAS_FIELD_TYPE" for e in graph.edges)


def test_flat_model_resolves_qualified_spelling_to_seeded_bare_node() -> None:
    # Codex review: the alternative --ast-frontend clang L2 backend's own
    # field/base-type extraction uses clang's raw qualType, which prints
    # "as written" — a type referenced from a sibling namespace prints
    # qualified (e.g. "detail::Impl") even though the flat model records
    # only Impl's own bare name. Without stripping to the bare leaf first,
    # the edge missed the already-seeded, correctly-classified "Impl" node
    # entirely and created a brand new, unclassified "detail::Impl" one.
    impl = RecordType(name="Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER)
    public = RecordType(
        name="Public",
        kind="struct",
        fields=[TypeField(name="p", type="detail::Impl *")],
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    graph = build_header_only_graph(_snapshot(types=[public, impl]))
    edge = next(e for e in graph.edges if e.kind == "TYPE_HAS_FIELD_TYPE")
    assert edge.dst == "type://Impl"
    node_by_id = {n.id: n for n in graph.nodes}
    assert node_by_id["type://Impl"].attrs["visibility"] == "private_header"
    assert "type://detail::Impl" not in node_by_id


def test_flat_model_ambiguous_source_record_edges_skipped() -> None:
    # Codex review: the *emitting* record's own bare name can be just as
    # ambiguous as an edge target's. A public "Foo" and an unrelated private
    # "Foo" collapse to the same type://Foo node; the private Foo's own
    # private-typed field must not get attributed to the shared node and
    # read as a (nonexistent) public-to-internal dependency of the public
    # Foo.
    foo_public = RecordType(name="Foo", kind="struct", origin=ScopeOrigin.PUBLIC_HEADER)
    priv = RecordType(name="Priv", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER)
    foo_private = RecordType(
        name="Foo",
        kind="struct",
        fields=[TypeField(name="p", type="Priv*")],
        origin=ScopeOrigin.PRIVATE_HEADER,
    )
    graph = build_header_only_graph(_snapshot(types=[foo_public, priv, foo_private]))
    assert not any(e.kind == "TYPE_HAS_FIELD_TYPE" for e in graph.edges)


def test_flat_model_ambiguous_bare_name_across_struct_and_enum() -> None:
    # _flat_type_name_counts merges snapshot.types and snapshot.enums into
    # one shared count dict — a struct and an enum sharing a bare name must
    # be just as ambiguous as two structs sharing one, not silently exempt
    # because they're different declaration kinds.
    tag_struct = RecordType(
        name="Tag", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER
    )
    tag_enum = EnumType(name="Tag", origin=ScopeOrigin.PUBLIC_HEADER)
    public = RecordType(
        name="Public",
        kind="struct",
        fields=[TypeField(name="t", type="Tag*")],
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    graph = build_header_only_graph(
        _snapshot(types=[public, tag_struct], enums=[tag_enum])
    )
    assert not any(e.kind == "TYPE_HAS_FIELD_TYPE" for e in graph.edges)


def test_flat_model_resolves_private_type_nested_in_a_template_argument() -> None:
    # Codex review: a public function returning e.g. std::vector<Private>
    # must not stop at the whole template spelling — the private template
    # argument itself is the real dependency a public-to-internal-dependency
    # check cares about, and it was previously missed entirely (only an
    # unresolved edge to the literal "std::vector<Private>" string was
    # created).
    private = RecordType(
        name="Private", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER
    )
    fn = Function(
        name="f",
        mangled="_Z1fv",
        return_type="std::vector<Private>",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    graph = build_header_only_graph(_snapshot(functions=[fn], types=[private]))
    edges = [e for e in graph.edges if e.kind == "DECL_HAS_TYPE"]
    private_edge = next(e for e in edges if e.dst == "type://Private")
    assert private_edge.attrs["resolution"] == "unique_candidate"
    node_by_id = {n.id: n for n in graph.nodes}
    assert node_by_id["type://Private"].attrs["visibility"] == "private_header"


def test_flat_model_never_stamps_call_graph_pass() -> None:
    # No bodies are ever visible to the flat model, in any circumstance — a
    # header-only-confirmed call-graph pass would falsely vouch for a
    # project-wide zero on DECL_CALLS_DECL/DECL_REFERENCES_DECL.
    public = RecordType(name="Public", kind="struct", origin=ScopeOrigin.PUBLIC_HEADER)
    graph = build_header_only_graph(_snapshot(types=[public]))
    assert HEADER_CALL_GRAPH_PASS not in graph.extractor_passes
    assert graph.extractor_passes == {HEADER_TYPE_GRAPH_PASS: True}


def test_flat_model_never_stamps_type_graph_pass_on_scope_fallback() -> None:
    # Codex review: a PE/Mach-O header-scoped dump that fell back to
    # export-table mode (mangling mismatch, or an unavailable header backend)
    # never actually ran a real header parse — its functions/types are
    # placeholder export-table entries or a PDB-recovered approximation, not
    # a genuine structural scan. Stamping HEADER_TYPE_GRAPH_PASS here would
    # let a later real-header dump's first structural edge misread as newly
    # added (the same false-positive class the header-only-vs-build-
    # integrated fix already guards against).
    public = RecordType(name="Public", kind="struct", origin=ScopeOrigin.UNKNOWN)
    graph = build_header_only_graph(
        _snapshot(types=[public], scope_fallback="mangling-fallback")
    )
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


# ── include_search_dirs widening (Codex review, fresh evidence) ─────────────


def test_header_node_respects_widened_include_search_dirs() -> None:
    """A header reached transitively under an explicit -I root (not itself
    named as -H) is promoted to PUBLIC_HEADER at the per-declaration level
    by apply_provenance's own include_search_dirs widening -- the header
    GRAPH must agree, not independently reclassify the same header's own
    node as private using only the bare public_header_paths/public_dir_paths
    (the real regression: a type could read public_header for its own
    declaration but private_header for its own defining header node)."""
    transitive_header = "/proj/include/detail/impl.h"
    fn = Function(
        name="dep",
        mangled="_Z3depv",
        return_type="void",
        source_header=transitive_header,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    graph = build_header_only_graph(
        _snapshot(functions=[fn]),
        public_header_paths=[PUBLIC_HEADER],
        include_search_dirs=["/proj/include"],
    )
    node = next(n for n in graph.nodes if n.id == f"header://{transitive_header}")
    assert node.attrs["visibility"] == "public_header"


def test_header_node_include_search_dirs_omitted_keeps_prior_behavior() -> None:
    """Without include_search_dirs (a caller that never threaded -I roots
    through), the header node's own classification is unchanged -- still
    only the literal public_header_paths/public_dir_paths, matching
    test_header_node_visibility_classified_from_declarations_too above."""
    transitive_header = "/proj/include/detail/impl.h"
    fn = Function(
        name="dep",
        mangled="_Z3depv",
        return_type="void",
        source_header=transitive_header,
        origin=ScopeOrigin.PRIVATE_HEADER,
    )
    graph = build_header_only_graph(
        _snapshot(functions=[fn]), public_header_paths=[PUBLIC_HEADER]
    )
    node = next(n for n in graph.nodes if n.id == f"header://{transitive_header}")
    assert node.attrs["visibility"] == "private_header"


def test_include_search_dirs_cannot_opt_in_classification_by_itself() -> None:
    """ADR-015 D4's opt-in contract: include_search_dirs alone (no real
    public_header_paths/public_dir_paths) must never turn classification on."""
    fn = Function(
        name="dep",
        mangled="_Z3depv",
        return_type="void",
        source_header="/proj/include/detail/impl.h",
    )
    graph = build_header_only_graph(
        _snapshot(functions=[fn]), include_search_dirs=["/proj/include"]
    )
    node = next(n for n in graph.nodes if n.id == "decl://_Z3depv")
    assert "visibility" not in node.attrs


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

    monkeypatch.setattr(ig.deadline, "run_bounded", lambda *a, **k: _Proc())

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

    monkeypatch.setattr(ig.deadline, "run_bounded", _fake_run)
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

    monkeypatch.setattr(ig.deadline, "run_bounded", lambda *a, **k: _Proc())

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


def test_ast_only_reference_source_gets_visibility_even_when_unseeded() -> None:
    # Codex review: the *source* side of a DECL_REFERENCES_DECL edge can be
    # unseeded too, not just the target — a field's default member
    # initializer (`struct Widget { int x = detail::k; };`) makes
    # `Widget::x` the edge's source, and a field has no equivalent entity in
    # snapshot.functions/snapshot.variables to seed from either. Without a
    # declaring-file backfill for the source too, Widget::x carried no
    # visibility at all, so the public struct's dependency on the private
    # constant through it was invisible to public_to_internal_dependency.
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "detail",
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "k",
                    "mangledName": "_ZN6detail1kE",
                    "loc": _loc(PRIVATE_HEADER),
                    "type": {"qualType": "const int"},
                },
            ],
        },
        {
            "kind": "CXXRecordDecl",
            "name": "Widget",
            "loc": _loc(PUBLIC_HEADER),
            "inner": [
                {
                    "kind": "FieldDecl",
                    "name": "x",
                    "loc": _loc(PUBLIC_HEADER),
                    "type": {"qualType": "int"},
                    "inner": [
                        {
                            "kind": "ImplicitCastExpr",
                            "inner": [
                                {
                                    "kind": "DeclRefExpr",
                                    "referencedDecl": {
                                        "kind": "VarDecl",
                                        "name": "k",
                                        "mangledName": "_ZN6detail1kE",
                                    },
                                }
                            ],
                        }
                    ],
                },
            ],
        },
    )
    graph = build_header_only_graph(
        _snapshot(), ast, public_header_paths=[PUBLIC_HEADER]
    )
    node_by_id = {n.id: n for n in graph.nodes}
    src_id = "decl://Widget::x"
    dst_id = "decl://_ZN6detail1kE"
    assert node_by_id[src_id].attrs["visibility"] == "public_header"
    assert node_by_id[dst_id].attrs["visibility"] == "private_header"
    assert any(
        e.kind == "DECL_REFERENCES_DECL" and e.src == src_id and e.dst == dst_id
        for e in graph.edges
    )
    exported: set[str] = set()
    assert is_internal_dependency_node(dst_id, node_by_id, exported, {})


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

    monkeypatch.setattr(ig.deadline, "run_bounded", _fake_run)
    ClangHeaderIncludeExtractor().extract(
        [str(pub)], [], sysroot="/opt/cross-sysroot", nostdinc=True
    )
    assert "--sysroot=/opt/cross-sysroot" in seen_argv["cmd"]
    assert "-nostdinc" in seen_argv["cmd"]
