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

from abicheck.dumper_clang import _ClangAstParser


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
