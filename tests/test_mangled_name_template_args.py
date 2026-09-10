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

"""Primitive-level edge-case tests for
:mod:`abicheck.model.mangled_name_template_args` -- the length-prefixed
source-name/template-argument-skip primitives and the recursive
type-candidate identifier extraction ``mangled_name.py``'s
``itanium_special_name_owner_identifiers`` builds on.

``test_mangled_name.py`` already exercises this module's public behaviour
end-to-end through real ``_ZTV``/``_ZTI``/``_ZTT`` mangled symbols (the
success paths any real compiler-emitted symbol reaches). This file adds the
malformed-input/fail-safe branches every function here documents in its own
docstring ("a wrong guess never fabricates a candidate, it can only omit
one") but that a real, well-formed mangled symbol never triggers -- ABI
tags, doubly-nested template arguments, non-type (literal) template
arguments, and truncated/malformed encodings. Per this file's own
docstring, none of these functions raises on malformed input; each is
tested directly, at the primitive level, per this repo's "primitive-level
property tests" convention (CLAUDE.md) rather than only through the
highest-level caller.
"""

from __future__ import annotations

from abicheck.model.mangled_name_template_args import (
    _collect_nested_name_candidates,
    collect_type_candidate_identifiers,
    read_length_prefixed_name,
    skip_template_args,
)


class TestReadLengthPrefixedName:
    def test_a_length_prefix_larger_than_the_remaining_string_is_rejected(self) -> None:
        # "99" as a length prefix on a 5-char string can never be satisfied --
        # rejected outright rather than reading past the string's own end.
        assert read_length_prefixed_name("99abc", 0) == (None, 0)


class TestSkipTemplateArgs:
    def test_a_malformed_length_prefix_mid_scan_is_rejected(self) -> None:
        # The lone digit "9" claims a 9-char name than the 1 remaining
        # character can supply.
        assert skip_template_args("I9E", 0) is None

    def test_a_non_type_literal_argument_is_skipped_as_a_unit(self) -> None:
        # `Array<4>` -> `ILi4EE` (this module's own docstring example): the
        # literal's `4` must not be misread as a length prefix, and its own
        # `E` must not be misread as the outer list's closing `E`.
        assert skip_template_args("ILi4EE", 0) == 6

    def test_an_unterminated_literal_argument_is_rejected(self) -> None:
        assert skip_template_args("ILi4", 0) is None

    def test_running_off_the_end_before_the_matching_e_is_rejected(self) -> None:
        assert skip_template_args("I3abc", 0) is None


class TestCollectNestedNameCandidates:
    """``_collect_nested_name_candidates`` parses one Itanium ``N...E``
    nested-name production found *inside* a template-argument span."""

    def test_a_cv_qualifier_before_the_first_component_is_skipped(self) -> None:
        # `NK3FooE` -- a `const`-qualified nested name; the `K` must not be
        # misread as (or block reading) the first length-prefixed component.
        assert _collect_nested_name_candidates("NK3FooE", 0, 7) == (
            frozenset({"Foo"}),
            7,
        )

    def test_an_operator_or_ctor_component_is_unmodelled_and_rejected(self) -> None:
        # A non-digit, non-`E` byte where a length-prefixed component is
        # expected (an operator/constructor/substitution component this
        # parser does not model) -- rejected, not guessed at.
        assert _collect_nested_name_candidates("Nx", 0, 2) is None

    def test_a_malformed_component_length_is_rejected(self) -> None:
        assert _collect_nested_name_candidates("N9xE", 0, 4) is None

    def test_an_abi_tag_is_folded_into_the_component_it_tags(self) -> None:
        # `N3FooB3xxxE` -- `Foo` carrying a GNU ABI tag (`B<tag>`) directly
        # after its name, before any `E`/template-argument list.
        result = _collect_nested_name_candidates("N3FooB3xxxE", 0, 11)
        assert result is not None
        fused, after = result
        assert fused == {"Foo[abi:xxx]"}
        assert after == 11

    def test_a_malformed_abi_tag_stops_only_the_tag_loop(self) -> None:
        # `N3FooBx` -- `Foo` followed by a `B` that isn't a valid
        # length-prefixed tag: the tag loop drops it (leaving `name`/`j`
        # unchanged) rather than fabricating one, but the outer scan then
        # finds the unconsumed `B` itself unparseable (not a digit, not
        # `E`) and rejects the whole production -- same fail-safe outcome
        # as the sibling "unmodelled component" case above, reached via a
        # different path.
        assert _collect_nested_name_candidates("N3FooBx", 0, 7) is None

    def test_an_unterminated_nested_template_argument_list_is_rejected(self) -> None:
        # `N3FooI...` -- the component's own directly-attached template-arg
        # list never closes.
        assert _collect_nested_name_candidates("N3FooI", 0, 6) is None

    def test_running_off_the_end_without_a_terminating_e_is_rejected(self) -> None:
        assert _collect_nested_name_candidates("N3Foo", 0, 5) is None

    def test_an_empty_nested_name_with_no_components_is_rejected(self) -> None:
        assert _collect_nested_name_candidates("NE", 0, 2) is None


class TestCollectTypeCandidateIdentifiers:
    def test_a_component_neither_the_structural_parser_understands_is_skipped_over(
        self,
    ) -> None:
        # The unmodelled `Nx...` nested-name production above (an operator/
        # ctor/substitution component) must not abort the whole scan -- it is
        # skipped, and scanning resumes and still finds the sibling `Foo`
        # length-prefixed name that follows it in the same span.
        assert collect_type_candidate_identifiers("Nx3FooE", 0, 7) == frozenset({"Foo"})

    def test_a_malformed_top_level_length_prefix_stops_the_scan(self) -> None:
        assert collect_type_candidate_identifiers("9x", 0, 2) == frozenset()

    def test_a_top_level_abi_tag_that_fails_to_parse_stops_the_tag_loop_only(
        self,
    ) -> None:
        # `3FooBx` -- `Foo` followed by a `B` that isn't a valid
        # length-prefixed tag; the malformed tag is dropped, but the `Foo`
        # name already read is still a real candidate.
        assert collect_type_candidate_identifiers("3FooBx", 0, 6) == frozenset({"Foo"})

    def test_a_doubly_nested_template_argument_recurses(self) -> None:
        # `Wrapper<Box<int>>`'s inner `Box<int>` argument, in isolation:
        # `3BoxIiE` -- a plain (non-namespaced) generic name carrying its
        # own directly-attached template-argument list, unlike every
        # `TestTemplatedOwnerHostIndependence` case in test_mangled_name.py
        # (which only nests a *qualified* `N...E` name, never a second
        # plain templated name).
        assert collect_type_candidate_identifiers("3BoxIiE", 0, 7) == frozenset({"Box"})

    def test_an_unterminated_doubly_nested_template_argument_is_dropped(self) -> None:
        # Same shape as above but the inner `Box<...>` list never closes --
        # the outer `Box` name already read is kept; the malformed nested
        # list simply contributes nothing.
        assert collect_type_candidate_identifiers("3BoxI", 0, 5) == frozenset({"Box"})

    def test_a_non_type_literal_argument_at_the_top_level_contributes_nothing(
        self,
    ) -> None:
        assert collect_type_candidate_identifiers("Li4E", 0, 4) == frozenset()

    def test_an_unterminated_top_level_literal_argument_is_dropped(self) -> None:
        assert collect_type_candidate_identifiers("Li4", 0, 3) == frozenset()
