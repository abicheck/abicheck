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

"""G29 Phase 5 item 5 — full type-role coverage for the L5 type graph.

Split out of ``tests/test_type_graph.py``, which sits at its own line-count
cap. Covers the three roles that had no producer before this item
(``enum_underlying``/``template_param``/``default_template_arg``), the five
the item names that turned out to be **already** covered by an existing role
(pinned here so a later refactor can't silently drop them), the shapes
deliberately left unemitted because clang's JSON carries no dependency to
emit, and the ADR-046 D3 role-coverage matrix's agreement with what the
parser actually produces.

The hand-built AST trees mirror real ``clang -ast-dump=json`` output; the
``integration``-marked tests at the end re-derive the same shapes from a real
compiler, so the fixtures fail loudly if clang ever changes them.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from abicheck.buildsource.inline_graph_fold import ROLE_COVERAGE_MATRIX
from abicheck.buildsource.type_graph import (
    CONF_HIGH,
    EDGE_DECL_HAS_TYPE,
    EDGE_DECL_REFERENCES_DECL,
    EDGE_TYPE_HAS_FIELD_TYPE,
    EDGE_TYPE_INHERITS,
    parse_clang_ast_types,
)

# ── fixture builders (real clang AST shapes, verified by the integration
#    tests at the bottom of this file) ────────────────────────────────────


def _record(name: str) -> dict:
    return {
        "kind": "CXXRecordDecl",
        "name": name,
        "tagUsed": "struct",
        "inner": [{"kind": "FieldDecl", "name": "x", "type": {"qualType": "int"}}],
    }


def _namespace(name: str, *inner: dict) -> dict:
    return {"kind": "NamespaceDecl", "name": name, "inner": list(inner)}


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


def _detail_ns() -> dict:
    """``namespace detail { struct Impl { int x; }; using Handle = int; }``."""
    return _namespace(
        "detail",
        _record("Impl"),
        {"kind": "TypeAliasDecl", "name": "Handle", "type": {"qualType": "int"}},
    )


def _roles(ast: dict) -> set[str]:
    return {e.role for e in parse_clang_ast_types(ast)}


def _find(ast: dict, role: str) -> list[tuple[str, str, str, str]]:
    """``(kind, src, dst, confidence)`` for every edge carrying *role*."""
    return [
        (e.kind, e.src, e.dst, e.confidence)
        for e in parse_clang_ast_types(ast)
        if e.role == role
    ]


# ── enum_underlying ────────────────────────────────────────────────────────


def test_enum_fixed_underlying_type_emits_edge() -> None:
    """``enum class Color : detail::Handle`` depends on the private alias.

    An ``EnumDecl`` carries no ``type`` key at all, so the shared typedef
    path this kind used to take read an empty spelling and emitted nothing.
    """
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            {
                "kind": "EnumDecl",
                "name": "Color",
                "scopedEnumTag": "class",
                "fixedUnderlyingType": {
                    "qualType": "detail::Handle",
                    "desugaredQualType": "int",
                },
                "inner": [
                    {
                        "kind": "EnumConstantDecl",
                        "name": "Red",
                        "type": {"qualType": "api::Color"},
                    }
                ],
            },
        ),
    )
    assert _find(ast, "enum_underlying") == [
        (EDGE_TYPE_HAS_FIELD_TYPE, "api::Color", "detail::Handle", CONF_HIGH)
    ]


def test_enum_underlying_prefers_as_written_spelling_over_desugared() -> None:
    """The desugared form has already lost the identity the edge names."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            {
                "kind": "EnumDecl",
                "name": "Color",
                "fixedUnderlyingType": {
                    "qualType": "detail::Handle",
                    "desugaredQualType": "int",
                },
            },
        ),
    )
    dsts = {dst for _, _, dst, _ in _find(ast, "enum_underlying")}
    assert dsts == {"detail::Handle"}


def test_unscoped_enum_without_fixed_underlying_type_emits_nothing() -> None:
    """clang omits ``fixedUnderlyingType`` entirely for a plain ``enum E``."""
    ast = _tu(_detail_ns(), _namespace("api", {"kind": "EnumDecl", "name": "Plain"}))
    assert _find(ast, "enum_underlying") == []


def test_scoped_enum_with_implicit_int_underlying_type_is_excluded() -> None:
    """A scoped enum gets an implicit ``"int"`` — a fundamental type, so the
    existing exclusion filter drops it rather than minting a noise edge."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            {
                "kind": "EnumDecl",
                "name": "Plain",
                "scopedEnumTag": "class",
                "fixedUnderlyingType": {"qualType": "int"},
            },
        ),
    )
    assert _find(ast, "enum_underlying") == []


def test_typedef_underlying_edge_still_uses_the_alias_role() -> None:
    """The ``EnumDecl`` branch must not divert an ordinary typedef."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            {
                "kind": "TypeAliasDecl",
                "name": "Handle",
                "type": {"qualType": "detail::Impl *"},
            },
        ),
    )
    assert _find(ast, "alias") == [
        (EDGE_TYPE_HAS_FIELD_TYPE, "api::Handle", "detail::Impl", CONF_HIGH)
    ]


# ── template_param (non-type template parameter type) ──────────────────────


def _class_template(name: str, *params: dict) -> dict:
    return {
        "kind": "ClassTemplateDecl",
        "name": name,
        "inner": [*params, _record(name)],
    }


def _non_type_parm(name: str, qual_type: str, **extra: object) -> dict:
    return {
        "kind": "NonTypeTemplateParmDecl",
        "name": name,
        "type": {"qualType": qual_type},
        **extra,
    }


def _type_parm(name: str, **extra: object) -> dict:
    return {"kind": "TemplateTypeParmDecl", "name": name, "tagUsed": "class", **extra}


def test_non_type_template_parameter_type_emits_edge_on_the_record_node() -> None:
    """``template <detail::Handle H> struct Slot`` depends on the alias.

    The edge must land on ``api::Slot`` — the same ``record_type`` node the
    class's own field/base edges use — not on the wrapper template decl,
    which is not a node in this graph at all.
    """
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api", _class_template("Slot", _non_type_parm("H", "detail::Handle"))
        ),
    )
    assert _find(ast, "template_param") == [
        (EDGE_TYPE_HAS_FIELD_TYPE, "api::Slot", "detail::Handle", CONF_HIGH)
    ]


def test_type_template_parameter_alone_emits_no_template_param_edge() -> None:
    """A ``TemplateTypeParmDecl`` names no type of its own."""
    ast = _tu(_detail_ns(), _namespace("api", _class_template("Box", _type_parm("T"))))
    assert _find(ast, "template_param") == []


def _template_template_parm(name: str, *nested_params: dict, **extra: object) -> dict:
    return {
        "kind": "TemplateTemplateParmDecl",
        "name": name,
        "inner": list(nested_params),
        **extra,
    }


def test_non_type_parameter_nested_in_a_template_template_parameter_resolves() -> None:
    """``template <template <detail::Handle H> class C> struct Outer`` —
    clang nests ``H`` directly inside the ``TemplateTemplateParmDecl`` node,
    not the outer template's own children (Codex review, fresh evidence: the
    original single-level loop never descended into a
    ``TemplateTemplateParmDecl`` child's own ``inner``). The edge must still
    land on ``api::Outer`` — the same node the outer template's own field
    edges use — not on the wrapper ``C``, which is not a node in this graph.
    """
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            _class_template(
                "Outer",
                _template_template_parm("C", _non_type_parm("H", "detail::Handle")),
            ),
        ),
    )
    assert _find(ast, "template_param") == [
        (EDGE_TYPE_HAS_FIELD_TYPE, "api::Outer", "detail::Handle", CONF_HIGH)
    ]


def test_non_type_parameter_nested_two_levels_deep_still_resolves() -> None:
    """C++ permits an arbitrarily deep chain of template-template
    parameters; the recursive walk must not stop after one level."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            _class_template(
                "TwoDeep",
                _template_template_parm(
                    "Outer2",
                    _template_template_parm(
                        "Inner", _non_type_parm("D", "detail::Handle")
                    ),
                ),
            ),
        ),
    )
    assert _find(ast, "template_param") == [
        (EDGE_TYPE_HAS_FIELD_TYPE, "api::TwoDeep", "detail::Handle", CONF_HIGH)
    ]


def test_template_template_parameter_with_only_a_type_parameter_emits_nothing() -> None:
    """A nested ``TemplateTypeParmDecl`` (no non-type parameter anywhere in
    the chain) still names no type of its own — mirrors the top-level case."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            _class_template("TT", _template_template_parm("C", _type_parm("T"))),
        ),
    )
    assert _find(ast, "template_param") == []


def test_function_template_parameter_edge_shares_the_signature_node() -> None:
    """A function template's parameter edge is a ``DECL_HAS_TYPE`` landing on
    the identical identity the templated ``FunctionDecl``'s own signature
    edges use — a template pattern carries no ``mangledName``, so both sides
    must fall through the same qualified-name+signature identity."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            {
                "kind": "FunctionTemplateDecl",
                "name": "slot",
                "inner": [
                    _non_type_parm("H", "detail::Handle"),
                    {
                        "kind": "FunctionDecl",
                        "name": "slot",
                        "type": {"qualType": "void (detail::Impl)"},
                        "inner": [
                            {
                                "kind": "ParmVarDecl",
                                "name": "u",
                                "type": {"qualType": "detail::Impl"},
                            }
                        ],
                    },
                ],
            },
        ),
    )
    param_edges = _find(ast, "template_param")
    signature_edges = _find(ast, "param")
    assert [(k, d, c) for k, _, d, c in param_edges] == [
        (EDGE_DECL_HAS_TYPE, "detail::Handle", CONF_HIGH)
    ]
    assert signature_edges, "the templated FunctionDecl still emits its own edges"
    # The load-bearing property: one node, not two.
    assert {src for _, src, _, _ in param_edges} == {
        src for _, src, _, _ in signature_edges
    }


def test_variable_template_parameter_edge_shares_the_var_node() -> None:
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            {
                "kind": "VarTemplateDecl",
                "name": "gvar",
                "inner": [
                    _type_parm(
                        "T",
                        defaultArg={
                            "kind": "TemplateArgument",
                            "type": {"qualType": "detail::Impl"},
                        },
                    ),
                    {
                        "kind": "VarDecl",
                        "name": "gvar",
                        "type": {"qualType": "detail::Handle"},
                    },
                ],
            },
        ),
    )
    default_edges = _find(ast, "default_template_arg")
    var_edges = _find(ast, "var")
    assert [(k, d) for k, _, d, _ in default_edges] == [
        (EDGE_DECL_HAS_TYPE, "detail::Impl")
    ]
    assert (
        {src for _, src, _, _ in default_edges}
        == {src for _, src, _, _ in var_edges}
        == {"api::gvar"}
    )


def test_template_with_no_recognized_templated_child_emits_nothing() -> None:
    """No templated entity means no node to attribute the dependency to —
    degrade to no fact rather than guessing at a node id (ADR-028 D3)."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            {
                "kind": "ClassTemplateDecl",
                "name": "Orphan",
                "inner": [_non_type_parm("H", "detail::Handle")],
            },
        ),
    )
    assert _find(ast, "template_param") == []


# ── scope boundary: template_param is a parameter-type question, not an
#    argument-value question (Codex review, fresh evidence, second round) ──


def test_declaration_valued_specialization_argument_emits_no_template_param_edge() -> (
    None
):
    """``template_param`` answers "what type does the parameter require",
    never "what does a specific instantiation's argument reference".

    A declaration-valued non-type argument at a use site
    (``Holder<&detail::f>``) resolves — when it resolves at all — through a
    ``ClassTemplateSpecializationDecl``'s own ``TemplateArgument.decl``
    cross-reference, which is ``template_graph.py``'s reserved, unpopulated
    ``TEMPLATE_USES_DECL`` (G29 Phase 5 item 1), not this role.
    ``_walk_types`` deliberately never emits edges for a
    ``ClassTemplateSpecializationDecl`` node at all (misattribution to the
    shared generic template otherwise), so this AST shape — verified
    against real Clang 18 — produces nothing here, correctly.
    """
    ast = _tu(
        _detail_ns(),
        {"kind": "FunctionDecl", "name": "f", "type": {"qualType": "void ()"}},
        _namespace(
            "api",
            _class_template("Holder", _non_type_parm("H", "detail::Handle")),
            {
                "kind": "ClassTemplateSpecializationDecl",
                "name": "Holder",
                "inner": [
                    {
                        "kind": "TemplateArgument",
                        "decl": {
                            "kind": "FunctionDecl",
                            "name": "f",
                            "type": {"qualType": "void ()"},
                        },
                    }
                ],
            },
        ),
    )
    # The parameter's own type still resolves — that part of the role is
    # unaffected by this scope boundary.
    assert _find(ast, "template_param") == [
        (EDGE_TYPE_HAS_FIELD_TYPE, "api::Holder", "detail::Handle", CONF_HIGH)
    ]
    # The specialization's own argument-declaration reference (naming the
    # private free function `f`) is never emitted under any role — the
    # load-bearing assertion this test exists for.
    assert not any(e.dst in ("f", "detail::f") for e in parse_clang_ast_types(ast))


@pytest.mark.parametrize(
    "inner",
    [
        pytest.param(["not-a-dict", 42, None], id="non-dict-children"),
        pytest.param(
            [{"kind": "CXXRecordDecl"}, {"kind": "TypeAliasDecl"}],
            id="unnamed-children",
        ),
    ],
)
def test_malformed_template_children_emit_nothing(inner: list) -> None:
    """A hand-edited or truncated AST must degrade to no edge, not raise —
    the same defensive-parsing discipline the rest of this module uses."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            {
                "kind": "ClassTemplateDecl",
                "name": "Broken",
                "inner": [*inner, _non_type_parm("H", "detail::Handle")],
            },
        ),
    )
    assert _find(ast, "template_param") == []


def test_non_dict_template_parameter_child_is_skipped() -> None:
    """A recognized templated entity plus junk siblings: the junk is skipped
    and the real parameter still resolves."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            {
                "kind": "ClassTemplateDecl",
                "name": "Slot",
                "inner": [
                    "junk",
                    None,
                    _non_type_parm("H", "detail::Handle"),
                    _record("Slot"),
                ],
            },
        ),
    )
    assert _find(ast, "template_param") == [
        (EDGE_TYPE_HAS_FIELD_TYPE, "api::Slot", "detail::Handle", CONF_HIGH)
    ]


# ── default_template_arg ───────────────────────────────────────────────────


def test_default_template_argument_type_emits_edge() -> None:
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            _class_template(
                "Box",
                _type_parm(
                    "T",
                    defaultArg={
                        "kind": "TemplateArgument",
                        "type": {"qualType": "detail::Impl"},
                    },
                ),
            ),
        ),
    )
    assert _find(ast, "default_template_arg") == [
        (EDGE_TYPE_HAS_FIELD_TYPE, "api::Box", "detail::Impl", CONF_HIGH)
    ]


def test_alias_template_default_argument_and_target_share_one_node() -> None:
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            {
                "kind": "TypeAliasTemplateDecl",
                "name": "Al",
                "inner": [
                    _type_parm(
                        "T",
                        defaultArg={
                            "kind": "TemplateArgument",
                            "type": {"qualType": "detail::Impl"},
                        },
                    ),
                    {
                        "kind": "TypeAliasDecl",
                        "name": "Al",
                        "type": {"qualType": "detail::Impl *"},
                    },
                ],
            },
        ),
    )
    assert _find(ast, "default_template_arg") == [
        (EDGE_TYPE_HAS_FIELD_TYPE, "api::Al", "detail::Impl", CONF_HIGH)
    ]
    # The alias-template *target* role — covered incidentally by the existing
    # typedef path, pinned here so a refactor of the walk can't drop it.
    assert _find(ast, "alias") == [
        (EDGE_TYPE_HAS_FIELD_TYPE, "api::Al", "detail::Impl", CONF_HIGH)
    ]


def test_non_type_parameter_default_value_expression_emits_no_type_edge() -> None:
    """clang dumps a non-type default as ``{"isExpr": true}`` with no
    ``type`` — the referenced constant is a ``DeclRefExpr``, a
    ``DECL_REFERENCES_DECL`` question, not a type role."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            _class_template(
                "NT",
                _non_type_parm(
                    "H",
                    "detail::Handle",
                    defaultArg={"kind": "TemplateArgument", "isExpr": True},
                ),
            ),
        ),
    )
    assert _find(ast, "default_template_arg") == []
    # ...but the parameter's own type is still a real dependency.
    assert _find(ast, "template_param") == [
        (EDGE_TYPE_HAS_FIELD_TYPE, "api::NT", "detail::Handle", CONF_HIGH)
    ]


def test_template_template_parameter_default_emits_nothing() -> None:
    """A template-template default dumps as a bare ``{"kind":
    "TemplateArgument"}`` — neither a type nor a name to resolve."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            _class_template(
                "TT",
                {
                    "kind": "TemplateTemplateParmDecl",
                    "name": "C",
                    "defaultArg": {"kind": "TemplateArgument"},
                    "inner": [_type_parm("")],
                },
            ),
        ),
    )
    assert _find(ast, "default_template_arg") == []


# ── roles the item names that were already covered ─────────────────────────


@pytest.mark.parametrize(
    ("qual_type", "expected"),
    [
        # member-pointer to data
        ("int detail::Impl::*", "detail::Impl"),
        # member-pointer to function
        ("void (detail::Impl::*)(int)", "detail::Impl"),
        # function-pointer signature: a private parameter type
        ("void (*)(detail::Impl *)", "detail::Impl"),
    ],
)
def test_member_and_function_pointer_spellings_already_resolve(
    qual_type: str, expected: str
) -> None:
    """Two of the nine roles the item lists — member-pointer type and
    function-pointer signature — need no new producer: the existing nested
    type-name walk already reaches the owner/parameter types inside those
    spellings. Pinned so that stays true."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api", {"kind": "VarDecl", "name": "slot", "type": {"qualType": qual_type}}
        ),
    )
    assert (EDGE_DECL_HAS_TYPE, "api::slot", expected, CONF_HIGH) in _find(ast, "var")


# ── ADR-046 D3 coverage-matrix agreement ───────────────────────────────────


def _matrix_roles() -> set[str]:
    return {role for roles in ROLE_COVERAGE_MATRIX.values() for role in roles}


def test_role_coverage_matrix_matches_the_roles_the_parser_emits() -> None:
    """The ADR-046 D3 matrix claims per-role coverage on behalf of this pass,
    so a role the parser emits but the matrix omits is an unclaimed (and
    therefore invisible) capability, and a role the matrix claims but no code
    path emits is a false coverage claim. One AST exercising every role keeps
    both directions honest."""
    ast = _tu(
        _detail_ns(),
        _namespace(
            "api",
            # base + field
            {
                "kind": "CXXRecordDecl",
                "name": "Derived",
                "tagUsed": "struct",
                "bases": [{"type": {"qualType": "detail::Impl"}}],
                "inner": [
                    {
                        "kind": "FieldDecl",
                        "name": "f",
                        "type": {"qualType": "detail::Impl"},
                    }
                ],
            },
            # alias
            {
                "kind": "TypeAliasDecl",
                "name": "Handle",
                "type": {"qualType": "detail::Impl *"},
            },
            # enum_underlying
            {
                "kind": "EnumDecl",
                "name": "Color",
                "fixedUnderlyingType": {"qualType": "detail::Handle"},
            },
            # template_param + default_template_arg
            _class_template(
                "Slot",
                _non_type_parm("H", "detail::Handle"),
                _type_parm(
                    "T",
                    defaultArg={
                        "kind": "TemplateArgument",
                        "type": {"qualType": "detail::Impl"},
                    },
                ),
            ),
            # var
            {
                "kind": "VarDecl",
                "name": "g",
                "type": {"qualType": "detail::Impl *"},
            },
            # return + param + ref
            {
                "kind": "FunctionDecl",
                "name": "make",
                "mangledName": "_ZN3api4makeEPN6detail4ImplE",
                "type": {"qualType": "detail::Impl *(detail::Impl *)"},
                "inner": [
                    {
                        "kind": "ParmVarDecl",
                        "name": "p",
                        "type": {"qualType": "detail::Impl *"},
                    },
                    {
                        "kind": "CompoundStmt",
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
                    },
                ],
            },
        ),
        _namespace(
            "detail",
            {
                "kind": "VarDecl",
                "name": "k",
                "mangledName": "_ZN6detail1kE",
                "type": {"qualType": "int"},
            },
        ),
    )
    emitted = _roles(ast)
    assert emitted == _matrix_roles()


def test_every_matrix_edge_kind_is_a_kind_the_parser_emits() -> None:
    # Pins the matrix's key set against the parser's own EDGE_* constants
    # (not duplicated string literals) so a renamed edge kind fails this
    # test loudly instead of silently drifting the two apart. Whether the
    # parser actually *emits* each of these kinds — and every role under
    # it — is what the role-agreement test above and each role's own test
    # already assert; this test is deliberately just the key-set pin
    # (CodeRabbit review: an earlier version also asserted
    # ``parse_clang_ast_types(ast) is not None``, which can never fail
    # since the function always returns a list).
    assert set(ROLE_COVERAGE_MATRIX) == {
        EDGE_TYPE_INHERITS,
        EDGE_TYPE_HAS_FIELD_TYPE,
        EDGE_DECL_HAS_TYPE,
        EDGE_DECL_REFERENCES_DECL,
    }


# ── integration: the real clang AST shapes these fixtures encode ───────────


_ROLE_HEADER = """
namespace detail {
struct Impl { int x; };
using Handle = int;
constexpr Handle K = 3;
template <class> struct Def {};
void f();
}
namespace api {
enum class Color : detail::Handle { Red };
enum class Implicit { A };
enum Unscoped { B };
template <class T> using Ptr = detail::Impl *;
template <detail::Handle H> struct Slot { int v; };
template <class T = detail::Impl> struct Box { int v; };
template <detail::Handle H = detail::K> struct NT { int v; };
template <template <class> class C = detail::Def> struct TT { int v; };
template <detail::Handle H, class U = detail::Impl> void slot();
int detail::Impl::*pm;
void (detail::Impl::*pmf)(int);
template <template <detail::Handle H> class C> struct Outer { C<0> c; };
template <void (*Fn)()> struct Holder {};
using Public = Holder<&detail::f>;
}
"""


def _real_clang_ast(tmp_path) -> dict:
    header = tmp_path / "roles.hpp"
    header.write_text(_ROLE_HEADER)
    proc = subprocess.run(
        [
            "clang",
            "-x",
            "c++",
            "-std=c++17",
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            str(header),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_real_clang_emits_every_new_role_end_to_end(tmp_path) -> None:
    """The whole point of this item, against a real compiler rather than a
    fixture: a public template/enum/alias constrained by a private type
    produces a real dependency edge."""
    edges = {
        (e.role, e.src, e.dst) for e in parse_clang_ast_types(_real_clang_ast(tmp_path))
    }
    assert ("enum_underlying", "api::Color", "detail::Handle") in edges
    assert ("template_param", "api::Slot", "detail::Handle") in edges
    assert ("default_template_arg", "api::Box", "detail::Impl") in edges
    # already-covered roles the item also names
    assert ("alias", "api::Ptr", "detail::Impl") in edges
    assert any(role == "var" and dst == "detail::Impl" for role, _, dst in edges), (
        "member-pointer owner types resolve through the existing nested walk"
    )
    assert ("template_param", "api::Outer", "detail::Handle") in edges, (
        "a non-type parameter nested inside a template-template parameter "
        "still resolves onto the outer templated entity"
    )


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_real_clang_never_resolves_a_declaration_valued_specialization_argument(
    tmp_path,
) -> None:
    """Scope-boundary regression (Codex review, fresh evidence, second
    round): ``template_param`` must never be mistaken for resolving a
    specific instantiation's own argument. ``Holder<&detail::f>`` — a
    declaration-valued non-type argument — must produce no edge naming
    ``detail::f`` under any role; that is ``template_graph.py``'s reserved
    ``TEMPLATE_USES_DECL``, not this module's job. The parameter's own type
    (``void (*)()``, fundamental, contributes no edge either way) is not
    what this test is pinning — the absence of an argument-level edge is.
    """
    edges = parse_clang_ast_types(_real_clang_ast(tmp_path))
    assert not any(e.dst in ("f", "detail::f") for e in edges)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_real_clang_omits_the_shapes_this_item_deliberately_skips(tmp_path) -> None:
    """Pins the two deliberate non-emissions against the compiler itself, so
    a future clang that *does* carry the dependency shows up as a failing
    test rather than a silently persisting gap."""
    edges = {
        (e.role, e.src, e.dst) for e in parse_clang_ast_types(_real_clang_ast(tmp_path))
    }
    # non-type parameter default *value* (detail::K) — dumped as an expr
    assert not any(
        role == "default_template_arg" and src == "api::NT" for role, src, _ in edges
    )
    # template-template parameter default (detail::Def) — dumped bare
    assert not any(
        role == "default_template_arg" and src == "api::TT" for role, src, _ in edges
    )
    # an unscoped enum has no fixed underlying type at all, and a scoped one
    # with no written type gets an implicit `int` the exclusion filter drops
    assert not any(
        role == "enum_underlying" and src in {"api::Unscoped", "api::Implicit"}
        for role, src, _ in edges
    )


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_real_clang_function_template_shares_one_node_with_its_signature(
    tmp_path,
) -> None:
    parsed = parse_clang_ast_types(_real_clang_ast(tmp_path))
    template_param_srcs = {
        e.src for e in parsed if e.role == "template_param" and "slot" in e.src
    }
    default_srcs = {
        e.src for e in parsed if e.role == "default_template_arg" and "slot" in e.src
    }
    assert template_param_srcs
    assert template_param_srcs == default_srcs
