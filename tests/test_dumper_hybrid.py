"""Unit tests for G28 Phase 3: dumper_hybrid.merge_snapshots and fact_provenance.

Covers the ctor/dtor synthetic-key reconciliation (the concrete motivating
bug from the G28 plan), per-fact backfill/provenance recording, and the
fact_provenance.py reader-side helpers every migrated detector now uses.
"""

from __future__ import annotations

from unittest.mock import patch

from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX
from abicheck.dumper_hybrid import merge_snapshots
from abicheck.fact_provenance import (
    both_castxml_backed_fact,
    enum_fact_key,
    fact_producer,
    field_fact_key,
    func_fact_key,
    is_castxml_backed_fact,
    type_fact_key,
    var_fact_key,
)
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
)


def _snap(
    functions=None, variables=None, types=None, enums=None, from_headers=True, **kwargs
):
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        enums=enums or [],
        from_headers=from_headers,
        **kwargs,
    )


class TestMergeSnapshotsBasics:
    def test_ast_producer_is_hybrid(self):
        merged = merge_snapshots(
            _snap(ast_producer="castxml"), _snap(ast_producer="clang")
        )
        assert merged.ast_producer == "hybrid"

    def test_no_headers_returns_castxml_snap_unchanged(self):
        # Codex review: neither backend parsed headers (no headers supplied,
        # or dwarf_only/symbols_only) -- must NOT be falsely upgraded to
        # confirmed header-aware "hybrid" provenance, or a header-tier
        # detector (param defaults, constants, param renames) misreads a
        # real header-aware comparison side as having lost data.
        castxml = _snap(ast_producer=None, from_headers=False)
        clang = _snap(ast_producer=None, from_headers=False)
        merged = merge_snapshots(castxml, clang)
        assert merged is castxml
        assert merged.from_headers is False
        assert merged.ast_producer is None

    def test_clang_side_non_header_fallback_returns_castxml_snap_unchanged(self):
        # Codex review: the ORIGINAL guard only checked castxml_snap.
        # from_headers -- if the clang side alone degraded to a non-header
        # fallback (e.g. the PE/Mach-O header-scoped path falling back to
        # export-table mode), the merge still unioned clang_snap's much
        # broader, non-header-scoped declarations into a result falsely
        # marked confirmed header-aware.
        clang_only_func = Function(name="bar", mangled="_Z3barv", return_type="void")
        castxml = _snap(ast_producer="castxml", from_headers=True)
        clang = _snap(
            functions=[clang_only_func], ast_producer=None, from_headers=False
        )
        merged = merge_snapshots(castxml, clang)
        assert merged is castxml
        assert merged.func_by_mangled("_Z3barv") is None
        assert merged.ast_producer == "castxml"

    def test_from_headers_inferred_preserved_when_true(self):
        castxml = _snap(
            ast_producer="castxml", from_headers=True, from_headers_inferred=True
        )
        clang = _snap(ast_producer="clang", from_headers=True)
        merged = merge_snapshots(castxml, clang)
        # from_headers=True here, so the merge proceeds; from_headers_inferred
        # must come through from castxml_snap unchanged, not be forced False.
        assert merged.from_headers_inferred is True

    def test_layout_facts_come_from_castxml_unchanged(self):
        t = RecordType(name="Foo", kind="struct", size_bits=64, alignment_bits=32)
        castxml = _snap(types=[t], ast_producer="castxml")
        clang = _snap(ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert merged.type_by_name("Foo").size_bits == 64
        assert merged.type_by_name("Foo").alignment_bits == 32

    def test_index_rebuilds_after_merge(self):
        f = Function(name="foo", mangled="_Z3foov", return_type="void")
        castxml = _snap(functions=[f], ast_producer="castxml")
        clang = _snap(ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        # A fresh lookup must reflect the merged functions list, not a stale
        # cached index carried over from the castxml snapshot via replace().
        assert merged.func_by_mangled("_Z3foov") is not None

    def test_clang_only_function_is_appended(self):
        clang_only = Function(name="bar", mangled="_Z3barv", return_type="void")
        castxml = _snap(ast_producer="castxml")
        clang = _snap(functions=[clang_only], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert merged.func_by_mangled("_Z3barv") is not None
        # No castxml confirmation exists for a clang-only entity.
        key = func_fact_key("_Z3barv", "deprecated")
        assert not is_castxml_backed_fact(merged, key)


class TestFunctionFactBackfill:
    def test_castxml_value_wins_and_is_marked_castxml(self):
        old_f = Function(
            name="foo", mangled="_Z3foov", return_type="void", deprecated="msg"
        )
        castxml = _snap(functions=[old_f], ast_producer="castxml")
        clang = _snap(ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        f = merged.func_by_mangled("_Z3foov")
        assert f.deprecated == "msg"
        assert is_castxml_backed_fact(merged, func_fact_key("_Z3foov", "deprecated"))

    def test_backfill_from_clang_when_castxml_is_none(self):
        # Forward-looking: a no-op today (dumper_clang doesn't populate
        # deprecated), exercised here via hand-built snapshots since no real
        # clang dump would produce this yet.
        old_f = Function(
            name="foo", mangled="_Z3foov", return_type="void", deprecated=None
        )
        clang_f = Function(
            name="foo", mangled="_Z3foov", return_type="void", deprecated="msg"
        )
        castxml = _snap(functions=[old_f], ast_producer="castxml")
        clang = _snap(functions=[clang_f], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        f = merged.func_by_mangled("_Z3foov")
        assert f.deprecated == "msg"
        key = func_fact_key("_Z3foov", "deprecated")
        assert merged.fact_provenance[key] == "clang"

    def test_no_clang_counterpart_still_marked_castxml(self):
        old_f = Function(
            name="foo", mangled="_Z3foov", return_type="void", deprecated=None
        )
        castxml = _snap(functions=[old_f], ast_producer="castxml")
        clang = _snap(ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert is_castxml_backed_fact(merged, func_fact_key("_Z3foov", "deprecated"))

    def test_is_override_backfill_independent_of_deprecated(self):
        old_f = Function(
            name="foo", mangled="_Z3foov", return_type="void", is_override=True
        )
        castxml = _snap(functions=[old_f], ast_producer="castxml")
        clang = _snap(ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        f = merged.func_by_mangled("_Z3foov")
        assert f.is_override is True
        assert is_castxml_backed_fact(merged, func_fact_key("_Z3foov", "is_override"))


class TestCtorDtorReconciliation:
    """The concrete motivating bug: a castxml synthetic ctor/dtor key has no
    shared identity with the same entity's real clang-mangled key."""

    def test_template_class_constructor_scope_normalized_across_producers(self):
        # Codex review: castxml spells a template's scope in SOURCE form
        # ("ns::Widget<int>"), while itanium_scope_components (real clang
        # mangled name) spells the identical class "ns::WidgetIiE" (the raw
        # Itanium <template-args> encoding) -- an exact scope-string
        # comparison never matched ANY templated class's ctor, even for
        # unchanged source.
        synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget<int>(int)"
        castxml_ctor = Function(
            name="Widget",
            mangled=synthetic,
            return_type="void",
            params=[Param(name="n", type="int")],
            access=AccessLevel.PUBLIC,
        )
        real_mangled = "_ZN2ns6WidgetIiEC1Ei"
        clang_ctor = Function(
            name="Widget",
            mangled=real_mangled,
            return_type="void",
            params=[Param(name="n", type="int")],
            access=AccessLevel.PUBLIC,
        )
        castxml = _snap(functions=[castxml_ctor], ast_producer="castxml")
        clang = _snap(functions=[clang_ctor], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)

        assert merged.func_by_mangled(synthetic) is None
        assert merged.func_by_mangled(real_mangled) is not None

    def test_template_class_base_name_containing_uppercase_i_still_normalized(self):
        # Codex review: a base name that itself contains an uppercase "I"
        # (e.g. "Image") has its OWN "I" appear before the real
        # template-argument-opening one in the Itanium component
        # ("ImageIiE") -- the naive first-"I" search tried to skip template
        # args starting at the wrong "I" and never reached the end of the
        # string, so the component came back UNCHANGED ("ImageIiE") instead
        # of stripped to "Image", permanently mismatching against castxml's
        # own "ns::Image<int>" -> "Image" normalization.
        synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Image<int>(int)"
        castxml_ctor = Function(
            name="Image", mangled=synthetic, return_type="void",
            params=[Param(name="n", type="int")], access=AccessLevel.PUBLIC,
        )
        real_mangled = "_ZN2ns5ImageIiEC1Ei"
        clang_ctor = Function(
            name="Image", mangled=real_mangled, return_type="void",
            params=[Param(name="n", type="int")], access=AccessLevel.PUBLIC,
        )
        castxml = _snap(functions=[castxml_ctor], ast_producer="castxml")
        clang = _snap(functions=[clang_ctor], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)

        assert merged.func_by_mangled(synthetic) is None
        assert merged.func_by_mangled(real_mangled) is not None

    def test_template_class_destructor_scope_normalized_across_producers(self):
        synthetic = "~ns::Widget<int>"
        castxml_dtor = Function(
            name="~Widget", mangled=synthetic, return_type="void",
            is_virtual=True, access=AccessLevel.PUBLIC,
        )
        real_mangled = "_ZN2ns6WidgetIiED1Ev"
        clang_dtor = Function(
            name="~Widget", mangled=real_mangled, return_type="void",
            is_virtual=True, access=AccessLevel.PUBLIC,
        )
        castxml = _snap(functions=[castxml_dtor], ast_producer="castxml")
        clang = _snap(functions=[clang_dtor], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)

        assert merged.func_by_mangled(synthetic) is None
        assert merged.func_by_mangled(real_mangled) is not None

    def test_different_template_instantiations_disambiguated_by_param_type(self):
        # Two distinct instantiations (Widget<int>, Widget<double>) share the
        # SAME normalized scope ("ns::Widget") once template args are
        # stripped -- their own (type-dependent) constructor parameter must
        # still tell them apart, not a false match to the wrong one.
        int_synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget<int>(int)"
        int_castxml = Function(
            name="Widget", mangled=int_synthetic, return_type="void",
            params=[Param(name="n", type="int")], access=AccessLevel.PUBLIC,
        )
        int_real = "_ZN2ns6WidgetIiEC1Ei"
        int_clang = Function(
            name="Widget", mangled=int_real, return_type="void",
            params=[Param(name="n", type="int")], access=AccessLevel.PUBLIC,
        )
        double_synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget<double>(double)"
        double_castxml = Function(
            name="Widget", mangled=double_synthetic, return_type="void",
            params=[Param(name="n", type="double")], access=AccessLevel.PUBLIC,
        )
        double_real = "_ZN2ns6WidgetIdEC1Ed"
        double_clang = Function(
            name="Widget", mangled=double_real, return_type="void",
            params=[Param(name="n", type="double")], access=AccessLevel.PUBLIC,
        )
        castxml = _snap(
            functions=[int_castxml, double_castxml], ast_producer="castxml"
        )
        clang = _snap(functions=[int_clang, double_clang], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)

        assert merged.func_by_mangled(int_real) is not None
        assert merged.func_by_mangled(double_real) is not None
        assert merged.func_by_mangled(int_synthetic) is None
        assert merged.func_by_mangled(double_synthetic) is None

    def test_multiple_instantiations_default_ctor_stays_safely_unreconciled(self):
        # Known residual limitation (Codex review): once the scope key is
        # template-argument-free, two distinct instantiations' DEFAULT
        # (no-parameter) constructors collide under the identical
        # (marker, scope) key with no parameter signature left to tell them
        # apart. The matcher must stay safe (no match, not a WRONG match) —
        # both synthetic keys survive unreconciled rather than one being
        # matched to the wrong instantiation's real mangled name.
        int_synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget<int>()"
        int_castxml = Function(
            name="Widget", mangled=int_synthetic, return_type="void",
            access=AccessLevel.PUBLIC,
        )
        int_real = "_ZN2ns6WidgetIiEC1Ev"
        int_clang = Function(
            name="Widget", mangled=int_real, return_type="void",
            access=AccessLevel.PUBLIC,
        )
        double_synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget<double>()"
        double_castxml = Function(
            name="Widget", mangled=double_synthetic, return_type="void",
            access=AccessLevel.PUBLIC,
        )
        double_real = "_ZN2ns6WidgetIdEC1Ev"
        double_clang = Function(
            name="Widget", mangled=double_real, return_type="void",
            access=AccessLevel.PUBLIC,
        )
        castxml = _snap(
            functions=[int_castxml, double_castxml], ast_producer="castxml"
        )
        clang = _snap(functions=[int_clang, double_clang], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)

        # Neither synthetic key got (wrongly) rewritten to either real
        # mangled name -- both sets of functions coexist unreconciled.
        assert merged.func_by_mangled(int_synthetic) is not None
        assert merged.func_by_mangled(double_synthetic) is not None
        assert merged.func_by_mangled(int_real) is not None
        assert merged.func_by_mangled(double_real) is not None

    def test_constructor_synthetic_key_reconciled_to_real_mangled_name(self):
        synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget(int)"
        castxml_ctor = Function(
            name="Widget",
            mangled=synthetic,
            return_type="void",
            params=[Param(name="n", type="int")],
            access=AccessLevel.PUBLIC,
        )
        real_mangled = "_ZN2ns6WidgetC1Ei"
        clang_ctor = Function(
            name="Widget",
            mangled=real_mangled,
            return_type="void",
            params=[Param(name="n", type="int")],
            access=AccessLevel.PUBLIC,
        )
        castxml = _snap(functions=[castxml_ctor], ast_producer="castxml")
        clang = _snap(functions=[clang_ctor], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)

        assert merged.func_by_mangled(synthetic) is None
        reconciled = merged.func_by_mangled(real_mangled)
        assert reconciled is not None
        assert reconciled.name == "Widget"

    def test_constructor_with_comma_in_single_param_type_still_matches(self):
        # Codex review: the synthetic key's embedded param signature is a
        # bare "," join with no escaping. A single parameter whose OWN type
        # contains a comma (a multi-argument template) must not be split
        # into two -- that would understate the ctor's arity and block
        # reconciliation forever, keeping the synthetic key around and
        # reintroducing the false FUNC_REMOVED/FUNC_ADDED pair.
        synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget(Box<int, int>)"
        castxml_ctor = Function(
            name="Widget",
            mangled=synthetic,
            return_type="void",
            params=[Param(name="b", type="Box<int, int>")],
            access=AccessLevel.PUBLIC,
        )
        real_mangled = "_ZN2ns6WidgetC1E3BoxIiiE"
        clang_ctor = Function(
            name="Widget",
            mangled=real_mangled,
            return_type="void",
            params=[Param(name="b", type="Box<int, int>")],
            access=AccessLevel.PUBLIC,
        )
        castxml = _snap(functions=[castxml_ctor], ast_producer="castxml")
        clang = _snap(functions=[clang_ctor], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)

        assert merged.func_by_mangled(synthetic) is None
        assert merged.func_by_mangled(real_mangled) is not None

    def test_constructor_with_two_comma_bearing_params_still_matches(self):
        # Two distinct parameters, each itself comma-bearing -- makes sure
        # the fix splits exactly at the two top-level commas, not more.
        synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget(Box<int, int>,Pair<int, int>)"
        params = [
            Param(name="a", type="Box<int, int>"),
            Param(name="b", type="Pair<int, int>"),
        ]
        castxml_ctor = Function(
            name="Widget", mangled=synthetic, return_type="void",
            params=params, access=AccessLevel.PUBLIC,
        )
        real_mangled = "_ZN2ns6WidgetC1E3BoxIiiE4PairIiiE"
        clang_ctor = Function(
            name="Widget", mangled=real_mangled, return_type="void",
            params=params, access=AccessLevel.PUBLIC,
        )
        castxml = _snap(functions=[castxml_ctor], ast_producer="castxml")
        clang = _snap(functions=[clang_ctor], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)

        assert merged.func_by_mangled(synthetic) is None
        assert merged.func_by_mangled(real_mangled) is not None

    def test_destructor_synthetic_key_reconciled_to_real_mangled_name(self):
        synthetic = "~ns::Base1"
        castxml_dtor = Function(
            name="~Base1",
            mangled=synthetic,
            return_type="void",
            is_virtual=True,
            access=AccessLevel.PUBLIC,
        )
        real_mangled = "_ZN2ns5Base1D1Ev"
        clang_dtor = Function(
            name="~Base1",
            mangled=real_mangled,
            return_type="void",
            is_virtual=True,
            access=AccessLevel.PUBLIC,
        )
        castxml = _snap(functions=[castxml_dtor], ast_producer="castxml")
        clang = _snap(functions=[clang_dtor], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)

        assert merged.func_by_mangled(synthetic) is None
        assert merged.func_by_mangled(real_mangled) is not None

    def test_constructor_no_match_when_signature_differs(self):
        # Same class, but the clang candidate takes a different parameter —
        # a genuinely different overload must NOT be matched.
        synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget(int)"
        castxml_ctor = Function(
            name="Widget",
            mangled=synthetic,
            return_type="void",
            params=[Param(name="n", type="int")],
        )
        clang_other_overload = Function(
            name="Widget",
            mangled="_ZN2ns6WidgetC1Ed",
            return_type="void",
            params=[Param(name="d", type="double")],
        )  # Widget(double), not Widget(int)
        castxml = _snap(functions=[castxml_ctor], ast_producer="castxml")
        clang = _snap(functions=[clang_other_overload], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)

        # Falls back to today's (buggy-but-safe) behavior: synthetic key kept.
        assert merged.func_by_mangled(synthetic) is not None
        assert merged.func_by_mangled("_ZN2ns6WidgetC1Ed") is not None

    def test_constructor_no_match_when_scope_differs(self):
        synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget(int)"
        castxml_ctor = Function(name="Widget", mangled=synthetic, return_type="void")
        unrelated_class_ctor = Function(
            name="Widget", mangled="_ZN3ns26WidgetC1Ei", return_type="void"
        )
        castxml = _snap(functions=[castxml_ctor], ast_producer="castxml")
        clang = _snap(functions=[unrelated_class_ctor], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert merged.func_by_mangled(synthetic) is not None

    def test_destructor_ambiguous_when_two_candidates(self):
        # Two dtor-shaped candidates under the identical (marker, scope) key
        # must not be guessed between — this shouldn't happen for a real
        # class (at most one dtor), but the matcher must stay safe if it did.
        synthetic = "~ns::Base1"
        castxml_dtor = Function(name="~Base1", mangled=synthetic, return_type="void")
        cand1 = Function(name="~Base1", mangled="_ZN2ns5Base1D1Ev", return_type="void")
        cand2 = Function(name="~Base1", mangled="_ZN2ns5Base1D2Ev", return_type="void")
        castxml = _snap(functions=[castxml_dtor], ast_producer="castxml")
        clang = _snap(functions=[cand1, cand2], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert merged.func_by_mangled(synthetic) is not None

    def test_ordinary_mangled_function_is_untouched(self):
        f = Function(name="foo", mangled="_Z3foov", return_type="void")
        castxml = _snap(functions=[f], ast_producer="castxml")
        clang = _snap(ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert merged.func_by_mangled("_Z3foov") is not None

    def test_nested_class_inside_template_scope_normalized(self):
        # Codex review: a last-component-only normalization leaves an
        # ENCLOSING template argument untouched — castxml spells the nested
        # class's scope "ns::Outer<int>::Inner" (only "Inner" has no
        # template args), while the real mangled scope encodes the template
        # arg on the ENCLOSING component: "ns::OuterIiE::Inner". Every
        # component must be normalized, not just the innermost one.
        synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Outer<int>::Inner()"
        castxml_ctor = Function(
            name="Inner", mangled=synthetic, return_type="void",
            access=AccessLevel.PUBLIC,
        )
        real_mangled = "_ZN2ns5OuterIiE5InnerC1Ev"
        clang_ctor = Function(
            name="Inner", mangled=real_mangled, return_type="void",
            access=AccessLevel.PUBLIC,
        )
        castxml = _snap(functions=[castxml_ctor], ast_producer="castxml")
        clang = _snap(functions=[clang_ctor], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)

        assert merged.func_by_mangled(synthetic) is None
        assert merged.func_by_mangled(real_mangled) is not None


class TestVariableFactBackfill:
    def test_deprecated_backfill_and_provenance(self):
        old_v = Variable(name="g", mangled="g", type="int", deprecated=None)
        clang_v = Variable(name="g", mangled="g", type="int", deprecated="msg")
        castxml = _snap(variables=[old_v], ast_producer="castxml")
        clang = _snap(variables=[clang_v], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        v = merged.var_by_mangled("g")
        assert v.deprecated == "msg"
        assert merged.fact_provenance[var_fact_key("g", "deprecated")] == "clang"


class TestMachoMangledNormalization:
    """Codex review: on Mach-O, clang's ``mangledName`` carries the extra
    Darwin linker-symbol leading underscore (``__ZN...``) while castxml's own
    ``mangled`` is prefix-free (``_ZN...``) for the SAME function -- without
    normalizing this before the merge, EVERY Mach-O C++ function/variable
    would mismatch and get treated as clang-only, duplicating the entire
    function/variable list."""

    def test_function_not_duplicated_when_mangled_differs_by_darwin_underscore(self):
        castxml_f = Function(
            name="foo", mangled="_ZN2ns3fooEv", return_type="void",
            access=AccessLevel.PUBLIC,
        )
        clang_f = Function(
            name="foo", mangled="__ZN2ns3fooEv", return_type="void",
            access=AccessLevel.PUBLIC,
        )
        castxml = _snap(
            functions=[castxml_f], ast_producer="castxml", platform="macho"
        )
        clang = _snap(functions=[clang_f], ast_producer="clang", platform="macho")
        merged = merge_snapshots(castxml, clang)

        assert len(merged.functions) == 1
        assert merged.func_by_mangled("_ZN2ns3fooEv") is not None
        assert merged.func_by_mangled("__ZN2ns3fooEv") is None

    def test_plain_c_function_not_duplicated_when_mangled_differs_by_underscore(self):
        castxml_f = Function(name="foo", mangled="foo", return_type="void")
        clang_f = Function(name="foo", mangled="_foo", return_type="void")
        castxml = _snap(
            functions=[castxml_f], ast_producer="castxml", platform="macho"
        )
        clang = _snap(functions=[clang_f], ast_producer="clang", platform="macho")
        merged = merge_snapshots(castxml, clang)

        assert len(merged.functions) == 1
        assert merged.func_by_mangled("foo") is not None

    def test_variable_not_duplicated_when_mangled_differs_by_darwin_underscore(self):
        castxml_v = Variable(name="g", mangled="_ZN2ns1gE", type="int")
        clang_v = Variable(name="g", mangled="__ZN2ns1gE", type="int")
        castxml = _snap(
            variables=[castxml_v], ast_producer="castxml", platform="macho"
        )
        clang = _snap(variables=[clang_v], ast_producer="clang", platform="macho")
        merged = merge_snapshots(castxml, clang)

        assert len(merged.variables) == 1
        assert merged.var_by_mangled("_ZN2ns1gE") is not None
        assert merged.var_by_mangled("__ZN2ns1gE") is None

    def test_ctor_reconciled_across_darwin_underscore(self):
        synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget(int)"
        castxml_ctor = Function(
            name="Widget", mangled=synthetic, return_type="void",
            params=[Param(name="n", type="int")], access=AccessLevel.PUBLIC,
        )
        real_mangled = "__ZN2ns6WidgetC1Ei"
        clang_ctor = Function(
            name="Widget", mangled=real_mangled, return_type="void",
            params=[Param(name="n", type="int")], access=AccessLevel.PUBLIC,
        )
        castxml = _snap(
            functions=[castxml_ctor], ast_producer="castxml", platform="macho"
        )
        clang = _snap(functions=[clang_ctor], ast_producer="clang", platform="macho")
        merged = merge_snapshots(castxml, clang)

        assert merged.func_by_mangled(synthetic) is None
        assert merged.func_by_mangled("_ZN2ns6WidgetC1Ei") is not None

    def test_elf_functions_untouched_by_macho_normalization(self):
        # Sanity: the normalization must be platform-gated -- an ELF/PE
        # mangled name that happens to start with "_" (any Itanium name)
        # must NOT be stripped.
        castxml_f = Function(name="foo", mangled="_Z3foov", return_type="void")
        clang_f = Function(name="foo", mangled="_Z3foov", return_type="void")
        castxml = _snap(functions=[castxml_f], ast_producer="castxml", platform="elf")
        clang = _snap(functions=[clang_f], ast_producer="clang", platform="elf")
        merged = merge_snapshots(castxml, clang)

        assert len(merged.functions) == 1
        assert merged.func_by_mangled("_Z3foov") is not None


class TestParamDefaultsProvenance:
    """Codex review: every merged function needs a "param_defaults" producer
    tag so ``_diff_param_defaults`` can require the SAME producer on both
    sides of a pair — both backends populate ``Param.default`` now, but
    their value representations (castxml: real source expression; clang:
    structural fingerprint/placeholder) aren't cross-comparable, while a
    same-producer pair (e.g. clang-only on both sides) is safe to compare
    exactly like a plain ``--ast-frontend clang`` run already does."""

    def test_castxml_sourced_function_tagged_castxml(self):
        f = Function(name="foo", mangled="_Z3fooi", return_type="void")
        castxml = _snap(functions=[f], ast_producer="castxml")
        clang = _snap(ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert (
            merged.fact_provenance[func_fact_key("_Z3fooi", "param_defaults")]
            == "castxml"
        )

    def test_clang_only_function_tagged_clang(self):
        cf = Function(name="bar", mangled="_Z3bari", return_type="void")
        castxml = _snap(ast_producer="castxml")
        clang = _snap(functions=[cf], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert (
            merged.fact_provenance[func_fact_key("_Z3bari", "param_defaults")]
            == "clang"
        )
        assert not both_castxml_backed_fact(
            merged, merged, func_fact_key("_Z3bari", "param_defaults")
        )

    def test_ctor_dtor_reconciled_function_still_tagged_castxml(self):
        # The declaration is castxml's even though its key got rewritten to
        # the real clang mangled name during ctor/dtor reconciliation.
        synthetic = f"{SYNTHETIC_CTOR_KEY_PREFIX}ns::Widget(int)"
        castxml_ctor = Function(
            name="Widget", mangled=synthetic, return_type="void",
            params=[Param(name="n", type="int", default="5")],
            access=AccessLevel.PUBLIC,
        )
        real_mangled = "_ZN2ns6WidgetC1Ei"
        clang_ctor = Function(
            name="Widget", mangled=real_mangled, return_type="void",
            params=[Param(name="n", type="int")], access=AccessLevel.PUBLIC,
        )
        castxml = _snap(functions=[castxml_ctor], ast_producer="castxml")
        clang = _snap(functions=[clang_ctor], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert (
            merged.fact_provenance[func_fact_key(real_mangled, "param_defaults")]
            == "castxml"
        )


class TestTypeAndFieldFactBackfill:
    def test_type_is_abstract_and_deprecated_from_castxml(self):
        t = RecordType(
            name="Shape", kind="class", size_bits=64, is_abstract=True, deprecated="msg"
        )
        castxml = _snap(types=[t], ast_producer="castxml")
        clang = _snap(ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        merged_t = merged.type_by_name("Shape")
        assert merged_t.is_abstract is True
        assert merged_t.deprecated == "msg"
        assert is_castxml_backed_fact(merged, type_fact_key("Shape", "is_abstract"))
        assert is_castxml_backed_fact(merged, type_fact_key("Shape", "deprecated"))

    def test_field_default_and_deprecated_backfill(self):
        old_field = TypeField(
            name="x", type="int", offset_bits=0, default=None, deprecated=None
        )
        clang_field = TypeField(
            name="x", type="int", offset_bits=0, default="1", deprecated="msg"
        )
        t_old = RecordType(name="Cfg", kind="struct", size_bits=32, fields=[old_field])
        t_clang = RecordType(
            name="Cfg", kind="struct", size_bits=32, fields=[clang_field]
        )
        castxml = _snap(types=[t_old], ast_producer="castxml")
        clang = _snap(types=[t_clang], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        merged_field = merged.type_by_name("Cfg").fields[0]
        assert merged_field.default == "1"
        assert merged_field.deprecated == "msg"
        assert merged.fact_provenance[field_fact_key("Cfg", "x", "default")] == "clang"
        assert (
            merged.fact_provenance[field_fact_key("Cfg", "x", "deprecated")] == "clang"
        )

    def test_unmatched_field_untouched(self):
        f = TypeField(name="x", type="int", offset_bits=0)
        t = RecordType(name="Cfg", kind="struct", size_bits=32, fields=[f])
        castxml = _snap(types=[t], ast_producer="castxml")
        clang = _snap(ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert merged.type_by_name("Cfg").fields[0].name == "x"

    def test_layout_scalar_fields_backfilled_from_enriched_clang(self):
        # Codex review: castxml never populates data_size_bits/
        # is_standard_layout/is_trivially_copyable at all -- when the
        # optional G28 Phase 4 layout tool already enriched clang_snap
        # before this merge, a type present on BOTH backends (the common
        # case) must still pick those facts up, not just a clang-only type.
        t_old = RecordType(
            name="Widget", kind="class", size_bits=64,
            data_size_bits=None, is_standard_layout=None,
            is_trivially_copyable=None, vptr_offset_bits=None,
        )
        t_clang = RecordType(
            name="Widget", kind="class", size_bits=64,
            data_size_bits=48, is_standard_layout=True,
            is_trivially_copyable=False, vptr_offset_bits=0,
        )
        castxml = _snap(types=[t_old], ast_producer="castxml")
        clang = _snap(types=[t_clang], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        merged_t = merged.type_by_name("Widget")
        assert merged_t.data_size_bits == 48
        assert merged_t.is_standard_layout is True
        assert merged_t.is_trivially_copyable is False
        assert merged_t.vptr_offset_bits == 0

    def test_layout_scalar_fields_never_override_castxml(self):
        # castxml's own real layout, when present, always wins.
        t_old = RecordType(
            name="Widget", kind="class", size_bits=64, data_size_bits=64,
        )
        t_clang = RecordType(
            name="Widget", kind="class", size_bits=64, data_size_bits=999,
        )
        castxml = _snap(types=[t_old], ast_producer="castxml")
        clang = _snap(types=[t_clang], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert merged.type_by_name("Widget").data_size_bits == 64

    def test_base_offsets_backfilled_when_castxml_empty(self):
        t_old = RecordType(
            name="Derived", kind="class", bases=["Base"], base_offsets={},
        )
        t_clang = RecordType(
            name="Derived", kind="class", bases=["Base"],
            base_offsets={"Base": 64},
        )
        castxml = _snap(types=[t_old], ast_producer="castxml")
        clang = _snap(types=[t_clang], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert merged.type_by_name("Derived").base_offsets == {"Base": 64}

    def test_field_offset_bits_backfilled_from_enriched_clang(self):
        f_old = TypeField(name="a", type="int", offset_bits=None)
        f_clang = TypeField(name="a", type="int", offset_bits=32)
        t_old = RecordType(name="Foo", kind="struct", fields=[f_old])
        t_clang = RecordType(name="Foo", kind="struct", fields=[f_clang])
        castxml = _snap(types=[t_old], ast_producer="castxml")
        clang = _snap(types=[t_clang], ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        assert merged.type_by_name("Foo").fields[0].offset_bits == 32


class TestEnumFactBackfill:
    def test_is_scoped_and_deprecated_from_castxml(self):
        e = EnumType(name="Color", is_scoped=True, deprecated="msg")
        castxml = _snap(enums=[e], ast_producer="castxml")
        clang = _snap(ast_producer="clang")
        merged = merge_snapshots(castxml, clang)
        merged_e = next(x for x in merged.enums if x.name == "Color")
        assert merged_e.is_scoped is True
        assert merged_e.deprecated == "msg"
        assert is_castxml_backed_fact(merged, enum_fact_key("Color", "is_scoped"))


class TestFactProvenanceHelpers:
    def test_castxml_producer_is_always_backed(self):
        snap = _snap(ast_producer="castxml")
        assert is_castxml_backed_fact(snap, "anything:not:recorded")

    def test_clang_producer_is_never_backed(self):
        snap = _snap(ast_producer="clang")
        assert not is_castxml_backed_fact(snap, "anything:not:recorded")

    def test_none_producer_is_never_backed(self):
        snap = _snap(ast_producer=None)
        assert not is_castxml_backed_fact(snap, "anything:not:recorded")

    def test_not_header_aware_is_never_backed(self):
        snap = _snap(ast_producer="castxml", from_headers=False)
        assert not is_castxml_backed_fact(snap, "anything:not:recorded")

    def test_inferred_header_awareness_is_never_backed(self):
        snap = _snap(ast_producer="castxml", from_headers_inferred=True)
        assert not is_castxml_backed_fact(snap, "anything:not:recorded")

    def test_hybrid_producer_checks_provenance_map(self):
        key = func_fact_key("_Z3foov", "deprecated")
        backed = _snap(ast_producer="hybrid", fact_provenance={key: "castxml"})
        unbacked = _snap(ast_producer="hybrid", fact_provenance={})
        clang_backed = _snap(ast_producer="hybrid", fact_provenance={key: "clang"})
        assert is_castxml_backed_fact(backed, key)
        assert not is_castxml_backed_fact(unbacked, key)
        assert not is_castxml_backed_fact(clang_backed, key)

    def test_both_castxml_backed_fact_requires_both_sides(self):
        key = func_fact_key("_Z3foov", "deprecated")
        old = _snap(ast_producer="castxml")
        new_backed = _snap(ast_producer="hybrid", fact_provenance={key: "castxml"})
        new_unbacked = _snap(ast_producer="hybrid", fact_provenance={})
        assert both_castxml_backed_fact(old, new_backed, key)
        assert not both_castxml_backed_fact(old, new_unbacked, key)

    def test_fact_producer_single_backend_snapshots(self):
        key = func_fact_key("_Z3foov", "param_defaults")
        assert fact_producer(_snap(ast_producer="castxml"), key) == "castxml"
        assert fact_producer(_snap(ast_producer="clang"), key) == "clang"
        assert fact_producer(_snap(ast_producer=None), key) is None
        assert fact_producer(_snap(ast_producer="castxml", from_headers=False), key) is None

    def test_fact_producer_hybrid_reads_provenance_map(self):
        key = func_fact_key("_Z3foov", "param_defaults")
        castxml_side = _snap(ast_producer="hybrid", fact_provenance={key: "castxml"})
        clang_side = _snap(ast_producer="hybrid", fact_provenance={key: "clang"})
        unrecorded = _snap(ast_producer="hybrid", fact_provenance={})
        assert fact_producer(castxml_side, key) == "castxml"
        assert fact_producer(clang_side, key) == "clang"
        assert fact_producer(unrecorded, key) is None


class TestDumpHybridDispatch:
    """Codex review: `abicheck dump -H ... --ast-frontend hybrid` on an ELF
    binary reaches ``dumper.dump()`` directly (``cli_dump_helpers.
    perform_elf_dump`` imports and calls it, bypassing ``service.run_dump``
    entirely) -- so ``dump()`` itself must resolve "hybrid" rather than
    falling through to ``_header_ast_parser``'s single-backend guard.
    """

    def test_dump_hybrid_delegates_to_run_hybrid_dump(self, tmp_path):
        from abicheck.dumper import dump

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        sentinel = AbiSnapshot(library="test", version="1.0", ast_producer="hybrid")
        calls = []

        def fake_run_hybrid_dump(dump_fn, so_path, headers, **kwargs):
            calls.append((dump_fn, so_path, headers))
            return sentinel

        with patch(
            "abicheck.dumper_hybrid.run_hybrid_dump", side_effect=fake_run_hybrid_dump
        ):
            result = dump(p, [], header_backend="hybrid")

        assert result is sentinel
        assert len(calls) == 1
        assert calls[0][0] is dump
        assert calls[0][1] == p

    def test_dump_hybrid_case_insensitive(self, tmp_path):
        from abicheck.dumper import dump

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        sentinel = AbiSnapshot(library="test", version="1.0", ast_producer="hybrid")

        with patch(
            "abicheck.dumper_hybrid.run_hybrid_dump", return_value=sentinel
        ) as mock_run:
            result = dump(p, [], header_backend="HYBRID")

        assert result is sentinel
        assert mock_run.call_count == 1
