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

"""Detectors for KDE C++ binary-compatibility gaps.

Covers two rules from
https://community.kde.org/Policies/Binary_Compatibility_Issues_With_C%2B%2B that
previously had no dedicated detector:

- VIRTUAL_METHOD_ADDED — adding a virtual method to a class that already exists
  across versions ("do not add virtuals to a non-leaf class"). BREAKING. Scoped
  to the blind spot where the vtable array itself is not diff-able; when it is,
  TYPE_VTABLE_CHANGED already reports the growth.
- OVERLOAD_ADDED — adding an overload to a previously unique public name. Binary
  compatible but source-risky (`&f` ambiguity, resolution shifts).
  COMPATIBLE_WITH_RISK.
"""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.diff_cxx_rules import (
    itanium_ctor_dtor_marker_span,
    itanium_qualified_name,
    itanium_scope_components,
    msvc_qualified_name,
    msvc_scope_components,
    owner_class_of,
)
from abicheck.model import (
    AbiSnapshot,
    Fact,
    Function,
    Param,
    RecordType,
    Visibility,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _snap(
    version: str = "1.0",
    *,
    functions: list[Function] | None = None,
    types: list[RecordType] | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions or [],
        types=types or [],
    )


def _method(
    name: str, mangled: str, *, is_virtual: bool = False, params=None
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=params or [],
        visibility=Visibility.PUBLIC,
        is_virtual=is_virtual,
    )


def _cls(name: str, *, vtable: list[str] | None = None) -> RecordType:
    # ADR-063 Phase 5B: `bases`/`virtual_bases` are stated explicitly (an
    # empty list, not omitted) so `bases_fact`/`virtual_bases_fact` read
    # PRESENT — the same "always stated, even when empty" shape every real
    # producer (`dwarf_snapshot.py` et al.) already constructs, and what
    # `diff_cxx_rules.virtual_method_addition`'s evidence-completeness check
    # now requires before it will trust a transitive-bases walk that reaches
    # this record.
    return RecordType(
        name=name,
        kind="class",
        size_bits=64,
        vtable=vtable or [],
        bases=[],
        virtual_bases=[],
    )


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


# ── VIRTUAL_METHOD_ADDED ─────────────────────────────────────────────────────


class TestVirtualMethodAdded:
    def test_new_virtual_on_existing_class_is_breaking(self):
        c_old = _cls("Widget")
        c_new = _cls("Widget")
        old = _snap(
            functions=[_method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True)],
            types=[c_old],
        )
        new = _snap(
            functions=[
                _method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True),
                _method("Widget::resize", "_ZN6Widget6resizeEv", is_virtual=True),
            ],
            types=[c_new],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_new_nonvirtual_method_is_compatible(self):
        """Adding a non-virtual method is a compatible addition, not a vtable break."""
        old = _snap(
            functions=[_method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True)],
            types=[_cls("Widget")],
        )
        new = _snap(
            functions=[
                _method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True),
                _method("Widget::helper", "_ZN6Widget6helperEv", is_virtual=False),
            ],
            types=[_cls("Widget")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)
        assert ChangeKind.FUNC_ADDED in _kinds(result)

    def test_virtual_on_brand_new_class_is_compatible(self):
        """A new class (absent from old) with virtuals is an additive, compatible change."""
        old = _snap(functions=[], types=[])
        new = _snap(
            functions=[_method("Fresh::go", "_ZN5Fresh2goEv", is_virtual=True)],
            types=[_cls("Fresh")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)

    def test_diffable_vtable_growth_defers_to_vtable_change(self):
        """When the vtable array itself records the growth, TYPE_VTABLE_CHANGED
        owns the finding and VIRTUAL_METHOD_ADDED stays silent (no double-report).

        An anchor function keeps ``Widget`` in the ABI surface so the
        surface-scoped vtable detector engages (mirrors the oracle fixtures)."""
        anchor = Function(
            name="make",
            mangled="_Z4makev",
            return_type="Widget *",
            visibility=Visibility.PUBLIC,
        )
        old = _snap(
            functions=[
                anchor,
                _method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True),
            ],
            types=[_cls("Widget", vtable=["_ZN6Widget5paintEv"])],
        )
        new = _snap(
            functions=[
                anchor,
                _method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True),
                _method("Widget::resize", "_ZN6Widget6resizeEv", is_virtual=True),
            ],
            types=[
                _cls("Widget", vtable=["_ZN6Widget5paintEv", "_ZN6Widget6resizeEv"])
            ],
        )
        result = compare(old, new)
        assert ChangeKind.TYPE_VTABLE_CHANGED in _kinds(result)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_namespaced_owner_resolves(self):
        old = _snap(
            functions=[
                _method("kde::View::show", "_ZN3kde4View4showEv", is_virtual=True)
            ],
            types=[_cls("kde::View")],
        )
        new = _snap(
            functions=[
                _method("kde::View::show", "_ZN3kde4View4showEv", is_virtual=True),
                _method("kde::View::hide", "_ZN3kde4View4hideEv", is_virtual=True),
            ],
            types=[_cls("kde::View")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED in _kinds(result)

    def test_unqualified_castxml_name_resolves_owner_from_mangled(self):
        """castxml records the bare leaf (``bar``) on methods, so the owner must
        be recovered from the mangled name — otherwise the detector's own
        blind-spot case (empty vtable array) degrades to a compatible
        FUNC_ADDED instead of the BREAKING vtable growth."""
        old = _snap(
            functions=[_method("foo", "_ZN1C3fooEv", is_virtual=True)],
            types=[_cls("C")],
        )
        new = _snap(
            functions=[
                _method("foo", "_ZN1C3fooEv", is_virtual=True),
                _method("bar", "_ZN1C3barEv", is_virtual=True),  # unqualified leaf
            ],
            types=[_cls("C")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_namespaced_owner_matches_castxml_leaf_record_name(self):
        """CastXML records the class under its leaf name (``View``) while the
        owner derived from the mangled symbol is qualified (``kde::View``); the
        lookup must reconcile the two so the vtable break is still caught."""
        old = _snap(
            functions=[_method("show", "_ZN3kde4View4showEv", is_virtual=True)],
            types=[_cls("View")],  # leaf-only record name, as CastXML emits
        )
        new = _snap(
            functions=[
                _method("show", "_ZN3kde4View4showEv", is_virtual=True),
                _method("hide", "_ZN3kde4View4hideEv", is_virtual=True),
            ],
            types=[_cls("View")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_brand_new_namespaced_class_sharing_a_leaf_is_compatible(self):
        """A brand-new ``kde::View`` must not be attached to an unrelated
        pre-existing ``foo::View`` just because CastXML records both as the leaf
        ``View``. Pre-existence is decided by the qualified owner of sibling
        symbols, so adding ``kde::View::hide`` here stays a compatible addition."""
        old = _snap(
            functions=[
                _method("bar", "_ZN3foo4View3barEv", is_virtual=True)
            ],  # foo::View
            types=[_cls("View")],
        )
        new = _snap(
            functions=[
                _method("bar", "_ZN3foo4View3barEv", is_virtual=True),
                _method(
                    "hide", "_ZN3kde4View4hideEv", is_virtual=True
                ),  # kde::View (new)
            ],
            types=[_cls("View")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)

    def test_added_virtual_destructor_resolves_owner_from_mangled(self):
        """A virtual destructor added to an existing class (empty-vtable blind
        spot) is a vtable break; its CastXML leaf name is just ``~C`` so the
        owner must come from the mangled name (``_ZN1CD1Ev``)."""
        old = _snap(
            functions=[_method("C::foo", "_ZN1C3fooEv", is_virtual=True)],
            types=[_cls("C")],
        )
        new = _snap(
            functions=[
                _method("C::foo", "_ZN1C3fooEv", is_virtual=True),
                _method("~C", "_ZN1CD1Ev", is_virtual=True),  # virtual dtor, leaf name
            ],
            types=[_cls("C")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_inherited_override_is_not_virtual_method_added(self):
        """Overriding a virtual inherited from a base reuses the base's vtable
        slot — ABI-compatible, not a new slot. Must not fire."""
        base = _cls("Base")
        derived = RecordType(
            name="Derived",
            kind="class",
            size_bits=64,
            vtable=[],
            bases=["Base"],
            virtual_bases=[],
        )
        old = _snap(
            functions=[
                _method("Base::paint", "_ZN4Base5paintEv", is_virtual=True),
                _method(
                    "Derived::help", "_ZN7Derived4helpEv"
                ),  # keeps Derived in surface
            ],
            types=[base, derived],
        )
        new = _snap(
            functions=[
                _method("Base::paint", "_ZN4Base5paintEv", is_virtual=True),
                _method("Derived::help", "_ZN7Derived4helpEv"),
                _method(
                    "Derived::paint", "_ZN7Derived5paintEv", is_virtual=True
                ),  # override
            ],
            types=[base, derived],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)

    def test_same_name_different_signature_virtual_is_new_slot(self):
        """A same-named virtual with a *different* signature is a new vtable slot,
        not an override — must still fire."""
        base = _cls("Base")
        derived = RecordType(
            name="Derived",
            kind="class",
            size_bits=64,
            vtable=[],
            bases=["Base"],
            virtual_bases=[],
        )
        old = _snap(
            functions=[
                _method(
                    "Base::paint",
                    "_ZN4Base5paintEi",
                    is_virtual=True,
                    params=[Param(name="x", type="int")],
                ),
                _method("Derived::help", "_ZN7Derived4helpEv"),
            ],
            types=[base, derived],
        )
        new = _snap(
            functions=[
                _method(
                    "Base::paint",
                    "_ZN4Base5paintEi",
                    is_virtual=True,
                    params=[Param(name="x", type="int")],
                ),
                _method("Derived::help", "_ZN7Derived4helpEv"),
                _method(
                    "Derived::paint",
                    "_ZN7Derived5paintEd",
                    is_virtual=True,
                    params=[Param(name="x", type="double")],
                ),  # different signature
            ],
            types=[base, derived],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED in _kinds(result)

    def test_namespaced_inherited_override_is_not_virtual_method_added(self):
        """Override of an inherited virtual in a namespaced class (CastXML
        leaf-only records) must resolve bases and stay compatible."""
        base = _cls("Base")  # CastXML stores ns::Base as leaf "Base"
        derived = RecordType(
            name="Derived",
            kind="class",
            size_bits=64,
            vtable=[],
            bases=["Base"],
            virtual_bases=[],
        )
        old = _snap(
            functions=[
                _method(
                    "paint", "_ZN2ns4Base5paintEv", is_virtual=True
                ),  # ns::Base::paint
                _method("help", "_ZN2ns7Derived4helpEv"),  # ns::Derived::help
            ],
            types=[base, derived],
        )
        new = _snap(
            functions=[
                _method("paint", "_ZN2ns4Base5paintEv", is_virtual=True),
                _method("help", "_ZN2ns7Derived4helpEv"),
                _method(
                    "paint", "_ZN2ns7Derived5paintEv", is_virtual=True
                ),  # ns::Derived::paint override
            ],
            types=[base, derived],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)

    def test_unchanged_class_no_finding(self):
        old = _snap(
            functions=[_method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True)],
            types=[_cls("Widget")],
        )
        new = _snap(
            functions=[_method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True)],
            types=[_cls("Widget")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)

    def test_incomplete_bases_evidence_declines_rather_than_fabricates(self):
        """ADR-063 Phase 5B: a class whose ``bases``/``virtual_bases``
        evidence never arrived (``NOT_COLLECTED``, not confirmed-empty) must
        not be read as "no bases, therefore no possible override" — that
        gap could just as easily be hiding the very base that would make
        this an ABI-compatible override, not a genuine new virtual slot.
        Declining is the safe default (a missed VIRTUAL_METHOD_ADDED, not a
        fabricated one), the same "decline rather than fabricate" discipline
        already applied to ``bases``/``virtual_bases``/``is_va_list`` at
        their primary finding-emitting call sites.
        """
        derived = RecordType(
            name="Derived",
            kind="class",
            size_bits=64,
            vtable=[],
            bases_fact=Fact.not_collected(),
            virtual_bases_fact=Fact.not_collected(),
        )
        old = _snap(
            functions=[
                _method("Derived::help", "_ZN7Derived4helpEv"),
            ],
            types=[derived],
        )
        new = _snap(
            functions=[
                _method("Derived::help", "_ZN7Derived4helpEv"),
                _method("Derived::resize", "_ZN7Derived6resizeEv", is_virtual=True),
            ],
            types=[derived],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)

    def test_partial_virtual_bases_evidence_declines(self):
        """Sibling case: ``PARTIAL`` (not ``NOT_COLLECTED``) is incomplete
        too — the uncovered remainder of a partially-covered virtual-bases
        list could hold the very base that makes this an override."""
        derived = RecordType(
            name="Derived",
            kind="class",
            size_bits=64,
            vtable=[],
            bases_fact=Fact.present([]),
            virtual_bases_fact=Fact.partial([]),
        )
        old = _snap(
            functions=[_method("Derived::help", "_ZN7Derived4helpEv")],
            types=[derived],
        )
        new = _snap(
            functions=[
                _method("Derived::help", "_ZN7Derived4helpEv"),
                _method("Derived::resize", "_ZN7Derived6resizeEv", is_virtual=True),
            ],
            types=[derived],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)

    def test_incomplete_evidence_on_transitive_base_declines(self):
        """Sibling case: the owner's own ``bases``/``virtual_bases`` are
        fully confirmed, but a base reached *transitively* (one level
        further out) has incomplete evidence — the walk-wide completeness
        tracking must catch this too, not just the immediate owner's own
        facts."""
        base = RecordType(
            name="Base",
            kind="class",
            size_bits=64,
            vtable=[],
            bases_fact=Fact.not_collected(),  # Base's own ancestry is unknown
            virtual_bases_fact=Fact.not_collected(),
        )
        derived = RecordType(
            name="Derived",
            kind="class",
            size_bits=64,
            vtable=[],
            bases=["Base"],
            virtual_bases=[],
        )
        old = _snap(
            functions=[_method("Derived::help", "_ZN7Derived4helpEv")],
            types=[base, derived],
        )
        new = _snap(
            functions=[
                _method("Derived::help", "_ZN7Derived4helpEv"),
                _method("Derived::resize", "_ZN7Derived6resizeEv", is_virtual=True),
            ],
            types=[base, derived],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)

    def test_complete_empty_bases_evidence_still_fires(self):
        """The control for the previous test: a *confirmed*-empty
        ``bases``/``virtual_bases`` (``PRESENT`` status, empty list — what
        every real producer emits for a base-less class) is fully trusted,
        same as before this evidence-completeness check existed."""
        old = _snap(
            functions=[_method("Derived::help", "_ZN7Derived4helpEv")],
            types=[_cls("Derived")],
        )
        new = _snap(
            functions=[
                _method("Derived::help", "_ZN7Derived4helpEv"),
                _method("Derived::resize", "_ZN7Derived6resizeEv", is_virtual=True),
            ],
            types=[_cls("Derived")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED in _kinds(result)


# ── OVERLOAD_ADDED ───────────────────────────────────────────────────────────


class TestOverloadAdded:
    def test_overload_added_to_unique_function_is_risk(self):
        old = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")])
            ]
        )
        new = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")]),
                _method("draw", "_Z4drawd", params=[Param(name="x", type="double")]),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED in _kinds(result)
        assert result.verdict == Verdict.COMPATIBLE_WITH_RISK

    def test_overload_added_to_method(self):
        old = _snap(
            functions=[
                _method("Img::at", "_ZN3Img2atEi", params=[Param(name="i", type="int")])
            ]
        )
        new = _snap(
            functions=[
                _method(
                    "Img::at", "_ZN3Img2atEi", params=[Param(name="i", type="int")]
                ),
                _method(
                    "Img::at", "_ZN3Img2atEll", params=[Param(name="i", type="long")]
                ),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED in _kinds(result)

    def test_adding_to_already_overloaded_name_is_compatible(self):
        """KDE allows adding further overloads to an already-overloaded name."""
        old = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")]),
                _method("draw", "_Z4drawd", params=[Param(name="x", type="double")]),
            ]
        )
        new = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")]),
                _method("draw", "_Z4drawd", params=[Param(name="x", type="double")]),
                _method("draw", "_Z4drawf", params=[Param(name="x", type="float")]),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)

    def test_brand_new_unique_function_is_not_overload(self):
        old = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")])
            ]
        )
        new = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")]),
                _method("paint", "_Z5paintv"),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)
        assert ChangeKind.FUNC_ADDED in _kinds(result)

    def test_added_conversion_operator_is_not_treated_as_overload(self):
        # Codex review, fresh evidence: itanium_scope_components() previously
        # reduced every conversion operator's leaf to the same fixed
        # "{op:cv}" placeholder regardless of target type, so adding
        # operator double() alongside an existing operator int() collapsed
        # to the same _overload_group_key() and fired a false
        # OVERLOAD_ADDED -- two conversion operators to different types are
        # never overloads of each other (no shared `&Foo::operator T` can
        # become ambiguous).
        old = _snap(
            functions=[
                _method("operator int", "_ZNK3FoocviEv"),
            ]
        )
        new = _snap(
            functions=[
                _method("operator int", "_ZNK3FoocviEv"),
                _method("operator double", "_ZNK3FoocvdEv"),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)

    def test_signature_change_is_not_overload_added(self):
        """A pure signature change (remove+add of the same name) must not look
        like an overload addition: the original declaration is gone."""
        old = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")])
            ]
        )
        new = _snap(
            functions=[
                _method("draw", "_Z4drawd", params=[Param(name="x", type="double")])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)

    def test_no_change_no_overload(self):
        old = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")])
            ]
        )
        new = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)

    def test_added_operator_overload_is_overload_added(self):
        """Operators are encoded as fixed Itanium codes, not length-prefixed
        names; an operator overload (`operator[](int)` → also `(long)`) must
        still group and fire OVERLOAD_ADDED — `&C::operator[]` becomes ambiguous."""
        old = _snap(
            functions=[
                _method(
                    "C::operator[]", "_ZN1CixEi", params=[Param(name="i", type="int")]
                ),
            ]
        )
        new = _snap(
            functions=[
                _method(
                    "C::operator[]", "_ZN1CixEi", params=[Param(name="i", type="int")]
                ),
                _method(
                    "C::operator[]", "_ZN1CixEl", params=[Param(name="i", type="long")]
                ),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED in _kinds(result)

    def test_abi_tagged_overload_is_overload_added(self):
        """GNU ABI tags (`B5cxx11`, e.g. libstdc++ cxx11 std::string returns) are
        part of the unqualified name; a tagged overload must still group."""
        old = _snap(functions=[_method("C::get", "_ZN1C3getB5cxx11Ev")])
        new = _snap(
            functions=[
                _method("C::get", "_ZN1C3getB5cxx11Ev"),
                _method(
                    "C::get", "_ZN1C3getB5cxx11Ei", params=[Param(name="i", type="int")]
                ),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED in _kinds(result)

    def test_added_constructor_overload_is_not_overload_added(self):
        """Constructors can't be named or address-taken (`&C::C` is invalid), so
        adding a constructor overload is a compatible FUNC_ADDED, not the
        address-of-ambiguity OVERLOAD_ADDED."""
        old = _snap(functions=[_method("C", "_ZN1CC1Ev")])  # C::C()
        new = _snap(
            functions=[
                _method("C", "_ZN1CC1Ev"),
                _method(
                    "C", "_ZN1CC1Ei", params=[Param(name="x", type="int")]
                ),  # C::C(int)
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)
        assert ChangeKind.FUNC_ADDED in _kinds(result)

    def test_same_leaf_different_scope_is_not_overload(self):
        """Regression for the castxml/header path: ``Function.name`` is recorded
        without namespace/class scope, so ``A::size`` and a newly added
        ``B::size`` both arrive as the leaf ``size``. Grouping must use the
        scope-qualified identity (from the mangled name) so adding ``B::size``
        does not look like a second overload of ``A::size``."""
        old = _snap(functions=[_method("size", "_ZN1A4sizeEv")])  # A::size
        new = _snap(
            functions=[
                _method("size", "_ZN1A4sizeEv"),  # A::size retained
                _method("size", "_ZN1B4sizeEv"),  # B::size added in a different scope
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)
        assert ChangeKind.FUNC_ADDED in _kinds(result)

    def test_graduated_namespace_is_not_overload(self):
        """case99 shape: a stable ``lib::sort`` is added alongside the retained
        ``lib::experimental::sort``. Different scopes → not an overload add."""
        old = _snap(functions=[_method("sort", "_ZN3lib12experimental4sortEv")])
        new = _snap(
            functions=[
                _method("sort", "_ZN3lib12experimental4sortEv"),
                _method("sort", "_ZN3lib4sortEv"),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)

    def test_uniqueness_is_per_scope_not_per_leaf(self):
        """Even when an unrelated same-leaf ``B::size`` exists, adding a real
        ``A::size`` overload must still fire — the uniqueness test is per
        scope-qualified name, not the bare leaf (CastXML records both as
        ``size``)."""
        old = _snap(
            functions=[
                _method("size", "_ZN1A4sizeEv"),  # A::size (unique in its scope)
                _method("size", "_ZN1B4sizeEv"),  # unrelated B::size, same leaf
            ]
        )
        new = _snap(
            functions=[
                _method("size", "_ZN1A4sizeEv"),
                _method("size", "_ZN1B4sizeEv"),
                _method("size", "_ZN1A4sizeEi"),  # A::size(int) overload added
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED in _kinds(result)


class TestItaniumScopeParser:
    """The structural Itanium parser must work with no external demangler."""

    @pytest.mark.parametrize(
        "mangled,expected",
        [
            ("_Z4drawi", ["draw"]),  # free function
            ("_ZN1C3barEv", ["C", "bar"]),  # member
            ("_ZNK1C3barEv", ["C", "bar"]),  # const member (NK)
            ("_ZNV1C3barEv", ["C", "bar"]),  # volatile member (NV)
            ("_ZN3lib12experimental4sortEv", ["lib", "experimental", "sort"]),
            ("_ZN3BoxIiE4sizeEv", ["BoxIiE", "size"]),  # Box<int>::size
            ("_ZN3BoxIfE4sizeEv", ["BoxIfE", "size"]),  # Box<float>::size (distinct)
            ("_ZNR1C1fEv", ["C", "f"]),  # C::f() & (lvalue ref-qual)
            ("_ZNO1C1fEv", ["C", "f"]),  # C::f() && (rvalue ref-qual)
            ("_ZN1CC1Ev", ["C", "{ctor}"]),  # C::C() constructor
            ("_ZN1CD1Ev", ["C", "{dtor}"]),  # C::~C() destructor
            (
                "_ZN5ArrayILi4EE4sizeEv",
                ["ArrayILi4EE", "size"],
            ),  # Array<4>::size (non-type arg)
            ("_ZN1C3getB5cxx11Ev", ["C", "get[abi:cxx11]"]),  # C::get[abi:cxx11]()
            ("_ZSt5touchv", ["std", "touch"]),  # std::touch(), no N...E wrapper
            ("_ZNSt6detail3fooEv", ["std", "detail", "foo"]),  # std::detail::foo()
            # Mach-O direct-clang mangled names carry an extra platform leading
            # underscore (Codex review, fresh evidence: dumper_clang.py's own
            # _visibility() docstring documents "__ZN3lib3addEii" on macOS).
            ("__ZN1C3barEv", ["C", "bar"]),  # Mach-O member
            ("__ZSt5touchv", ["std", "touch"]),  # Mach-O std::touch()
            # Conversion operator (Codex review, fresh evidence): the "cv" code
            # is followed by the target type's own encoding, which is not
            # parsed -- only the scope prefix before "cv" is needed for owner
            # recovery -- but the leaf label embeds the raw remainder verbatim
            # (not a fixed placeholder) so distinct conversion targets still
            # produce distinct overload-grouping keys elsewhere.
            (
                "_ZNK3FoocvN2ns3BarEEv",
                ["Foo", "{op:cv:N2ns3BarEEv}"],
            ),  # Foo::operator ns::Bar() const
        ],
    )
    def test_components(self, mangled, expected):
        assert itanium_scope_components(mangled) == expected

    def test_template_specializations_have_distinct_keys(self):
        assert itanium_qualified_name("_ZN3BoxIiE4sizeEv") != itanium_qualified_name(
            "_ZN3BoxIfE4sizeEv"
        )
        # Non-type (integer) template args must also stay distinct per value.
        assert itanium_qualified_name(
            "_ZN5ArrayILi4EE4sizeEv"
        ) != itanium_qualified_name("_ZN5ArrayILi8EE4sizeEv")

    def test_ref_qualified_overloads_share_a_key(self):
        # C::f() & and C::f() && are genuine overloads → same scope key.
        assert itanium_qualified_name("_ZNR1C1fEv") == itanium_qualified_name(
            "_ZNO1C1fEv"
        )

    def test_constructor_overloads_share_a_key(self):
        assert itanium_qualified_name("_ZN1CC1Ev") == itanium_qualified_name(
            "_ZN1CC1Ei"
        )

    def test_destructor_owner_resolves_from_mangled(self):
        f = Function(
            name="~C",
            mangled="_ZN1CD1Ev",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        assert owner_class_of(f) == "C"

    def test_conversion_operator_owner_resolves_from_mangled(self):
        # direct-clang records a conversion operator's bare AST name
        # ("operator Bar", no owning-class prefix, confirmed via a real
        # clang -ast-dump) -- owner_class_of must fall back to the mangled
        # name, whose "cv" code was previously unmodelled entirely (Codex
        # review, fresh evidence).
        f = Function(
            name="operator Bar",
            mangled="_ZNK3FoocvN2ns3BarEEv",
            return_type="ns::Bar",
            visibility=Visibility.PUBLIC,
        )
        assert owner_class_of(f) == "Foo"

    def test_conversion_operator_owner_resolves_with_namespaced_class(self):
        f = Function(
            name="operator Bar",
            mangled="_ZNK2ns3FoocvN2ns3BarEEv",
            return_type="ns::Bar",
            visibility=Visibility.PUBLIC,
        )
        assert owner_class_of(f) == "ns::Foo"

    def test_bare_conversion_operator_with_qualified_target_falls_through(self):
        # CodeRabbit review: a bare-recorded conversion operator (no owning-
        # class prefix) can still carry a qualified target with its own
        # "::" (e.g. "operator ns::Bar") -- the "::operator " marker isn't
        # present (no owner precedes "operator"), so naively rsplit-ting at
        # the last "::" would wrongly treat the target's own qualification
        # as the owner/member boundary, returning junk like "operator ns"
        # instead of falling through to the mangled-name recovery.
        f = Function(
            name="operator ns::Bar",
            mangled="_ZNK3FoocvN2ns3BarEEv",
            return_type="ns::Bar",
            visibility=Visibility.PUBLIC,
        )
        assert owner_class_of(f) == "Foo"

    @pytest.mark.parametrize(
        "mangled",
        [
            "foo",  # not Itanium-mangled (C symbol)
            "_ZN1C99barEv",  # length runs past the string (malformed)
            "_Z1²0",  # fuzzed: Unicode digit must not reach int()
            "_ZN1CplEv",  # operator+ — not modelled
            "_ZN3BoxIiE",  # unterminated nested name after template args
            "_ZN3BoxIi4sizeEv",  # template-arg list with no closing E (unbalanced)
            "_ZN1C",  # truncated nested name
        ],
    )
    def test_unmodelled_or_degenerate_does_not_crash(self, mangled):
        # Must never raise; either parses to something or returns None.
        result = itanium_scope_components(mangled)
        assert result is None or isinstance(result, list)

    def test_qualified_name(self):
        assert itanium_qualified_name("_ZN1A4sizeEv") == "A::size"
        assert itanium_qualified_name("_Z4drawi") == "draw"

    def test_owner_prefers_display_name(self):
        f = Function(
            name="ns::C::bar",
            mangled="_ZN2ns1C3barEv",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        assert owner_class_of(f) == "ns::C"

    def test_owner_falls_back_to_mangled(self):
        f = Function(
            name="bar",
            mangled="_ZN1C3barEv",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        assert owner_class_of(f) == "C"

    def test_owner_none_for_free_function(self):
        f = Function(
            name="draw",
            mangled="_Z4drawi",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        assert owner_class_of(f) is None

    @pytest.mark.parametrize(
        "mangled,expected",
        [
            ("_ZN1CC1Ev", "C1"),  # C::C()
            ("_ZN1CD1Ev", "D1"),  # C::~C()
            ("_ZN3BoxIiEC1Eii", "C1"),  # ctor with parameters
            # buildsource.template_graph._ctor_dtor_symbol_variants's own
            # motivating case (Codex review, fresh evidence): a class literally
            # named C1Evil<int> embeds the literal substring "C1E" inside its
            # own length-prefixed name ("6C1Evil"), *before* the real ctor
            # code -- the structural walk must skip that whole identifier as
            # one unit rather than pattern-matching into the middle of it.
            ("_ZN6C1EvilIiEC1Ev", "C1"),
            # CodeRabbit nitpick: exercise the other real ctor/dtor codes
            # (C2/C3/D0/D2), not just the C1/D1 clang always reports, and a
            # Mach-O double-underscore-prefixed input -- confirmed these are
            # the exact sibling spellings a real compiled object exports
            # (buildsource.template_graph's own fourteenth review round).
            ("_ZN1CC2Ev", "C2"),
            ("_ZN1CC3Ev", "C3"),
            ("_ZN1CD0Ev", "D0"),
            ("_ZN1CD2Ev", "D2"),
            ("__ZN1CC1Ev", "C1"),  # Mach-O double-underscore prefix
            # ABI-tagged class template (Codex review, fresh evidence,
            # confirmed via a real `g++ -c` of `template <typename T> struct
            # __attribute__((abi_tag("tag"))) C { C(); };` instantiated as
            # C<int>): the tag ("B3tag") mangles *before* the template-args
            # ("IiE"), not after -- the real ctor code still follows both.
            ("_ZN1CB3tagIiEC1Ev", "C1"),
        ],
    )
    def test_ctor_dtor_marker_span_locates_the_real_code(self, mangled, expected):
        span = itanium_ctor_dtor_marker_span(mangled)
        assert span is not None
        start, end = span
        assert mangled[start:end] == expected

    @pytest.mark.parametrize(
        "mangled",
        [
            "_Z4drawi",  # free function -- never a ctor/dtor
            "_ZN1C3barEv",  # ordinary member function
            "_ZN3BoxIiE5valueE",  # a variable, not a ctor/dtor
            "foo",  # not Itanium-mangled at all
        ],
    )
    def test_ctor_dtor_marker_span_none_when_not_a_ctor_dtor(self, mangled):
        assert itanium_ctor_dtor_marker_span(mangled) is None

    def test_abi_tag_boundary_does_not_collide_with_a_plain_class_name(self):
        """An ABI-tagged class template's flattened identity must not
        collide with an unrelated, plainly-spelled class merely starting
        with the same letters (Codex review, fresh evidence): confirmed
        against two real compiled symbols -- `C[abi_tag("tag")]<int>::f()`
        (`_ZN1CB3tagIiE1fEv`) and an unrelated `CBtag<int>::f()`
        (`_ZN5CBtagIiE1fEv`) -- both flattened to the identical
        `"CBtagIiE"` before the `[abi:tag]` delimiter fix."""
        tagged = itanium_scope_components("_ZN1CB3tagIiE1fEv")
        plain = itanium_scope_components("_ZN5CBtagIiE1fEv")
        assert tagged is not None
        assert plain is not None
        assert tagged != plain
        assert tagged == ["C[abi:tag]IiE", "f"]
        assert plain == ["CBtagIiE", "f"]


class TestMsvcScopeParser:
    """Structural parser for clang-cl/MSVC-mangled symbols (Codex review,
    fresh evidence: confirmed against real ``clang --target=x86_64-pc-
    windows-msvc -Xclang -ast-dump=json`` output for every case below)."""

    @pytest.mark.parametrize(
        "mangled,expected",
        [
            ("?run@Foo@@QEAAXXZ", ["Foo", "run"]),  # Foo::run()
            ("?freefunc@ns@@YAXXZ", ["ns", "freefunc"]),  # ns::freefunc()
            (
                "?method@Box@inner@outer@@QEAAXXZ",
                ["outer", "inner", "Box", "method"],
            ),  # outer::inner::Box::method()
            ("?instantiate@@YAXXZ", ["instantiate"]),  # global free function
            ("?vf@Base@@UEAAXXZ", ["Base", "vf"]),  # virtual member
            ("?f@A@@QEAAXXZ", ["A", "f"]),  # single-letter class name
            ("?g@A@N@@QEAAXXZ", ["N", "A", "g"]),  # N::A::g()
        ],
    )
    def test_components(self, mangled, expected):
        assert msvc_scope_components(mangled) == expected

    @pytest.mark.parametrize(
        "mangled",
        [
            "foo",  # not MSVC-mangled (no leading ?)
            "??0Box@inner@outer@@QEAA@XZ",  # constructor -- not modelled
            "??_DBox@inner@outer@@QEAAXXZ",  # destructor -- not modelled
            "??4Base@@QEAAAEAU0@AEBU0@@Z",  # operator= -- not modelled
            "?go@?$Wrapper@H@@QEAAXXZ",  # template class -- not modelled
            "??0?$Wrapper@H@@QEAA@XZ",  # template ctor -- not modelled
            "?run@Foo@",  # missing "@@" terminator
            "?@@YAXXZ",  # empty leaf name
        ],
    )
    def test_unmodelled_or_degenerate_does_not_crash(self, mangled):
        result = msvc_scope_components(mangled)
        assert result is None or isinstance(result, list)

    def test_qualified_name(self):
        assert msvc_qualified_name("?run@Foo@@QEAAXXZ") == "Foo::run"
        assert msvc_qualified_name("?instantiate@@YAXXZ") == "instantiate"

    def test_owner_falls_back_to_msvc_mangled(self):
        # clang-cl records a bare AST name (like CastXML) but MSVC mangling
        # (unlike CastXML's Itanium mangling) -- owner_class_of must try both.
        f = Function(
            name="run",
            mangled="?run@Foo@@QEAAXXZ",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        assert owner_class_of(f) == "Foo"

    def test_owner_none_for_unscoped_msvc_free_function(self):
        f = Function(
            name="instantiate",
            mangled="?instantiate@@YAXXZ",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        assert owner_class_of(f) is None

    def test_owner_treats_namespaced_msvc_free_function_scope_as_owner(self):
        # Same pre-existing namespace-vs-class ambiguity documented for the
        # Itanium fallback (AGENTS.md "Known gaps" -- owner_class_of cannot
        # syntactically tell a namespace from a class): a namespaced free
        # function's enclosing scope resolves the same way a method's owning
        # class would, for both mangling schemes alike.
        f = Function(
            name="freefunc",
            mangled="?freefunc@ns@@YAXXZ",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        assert owner_class_of(f) == "ns"

    def test_owner_none_for_msvc_constructor(self):
        f = Function(
            name="Box",
            mangled="??0Box@inner@outer@@QEAA@XZ",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
        assert owner_class_of(f) is None
