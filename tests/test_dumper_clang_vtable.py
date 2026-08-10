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

"""Unit tests for G31 Phase C's direct-clang vtable reconstruction.

Hand-built ``-ast-dump=json``-shaped node dicts, mirroring
``test_dumper_clang.py``'s own convention (the parser is pure, so the whole
emit surface is exercised without clang installed). Every shape here was
first verified against a REAL ``clang++ -Xclang -ast-dump=json`` run before
being reduced to a fixture -- see ``dumper_clang_vtable.py``'s own docstring
for the exact empirical findings this module's design rests on.
"""

from __future__ import annotations

import pytest

from abicheck.dumper_clang import _ClangAstParser
from abicheck.dumper_clang_vtable import (
    _normalize_param_type,
    _top_level_param_list_close,
)


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


def _record(name: str, *inner: dict, bases: list[dict] | None = None) -> dict:
    node = {
        "kind": "CXXRecordDecl",
        "name": name,
        "tagUsed": "struct",
        "loc": {"file": "include/foo.h", "line": 1},
        "completeDefinition": True,
        "inner": list(inner),
    }
    if bases:
        node["bases"] = bases
    return node


def _base(qualtype: str, *, is_virtual: bool = False) -> dict:
    return {"type": {"qualType": qualtype}, "access": "public", "isVirtual": is_virtual}


def _method(
    name: str,
    mangled: str,
    *,
    virtual: bool = False,
    override_attr: bool = False,
    params: list[str] | None = None,
    is_const: bool = False,
) -> dict:
    qual = f"void ({', '.join(params or [])})" + (" const" if is_const else "")
    inner = [{"kind": "ParmVarDecl", "type": {"qualType": p}} for p in (params or [])]
    if override_attr:
        inner.append({"kind": "OverrideAttr"})
    node: dict = {
        "kind": "CXXMethodDecl",
        "name": name,
        "mangledName": mangled,
        "type": {"qualType": qual},
        "inner": inner,
    }
    if virtual:
        node["virtual"] = True
    return node


def _dtor(mangled: str, *, virtual: bool = False, implicit: bool = False) -> dict:
    node: dict = {"kind": "CXXDestructorDecl", "mangledName": mangled}
    if virtual:
        node["virtual"] = True
    if implicit:
        node["isImplicit"] = True
    return node


def _types(root: dict) -> dict[str, object]:
    return {t.name: t for t in _ClangAstParser(root, set(), set()).parse_types()}


# ── primary vtable, no inheritance ────────────────────────────────────────


def test_own_virtual_method_populates_vtable_and_offset() -> None:
    root = _tu(_record("A", _method("a", "_ZN1A1aEv", virtual=True)))
    types = _types(root)
    assert types["A"].vtable == ["_ZN1A1aEv"]
    assert types["A"].vptr_offset_bits == 0


def test_non_polymorphic_record_has_empty_vtable_and_none_offset() -> None:
    root = _tu(
        _record(
            "Plain", {"kind": "FieldDecl", "name": "x", "type": {"qualType": "int"}}
        )
    )
    types = _types(root)
    assert types["Plain"].vtable == []
    assert types["Plain"].vptr_offset_bits is None


# ── the core new capability: override with no virtual/override keyword ────


def test_override_with_no_keyword_still_recognized_and_replaces_slot() -> None:
    """The gap this module exists to close: clang's JSON AST gives NO signal
    at all for a re-declaration that overrides a base's virtual method
    without repeating `virtual` and without writing `override` either
    (confirmed empirically against real clang++ -- see module docstring).
    Must be recognized via pure signature matching, and must REPLACE the
    base's slot in place, not append a duplicate."""
    root = _tu(
        _record("A", _method("a", "_ZN1A1aEv", virtual=True)),
        _record(
            "F",
            _method("a", "_ZN1F1aEv"),  # no virtual, no OverrideAttr
            bases=[_base("A")],
        ),
    )
    types = _types(root)
    assert types["F"].vtable == ["_ZN1F1aEv"]  # replaced, not appended
    assert types["F"].vptr_offset_bits == 0


def test_override_attr_keyword_recognized() -> None:
    root = _tu(
        _record("A", _method("a", "_ZN1A1aEv", virtual=True)),
        _record("F", _method("a", "_ZN1F1aEv", override_attr=True), bases=[_base("A")]),
    )
    types = _types(root)
    assert types["F"].vtable == ["_ZN1F1aEv"]


def test_different_const_qualifier_is_not_an_override() -> None:
    """A same-named method that differs only in const-qualifier hides the
    base's method rather than overriding it -- must NOT replace the base's
    slot, and must NOT itself be added (it isn't virtual)."""
    root = _tu(
        _record("A", _method("a", "_ZN1A1aEv", virtual=True, is_const=True)),
        _record(
            "G",
            _method("a", "_ZN1G1aEv", is_const=False),  # different signature
            bases=[_base("A")],
        ),
    )
    types = _types(root)
    assert types["G"].vtable == ["_ZN1A1aEv"]  # unchanged, inherited as-is


def test_covariant_return_type_still_matches_by_params() -> None:
    """Return type is deliberately excluded from the signature key -- a
    covariant return is the SAME slot, just a different spelling."""
    root = _tu(
        _record(
            "A",
            {
                "kind": "CXXMethodDecl",
                "name": "clone",
                "mangledName": "_ZN1A5cloneEv",
                "type": {"qualType": "A *()"},
                "virtual": True,
            },
        ),
        _record(
            "D",
            {
                "kind": "CXXMethodDecl",
                "name": "clone",
                "mangledName": "_ZN1D5cloneEv",
                "type": {"qualType": "D *()"},  # covariant return, no keyword
                "inner": [{"kind": "OverrideAttr"}],
            },
            bases=[_base("A")],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1D5cloneEv"]


# ── destructors: implicit virtuality, name never participates ─────────────


def test_user_declared_destructor_inherits_virtuality_with_no_keyword() -> None:
    root = _tu(
        _record("A", _dtor("_ZN1AD1Ev", virtual=True)),
        _record("D", _dtor("_ZN1DD1Ev"), bases=[_base("A")]),  # no `virtual`
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1DD1Ev"]


def test_compiler_implicit_destructor_inherits_virtuality() -> None:
    """A class that declares no destructor of its own still gets one
    (isImplicit=True) from clang -- it must inherit virtuality from a base
    with a virtual destructor exactly like a user-declared one does."""
    root = _tu(
        _record("A", _dtor("_ZN1AD1Ev", virtual=True)),
        _record("E", _dtor("_ZN1ED1Ev", implicit=True), bases=[_base("A")]),
    )
    types = _types(root)
    assert types["E"].vtable == ["_ZN1ED1Ev"]


def test_destructor_without_polymorphic_base_stays_non_virtual() -> None:
    root = _tu(_record("Plain", _dtor("_ZN5PlainD1Ev", implicit=True)))
    types = _types(root)
    assert types["Plain"].vtable == []
    assert types["Plain"].vptr_offset_bits is None


# ── multiple inheritance ordering ──────────────────────────────────────────


def test_multiple_inheritance_orders_bases_then_own() -> None:
    root = _tu(
        _record(
            "A",
            _method("a", "_ZN1A1aEv", virtual=True),
            _method("a2", "_ZN1A2a2Ev", virtual=True),
        ),
        _record("B", _method("b", "_ZN1B1bEv", virtual=True)),
        _record(
            "C",
            _method("c", "_ZN1C1cEv", virtual=True),
            _method("a", "_ZN1C1aEv", override_attr=True),
            bases=[_base("A"), _base("B")],
        ),
    )
    types = _types(root)
    # C::a replaces A::a's slot in place (position 0); A::a2 and B::b are
    # inherited unchanged; C::c is genuinely new and appends at the end.
    assert types["C"].vtable == [
        "_ZN1C1aEv",
        "_ZN1A2a2Ev",
        "_ZN1B1bEv",
        "_ZN1C1cEv",
    ]


# ── namespaced/nested base resolution ──────────────────────────────────────


def test_namespaced_base_resolves_by_qualified_name() -> None:
    root = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "inner": [_record("A", _method("a", "_ZN2ns1A1aEv", virtual=True))],
        },
        _record("C", _method("a", "_ZN1C1aEv"), bases=[_base("ns::A")]),
    )
    types = _types(root)
    assert types["C"].vtable == ["_ZN1C1aEv"]


def test_unresolvable_base_degrades_without_crashing() -> None:
    """A base whose own record isn't in this TU (e.g. excluded by dependency
    scoping) must not crash the walk -- it's simply invisible, same as
    castxml's own degradation on an unresolvable Base XML element."""
    root = _tu(_record("D", _method("a", "_ZN1D1aEv"), bases=[_base("Unseen")]))
    types = _types(root)
    # No virtual/override marker and no visible base slot to match against
    # -- correctly NOT recognized as virtual.
    assert types["D"].vtable == []
    assert types["D"].vptr_offset_bits is None


# ── second review round: forward-decl records, base-name resolution, ──────
# ── cross-base signature collisions, remaining qualifiers, conversion ─────
# ── operators, and top-level param-const normalization ────────────────────


def test_forward_declaration_does_not_shadow_the_complete_definition() -> None:
    """`struct A; struct A { virtual void f(); };` -- clang emits BOTH
    CXXRecordDecl nodes for this real, common shape (confirmed against
    real clang). The record index must prefer the complete definition
    regardless of which one was walked first, or the forward decl's empty
    node wins and every virtual method is silently lost."""
    forward_decl = {
        "kind": "CXXRecordDecl",
        "name": "A",
        "tagUsed": "struct",
        "loc": {"file": "include/foo.h", "line": 1},
        # no completeDefinition, no inner -- pure forward declaration
    }
    root = _tu(
        forward_decl,
        _record("A", _method("f", "_ZN1A1fEv", virtual=True)),
    )
    types = _types(root)
    assert types["A"].vtable == ["_ZN1A1fEv"]


def test_base_resolved_via_desugared_qualtype_same_namespace() -> None:
    """The ordinary unqualified spelling for a base declared in the SAME
    namespace as the derived class (`struct C : A {}` where A is `ns::A`)
    reports the bare, non-canonical `qualType: "A"` with the fully
    qualified form only in `desugaredQualType` -- confirmed against real
    clang. Reading `qualType` alone can never resolve it against the
    qualname-keyed record index."""
    root = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "inner": [
                _record("A", _method("a", "_ZN2ns1A1aEv", virtual=True)),
                _record(
                    "C",
                    _method("a", "_ZN2ns1C1aEv"),
                    bases=[
                        {
                            "type": {"qualType": "A", "desugaredQualType": "ns::A"},
                            "access": "public",
                            "isVirtual": False,
                        }
                    ],
                ),
            ],
        }
    )
    types = _types(root)
    assert types["C"].vtable == ["_ZN2ns1C1aEv"]


def test_base_resolved_via_desugared_qualtype_type_alias() -> None:
    """A type-alias base (`using AliasA = ns::A; struct D : AliasA {};`)
    spells `qualType` as the alias name, with `desugaredQualType` again
    carrying the real target -- confirmed against real clang."""
    root = _tu(
        _record("A", _method("a", "_ZN1A1aEv", virtual=True)),
        _record(
            "D",
            _method("a", "_ZN1D1aEv"),
            bases=[
                {
                    "type": {"qualType": "AliasA", "desugaredQualType": "A"},
                    "access": "public",
                    "isVirtual": False,
                }
            ],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1D1aEv"]


def test_two_unrelated_bases_sharing_a_signature_stay_two_slots() -> None:
    """`struct D : B1, B2` where B1 and B2 independently declare an
    identically-signed `virtual void q();` with NO inheritance relationship
    between them -- confirmed against real clang that these are two
    genuinely separate vtable-group slots. A signature-keyed dict without
    per-physical-slot identity collapses them onto one, silently discarding
    one of the two real slots."""
    root = _tu(
        _record("B1", _method("q", "_ZN2B11qEv", virtual=True)),
        _record("B2", _method("q", "_ZN2B21qEv", virtual=True)),
        _record("D", bases=[_base("B1"), _base("B2")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN2B11qEv", "_ZN2B21qEv"]


def test_override_of_cross_base_shared_signature_replaces_both_slots() -> None:
    """Per [class.virtual], D's own `void q() override;` becomes the final
    overrider for BOTH B1::q and B2::q at once (they share a signature) --
    confirmed against real clang this compiles. Both physical slots must
    end up occupied by D's own q, not just one."""
    root = _tu(
        _record("B1", _method("q", "_ZN2B11qEv", virtual=True)),
        _record("B2", _method("q", "_ZN2B21qEv", virtual=True)),
        _record(
            "D",
            _method("q", "_ZN1D1qEv", override_attr=True),
            bases=[_base("B1"), _base("B2")],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1D1qEv", "_ZN1D1qEv"]


def test_ref_qualifier_mismatch_is_not_an_override() -> None:
    """`virtual void f() &;` vs. an unqualified `void f();` in a derived
    class are DIFFERENT signatures -- confirmed against real clang both
    compile with distinct manglings. A signature key reduced to a plain
    `is_const` boolean (an earlier version of this module) would have
    incorrectly matched them."""
    root = _tu(
        _record(
            "A",
            {
                "kind": "CXXMethodDecl",
                "name": "f",
                "mangledName": "_ZNR1A1fEv",
                "type": {"qualType": "void () &"},
                "virtual": True,
            },
        ),
        _record("D", _method("f", "_ZN1D1fEv"), bases=[_base("A")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZNR1A1fEv"]  # unchanged, inherited as-is


def test_conversion_operator_is_included_in_vtable() -> None:
    """A virtual conversion operator (`operator int() const`) is a separate
    clang node kind, CXXConversionDecl, not CXXMethodDecl -- confirmed
    against real clang. Must still enter the vtable and be overridable the
    same way an ordinary virtual method is."""
    root = _tu(
        _record(
            "A",
            {
                "kind": "CXXConversionDecl",
                "name": "operator int",
                "mangledName": "_ZNK1AcviEv",
                "type": {"qualType": "int () const"},
                "virtual": True,
            },
        ),
        _record(
            "D",
            {
                "kind": "CXXConversionDecl",
                "name": "operator int",
                "mangledName": "_ZNK1DcviEv",
                "type": {"qualType": "int () const"},
            },
            bases=[_base("A")],
        ),
    )
    types = _types(root)
    assert types["A"].vptr_offset_bits == 0
    assert types["D"].vtable == ["_ZNK1DcviEv"]


def test_top_level_param_const_is_normalized_for_override_matching() -> None:
    """`virtual void f(const int x);` vs. a derived `void f(int x);` are
    the SAME signature -- top-level cv on a by-value parameter doesn't
    participate in override identity (confirmed against real clang: both
    mangle to an identical parameter-encoding tail). Must be recognized as
    an override, not treated as an unrelated new method."""
    root = _tu(
        _record(
            "A",
            _method("f", "_ZN1A1fEi", virtual=True, params=["const int"]),
        ),
        _record("D", _method("f", "_ZN1D1fEi", params=["int"]), bases=[_base("A")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1D1fEi"]  # replaced, not appended


def test_pointee_level_const_is_not_normalized_away() -> None:
    """`virtual void b(const int* p);` vs. a derived `void b(int* p);` are
    genuinely DIFFERENT signatures -- the const applies to the pointee, not
    the pointer itself, and DOES survive Itanium mangling (confirmed against
    real clang: `...bEPKi` keeps the K). Must NOT be treated as an override."""
    root = _tu(
        _record(
            "A",
            _method("b", "_ZN1A1bEPKi", virtual=True, params=["const int *"]),
        ),
        _record("D", _method("b", "_ZN1D1bEPi", params=["int *"]), bases=[_base("A")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1A1bEPKi"]  # unchanged, inherited as-is


# ── third review round: variadics, param-type desugaring, and the ─────────
# ── inferred-virtuality leak into Function.is_virtual ──────────────────────


def test_variadic_base_slot_is_not_replaced_by_unrelated_fixed_arity_method() -> None:
    """`virtual void g(int, ...);` and a derived, genuinely unrelated
    `void g(int);` report the IDENTICAL single ParmVarDecl list -- the `...`
    is only visible in the outer function qualType and in the two methods'
    distinct manglings (confirmed against real clang: `...gEiz` vs
    `...gEi`). Must NOT be treated as an override."""
    root = _tu(
        _record(
            "A",
            {
                "kind": "CXXMethodDecl",
                "name": "g",
                "mangledName": "_ZN1A1gEiz",
                "type": {"qualType": "void (int, ...)"},
                "virtual": True,
                "inner": [{"kind": "ParmVarDecl", "type": {"qualType": "int"}}],
            },
        ),
        _record("D", _method("g", "_ZN1D1gEi", params=["int"]), bases=[_base("A")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1A1gEiz"]  # unchanged, inherited as-is


def test_typedef_param_resolved_via_desugared_qualtype() -> None:
    """`using I = int; virtual void f(I x);` vs. a derived `void f(int x)
    override;` mangle to an IDENTICAL parameter encoding (typedefs are
    transparent to Itanium mangling, confirmed against real clang), but the
    base's own qualType spells the parameter as the alias name `"I"` with
    the resolved `"int"` only in a separate `desugaredQualType` field."""
    root = _tu(
        _record(
            "A",
            {
                "kind": "CXXMethodDecl",
                "name": "f",
                "mangledName": "_ZN1A1fEi",
                "type": {"qualType": "void (I)"},
                "virtual": True,
                "inner": [
                    {
                        "kind": "ParmVarDecl",
                        "type": {"qualType": "I", "desugaredQualType": "int"},
                    }
                ],
            },
        ),
        _record(
            "D",
            _method("f", "_ZN1D1fEi", override_attr=True, params=["int"]),
            bases=[_base("A")],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1D1fEi"]  # replaced, not appended


def test_inferred_virtuality_propagates_to_function_is_virtual() -> None:
    """The gap this fix closes: dumper_clang_vtable correctly recognizes a
    no-keyword override and replaces the inherited slot, but
    parse_functions()'s own Function.is_virtual used to read only clang's
    raw `node.get("virtual")` -- missing exactly this case. Without the fix,
    diff_cxx_rules.vtable_slot_is_override_reuse() (which requires both
    sides' Function.is_virtual) would reject the reuse and
    diff_types._diff_type_vtable would emit a spurious TYPE_VTABLE_CHANGED
    for a benign in-place slot rename (confirmed end-to-end through the live
    dump()/compare() pipeline before this fix)."""
    root = _tu(
        _record("A", _method("a", "_ZN1A1aEv", virtual=True)),
        _record(
            "F",
            _method("a", "_ZN1F1aEv"),  # no virtual, no OverrideAttr
            bases=[_base("A")],
        ),
    )
    parser = _ClangAstParser(root, set(), set())
    funcs = {f.mangled: f for f in parser.parse_functions()}
    assert funcs["_ZN1F1aEv"].is_virtual is True


def test_explicit_virtual_keyword_still_recognized_without_vtable_lookup() -> None:
    """Baseline: a method with the literal `virtual` keyword must still be
    recognized correctly (the OR in the fixed expression must not somehow
    suppress the pre-existing signal)."""
    root = _tu(_record("A", _method("a", "_ZN1A1aEv", virtual=True)))
    parser = _ClangAstParser(root, set(), set())
    funcs = {f.mangled: f for f in parser.parse_functions()}
    assert funcs["_ZN1A1aEv"].is_virtual is True


def test_non_virtual_method_stays_non_virtual() -> None:
    """A method that matches nothing in any vtable must stay non-virtual --
    the fix only ever widens False -> True, never fabricates a False -> True
    flip for an unrelated ordinary method."""
    root = _tu(_record("A", _method("plain", "_ZN1A5plainEv")))
    parser = _ClangAstParser(root, set(), set())
    funcs = {f.mangled: f for f in parser.parse_functions()}
    assert funcs["_ZN1A5plainEv"].is_virtual is False


# ── fourth review round: __restrict, and a deeper pre-existing bug it ─────
# ── exposed in the pointer-itself-qualifier normalization ─────────────────


@pytest.mark.parametrize(
    "qualtype,expected",
    [
        # Real clang spelling has NO space between "*" and the FIRST
        # trailing qualifier word -- confirmed against real clang builds.
        # An earlier version of _normalize_param_type assumed a leading
        # space on every trailing qualifier and so never matched ANY of
        # these single-qualifier pointer cases at all (Codex review, fresh
        # evidence -- caught while investigating the __restrict finding
        # specifically, but the bug predates and is broader than that one
        # case: plain "int *const"/"int *volatile" were silently never
        # normalized either, despite the module's own docstring claiming
        # they were).
        ("int *const", "int *"),
        ("int *volatile", "int *"),
        ("int *__restrict", "int *"),
        # A second stacked qualifier word IS space-separated from the
        # first in real clang's spelling -- confirmed against real clang.
        ("int *const volatile", "int *"),
        ("int *const __restrict", "int *"),
        # Pointee-level qualifiers must survive untouched.
        ("const int *", "const int *"),
        ("const int *__restrict", "const int *"),
        ("const int *const", "const int *"),
        ("const int &", "const int &"),
        # Nested pointer-to-const-pointer: the inner "const" is one level
        # in, not top-level on the outer pointer -- must survive (mangles
        # to PKPi, confirmed against real clang in an earlier round).
        ("int *const *", "int * const *"),
        # Plain by-value leading qualifiers, unrelated to the pointer cases
        # above but sharing the same function.
        ("const int", "int"),
        ("const volatile int", "int"),
        ("int", "int"),
        ("int *", "int *"),
    ],
)
def test_normalize_param_type_matches_real_clang_spellings(
    qualtype: str, expected: str
) -> None:
    assert _normalize_param_type(qualtype) == expected


def test_pointer_itself_const_override_recognized_with_no_restrict() -> None:
    """The specific case the pre-existing bug above silently broke:
    `virtual void a(int* const p);` vs. a derived `void a(int* p) override;`
    -- no __restrict involved at all, just a plain top-level pointer-const.
    Confirmed against real clang this must be recognized as an override."""
    root = _tu(
        _record(
            "A",
            {
                "kind": "CXXMethodDecl",
                "name": "a",
                "mangledName": "_ZN1A1aEPi",
                "type": {"qualType": "void (int *const)"},
                "virtual": True,
                "inner": [{"kind": "ParmVarDecl", "type": {"qualType": "int *const"}}],
            },
        ),
        _record(
            "D",
            _method("a", "_ZN1D1aEPi", override_attr=True, params=["int *"]),
            bases=[_base("A")],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1D1aEPi"]  # replaced, not appended


def test_restrict_qualified_base_param_override_recognized() -> None:
    """`virtual void a(int* __restrict p);` vs. a derived
    `void a(int* p) override;` -- confirmed against real clang both mangle
    identically (`__restrict` is a hint, not part of the type)."""
    root = _tu(
        _record(
            "A",
            {
                "kind": "CXXMethodDecl",
                "name": "a",
                "mangledName": "_ZN1A1aEPi",
                "type": {"qualType": "void (int *__restrict)"},
                "virtual": True,
                "inner": [
                    {"kind": "ParmVarDecl", "type": {"qualType": "int *__restrict"}}
                ],
            },
        ),
        _record(
            "D",
            _method("a", "_ZN1D1aEPi", override_attr=True, params=["int *"]),
            bases=[_base("A")],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1D1aEPi"]  # replaced, not appended


def test_pointee_restrict_qualified_param_is_not_an_override() -> None:
    """`virtual void b(const int* __restrict p);` vs. a derived
    `void b(int* p) override;` are genuinely DIFFERENT signatures -- the
    const applies to the pointee, not the pointer itself. Must NOT be
    treated as an override (the derived method still IS virtual, via
    `override_attr`, so it appends as a genuinely new slot)."""
    root = _tu(
        _record(
            "A",
            {
                "kind": "CXXMethodDecl",
                "name": "b",
                "mangledName": "_ZN1A1bEPKi",
                "type": {"qualType": "void (const int *__restrict)"},
                "virtual": True,
                "inner": [
                    {
                        "kind": "ParmVarDecl",
                        "type": {"qualType": "const int *__restrict"},
                    }
                ],
            },
        ),
        _record(
            "D",
            _method("b", "_ZN1D1bEPi", override_attr=True, params=["int *"]),
            bases=[_base("A")],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1A1bEPKi", "_ZN1D1bEPi"]  # both, distinct slots


# ── fifth review round: the parameter-list close paren vs. an exception ───
# ── specification's own trailing `()` ──────────────────────────────────


@pytest.mark.parametrize(
    ("qual_type", "expected_char"),
    [
        # Real clang spelling (Clang 18, -std=c++14) for a ref-qualified
        # method that ALSO carries a dynamic exception spec: the spec's own
        # `()` sits textually LAST, so a naive `rfind(")")` finds ITS close
        # paren instead of the parameter list's own.
        ("void () & throw()", ")"),
        ("void () && throw()", ")"),
        ("void (int, ...)", ")"),
        ("void (void (*)(int)) const", ")"),
        ("void ()", ")"),
    ],
)
def test_top_level_param_list_close_skips_exception_spec_parens(
    qual_type: str, expected_char: str
) -> None:
    idx = _top_level_param_list_close(qual_type)
    assert idx != -1
    assert qual_type[idx] == expected_char
    # The parameter list's own close paren is always the FIRST top-level
    # `)` reachable by walking forward from the FIRST top-level `(` -- for
    # every case above (with a trailing exception spec or not) this must
    # NOT be the string's last character index unless there is genuinely
    # nothing after it (the bare `"void ()"` case).
    if "throw" in qual_type or qual_type.endswith("..."):
        assert idx != len(qual_type) - 1


def test_ref_qualifier_mismatch_survives_trailing_exception_spec() -> None:
    """Codex review, fresh evidence: a base `virtual void g() & throw();`
    and an unrelated derived `void g() && throw();` used to reduce to an
    IDENTICAL empty qualifier tail (the naive `rfind(")")` search matched
    `throw()`'s own close paren, discarding both ref-qualifiers), so the
    derived declaration was misclassified as an override that REPLACES the
    base's slot in place. Must instead be recognized as a genuinely
    DIFFERENT signature -- the base's slot is untouched, and since the
    derived method carries no `virtual`/`override` marker and doesn't match
    any known signature, it is not virtual and does not appear in the
    vtable at all.
    """
    root = _tu(
        _record(
            "A",
            {
                "kind": "CXXMethodDecl",
                "name": "g",
                "mangledName": "_ZN1A1gEvRO",  # arbitrary but distinct
                "type": {"qualType": "void () & throw()"},
                "virtual": True,
            },
        ),
        _record(
            "D",
            {
                "kind": "CXXMethodDecl",
                "name": "g",
                "mangledName": "_ZN1D1gEvRR",  # arbitrary but distinct
                "type": {"qualType": "void () && throw()"},
            },
            bases=[_base("A")],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1A1gEvRO"]  # base slot untouched


# ── a base that is a CONCRETE template specialization, not an ordinary ────
# ── record (Codex review, fresh evidence, P1) ──────────────────────────


def _specialization(name: str, *inner: dict, type_args: list[str]) -> dict:
    """A ``ClassTemplateSpecializationDecl`` node, mirroring real clang
    output: one ``TemplateArgument`` child per type argument (each carrying
    only the ``type.qualType`` this module's spelling-reconstruction
    reads), followed by *inner*'s own children.
    """
    args = [{"kind": "TemplateArgument", "type": {"qualType": t}} for t in type_args]
    return {
        "kind": "ClassTemplateSpecializationDecl",
        "name": name,
        "completeDefinition": True,
        "inner": [*args, *inner],
    }


def test_base_resolved_via_concrete_template_specialization() -> None:
    """A base that is a CONCRETE template specialization (``struct D :
    A<int> {...};``) used to be entirely unresolvable: clang emits the
    usable definition as a ``ClassTemplateSpecializationDecl``, a different
    node kind from the ``CXXRecordDecl``/``RecordDecl`` pair the ordinary
    base-lookup index collects, so ``A<int>``'s own vtable was invisible to
    ``D`` (Codex review, fresh evidence, P1). An old ``D`` deriving from
    ``A<int>`` resolved to an empty vtable/no vptr regardless of what
    ``A<int>`` itself provides -- confirmed via the sibling live-clang
    integration test (``test_clang_backend_resolves_concrete_template_
    specialization_base``); this is the same shape reduced to a hand-built
    fixture.
    """
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                _record("A"),  # the uninstantiated pattern -- irrelevant here
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AIiE1fEi",
                        "type": {"qualType": "void (int)"},
                        "virtual": True,
                        "inner": [{"kind": "ParmVarDecl", "type": {"qualType": "int"}}],
                    },
                    type_args=["int"],
                ),
            ],
        },
        _record("D", bases=[_base("A<int>")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1AIiE1fEi"]
    assert types["D"].vptr_offset_bits == 0


def test_specialization_base_override_with_no_keyword_replaces_slot_in_place() -> None:
    """The flagship no-keyword-override scenario, through a concrete
    template specialization base instead of an ordinary one: ``D::f``
    repeats neither ``virtual`` nor ``override``, yet must still be
    recognized as overriding ``A<int>::f`` via signature matching and
    replace its slot in place, not append a duplicate."""
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AIiE1fEi",
                        "type": {"qualType": "void (int)"},
                        "virtual": True,
                        "inner": [{"kind": "ParmVarDecl", "type": {"qualType": "int"}}],
                    },
                    type_args=["int"],
                ),
            ],
        },
        _record(
            "D",
            _method("f", "_ZN1D1fEi", params=["int"]),  # no virtual, no OverrideAttr
            bases=[_base("A<int>")],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1D1fEi"]  # replaced, not appended

    funcs = {
        f.mangled: f for f in _ClangAstParser(root, set(), set()).parse_functions()
    }
    # The inferred virtuality must propagate to Function.is_virtual (feeds
    # diff_cxx_rules.vtable_slot_is_override_reuse()'s own requirement)...
    assert funcs["_ZN1D1fEi"].is_virtual is True
    # ...and the specialization's own occupant must be qualified with its
    # SPELLED specialization name (matching what D.bases records), not left
    # bare -- otherwise owner_class_of()'s mangled-name fallback recovers
    # only the RAW, un-spelled Itanium encoding ("AIiE"), which never
    # matches D.bases == ["A<int>"] and produces a false
    # TYPE_VTABLE_CHANGED once the base above is resolved at all.
    assert funcs["_ZN1AIiE1fEi"].name == "A<int>::f"


def _forward_specialization(name: str, *, type_args: list[str]) -> dict:
    """A forward-declared explicit specialization node -- no
    ``completeDefinition``, no member children -- sharing the same spelling
    a later complete definition would."""
    args = [{"kind": "TemplateArgument", "type": {"qualType": t}} for t in type_args]
    return {
        "kind": "ClassTemplateSpecializationDecl",
        "name": name,
        "inner": args,
    }


def test_specialization_forward_declaration_does_not_shadow_complete_definition() -> (
    None
):
    """Codex review, fresh evidence (P1): an explicit specialization can be
    forward-declared (`template<> struct A<int>;`) before its complete
    definition (`template<> struct A<int> { ... };`) -- both share the
    identical spelling `"A<int>"`. A first-registration-wins policy would
    permanently keep the empty forward-decl stub whenever it's walked
    first, the same shape `_record_index()` already guards for an ordinary
    record."""
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                _record("A"),
            ],
        },
        _forward_specialization("A", type_args=["int"]),  # forward decl, walked FIRST
        _specialization(
            "A",
            {
                "kind": "CXXMethodDecl",
                "name": "f",
                "mangledName": "_ZN1AIiE1fEi",
                "type": {"qualType": "void ()"},
                "virtual": True,
            },
            type_args=["int"],
        ),
        _record("D", bases=[_base("A<int>")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1AIiE1fEi"]
    assert types["D"].vptr_offset_bits == 0


def test_bool_nontype_specialization_argument_is_not_indexed() -> None:
    """Codex review, fresh evidence (P1): a non-type template argument's
    JSON ``value`` does not reliably print the same way a base reference
    spells it -- `template <bool B> struct A; ... A<true>` reports
    `value: -1` (confirmed empirically), never `"true"`, so a naively
    reconstructed `"A<-1>"` spelling never matches the real base reference
    `"A<true>"`. Must degrade to the same "unresolvable base" false
    negative every other unresolvable-base shape in this module already
    degrades to -- NOT fabricate a mismatched key that could actively
    collide with, or fail to find, the right specialization."""
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "inner": [
                {
                    "kind": "NonTypeTemplateParmDecl",
                    "name": "B",
                    "type": {"qualType": "bool"},
                },
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AILb1EE1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=[],  # overridden below via a raw value arg
                ),
            ],
        },
        _record("D", bases=[_base("A<true>")]),
    )
    # Patch the specialization's TemplateArgument to the real clang shape
    # for a bool argument (`value`, no `type`) -- the `_specialization`
    # helper only builds type arguments.
    spec = root["inner"][0]["inner"][2]
    spec["inner"].insert(0, {"kind": "TemplateArgument", "value": -1})

    types = _types(root)
    # Unresolvable: D's base "A<true>" can't be matched against anything
    # this module would index it under, so D stays non-polymorphic rather
    # than risking a wrong match.
    assert types["D"].vtable == []
    assert types["D"].vptr_offset_bits is None


def test_int_nontype_specialization_argument_still_resolves() -> None:
    """The safe counterpart to the bool case above: a plain `int` non-type
    argument's `value` DOES reliably print the same way a base reference
    spells it (`A<3>`), confirmed empirically -- must still resolve."""
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "inner": [
                {
                    "kind": "NonTypeTemplateParmDecl",
                    "name": "N",
                    "type": {"qualType": "int"},
                },
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AILi3EE1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=[],
                ),
            ],
        },
        _record("D", bases=[_base("A<3>")]),
    )
    spec = root["inner"][0]["inner"][2]
    spec["inner"].insert(0, {"kind": "TemplateArgument", "value": 3})

    types = _types(root)
    assert types["D"].vtable == ["_ZN1AILi3EE1fEv"]
    assert types["D"].vptr_offset_bits == 0

    # CodeRabbit review, fresh evidence: the `_walk` call site that scopes a
    # specialization's own members for Function.name qualification must
    # ALSO resolve a confirmed-safe non-type argument like this one -- an
    # earlier version passed no param_kinds context there at all, so this
    # member stayed bare ("f") regardless of the base-lookup index's own
    # (correctly resolved) spelling, leaving owner_class_of's mangled
    # fallback to recover the raw "AILi3EE" encoding instead, which never
    # matches D.bases == ["A<3>"].
    funcs = {
        f.mangled: f for f in _ClangAstParser(root, set(), set()).parse_functions()
    }
    assert funcs["_ZN1AILi3EE1fEv"].name == "A<3>::f"


# ── sixth review round: dynamic exception specs and omitted default ───────
# ── template arguments ─────────────────────────────────────────────────


def test_dynamic_exception_spec_narrowing_is_not_an_override_mismatch() -> None:
    """Codex review, fresh evidence: a C++14-and-earlier dynamic exception
    specification (`throw(int)`) participated in the qualifier tail
    unstripped (only `noexcept` was), so a base `virtual void f()
    throw(int);` and a derived, explicitly-overriding `void f() throw()
    override;` -- a legal narrowing, confirmed compiling with real clang --
    compared as different signatures and appended as a second slot instead
    of replacing the inherited one."""
    root = _tu(
        _record(
            "A",
            {
                "kind": "CXXMethodDecl",
                "name": "f",
                "mangledName": "_ZN1A1fEv",
                "type": {"qualType": "void () throw(int)"},
                "virtual": True,
            },
        ),
        _record(
            "D",
            {
                "kind": "CXXMethodDecl",
                "name": "f",
                "mangledName": "_ZN1D1fEv",
                "type": {"qualType": "void () throw()"},
                "inner": [{"kind": "OverrideAttr"}],
            },
            bases=[_base("A")],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1D1fEv"]  # replaced, not appended


def test_default_template_argument_omitted_from_base_reference_resolves() -> None:
    """Codex review, fresh evidence (P1): a specialization always carries a
    `TemplateArgument` for EVERY parameter, including one a base reference
    omitted because it equals its own default (`template <class T, class U
    = int> struct A; struct D : A<double> {...};` reports arguments for
    BOTH `T` and `U`, confirmed with a real clang build) -- joining all of
    them unconditionally produced `"A<double, int>"`, which never matches
    the referring site's own `"A<double>"`, leaving the base unresolvable.
    """
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                {
                    "kind": "TemplateTypeParmDecl",
                    "name": "U",
                    "defaultArg": {
                        "kind": "TemplateArgument",
                        "type": {"qualType": "int"},
                    },
                },
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AIdiE1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=["double", "int"],
                ),
            ],
        },
        _record("D", bases=[_base("A<double>")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1AIdiE1fEv"]
    assert types["D"].vptr_offset_bits == 0


def test_default_template_argument_explicitly_repeated_still_resolves() -> None:
    """The same collapse must apply when a base explicitly repeats the
    default value: `struct D : A<double, int> {...};` reports
    `type.qualType == "A<double, int>"` (as literally written) but
    `type.desugaredQualType == "A<double>"` (defaults collapsed), confirmed
    with a real clang build -- `_base_qualnames` already prefers
    `desugaredQualType`, so this must resolve to the SAME trimmed spelling
    the omitted-default case does."""
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                {
                    "kind": "TemplateTypeParmDecl",
                    "name": "U",
                    "defaultArg": {
                        "kind": "TemplateArgument",
                        "type": {"qualType": "int"},
                    },
                },
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AIdiE1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=["double", "int"],
                ),
            ],
        },
        _record(
            "D",
            bases=[
                {
                    "type": {
                        "qualType": "A<double, int>",
                        "desugaredQualType": "A<double>",
                    },
                    "access": "public",
                    "isVirtual": False,
                }
            ],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1AIdiE1fEv"]


# ── eighth review round: an explicit override on an unresolvable-base ─────
# ── class must not fabricate a new slot, and a dependent default must ─────
# ── be substituted before comparing ────────────────────────────────────────


def test_explicit_override_on_unresolvable_base_does_not_fabricate_a_slot() -> None:
    """Codex review, fresh evidence (P1): an unresolvable specialization
    base (a `bool` non-type argument, deliberately left unindexed --
    see the bool test above) used to leave `D`'s own EXPLICITLY-marked
    override as an apparent brand-new slot, since nothing recognized it as
    an override of anything. Confirmed end-to-end: an old `D : A<true> {}`
    (empty vtable, unresolvable base) and a new `D` adding only `void f()
    override;` produced a real `VPTR_INTRODUCED`/`TYPE_VTABLE_CHANGED`
    false positive. A class with an unresolved base must not add ANY new
    own slot (explicit or not) -- ambiguous whether it's a genuine
    addition or an invisible override -- degrading to the same accepted
    false negative this module's unresolvable-base handling already uses
    elsewhere."""
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "inner": [
                {
                    "kind": "NonTypeTemplateParmDecl",
                    "name": "B",
                    "type": {"qualType": "bool"},
                },
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AILb1EE1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=[],
                ),
            ],
        },
        _record(
            "D",
            _method("f", "_ZN1D1fEv", override_attr=True),  # explicit override
            bases=[_base("A<true>")],
        ),
    )
    spec = root["inner"][0]["inner"][2]
    spec["inner"].insert(0, {"kind": "TemplateArgument", "value": -1})

    types = _types(root)
    # The base stays unresolvable (same as the plain bool test), and D's
    # own explicit override must NOT be fabricated as a new slot either.
    assert types["D"].vtable == []
    assert types["D"].vptr_offset_bits is None


def test_unresolvable_base_still_suppresses_a_genuinely_unrelated_new_virtual() -> None:
    """The suppression is deliberately coarse (per-record, not
    per-method): a class with an unresolvable base also suppresses an own
    virtual method that has NOTHING to do with that base, since this
    module has no way to distinguish the two cases. Documents the accepted
    false-negative trade explicitly, rather than leaving it implicit."""
    root = _tu(
        _record(
            "D",
            _method("unrelated", "_ZN1D9unrelatedEv", virtual=True),
            bases=[_base("Unseen")],
        ),
    )
    types = _types(root)
    assert types["D"].vtable == []  # accepted false negative, not a crash


def test_dependent_default_template_argument_is_substituted_before_matching() -> None:
    """Codex review, fresh evidence (P1): a default that depends on an
    EARLIER parameter (`template <class T, class U = T> struct A;`)
    reports the literal, unsubstituted default spelling (`"T"`), which
    never equals a real resolved argument (`"double"`) by plain string
    comparison -- confirmed with a real clang build that this left `struct
    D : A<double> {...};` unresolvable, the identical false-positive shape
    the plain-default-collapse fix above closed for a non-dependent
    default. Must substitute the earlier parameter's own resolved value
    before comparing."""
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                {
                    "kind": "TemplateTypeParmDecl",
                    "name": "U",
                    "defaultArg": {
                        "kind": "TemplateArgument",
                        "type": {"qualType": "T"},  # dependent on T, not a literal
                    },
                },
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AIddE1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=["double", "double"],  # U resolved to T's own value
                ),
            ],
        },
        _record("D", bases=[_base("A<double>")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1AIddE1fEv"]
    assert types["D"].vptr_offset_bits == 0


def test_partially_dependent_default_is_conservatively_not_substituted() -> None:
    """A default only PARTIALLY dependent on an earlier parameter (e.g.
    `U = std::vector<T>` in spirit -- modeled here as a default spelling
    that merely CONTAINS the earlier parameter's name rather than being
    exactly equal to it) must not be guessed at: the substitution only
    ever fires on an EXACT match against a parameter's bare name, so this
    stays unindexed rather than risking a wrong drop."""
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                {
                    "kind": "TemplateTypeParmDecl",
                    "name": "U",
                    "defaultArg": {
                        "kind": "TemplateArgument",
                        "type": {"qualType": "Wrapper<T>"},  # not a bare "T"
                    },
                },
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AIdS_E1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=["double", "Wrapper<double>"],
                ),
            ],
        },
        _record("D", bases=[_base("A<double>")]),
    )
    types = _types(root)
    # Unresolvable: the reconstructed spelling stays "A<double,
    # Wrapper<double>>", which never matches the base reference's own
    # "A<double>" -- correctly conservative rather than guessing.
    assert types["D"].vtable == []


def test_fully_defaulted_specialization_still_indexes_as_empty_angle_brackets() -> (
    None
):
    """Codex review, fresh evidence (P1): when EVERY template argument
    equals its own default, popping them all left ``args`` empty and the
    whole specialization returned ``None`` (unresolvable) -- but clang
    still prints an explicit, empty angle-bracket pair (``"A<>"``) on the
    base reference, never a bare ``"A"`` (confirmed with a real clang
    build: ``template<class T=int> struct A {...}; struct D : A<> {...};``
    gives ``bases[0].type.qualType == "A<>"``). Unlike a genuine
    no-arguments case (a template-template argument, pack expansion, or
    zero-parameter template), every argument here IS safely known -- only
    the resulting joined spelling happens to be empty.
    """
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "inner": [
                {
                    "kind": "TemplateTypeParmDecl",
                    "name": "T",
                    "defaultArg": {
                        "kind": "TemplateArgument",
                        "type": {"qualType": "int"},
                    },
                },
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AIiE1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=["int"],
                ),
            ],
        },
        _record("D", bases=[_base("A<>")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1AIiE1fEv"]
    assert types["D"].vptr_offset_bits == 0


def test_nested_specialization_with_defaulted_argument_resolves() -> None:
    """Codex review, fresh evidence (P1, second round): a NESTED template's
    own defaulted argument (``Outer<int>::A<>``) needs its
    param_kinds/param_defaults/param_names looked up under the SAME
    unspelled scope the whole-AST index functions themselves use for a
    nested template's own ``ClassTemplateDecl`` (confirmed empirically:
    ``_index_template_param_kinds`` registers a specialization's own
    nested template under its bare, unqualified name -- e.g. ``"A"`` --
    never under the outer specialization's spelled qualname
    ``"Outer<int>::A"``, since ``ClassTemplateSpecializationDecl`` never
    extends scope in those three index functions' own walks). Looking the
    lookup up with the SPELLED scope (as an earlier fix here did, for
    getting the FINAL registration qualname right) missed every entry,
    leaving the trailing default un-trimmed and the whole nested
    specialization unresolvable.
    """
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "Outer",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                _record("Outer"),
                {
                    "kind": "ClassTemplateSpecializationDecl",
                    "name": "Outer",
                    "completeDefinition": True,
                    "inner": [
                        {"kind": "TemplateArgument", "type": {"qualType": "int"}},
                        {
                            "kind": "ClassTemplateDecl",
                            "name": "A",
                            "inner": [
                                {
                                    "kind": "TemplateTypeParmDecl",
                                    "name": "U",
                                    "defaultArg": {
                                        "kind": "TemplateArgument",
                                        "type": {"qualType": "int"},
                                    },
                                },
                                _record("A"),
                                _specialization(
                                    "A",
                                    {
                                        "kind": "CXXMethodDecl",
                                        "name": "f",
                                        "mangledName": "_ZN5OuterIiE1AIiE1fEv",
                                        "type": {"qualType": "void ()"},
                                        "virtual": True,
                                    },
                                    type_args=["int"],
                                ),
                            ],
                        },
                    ],
                },
            ],
        },
        _record("D", bases=[_base("Outer<int>::A<>")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN5OuterIiE1AIiE1fEv"]
    assert types["D"].vptr_offset_bits == 0


def test_nested_specialization_indexes_under_outer_specialization_qualname() -> None:
    """Codex review, fresh evidence (P2): a specialization containing its
    own NESTED specialization (``struct D : Outer<int>::A<double>``) must
    descend under the OUTER specialization's own SPELLED qualname
    (``"Outer<int>"``), not its bare ``name`` (``"Outer"``) --
    ``ClassTemplateSpecializationDecl`` is deliberately not in
    ``_SCOPE_NODE_KINDS`` (it isn't an ordinary namespace/class/
    linkage-spec scope), so falling through to that generic branch would
    silently drop the outer specialization's own template arguments from
    the nested one's qualname, indexing it as bare ``"A<double>"`` instead
    of ``"Outer<int>::A<double>"`` and leaving the base unresolvable.
    """
    inner_spec = _specialization(
        "A",
        {
            "kind": "CXXMethodDecl",
            "name": "f",
            "mangledName": "_ZN5OuterIiE1AIdE1fEv",
            "type": {"qualType": "void ()"},
            "virtual": True,
        },
        type_args=["double"],
    )
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "Outer",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                _record("Outer"),
                {
                    "kind": "ClassTemplateSpecializationDecl",
                    "name": "Outer",
                    "completeDefinition": True,
                    "inner": [
                        {"kind": "TemplateArgument", "type": {"qualType": "int"}},
                        inner_spec,
                    ],
                },
            ],
        },
        _record("D", bases=[_base("Outer<int>::A<double>")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN5OuterIiE1AIdE1fEv"]
    assert types["D"].vptr_offset_bits == 0
