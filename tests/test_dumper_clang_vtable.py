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
