# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Property tests for the namespace-glob matcher (``policy/namespace_glob.py``).

Added when the matcher moved out of ``suppression.py`` into its own module
(ADR-061 D5). Root ``AGENTS.md``'s "Primitive-level property tests" section
asks for exactly this whenever a reusable general-purpose primitive gets a
standalone home: a small property-test class stating the primitive's contract
as invariants, decoupled from any one caller's domain logic.

The central property here is a **differential** one, available because this
module carries two spellings of one semantics: ``_translate_namespace_glob``
compiles a pattern to a regex, while ``_SegmentGlobMatcher`` walks it segment
by segment without backtracking. The matcher's own docstring records why the
second exists — a pattern chaining several non-adjacent globstars took over
8 seconds for ``re`` to reject — and claims it "exactly reproduc[es] this
module's pre-globstar-rewrite behavior". That claim is what these tests pin,
and it matters because every caller uses only the matcher: nothing in
production would notice the two disagreeing until a suppression rule quietly
stopped matching.

**Be precise about the reach of that oracle, because "two implementations"
overstates it.** The two halves are independent only in their *structural*
logic — run-splitting and the reachability walk on one side, whole-pattern
regex assembly on the other. They share the character-level helpers
(``_fnmatch_segment_regex``, ``_bracket_class_end``, ``_has_wildcard_char``),
so a change there moves both halves together and this sweep stays green.
Verified by seeding drift three ways: a change to the regex half's own body
and a change to the walk's own body each fail
``test_every_pattern_name_pair_in_the_small_domain_agrees``, while a change
to the shared ``_fnmatch_segment_regex`` does not. The shared helpers
therefore need their own direct assertions, which is what
``tests/test_suppression_edge_cases.py`` provides — it imports
``_fnmatch_segment_regex`` and ``_SEGMENT_RE_WRAPPER`` and checks them
head-on. The class below adds contract invariants that hold without
reference to the regex half at all, for the same reason.

The domain is enumerated exhaustively rather than sampled: a two-letter
alphabet with every wildcard construct, at one to three segments, is ~2000
pattern/name pairs and runs in well under a second, which is a stronger
guarantee than randomised sampling over the same space.
"""

from __future__ import annotations

import itertools
import re

import pytest

from abicheck.policy.namespace_glob import (
    _collapsed_namespace_segments,
    _SegmentGlobMatcher,
    _split_namespace_segments,
    _translate_namespace_glob,
)

#: Every construct the matcher distinguishes, over a deliberately tiny
#: alphabet: two literals, a single-char wildcard, an intra-segment star, and
#: the globstar whose cross-``::`` semantics are the reason this module exists.
_SEGMENT_VOCAB = ("a", "b", "*", "**", "?")
_NAME_VOCAB = ("a", "b")


def _patterns(max_segments: int = 3) -> list[str]:
    out: list[str] = []
    for n in range(1, max_segments + 1):
        out.extend(
            "::".join(combo) for combo in itertools.product(_SEGMENT_VOCAB, repeat=n)
        )
    return out


def _names(max_segments: int = 3) -> list[str]:
    out: list[str] = []
    for n in range(1, max_segments + 1):
        out.extend(
            "::".join(combo) for combo in itertools.product(_NAME_VOCAB, repeat=n)
        )
    return out


def _matcher(pattern: str) -> _SegmentGlobMatcher:
    return _SegmentGlobMatcher(pattern, _collapsed_namespace_segments(pattern))


def _regex_match(pattern: str, name: str) -> bool:
    return bool(re.compile(_translate_namespace_glob(pattern)).match(name))


class TestMatcherAgreesWithTheRegexItReplaced:
    """The differential contract: the fast walk and the regex translation are
    two spellings of one semantics, so they must answer identically.

    Catches drift in either half's own structural logic; blind to a change in
    the character-level helpers they share (see this module's docstring).
    """

    def test_every_pattern_name_pair_in_the_small_domain_agrees(self) -> None:
        names = _names()
        disagreements = [
            (pattern, name, walk, rx)
            for pattern in _patterns()
            for name in names
            for walk, rx in [
                (_matcher(pattern).match(name), _regex_match(pattern, name))
            ]
            if walk != rx
        ]
        assert disagreements == [], (
            "_SegmentGlobMatcher and _translate_namespace_glob disagree on "
            f"{len(disagreements)} pair(s), e.g. {disagreements[:5]} — the "
            "matcher exists to be a faster spelling of the same semantics, so "
            "a divergence here is a real behaviour change in suppression "
            "matching that no caller would notice (every caller uses only the "
            "matcher)"
        )

    def test_the_domain_actually_exercises_both_answers(self) -> None:
        """A differential test that only ever compares False to False proves
        nothing. Pin that the enumeration produces a real mix."""
        results = [_matcher(p).match(n) for p in _patterns() for n in _names()]
        assert any(results), "no pattern matched anything — domain is degenerate"
        assert not all(results), (
            "every pattern matched everything — domain is degenerate"
        )


class TestMatcherContract:
    """Invariants that hold for any pattern, independent of the regex half."""

    @pytest.mark.parametrize("pattern", ["a", "a::b", "a::b::c"])
    def test_a_wildcard_free_pattern_matches_exactly_itself(self, pattern: str) -> None:
        matcher = _matcher(pattern)
        assert matcher.match(pattern)
        for other in _names():
            if other != pattern:
                assert not matcher.match(other), (
                    f"literal pattern {pattern!r} matched unrelated {other!r}"
                )

    def test_matching_is_deterministic_and_free_of_compile_state(self) -> None:
        """The matcher caches compiled runs; two calls, and two separately
        constructed matchers, must still answer identically."""
        for pattern in _patterns(2):
            for name in _names(2):
                first = _matcher(pattern)
                assert first.match(name) == first.match(name)
                assert first.match(name) == _matcher(pattern).match(name)

    def test_globstar_crosses_a_separator_where_a_bare_star_run_still_matches(
        self,
    ) -> None:
        """The distinction this module exists for, stated directly rather than
        left implicit in the differential sweep."""
        assert _matcher("a::**::d").match("a::b::c::d")
        assert _matcher("a::**").match("a::b::c")
        # A trailing globstar is not required to consume anything.
        assert _matcher("a::**").match("a")

    def test_a_pattern_never_matches_a_strict_prefix_of_its_literal_tail(self) -> None:
        assert not _matcher("a::b::c").match("a::b")
        assert not _matcher("a::b").match("a")

    def test_matches_any_ancestor_is_implied_by_matching_an_ancestor(self) -> None:
        """``matches_any_ancestor`` must agree with running ``match`` against
        each ancestor by hand — the caller-visible contract, checked against an
        independent formulation rather than the method's own walk."""
        for pattern in _patterns(2):
            matcher = _matcher(pattern)
            for name in _names():
                segments = _split_namespace_segments(name)
                by_hand = any(
                    matcher.match("::".join(segments[: i + 1]))
                    for i in range(len(segments))
                )
                assert matcher.matches_any_ancestor(name) == by_hand, (
                    f"matches_any_ancestor disagreed with a per-ancestor match "
                    f"for pattern {pattern!r} against {name!r}"
                )
