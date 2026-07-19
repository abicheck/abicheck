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

"""diff_symbols.py's own old/new RecordType-matching maps used to key purely
by bare ``RecordType.name`` (``{t.name: t for t in old.types}`` and
variants) -- the exact short/leaf-name collision class PR #608 fixed for
diff_types.py, just never propagated here. Two distinct classes sharing a
bare name in different namespaces could get cross-matched across old/new,
fabricating findings for the wrong class (or missing a real one) in
``_diff_access_levels`` (FIELD_ACCESS_CHANGED), ``_diff_anon_fields``
(ANON_FIELD_CHANGED), and ``_diff_ctor_overload_ambiguity``
(CTOR_OVERLOAD_AMBIGUITY_RISK), and could misattribute a new virtual method
to the wrong owner in ``_diff_functions``' vtable/virtual-method-addition
lookups.

These tests reproduce the exact "which namespace does 'Impl' resolve to"
ambiguity for each of those four call sites now that they route through
``diff_helpers.build_type_map``/``lookup_matched_type``.
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, compare
from abicheck.diff_helpers import build_type_map
from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)


def _snap(version="1.0", functions=None, types=None):
    return AbiSnapshot(
        library="libtest.so.1", version=version,
        functions=functions or [], variables=[], types=types or [],
    )


def _kinds(result):
    return {c.kind for c in result.changes}


class TestAccessLevelsAmbiguitySafe:
    def test_field_access_change_not_cross_attributed(self):
        """Two distinct 'Impl' classes in different namespaces; only
        ns2::Impl's field access narrows. Reversed insertion order on the
        new side forces a naive last-write-wins bare-name dict to compare
        the wrong pair.
        """
        ns1_old = RecordType(name="Impl", qualified_name="ns1::Impl", kind="class",
                             fields=[TypeField("a", "int", 0, access=AccessLevel.PUBLIC)])
        ns2_old = RecordType(name="Impl", qualified_name="ns2::Impl", kind="class",
                             fields=[TypeField("b", "int", 0, access=AccessLevel.PUBLIC)])
        ns1_new = RecordType(name="Impl", qualified_name="ns1::Impl", kind="class",
                             fields=[TypeField("a", "int", 0, access=AccessLevel.PUBLIC)])
        ns2_new = RecordType(name="Impl", qualified_name="ns2::Impl", kind="class",
                             fields=[TypeField("b", "int", 0, access=AccessLevel.PRIVATE)])

        r = compare(
            _snap(types=[ns1_old, ns2_old]),
            _snap(types=[ns2_new, ns1_new]),  # reversed order
        )

        access_changes = [c for c in r.changes if c.kind == ChangeKind.FIELD_ACCESS_CHANGED]
        assert len(access_changes) == 1
        assert access_changes[0].description.count("b") >= 1 or "b" in (access_changes[0].detail or "")


class TestAnonFieldsAmbiguitySafe:
    def test_anon_field_change_detected_despite_reversed_insertion_order(self):
        """ns1::Impl is unchanged; ns2::Impl's anonymous field is genuinely
        removed. Reversed insertion order on the new side makes a naive
        last-write-wins bare-name dict compare the WRONG pair (old_map
        picks ns2::Impl, new_map picks ns1::Impl) and silently miss the real
        change entirely -- confirmed by running this exact scenario against
        the pre-fix diff_symbols.py, which reports zero ANON_FIELD_CHANGED
        findings here.
        """
        ns1_old = RecordType(name="Impl", qualified_name="ns1::Impl", kind="struct",
                             fields=[TypeField("__anon0", "union", 0)])
        ns2_old = RecordType(name="Impl", qualified_name="ns2::Impl", kind="struct",
                             fields=[TypeField("__anon0", "union", 0)])
        ns1_new = RecordType(name="Impl", qualified_name="ns1::Impl", kind="struct",
                             fields=[TypeField("__anon0", "union", 0)])
        ns2_new = RecordType(name="Impl", qualified_name="ns2::Impl", kind="struct",
                             fields=[])  # ns2::Impl's anon field genuinely removed

        r = compare(
            _snap(types=[ns1_old, ns2_old]),
            _snap(types=[ns2_new, ns1_new]),
        )

        anon_changes = [c for c in r.changes if c.kind == ChangeKind.ANON_FIELD_CHANGED]
        assert len(anon_changes) == 1


class TestCtorOverloadAmbiguityIsAmbiguitySafe:
    def _ctor(self, mangled, ns, cls, param_type, default=None):
        return Function(
            name=cls, mangled=mangled, return_type="void",
            params=[Param(name="x", type=param_type, default=default)],
            visibility=Visibility.PUBLIC, is_explicit=False,
        )

    def test_second_converting_ctor_not_attributed_to_other_namespace(self):
        """Real Itanium-mangled constructors so owner_class_of resolves the
        true scope: ns1::Impl gains a 2nd converting ctor; ns2::Impl (a
        distinct, unrelated class sharing the bare name 'Impl') is untouched
        and must not be the one flagged.
        """
        ns1 = RecordType(name="Impl", qualified_name="ns1::Impl", kind="class")
        ns2 = RecordType(name="Impl", qualified_name="ns2::Impl", kind="class")

        old_funcs = [
            self._ctor("_ZN3ns14ImplC1Ei", "ns1", "Impl", "int"),
            self._ctor("_ZN3ns24ImplC1Ei", "ns2", "Impl", "int"),
        ]
        new_funcs = [
            self._ctor("_ZN3ns14ImplC1Ei", "ns1", "Impl", "int"),
            self._ctor("_ZN3ns14ImplC1EPKc", "ns1", "Impl", "char const *"),
            self._ctor("_ZN3ns24ImplC1Ei", "ns2", "Impl", "int"),
        ]

        r = compare(
            _snap(functions=old_funcs, types=[ns1, ns2]),
            _snap(functions=new_funcs, types=[ns2, ns1]),  # reversed type order
        )

        risk = [c for c in r.changes if c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK]
        assert len(risk) == 1
        assert "ns1::Impl" in risk[0].description
        assert "ns2" not in risk[0].description

    def test_unrelated_namespace_with_only_one_ctor_each_stays_silent(self):
        """Sanity check: when neither namespace's ctor count actually grows,
        no risk finding fires at all (would catch a cross-attribution that
        fabricates a 1->2 transition from two genuinely-1-ctor classes)."""
        ns1 = RecordType(name="Impl", qualified_name="ns1::Impl", kind="class")
        ns2 = RecordType(name="Impl", qualified_name="ns2::Impl", kind="class")
        old_funcs = [self._ctor("_ZN3ns14ImplC1Ei", "ns1", "Impl", "int")]
        new_funcs = [
            self._ctor("_ZN3ns14ImplC1Ei", "ns1", "Impl", "int"),
            self._ctor("_ZN3ns24ImplC1EPKc", "ns2", "Impl", "char const *"),
        ]

        r = compare(
            _snap(functions=old_funcs, types=[ns1]),
            _snap(functions=new_funcs, types=[ns2, ns1]),
        )

        assert ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK not in _kinds(r)

    def test_legacy_snapshot_missing_qualified_name_still_flags_risk(self):
        """A schema-evolution mix: the old side's RecordType predates
        RecordType.qualified_name (None on a namespaced class), while the
        new side is a fresh, fully-qualified snapshot. owner_class_of is
        derived purely from the constructor's mangled symbol, independent of
        RecordType at all, so it resolves the real 'ns::Widget' on BOTH
        sides regardless of which snapshot's RecordType lacks
        qualified_name -- a raw canonical-key set intersection between the
        two TypeMaps missed this class entirely (Codex review, PR #608
        follow-up second round), silently dropping its constructors.
        """
        old_widget = RecordType(name="Widget", qualified_name=None, kind="class")
        new_widget = RecordType(name="Widget", qualified_name="ns::Widget", kind="class")

        old_funcs = [self._ctor("_ZN2ns6WidgetC1Ei", "ns", "Widget", "int")]
        new_funcs = [
            self._ctor("_ZN2ns6WidgetC1Ei", "ns", "Widget", "int"),
            self._ctor("_ZN2ns6WidgetC1EPKc", "ns", "Widget", "char const *"),
        ]

        r = compare(
            _snap(functions=old_funcs, types=[old_widget]),
            _snap(functions=new_funcs, types=[new_widget]),
        )

        risk = [c for c in r.changes if c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK]
        assert len(risk) == 1
        assert "ns::Widget" in risk[0].description

    def test_legacy_snapshot_on_new_side_still_flags_risk(self):
        """Same schema-evolution mix, reversed: legacy (unqualified) new
        side, fresh (qualified) old side."""
        old_widget = RecordType(name="Widget", qualified_name="ns::Widget", kind="class")
        new_widget = RecordType(name="Widget", qualified_name=None, kind="class")

        old_funcs = [self._ctor("_ZN2ns6WidgetC1Ei", "ns", "Widget", "int")]
        new_funcs = [
            self._ctor("_ZN2ns6WidgetC1Ei", "ns", "Widget", "int"),
            self._ctor("_ZN2ns6WidgetC1EPKc", "ns", "Widget", "char const *"),
        ]

        r = compare(
            _snap(functions=old_funcs, types=[old_widget]),
            _snap(functions=new_funcs, types=[new_widget]),
        )

        risk = [c for c in r.changes if c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK]
        assert len(risk) == 1

    def test_synthetic_ctor_key_still_flags_risk(self):
        """castxml omits a real mangled name for some public overloaded
        constructors and synthesizes a key instead
        (``SYNTHETIC_CTOR_KEY_PREFIX + "scope(params)"``). That key doesn't
        start with the Itanium ``_Z`` prefix, so owner_class_of can't parse
        it and used to fall back to the bare class name -- dropping every
        namespaced synthetic-key constructor from its overload group even on
        two fully fresh (non-legacy) snapshots (Codex review, PR #608
        follow-up, third round).
        """
        widget_old = RecordType(name="Widget", qualified_name="ns::Widget", kind="class")
        widget_new = RecordType(name="Widget", qualified_name="ns::Widget", kind="class")

        def synth_ctor(param_type):
            key = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget({param_type})"
            return Function(
                name="Widget", mangled=key, return_type="void",
                params=[Param(name="x", type=param_type)],
                visibility=Visibility.PUBLIC, is_explicit=False,
            )

        old_funcs = [synth_ctor("int")]
        new_funcs = [synth_ctor("int"), synth_ctor("char const *")]

        r = compare(
            _snap(functions=old_funcs, types=[widget_old]),
            _snap(functions=new_funcs, types=[widget_new]),
        )

        risk = [c for c in r.changes if c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK]
        assert len(risk) == 1


class TestVirtualMethodOwnerResolutionAmbiguitySafe:
    """``_diff_functions`` feeds ``old_types``/``new_types`` into
    ``virtual_method_addition`` -> ``diff_cxx_rules._resolve_owner_type`` to
    resolve a new virtual method's owner class (deciding override-reuse vs.
    a genuinely new vtable slot). Verified directly against
    ``_resolve_owner_type`` (rather than trying to force a specific
    end-to-end ``compare()`` misclassification, which depends on override-
    reuse internals unrelated to this bug): a naive last-write-wins
    bare-name dict resolves the WRONG class whenever a same-leaf-name
    collision's last-inserted entry isn't the one being asked for; the
    ``build_type_map``/``TypeMap`` this file's detectors now use does not.
    """

    def test_naive_bare_name_dict_would_misattribute_owner(self):
        """Sanity/documentation check: proves the OLD bug is real and
        deterministic, not just theoretical -- a plain ``{t.name: t}`` dict
        (what diff_symbols.py built before this fix) resolves 'ns2::Impl'
        to ns1's RecordType whenever ns1 was inserted last.
        """
        ns1 = RecordType(name="Impl", qualified_name="ns1::Impl", kind="class", size_bits=64)
        ns2 = RecordType(name="Impl", qualified_name="ns2::Impl", kind="class", size_bits=32)
        naive = {t.name: t for t in [ns2, ns1]}  # ns1 inserted last -> wins the bare slot

        from abicheck.diff_cxx_rules import _resolve_owner_type
        resolved = _resolve_owner_type(
            "ns2::Impl", naive, known_owners={"ns1::Impl", "ns2::Impl"}
        )
        assert resolved is ns1  # the historical bug: wrong class returned

    def test_type_map_resolves_the_correct_owner_regardless_of_order(self):
        ns1 = RecordType(name="Impl", qualified_name="ns1::Impl", kind="class", size_bits=64)
        ns2 = RecordType(name="Impl", qualified_name="ns2::Impl", kind="class", size_bits=32)

        from abicheck.diff_cxx_rules import _resolve_owner_type
        for ordering in ([ns1, ns2], [ns2, ns1]):
            types = build_type_map(ordering)
            resolved = _resolve_owner_type(
                "ns2::Impl", types, known_owners={"ns1::Impl", "ns2::Impl"}
            )
            assert resolved is ns2, f"failed for insertion order {ordering}"

    def test_diff_functions_builds_type_map_not_naive_dict(self):
        """Regression guard on the actual production call site: ``_diff_functions``
        must build ``old_types``/``new_types`` via ``diff_helpers.build_type_map``
        (or an equivalent ambiguity-safe map), not a raw ``{t.name: t
        for t in ...}`` dict comprehension.
        """
        import inspect

        import abicheck.diff_symbols as diff_symbols_module

        source = inspect.getsource(diff_symbols_module._diff_functions)
        assert "build_type_map(old.types)" in source
        assert "build_type_map(new.types)" in source
        assert "{t.name: t for t in old.types}" not in source
        assert "{t.name: t for t in new.types}" not in source
