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

"""ADR-063 Phase 2: direct primitive-level tests for
``model.signature_normalization.canonicalize_function_signature_param_type``.

``tests/test_model_identity.py`` already exercises this primitive through
``entity_id_for_function``'s own overload-discrimination contract; this
file pins the primitive's own contract directly, per AGENTS.md's
"Primitive-level property tests" convention -- a new reusable string-
normalization primitive gets its own standalone tests, decoupled from any
one caller's domain logic.
"""

from __future__ import annotations

from abicheck.model import signature_normalization
from abicheck.model.signature_normalization import (
    canonicalize_function_signature_param_type as canon,
)


class TestByValueCvIsDropped:
    """A top-level BY-VALUE cv-qualifier plays no part in a function's own
    type for linkage purposes -- void f(int) and void f(const int) name
    the same function."""

    def test_plain_scalar_unchanged(self) -> None:
        assert canon("int") == "int"

    def test_leading_const_dropped(self) -> None:
        assert canon("const int") == canon("int")

    def test_leading_volatile_dropped(self) -> None:
        assert canon("volatile unsigned long long") == canon("unsigned long long")

    def test_const_volatile_class_type_dropped(self) -> None:
        assert canon("const std::string") == canon("std::string")


class TestCrossProducerSpellingNormalized:
    """CastXML and Clang spell an otherwise-identical type differently."""

    def test_castxml_vs_clang_pointer_spacing(self) -> None:
        assert canon("char const*") == canon("char const *")

    def test_leading_vs_trailing_const_spelling(self) -> None:
        assert canon("const char *") == canon("char const *")


class TestPointeeCvIsPreserved:
    """A pointee cv-qualifier on a pointer/reference parameter is a
    genuine, standard-mandated overload discriminator -- unlike the
    by-value case, it must never be dropped."""

    def test_const_pointer_differs_from_mutable(self) -> None:
        assert canon("char *") != canon("const char *")

    def test_intermediate_pointer_level_cv_differs(self) -> None:
        # "pointer to a const-qualified pointer to int" vs. "pointer to
        # pointer to int" -- genuinely different, non-interchangeable
        # types, even though the qualifier isn't on the outermost sigil.
        assert canon("int **") != canon("int * const *")

    def test_template_argument_cv_differs(self) -> None:
        # A cv-qualifier nested in a template argument names a genuinely
        # different type -- Box<const int> vs. Box<int>.
        assert canon("Box<const int>") != canon("Box<int>")

    def test_anonymous_namespace_qualified_pointee_cv_differs(self) -> None:
        # Regression pin: a real, observed producer spelling for a type
        # in an anonymous namespace, "(anonymous namespace)::Foo", starts
        # with an opaque paren that precedes the parameter's own actual
        # pointer sigil entirely -- this must not be mistaken for the
        # declarator's own trailing parameter list (which would wrongly
        # lock out that later sigil and merge two genuinely different
        # pointer-to-const-vs-non-const types).
        assert canon("(anonymous namespace)::Foo const *") != canon(
            "(anonymous namespace)::Foo *"
        )

    def test_anonymous_namespace_qualifier_preserved(self) -> None:
        assert "(anonymous namespace)::Foo" in canon(
            "(anonymous namespace)::Foo const *"
        )


class TestPointersOwnTopLevelCvIsDropped:
    """A cv-qualifier trailing the pointer's own outermost sigil qualifies
    the pointer value itself, not what it points to -- dropped exactly
    like any other top-level by-value parameter qualifier."""

    def test_pointer_own_const_dropped(self) -> None:
        assert canon("int * const") == canon("int *")

    def test_pointer_own_const_does_not_erase_pointee_cv(self) -> None:
        # The pointer's own trailing qualifier is dropped, but a genuine
        # pointee qualifier earlier in the same string must survive.
        assert canon("const int * const") == canon("const int *")
        assert canon("const int * const") != canon("int *")


class TestArrayParameterDecay:
    """A function parameter's array type always decays to a pointer -- the
    bound plays no part in the adjusted type at all."""

    def test_bound_does_not_distinguish(self) -> None:
        assert canon("int []") == canon("int [3]") == canon("int [4]") == canon("int *")

    def test_element_cv_becomes_pointee_cv(self) -> None:
        assert canon("const int []") == canon("const int *")
        assert canon("const int [3]") != canon("int [3]")

    def test_multi_dimensional_array_left_unchanged(self) -> None:
        # Documented, accepted limitation: correctly re-spelling T[][N]'s
        # adjusted type (T(*)[N]) needs declarator-rewriting this
        # primitive does not implement.
        assert canon("int [3][4]") == "int [3][4]"

    def test_multi_dimensional_array_element_cv_not_wrongly_stripped(self) -> None:
        # The accepted limitation must not become an active regression:
        # a genuinely different element-cv must still compare different,
        # even though the bound itself isn't normalized away here.
        assert canon("const int [3][4]") != canon("int [3][4]")

    def test_pointer_to_array_left_unchanged(self) -> None:
        # int (*)[3] ("pointer to array of 3 ints") already has its own
        # outermost sigil -- the trailing [3] is the POINTEE's bound, not
        # the parameter's own top-level shape, and must not be decayed.
        assert "*" in canon("int (*)[3]")
        assert "[3]" in canon("int (*)[3]")


class TestParenthesizedDeclaratorOwnCvIsDropped:
    """A parenthesized declarator's own grouping parens (a function-pointer
    or pointer-to-array parameter) are transparent for by-value cv
    purposes -- the cv-qualifier on the declarator's own outermost pointer
    is by-value and dropped, exactly like an unparenthesized pointer."""

    def test_function_pointer_own_const_dropped(self) -> None:
        assert canon("void (* const)(int)") == canon("void (*)(int)")

    def test_function_pointer_param_list_untouched(self) -> None:
        # The callback's OWN parameter types are not this parameter's
        # by-value qualifiers -- they must survive verbatim either way.
        assert "(int)" in canon("void (*)(int)")
        assert "(int)" in canon("void (* const)(int)")

    def test_pointer_to_array_own_const_dropped(self) -> None:
        assert canon("int (* const)[3]") == canon("int (*)[3]")

    def test_pointer_to_array_bound_untouched(self) -> None:
        assert "[3]" in canon("int (* const)[3]")


class TestPointerToMemberOwnCvIsDropped:
    """A pointer-to-member-function declarator's own outermost sigil is
    preceded by the member's qualified-name prefix (``C::``) inside the
    same declarator-grouping parens -- its own trailing cv-qualifier is
    by-value and dropped exactly like a plain function-pointer's."""

    def test_member_pointer_own_const_dropped(self) -> None:
        assert canon("void (C::* const)(int)") == canon("void (C::*)(int)")

    def test_nested_namespace_member_pointer_own_const_dropped(self) -> None:
        assert canon("void (ns::C::* const)(int)") == canon("void (ns::C::*)(int)")

    def test_member_pointer_param_list_untouched(self) -> None:
        assert "(int)" in canon("void (C::*)(int)")
        assert "(int)" in canon("void (C::* const)(int)")


class TestBareDataMemberPointerPointeeCvIsCanonicalized:
    """A bare (non-parenthesized) data-member-pointer parameter, e.g.
    ``int C::*`` (pointer to an int member of C), has a pointee
    cv-qualifier that must canonicalize consistently regardless of
    whether the source spelled it before or after the base type --
    ``canonicalize_type_name`` itself mishandles this across the
    ``C::`` infix, misplacing (not merely leaving unmoved) a leading
    ``const``."""

    def test_leading_and_trailing_const_spellings_unify(self) -> None:
        assert canon("int const C::*") == canon("const int C::*")

    def test_leading_and_trailing_const_still_distinguish_from_unqualified(
        self,
    ) -> None:
        assert canon("int const C::*") != canon("int C::*")

    def test_nested_namespace_qualifier_handled(self) -> None:
        assert canon("int const ns::C::*") == canon("const int ns::C::*")

    def test_class_qualifier_preserved(self) -> None:
        assert "C::" in canon("int const C::*")

    def test_ordinary_namespace_qualified_pointer_unaffected(self) -> None:
        # A plain pointer whose pointee is namespace-qualified must not
        # be mistaken for a member pointer -- the "::" there is nowhere
        # near the sigil, unlike a member pointer's own qualifier.
        assert canon("ns::Foo *") == "ns::Foo *"
        assert canon("ns::Foo *") != canon("const ns::Foo *")

    def test_parenthesized_member_function_pointer_unaffected(self) -> None:
        # The parenthesized case (a member-FUNCTION-pointer's own
        # trailing parameter list) is a structurally different shape,
        # already handled by TestPointerToMemberOwnCvIsDropped -- this
        # bare-pointee-cv fix must not interfere with it.
        assert canon("void (C::* const)(int)") == canon("void (C::*)(int)")


class TestRestrictQualifierSharesCvPositionDiscipline:
    """``restrict``/``__restrict``/``__restrict__`` is dropped on the
    parameter's own outermost, by-value pointer position -- exactly like
    a genuine cv-qualifier there -- but stays genuinely distinguishing on
    an inner pointer level, ALSO exactly like cv. Verified against real
    compiler output (``g++ -c``, both GCC and Clang): ``void f(int *)``
    and ``void f(int * restrict)`` mangle identically and cannot even be
    declared as an overload pair, but ``void f(int **)`` and
    ``void f(int * restrict *)`` mangle to two different, simultaneously-
    declarable symbols (``_Z1fPPi`` vs ``_Z1fPrPi``) -- restrict is NOT
    unconditionally mangling-inert, contrary to an earlier (reverted)
    round's own "strip everywhere" fix."""

    def test_bare_pointer_restrict_stripped(self) -> None:
        assert canon("int *restrict") == canon("int *")

    def test_double_underscore_spelling_stripped(self) -> None:
        assert canon("int *__restrict") == canon("int *")

    def test_double_underscore_trailing_spelling_stripped(self) -> None:
        assert canon("int *__restrict__") == canon("int *")

    def test_restrict_still_distinguishes_nothing_from_a_genuine_cv_change(
        self,
    ) -> None:
        # Sanity check that this doesn't accidentally strip a REAL,
        # distinguishing pointee cv-qualifier too.
        assert canon("int *restrict") != canon("const int *")

    def test_restrict_on_non_outermost_pointer_still_distinguishes(self) -> None:
        # Restrict on an INNER pointer level is a genuine, standard-
        # mandated (GCC/Clang-confirmed) overload discriminator, just
        # like a genuine pointee cv-qualifier there -- must NOT collapse.
        assert canon("int * restrict *") != canon("int * *")

    def test_restrict_inside_callback_parameter_stripped(self) -> None:
        # The recursive nested-parameter-list normalization must also
        # apply the outermost-position rule to each of a callback's own
        # parameters.
        assert canon("void (*)(int *restrict)") == canon("void (*)(int *)")

    def test_idempotent_on_outermost_restrict(self) -> None:
        once = canon("int *restrict")
        assert canon(once) == once

    def test_idempotent_on_inner_restrict(self) -> None:
        once = canon("int * restrict *")
        assert canon(once) == once


class TestClangTrailingCallingConventionAttributeUnifies:
    """Clang's own ``qualType`` spelling for a calling-convention-decorated
    function-pointer declarator trails the attribute AFTER the parameter
    list (``__attribute__((cdecl))``) rather than using the leading
    ``__cdecl``-style keyword MSVC/castxml spell it with -- both spellings
    of the identical type must converge on one identity, or two backends
    observing the same declaration would fragment it."""

    def test_cdecl_attribute_unifies_with_leading_keyword(self) -> None:
        assert canon("void (*)(int) __attribute__((cdecl))") == canon(
            "void (__cdecl *)(int)"
        )

    def test_stdcall_attribute_unifies_with_leading_keyword(self) -> None:
        assert canon("void (*)(int) __attribute__((stdcall))") == canon(
            "void (__stdcall *)(int)"
        )

    def test_member_function_pointer_attribute_unifies(self) -> None:
        assert canon("void (C::*)(int) __attribute__((thiscall))") == canon(
            "void (__thiscall C::*)(int)"
        )

    def test_no_attribute_stays_unaffected(self) -> None:
        assert canon("void (*)(int)") == "void ( * )(int)"

    def test_different_conventions_still_distinguish(self) -> None:
        assert canon("void (*)(int) __attribute__((cdecl))") != canon(
            "void (*)(int) __attribute__((stdcall))"
        )

    def test_attribute_coexists_with_trailing_noexcept(self) -> None:
        assert canon("void (*)(int) __attribute__((cdecl)) noexcept") == canon(
            "void (__cdecl *)(int) noexcept"
        )

    def test_idempotent(self) -> None:
        once = canon("void (*)(int) __attribute__((cdecl))")
        assert canon(once) == once

    def test_redundant_leading_and_trailing_spelling_deduplicates(self) -> None:
        # An (unrealistic, but defensive) input carrying BOTH spellings at
        # once must not duplicate the keyword -- the existing leading one
        # wins and the trailing attribute is simply dropped.
        assert canon("void (__cdecl *)(int) __attribute__((cdecl))") == canon(
            "void (__cdecl *)(int)"
        )

    def test_return_type_containing_convention_keyword_as_substring(self) -> None:
        # A return type that merely CONTAINS a convention keyword as a
        # substring of an unrelated identifier (e.g. "my__cdecl_result")
        # must not be mistaken for a declarator that already has one --
        # the trailing attribute must still be injected as the
        # declarator's own leading keyword, not silently dropped.
        assert canon("my__cdecl_result (*)(int) __attribute__((stdcall))") == canon(
            "my__cdecl_result (__stdcall *)(int)"
        )
        assert canon("my__cdecl_result (*)(int) __attribute__((stdcall))") != canon(
            "my__cdecl_result (*)(int)"
        )


class TestLeadingGlobalScopeQualifierIsPreserved:
    """An explicit leading ``::`` (forcing global-namespace lookup) must
    NOT be stripped -- a nineteenth-round attempt to strip it
    unconditionally was reverted in the twenty-first round once direct
    compilation showed Clang's own ``qualType`` can legitimately print
    the BARE, unqualified spelling for a type that resolves to a
    locally-shadowing entity distinct from the true global one a sibling
    declaration prints WITH the leading ``::`` -- so stripping it can
    silently merge two non-interchangeable types. This primitive has no
    scope-tree information to tell the two cases apart, so the leading
    ``::`` -- Clang's own signal for the distinction -- must survive."""

    def test_leading_qualifier_preserved(self) -> None:
        assert canon("::dep::Thing *") == "::dep::Thing *"

    def test_leading_qualifier_distinguishes_from_unqualified(self) -> None:
        # The two spellings must NOT collapse -- they can name genuinely
        # different, locally-shadowing entities.
        assert canon("::dep::Thing *") != canon("dep::Thing *")

    def test_template_argument_qualifier_preserved(self) -> None:
        assert canon("Box<::dep::Thing>") != canon("Box<dep::Thing>")

    def test_callback_parameter_qualifier_preserved(self) -> None:
        assert canon("void (*)(::dep::Thing*)") != canon("void (*)(dep::Thing*)")

    def test_ordinary_namespace_qualifier_unaffected(self) -> None:
        # A plain namespace-qualified name's own internal "::" was never
        # touched by the reverted fix either -- only a leading one was.
        assert canon("ns::Foo *") != canon("Foo *")

    def test_anonymous_namespace_separator_unaffected(self) -> None:
        # The "::" following "(anonymous namespace)" is a genuine,
        # load-bearing separator, not a global-scope marker -- unrelated
        # to this reverted fix, but still worth pinning here.
        assert canon("(anonymous namespace)::Foo *") == "(anonymous namespace)::Foo *"

    def test_member_pointer_qualifier_preserved(self) -> None:
        assert canon("void (::C::*)(int)") != canon("void (C::*)(int)")

    def test_idempotent(self) -> None:
        once = canon("::dep::Thing *")
        assert canon(once) == once


class TestTemplateQualifiedMemberPointerOwnCvIsDropped:
    """A nested-name-specifier's own segment can itself be a template-id
    (``C<int>::``), not only a plain identifier -- the declarator-group
    transparency test must still recognize it and find the sigil, since a
    real nested-name-specifier commonly looks like this."""

    def test_template_member_pointer_own_const_dropped(self) -> None:
        assert canon("void (C<int>::* const)(int)") == canon("void (C<int>::*)(int)")

    def test_nested_template_arguments_handled(self) -> None:
        # A template argument can itself contain another template-id --
        # the balanced-<...> scan must not stop at the first ">".
        assert canon("void (Box<Pair<int, int>>::* const)(int)") == canon(
            "void (Box<Pair<int, int>>::*)(int)"
        )

    def test_template_member_pointer_param_list_untouched(self) -> None:
        assert "(int)" in canon("void (C<int>::*)(int)")

    def test_template_argument_content_preserved(self) -> None:
        assert "C<int>" in canon("void (C<int>::*)(int)")


class TestCallingConventionDeclaratorGroupIsRecognized:
    """An MSVC/PE calling-convention keyword (``__cdecl``, ``__stdcall``,
    ...) can precede a declarator's own sigil inside its grouping parens
    -- the transparency test must still find the sigil at depth 0, and the
    convention keyword itself must survive verbatim (it is genuine,
    distinguishing content, not something this primitive erases)."""

    def test_own_const_dropped_with_calling_convention_present(self) -> None:
        assert canon("void (__cdecl * const)(int)") == canon("void (__cdecl *)(int)")

    def test_calling_convention_keyword_preserved(self) -> None:
        assert "__cdecl" in canon("void (__cdecl *)(int)")

    def test_different_calling_conventions_still_distinguish(self) -> None:
        # The convention itself is genuine ABI content -- __cdecl and
        # __stdcall are two different, non-interchangeable types.
        assert canon("void (__cdecl *)(int)") != canon("void (__stdcall *)(int)")


class TestPointerToMemberTrailingQualifiersPreserved:
    """A pointer-to-member-function's own trailing cv/ref-qualifiers (the
    ``const``/``volatile``/``&``/``&&`` that can follow its parameter
    list) qualify the POINTED-TO member function -- a genuine,
    standard-mandated discriminator, unlike the pointer's own by-value
    qualifier -- so they must survive, only reordered, never dropped."""

    def test_trailing_const_distinguishes_from_unqualified(self) -> None:
        assert canon("void (C::*)(int) const") != canon("void (C::*)(int)")

    def test_trailing_const_still_present(self) -> None:
        assert "const" in canon("void (C::*)(int) const")

    def test_trailing_qualifier_order_does_not_matter(self) -> None:
        assert canon("void (C::*)(int) const volatile") == canon(
            "void (C::*)(int) volatile const"
        )

    def test_own_by_value_cv_still_dropped_alongside_trailing_qualifier(self) -> None:
        # The pointer's own by-value cv (before the parameter list) and
        # the pointed-to function's own trailing cv (after it) are
        # independent regions -- dropping the former must not affect the
        # latter surviving.
        assert canon("void (C::* const)(int) const") == canon("void (C::*)(int) const")
        assert canon("void (C::* const)(int) const") != canon("void (C::*)(int)")

    def test_trailing_lvalue_ref_qualifier_distinguishes(self) -> None:
        assert canon("void (C::*)(int) &") != canon("void (C::*)(int)")

    def test_trailing_rvalue_ref_qualifier_distinguishes(self) -> None:
        # canonicalize_type_name spells "&&" as "& &" internally -- this
        # must not be mistaken for two separate top-level sigils, and must
        # not collide with the single-"&" lvalue-ref-qualifier case.
        assert canon("void (C::*)(int) &&") != canon("void (C::*)(int)")
        assert canon("void (C::*)(int) &&") != canon("void (C::*)(int) &")

    def test_trailing_cv_and_ref_qualifier_combine(self) -> None:
        assert canon("void (C::*)(int) const &") != canon("void (C::*)(int) const")
        assert canon("void (C::*)(int) const &") != canon("void (C::*)(int) &")

    def test_trailing_ref_qualifier_does_not_corrupt_own_sigil(self) -> None:
        # Regression pin for a self-caught bug: a trailing "&"/"&&" was
        # briefly mistaken for a NEW top-level sigil, overriding the
        # declarator's own already-found "*" and corrupting the
        # prefix/suffix split entirely.
        once = canon("void (C::*)(int) &&")
        assert canon(once) == once
        assert "(int)" in canon("void (C::*)(int) &&")
        assert "C" in canon("void (C::*)(int) &&")

    def test_noexcept_distinguishes_from_unqualified(self) -> None:
        # Regression pin: an earlier revision of the trailing-qualifier
        # fix reconstructed the whole trailing region from only cv/ref,
        # silently dropping "noexcept" -- collapsing two genuinely
        # different, non-interchangeable C++17 function-pointer types
        # into one identity, the same over-merge class this whole
        # primitive exists to prevent.
        assert canon("void (*)(int) noexcept") != canon("void (*)(int)")

    def test_noexcept_still_present(self) -> None:
        assert "noexcept" in canon("void (*)(int) noexcept")

    def test_noexcept_combines_with_trailing_cv(self) -> None:
        assert canon("void (C::*)(int) const noexcept") != canon(
            "void (C::*)(int) const"
        )
        assert canon("void (C::*)(int) const noexcept") != canon(
            "void (C::*)(int) noexcept"
        )

    def test_cv_still_order_independent_alongside_noexcept(self) -> None:
        assert canon("void (C::*)(int) const noexcept") == canon(
            "void (C::*)(int) noexcept const"
        )


class TestNoexceptSpellingsCanonicalized:
    """Since C++17, a function type's exception specification collapses
    to exactly two kinds for TYPE purposes: "non-throwing" (bare
    ``noexcept``/``noexcept(true)``) and "potentially-throwing" (no
    specifier at all, or ``noexcept(false)``) -- those pairs are the SAME
    type and must canonicalize identically, not merely both survive."""

    def test_bare_noexcept_equals_noexcept_true(self) -> None:
        assert canon("void (*)(int) noexcept") == canon("void (*)(int) noexcept(true)")

    def test_noexcept_false_equals_no_specifier(self) -> None:
        assert canon("void (*)(int) noexcept(false)") == canon("void (*)(int)")

    def test_noexcept_still_distinguishes_from_no_specifier(self) -> None:
        assert canon("void (*)(int) noexcept") != canon("void (*)(int)")
        assert canon("void (*)(int) noexcept(true)") != canon("void (*)(int)")

    def test_non_literal_noexcept_expression_left_untouched(self) -> None:
        # Evaluating an arbitrary constant expression is out of scope --
        # this must not be silently (and wrongly) treated as equivalent
        # to either canonical form.
        assert "SOME_CONSTANT" in canon("void (*)(int) noexcept(SOME_CONSTANT)")
        assert canon("void (*)(int) noexcept(SOME_CONSTANT)") != canon(
            "void (*)(int) noexcept"
        )
        assert canon("void (*)(int) noexcept(SOME_CONSTANT)") != canon("void (*)(int)")

    def test_noexcept_normalization_combines_with_trailing_cv(self) -> None:
        assert canon("void (C::*)(int) const noexcept(true)") == canon(
            "void (C::*)(int) noexcept const"
        )
        assert canon("void (C::*)(int) const noexcept(false)") == canon(
            "void (C::*)(int) const"
        )


class TestNoexceptIntegerLiteralSpellingsCanonicalized:
    """A ``noexcept`` argument is contextually converted to ``bool``, so
    ``noexcept(1)``/``noexcept(0)`` are the identical types as
    ``noexcept(true)``/``noexcept(false)`` -- confirmed both by direct
    compilation (redefinition errors) and by Clang's own ``qualType``,
    which genuinely emits these integer-literal spellings verbatim
    (``clang -Xclang -ast-dump=json``: ``void (int) noexcept(1)``)."""

    def test_noexcept_1_equals_noexcept_true(self) -> None:
        assert canon("void (*)(int) noexcept(1)") == canon(
            "void (*)(int) noexcept(true)"
        )

    def test_noexcept_0_equals_no_specifier(self) -> None:
        assert canon("void (*)(int) noexcept(0)") == canon("void (*)(int)")

    def test_noexcept_1_still_distinguishes_from_no_specifier(self) -> None:
        assert canon("void (*)(int) noexcept(1)") != canon("void (*)(int)")

    def test_other_integer_literal_left_untouched(self) -> None:
        # Deliberately narrow to exactly 0/1 -- any other integer
        # constant is not a spelling this module has confirmed evidence
        # for, and triggers a narrowing-conversion diagnostic in real
        # compilers, so it is left alone rather than guessed at.
        assert "noexcept(2)" in canon("void (*)(int) noexcept(2)")

    def test_idempotent_on_integer_literal_spelling(self) -> None:
        once = canon("void (*)(int) noexcept(1)")
        assert canon(once) == once


class TestCvInsideNoexceptExpressionIsNotExtracted:
    """A ``const``/``volatile`` token appearing INSIDE a non-literal
    ``noexcept(expr)``'s own argument belongs to that expression, not to
    this declarator's own trailing cv-qualifier sequence -- it must not
    be extracted, reordered, or otherwise mutated."""

    def test_const_inside_noexcept_expression_not_extracted(self) -> None:
        assert canon("void (C::*)(int) noexcept(Foo<const int>)") == canon(
            "void (C::*)(int) noexcept(Foo<const int>)"
        )

    def test_const_inside_noexcept_expression_preserved_verbatim(self) -> None:
        result = canon("void (C::*)(int) noexcept(Foo<const int>)")
        assert "Foo<const int>" in result

    def test_nested_const_does_not_merge_with_real_leading_const(self) -> None:
        # Regression pin: a plain, depth-blind search for "const" over
        # the whole trailing region wrongly found this nested one and
        # moved it to the front, producing the SAME string as a
        # genuinely different declaration with a real leading const.
        assert canon("void (C::*)(int) noexcept(Foo<const int>)") != canon(
            "void (C::*)(int) const noexcept(Foo<int>)"
        )

    def test_nested_const_does_not_distinguish_two_genuinely_equal_specs(self) -> None:
        # The nested const must not itself become a spurious real-cv
        # signal: two declarations differing only in an irrelevant
        # detail outside the nested expression should still compare via
        # their own real qualifiers, not get contaminated by it.
        assert canon("void (C::*)(int) const noexcept(Foo<const int>)") == canon(
            "void (C::*)(int) noexcept(Foo<const int>) const"
        )


class TestNestedCallbackParametersAreNormalizedRecursively:
    """A declarator's own trailing parameter list (a callback or
    member-function-pointer's parameters) is exactly as much a function's
    parameter list as this function's own top-level one -- the identical
    by-value cv rule applies to each of ITS parameters too, recursively to
    any nesting depth."""

    def test_single_nested_param_by_value_cv_dropped(self) -> None:
        assert canon("void (*)(const int)") == canon("void (*)(int)")

    def test_multiple_nested_params_by_value_cv_dropped(self) -> None:
        assert canon("void (*)(const int, int)") == canon("void (*)(int, const int)")

    def test_nested_pointee_cv_still_distinguishes(self) -> None:
        # A nested parameter's POINTEE cv is a genuine, standard-mandated
        # discriminator, same as at the top level -- must not be dropped.
        assert canon("void (*)(char *)") != canon("void (*)(const char *)")

    def test_variadic_marker_untouched(self) -> None:
        assert canon("void (*)(int, ...)") == canon("void (*)(const int, ...)")
        assert "..." in canon("void (*)(int, ...)")

    def test_empty_and_void_param_lists_unify(self) -> None:
        # An empty parameter list and a bare "void" are the identical
        # "no parameters" adjusted type -- must canonicalize identically,
        # not merely both survive unchanged.
        assert canon("void (*)()") == canon("void (*)(void)") == "void ( * )()"

    def test_doubly_nested_callback_normalized(self) -> None:
        # A callback parameter that itself takes a callback parameter --
        # the recursion must reach the innermost level too.
        assert canon("void (*)(void (*)(const int))") == canon(
            "void (*)(void (*)(int))"
        )


class TestNestingDepthIsBounded:
    """Codex review, PR #952: the recursion above terminates eventually
    (each call operates on a strictly shorter substring) but not before
    exhausting Python's call stack for a deep-enough input -- an
    adversarial/corrupt snapshot's parameter type could crash a whole
    compare() with an uncaught RecursionError. A safe (unnormalized
    passthrough) fallback beyond a generous depth bound fixes the crash
    without touching any real, non-adversarial signature's result."""

    def test_pathologically_nested_callback_does_not_crash(self) -> None:
        pathological = "void (*)(" * 500 + "int" + ")" * 500
        assert canon(pathological)  # must not raise RecursionError

    def test_ordinary_nesting_is_unaffected_by_the_bound(self) -> None:
        assert canon("void (*)(void (*)(const int))") == canon(
            "void (*)(void (*)(int))"
        )


class TestIdempotence:
    """Canonicalizing an already-canonical form is a no-op -- a basic
    sanity property any normalization function should hold."""

    def test_idempotent_on_plain_type(self) -> None:
        once = canon("const int")
        assert canon(once) == once

    def test_idempotent_on_pointer_type(self) -> None:
        once = canon("int * const")
        assert canon(once) == once

    def test_idempotent_on_array_type(self) -> None:
        once = canon("const int [3]")
        assert canon(once) == once

    def test_idempotent_on_function_pointer_type(self) -> None:
        once = canon("void (* const)(int)")
        assert canon(once) == once

    def test_idempotent_on_member_pointer_type(self) -> None:
        once = canon("void (C::* const)(int)")
        assert canon(once) == once

    def test_idempotent_on_nested_callback_type(self) -> None:
        once = canon("void (*)(const int, void (*)(char *))")
        assert canon(once) == once

    def test_idempotent_on_calling_convention_type(self) -> None:
        once = canon("void (__cdecl * const)(int)")
        assert canon(once) == once

    def test_idempotent_on_trailing_member_qualifiers(self) -> None:
        once = canon("void (C::*)(int) volatile const")
        assert canon(once) == once

    def test_idempotent_on_template_qualified_member_pointer(self) -> None:
        once = canon("void (C<int>::* const)(int)")
        assert canon(once) == once

    def test_idempotent_on_noexcept(self) -> None:
        once = canon("void (C::*)(int) noexcept const")
        assert canon(once) == once

    def test_idempotent_on_noexcept_true_spelling(self) -> None:
        once = canon("void (*)(int) noexcept(true)")
        assert canon(once) == once

    def test_idempotent_on_non_literal_noexcept(self) -> None:
        once = canon("void (*)(int) noexcept(SOME_CONSTANT)")
        assert canon(once) == once

    def test_idempotent_on_void_param_list(self) -> None:
        once = canon("void (*)(void)")
        assert canon(once) == once

    def test_idempotent_on_const_inside_noexcept_expression(self) -> None:
        once = canon("void (C::*)(int) noexcept(Foo<const int>)")
        assert canon(once) == once

    def test_idempotent_on_anonymous_namespace_qualified_pointer(self) -> None:
        once = canon("(anonymous namespace)::Foo const *")
        assert canon(once) == once

    def test_idempotent_on_bare_data_member_pointer(self) -> None:
        once = canon("const int C::*")
        assert canon(once) == once


def test_module_declares_no_dependency_above_model() -> None:
    """Leaf-module contract (ADR-063 D10): ``model.signature_normalization``
    imports nothing from ``checker_types``/``diff_*``/anything above
    ``model`` — the same contract ``model.identity`` (its only caller)
    states, checked the identical way -- against the module's real
    ``import``/``from ... import`` AST nodes, not a substring scan."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(signature_normalization))
    banned_prefixes = (
        "checker_types",
        "diff_",
        "checker",
        "compare",
        "finding_identity",
    )
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
    for name in imported_names:
        bare = name.lstrip(".")
        assert not bare.startswith(banned_prefixes), name
