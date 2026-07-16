# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Unit tests for the template / overload-set pattern detectors.

Synthetic ``AbiSnapshot`` fixtures only — no compiler, no castxml.
"""
from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.diff_templates import (
    _count_top_level_template_args,
    _return_is_unspecified,
    _strip_template_args,
    detect_cpo_kind_changed,
    detect_internal_template_leaks,
    detect_mandatory_template_param_added,
    detect_overload_set_rerouted,
    detect_template_patterns,
    detect_unspecified_return_now_named,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    Variable,
    Visibility,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _snap(funcs=None, vars_=None, types=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libt.so",
        version="0",
        functions=list(funcs or []),
        variables=list(vars_ or []),
        types=list(types or []),
    )


def _fn(name: str, mangled: str | None = None,
        return_type: str = "void",
        params: list[tuple[str, str]] | None = None,
        visibility: Visibility = Visibility.PUBLIC) -> Function:
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{name}",
        return_type=return_type,
        params=[Param(name=n, type=t) for n, t in (params or [])],
        visibility=visibility,
    )


def _var(name: str, type_: str = "int",
         visibility: Visibility = Visibility.PUBLIC,
         mangled: str | None = None) -> Variable:
    return Variable(name=name, mangled=mangled if mangled is not None else f"_Z{name}",
                    type=type_, visibility=visibility)


def _rec(name: str) -> RecordType:
    return RecordType(name=name, kind="class")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestStripTemplateArgs:
    @pytest.mark.parametrize("name, expected", [
        ("Foo<int>", "Foo"),
        ("ns::Foo<int, char>", "ns::Foo"),
        ("ns::Foo<bar::baz<int>>", "ns::Foo"),
        ("plain", "plain"),
        ("", ""),
    ])
    def test_strips(self, name: str, expected: str) -> None:
        assert _strip_template_args(name) == expected


class TestCountTopLevelTemplateArgs:
    @pytest.mark.parametrize("name, expected", [
        ("Foo<int>", 1),
        ("Foo<int, char>", 2),
        ("Foo<int, std::pair<int, char>>", 2),
        ("Foo", None),
        ("", None),
    ])
    def test_count(self, name: str, expected: int | None) -> None:
        assert _count_top_level_template_args(name) == expected


class TestReturnIsUnspecified:
    @pytest.mark.parametrize("rt, expected", [
        ("auto", True),
        ("decltype(auto)", True),
        ("(anonymous namespace)::T", True),
        ("ns::Named", False),
        ("int", False),
        ("", False),
    ])
    def test_classification(self, rt: str, expected: bool) -> None:
        assert _return_is_unspecified(rt) is expected


# ---------------------------------------------------------------------------
# INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API
# ---------------------------------------------------------------------------


class TestInternalTemplateLeaks:
    def test_changed_instantiation_set_fires(self) -> None:
        old = _snap(funcs=[
            _fn("lib::__detail::walk<int>"),
            _fn("lib::__detail::walk<char>"),
        ])
        new = _snap(funcs=[
            _fn("lib::__detail::walk<int>"),
            _fn("lib::__detail::walk<double>"),
        ])
        changes = detect_internal_template_leaks(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API
        assert c.symbol == "lib::__detail::walk"

    def test_internal_stem_unchanged_no_finding(self) -> None:
        old = _snap(funcs=[_fn("lib::__detail::walk<int>")])
        new = _snap(funcs=[_fn("lib::__detail::walk<int>")])
        assert detect_internal_template_leaks(old, new) == []

    def test_public_stem_not_internal(self) -> None:
        old = _snap(funcs=[_fn("lib::walk<int>")])
        new = _snap(funcs=[_fn("lib::walk<char>")])
        assert detect_internal_template_leaks(old, new) == []

    def test_custom_internal_namespaces(self) -> None:
        old = _snap(funcs=[_fn("lib::priv::walk<int>")])
        new = _snap(funcs=[_fn("lib::priv::walk<char>")])
        changes = detect_internal_template_leaks(
            old, new, internal_namespaces=("priv",),
        )
        assert len(changes) == 1

    def test_non_template_internal_funcs_ignored(self) -> None:
        # The detector targets *instantiations*; plain (non-template)
        # internal helpers are out of scope.
        old = _snap(funcs=[_fn("lib::__detail::plain_helper")])
        new = _snap(funcs=[])
        assert detect_internal_template_leaks(old, new) == []


# ---------------------------------------------------------------------------
# CPO_KIND_CHANGED
# ---------------------------------------------------------------------------


class TestCpoKindChanged:
    # Variables use a bare, unqualified `name` ("sort") to match real castxml
    # output — it never namespace-qualifies Variable elements — but a real
    # external-linkage variable's `mangled` demangles to the full qualified
    # path ("lib::sort"), which is what the detector actually compares (both
    # sides are matched by full qualified name, never a bare leaf, so two
    # unrelated namespaces reusing the same leaf never cross-match).
    def test_function_became_variable(self) -> None:
        old = _snap(funcs=[_fn("lib::sort")])
        new = _snap(vars_=[_var("sort", type_="lib::__sort_fn", mangled="_ZN3lib4sortE")])
        changes = detect_cpo_kind_changed(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.CPO_KIND_CHANGED
        assert c.old_value == "function"
        assert c.new_value == "variable"

    def test_variable_became_function(self) -> None:
        old = _snap(vars_=[_var("sort", type_="lib::__sort_fn", mangled="_ZN3lib4sortE")])
        new = _snap(funcs=[_fn("lib::sort")])
        changes = detect_cpo_kind_changed(old, new)
        assert len(changes) == 1
        assert changes[0].new_value == "function"

    def test_no_flip_no_finding(self) -> None:
        old = _snap(funcs=[_fn("lib::sort")])
        new = _snap(funcs=[_fn("lib::sort")])
        assert detect_cpo_kind_changed(old, new) == []

    def test_name_present_as_both_in_old_skipped(self) -> None:
        # If the name was already ambiguous (both function and variable)
        # in old, the new state is not a flip — silently skip to avoid
        # false positives.
        old = _snap(
            funcs=[_fn("lib::sort")],
            vars_=[_var("sort", mangled="_ZN3lib4sortE")],
        )
        new = _snap(vars_=[_var("sort", mangled="_ZN3lib4sortE")])
        assert detect_cpo_kind_changed(old, new) == []

    def test_different_namespaces_not_conflated(self) -> None:
        # ns1::sort (a function, removed) and ns2::sort (an unrelated
        # variable, added) share a bare leaf name but live in different
        # namespaces — this must NOT be reported as a CPO kind flip
        # (regression: a bare-leaf-only comparison would wrongly conflate
        # them, since Variable.name itself carries no namespace).
        old = _snap(funcs=[_fn("ns1::sort")])
        new = _snap(vars_=[_var("sort", type_="ns2::__sort_fn", mangled="_ZN3ns24sortE")])
        assert detect_cpo_kind_changed(old, new) == []

    def test_function_template_became_variable(self) -> None:
        # A function TEMPLATE instantiation's demangled name includes a
        # leading return type (Itanium demangling needs it to disambiguate
        # return-type-only overloads) — real mangled name for
        # `template<class T> T lib::sort(T*, T*)` instantiated as
        # `sort<int>`, verified via c++filt to demangle to
        # "int lib::sort<int>(int*, int*)". After template-arg and
        # param-signature stripping that leaves a leaked "int " prefix,
        # which must be stripped so this still matches the CPO variable
        # side's plain "lib::sort" (Codex review: function-template variant
        # of case88).
        old = _snap(funcs=[_fn("sort", mangled="_ZN3lib4sortIiEET_PS1_S2_")])
        new = _snap(vars_=[_var("sort", type_="lib::__sort_fn", mangled="_ZN3lib4sortE")])
        changes = detect_cpo_kind_changed(old, new)
        assert len(changes) == 1
        assert changes[0].new_value == "variable"

    def test_thunk_prefix_not_treated_as_leaked_return_type(self) -> None:
        # An ABI thunk marker ("non-virtual thunk to ...") is a demangled
        # name that, like a genuine function-template leak, contains a
        # top-level space before the qualified name — but it is not a
        # template instantiation, so it must not be routed through the
        # leaked-return-type stripper. Doing so would collapse it to
        # "lib::sort" and wrongly collide with an unrelated same-named CPO
        # variable (Codex review).
        old = _snap(funcs=[_fn("non-virtual thunk to lib::sort()")])
        new = _snap(vars_=[_var("sort", type_="lib::__sort_fn", mangled="_ZN3lib4sortE")])
        assert detect_cpo_kind_changed(old, new) == []


# ---------------------------------------------------------------------------
# OVERLOAD_SET_REROUTED
# ---------------------------------------------------------------------------


class TestOverloadSetRerouted:
    def test_overload_swap_fires(self) -> None:
        old = _snap(funcs=[
            _fn("lib::sort", mangled="_Zold1", params=[("a", "int*")]),
            _fn("lib::sort", mangled="_Zold2", params=[("a", "long*")]),
        ])
        new = _snap(funcs=[
            _fn("lib::sort", mangled="_Znew1", params=[("a", "int*")]),
            _fn("lib::sort", mangled="_Znew2", params=[("a", "double*")]),
        ])
        changes = detect_overload_set_rerouted(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.OVERLOAD_SET_REROUTED
        assert changes[0].symbol == "lib::sort"

    def test_pure_addition_no_finding(self) -> None:
        old = _snap(funcs=[
            _fn("lib::sort", mangled="_Zo1", params=[("a", "int*")]),
        ])
        new = _snap(funcs=[
            _fn("lib::sort", mangled="_Zn1", params=[("a", "int*")]),
            _fn("lib::sort", mangled="_Zn2", params=[("a", "long*")]),
        ])
        assert detect_overload_set_rerouted(old, new) == []

    def test_pure_removal_no_finding(self) -> None:
        old = _snap(funcs=[
            _fn("lib::sort", mangled="_Zo1", params=[("a", "int*")]),
            _fn("lib::sort", mangled="_Zo2", params=[("a", "long*")]),
        ])
        new = _snap(funcs=[
            _fn("lib::sort", mangled="_Zn1", params=[("a", "int*")]),
        ])
        assert detect_overload_set_rerouted(old, new) == []

    def test_volatile_and_ref_qualifiers_rendered(self) -> None:
        """Overloads differing by volatile / ref-qualifier are distinct members
        and the rendered old/new values surface those qualifiers."""
        f_vol = _fn("lib::g", mangled="_ZVo", params=[("a", "int")])
        f_vol.is_volatile = True
        f_ref = _fn("lib::g", mangled="_ZRo", params=[("a", "int")])
        f_ref.ref_qualifier = "&"
        old = _snap(funcs=[
            _fn("lib::g", mangled="_Zo", params=[("a", "int")]),
            f_vol,
            f_ref,
        ])
        new = _snap(funcs=[_fn("lib::g", mangled="_Zn", params=[("a", "long")])])
        changes = detect_overload_set_rerouted(old, new)
        assert len(changes) == 1
        assert "volatile" in changes[0].old_value
        assert "&" in changes[0].old_value

    def test_cv_ref_only_overload_set_still_fires(self) -> None:
        """Overloads that differ only in implicit-object cv/ref qualifiers share
        a parameter-type tuple but are distinct overloads. A genuine overload
        set (e.g. `f(int)` + `f(int) const`) replaced by `f(long)` must still
        fire OVERLOAD_SET_REROUTED — the guard counts actual overloads, not
        distinct parameter-type tuples."""
        f_const = _fn("lib::f", mangled="_ZNK3lib1fEi", params=[("a", "int")])
        f_const.is_const = True
        old = _snap(funcs=[
            _fn("lib::f", mangled="_ZN3lib1fEi", params=[("a", "int")]),
            f_const,
        ])
        new = _snap(funcs=[
            _fn("lib::f", mangled="_ZN3lib1fEl", params=[("a", "long")]),
        ])
        changes = detect_overload_set_rerouted(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.OVERLOAD_SET_REROUTED

    def test_cv_ref_only_removal_in_mixed_change_fires(self) -> None:
        """Membership diff must use the cv/ref-aware overload key, not just
        parameter-type tuples. {f(int), f(int) const} -> {f(int), f(long)}
        removes the `const` overload and adds `f(long)`; with a param-only key
        the shared `(int)` tuple would hide the removal and the reroute would be
        missed. The const overload's disappearance must be detected."""
        f_const = _fn("lib::f", mangled="_ZNK3lib1fEi", params=[("a", "int")])
        f_const.is_const = True
        old = _snap(funcs=[
            _fn("lib::f", mangled="_ZN3lib1fEi", params=[("a", "int")]),
            f_const,
        ])
        new = _snap(funcs=[
            _fn("lib::f", mangled="_ZN3lib1fEi", params=[("a", "int")]),
            _fn("lib::f", mangled="_ZN3lib1fEl", params=[("a", "long")]),
        ])
        changes = detect_overload_set_rerouted(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.OVERLOAD_SET_REROUTED

    def test_single_function_signature_change_no_finding(self) -> None:
        """A name that maps to exactly one function on both sides is not an
        overload set — a 1→1 signature change cannot re-route to a different
        overload, so it must not produce a spurious OVERLOAD_SET_REROUTED
        finding (it is already reported as FUNC_PARAMS_CHANGED). This also
        covers every plain C function, which can never be overloaded."""
        old = _snap(funcs=[
            _fn("add", mangled="add", params=[("a", "int"), ("b", "int")]),
        ])
        new = _snap(funcs=[
            _fn("add", mangled="add", params=[("a", "long"), ("b", "int")]),
        ])
        assert detect_overload_set_rerouted(old, new) == []


# ---------------------------------------------------------------------------
# MANDATORY_TEMPLATE_PARAM_ADDED
# ---------------------------------------------------------------------------


class TestMandatoryTemplateParamAdded:
    def test_arity_grew(self) -> None:
        old = _snap(funcs=[_fn("Foo<int>")])
        new = _snap(funcs=[_fn("Foo<int, char>")])
        changes = detect_mandatory_template_param_added(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.MANDATORY_TEMPLATE_PARAM_ADDED
        assert changes[0].symbol == "Foo"

    def test_arity_stable_no_finding(self) -> None:
        # Library kept a Foo<int> instantiation alive, so the heuristic
        # cannot tell a new defaulted param from a mandatory one.
        old = _snap(funcs=[_fn("Foo<int>")])
        new = _snap(funcs=[_fn("Foo<int>"), _fn("Foo<int, char>")])
        assert detect_mandatory_template_param_added(old, new) == []

    def test_works_for_types(self) -> None:
        old = _snap(types=[_rec("Bar<int>")])
        new = _snap(types=[_rec("Bar<int, float>")])
        changes = detect_mandatory_template_param_added(old, new)
        assert len(changes) == 1


# ---------------------------------------------------------------------------
# UNSPECIFIED_RETURN_NOW_NAMED
# ---------------------------------------------------------------------------


class TestUnspecifiedReturnNowNamed:
    def test_auto_to_named(self) -> None:
        old = _snap(funcs=[_fn("lib::make", return_type="auto")])
        new = _snap(funcs=[_fn("lib::make", return_type="lib::Foo")])
        changes = detect_unspecified_return_now_named(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED
        assert c.old_value == "auto"
        assert c.new_value == "lib::Foo"

    def test_named_to_lambda(self) -> None:
        old = _snap(funcs=[_fn("lib::make", return_type="lib::Foo")])
        new = _snap(funcs=[_fn("lib::make", return_type="<lambda(int)>")])
        changes = detect_unspecified_return_now_named(old, new)
        assert len(changes) == 1
        assert "unspecified" in changes[0].description

    def test_stable_no_finding(self) -> None:
        old = _snap(funcs=[_fn("lib::make", return_type="lib::Foo")])
        new = _snap(funcs=[_fn("lib::make", return_type="lib::Foo")])
        assert detect_unspecified_return_now_named(old, new) == []


# ---------------------------------------------------------------------------
# Combined entry point & pipeline integration
# ---------------------------------------------------------------------------


class TestCombined:
    def test_runs_all(self) -> None:
        old = _snap(
            funcs=[
                _fn("lib::__detail::walk<int>"),
                _fn("lib::sort"),
                _fn("lib::make", return_type="auto"),
            ],
        )
        new = _snap(
            funcs=[
                _fn("lib::__detail::walk<char>"),
                _fn("lib::make", return_type="lib::Foo"),
            ],
            # Bare, unqualified `name` — matches real castxml Variable output —
            # with a realistic mangled name so it demangles to "lib::sort"
            # (see TestCpoKindChanged's comment).
            vars_=[_var("sort", type_="lib::__sort_fn", mangled="_ZN3lib4sortE")],
        )
        changes = detect_template_patterns(old, new)
        kinds = {c.kind for c in changes}
        assert ChangeKind.INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API in kinds
        assert ChangeKind.CPO_KIND_CHANGED in kinds
        assert ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED in kinds


class TestPipelineIntegration:
    def test_default_pipeline_includes_template_step(self) -> None:
        from abicheck.post_processing import DEFAULT_PIPELINE
        assert "detect_template_patterns" in DEFAULT_PIPELINE.step_names
