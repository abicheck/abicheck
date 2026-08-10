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

"""Tests for G29 Phase 5 item 2: the macro/config dependency graph
(``MACRO_CONTROLS_DECL``/``DECL_USES_MACRO``).

Pure-parser tests (``scan_conditional_regions``, ``parse_clang_ast_decl_ranges``,
``find_decl_macro_uses``) need no compiler; the live subprocess path is
integration-only (guarded by ``shutil.which("clang++")``, mirroring
``test_type_graph.py``/``test_template_graph.py``)."""

from __future__ import annotations

import os
import shutil

import pytest

from abicheck.buildsource.graph_facts import CONF_HIGH, CONF_REDUCED, GraphNode
from abicheck.buildsource.macro_graph import (
    ClangMacroGraphExtractor,
    ConditionalRegion,
    DeclRange,
    _macro_definition_lines,
    augment_graph_with_macro_dependencies,
    find_decl_macro_uses,
    parse_clang_ast_decl_ranges,
    scan_conditional_regions,
)
from abicheck.buildsource.source_graph import SourceGraphSummary

# ── fixtures ─────────────────────────────────────────────────────────────


def _decl_node(name: str) -> GraphNode:
    return GraphNode(
        id=f"decl://{name}",
        kind="source_decl",
        label=name,
        provenance="source_abi",
        confidence=CONF_HIGH,
    )


def _macro_node(name: str) -> GraphNode:
    return GraphNode(
        id=f"macro://{name}",
        kind="macro",
        label=name,
        provenance="source_abi",
        confidence=CONF_HIGH,
    )


def _loc(file: str, line: int, col: int = 1) -> dict:
    return {"file": file, "line": line, "col": col, "offset": 0, "tokLen": 1}


def _func(
    name: str, mangled: str, file: str, begin: int, end: int, inner: list | None = None
) -> dict:
    """A hand-built FunctionDecl node with explicit (always-present, never
    sticky-suppressed) loc/range — simpler than replicating clang's own
    suppression for unit fixtures, and equivalent input for the parser
    (:class:`~abicheck.buildsource.macro_graph._LocationCursor` treats an
    always-present field identically to a sparsely-present one)."""
    return {
        "kind": "FunctionDecl",
        "name": name,
        "mangledName": mangled,
        "loc": _loc(file, begin),
        "range": {"begin": _loc(file, begin), "end": _loc(file, end)},
        "type": {"qualType": "void ()"},
        "inner": inner or [],
    }


def _record(
    name: str, file: str, begin: int, end: int, inner: list | None = None
) -> dict:
    return {
        "kind": "CXXRecordDecl",
        "name": name,
        "loc": _loc(file, begin),
        "range": {"begin": _loc(file, begin), "end": _loc(file, end)},
        "inner": inner or [],
    }


def _typedef(name: str, file: str, line: int) -> dict:
    return {
        "kind": "TypedefDecl",
        "name": name,
        "loc": _loc(file, line),
        "range": {"begin": _loc(file, line), "end": _loc(file, line)},
    }


def _namespace(name: str, inner: list) -> dict:
    return {"kind": "NamespaceDecl", "name": name, "inner": inner}


def _tu(*decls: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(decls)}


def _expansion_loc(file: str, line: int) -> dict:
    """A clang-macro-generated location: nested ``spellingLoc``/
    ``expansionLoc`` instead of flat ``file``/``line`` keys (Codex review, PR
    #708 — confirmed against real clang output for a macro-generated
    declaration). ``spellingLoc`` points at the macro's own replacement text
    (deliberately a different, irrelevant file here, to prove
    ``_LocationCursor`` prefers ``expansionLoc``)."""
    return {
        "spellingLoc": {"file": "macros.h", "line": 1, "offset": 0},
        "expansionLoc": {"file": file, "line": line, "offset": 0},
    }


# ── scan_conditional_regions ─────────────────────────────────────────────


def test_scan_ifdef_simple() -> None:
    text = "#ifdef FEATURE_X\nvoid f();\n#endif\n"
    regions = scan_conditional_regions(text)
    assert regions == [ConditionalRegion("FEATURE_X", False, 2, 2)]


def test_scan_ifndef_simple() -> None:
    text = "#ifndef FEATURE_X\nvoid f();\n#endif\n"
    regions = scan_conditional_regions(text)
    assert regions == [ConditionalRegion("FEATURE_X", True, 2, 2)]


def test_scan_if_defined() -> None:
    text = "#if defined(FEATURE_X)\nvoid f();\n#endif\n"
    regions = scan_conditional_regions(text)
    assert regions == [ConditionalRegion("FEATURE_X", False, 2, 2)]


def test_scan_if_not_defined() -> None:
    text = "#if !defined(FEATURE_X)\nvoid f();\n#endif\n"
    regions = scan_conditional_regions(text)
    assert regions == [ConditionalRegion("FEATURE_X", True, 2, 2)]


def test_scan_ifdef_with_trailing_line_comment() -> None:
    # Codex review, PR #708: a trailing `//` comment on an otherwise-simple
    # guard must not defeat the end-anchored simple-form patterns.
    text = "#ifdef FEATURE_X // enabled by build\nvoid f();\n#endif\n"
    regions = scan_conditional_regions(text)
    assert regions == [ConditionalRegion("FEATURE_X", False, 2, 2)]


def test_scan_ifndef_with_trailing_line_comment() -> None:
    text = "#ifndef FEATURE_X // disabled by build\nvoid f();\n#endif\n"
    regions = scan_conditional_regions(text)
    assert regions == [ConditionalRegion("FEATURE_X", True, 2, 2)]


def test_scan_if_defined_with_trailing_block_comment() -> None:
    text = "#if defined(FEATURE_X) /* config */\nvoid f();\n#endif\n"
    regions = scan_conditional_regions(text)
    assert regions == [ConditionalRegion("FEATURE_X", False, 2, 2)]


def test_scan_if_not_defined_with_trailing_block_comment() -> None:
    text = "#if !defined(FEATURE_X) /* config */\nvoid f();\n#endif\n"
    regions = scan_conditional_regions(text)
    assert regions == [ConditionalRegion("FEATURE_X", True, 2, 2)]


def test_scan_else_branch_negates_simple_ifdef() -> None:
    text = "#ifdef FEATURE_X\nvoid a();\n#else\nvoid b();\n#endif\n"
    regions = scan_conditional_regions(text)
    assert regions == [
        ConditionalRegion("FEATURE_X", False, 2, 2),
        ConditionalRegion("FEATURE_X", True, 4, 4),
    ]


def test_scan_else_branch_negates_simple_ifndef() -> None:
    text = "#ifndef FEATURE_X\nvoid a();\n#else\nvoid b();\n#endif\n"
    regions = scan_conditional_regions(text)
    assert regions == [
        ConditionalRegion("FEATURE_X", True, 2, 2),
        ConditionalRegion("FEATURE_X", False, 4, 4),
    ]


def test_scan_nested_same_macro_yields_two_regions_for_inner_line() -> None:
    # A decl nested inside two stacked guards on the SAME macro still gets
    # two independent regions, one per nesting level: the tight inner one
    # and the wider outer one (which also spans the inner guard's own
    # directives) — both contain f()'s line (3).
    text = "#ifdef A\n#ifdef A\nvoid f();\n#endif\n#endif\n"
    regions = scan_conditional_regions(text)
    assert ConditionalRegion("A", False, 3, 3) in regions
    assert ConditionalRegion("A", False, 2, 4) in regions
    assert len(regions) == 2
    assert all(r.start_line <= 3 <= r.end_line for r in regions)


def test_scan_nested_different_macros_both_cover_inner_line() -> None:
    text = "#ifdef A\n#ifdef B\nvoid f();\n#endif\n#endif\n"
    regions = scan_conditional_regions(text)
    assert ConditionalRegion("A", False, 2, 4) in regions
    assert ConditionalRegion("B", False, 3, 3) in regions
    assert len(regions) == 2


def test_scan_sibling_regions_independent() -> None:
    text = "#ifdef A\nvoid a();\n#endif\nvoid mid();\n#ifdef B\nvoid b();\n#endif\n"
    regions = scan_conditional_regions(text)
    assert regions == [
        ConditionalRegion("A", False, 2, 2),
        ConditionalRegion("B", False, 6, 6),
    ]


def test_scan_compound_condition_skipped_but_nesting_maintained() -> None:
    # The compound #if contributes no region, but an outer simple guard around
    # it must still see its own #endif correctly (no depth desync), and a
    # sibling guard after it must resolve to the right lines.
    text = (
        "#ifdef OUTER\n"
        "#if defined(X) && defined(Y)\n"
        "void inner();\n"
        "#endif\n"
        "void after_compound();\n"
        "#endif\n"
        "#ifdef SIBLING\n"
        "void sib();\n"
        "#endif\n"
    )
    regions = scan_conditional_regions(text)
    assert ConditionalRegion("OUTER", False, 2, 5) in regions
    assert ConditionalRegion("SIBLING", False, 8, 8) in regions
    assert not any(r.macro in ("X", "Y") for r in regions)
    assert len(regions) == 2


def test_scan_ignores_directives_inside_block_comment() -> None:
    # Codex review, fresh evidence: a block-commented-out #ifdef/#endif pair
    # around an ordinary declaration must not be treated as a live directive
    # -- no region, no false CONF_HIGH MACRO_CONTROLS_DECL edge.
    text = "/*\n#ifdef FEATURE_X\nvoid a();\n#endif\n*/\nvoid real_decl();\n"
    assert scan_conditional_regions(text) == []


def test_scan_real_guard_after_block_comment_still_recognized() -> None:
    text = (
        "/* an old example:\n"
        "#ifdef OLD\n"
        "#endif\n"
        "*/\n"
        "#ifdef FEATURE_X\n"
        "void f();\n"
        "#endif\n"
    )
    regions = scan_conditional_regions(text)
    assert regions == [ConditionalRegion("FEATURE_X", False, 6, 6)]


def test_lines_starting_inside_block_comment_single_line_comment_unaffected() -> None:
    from abicheck.buildsource.macro_graph import _lines_starting_inside_block_comment

    text = "/* one-line comment */\n#ifdef FEATURE_X\nvoid f();\n#endif\n"
    starts = _lines_starting_inside_block_comment(text)
    assert starts == [False, False, False, False]
    regions = scan_conditional_regions(text)
    assert regions == [ConditionalRegion("FEATURE_X", False, 3, 3)]


def test_macro_definition_lines_ignores_define_inside_block_comment() -> None:
    text = "/* #define FEATURE_X 1 */\nvoid f() { return FEATURE_X; }\n"
    assert _macro_definition_lines(text) == {}


def test_macro_definition_lines_recognizes_compact_function_like_macro() -> None:
    # Codex review, fresh evidence: no space after `)` is still a valid
    # function-like definition to a real preprocessor.
    text = "#define ATTR(x)__attribute__((x))\n"
    assert _macro_definition_lines(text) == {"ATTR": 1}


def test_find_decl_macro_uses_finds_compact_function_like_macro() -> None:
    text = "#define ATTR(x)__attribute__((x))\nvoid f() ATTR(unused);\n"
    defines = _macro_definition_lines(text)
    decl = DeclRange("f", "t.h", 2, 2)
    uses = find_decl_macro_uses(text, decl, frozenset({"ATTR"}), defines)
    assert uses == frozenset({"ATTR"})


def test_scan_compound_condition_else_branch_also_unmodeled() -> None:
    text = "#if defined(X) && defined(Y)\nvoid a();\n#else\nvoid b();\n#endif\n"
    assert scan_conditional_regions(text) == []


def test_scan_elif_chain_unmodeled_but_nesting_maintained() -> None:
    text = (
        "#ifdef A\n"
        "void a();\n"
        "#elif defined(B)\n"
        "void b();\n"
        "#else\n"
        "void c();\n"
        "#endif\n"
        "#ifdef SIBLING\n"
        "void sib();\n"
        "#endif\n"
    )
    regions = scan_conditional_regions(text)
    assert not any(r.macro == "A" for r in regions)
    assert regions == [ConditionalRegion("SIBLING", False, 9, 9)]


def test_scan_deeply_nested_four_levels() -> None:
    lines = [f"#ifdef L{i}" for i in range(1, 5)]
    lines.append("void deep();")
    lines += ["#endif"] * 4
    lines.append("#ifdef AFTER")
    lines.append("void after();")
    lines.append("#endif")
    text = "\n".join(lines) + "\n"
    regions = scan_conditional_regions(text)
    # deep() is on line 5; each of the 4 stacked guards' region contains it
    # (the innermost is tight (5,5); each enclosing one is wider, also
    # spanning the nested guards' own directive lines).
    assert len(regions) == 5
    for r in regions[:4]:
        assert r.start_line <= 5 <= r.end_line
    assert {r.macro for r in regions[:4]} == {"L1", "L2", "L3", "L4"}
    # AFTER starts right after the 4 #endifs close cleanly (no depth desync).
    assert ConditionalRegion("AFTER", False, 11, 11) in regions


def test_scan_empty_region_not_emitted() -> None:
    text = "#ifdef A\n#endif\n"
    assert scan_conditional_regions(text) == []


# ── parse_clang_ast_decl_ranges ──────────────────────────────────────────


def test_parse_decl_ranges_function() -> None:
    ast = _tu(_func("foo", "_Z3foov", "t.h", 3, 3))
    ranges = parse_clang_ast_decl_ranges(ast)
    assert ranges == [DeclRange("_Z3foov", "t.h", 3, 3)]


def test_parse_decl_ranges_record_and_field_span() -> None:
    ast = _tu(_record("Widget", "t.h", 5, 8))
    ranges = parse_clang_ast_decl_ranges(ast)
    assert ranges == [DeclRange("Widget", "t.h", 5, 8)]


def test_parse_decl_ranges_typedef() -> None:
    ast = _tu(_typedef("Handle", "t.h", 2))
    ranges = parse_clang_ast_decl_ranges(ast)
    assert ranges == [DeclRange("Handle", "t.h", 2, 2)]


def test_parse_decl_ranges_namespaced_scope_qualifies_identity() -> None:
    ast = _tu(_namespace("ns", [_record("Widget", "t.h", 4, 6)]))
    ranges = parse_clang_ast_decl_ranges(ast)
    assert ranges == [DeclRange("ns::Widget", "t.h", 4, 6)]


def test_parse_decl_ranges_class_member_function() -> None:
    method = _func("run", "_ZN3Foo3runEv", "t.h", 5, 5)
    ast = _tu(_record("Foo", "t.h", 4, 6, inner=[method]))
    ranges = parse_clang_ast_decl_ranges(ast)
    idents = {r.identity for r in ranges}
    assert "Foo" in idents
    assert "_ZN3Foo3runEv" in idents


def test_parse_decl_ranges_skips_node_with_no_range() -> None:
    # An implicit/compiler-synthesized decl with no range at all must be
    # skipped, not crash the walk.
    broken = {"kind": "FunctionDecl", "name": "implicit_thing"}
    ast = _tu(broken, _func("after", "_Z5afterv", "t.h", 9, 9))
    ranges = parse_clang_ast_decl_ranges(ast)
    assert ranges == [DeclRange("_Z5afterv", "t.h", 9, 9)]


def test_parse_decl_ranges_local_var_in_function_body_excluded() -> None:
    local_var = {
        "kind": "VarDecl",
        "name": "x",
        "loc": _loc("t.h", 4),
        "range": {"begin": _loc("t.h", 4), "end": _loc("t.h", 4)},
        "type": {"qualType": "int"},
    }
    func = _func("f", "_Z1fv", "t.h", 3, 5, inner=[local_var])
    ast = _tu(func)
    ranges = parse_clang_ast_decl_ranges(ast)
    idents = {r.identity for r in ranges}
    assert idents == {"_Z1fv"}


def test_parse_decl_ranges_sticky_cursor_matches_real_clang_shape() -> None:
    """Reproduces the empirical sticky-cursor AST-dump shape (module
    docstring): a node's ``loc``/``range.begin``/``range.end`` may omit
    ``file``/``line`` when unchanged from the previous node's — verified this
    parser still resolves the correct absolute line for a later sibling."""
    foo = {
        "kind": "FunctionDecl",
        "name": "foo",
        "mangledName": "_Z3foov",
        "loc": {"offset": 42, "file": "t.h", "line": 3, "col": 6},
        "range": {
            "begin": {"offset": 37, "col": 1},  # no file/line: same as loc
            "end": {"offset": 46, "col": 10},  # no file/line: same line
        },
        "type": {"qualType": "void ()"},
    }
    bar = {
        "kind": "FunctionDecl",
        "name": "bar",
        "mangledName": "_Z3barv",
        "loc": {"offset": 80, "line": 7, "col": 6},  # no file: sticky from foo
        "range": {"begin": {"offset": 75, "col": 1}, "end": {"offset": 84, "col": 10}},
        "type": {"qualType": "void ()"},
    }
    ast = _tu(foo, bar)
    ranges = parse_clang_ast_decl_ranges(ast)
    assert DeclRange("_Z3foov", "t.h", 3, 3) in ranges
    assert DeclRange("_Z3barv", "t.h", 7, 7) in ranges


def test_parse_decl_ranges_macro_generated_decl_uses_expansion_loc() -> None:
    """A declaration produced by a macro carries nested ``spellingLoc``/
    ``expansionLoc`` dicts instead of flat ``file``/``line`` keys (Codex
    review, PR #708). ``_LocationCursor`` must prefer ``expansionLoc`` (the
    real source file/line — where Pass B's raw-text scan can actually find
    it) and keep tracking the cursor correctly for the sibling that follows,
    which relies on the sticky ``file`` this node resolved."""
    generated = {
        "kind": "FunctionDecl",
        "name": "generated",
        "mangledName": "_Z9generatedv",
        "loc": _expansion_loc("t.h", 10),
        "range": {
            "begin": _expansion_loc("t.h", 10),
            "end": _expansion_loc("t.h", 10),
        },
        "type": {"qualType": "void ()"},
    }
    after = {
        "kind": "FunctionDecl",
        "name": "after",
        "mangledName": "_Z5afterv",
        "loc": {"offset": 90, "line": 12, "col": 6},  # no file: sticky from `generated`
        "range": {"begin": {"offset": 85, "col": 1}, "end": {"offset": 94, "col": 10}},
        "type": {"qualType": "void ()"},
    }
    ast = _tu(generated, after)
    ranges = parse_clang_ast_decl_ranges(ast)
    assert DeclRange("_Z9generatedv", "t.h", 10, 10) in ranges
    # The sticky cursor must resolve to the real source file (t.h, from
    # expansionLoc), never the macro-definition file spellingLoc names —
    # otherwise this and every later sibling would desync onto "macros.h".
    assert not any(r.file == "macros.h" for r in ranges)
    assert DeclRange("_Z5afterv", "t.h", 12, 12) in ranges


def test_parse_decl_ranges_first_node_expansion_loc_omits_file() -> None:
    """A follow-up finding on the same nested-location fix (Codex review, PR
    #708, fresh evidence): for the *first* AST node in a TU that names a
    file at all, real clang can print ``spellingLoc.file`` while
    ``expansionLoc`` carries only ``line``/``col``/``offset`` — its own
    ``file`` is sticky-omitted against the whole-document cursor, which at
    that point hasn't been set to anything yet (confirmed against a real
    ``clang -ast-dump=json`` run of a macro-argument expansion as the very
    first declaration in a header). Choosing ``expansionLoc`` wholesale
    (an earlier fix) left the cursor's file empty here, discarding this
    declaration and every later sibling relying on the same sticky file.
    Advancing through ``spellingLoc`` first supplies the file;
    ``expansionLoc`` then still wins on ``line`` (the real invocation site,
    not the macro's own definition line)."""
    generated = {
        "kind": "FunctionDecl",
        "name": "foo",
        "mangledName": "_Z3foov",
        "loc": {
            "spellingLoc": {"file": "t.h", "line": 2, "offset": 0},
            "expansionLoc": {"line": 2, "col": 1, "offset": 0},  # no file
        },
        "range": {
            "begin": {
                "spellingLoc": {"file": "t.h", "line": 1, "offset": 0},
                "expansionLoc": {"line": 2, "offset": 0},
            },
            "end": {
                "spellingLoc": {"file": "t.h", "line": 1, "offset": 0},
                "expansionLoc": {"line": 2, "offset": 0},
            },
        },
        "type": {"qualType": "void ()"},
    }
    after = {
        "kind": "FunctionDecl",
        "name": "after",
        "mangledName": "_Z5afterv",
        "loc": {"line": 3, "col": 6, "offset": 40},  # no file: sticky from `foo`
        "range": {"begin": {"col": 1, "offset": 35}, "end": {"col": 10, "offset": 44}},
        "type": {"qualType": "void ()"},
    }
    ast = _tu(generated, after)
    ranges = parse_clang_ast_decl_ranges(ast)
    assert DeclRange("_Z3foov", "t.h", 2, 2) in ranges
    assert DeclRange("_Z5afterv", "t.h", 3, 3) in ranges


def test_parse_decl_ranges_expansion_loc_overrides_different_spelling_file() -> None:
    """The companion real-clang shape: a macro *body* (not argument)
    expansion from a genuinely different file (the macro's own definition
    site) — ``expansionLoc`` explicitly restates ``file`` back to the real
    invocation file precisely because it differs from what ``spellingLoc``
    just made sticky, so advancing through ``spellingLoc`` then
    ``expansionLoc`` must still resolve to the invocation file, never the
    macro-definition one (confirmed against a real cross-header
    ``clang -ast-dump=json`` run)."""
    generated = {
        "kind": "FunctionDecl",
        "name": "generated",
        "mangledName": "_Z9generatedv",
        "loc": {
            "spellingLoc": {"file": "macrodefs.h", "line": 1, "offset": 0},
            "expansionLoc": {"file": "user.h", "line": 2, "offset": 0},
        },
        "range": {
            "begin": {
                "spellingLoc": {"file": "macrodefs.h", "line": 1, "offset": 0},
                "expansionLoc": {"file": "user.h", "line": 2, "offset": 0},
            },
            "end": {
                "spellingLoc": {"file": "macrodefs.h", "line": 1, "offset": 0},
                "expansionLoc": {"file": "user.h", "line": 2, "offset": 0},
            },
        },
        "type": {"qualType": "void ()"},
    }
    ast = _tu(generated)
    ranges = parse_clang_ast_decl_ranges(ast)
    assert DeclRange("_Z9generatedv", "user.h", 2, 2) in ranges
    assert not any(r.file == "macrodefs.h" for r in ranges)


# ── find_decl_macro_uses ──────────────────────────────────────────────────


def test_find_decl_macro_uses_finds_macro_defined_before() -> None:
    text = "#define FEATURE_X 1\nvoid f() { return FEATURE_X; }\n"
    decl = DeclRange("f", "t.h", 2, 2)
    uses = find_decl_macro_uses(text, decl, frozenset({"FEATURE_X"}), {"FEATURE_X": 1})
    assert uses == frozenset({"FEATURE_X"})


def test_find_decl_macro_uses_ignores_macro_defined_after() -> None:
    text = "void f() { return FEATURE_X; }\n#define FEATURE_X 1\n"
    decl = DeclRange("f", "t.h", 1, 1)
    uses = find_decl_macro_uses(text, decl, frozenset({"FEATURE_X"}), {"FEATURE_X": 2})
    assert uses == frozenset()


def test_find_decl_macro_uses_overmatches_coincidental_identifier() -> None:
    # Documented heuristic limitation: an unrelated local identifier sharing
    # a defined macro's name is reported as a "use" too — this is the
    # accepted, documented false-positive, not a bug to chase.
    text = "#define FOO 1\nint f() { int FOO_LOCAL_UNRELATED = 0; return FOO; }\n"
    decl = DeclRange("f", "t.h", 2, 2)
    uses = find_decl_macro_uses(text, decl, frozenset({"FOO"}), {"FOO": 1})
    assert uses == frozenset({"FOO"})


def test_find_decl_macro_uses_excludes_define_line_itself() -> None:
    text = "#define FOO 1\nvoid f();\n"
    decl = DeclRange("FOO_is_not_a_decl", "t.h", 1, 1)
    uses = find_decl_macro_uses(text, decl, frozenset({"FOO"}), {"FOO": 1})
    assert uses == frozenset()


# ── augment_graph_with_macro_dependencies ────────────────────────────────


def _seeded_graph() -> SourceGraphSummary:
    graph = SourceGraphSummary()
    graph.add_node(_decl_node("f"))
    graph.add_node(_macro_node("FEATURE_X"))
    return graph


def test_augment_emits_controls_and_uses_edges_on_existing_nodes() -> None:
    graph = _seeded_graph()
    text = "#define FEATURE_X 1\n#ifdef FEATURE_X\nvoid f() { return FEATURE_X; }\n#endif\n"
    decls = [DeclRange("f", "t.h", 3, 3)]
    result = augment_graph_with_macro_dependencies(
        graph, decls, source_lookup={"t.h": text}
    )
    assert result.controls_edges == 1
    assert result.uses_edges == 1
    controls = [e for e in graph.edges if e.kind == "MACRO_CONTROLS_DECL"]
    uses = [e for e in graph.edges if e.kind == "DECL_USES_MACRO"]
    assert len(controls) == 1
    assert controls[0].src == "macro://FEATURE_X"
    assert controls[0].dst == "decl://f"
    assert controls[0].confidence == CONF_HIGH
    assert len(uses) == 1
    assert uses[0].src == "decl://f"
    assert uses[0].dst == "macro://FEATURE_X"
    assert uses[0].confidence == CONF_REDUCED


def test_augment_skips_decl_not_already_in_graph() -> None:
    graph = SourceGraphSummary()
    graph.add_node(_macro_node("FEATURE_X"))  # no source_decl node at all
    text = "#ifdef FEATURE_X\nvoid f();\n#endif\n"
    decls = [DeclRange("f", "t.h", 2, 2)]
    result = augment_graph_with_macro_dependencies(
        graph, decls, source_lookup={"t.h": text}
    )
    assert result.controls_edges == 0
    assert result.decls_joined == 0
    assert graph.edges == []


def test_augment_never_reads_source_for_unjoinable_decl() -> None:
    """Codex review, PR #708, fresh evidence: a TU's full declaration set
    routinely includes every system/dependency header it transitively
    includes, none of which the graph carries a node for — filtering to
    joinable declarations *before* grouping by file means this pass never
    opens a source file it has no use for (a real TU including ``<vector>``
    produced 4,258 ranges across 47 files, none joinable). A file whose only
    declarations are unjoinable must never even reach ``source_lookup``."""
    graph = _seeded_graph()  # has decl://f, no node for "unrelated"

    def _lookup(path: str) -> str | None:
        raise AssertionError(f"source_lookup called for {path!r}")

    decls = [DeclRange("unrelated", "system_header.h", 1, 1)]
    result = augment_graph_with_macro_dependencies(graph, decls, source_lookup=_lookup)
    assert result.decls_seen == 1
    assert result.decls_joined == 0
    assert result.files_scanned == 0
    assert graph.edges == []


def test_augment_unreadable_file_diagnostic_is_redacted(tmp_path, monkeypatch) -> None:
    """Codex review, PR #708, fresh evidence: `_extract_from_compile_unit`
    resolves a relative source path against the compile unit's own replayed
    cwd, so by the time a file reaches this function it may already be an
    absolute, un-redacted home/build path — and `OSError.__str__` typically
    embeds that same real path a second time. Both must be redacted before
    landing in `MacroGraphResult.diagnostics` (which `fold_macro_graph`
    copies straight into the persisted `BuildEvidence.diagnostics`), the
    same leak class + fix as `archive_graph.py`'s identical try/except
    (mirrors that module's own
    ``test_augment_graph_unreadable_archive_diagnostic_is_redacted``)."""
    from abicheck.buildsource import redaction as redaction_mod

    monkeypatch.setattr(
        redaction_mod,
        "DEFAULT_REDACTION",
        redaction_mod.RedactionPolicy(
            home_replacements={str(tmp_path): "~project-root~"}
        ),
    )
    graph = _seeded_graph()
    real_path = str(tmp_path / "t.h")
    decls = [DeclRange("f", real_path, 3, 3)]

    def _raising_lookup(path: str) -> str | None:
        raise OSError(f"[Errno 13] Permission denied: '{path}'")

    result = augment_graph_with_macro_dependencies(
        graph, decls, source_lookup=_raising_lookup
    )
    assert result.diagnostics
    assert str(tmp_path) not in result.diagnostics[0]
    assert "~project-root~" in result.diagnostics[0]


def test_augment_skips_macro_not_already_in_graph() -> None:
    graph = SourceGraphSummary()
    graph.add_node(_decl_node("f"))  # no macro node at all
    text = "#define FEATURE_X 1\n#ifdef FEATURE_X\nvoid f() { return FEATURE_X; }\n#endif\n"
    decls = [DeclRange("f", "t.h", 3, 3)]
    result = augment_graph_with_macro_dependencies(
        graph, decls, source_lookup={"t.h": text}
    )
    assert result.controls_edges == 0
    assert result.uses_edges == 0
    assert graph.edges == []


def test_augment_negated_guard_produces_no_edge_when_macro_node_absent() -> None:
    """Documents a known, accepted gap (Codex review, PR #708 — see
    ``EDGE_MACRO_CONTROLS_DECL``'s docstring entry): a negated guard
    (``#ifndef X``/``#if !defined(X)``/an ``#ifdef X``'s ``#else`` branch)
    only produces a ``MACRO_CONTROLS_DECL`` edge when the graph already
    carries a ``macro`` node for ``X`` — but ``X`` is by definition typically
    *undefined* in the scanned configuration for a negated region to be the
    active branch at all, so no ``macro`` node was ever seeded for it. This
    is the same join-only-onto-an-existing-node behavior
    ``test_augment_skips_macro_not_already_in_graph`` already pins for the
    positive form, deliberately not overridden for the negated form either."""
    graph = SourceGraphSummary()
    graph.add_node(_decl_node("f"))  # no macro node for NEVER_DEFINED at all
    text = "#ifndef NEVER_DEFINED\nvoid f();\n#endif\n"
    decls = [DeclRange("f", "t.h", 2, 2)]
    result = augment_graph_with_macro_dependencies(
        graph, decls, source_lookup={"t.h": text}
    )
    assert result.controls_edges == 0
    assert graph.edges == []


def test_augment_never_mints_new_nodes() -> None:
    graph = _seeded_graph()
    text = "#define FEATURE_X 1\n#ifdef FEATURE_X\nvoid f() { return FEATURE_X; }\n#endif\n"
    decls = [DeclRange("f", "t.h", 3, 3)]
    before = {n.id for n in graph.nodes}
    augment_graph_with_macro_dependencies(graph, decls, source_lookup={"t.h": text})
    after = {n.id for n in graph.nodes}
    assert before == after


def test_augment_read_failure_is_diagnostic_not_exception() -> None:
    graph = _seeded_graph()
    decls = [DeclRange("f", "t.h", 3, 3)]

    def _raising_lookup(path: str) -> str | None:
        raise OSError("boom")

    result = augment_graph_with_macro_dependencies(
        graph, decls, source_lookup=_raising_lookup
    )
    assert result.diagnostics
    assert "boom" in result.diagnostics[0]
    assert result.controls_edges == 0
    assert not result.complete


def test_augment_missing_source_text_is_diagnostic() -> None:
    graph = _seeded_graph()
    decls = [DeclRange("f", "t.h", 3, 3)]
    result = augment_graph_with_macro_dependencies(graph, decls, source_lookup={})
    assert result.diagnostics
    assert result.controls_edges == 0


def test_augment_empty_decl_ranges_is_no_op() -> None:
    graph = _seeded_graph()
    result = augment_graph_with_macro_dependencies(graph, [], source_lookup={})
    assert result.decls_seen == 0
    assert result.complete is False
    assert graph.edges == []


def test_augment_result_complete_when_clean() -> None:
    graph = _seeded_graph()
    text = "#ifdef FEATURE_X\nvoid f();\n#endif\n"
    decls = [DeclRange("f", "t.h", 2, 2)]
    result = augment_graph_with_macro_dependencies(
        graph, decls, source_lookup={"t.h": text}
    )
    assert result.complete is True


# ── ClangMacroGraphExtractor availability degrade ────────────────────────


def test_extract_from_compile_unit_resolves_relative_file_against_own_cwd(
    monkeypatch, tmp_path
) -> None:
    """Codex review, PR #708: clang echoes a relative source path (an
    out-of-source build's ``directory`` + relative ``source``) back into the
    AST dump's ``file`` fields exactly as given on the command line -- it
    never resolves that against the compile unit's own ``directory`` itself.
    ``_extract_from_compile_unit`` must resolve a relative ``DeclRange.file``
    against the compile unit's *own* replayed cwd, not the process cwd.

    Uses a real, OS-native ``tmp_path`` (rather than a hand-rolled POSIX-style
    literal) so the expected joined path is computed the same way on every
    platform -- a hardcoded ``"/build/src/a.h"``-shaped literal fails on
    Windows, where ``os.path.join``/``normpath`` (which the implementation
    itself correctly uses, since a real join must produce a path the host OS
    can actually open) produce backslash separators instead."""
    import abicheck.buildsource.call_graph as call_graph_mod
    from abicheck.buildsource.build_evidence import CompileUnit

    out_dir = tmp_path / "build" / "out"
    extractor = ClangMacroGraphExtractor(clang_bin="clang++")
    monkeypatch.setattr(
        call_graph_mod,
        "_safe_clang_args_from_compile_unit",
        lambda cu: ["--", cu.source],
    )
    monkeypatch.setattr(call_graph_mod, "_replay_cwd", lambda cu: str(out_dir))
    monkeypatch.setattr(
        extractor,
        "_extract_from_safe_args",
        lambda argv, cwd=None: [DeclRange("f", os.path.join("..", "src", "a.h"), 3, 3)],
    )
    cu = CompileUnit(
        id="cu://a",
        source=os.path.join("..", "src", "a.cpp"),
        input_files=[os.path.join("..", "src", "a.cpp")],
        directory=str(out_dir),
    )
    ranges = extractor._extract_from_compile_unit(cu)
    expected_file = os.path.normpath(str(tmp_path / "build" / "src" / "a.h"))
    assert ranges == [DeclRange("f", expected_file, 3, 3)]


def test_extract_from_compile_unit_leaves_absolute_file_untouched(
    monkeypatch, tmp_path
) -> None:
    import abicheck.buildsource.call_graph as call_graph_mod
    from abicheck.buildsource.build_evidence import CompileUnit

    out_dir = tmp_path / "build" / "out"
    abs_file = str(tmp_path / "abs" / "a.h")
    extractor = ClangMacroGraphExtractor(clang_bin="clang++")
    monkeypatch.setattr(
        call_graph_mod,
        "_safe_clang_args_from_compile_unit",
        lambda cu: ["--", cu.source],
    )
    monkeypatch.setattr(call_graph_mod, "_replay_cwd", lambda cu: str(out_dir))
    monkeypatch.setattr(
        extractor,
        "_extract_from_safe_args",
        lambda argv, cwd=None: [DeclRange("f", abs_file, 3, 3)],
    )
    cu = CompileUnit(
        id="cu://a", source="a.cpp", input_files=["a.cpp"], directory=str(out_dir)
    )
    ranges = extractor._extract_from_compile_unit(cu)
    assert ranges == [DeclRange("f", abs_file, 3, 3)]


def test_extract_from_build_keeps_distinct_spans_for_same_identity(monkeypatch) -> None:
    """Codex review, PR #708: a forward declaration and its own later
    definition share ``(identity, file)`` but have distinct spans -- the
    definition's span (which may carry the real macro guard/reference) must
    survive even though the forward declaration is also seen."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    extractor = ClangMacroGraphExtractor(clang_bin="clang++")
    monkeypatch.setattr(extractor, "available", lambda: True)
    forward = DeclRange("Widget", "t.h", 2, 2)
    definition = DeclRange("Widget", "t.h", 10, 14)
    per_unit = {"cu://a": [forward], "cu://b": [definition]}
    monkeypatch.setattr(
        extractor, "_extract_from_compile_unit", lambda cu: per_unit[cu.id]
    )
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp", input_files=["a.cpp"]),
            CompileUnit(id="cu://b", source="b.cpp", input_files=["b.cpp"]),
        ]
    )
    ranges = extractor.extract_from_build(build)
    assert forward in ranges
    assert definition in ranges


def test_extract_from_build_dedups_exact_duplicate_span(monkeypatch) -> None:
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    extractor = ClangMacroGraphExtractor(clang_bin="clang++")
    monkeypatch.setattr(extractor, "available", lambda: True)
    same = DeclRange("Widget", "t.h", 2, 2)
    monkeypatch.setattr(extractor, "_extract_from_compile_unit", lambda cu: [same])
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp", input_files=["a.cpp"]),
            CompileUnit(id="cu://b", source="b.cpp", input_files=["b.cpp"]),
        ]
    )
    ranges = extractor.extract_from_build(build)
    assert ranges == [same]


def test_extractor_unavailable_records_diagnostic() -> None:
    extractor = ClangMacroGraphExtractor(clang_bin="definitely-not-a-real-clang-binary")
    assert extractor.available() is False
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    build = BuildEvidence(
        compile_units=[CompileUnit(id="cu://a", source="a.cpp", input_files=["a.cpp"])]
    )
    ranges = extractor.extract_from_build(build)
    assert ranges == []
    assert extractor.diagnostics


# ── integration: real clang (only when available) ───────────────────────


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_parse_real_clang_ast_decl_ranges_end_to_end(tmp_path) -> None:
    import json
    import subprocess

    header = tmp_path / "t.h"
    header.write_text(
        "#define FEATURE_X 1\n"
        "#ifdef FEATURE_X\n"
        "void guarded();\n"
        "#endif\n"
        "\n"
        "struct Widget {\n"
        "    int x;\n"
        "};\n"
    )
    proc = subprocess.run(
        [
            "clang",
            "-x",
            "c++",
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            str(header),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    ast = json.loads(proc.stdout)
    ranges = parse_clang_ast_decl_ranges(ast)
    # clang's C++ mode also emits an implicit injected-class-name CXXRecordDecl
    # alongside the real definition (same identity, a narrower range) — a real
    # production run dedups via ClangMacroGraphExtractor (first-seen wins, and
    # the real definition is always visited before the implicit one); this
    # pure-parser call intentionally doesn't dedup, so check "any" instead of
    # a single dict entry.
    # Matched by substring, not a literal Itanium-mangled prefix (Codex
    # review, fresh evidence: a Windows CI runner's real `clang` targets the
    # MSVC ABI by default, mangling `guarded` as `?guarded@@YAXXZ` rather
    # than Itanium's `_Z7guardedv` -- the earlier `startswith("_Z7guardedv")`
    # check made this test unconditionally fail on that platform even
    # though `parse_clang_ast_decl_ranges` itself worked correctly. Both
    # mangling schemes embed the plain identifier as a literal substring, so
    # this check is mangling-scheme-agnostic instead of Itanium-only.
    guarded = [r for r in ranges if "guarded" in r.identity]
    assert guarded and guarded[0].begin_line == 3
    widget = [r for r in ranges if r.identity == "Widget"]
    assert any(r.begin_line == 6 and r.end_line == 8 for r in widget)

    text = header.read_text()
    regions = scan_conditional_regions(text)
    assert ConditionalRegion("FEATURE_X", False, 3, 3) in regions
