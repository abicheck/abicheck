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

"""The header-exclusion and receipt primitives, tested directly.

Per AGENTS.md's "Primitive-level property tests": these are reusable,
general-purpose helpers, and a test that only reaches them through the
highest-level caller both misses their contract and drags a compiler into
every assertion. The end-to-end behaviour lives in
``test_release_header_exclusions.py`` (whose compiling classes carry the
``integration`` marker, so they do not run on the unit lane at all); this
module is what states what each primitive promises, with no toolchain.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from abicheck.extract.header_exclusions import (
    apply_header_exclusions,
    apply_header_exclusions_to_inputs,
    unmatched_exclusion_patterns,
)
from abicheck.model.header_exclusion_record import (
    DESCRIPTOR_MATCHING,
    GLOB_MATCHING,
    canonical_exclusion_identity,
    comparison_exclusion_identity,
    release_exclusion_identity,
)
from abicheck.workflows.header_exclusion_audit import unmatched_exclusion_warning


class TestUnmatchedExclusionPatterns:
    """ "Which rules matched nothing" -- the input to the one-per-run warning."""

    def test_no_patterns_is_no_answer(self) -> None:
        assert unmatched_exclusion_patterns([Path("a.h")], []) == []

    def test_a_matching_pattern_is_not_reported(self) -> None:
        assert unmatched_exclusion_patterns([Path("a.h"), Path("b.h")], ["b.h"]) == []

    def test_only_the_unmatched_ones_are_reported(self) -> None:
        assert unmatched_exclusion_patterns(
            [Path("a.h"), Path("b.h")], ["b.h", "zz.h", "qq.h"]
        ) == ["qq.h", "zz.h"]

    def test_the_answer_is_canonical(self) -> None:
        """Sorted and de-duplicated: the rules are a set, so two runs that
        stated the same thing must not read differently."""
        assert unmatched_exclusion_patterns(
            [Path("a.h")], ["zz.h", "qq.h", "zz.h"]
        ) == ["qq.h", "zz.h"]

    def test_an_empty_pattern_is_ignored(self) -> None:
        """An empty string matches nothing and narrows nothing; reporting it
        as an unmatched *rule* would be noise about a non-rule."""
        assert unmatched_exclusion_patterns([Path("a.h")], [""]) == []

    def test_a_glob_that_matches_is_not_reported(self) -> None:
        """``**/<dir>/*`` matches at any depth -- note it needs a parent
        component, so ``d/impl.h`` with no ancestor is *not* a match and
        ``x/d/impl.h`` is. Both directions asserted, since getting this
        backwards is how a "the rule matched nothing" report would be wrong
        about a rule that did."""
        assert unmatched_exclusion_patterns([Path("x/d/impl.h")], ["**/d/*"]) == []
        assert unmatched_exclusion_patterns([Path("d/impl.h")], ["**/d/*"]) == [
            "**/d/*"
        ]

    def test_it_answers_against_the_same_set_the_filter_uses(
        self, tmp_path: Path
    ) -> None:
        """The invariant that makes this trustworthy, stated as a property
        over a directory operand rather than assumed: a pattern is reported
        unmatched exactly when applying it removes nothing. Sharing one
        expansion is what guarantees it -- answering "what was it tried
        against" one way when filtering and another way when reporting is
        precisely the drift this checks for."""
        (tmp_path / "sub").mkdir()
        (tmp_path / "keep.h").write_text("", encoding="utf-8")
        (tmp_path / "sub" / "drop.h").write_text("", encoding="utf-8")
        for pattern in ("drop.h", "keep.h", "**/sub/*", "nothing.h", "*.hpp"):
            filtered = apply_header_exclusions_to_inputs([tmp_path], [pattern])
            unfiltered = apply_header_exclusions_to_inputs([tmp_path], ["\0none"])
            removed_something = len(filtered) < len(unfiltered)
            reported = unmatched_exclusion_patterns([tmp_path], [pattern]) == []
            assert removed_something == reported, pattern


class TestUnmatchedExclusionWarning:
    """One warning for the whole run, or none."""

    def test_no_rules_no_warning(self) -> None:
        assert unmatched_exclusion_warning([Path("a.h")], []) is None

    def test_all_rules_matched_no_warning(self) -> None:
        assert unmatched_exclusion_warning([Path("a.h")], ["a.h"]) is None

    def test_an_unmatched_rule_is_named_once(self) -> None:
        msg = unmatched_exclusion_warning([Path("a.h")], ["zz.h"])
        assert msg is not None
        assert msg.count("zz.h") == 1
        assert msg.startswith("Warning: --exclude-header matched no header")

    def test_every_unmatched_rule_is_named_in_one_message(self) -> None:
        """One message, not one per rule -- the whole point of answering
        this once for the run."""
        msg = unmatched_exclusion_warning([Path("a.h")], ["zz.h", "qq.h"])
        assert msg is not None
        assert "zz.h" in msg
        assert "qq.h" in msg
        assert msg.count("Warning:") == 1

    def test_a_partially_matched_set_names_only_the_misses(self) -> None:
        msg = unmatched_exclusion_warning([Path("a.h")], ["a.h", "zz.h"])
        assert msg is not None
        assert "zz.h" in msg
        assert "a.h" not in msg.replace("--exclude-header", "")


class TestCanonicalExclusionIdentity:
    """The run's exclusion rules as one stable configuration identity."""

    def test_no_rules_is_the_empty_identity(self) -> None:
        assert canonical_exclusion_identity([]) == ""

    def test_order_and_repetition_do_not_change_it(self) -> None:
        base = canonical_exclusion_identity(["a.h", "b.h"])
        for spelling in (["b.h", "a.h"], ["a.h", "a.h", "b.h"], ["b.h", "b.h", "a.h"]):
            assert canonical_exclusion_identity(spelling) == base

    def test_membership_does_change_it(self) -> None:
        """The vacuity guard: a canonicalizer collapsing everything to one
        value passes every equivalence above."""
        assert canonical_exclusion_identity(["a.h"]) != canonical_exclusion_identity(
            ["b.h"]
        )
        assert canonical_exclusion_identity(["a.h"]) != canonical_exclusion_identity(
            ["a.h", "b.h"]
        )

    def test_the_matching_rule_is_part_of_the_identity(self) -> None:
        assert canonical_exclusion_identity(
            ["include/foo.h"], GLOB_MATCHING
        ) != canonical_exclusion_identity(["include/foo.h"], DESCRIPTOR_MATCHING)

    def test_an_unrecognised_rule_normalizes_rather_than_being_trusted(self) -> None:
        """Recognising a name is not implementing it -- an unknown rule must
        not be echoed back as if this build could reason about it."""
        assert canonical_exclusion_identity(["a.h"], "regex").startswith("unknown:")

    def test_empty_patterns_are_dropped(self) -> None:
        assert canonical_exclusion_identity(
            ["", "a.h"]
        ) == canonical_exclusion_identity(["a.h"])


class _Snap:
    """The two fields ``comparison_exclusion_identity`` reads."""

    def __init__(self, patterns: tuple[str, ...], matching: str = GLOB_MATCHING):
        self.excluded_header_patterns = patterns
        self.excluded_header_matching = matching


class TestComparisonExclusionIdentity:
    """One identity for a comparison, read off the snapshots."""

    def test_both_sides_agree_is_that_value(self) -> None:
        both = _Snap(("a.h",))
        assert comparison_exclusion_identity(both, both) == (
            canonical_exclusion_identity(["a.h"])
        )

    def test_neither_side_narrowed_is_empty(self) -> None:
        assert comparison_exclusion_identity(_Snap(()), _Snap(())) == ""

    def test_a_declared_absent_baseline_falls_back_to_new(self) -> None:
        assert comparison_exclusion_identity(None, _Snap(("a.h",))) == (
            canonical_exclusion_identity(["a.h"])
        )

    def test_an_old_only_record_is_still_reported(self) -> None:
        """A NEW that recorded nothing must not erase what OLD recorded --
        a stored baseline carries its own rules and this run may have
        loaded rather than extracted the new side.

        It is reported as the *one-sided* narrowing it is, not folded onto
        the symmetric pair's identity: under ``diagnostic_comparison`` an
        asymmetric pair does reach report generation, and the two compared
        different surfaces (see
        ``TestAnAsymmetricComparisonKeepsBothSides``)."""
        old_only = comparison_exclusion_identity(_Snap(("a.h",)), _Snap(()))
        assert canonical_exclusion_identity(["a.h"]) in old_only
        assert old_only != canonical_exclusion_identity(["a.h"])

    def test_the_recorded_matching_rule_is_carried(self) -> None:
        snap = _Snap(("a.h",), DESCRIPTOR_MATCHING)
        assert comparison_exclusion_identity(snap, snap).startswith("abicc:")


class TestApplyHeaderExclusionsIsUnchangedWithoutPatterns:
    """The no-flag path stays byte-identical, which is what keeps every warm
    cache entry valid (see ``apply_header_exclusions_to_inputs``' docstring)."""

    def test_no_patterns_returns_the_same_list_object(self) -> None:
        headers = [Path("a.h"), Path("b.h")]
        assert apply_header_exclusions_to_inputs(headers, []) is headers

    def test_no_patterns_does_not_expand_a_directory(self, tmp_path: Path) -> None:
        (tmp_path / "x.h").write_text("", encoding="utf-8")
        assert apply_header_exclusions_to_inputs([tmp_path], []) == [tmp_path]

    def test_the_bare_name_spelling_matches_on_every_platform(self) -> None:
        """The one spelling of a pattern that is separator-independent, and
        so the only one this asserts unconditionally."""
        headers = [Path("/inc/a.h"), Path("/inc/b.h")]
        assert apply_header_exclusions(headers, ["b.h"]) == [Path("/inc/a.h")]
        assert apply_header_exclusions(headers, ["nothing.h"]) == headers

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "the path-shaped spellings are POSIX-shaped: apply_header_exclusions "
            "matches against str(path), which is backslash-separated on Windows, "
            "so a forward-slash pattern matches nothing there. Pre-existing "
            "behaviour of the primitive, not of this test -- see "
            "docs/contribute/known-gaps.md"
        ),
    )
    @pytest.mark.parametrize(
        ("pattern", "kept"),
        [
            ("/inc/b.h", [Path("/inc/a.h")]),
            ("**/inc/*", []),
        ],
    )
    def test_the_path_shaped_spellings_match_on_posix(
        self, pattern: str, kept: list[Path]
    ) -> None:
        """The full-path and ``**/``-glob spellings ``--exclude-header``'s
        help text promises. Asserted with real ``Path`` objects rather than
        rendered strings, so the comparison is not itself separator-
        dependent."""
        headers = [Path("/inc/a.h"), Path("/inc/b.h")]
        assert apply_header_exclusions(headers, [pattern]) == kept


class TestTheIdentityEncodingIsInjective:
    """Two different rule sets can never share one identity string.

    The single property the configuration digest rests on, and the one the
    task's P0-A requirement states outright: two otherwise identical runs
    with different exclusion rules must not share a digest. A comma join
    did not satisfy it -- a glob pattern is arbitrary text and may itself
    contain the separator, so ``["a", "b,c"]`` and ``["a,b", "c"]`` both
    rendered ``glob:a,b,c`` (CodeRabbit review; reproduced) and two
    genuinely different header trees were recorded as one configuration.

    Stated over a generated domain rather than as the one reported pair,
    per the bug-class contract: the oracle is set equality of the
    *canonicalized* rule sets, derived independently of the encoding under
    test.
    """

    @staticmethod
    def _canonical(patterns: list[str]) -> frozenset[str]:
        return frozenset(p for p in patterns if p)

    def test_distinct_rule_sets_never_collide(self) -> None:
        # Every arrangement of separator-bearing and plain patterns that a
        # join-based encoding conflates, plus ordinary siblings as a
        # control. Enumerated exhaustively over the small domain rather
        # than sampled, and asserted in one batch so a failure names every
        # colliding pair at once.
        candidates = [
            ["a", "b,c"],
            ["a,b", "c"],
            ["a,b,c"],
            ["a", "b", "c"],
            ['a"b'],
            ["a\\b"],
            ["a", "b"],
            ["ab"],
            ["a:b"],
            ["glob:a"],
            [],
            [""],
        ]
        seen: dict[str, frozenset[str]] = {}
        collisions = []
        for patterns in candidates:
            identity = canonical_exclusion_identity(patterns)
            canonical = self._canonical(patterns)
            if identity in seen and seen[identity] != canonical:
                collisions.append((sorted(seen[identity]), sorted(canonical), identity))
            seen.setdefault(identity, canonical)
        assert not collisions, (
            "these rule sets narrow different header trees but share one "
            f"identity: {collisions}"
        )

    def test_equal_rule_sets_still_share_one_identity(self) -> None:
        """The vacuity guard. An encoding that hashed in something unique
        per call would satisfy the property above completely and be
        useless; order and repetition must still collapse."""
        assert canonical_exclusion_identity(["b,c", "a", "a"]) == (
            canonical_exclusion_identity(["a", "b,c"])
        )

    def test_the_matching_rule_still_separates_two_readings(self) -> None:
        assert canonical_exclusion_identity(["a.h"], "glob") != (
            canonical_exclusion_identity(["a.h"], "abicc")
        )


class TestAnAsymmetricComparisonKeepsBothSides:
    """``diagnostic_comparison=True`` lets an asymmetric pair through.

    ``comparability`` refuses a pair whose two sides were narrowed
    differently, which is why this identity was originally a single value.
    That refusal is not absolute: ``checker.compare(...,
    diagnostic_comparison=True)`` is ADR-050 D2's sanctioned escape hatch
    and skips the contract check entirely, so the pair does reach report
    generation -- where reading only the non-empty side gave three
    genuinely different compared surfaces one identity (CodeRabbit review;
    reproduced).
    """

    def test_the_three_asymmetric_shapes_are_all_distinct(self) -> None:
        one_sided_old = comparison_exclusion_identity(_Snap(("a.h",)), _Snap(()))
        one_sided_new = comparison_exclusion_identity(_Snap(()), _Snap(("a.h",)))
        symmetric = comparison_exclusion_identity(_Snap(("a.h",)), _Snap(("a.h",)))
        assert len({one_sided_old, one_sided_new, symmetric}) == 3, (
            "narrowing only the baseline, only the candidate, and both "
            "sides are three different compared surfaces: "
            f"{one_sided_old!r} / {one_sided_new!r} / {symmetric!r}"
        )

    def test_a_symmetric_pair_keeps_its_single_value(self) -> None:
        """The vacuity guard: always encoding both sides would satisfy the
        property above and lose the ordinary case's stable identity."""
        assert comparison_exclusion_identity(
            _Snap(("a.h",)), _Snap(("a.h",))
        ) == canonical_exclusion_identity(["a.h"])

    def test_no_baseline_object_is_not_an_asymmetry(self) -> None:
        assert comparison_exclusion_identity(None, _Snap(("a.h",))) == (
            canonical_exclusion_identity(["a.h"])
        )

    def test_neither_side_excluded_anything(self) -> None:
        assert comparison_exclusion_identity(_Snap(()), _Snap(())) == ""


class TestReleaseIdentityComesFromWhatWasObserved:
    """A release's identity is read off its members, not off the request.

    A stored snapshot is never restamped with the current
    ``--exclude-header``, so deriving the release identity from the request
    reported "excluded nothing" for two stored packages that were in truth
    narrowed differently, handing them one configuration digest
    (CodeRabbit review).
    """

    def test_members_that_agree_collapse_to_their_shared_identity(self) -> None:
        assert release_exclusion_identity(['glob:["a.h"]'] * 3, "") == 'glob:["a.h"]'

    def test_members_that_disagree_stay_distinguishable(self) -> None:
        mixed = release_exclusion_identity(['glob:["a.h"]', 'glob:["b.h"]'], "")
        agreed = release_exclusion_identity(['glob:["a.h"]', 'glob:["a.h"]'], "")
        other = release_exclusion_identity(['glob:["b.h"]'], "")
        assert len({mixed, agreed, other}) == 3

    def test_an_observation_is_never_overridden_by_the_request(self) -> None:
        assert release_exclusion_identity(['glob:["a.h"]'], 'glob:["z.h"]') == (
            'glob:["a.h"]'
        )

    def test_every_member_observed_no_exclusion(self) -> None:
        """An observation in its own right, not an absence of one -- so the
        request must not fill it in."""
        assert release_exclusion_identity(["", ""], 'glob:["z.h"]') == ""

    def test_the_request_is_the_fallback_only_when_nothing_completed(
        self,
    ) -> None:
        assert release_exclusion_identity([], 'glob:["z.h"]') == 'glob:["z.h"]'
