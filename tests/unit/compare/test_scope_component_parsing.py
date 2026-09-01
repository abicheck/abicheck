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

"""``qualified_name_scope_components``/``strip_trailing_top_level_parameter_list``
edge cases: template nesting, expression-bearing scopes, lambda bodies,
subscript/trailing-return-arrow template arguments, and ``operator`` token
boundaries.

Split out of ``test_namespace_move.py`` (ADR-061 debt cleanup): these cases
exercise ``diff_cxx_rules``'s qualified-name scope parsing directly, not the
namespace-move grouping/emission entry points that file owns, though several
also confirm the parsed chain still feeds ``find_namespace_move_groups``
correctly for the header-tier (unmangled) fallback shapes it depends on.
"""

from __future__ import annotations

from abicheck.compare.namespace_move import find_namespace_move_groups
from abicheck.diff_cxx_rules import (
    qualified_name_scope_components,
    strip_trailing_top_level_parameter_list,
)


class TestQualifiedNameScopeComponentsRespectsTemplateNesting:
    """Codex review, fresh evidence: a naive ``split("::")`` treats a
    separator INSIDE a template argument as an enclosing scope. For
    ``lib::foo<old::A>``, that would fabricate a middle component
    ``"foo<old"`` -- which can then coincidentally collide with an
    unrelated ``"foo<new"`` from a different instantiation, producing a
    false namespace-move grouping between two type arguments that were
    never renamed at all."""

    def test_splits_only_at_top_level_separators(self) -> None:
        assert qualified_name_scope_components("lib::foo<old::A>") == [
            "lib",
            "foo<old::A>",
        ]
        assert qualified_name_scope_components("ns::Class::method") == [
            "ns",
            "Class",
            "method",
        ]
        assert qualified_name_scope_components("freefunc") == ["freefunc"]

    def test_a_templated_removal_and_addition_never_pair_as_a_namespace_move(
        self,
    ) -> None:
        """The exact repro from review: ``lib::foo<old::A>``/
        ``lib::foo<old::B>`` removed and ``lib::foo<new::A>``/
        ``lib::foo<new::B>`` added must NOT group as a ``foo<old`` ->
        ``foo<new`` namespace move -- these are two distinct template
        instantiations, not a namespace rename."""
        removed = {"lib::foo<old::A>", "lib::foo<old::B>"}
        added = {"lib::foo<new::A>", "lib::foo<new::B>"}
        groups = find_namespace_move_groups(removed, added)
        assert groups == {}

    def test_unbalanced_nesting_returns_none(self) -> None:
        assert qualified_name_scope_components("lib::foo<old::A") is None
        assert qualified_name_scope_components("lib::foo>old::A") is None

    def test_empty_and_degenerate_inputs_return_none(self) -> None:
        assert qualified_name_scope_components("") is None
        assert qualified_name_scope_components("::foo") is None
        assert qualified_name_scope_components("foo::::bar") is None


class TestQualifiedNameScopeComponentsKeepsConversionTargetsWhole:
    """Codex review, fresh evidence: a conversion operator's own target
    type can carry ``"::"`` (``operator old::X()``) -- without special
    handling, the target's own separator is mistaken for an enclosing
    scope boundary, the same concern :func:`owner_class_of` already
    documents and handles for exactly this shape."""

    def test_owned_conversion_operator_keeps_the_target_whole(self) -> None:
        assert qualified_name_scope_components("api::C::operator old::X") == [
            "api",
            "C",
            "operator old::X",
        ]

    def test_bare_conversion_operator_with_no_owner_has_no_scope(self) -> None:
        assert qualified_name_scope_components("operator old::X") == ["operator old::X"]

    def test_malformed_target_with_unclosed_template_is_rejected(self) -> None:
        """CodeRabbit review, fresh evidence: an earlier revision stopped
        scanning the instant the ``"::operator "`` marker was found, so
        nothing past it was ever validated for balanced nesting -- a
        malformed target like ``operator old::X<`` (an unclosed template
        argument) was silently accepted instead of rejected."""
        assert qualified_name_scope_components("api::C::operator old::X<") is None

    def test_malformed_target_with_stray_closing_paren_is_rejected(self) -> None:
        assert qualified_name_scope_components("api::C::operator old::X)") is None

    def test_well_formed_template_target_is_still_accepted(self) -> None:
        """The balance check must not reject a genuinely well-formed
        target that merely contains its own template arguments."""
        assert qualified_name_scope_components("api::C::operator Foo<int>") == [
            "api",
            "C",
            "operator Foo<int>",
        ]

    def test_two_conversion_operator_removals_and_additions_still_pair_correctly(
        self,
    ) -> None:
        """The exact repro shape from review: two classes' conversion
        operators to a namespace-qualified target, the namespace moving
        the same way plain ``d1`` -> ``d2`` does elsewhere in this file.
        Confirmed to produce a false ``operator old`` -> ``operator new``
        namespace-move claim before the fix, instead of the real
        ``old`` -> ``new`` substitution over the whole target."""
        removed = {
            "api::C::operator old::X",
            "api::D::operator old::X",
        }
        added = {
            "api::C::operator new::X",
            "api::D::operator new::X",
        }
        groups = find_namespace_move_groups(removed, added)
        assert ("operator old", "operator new") not in groups
        assert ("old::X", "new::X") not in groups
        # No scope substitution can pair these at all: the differing
        # segment is the whole conversion-target leaf, which
        # find_namespace_move_groups deliberately never treats as
        # substitutable (a differing leaf is a renamed declaration, not a
        # moved scope -- see this function's own docstring).
        assert groups == {}


class TestQualifiedNameScopeComponentsAcceptsExpressionBearingTargets:
    """Codex review, fresh evidence: an earlier revision of the balanced-
    nesting check (added to close the malformed-target gap above) used one
    shared depth counter for both ``<``/``>`` and ``(``/``)`` -- but a real,
    demangled non-type template argument can legitimately contain a bare
    ``<``/``>`` comparison, e.g. ``operator
    std::integral_constant<bool, (sizeof(T) > 1)>``. The comparison's own
    ``>`` was miscounted as closing the ``integral_constant<`` template,
    driving the counter negative and rejecting perfectly well-formed input.
    C++'s own grammar requires such a comparison to be parenthesized
    wherever it appears as a template argument specifically to remove this
    ambiguity, so a compiler's pretty-printed text always carries the
    disambiguating parens -- angle-bracket and paren nesting are now
    tracked as two independent counters, and a ``<``/``>`` is only ever
    treated as a real template delimiter while no paren is open."""

    def test_comparison_inside_a_non_type_template_argument_is_accepted(self) -> None:
        target = "operator std::integral_constant<bool, (sizeof(T) > 1)>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_less_than_comparison_inside_a_non_type_template_argument_is_accepted(
        self,
    ) -> None:
        target = "operator Array<bool, (N < M)>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_nested_template_argument_alongside_a_parenthesized_comparison_is_accepted(
        self,
    ) -> None:
        """A comparison AND a genuinely nested template argument in the same
        argument list -- the paren-open/close bracket the comparison sits in
        must not desynchronize the angle-bracket counter for the sibling
        template argument that follows it."""
        target = "operator Holder<(A > B), Other<C>>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_still_rejects_genuinely_unbalanced_nesting_alongside_a_comparison(
        self,
    ) -> None:
        """The two-counter fix must not become blind to real malformation --
        an unclosed template past a parenthesized comparison is still
        rejected."""
        assert (
            qualified_name_scope_components("api::C::operator Holder<(A > B), Other<C>")
            is None
        )

    def test_ordinary_qualified_name_with_a_comparison_template_argument_splits_correctly(
        self,
    ) -> None:
        """The same fix applies to the main top-level ``"::"`` split loop,
        not just the conversion-operator scan -- a plain (non-conversion-
        operator) qualified name can carry the identical non-type template
        argument shape anywhere in its scope chain."""
        assert qualified_name_scope_components("ns::Array<bool, (N > M)>::method") == [
            "ns",
            "Array<bool, (N > M)>",
            "method",
        ]


class TestQualifiedNameScopeComponentsAcceptsUnparenthesizedLessThan:
    """Codex review, fresh evidence: unlike ``>``, a bare, UNPARENTHESIZED
    ``<`` comparison as a non-type template argument is legal C++ -- a real
    parser disambiguates it via name lookup (is the identifier immediately
    to its left a known template name?), which this text-only scanner has
    no access to. Confirmed directly against real clang: ``template<int N,
    int M> struct C { operator B<N < M>() const; };`` compiles cleanly, and
    clang's own AST dump prints the unparenthesized comparison verbatim as
    ``operator B<N < M>`` for the uninstantiated member -- exactly the
    shape this function receives from this codebase's own castxml/clang-
    derived declaration names. An earlier revision treated every ``<`` at
    ``paren_depth == 0`` as a real template opener unconditionally, driving
    ``angle_depth`` one too high with nothing to bring it back down,
    rejecting this valid input."""

    def test_unparenthesized_less_than_comparison_target_is_accepted(self) -> None:
        target = "operator B<N < M>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_unparenthesized_less_than_in_an_ordinary_qualified_name_splits_correctly(
        self,
    ) -> None:
        assert qualified_name_scope_components("ns::B<N < M>::method") == [
            "ns",
            "B<N < M>",
            "method",
        ]

    def test_a_real_template_open_immediately_after_a_name_is_still_recognized(
        self,
    ) -> None:
        """The spacing signal must not become blind to genuine nested
        templates -- a real template-opening ``<`` (no preceding space)
        alongside an unparenthesized comparison in a sibling argument."""
        target = "operator Holder<N < M, Other<C>>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_still_rejects_genuinely_unbalanced_nesting_alongside_the_comparison(
        self,
    ) -> None:
        assert qualified_name_scope_components("api::C::operator B<N < M") is None


class TestStripTrailingTopLevelParameterListAcceptsUnparenthesizedLessThan:
    """The identical spacing-based fix, applied to
    :func:`strip_trailing_top_level_parameter_list`'s own angle-bracket
    tracking."""

    def test_unparenthesized_less_than_scope_splits_correctly(self) -> None:
        assert (
            strip_trailing_top_level_parameter_list("ns::Holder<N < M>(int)")
            == "ns::Holder<N < M>"
        )


class TestQualifiedNameScopeComponentsAcceptsLeftShiftExpressionOperators:
    """Codex review, fresh evidence: a lone ``<`` was correctly distinguished
    from a template opener via the spacing signal
    (:func:`_is_template_opening_angle`), but that signal examines each
    character independently -- the SECOND ``<`` of a genuine ``<<``
    left-shift expression operator (e.g. ``operator B<N << M>``) is
    preceded by the FIRST ``<``, not whitespace, so it was still
    misclassified as a template opener. Confirmed directly against real
    clang: ``template<int N, int M> struct C { operator B<N << M>() const;
    };`` compiles cleanly, and clang's AST dump prints the comparison
    verbatim as ``operator B<N << M>``. Fixed by tokenizing multi-character
    ``<``-led expression operators (``<<``, ``<=``, ``<<=``, ``<=>``)
    atomically via :func:`_less_than_led_operator_token_len` BEFORE
    considering either character individually -- structurally sound
    without any whitespace signal, since a template-argument-list can
    never begin with a bare ``<`` or ``=``, so two adjacent ``<``
    characters (or a ``<`` immediately followed by ``=``) can only ever be
    this operator's own spelling, never two independent delimiters."""

    def test_left_shift_comparison_target_is_accepted(self) -> None:
        target = "operator B<N << M>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_less_than_or_equal_comparison_target_is_accepted(self) -> None:
        """A second multi-character `<`-led token, confirmed against real
        clang (``operator B<N <= M>`` compiles and prints verbatim)."""
        target = "operator B<N <= M>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_left_shift_in_an_ordinary_qualified_name_splits_correctly(self) -> None:
        assert qualified_name_scope_components("ns::B<N << M>::method") == [
            "ns",
            "B<N << M>",
            "method",
        ]

    def test_a_real_nested_template_after_a_left_shift_sibling_is_still_recognized(
        self,
    ) -> None:
        """The multi-char-token skip must not desynchronize angle-bracket
        tracking for a genuinely nested template argument that follows."""
        target = "operator Foo<N << M, Other<C>>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_still_rejects_genuinely_unbalanced_nesting_alongside_left_shift(
        self,
    ) -> None:
        assert qualified_name_scope_components("api::C::operator B<N << M") is None


class TestStripTrailingTopLevelParameterListAcceptsLeftShiftExpressionOperators:
    """The identical multi-character-token fix, applied to
    :func:`strip_trailing_top_level_parameter_list`'s own angle-bracket
    tracking."""

    def test_left_shift_scope_splits_correctly(self) -> None:
        assert (
            strip_trailing_top_level_parameter_list("ns::Holder<N << M>(int)")
            == "ns::Holder<N << M>"
        )


class TestQualifiedNameScopeComponentsAcceptsLambdaBodyTemplateArguments:
    """Codex review, fresh evidence: C++20 allows a captureless lambda
    closure as a non-type template argument, and its BODY is a full,
    self-contained statement grammar -- a comparison inside it is not
    required to be parenthesized the way a bare comparison directly in the
    template-argument-list is, since it isn't at that grammar production at
    all. Confirmed directly against real clang: ``operator B<[]{ return N
    > M; }>() const`` (a lambda-typed conversion target) compiles under
    ``-std=c++20`` and is pretty-printed verbatim, unparenthesized
    comparison included, sometimes spanning multiple lines. An earlier
    revision treated every ``>`` at ``paren_depth == 0`` as a real
    template-closing delimiter unconditionally, so this comparison's ``>``
    closed the outer template early and the real closing ``>`` drove the
    counter negative, rejecting valid input."""

    def test_lambda_body_comparison_target_is_accepted(self) -> None:
        target = "operator B<[]{ return N > M; }>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_multi_line_lambda_body_target_confirmed_against_real_clang_output(
        self,
    ) -> None:
        """The exact spelling confirmed by ``clang -ast-dump`` for
        ``operator B<[]{ return N > M; }>()`` under ``-std=c++20`` --
        clang's pretty-printer wraps the lambda body across lines."""
        target = "operator B<[] {\n    return N > M;\n}>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_a_real_nested_template_after_a_lambda_body_sibling_is_still_recognized(
        self,
    ) -> None:
        target = "operator Foo<[]{ return N > M; }, Other<C>>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_still_rejects_genuinely_unbalanced_braces(self) -> None:
        assert (
            qualified_name_scope_components("api::C::operator B<[]{ return N > M; >")
            is None
        )

    def test_still_rejects_genuinely_unbalanced_nesting_after_a_closed_lambda_body(
        self,
    ) -> None:
        assert (
            qualified_name_scope_components("api::C::operator B<[]{ return N > M; }")
            is None
        )


class TestStripTrailingTopLevelParameterListAcceptsLambdaBodyTemplateArguments:
    """The identical brace-tracking fix, applied to
    :func:`strip_trailing_top_level_parameter_list`'s own angle-bracket
    tracking."""

    def test_lambda_body_scope_splits_correctly(self) -> None:
        assert (
            strip_trailing_top_level_parameter_list(
                "ns::Holder<[]{ return N > M; }>(int)"
            )
            == "ns::Holder<[]{ return N > M; }>"
        )


class TestQualifiedNameScopeComponentsAcceptsSubscriptTemplateArguments:
    """Codex review, fresh evidence: a subscript expression used as (or
    within) a non-type template argument carries a ``>`` that needs no
    parenthesization either -- ``]``, not ``>``, closes the subscript, so
    it carries none of the top-level template-argument ambiguity a bare
    ``>`` would. Confirmed directly against real clang:
    ``operator B<A[N > M]>()`` compiles cleanly (with ``constexpr int
    A[10]``) and is pretty-printed verbatim. An earlier revision treated
    every ``>`` at ``paren_depth == 0`` as a real template-closing
    delimiter unconditionally, so the comparison's own ``>`` closed the
    outer template early and the real closing ``>`` drove the counter
    negative, rejecting valid input."""

    def test_subscript_comparison_target_is_accepted(self) -> None:
        target = "operator B<A[N > M]>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_subscript_in_an_ordinary_qualified_name_splits_correctly(self) -> None:
        assert qualified_name_scope_components("ns::B<A[N > M]>::method") == [
            "ns",
            "B<A[N > M]>",
            "method",
        ]

    def test_a_real_nested_template_after_a_subscript_sibling_is_still_recognized(
        self,
    ) -> None:
        target = "operator Foo<A[N > M], Other<C>>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_still_rejects_genuinely_unbalanced_brackets(self) -> None:
        assert qualified_name_scope_components("api::C::operator B<A[N > M>") is None

    def test_still_rejects_genuinely_unbalanced_nesting_after_a_closed_subscript(
        self,
    ) -> None:
        assert qualified_name_scope_components("api::C::operator B<A[N > M]") is None


class TestStripTrailingTopLevelParameterListAcceptsSubscriptTemplateArguments:
    """The identical bracket-tracking fix, applied to
    :func:`strip_trailing_top_level_parameter_list`'s own angle-bracket
    tracking."""

    def test_subscript_scope_splits_correctly(self) -> None:
        assert (
            strip_trailing_top_level_parameter_list("ns::Holder<A[N > M]>(int)")
            == "ns::Holder<A[N > M]>"
        )


class TestQualifiedNameScopeComponentsAcceptsLambdaTrailingReturnArrows:
    """Codex review, fresh evidence: a lambda's trailing-return-type arrow
    (``[]() -> bool { ... }``) sits in the lambda's OWN declarator, between
    its parameter list and its body -- not inside any brace/bracket the
    earlier fixes already track as opaque. Confirmed directly against real
    clang: ``operator B<[]() -> bool { return N > 0; }>()`` compiles under
    ``-std=c++20`` and is pretty-printed verbatim. Unlike every other ``>``
    case, this needs no heuristic at all: by the C++ lexical grammar's own
    maximal-munch rule, a ``-`` immediately adjacent to a ``>`` can only
    ever tokenize as the single ``->`` token, never as two separate
    tokens."""

    def test_lambda_trailing_return_arrow_target_is_accepted(self) -> None:
        target = "operator B<[]() -> bool { return N > 0; }>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_multi_line_trailing_return_arrow_confirmed_against_real_clang_output(
        self,
    ) -> None:
        """The exact spelling confirmed by ``clang -ast-dump`` for
        ``operator B<[]() -> bool { return N > 0; }>()`` under
        ``-std=c++20``."""
        target = "operator B<[]() -> bool {\n    return N > 0;\n}>"
        assert qualified_name_scope_components(f"api::C::{target}") == [
            "api",
            "C",
            target,
        ]

    def test_trailing_return_arrow_in_an_ordinary_qualified_name_splits_correctly(
        self,
    ) -> None:
        assert qualified_name_scope_components(
            "ns::B<[]() -> bool { return N > 0; }>::method"
        ) == ["ns", "B<[]() -> bool { return N > 0; }>", "method"]

    def test_still_rejects_genuinely_unbalanced_nesting_alongside_the_arrow(
        self,
    ) -> None:
        assert (
            qualified_name_scope_components(
                "api::C::operator B<[]() -> bool { return N > 0; }"
            )
            is None
        )


class TestStripTrailingTopLevelParameterListAcceptsLambdaTrailingReturnArrows:
    """The identical arrow-token fix, applied to
    :func:`strip_trailing_top_level_parameter_list`'s own angle-bracket
    tracking."""

    def test_lambda_trailing_return_arrow_scope_splits_correctly(self) -> None:
        assert (
            strip_trailing_top_level_parameter_list(
                "ns::Holder<[]() -> bool { return N > 0; }>(int)"
            )
            == "ns::Holder<[]() -> bool { return N > 0; }>"
        )


class TestStripTrailingTopLevelParameterListAcceptsExpressionBearingScopes:
    """The identical bracket-kind-blind depth bug as the class above, in
    :func:`strip_trailing_top_level_parameter_list`'s own angle-bracket
    tracking: a comparison inside a parenthesized non-type template
    argument, followed by a genuinely nested function-type template
    argument, could make the counter close the enclosing template one
    character early and mistake the nested function type's own parameter
    list for the real, top-level one."""

    def test_comparison_alongside_a_nested_function_type_argument_splits_correctly(
        self,
    ) -> None:
        assert (
            strip_trailing_top_level_parameter_list(
                "ns::Holder<(A > B), int(int)>(really)"
            )
            == "ns::Holder<(A > B), int(int)>"
        )


class TestStripTrailingTopLevelParameterList:
    """CodeRabbit review, fresh evidence: a synthesized ctor key's
    parameter-list suffix (``__abicheck_ctor__<scope>(<params>)``) was
    stripped via a naive ``scope.find("(")``, which matches the FIRST
    ``(`` anywhere -- including one belonging to a function-type template
    argument nested inside the scope itself, truncating the scope well
    before the real parameter list and losing everything after it."""

    def test_strips_the_real_top_level_parameter_list(self) -> None:
        assert (
            strip_trailing_top_level_parameter_list("ns::Holder<void(int)>(int)")
            == "ns::Holder<void(int)>"
        )

    def test_no_parameter_list_is_unchanged(self) -> None:
        assert strip_trailing_top_level_parameter_list("ns::graph") == "ns::graph"

    def test_a_synthetic_ctor_key_with_a_function_type_template_argument_still_pairs(
        self,
    ) -> None:
        """The exact repro shape from review: a class template holding a
        function-type argument, moving namespace the same way plain
        ``tbb::detail::d1`` -> ``tbb::detail::d2`` does elsewhere in this
        file. Confirmed to return ``{}`` (or a corrupted group keyed on a
        truncated/mismatched scope) before the fix."""
        removed = {"__abicheck_ctor__ns::d1::Holder<void(int)>(int)"}
        added = {"__abicheck_ctor__ns::d2::Holder<void(int)>(int)"}
        groups = find_namespace_move_groups(removed, added)
        assert ("d1", "d2") in groups
        (old_q, new_q) = groups[("d1", "d2")][0]
        assert old_q == "ns::d1::Holder<void(int)>::{ctor}"
        assert new_q == "ns::d2::Holder<void(int)>::{ctor}"


class TestQualifiedNameScopeComponentsRecognizesSymbolOperatorAngleTokens:
    """Codex/CodeRabbit review, fresh evidence: a stream/relational
    operator's own spelling carries a literal ``<``/``>`` (``operator<<``,
    ``operator>>``, ``operator<``, ``operator<=``, ``operator<=>``, ...)
    that is not a template-argument delimiter at all. Without recognizing
    it, the depth tracker sees an unmatched bracket and rejects an
    otherwise ordinary qualified name as "unbalanced nesting"."""

    def test_stream_operators_are_recognized(self) -> None:
        assert qualified_name_scope_components("ns::Stream::operator<<") == [
            "ns",
            "Stream",
            "operator<<",
        ]
        assert qualified_name_scope_components("ns::Stream::operator>>") == [
            "ns",
            "Stream",
            "operator>>",
        ]

    def test_relational_and_spaceship_operators_are_recognized(self) -> None:
        assert qualified_name_scope_components("ns::Widget::operator<") == [
            "ns",
            "Widget",
            "operator<",
        ]
        assert qualified_name_scope_components("ns::Widget::operator<=") == [
            "ns",
            "Widget",
            "operator<=",
        ]
        assert qualified_name_scope_components("ns::Widget::operator<=>") == [
            "ns",
            "Widget",
            "operator<=>",
        ]

    def test_a_namespace_move_over_a_class_with_a_stream_operator_still_pairs(
        self,
    ) -> None:
        """The operator name itself never participates in the namespace
        move (it's the leaf, not a scope segment) -- only the enclosing
        namespace does, and that must still be detected correctly."""
        removed = {
            "d1::Widget::operator<<",
            "d1::Widget::run",
        }
        added = {
            "d2::Widget::operator<<",
            "d2::Widget::run",
        }
        groups = find_namespace_move_groups(removed, added)
        assert ("d1", "d2") in groups
        assert len(groups[("d1", "d2")]) == 2


class TestQualifiedNameScopeComponentsRequiresATokenBoundaryForOperator:
    """Codex review, fresh evidence: a suffix-only ``"operator"`` check
    mistakes the tail of a longer identifier (``myoperator``) for the
    ``operator`` keyword. ``lib::myoperator<old::A>::f`` legitimately ends
    in the eight characters ``"operator"`` right before its ``<``, but the
    real identifier is ``myoperator`` -- an ordinary (if unusually named)
    class, not an overloaded operator. Without a boundary check, the
    parser skips the ``<`` as if it opened a symbol-operator token,
    misreads the template argument's own ``::`` as top-level, and
    eventually returns ``None`` at the unmatched ``>``."""

    def test_an_identifier_merely_ending_in_operator_is_not_mistaken_for_one(
        self,
    ) -> None:
        assert qualified_name_scope_components("lib::myoperator<old::A>::f") == [
            "lib",
            "myoperator<old::A>",
            "f",
        ]

    def test_a_namespace_move_over_such_an_identifier_still_pairs(self) -> None:
        removed = {
            "lib::old::myoperator<T>::f",
            "lib::old::myoperator<T>::g",
        }
        added = {
            "lib::new1::myoperator<T>::f",
            "lib::new1::myoperator<T>::g",
        }
        groups = find_namespace_move_groups(removed, added)
        assert ("old", "new1") in groups
        assert len(groups[("old", "new1")]) == 2

    def test_a_real_operator_at_the_start_of_the_string_is_still_recognized(
        self,
    ) -> None:
        """The boundary check must not itself become over-strict: a bare
        ``"operator<<"`` with no preceding scope (index 0) is still a real
        operator token, not a truncated identifier."""
        assert qualified_name_scope_components("operator<<") == ["operator<<"]
