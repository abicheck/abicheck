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

"""``model.qualified_name_split.split_top_level_scopes`` -- the shared
bracket-depth-aware ``"::"`` splitter ``qualified_name_segments.raw_segments``
now delegates to (ADR-063 Phase 6, PDB EntityId slice). Mirrors
``qualified_name_segments.py``'s own existing coverage of the identical
algorithm (via ``raw_segments``/``segments``), since this is a mechanical
relocation, not new logic -- see that module's own tests for the broader
segment-splitting behavior this function's callers build on.
"""

from __future__ import annotations

from abicheck.model.qualified_name_split import (
    iter_top_level_chars,
    skip_template_arguments,
    split_top_level_scopes,
)


def test_empty_string_yields_no_segments() -> None:
    assert split_top_level_scopes("") == []


def test_bare_name_is_its_own_single_segment() -> None:
    assert split_top_level_scopes("Widget") == ["Widget"]


def test_simple_namespace_chain() -> None:
    assert split_top_level_scopes("ns::inner::Widget") == ["ns", "inner", "Widget"]


def test_template_arguments_are_kept_intact_on_their_own_segment() -> None:
    assert split_top_level_scopes("ns::Vector<int>") == ["ns", "Vector<int>"]


def test_scope_separator_inside_template_arguments_is_not_a_split_point() -> None:
    assert split_top_level_scopes("ns::Map<std::pair<int, int>>::iterator") == [
        "ns",
        "Map<std::pair<int, int>>",
        "iterator",
    ]


def test_nested_template_depth_is_tracked_correctly() -> None:
    """A closing '>>' at the end of a doubly-nested template must not be
    mistaken for a single-level close -- depth must reach exactly 0 only
    after both '>' characters are consumed."""
    assert split_top_level_scopes("A<B<C>>::D") == ["A<B<C>>", "D"]


def test_leading_and_trailing_scope_separators_produce_no_empty_segments() -> None:
    """A leading '::' (global-scope qualification) or a stray trailing one
    must not manufacture an empty segment a caller could mistake for a
    real (if empty-named) scope."""
    assert split_top_level_scopes("::ns::Widget") == ["ns", "Widget"]
    assert split_top_level_scopes("ns::Widget::") == ["ns", "Widget"]


def test_whitespace_around_a_segment_is_stripped() -> None:
    assert split_top_level_scopes("ns :: Widget") == ["ns", "Widget"]


def test_unbalanced_closing_angle_bracket_does_not_underflow_depth() -> None:
    """A stray '>' with no matching '<' (malformed input, e.g. a comparison
    operator leaking into what's assumed to be a pure name) must not drive
    depth negative and start treating a REAL top-level '::' after it as
    still nested."""
    assert split_top_level_scopes("A>::B") == ["A>", "B"]


def _top_level(text: str) -> str:
    """The characters `iter_top_level_chars` yields, joined back into a
    string -- convenient for asserting what survives the scan."""
    return "".join(ch for _, ch in iter_top_level_chars(text))


class TestIterTopLevelChars:
    """`iter_top_level_chars` -- the bracket-KIND-aware-stack, quote-aware
    primitive `diff_helpers.depth_aware_bare_name` uses for its top-level
    `"::"` scope split (Codex review on PR #1041, several rounds).
    `compare.opaque_types` no longer needs a whole-text sigil scan of its
    own for indirection classification -- that question is now answered
    occurrence-relative instead (see `_occurrence_is_indirect`'s own
    docstring for why a whole-text scan was the wrong question)."""

    def test_a_bare_string_is_returned_whole(self) -> None:
        assert _top_level("Handle") == "Handle"

    def test_parenthesized_and_bracketed_content_is_skipped(self) -> None:
        # Everything from "<" through its matching ">" is nested (the "S"
        # itself sits before the "<" and so is still genuinely top level).
        assert _top_level("S<(N > 0), &h>") == "S"
        assert _top_level("S<arr[1 > 0], &h>") == "S"

    def test_a_quoted_literal_is_skipped_including_its_delimiters(self) -> None:
        assert _top_level("S<'>', &h>") == "S"

    def test_a_backslash_escaped_quote_does_not_end_the_literal_early(self) -> None:
        # '\'' is a three-character char literal (escaped single quote);
        # the unescaped closing quote is the fourth character. If the
        # escape were mishandled, the literal's own "'" would end the
        # quote early and its "'" -- an indirection sigil -- would wrongly
        # surface as top level.
        assert _top_level("S<'\\'', &h>") == "S"

    def test_content_after_a_closed_bracket_or_literal_is_still_yielded(self) -> None:
        assert _top_level("S<(N > 0)> *") == "S *"
        assert _top_level("S<'x'> *") == "S *"

    def test_a_real_right_shift_inside_parens_is_not_two_template_closes(
        self,
    ) -> None:
        """`>>` inside a parenthesized non-type template argument is a
        shift/comparison operator, not two nested `<...>` closes -- the
        bracket-KIND-aware stack (mirroring `extract.
        semantic_normalizer_artifacts.has_unresolved_component`'s own
        design) must not pop a `<` that was never pushed for it. If it
        did, the trailing ` *` below would wrongly read as still nested
        instead of surfacing as real top-level indirection."""
        assert _top_level("S<(N >> 1), &h>") == "S"
        assert _top_level("S<(N >> 1), &h> *") == "S *"

    def test_a_less_than_inside_parens_is_a_real_comparison_not_a_template_open(
        self,
    ) -> None:
        assert _top_level("S<(N < 0), &h>") == "S"

    def test_tolerates_unbalanced_closing_brackets(self) -> None:
        """A stray ')'/']'/'>' with no matching opener must not raise or
        underflow the stack -- defensive floor for malformed/adversarial
        rendered text, not a shape any real declarator produces. None of
        the three is itself yielded (each is always treated as a closer,
        matched or not), but text after them is unaffected."""
        assert _top_level(")]> *") == " *"

    def test_an_unterminated_quote_consumes_the_rest_of_the_text(self) -> None:
        assert _top_level("S<'unterminated") == "S"

    def test_a_trailing_backslash_is_not_followed_by_an_out_of_range_index(
        self,
    ) -> None:
        """A backslash as the very last character inside an (unterminated)
        quoted literal must not attempt to skip past the end of *text*."""
        assert _top_level("'\\") == ""


class TestSkipTemplateArguments:
    """`skip_template_arguments` -- the sibling stack loop
    `compare.opaque_types._occurrence_is_indirect` uses to skip a matched
    type name's own `<...>` template arguments as one unit (Codex review
    on PR #1041)."""

    def test_a_non_angle_character_is_returned_unchanged(self) -> None:
        assert skip_template_arguments("Handle", 0) == 0
        assert skip_template_arguments("Handle *", 6) == 6

    def test_a_position_past_the_end_is_returned_unchanged(self) -> None:
        assert skip_template_arguments("Handle", 6) == 6

    def test_skips_a_simple_template_argument_list(self) -> None:
        text = "Box<int> *"
        assert skip_template_arguments(text, 3) == 8
        assert text[8] == " "

    def test_skips_nested_parens_and_brackets_inside_the_arguments(self) -> None:
        """The bracket-KIND-aware stack must treat a nested `(...)`/`[...]`
        as its own group, not mistake either for a template close."""
        text = "Box<void (*)(int[3])> *"
        end = skip_template_arguments(text, 3)
        assert text[end:] == " *"

    def test_skips_a_quoted_literal_inside_the_arguments(self) -> None:
        text = "S<'>', &h> *"
        end = skip_template_arguments(text, 1)
        assert text[end:] == " *"

    def test_a_backslash_escaped_quote_inside_the_arguments_does_not_end_it_early(
        self,
    ) -> None:
        text = "S<'\\'', &h> *"
        end = skip_template_arguments(text, 1)
        assert text[end:] == " *"

    def test_a_real_right_shift_inside_parens_is_not_two_closes(self) -> None:
        text = "S<(N >> 1), &h> *"
        end = skip_template_arguments(text, 1)
        assert text[end:] == " *"

    def test_a_less_than_inside_parens_is_a_real_comparison_not_a_template_open(
        self,
    ) -> None:
        text = "S<(N < 0), &h> *"
        end = skip_template_arguments(text, 1)
        assert text[end:] == " *"

    def test_skips_a_nested_template_argument_list(self) -> None:
        """A genuinely nested `<...>` template argument (as opposed to a
        real `>>` shift/comparison operator inside parens) must still push
        its own stack level, so the outer template's own close isn't
        mistaken for the inner one's."""
        text = "Box<Inner<int>> *"
        end = skip_template_arguments(text, 3)
        assert text[end:] == " *"

    def test_an_unterminated_template_argument_list_consumes_the_rest(self) -> None:
        assert skip_template_arguments("Box<unterminated", 3) == len("Box<unterminated")
