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

"""``policy.selectors_namespace_glob`` (ADR-063 D10, implementation plan
Phase 9) -- the fnmatch/regex/namespace-glob compilation and matching
machinery behind ``SelectorSet``'s ``symbol_pattern``/``type_pattern``/
``member_name``/``source_location``/``namespace``/``entity_namespace``/
``cause_namespace`` selectors, split into its own leaf module purely to keep
``selectors.py`` under the architecture gate's 800-line ceiling.

Exercised directly here (mirroring ``tests/test_selectors.py`` for the
sibling module) rather than only indirectly through
``SelectorSet``/``Suppression``, so this module carries its own real
detector-mutation coverage for the mutation-testing lane's own contract
(``tests/test_mutation_workflow_contract.py``), and so a regression in the
matcher itself -- not just in how a caller wires it up -- has a test that
fails close to the change. ``tests/test_suppression_edge_cases.py`` still
covers a couple of the trickier real-world globstar/backtracking-safety
scenarios end to end through ``Suppression`` itself; this file is the
primitive-level counterpart, not a replacement for those.
"""

from __future__ import annotations

import re

import pytest

from abicheck.policy.selectors_namespace_glob import (
    _bracket_class_end,
    _collapsed_namespace_segments,
    _compile_glob,
    _compile_namespace_glob,
    _compile_pattern,
    _has_wildcard_char,
    _SegmentGlobMatcher,
    _split_namespace_segments,
    _translate_namespace_glob,
)


class TestCompilePatternAndGlob:
    def test_compile_pattern_none_returns_none(self) -> None:
        assert _compile_pattern(None, "symbol_pattern") is None

    def test_compile_pattern_uses_fullmatch_semantics(self) -> None:
        compiled = _compile_pattern("foo.*", "symbol_pattern")
        assert compiled is not None
        assert compiled.fullmatch("foobar")
        assert not compiled.fullmatch("xfoobar")

    def test_compile_pattern_raises_valueerror_on_malformed_regex(self) -> None:
        with pytest.raises(ValueError, match="symbol_pattern"):
            _compile_pattern("(unclosed", "symbol_pattern")

    def test_compile_glob_none_returns_none(self) -> None:
        assert _compile_glob(None, "source_location") is None

    def test_compile_glob_uses_fnmatch_semantics(self) -> None:
        compiled = _compile_glob("*/internal/*", "source_location")
        assert compiled is not None
        assert compiled.match("/repo/internal/x.h")
        assert not compiled.match("/repo/public/x.h")


class TestNamespaceSegmentSplitting:
    def test_split_on_double_colon(self) -> None:
        assert _split_namespace_segments("a::b::c") == ["a", "b", "c"]

    def test_bracket_class_containing_literal_double_colon_is_not_split(self) -> None:
        # A "::" inside a genuine fnmatch bracket class is literal content,
        # not a segment boundary.
        assert _split_namespace_segments("ns::[!::]*") == ["ns", "[!::]*"]

    def test_unmatched_bracket_is_literal_not_a_class(self) -> None:
        assert _bracket_class_end("foo[bar", 3) == -1

    def test_negated_class_with_immediate_closing_bracket_as_literal_member(self) -> None:
        # "[!]" immediately followed by "]" -- the "]" right after the
        # negation is a literal class member, not the closer.
        assert _bracket_class_end("[!]abc]", 0) == 6

    def test_split_falls_back_to_literal_on_an_unclosed_bracket(self) -> None:
        # No genuine bracket class here (never closed) -- "::" inside it is
        # still a real segment boundary, unlike the closed-class case above.
        assert _split_namespace_segments("ns::[abc") == ["ns", "[abc"]

    def test_collapse_adjacent_globstars(self) -> None:
        assert _collapsed_namespace_segments("a::**::**::b") == ["a", "**", "b"]

    def test_has_wildcard_char_detects_star_question_and_bracket(self) -> None:
        assert _has_wildcard_char("foo*")
        assert _has_wildcard_char("foo?")
        assert _has_wildcard_char("foo[0-9]")
        assert not _has_wildcard_char("foo")
        # An unmatched leading '[' is literal, not a wildcard.
        assert not _has_wildcard_char("foo[bar")


class TestTranslateAndCompileNamespaceGlob:
    def test_plain_literal_pattern_matches_exactly(self) -> None:
        regex = _translate_namespace_glob("ns::detail")
        assert re.fullmatch(regex, "ns::detail")
        assert not re.fullmatch(regex, "ns::detail::x")

    def test_globstar_matches_zero_or_more_segments(self) -> None:
        regex = _translate_namespace_glob("a::**::b")
        assert re.fullmatch(regex, "a::b")
        assert re.fullmatch(regex, "a::x::b")
        assert re.fullmatch(regex, "a::x::y::b")
        assert not re.fullmatch(regex, "a::c")

    def test_standalone_globstar_matches_anything(self) -> None:
        regex = _translate_namespace_glob("**")
        assert re.fullmatch(regex, "oneapi::dal::foo")
        assert re.fullmatch(regex, "")

    def test_trailing_globstar_after_wildcard_requires_the_separator(self) -> None:
        # Regression: "foo**::**" must not match bare "foobar" -- there is
        # no "::" anywhere in that string (real fnmatch.translate agrees).
        regex = _translate_namespace_glob("foo**::**")
        assert not re.fullmatch(regex, "foobar")
        assert re.fullmatch(regex, "foo::bar")

    def test_compile_namespace_glob_none_returns_none(self) -> None:
        assert _compile_namespace_glob(None, "namespace") is None

    def test_compile_namespace_glob_returns_a_segment_glob_matcher(self) -> None:
        matcher = _compile_namespace_glob("ns::detail::**", "namespace")
        assert isinstance(matcher, _SegmentGlobMatcher)
        assert matcher.match("ns::detail::x::y")
        assert not matcher.match("ns::pub::x")


class TestSegmentGlobMatcherAncestorWalk:
    def test_matches_any_ancestor_walks_up_the_namespace_chain(self) -> None:
        matcher = _compile_namespace_glob("ns::detail", "namespace")
        assert matcher is not None
        assert matcher.matches_any_ancestor("ns::detail::x::Foo::bar")
        assert not matcher.matches_any_ancestor("ns::pub::x::Foo::bar")

    def test_multiple_globstars_use_the_dp_path_and_still_match(self) -> None:
        # Two or more standalone globstars route through the non-
        # backtracking DP matcher rather than the single-regex fast path --
        # exercise that path directly rather than only trusting the fast
        # path's own docstring.
        matcher = _compile_namespace_glob("**::a::**::b", "namespace")
        assert matcher is not None
        assert matcher.match("x::a::y::b")
        assert matcher.match("a::b")
        assert not matcher.match("x::a::y::c")

    def test_trailing_globstar_after_wildcarded_run_uses_the_tail_regex(self) -> None:
        # A pattern with 2+ globstars AND a trailing "**" immediately
        # preceded by a wildcarded run (here "c*") is the one shape
        # _SegmentGlobMatcher special-cases into its own `_tail` regex
        # instead of the ordinary run/globstar DP walk -- exercised via
        # both match() and matches_any_ancestor() (the tail branch each
        # method itself needs, not just the run/globstar path the simpler
        # multi-globstar test above covers).
        matcher = _compile_namespace_glob("a::b*::**::a::c*::**", "namespace")
        assert matcher is not None
        assert matcher.match("a::bx::a::cy::zz")
        assert not matcher.match("a::bx::a::nope")
        assert matcher.matches_any_ancestor("a::bx::a::cy::extra")
        assert not matcher.matches_any_ancestor("a::bx::a::nope::extra")

    def test_multiple_globstars_reject_large_non_matching_input_quickly(self) -> None:
        """The DP rewrite's whole reason to exist: this must not hang."""
        matcher = _compile_namespace_glob(
            "**::a::**::a::**::a::**::a::**::a::z", "namespace"
        )
        assert matcher is not None
        name = "::".join(["seg"] * 200)
        assert matcher.match(name) is False
