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

"""Declaration-identity discriminator tests for
:mod:`abicheck.buildsource.template_graph` (G29 Phase 5 item 1).

Split out of ``test_template_graph.py`` once that file reached the
AI-readiness 2000-line hard cap (CLAUDE.md's "prefer extending a split-out
module" guidance) -- home for tests specifically about *how two distinct
function templates sharing a qualified name are told apart*, as opposed to
the parent file's broader AST-shape/parsing coverage.
"""

from __future__ import annotations

from abicheck.buildsource.graph_facts import GraphNode
from abicheck.buildsource.source_graph import SourceGraphSummary
from abicheck.buildsource.template_graph import parse_clang_ast_templates
from abicheck.buildsource.template_graph_fold import (
    EDGE_DECL_INSTANTIATES_TEMPLATE,
    EDGE_INSTANTIATION_EMITS_SYMBOL,
    NODE_TEMPLATE_DECL,
    NODE_TEMPLATE_INSTANTIATION,
    augment_graph_with_templates,
)


def test_function_templates_differing_only_in_template_parameter_list_stay_distinct() -> (
    None
):
    """``template <class T> void f()`` and ``template <class T, class U>
    void f()`` both print the *identical* function-type spelling ``"void
    ()"`` -- the function parameter list is genuinely empty for both, and
    clang's printer never reflects the *template* parameter list in that
    spelling at all -- confirmed against a real compiled/dumped pair
    (Codex review, fresh evidence beyond the earlier overload fix). The
    pattern-type discriminator alone therefore still collapsed both onto
    one shared ``template_decl`` node despite their own instantiations
    correctly staying separate (distinct mangled names), falsely making
    both ``DECL_INSTANTIATES_TEMPLATE`` edges point at a declaration the
    other template didn't actually come from."""

    def f(mangled: str, param_decls: list[dict]) -> dict:
        return {
            "kind": "FunctionTemplateDecl",
            "name": "f",
            "inner": [
                *param_decls,
                {"kind": "FunctionDecl", "name": "f", "type": {"qualType": "void ()"}},
                {
                    "kind": "FunctionDecl",
                    "name": "f",
                    "mangledName": mangled,
                    "inner": [],
                },
            ],
        }

    one_param = f("_Z1fIiEvv", [{"kind": "TemplateTypeParmDecl", "name": "T"}])
    two_params = f(
        "_Z1fIidEvv",
        [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            {"kind": "TemplateTypeParmDecl", "name": "U"},
        ],
    )
    ast = {"kind": "TranslationUnitDecl", "inner": [one_param, two_params]}
    out = parse_clang_ast_templates(ast)
    function_insts = [i for i in out if i.kind == "function"]
    assert len(function_insts) == 2
    # Both instantiations correctly stay distinct...
    assert {i.emitted_symbols for i in function_insts} == {
        ("_Z1fIiEvv",),
        ("_Z1fIidEvv",),
    }

    graph = SourceGraphSummary()
    augment_graph_with_templates(graph, out)
    decl_nodes = {n.id for n in graph.nodes if n.kind == NODE_TEMPLATE_DECL}
    # ...and so must their own abstract declarations, one per template.
    assert len(decl_nodes) == 2
    instantiates_edges = {
        (e.src, e.dst) for e in graph.edges if e.kind == EDGE_DECL_INSTANTIATES_TEMPLATE
    }
    # Each instantiation's edge must land on a *different* declaration node.
    targets = {dst for _src, dst in instantiates_edges}
    assert len(targets) == 2


def test_partial_specialization_instantiation_gets_a_distinct_template_decl() -> None:
    """``C<int>`` (selects the primary pattern) and ``C<int*>`` (selects the
    partial specialization ``template <typename T> struct C<T*> {...}``)
    both resolve their ``ClassTemplateSpecializationDecl`` stub's
    ``template_qname`` to the same bare ``"C"`` -- clang nests both directly
    under the primary ``ClassTemplateDecl`` regardless of which pattern was
    actually selected, and the ``ClassTemplatePartialSpecializationDecl``
    itself is a *detached* top-level sibling, never a child of it (Codex
    review, verified against Clang 17/18 real AST output). An earlier
    revision therefore mapped both to the identical ``template_decl://C``
    node even though ``C<int*>`` genuinely instantiates a distinct declared
    pattern, merging the two patterns' provenance. Fixture mirrors the exact
    shape confirmed via a real ``clang -ast-dump=json`` dump of
    ``template <typename T> struct C { void primary(); }; template
    <typename T> struct C<T*> { void ptr_spec(); }; C<int> a; C<int*> b;``:
    the implicit specialization for ``C<int*>`` shares its own
    ``loc.offset`` with the detached partial spec's declaration, while
    ``C<int>``'s specialization does not."""
    primary_pattern = {"kind": "CXXRecordDecl", "name": "C", "inner": []}
    spec_int = {
        "kind": "ClassTemplateSpecializationDecl",
        "name": "C",
        "id": "spec_int",
        "completeDefinition": True,
        "loc": {"offset": 29},
        "inner": [{"kind": "TemplateArgument", "type": {"qualType": "int"}}],
    }
    spec_ptr = {
        "kind": "ClassTemplateSpecializationDecl",
        "name": "C",
        "id": "spec_ptr",
        "completeDefinition": True,
        # Shares the partial spec's own loc.offset -- the signal this
        # module uses to detect which pattern generated it.
        "loc": {"offset": 87},
        "inner": [{"kind": "TemplateArgument", "type": {"qualType": "int *"}}],
    }
    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "C",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            primary_pattern,
            spec_int,
            spec_ptr,
        ],
    }
    # Detached top-level sibling, per the confirmed AST shape -- NOT nested
    # under class_template.
    partial_spec = {
        "kind": "ClassTemplatePartialSpecializationDecl",
        "name": "C",
        "id": "partial1",
        "loc": {"offset": 87},
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "type-parameter-0-0 *"}}
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [class_template, partial_spec]}

    out = parse_clang_ast_templates(ast)
    record_insts = {i.label: i for i in out if i.kind == "record"}
    assert set(record_insts) == {"C<int>", "C<int *>"}
    # Only the partial-specialization-selected instantiation gets a
    # discriminating signature -- the primary-pattern one stays empty.
    assert record_insts["C<int>"].template_signature == ""
    assert record_insts["C<int *>"].template_signature != ""

    graph = SourceGraphSummary()
    augment_graph_with_templates(graph, out)
    decl_nodes = {n.id for n in graph.nodes if n.kind == NODE_TEMPLATE_DECL}
    # Distinct template_decl nodes -- the primary and the partial spec are
    # genuinely different declared patterns, not one merged identity.
    assert len(decl_nodes) == 2
    instantiates_edges = {
        (e.src, e.dst) for e in graph.edges if e.kind == EDGE_DECL_INSTANTIATES_TEMPLATE
    }
    targets = {dst for _src, dst in instantiates_edges}
    assert len(targets) == 2


def test_partial_specialization_offset_match_is_scoped_by_file() -> None:
    """``loc.offset`` is *file*-relative in clang's AST dump, not
    TU-relative -- two headers included by the same TU routinely reuse the
    identical numeric offset for their own, entirely unrelated declarations
    (Codex review, second round, fresh evidence, confirmed empirically: a
    real two-header TU had an unrelated primary-pattern class ``B`` in one
    header land at the exact same ``loc.offset`` as a genuine partial
    specialization ``A<T*>`` in a different header). An earlier revision's
    offset-only match let ``B<int>`` (which selects only its own, ordinary
    primary pattern -- ``B`` declares no partial specialization at all)
    wrongly inherit ``A``'s partial-pattern signature purely because their
    declarations happened to share a byte position within their own,
    different files -- ``B<int>`` and the real ``A<int*>`` would then
    collide onto the same discriminated ``template_decl`` node. Fixture:
    ``A``'s partial spec and ``B``'s own specialization share the identical
    ``loc.offset`` (87) but declare different ``loc.file`` values."""
    a_primary_pattern = {"kind": "CXXRecordDecl", "name": "A", "inner": []}
    a_spec_ptr = {
        "kind": "ClassTemplateSpecializationDecl",
        "name": "A",
        "id": "a_spec_ptr",
        "completeDefinition": True,
        "loc": {"offset": 87, "file": "h1.h"},
        "inner": [{"kind": "TemplateArgument", "type": {"qualType": "int *"}}],
    }
    a_class_template = {
        "kind": "ClassTemplateDecl",
        "name": "A",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            a_primary_pattern,
            a_spec_ptr,
        ],
    }
    a_partial_spec = {
        "kind": "ClassTemplatePartialSpecializationDecl",
        "name": "A",
        "id": "a_partial",
        "loc": {"offset": 87, "file": "h1.h"},
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "type-parameter-0-0 *"}}
        ],
    }
    # B declares no partial specialization at all -- an entirely ordinary
    # class template whose own specialization coincidentally shares A's
    # partial spec's numeric offset, but in a different file.
    b_primary_pattern = {"kind": "CXXRecordDecl", "name": "B", "inner": []}
    b_spec_int = {
        "kind": "ClassTemplateSpecializationDecl",
        "name": "B",
        "id": "b_spec_int",
        "completeDefinition": True,
        "loc": {"offset": 87, "file": "h2.h"},
        "inner": [{"kind": "TemplateArgument", "type": {"qualType": "int"}}],
    }
    b_class_template = {
        "kind": "ClassTemplateDecl",
        "name": "B",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            b_primary_pattern,
            b_spec_int,
        ],
    }
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [a_class_template, a_partial_spec, b_class_template],
    }

    out = parse_clang_ast_templates(ast)
    record_insts = {i.label: i for i in out if i.kind == "record"}
    assert set(record_insts) == {"A<int *>", "B<int>"}
    # A<int*> genuinely selected the partial spec -- keeps its signature.
    assert record_insts["A<int *>"].template_signature != ""
    # B<int> must NOT inherit A's partial-spec signature just because their
    # declarations share a numeric offset in different files.
    assert record_insts["B<int>"].template_signature == ""


def test_literal_asm_label_mangled_name_is_not_mach_o_stripped_at_collection() -> None:
    """A function given an explicit GNU ``asm("__Zfake")`` label reports
    ``mangledName: "__Zfake"`` verbatim, on any platform -- confirmed
    empirically against real clang output (``template <class T> void f(T)
    asm("__Zfake");``). This is a real, literal linker symbol name that
    only *looks* like the Mach-O double-underscore artifact
    :func:`~abicheck.buildsource.template_graph._normalize_mangled` exists
    to strip -- it is not an instance of it (Codex review, fresh evidence).
    An earlier revision stripped unconditionally at collection time, so
    ``emitted_symbols`` recorded the wrong, corrupted ``_Zfake`` -- silently
    missing the real ``__Zfake`` export, or worse, wrongly joining onto an
    unrelated ``_Zfake`` export if one happened to exist too. The strip is
    now a guarded fallback inside ``augment_graph_with_templates`` itself,
    tried only once the exact spelling fails to join."""
    pattern = {"kind": "FunctionDecl", "name": "f", "inner": []}
    instantiation = {
        "kind": "FunctionDecl",
        "name": "f",
        "mangledName": "__Zfake",
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "int"}, "inner": []},
        ],
    }
    function_template = {
        "kind": "FunctionTemplateDecl",
        "name": "f",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            pattern,
            instantiation,
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [function_template]}

    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    # Raw, unmodified spelling -- not corrupted to "_Zfake".
    assert out[0].emitted_symbols == ("__Zfake",)

    # The real ELF export is the literal "__Zfake" -- an unrelated "_Zfake"
    # also happens to exist (e.g. some other symbol's own real export).
    graph = SourceGraphSummary()
    graph.add_node(GraphNode(id="binary_symbol://__Zfake", kind="binary_symbol"))
    graph.add_node(GraphNode(id="binary_symbol://_Zfake", kind="binary_symbol"))
    augment_graph_with_templates(graph, out)
    emits_edges = {
        (e.src, e.dst) for e in graph.edges if e.kind == EDGE_INSTANTIATION_EMITS_SYMBOL
    }
    # Must join the real, exact "__Zfake" export -- never the unrelated
    # "_Zfake" one, even though it also exists in the graph.
    targets = {dst for _src, dst in emits_edges}
    assert targets == {"binary_symbol://__Zfake"}


def test_function_instantiation_node_id_matches_its_own_resolved_export() -> None:
    """The node *identity* (:func:`template_instantiation_node_id`) for a
    function-kind instantiation must match whichever spelling actually
    resolved to a real export -- **not** an independent, unconditional
    normalization (self-review round, second pass, fresh evidence): an
    earlier revision of this fix normalized ``function_mangled``
    unconditionally, which merged two genuinely distinct instantiations,
    each with its own real, independently exported asm-labeled symbol
    (``__Zfake``/``_Zfake``), onto the identical ``template_instantiation://
    _Zfake`` node -- see ``test_two_asm_labeled_instantiations_never_merge_
    onto_one_node``. When only the Mach-O-*stripped* form has a matching
    ``binary_symbol`` node (the real Mach-O scenario -- clang's raw
    ``mangledName`` carries the platform's extra underscore, but
    ``macho_metadata.py`` already stripped it before minting the node), the
    resolved, stripped spelling is correctly used for the node id too --
    platform-stable, matching what a Linux/Windows build of the identical
    source would produce."""
    pattern = {"kind": "FunctionDecl", "name": "identity", "inner": []}
    instantiation = {
        "kind": "FunctionDecl",
        "name": "identity",
        "mangledName": "__Z8identityIiET_S0_",
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "int"}, "inner": []},
        ],
    }
    function_template = {
        "kind": "FunctionTemplateDecl",
        "name": "identity",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            pattern,
            instantiation,
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [function_template]}

    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    # emitted_symbols itself stays raw...
    assert out[0].emitted_symbols == ("__Z8identityIiET_S0_",)

    # Only the normalized (single-underscore) form has a real export node --
    # the actual Mach-O shape.
    graph = SourceGraphSummary()
    graph.add_node(
        GraphNode(id="binary_symbol://_Z8identityIiET_S0_", kind="binary_symbol")
    )
    augment_graph_with_templates(graph, out)
    inst_nodes = [n for n in graph.nodes if n.kind == NODE_TEMPLATE_INSTANTIATION]
    assert len(inst_nodes) == 1
    assert inst_nodes[0].id == "template_instantiation://_Z8identityIiET_S0_"


def test_two_asm_labeled_instantiations_never_merge_onto_one_node() -> None:
    """Two genuinely distinct function-template instantiations, each with
    its own real, independently exported asm-labeled symbol (``__Zfake``
    and ``_Zfake`` -- confirmed reachable via ``f(T) asm("__Zfake")``/
    ``f(T) asm("_Zfake")`` on two overloads or two distinct templates) must
    never collapse onto one ``template_instantiation`` node (Codex review,
    fresh evidence, second round on this same fix): an earlier revision
    unconditionally normalized both instantiations' own ``function_mangled``
    for node-identity purposes, and normalizing ``__Zfake``/``_Zfake`` both
    reduces to the identical ``_Zfake`` spelling -- merging two genuinely
    distinct instantiations (and their independent
    ``EDGE_INSTANTIATION_EMITS_SYMBOL`` edges) onto one node, exactly the
    provenance-merging failure mode the original asm-label fix was meant to
    close, just relocated to node-identity construction instead of the
    symbol join."""

    def f(mangled: str, argtype: str) -> dict:
        pattern = {"kind": "FunctionDecl", "name": "f", "inner": []}
        instantiation = {
            "kind": "FunctionDecl",
            "name": "f",
            "mangledName": mangled,
            "inner": [
                {"kind": "TemplateArgument", "type": {"qualType": argtype}, "inner": []}
            ],
        }
        return {
            "kind": "FunctionTemplateDecl",
            "name": "f",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                pattern,
                instantiation,
            ],
        }

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [f("__Zfake", "int"), f("_Zfake", "double")],
    }
    out = parse_clang_ast_templates(ast)
    assert len(out) == 2

    # Both real exports genuinely exist -- each instantiation's own.
    graph = SourceGraphSummary()
    graph.add_node(GraphNode(id="binary_symbol://__Zfake", kind="binary_symbol"))
    graph.add_node(GraphNode(id="binary_symbol://_Zfake", kind="binary_symbol"))
    augment_graph_with_templates(graph, out)

    inst_nodes = [n for n in graph.nodes if n.kind == NODE_TEMPLATE_INSTANTIATION]
    assert {n.id for n in inst_nodes} == {
        "template_instantiation://__Zfake",
        "template_instantiation://_Zfake",
    }
    emits_edges = {
        (e.src, e.dst) for e in graph.edges if e.kind == EDGE_INSTANTIATION_EMITS_SYMBOL
    }
    # Each instantiation's own edge must land on its OWN real export --
    # never the other one's.
    assert emits_edges == {
        ("template_instantiation://__Zfake", "binary_symbol://__Zfake"),
        ("template_instantiation://_Zfake", "binary_symbol://_Zfake"),
    }


def test_decltype_auto_nttp_bare_value_argument_instantiation_is_skipped() -> None:
    """A ``decltype(auto)`` non-type template parameter (``template
    <decltype(auto) V> struct A;``) has the identical bare-``{"value": N}``
    ambiguity as a plain ``auto`` NTTP (see ``test_auto_nttp_bare_value_
    argument_instantiation_is_skipped_not_merged`` in the parent file), but
    clang spells its own ``NonTypeTemplateParmDecl.type.qualType`` as
    ``"decltype(auto)"``, not ``"auto"`` -- verified empirically against
    real clang 20 AST output (Codex review, fresh evidence, second round on
    this same guard): an exact ``== "auto"`` check missed this spelling
    entirely, so ``A<E1::X>``/``A<E2::X>`` (two distinct scoped enums
    sharing the same underlying value) still reduced to the identical
    ambiguous label ``"A<0>"`` and collapsed onto one node. Fixed by
    matching the substring ``"auto"`` instead of exact equality, covering
    ``"decltype(auto)"`` (and any cv/ref/pointer-decorated form) too."""

    def a_spec(spec_id: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "A",
            "completeDefinition": True,
            "inner": [{"kind": "TemplateArgument", "value": 0}],
        }

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "A",
                "inner": [
                    {
                        "kind": "NonTypeTemplateParmDecl",
                        "name": "V",
                        "type": {"qualType": "decltype(auto)"},
                    },
                    {"kind": "CXXRecordDecl", "name": "A", "inner": []},
                    a_spec("0xA_E1"),
                    a_spec("0xA_E2"),
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    assert out == []
