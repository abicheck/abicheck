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
    _strip_param_signature,
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
    ScopeOrigin,
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


def _rec_public(name: str) -> RecordType:
    """A type explicitly scoped to the public-header set (ADR-024
    --public-header), the one reliable public-reachability signal
    RecordType carries."""
    return RecordType(name=name, kind="class", origin=ScopeOrigin.PUBLIC_HEADER)


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
# _strip_param_signature
# ---------------------------------------------------------------------------


class TestStripParamSignature:
    """Codex review: a call operator's own name is spelled ``operator()`` —
    naively stopping at the first top-level ``(`` corrupted
    ``ns::C::operator()(ns::T const&) const`` down to ``ns::C::operator``,
    losing the ``()`` that is actually part of the identifier.
    """

    def test_call_operator_keeps_its_parentheses(self) -> None:
        sig = "ns::experimental::C::operator()(ns::detail::T const&) const"
        assert _strip_param_signature(sig) == "ns::experimental::C::operator()"

    def test_zero_arg_call_operator(self) -> None:
        assert _strip_param_signature("ns::C::operator()() const") == "ns::C::operator()"

    def test_plain_function_unaffected(self) -> None:
        assert _strip_param_signature("lib::sort(int*, int*)") == "lib::sort"

    def test_symbolic_operator_unaffected(self) -> None:
        assert _strip_param_signature("ns::C::operator+(int)") == "ns::C::operator+"

    def test_conversion_operator_unaffected(self) -> None:
        sig = "ns::C::operator ns::Bar() const"
        assert _strip_param_signature(sig) == "ns::C::operator ns::Bar"

    def test_no_parens_returns_unchanged(self) -> None:
        assert _strip_param_signature("lib::sort") == "lib::sort"

    def test_lookalike_operator_suffix_is_not_special_cased(self) -> None:
        # Codex review: "cooperator" merely *ends with* the substring
        # "operator" — it is an ordinary identifier, not a disguised call
        # operator, and its parameter list must strip normally.
        assert _strip_param_signature("ns::cooperator(int)") == "ns::cooperator"

    def test_function_pointer_return_type_does_not_truncate_at_wrapper_paren(
        self,
    ) -> None:
        # Codex review: a function template returning a function pointer
        # demangles with the return type's own declarator wrapped *around*
        # the name and its real arguments — the first "(" in the string
        # opens that wrapper, not the function's own parameter list.
        sig = "int (*ns::experimental::bar<int>(int))()"
        result = _strip_param_signature(sig)
        # The real fix is that _segments() (the actual downstream leaf
        # consumer) still recovers the correct leaf despite the wrapper
        # prefix surviving in the stripped text.
        from abicheck.diff_namespaces import _segments
        assert _segments(result)[-1] == "bar"

    def test_call_operator_after_function_pointer_wrapper(self) -> None:
        # Both fixes composed: a wrapping declarator paren followed later
        # by a genuine call operator's own empty parentheses.
        sig = "int (*ns::C::operator()(int))()"
        result = _strip_param_signature(sig)
        assert result.endswith("operator()")

    def test_member_function_pointer_return_type_does_not_truncate(self) -> None:
        # Codex review, fresh evidence after the plain-pointer-wrapper fix:
        # a template returning a pointer to *member* function has the "*"
        # preceded by the owning class's own scope ("ns::C::*"), not glued
        # directly to "(" — the plain "qualified[i+1] in '*&'" check missed
        # this shape entirely.
        sig = "int (ns::C::*ns::experimental::bar<int>(int))()"
        result = _strip_param_signature(sig)
        from abicheck.diff_namespaces import _segments
        assert _segments(result)[-1] == "bar"

    def test_pointer_parameter_is_not_mistaken_for_a_wrapper(self) -> None:
        # A "*" in the parameter list itself (not a return-type wrapper)
        # must not trip the wrapper heuristic — it's followed by ","/")",
        # never a further "(".
        assert _strip_param_signature("lib::sort(int*, int*)") == "lib::sort"

    def test_decltype_return_type_does_not_truncate(self) -> None:
        # Codex review, fresh evidence: a dependent decltype expression in
        # the return type also has its own "(" preceded by whitespace
        # ("decltype (...)") and no "*"/"&" at all — the current rule
        # (whitespace-preceded "(" is never a real parameter list) covers
        # this the same way it covers the two pointer-wrapper shapes.
        sig = "decltype ({parm#1}+{parm#1}) ns::sort<int>(int)"
        result = _strip_param_signature(sig)
        from abicheck.diff_namespaces import _segments
        assert _segments(result)[-1] == "sort"

    def test_decltype_with_nested_unrelated_call_does_not_truncate(self) -> None:
        # Codex review, fresh evidence: a decltype expression whose own
        # content has a further nested, unrelated call ("g()") defeats a
        # naive "skip just this one '(' character" rule -- the scan lands
        # on the nested "(" of "g()" next, which is *not* preceded by
        # whitespace (glued to "g"), so it would be mistaken for the real
        # parameter list. The whole balanced decltype group must be
        # skipped, not just its opening character.
        sig = "decltype ((g())?{parm#1} : {parm#1}) ns::experimental::f<int>(int)"
        result = _strip_param_signature(sig)
        from abicheck.diff_namespaces import _segments
        assert _segments(result)[-1] == "f"

    def test_decltype_with_arithmetic_star_is_not_a_declarator(self) -> None:
        # Codex review, fresh evidence: real GCC output for a dependent
        # decltype expression can itself contain a "*" that is ordinary
        # multiplication ("{parm#1}*(g())"), not a pointer-declarator wrapper
        # star. The un-gated version accepted that arithmetic "*" as if it
        # opened a wrapper, matched the following "(" of the unrelated
        # nested call "g()" as the wrapper's real call, and returned an
        # empty slice between the adjacent "*(" -- collapsing the whole
        # identity down to "". _pointer_declarator_star_index now requires
        # only whitespace/identifier/scope/template-bracket characters
        # before a star counts as a declarator prefix; anything else (like
        # the "{" opening this decltype's own token stream) means the "("
        # never opened a real wrapper, so the whole balanced group is
        # skipped instead (same as any other decltype expression).
        sig = "decltype ({parm#1}*(g())) ns::sort<int>(int)"
        result = _strip_param_signature(sig)
        assert result != ""
        from abicheck.diff_namespaces import _segments
        assert _segments(result)[-1] == "sort"

    def test_unbalanced_expression_paren_falls_back_safely(self) -> None:
        # No matching close paren for the leading (whitespace-preceded)
        # "(" -- must not raise or infinite-loop, just give up and return
        # the input unchanged.
        assert _strip_param_signature("decltype (unbalanced") == "decltype (unbalanced"

    def test_pointer_wrapper_leaves_a_clean_qualified_name(self) -> None:
        # Codex review, fresh evidence: skipping a pointer-declarator
        # wrapper left its own "(" and "*"/"Class::*" text sitting in the
        # returned prefix -- harmless for a leaf-only consumer (_segments()
        # takes only the last "::"-segment) but wrong for a caller using
        # the whole result as a qualified-name *identity*
        # (detect_cpo_kind_changed), which never matched a
        # pointer-to-member-function CPO transition against the corrupted
        # prefix. This is exactly the shape detect_cpo_kind_changed feeds
        # in (template args already stripped by _strip_template_args
        # before this function ever sees it).
        assert _strip_param_signature("int (*ns::sort(int))()") == "ns::sort"
        assert _strip_param_signature("int (ns::C::*ns::sort(int))()") == "ns::sort"

    def test_pointer_wrapper_with_pointer_template_argument(self) -> None:
        # Codex review, fresh evidence: the previous fix recovered the clean
        # name via prefix.rindex("*") over the *whole* prefix once a wrapper
        # was skipped -- wrong whenever the wrapped call's own name also
        # carries a pointer template argument ("bar<int*>"), since that
        # argument's "*" sits to the right of (and so wins rindex over) the
        # wrapper's own "*". "int (*ns::experimental::bar<int*>(int*))()"
        # collapsed to a bare ">" before this was fixed to track the
        # wrapper's own star position directly instead of re-deriving it.
        assert (
            _strip_param_signature("int (*ns::experimental::bar<int*>(int*))()")
            == "ns::experimental::bar<int*>"
        )
        assert (
            _strip_param_signature("int (ns::C::*ns::bar<int*>(int*))()")
            == "ns::bar<int*>"
        )

    def test_member_pointer_wrapper_with_multi_arg_template_owner(self) -> None:
        # Codex review, fresh evidence: a member-function-pointer wrapper's
        # owning class can itself be a template with more than one argument
        # ("ns::C<int, double>::*..."), and that comma inside the owner's
        # own template-argument list sits *before* the wrapper's own "*" is
        # ever reached while scanning forward. The un-tracked version
        # (no "<"/">" depth) mistook that comma for the parameter-list
        # terminator, rejected the wrapper entirely, and corrupted the
        # eventual leaf. Tracking angle-bracket depth so only a *top-level*
        # comma/close-paren ends the scan fixes it.
        assert (
            _strip_param_signature(
                "int (ns::C<int, double>::*ns::experimental::bar<int>(int))()"
            )
            == "ns::experimental::bar<int>"
        )

    def test_cpo_kind_changed_matches_across_pointer_to_member_wrapper(self) -> None:
        # End-to-end: a function template returning a pointer to member
        # function must still be recognized as the same qualified name as
        # a same-named CPO variable.
        import abicheck.demangle as dm

        def fake_demangle(mangled_list: list[str]) -> dict[str, str]:
            sigs = {
                "_Zfn": "int (ns::C::*ns::sort<int>(int))()",
                "_ZN2ns4sortE": "ns::sort",
            }
            return {m: sigs[m] for m in mangled_list if m in sigs}

        orig = dm.demangle_batch
        dm.demangle_batch = fake_demangle  # type: ignore[assignment]
        try:
            old = _snap(funcs=[_fn("", mangled="_Zfn")])
            new = _snap(vars_=[_var("sort", type_="ns::__sort_fn", mangled="_ZN2ns4sortE")])
            changes = detect_cpo_kind_changed(old, new)
        finally:
            dm.demangle_batch = orig  # type: ignore[assignment]

        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.CPO_KIND_CHANGED

    def test_cpo_kind_changed_matches_across_pointer_template_argument(self) -> None:
        # End-to-end for the pointer-template-argument regression: a
        # pointer-returning function template whose own name carries a
        # pointer template argument must still be recognized as the same
        # qualified name as a same-named CPO variable, not corrupted down
        # to a bare ">" by a stale rindex("*") over the whole prefix.
        import abicheck.demangle as dm

        def fake_demangle(mangled_list: list[str]) -> dict[str, str]:
            sigs = {
                "_Zfn": "int (*ns::experimental::bar<int*>(int*))()",
                "_ZN2ns12experimental3barE": "ns::experimental::bar",
            }
            return {m: sigs[m] for m in mangled_list if m in sigs}

        orig = dm.demangle_batch
        dm.demangle_batch = fake_demangle  # type: ignore[assignment]
        try:
            old = _snap(funcs=[_fn("", mangled="_Zfn")])
            new = _snap(vars_=[
                _var("bar", type_="ns::experimental::__bar_fn", mangled="_ZN2ns12experimental3barE"),
            ])
            changes = detect_cpo_kind_changed(old, new)
        finally:
            dm.demangle_batch = orig  # type: ignore[assignment]

        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.CPO_KIND_CHANGED


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
        # ADR-044 D1/D2 (Codex review): this finding's mere existence proves
        # public reachability, so it must be tagged directly — it is created
        # by DetectTemplatePatterns, which runs after ApplySuppression/
        # MarkReachability, so nothing else would ever tag it.
        assert c.public_reachable is True
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

    def test_purely_additive_instantiation_set_does_not_fire(self) -> None:
        # Reported bug: an internal-namespace template that only gained new
        # instantiations (every existing one is still there, unchanged) does
        # not remove anything a consumer could already be linked against —
        # an addition alone cannot break an already-linked consumer, so this
        # must not be reported as INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API.
        old = _snap(funcs=[_fn("lib::__detail::walk<int>")])
        new = _snap(funcs=[
            _fn("lib::__detail::walk<int>"),
            _fn("lib::__detail::walk<char>"),
        ])
        assert detect_internal_template_leaks(old, new) == []

    def test_removed_instantiation_alongside_addition_still_fires(self) -> None:
        # A mix of "existing instantiation vanished" and "new one appeared"
        # must still fire — the removal alone already breaks a consumer that
        # linked against it.
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
        # ADR-044 D1 (Codex review): _func_names/_var_names filter to
        # Visibility.PUBLIC, so this finding must be tagged reachable at
        # construction time — DetectTemplatePatterns runs after
        # ApplySuppression, so nothing else would ever tag it.
        assert c.public_reachable is True
        assert c.reachability_kind == "direct_public_symbol"

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

    def test_operator_substring_in_namespace_is_not_an_operator_overload(self) -> None:
        # A namespace merely spelled with "operator" as a substring
        # ("cooperator") is not an operator overload — the leaked return
        # type still needs stripping so the function-template-to-CPO
        # transition living under it is detected (Codex review).
        old = _snap(funcs=[_fn("int lib::cooperator::sort<int>")])
        new = _snap(
            vars_=[_var("sort", type_="lib::cooperator::__sort_fn", mangled="_ZN3lib10cooperator4sortE")]
        )
        changes = detect_cpo_kind_changed(old, new)
        assert len(changes) == 1
        assert changes[0].new_value == "variable"


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
        assert changes[0].public_reachable is True
        assert changes[0].reachability_kind == "direct_public_symbol"

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
        # RecordType carries no visibility field, and this type's origin
        # defaults to ScopeOrigin.UNKNOWN (no --public-header scoping used)
        # — no reliable signal in that (common) case.
        assert changes[0].public_reachable is False
        assert changes[0].reachability_kind is None

    def test_public_header_type_is_reachable(self) -> None:
        """Codex review: RecordType.origin == ScopeOrigin.PUBLIC_HEADER (set
        only under ADR-024's opt-in --public-header scoping) IS a reliable
        public-reachability signal, unlike the default ScopeOrigin.UNKNOWN
        case above."""
        old = _snap(types=[_rec_public("Bar<int>")])
        new = _snap(types=[_rec_public("Bar<int, float>")])
        changes = detect_mandatory_template_param_added(old, new)
        assert len(changes) == 1
        assert changes[0].public_reachable is True
        assert changes[0].reachability_kind == "direct_public_symbol"


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
        assert c.public_reachable is True
        assert c.reachability_kind == "direct_public_symbol"

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
