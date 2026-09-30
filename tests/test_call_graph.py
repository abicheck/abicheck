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


def test_parse_strips_macos_mach_o_underscore_from_mangled_names() -> None:
    # On Darwin, clang's own -ast-dump=json reports a C++ decl's mangledName
    # with the Mach-O ABI's extra linker-symbol-table underscore still
    # attached ("__Zcaller" rather than "_Zcaller") -- the same decoration
    # macho_metadata.py already strips off the *binary's* export table, so a
    # header_graph-seeded decl:// node for the same function is keyed on the
    # one-underscore form. Left unstripped here, the call edge would land on
    # a different, never-public node and public_api_internal_dependency_added
    # would never fire for a function-rooted dependency on macOS.
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func(
                "caller",
                "__Zcaller",
                [_direct_call(_ref("FunctionDecl", "callee", "__Zcallee"))],
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


def test_parse_virtual_member_call_resolves_realistic_string_id() -> None:
    """Codex review, fresh evidence, verified against real Clang 17/18
    ``-ast-dump=json`` output for ``p->f()``: ``MemberExpr.
    referencedMemberDecl`` is a bare node-id **string**, not the nested
    compact-dict shape ``_member_call``'s hand-built fixture (and the test
    above) use. An earlier version of ``_find_referenced_decl`` only
    recognized the dict shape, silently fell through into ``MemberExpr``'s
    own children, and resolved the *receiver* parameter's ``DeclRefExpr``
    instead -- misclassifying every real virtual/member call as
    ``CALL_KIND_FUNCTION_POINTER`` through the receiver. This reproduces
    the real shape end to end: a ``CXXMethodDecl`` with an ``id``, a
    ``CXXMemberCallExpr`` whose ``MemberExpr`` names that same id as a bare
    string, and (mirroring the real receiver ``DeclRefExpr`` clang also
    emits, to prove it is NOT what gets resolved) a receiver reference to
    an unrelated ``ParmVarDecl``.
    """
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "CXXRecordDecl",
                "name": "Base",
                "inner": [
                    {
                        "kind": "CXXMethodDecl",
                        "id": "0x1",
                        "name": "f",
                        "mangledName": "_ZN4Base1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    }
                ],
            },
            _func(
                "call_it",
                "_Z7call_itP4Base",
                [
                    {
                        "kind": "CXXMemberCallExpr",
                        "inner": [
                            {
                                "kind": "MemberExpr",
                                "referencedMemberDecl": "0x1",
                                "inner": [
                                    {
                                        "kind": "ImplicitCastExpr",
                                        "inner": [
                                            {
                                                "kind": "DeclRefExpr",
                                                "referencedDecl": {
                                                    "kind": "ParmVarDecl",
                                                    "name": "p",
                                                },
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            ),
        ],
    }
    edges = parse_clang_ast_calls(ast)
    assert edges == [
        CallEdge(
            "_Z7call_itP4Base",
            "_ZN4Base1fEv",
            CALL_KIND_VIRTUAL,
            RESOLUTION_OVERAPPROX,
        )
    ]


def test_parse_member_call_unresolved_id_is_dropped_not_misattributed() -> None:
    """A ``referencedMemberDecl`` string id that was never indexed (a
    forward reference, or a genuinely non-function member -- a data field,
    never in ``_FUNCTION_DECL_KINDS``) must resolve to no edge at all,
    never fall through to the receiver's own reference (the exact bug this
    fix closes)."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            _func(
                "call_it",
                "_Zcall_it",
                [
                    {
                        "kind": "CXXMemberCallExpr",
                        "inner": [
                            {
                                "kind": "MemberExpr",
                                "referencedMemberDecl": "0xnotindexed",
                                "inner": [
                                    {
                                        "kind": "DeclRefExpr",
                                        "referencedDecl": {
                                            "kind": "ParmVarDecl",
                                            "name": "w",
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            ),
        ],
    }
    assert parse_clang_ast_calls(ast) == []


def test_parse_member_call_to_later_declared_sibling_still_resolves() -> None:
    """Codex review, fresh evidence, verified against real Clang 17
    ``-ast-dump=json`` output for ``struct A { virtual void f(){ g(); }
    virtual void g(); };``: clang visits ``f``'s body -- and its call to
    ``g`` -- before ``g``'s own ``CXXMethodDecl`` sibling in the pre-order
    dump. ``member_index`` was previously built incrementally during the
    single combined walk, so at the moment ``f -> g`` was resolved, ``g``
    was not yet indexed and the call was silently dropped rather than
    misattributed. This reproduces the exact ordering (``f`` first, ``g``
    declared after) end to end; the whole-AST pre-pass
    (``_index_member_decls``) must make ``g`` resolvable regardless of
    visit order.
    """
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "CXXRecordDecl",
                "name": "A",
                "inner": [
                    {
                        "kind": "CXXMethodDecl",
                        "id": "0x1",
                        "name": "f",
                        "mangledName": "_ZN1A1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                        "inner": [
                            {
                                "kind": "CompoundStmt",
                                "inner": [
                                    {
                                        "kind": "CXXMemberCallExpr",
                                        "inner": [
                                            {
                                                "kind": "MemberExpr",
                                                "referencedMemberDecl": "0x2",
                                                "inner": [
                                                    {
                                                        "kind": "CXXThisExpr",
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "kind": "CXXMethodDecl",
                        "id": "0x2",
                        "name": "g",
                        "mangledName": "_ZN1A1gEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                ],
            },
        ],
    }
    edges = parse_clang_ast_calls(ast)
    assert edges == [
        CallEdge(
            "_ZN1A1fEv",
            "_ZN1A1gEv",
            CALL_KIND_VIRTUAL,
            RESOLUTION_OVERAPPROX,
        )
    ]


def _qualified_call_ast(
    *, name_len: int = 1, member_begin: int = 67, member_end: int = 70
) -> dict:
    """A ``struct D : B { void f() override { B::f(); } };``-shaped AST:
    an explicitly-qualified call to a virtual base method through the
    implicit ``this`` receiver, mirroring the real Clang 18 offsets
    recorded in the module's own ``_member_expr_is_qualified`` docstring
    (``B::f()`` spans offsets 67-70, receiver is a zero-width implicit
    ``CXXThisExpr`` wrapped in an ``UncheckedDerivedToBase`` cast)."""
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "CXXRecordDecl",
                "name": "B",
                "inner": [
                    {
                        "kind": "CXXMethodDecl",
                        "id": "0xb",
                        "name": "f",
                        "mangledName": "_ZN1B1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    }
                ],
            },
            {
                "kind": "CXXRecordDecl",
                "name": "D",
                "bases": [{"type": {"qualType": "B"}}],
                "inner": [
                    {
                        "kind": "CXXMethodDecl",
                        "id": "0xd",
                        "name": "f",
                        "mangledName": "_ZN1D1fEv",
                        "type": {"qualType": "void ()"},
                        "inner": [
                            {
                                "kind": "CompoundStmt",
                                "inner": [
                                    {
                                        "kind": "CXXMemberCallExpr",
                                        "inner": [
                                            {
                                                "kind": "MemberExpr",
                                                "range": {
                                                    "begin": {"offset": member_begin},
                                                    "end": {
                                                        "offset": member_end,
                                                        "tokLen": 1,
                                                    },
                                                },
                                                "name": "f",
                                                "isArrow": True,
                                                "referencedMemberDecl": "0xb",
                                                "inner": [
                                                    {
                                                        "kind": "ImplicitCastExpr",
                                                        "castKind": "UncheckedDerivedToBase",
                                                        "range": {
                                                            "begin": {
                                                                "offset": member_end
                                                            },
                                                            "end": {
                                                                "offset": member_end,
                                                                "tokLen": 1,
                                                            },
                                                        },
                                                        "inner": [
                                                            {
                                                                "kind": "CXXThisExpr",
                                                                "implicit": True,
                                                            }
                                                        ],
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                ],
            },
        ],
    }


def test_parse_qualified_call_via_implicit_this_suppresses_virtual_dispatch() -> None:
    """Codex review, fresh evidence, verified against real Clang 18 output
    for ``struct D : B { void f() override { B::f(); } };`` -- the common
    "call the base implementation from an override" pattern. clang's JSON
    AST carries no ``qualifier``/``NestedNameSpecifier`` field the way its
    text ``-ast-dump`` does, so this is derived from the ``MemberExpr``'s
    own begin-to-end span (67-70, covering ``B::f``) exceeding its bare
    member-name length (``len("f") == 1``) -- an explicit ``B::`` qualifier
    suppresses virtual dispatch regardless of ``B::f`` being virtual, so
    this must classify as ``direct``/``exact``, not ``virtual``/``overapprox``.
    """
    edges = parse_clang_ast_calls(_qualified_call_ast())
    assert edges == [
        CallEdge("_ZN1D1fEv", "_ZN1B1fEv", CALL_KIND_DIRECT, RESOLUTION_EXACT)
    ]


def test_parse_unqualified_call_via_implicit_this_stays_virtual() -> None:
    """The same shape as the qualified case above but with no extra span
    (``member_begin == member_end``, matching a bare ``f()`` call where
    clang's own implicit-``this`` anchor coincides with the member name) --
    must still classify as ``virtual``/``overapprox``, confirming the
    qualified-call fix doesn't over-fire on the ordinary unqualified case.
    """
    edges = parse_clang_ast_calls(_qualified_call_ast(member_begin=70, member_end=70))
    assert edges == [
        CallEdge("_ZN1D1fEv", "_ZN1B1fEv", CALL_KIND_VIRTUAL, RESOLUTION_OVERAPPROX)
    ]


def test_parse_qualified_call_via_explicit_receiver_suppresses_virtual_dispatch() -> (
    None
):
    """Codex review, fresh evidence, verified against real Clang 18 output
    for ``obj.B::f()`` (an explicit, non-``this`` receiver) -- the
    receiver-to-member gap (5: ``.B::f``) exceeds the expected unqualified
    gap (2: ``.f``), so this must classify as ``direct``/``exact``."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "CXXRecordDecl",
                "name": "B",
                "inner": [
                    {
                        "kind": "CXXMethodDecl",
                        "id": "0xb",
                        "name": "f",
                        "mangledName": "_ZN1B1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    }
                ],
            },
            _func(
                "call_it",
                "_Z7call_itR1B",
                [
                    {
                        "kind": "CXXMemberCallExpr",
                        "inner": [
                            {
                                "kind": "MemberExpr",
                                "range": {
                                    "begin": {"offset": 82},
                                    "end": {"offset": 89, "tokLen": 1},
                                },
                                "name": "f",
                                "isArrow": False,
                                "referencedMemberDecl": "0xb",
                                "inner": [
                                    {
                                        "kind": "DeclRefExpr",
                                        "range": {
                                            "begin": {"offset": 82},
                                            "end": {"offset": 82, "tokLen": 3},
                                        },
                                        "referencedDecl": {
                                            "kind": "ParmVarDecl",
                                            "name": "obj",
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            ),
        ],
    }
    edges = parse_clang_ast_calls(ast)
    assert edges == [
        CallEdge("_Z7call_itR1B", "_ZN1B1fEv", CALL_KIND_DIRECT, RESOLUTION_EXACT)
    ]


def test_parse_call_with_whitespace_around_dot_is_a_known_false_positive() -> None:
    """Codex review, fresh evidence, second round, verified against real
    Clang 18 output for ``obj . f()`` (legal, if unusual, whitespace around
    the ``.``): the receiver-to-member gap arithmetic
    (``_member_expr_is_qualified``) cannot distinguish incidental whitespace
    from a real ``Base::`` qualifier without reading the source text between
    the two offsets, which this pure-AST-dict function does not have access
    to. This pins the CURRENT, documented, accepted behavior (misclassified
    as ``direct``/``exact`` instead of the semantically-correct
    ``virtual``/``overapprox``) so a future change to this heuristic doesn't
    silently alter it without updating this test -- see
    ``_member_expr_is_qualified``'s own docstring for why this isn't closed
    from this function's own inputs.
    """
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "CXXRecordDecl",
                "name": "B",
                "inner": [
                    {
                        "kind": "CXXMethodDecl",
                        "id": "0xb",
                        "name": "f",
                        "mangledName": "_ZN1B1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    }
                ],
            },
            _func(
                "call_it",
                "_Z7call_itR1B",
                [
                    {
                        "kind": "CXXMemberCallExpr",
                        "inner": [
                            {
                                "kind": "MemberExpr",
                                # Real Clang 18 offsets for "obj . f()": the
                                # receiver "obj" ends at 85, but the member
                                # name "f" starts at 88 (two stray spaces
                                # around the "."), inflating the gap to 4
                                # against an expected unqualified gap of 2.
                                "range": {
                                    "begin": {"offset": 82},
                                    "end": {"offset": 88, "tokLen": 1},
                                },
                                "name": "f",
                                "isArrow": False,
                                "referencedMemberDecl": "0xb",
                                "inner": [
                                    {
                                        "kind": "DeclRefExpr",
                                        "range": {
                                            "begin": {"offset": 82},
                                            "end": {"offset": 82, "tokLen": 3},
                                        },
                                        "referencedDecl": {
                                            "kind": "ParmVarDecl",
                                            "name": "obj",
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            ),
        ],
    }
    edges = parse_clang_ast_calls(ast)
    # Documented false positive: semantically this should be
    # virtual/overapprox (no real qualifier), but the heuristic can't tell
    # whitespace from "B::" without source text.
    assert edges == [
        CallEdge("_Z7call_itR1B", "_ZN1B1fEv", CALL_KIND_DIRECT, RESOLUTION_EXACT)
    ]


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


def test_parse_extern_c_caller_identity_is_its_linker_name() -> None:
    # Evidence-entity-model I1: clang's mangledName == name for C linkage *is*
    # the linker symbol -- the key the L2 header graph and the L4 fold
    # (SourceEntity.names["linker"]) share. qualified_name#signature_hash,
    # which no L2 producer can compute, left the edge on a second node.
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
    assert e.caller == "api"


def _namespaced_api(mangled: str | None) -> dict:
    fn: dict = {
        "kind": "FunctionDecl",
        "name": "api",
        "type": {"qualType": "void (void)"},
        "inner": [
            {
                "kind": "CompoundStmt",
                "inner": [_direct_call(_ref("FunctionDecl", "helper"))],
            }
        ],
    }
    if mangled is not None:
        fn["mangledName"] = mangled
    return {
        "kind": "TranslationUnitDecl",
        "inner": [{"kind": "NamespaceDecl", "name": "detail", "inner": [fn]}],
    }


def test_parse_namespaced_c_linkage_caller_keys_on_the_linker_name() -> None:
    # An extern "C" function declared inside a namespace still has the bare
    # linker symbol, so the namespace does not enter its identity (I1).
    e = parse_clang_ast_calls(_namespaced_api("api"))[0]
    assert e.caller == "api"


def test_parse_namespaced_unmangled_caller_fallback_is_qualified() -> None:
    # With no linker name at all, the source-qualified fallback is still
    # scope-qualified the same way type_graph.py's own scope walk is.
    import hashlib

    e = parse_clang_ast_calls(_namespaced_api(None))[0]
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
    g.extractor_passes["call_graph"] = True  # as the real fold stamps it (gap A5)
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


# ── the shared L5 AST pass degrades gracefully (l5_ast_pass) ────────────────


def _call_pass():
    from abicheck.buildsource.l5_ast_pass import L5_AST_PASSES

    return [p for p in L5_AST_PASSES if p.name == "call_graph"]


def _run_call_pass(build: BuildEvidence):
    """Run only the call-graph family of the shared L5 AST pass."""
    from abicheck.buildsource.l5_ast_pass import run_ast_passes

    return run_ast_passes(build, "clang++", passes=_call_pass())["call_graph"]


def _one_unit() -> BuildEvidence:
    return BuildEvidence(compile_units=[CompileUnit(id="cu://x", source="x.cpp")])


def test_extractor_missing_clang_returns_empty() -> None:
    from abicheck.buildsource.inline_graph_fold import fold_call_graph
    from abicheck.buildsource.l5_ast_pass import run_l5_ast_pass

    build = _one_unit()
    run = run_l5_ast_pass(build, "definitely-not-a-real-clang-xyz")
    assert run.clang_available is False
    assert run.outcomes == {}
    graph = SourceGraphSummary()
    rows: list = []
    fold_call_graph(graph, build, run, rows)
    assert not any(e.kind == "DECL_CALLS_DECL" for e in graph.edges)
    # a reason was recorded
    assert [r.status for r in rows] == ["failed"]
    assert "not found" in rows[0].detail


class _FakeProc:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_clang(monkeypatch, *, proc=None, raises=None) -> None:

    def fake_run(*_a, **_k):
        if raises is not None:
            raise raises
        return proc

    monkeypatch.setattr("abicheck.deadline.run_bounded", fake_run)


def test_run_ast_passes_parses_mocked_clang(monkeypatch) -> None:
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
    outcome = _run_call_pass(_one_unit())
    assert outcome.result == [
        CallEdge("_Zc", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)
    ]


def test_safe_clang_args_drop_plugin_flags_and_keep_parse_flags() -> None:
    # Formerly pinned on the deleted raw-argv builder; the surviving argv
    # builder replays only normalized fields, so a plugin-loading flag that
    # reached `abi_relevant_flags` must still be dropped.
    from abicheck.buildsource.call_graph import _safe_clang_args_from_compile_unit

    cu = CompileUnit(
        id="cu://v",
        source="victim.cpp",
        standard="c++20",
        defines={"FEATURE": "1"},
        include_paths=["include"],
        abi_relevant_flags=[
            "-Xclang",
            "-load",
            "-Xclang",
            "./evil.so",
            "-fplugin=./evil.so",
        ],
    )
    cmd = _safe_clang_args_from_compile_unit(cu)
    assert "-fplugin=./evil.so" not in cmd
    assert "-load" not in cmd
    assert "./evil.so" not in cmd
    assert "-I" in cmd and "include" in cmd
    assert "-DFEATURE=1" in cmd
    assert "-std=c++20" in cmd
    assert cmd[-2:] == ["--", "victim.cpp"]


def test_run_ast_passes_ignores_compile_unit_raw_argv(monkeypatch) -> None:
    import json as _json

    ast = {"kind": "TranslationUnitDecl", "inner": []}
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return _FakeProc(_json.dumps(ast))

    monkeypatch.setattr("abicheck.deadline.run_bounded", fake_run)
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

    _run_call_pass(build)

    cmd = captured["cmd"]
    assert "-fplugin=./evil.so" not in cmd
    assert "-x" in cmd and "c++" in cmd
    assert "-std=c++17" in cmd
    assert "-DFEATURE=1" in cmd
    assert "-I" in cmd and "include" in cmd
    assert "-fvisibility=hidden" in cmd
    assert cmd[-2:] == ["--", "victim.cpp"]


def test_run_ast_passes_unredacts_home_placeholder_in_source_and_cwd(
    monkeypatch,
) -> None:
    """A normalized ``BuildEvidenceCompileUnit`` always carries its home-dir
    prefix redacted to ``~`` (ADR-032 D7, ``adapters/compile_db.py``'s
    ``RedactionPolicy``) -- confirmed on real Windows CI: the pass's own test
    fixture puts its temp source under the runner's home directory, and
    replaying the still-redacted ``~\\...`` path straight into a real
    ``clang`` subprocess (no shell, so ``~`` never expands) made every TU fail
    uniformly, degrading call/type/template graph collection together while
    the sibling ``include_graph`` pass (which already un-redacts its own
    argv) succeeded. The source positional and ``cwd`` passed to the
    subprocess must always be the real, expanded path."""
    import json as _json
    import os

    # Assert against the real, current home (like test_unredact_home_expands_tilde
    # in test_source_extractors.py) rather than a monkeypatched fake one: HOME is
    # not consulted by os.path.expanduser on Windows (it reads USERPROFILE/
    # HOMEDRIVE+HOMEPATH instead), so faking HOME alone silently no-ops there.
    home = os.path.expanduser("~")
    ast = {"kind": "TranslationUnitDecl", "inner": []}
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return _FakeProc(_json.dumps(ast))

    monkeypatch.setattr("abicheck.deadline.run_bounded", fake_run)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(
                id="cu://x",
                source="~/AppData/Local/Temp/t.cpp",
                directory="~/AppData/Local/Temp",
                argv=["/usr/bin/g++", "victim.cpp"],
                language="CXX",
                standard="c++17",
                include_paths=["~/AppData/Local/Temp/include"],
            )
        ]
    )

    _run_call_pass(build)

    cmd = captured["cmd"]
    assert cmd[-2:] == ["--", f"{home}/AppData/Local/Temp/t.cpp"]
    assert captured["cwd"] == f"{home}/AppData/Local/Temp"


def test_run_ast_passes_empty_stdout(monkeypatch) -> None:
    _patch_clang(monkeypatch, proc=_FakeProc("", stderr="boom"))
    outcome = _run_call_pass(_one_unit())
    assert outcome.result == []
    assert any("no AST" in d for d in outcome.diagnostics)


def test_run_ast_passes_nonzero_exit_records_diagnostic_but_salvages_edges(
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
    outcome = _run_call_pass(_one_unit())
    assert outcome.result == [
        CallEdge("_Zc", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)
    ]
    assert any("exited 1" in d for d in outcome.diagnostics)


def test_run_ast_passes_zero_exit_records_no_diagnostic(monkeypatch) -> None:
    import json as _json

    ast = {"kind": "TranslationUnitDecl", "inner": []}
    _patch_clang(monkeypatch, proc=_FakeProc(_json.dumps(ast), returncode=0))
    outcome = _run_call_pass(_one_unit())
    assert outcome.result == []
    assert outcome.diagnostics == []


def test_run_ast_passes_bad_json(monkeypatch) -> None:
    _patch_clang(monkeypatch, proc=_FakeProc("{not json"))
    outcome = _run_call_pass(_one_unit())
    assert outcome.result == []
    assert any("could not parse" in d for d in outcome.diagnostics)


def test_run_ast_passes_subprocess_error(monkeypatch) -> None:
    _patch_clang(monkeypatch, raises=OSError("no exec"))
    outcome = _run_call_pass(_one_unit())
    assert outcome.result == []
    assert any("invocation failed" in d for d in outcome.diagnostics)


def test_run_ast_passes_dedupes_across_units(monkeypatch) -> None:
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
    outcome = _run_call_pass(build)
    assert outcome.result == [
        CallEdge("_Zc", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)
    ]


def test_merge_call_edges_dedupes_first_seen_by_caller_callee_kind() -> None:
    from abicheck.buildsource.call_graph import merge_call_edges

    a = CallEdge("_Zc", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)
    a_again = CallEdge("_Zc", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_OVERAPPROX)
    b = CallEdge("_Zc", "_Zcallee", CALL_KIND_VIRTUAL, RESOLUTION_OVERAPPROX)
    c = CallEdge("_Zd", "_Zcallee", CALL_KIND_DIRECT, RESOLUTION_EXACT)
    assert merge_call_edges([[a, b], [a_again, c], []]) == [a, b, c]
    assert merge_call_edges([]) == []


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


def _patch_dump(monkeypatch, fake) -> None:
    import abicheck.buildsource.l5_ast_pass as l5

    monkeypatch.setattr(l5, "run_clang_ast_dump", fake)


_EMPTY_TU = {"kind": "TranslationUnitDecl", "inner": []}


def test_run_ast_passes_parallelizes_and_dedupes(monkeypatch) -> None:
    import abicheck.buildsource.call_graph as cg
    import abicheck.buildsource.l5_ast_pass as l5

    # Force a real pool (l5_ast_pass imports _call_graph_jobs by name).
    monkeypatch.setattr(
        "abicheck.buildsource.l5_ast_pass._call_graph_jobs", lambda _n: 2
    )
    seen_sources: list[str] = []

    def fake_dump(_clang, argv, *, cwd, diagnostics):
        del cwd, diagnostics
        seen_sources.append(argv[-1])
        return _EMPTY_TU

    _patch_dump(monkeypatch, fake_dump)
    call_pass = l5.AstPass(
        "call_graph",
        lambda _ast, _cu: [CallEdge("caller", "callee")],
        cg.merge_call_edges,
    )
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp"),
            CompileUnit(id="cu://b", source="b.cpp"),
            CompileUnit(id="cu://c", source="c.cpp"),
        ]
    )

    outcome = l5.run_ast_passes(build, "clang++", passes=[call_pass])["call_graph"]

    assert outcome.last_jobs == 2
    assert outcome.last_elapsed_s >= 0.0
    assert sorted(seen_sources) == ["a.cpp", "b.cpp", "c.cpp"]
    assert outcome.result == [CallEdge("caller", "callee")]


def test_run_ast_passes_propagates_deadline_into_pool_workers(monkeypatch) -> None:
    """Codex review (PR #591): contextvars don't cross a ThreadPoolExecutor
    boundary, so a worker submitted from inside deadline.deadline_scope()
    used to see no active deadline at all — each clang subprocess call
    inside it would run to its full fixed 120s regardless of --budget."""
    from abicheck import deadline

    # Force a real pool (l5_ast_pass imports _call_graph_jobs by name).
    monkeypatch.setattr(
        "abicheck.buildsource.l5_ast_pass._call_graph_jobs", lambda _n: 2
    )
    seen_remaining: list[float | None] = []

    def fake_dump(_clang, _argv, *, cwd, diagnostics):
        del cwd, diagnostics
        seen_remaining.append(deadline.remaining())
        return None

    _patch_dump(monkeypatch, fake_dump)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp"),
            CompileUnit(id="cu://b", source="b.cpp"),
            CompileUnit(id="cu://c", source="c.cpp"),
        ]
    )
    with deadline.deadline_scope(30.0):
        _run_call_pass(build)
    assert len(seen_remaining) == 3
    assert all(r is not None for r in seen_remaining), (
        "pool worker saw no active deadline (remaining()=None) — the scan "
        "deadline did not cross the executor boundary"
    )
    assert all(0 < r <= 30.0 for r in seen_remaining)


def test_run_ast_passes_diagnostics_deterministic_under_real_thread_pool(
    monkeypatch,
) -> None:
    # Codex review: the parallel workers used to append straight to a shared
    # diagnostics list, recording entries in subprocess-completion order
    # rather than input order -- nondeterministic across runs of identical,
    # pinned inputs. Each unit's diagnostics are collected per call and
    # folded on the single driving thread, in pool.map's input order.
    import time

    # Force a real pool (l5_ast_pass imports _call_graph_jobs by name).
    monkeypatch.setattr(
        "abicheck.buildsource.l5_ast_pass._call_graph_jobs", lambda _n: 4
    )
    n = 8

    def fake_dump(_clang, argv, *, cwd, diagnostics):
        del cwd
        # Reverse-staggered sleep: the LAST-dispatched unit tends to finish
        # FIRST -- the opposite of input order.
        idx = int(argv[-1].removesuffix(".cpp"))
        time.sleep((n - idx) * 0.01)
        diagnostics.append(f"failed for cu {idx}")
        return None

    _patch_dump(monkeypatch, fake_dump)
    build = BuildEvidence(
        compile_units=[CompileUnit(id=f"cu://{i}", source=f"{i}.cpp") for i in range(n)]
    )

    first_diagnostics = set()
    for _ in range(5):
        outcome = _run_call_pass(build)
        assert outcome.last_jobs == 4
        first_diagnostics.add(tuple(outcome.diagnostics))

    assert len(first_diagnostics) == 1, (
        f"diagnostics order varied across identical runs: {first_diagnostics}"
    )
    assert next(iter(first_diagnostics)) == tuple(
        f"failed for cu {i}" for i in range(n)
    )


def test_run_ast_passes_deadline_exceeded_degrades_to_diagnostic(
    monkeypatch,
) -> None:
    # Codex review (PR #591): this pass is advisory (ADR-028 D3) — a
    # DeadlineExceeded from the now-bounded clang subprocess must degrade to
    # the same diagnostic+[] contract as any other probe failure, not
    # propagate and abort the whole L5 call-graph fold.
    from abicheck import deadline

    _patch_clang(monkeypatch, raises=deadline.DeadlineExceeded(-1.0))
    outcome = _run_call_pass(_one_unit())
    assert outcome.result == []
    assert any("clang invocation failed" in d for d in outcome.diagnostics)


def test_run_ast_passes_bounded_by_local_cap_not_full_scan_budget(
    monkeypatch,
) -> None:
    """Codex review (PR #591), round 8: deadline.run_bounded() honors an
    active outer deadline verbatim (not min(timeout, left)), so a bare
    timeout=120 on this L5 clang call alone did nothing once a scan
    --budget was active: the call stayed bound by the FULL remaining scan
    budget instead of this pass's own 120s local cap. Assert the ContextVar
    deadline observed inside run_bounded is capped near the local cap, not
    the much larger outer scan budget."""
    from abicheck import deadline

    seen_remaining: list[float | None] = []

    def fake_run_bounded(*_a, **_k):
        seen_remaining.append(deadline.remaining())
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr("abicheck.deadline.run_bounded", fake_run_bounded)
    with deadline.deadline_scope(1800.0):  # a generous 30-minute --budget
        _run_call_pass(_one_unit())

    assert seen_remaining
    assert seen_remaining[0] is not None and 0 < seen_remaining[0] <= 120.5


def test_run_ast_passes_rechecks_deadline_before_parsing_ast(monkeypatch) -> None:
    """Codex review (PR #591): the same post-subprocess gap as the L2/L4
    clang paths — clang can exit successfully right as the budget expires,
    but json.loads()+parse_clang_ast_calls() used to run unbounded. Must
    degrade to the advisory diagnostic+[] contract (ADR-028 D3), not raise."""
    import json as _json
    import time

    from abicheck import deadline

    def fake_run(*_a, **_k):
        # Simulate the budget running out while clang was still parsing: by
        # the time it exits successfully, the deadline has already passed.
        time.sleep(0.05)
        return _FakeProc(_json.dumps(_EMPTY_TU))

    monkeypatch.setattr("abicheck.deadline.run_bounded", fake_run)
    with deadline.deadline_scope(0.03):
        outcome = _run_call_pass(_one_unit())
    assert outcome.result == []
    assert any(
        "scan deadline exceeded before parsing clang AST" in d
        for d in outcome.diagnostics
    )


def test_run_ast_passes_rechecks_deadline_before_walking_ast(monkeypatch) -> None:
    """Codex review (PR #591, round 4): json.loads() on a huge L5 call-graph
    AST can itself consume the rest of the budget -- the existing pre-load
    deadline.check() doesn't catch that; must re-check again after the load,
    before the recursive parse_clang_ast_calls() walk."""
    import json as _json
    import time

    from abicheck import deadline
    from abicheck.buildsource import clang_ast_run

    def fake_run(*_a, **_k):
        return _FakeProc(_json.dumps(_EMPTY_TU))

    monkeypatch.setattr("abicheck.deadline.run_bounded", fake_run)
    real_loads = clang_ast_run.json.loads

    def _slow_loads(text: str) -> object:
        time.sleep(0.05)
        return real_loads(text)

    monkeypatch.setattr(clang_ast_run.json, "loads", _slow_loads)
    with deadline.deadline_scope(0.03):
        outcome = _run_call_pass(_one_unit())
    assert outcome.result == []
    assert any(
        "scan deadline exceeded before walking clang AST" in d
        for d in outcome.diagnostics
    )


# ── collect: call-graph folds automatically (inline_graph_fold.fold_call_graph) ──
#
# `collect`'s call/type/include-graph folding is the exact same
# `l5_ast_pass.fold_semantic_graphs` the inline `dump --sources` path uses
# — the pass-ran/degraded/empty-build/missing-clang scenarios for that shared
# function are exercised in `tests/test_inline_changed_paths.py`. Only the
# `collect`-specific end-to-end wiring is tested here: no `--call-graph`
# flag exists any more — `--source-abi` + `--source-graph summary` together
# fold call edges in automatically, mirroring `dump --sources`.


def _patch_l5_pass(monkeypatch, edges: list[CallEdge]) -> None:
    """Make the shared L5 AST pass "run" and yield *edges* for the call graph
    (every other family empty), without a real clang."""
    from tests._fake_l5_ast_pass import install_fake_l5

    install_fake_l5(monkeypatch, results={"call_graph": lambda _t: edges})


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
    _patch_l5_pass(monkeypatch, [CallEdge("_Za", "_Zb")])

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
    # abicheck.model.evidence_depth_levels.EvidenceDepth/USER_DEPTHS), so this
    # drives collect_inline_pack directly with layers=("L3", "L5") instead of
    # going through a CLI invocation that cannot express this state.
    from abicheck.buildsource.inline import collect_inline_pack

    tree = _write_call_graph_source_tree(tmp_path)
    _patch_l5_pass(monkeypatch, [CallEdge("_Za", "_Zb")])

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
