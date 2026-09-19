# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Nikolay Petrov
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

"""Contract of the bounded type-spelling match/vocabulary caches.

Written as a *primitive-level* property suite per AGENTS.md's own guidance:
this is a reusable memoization primitive sitting under nine independent
reachability call sites, so its contract is stated here as invariants over
randomized inputs, decoupled from any one caller's domain logic, rather
than only through example tests of whichever caller motivated it.

The oracle throughout is the **uncached** matcher
(``_finditer_allow_nested``), never the cache's own bookkeeping -- a cache
that agreed with itself would assert nothing. The invariants are:

* reuse is *lexically transparent* -- cached and uncached results agree on
  text, span and order, for every vocabulary/text/window, empty results
  included;
* reuse is *vocabulary-scoped* and *window-scoped* -- two vocabularies, or
  two windows over one text, never serve each other's answer;
* reuse does not skip the *reachability operation* -- the same lexical
  result consumed under two different provenance origins still produces the
  two different findings it would have without any cache;
* retention is *bounded and incremental* -- an oversized input is analyzed
  but not retained, and exceeding the budget evicts least-recently-used
  entries rather than clearing the cache.
"""

from __future__ import annotations

import random
import re

import pytest

from abicheck.compare.spelling_match_cache import (
    MATCH_CACHE,
    MAX_CACHED_TEXT_CHARS,
    MAX_ENTRIES,
    MAX_RETAINED_BYTES,
    PATTERN_REGISTRY,
    VOCABULARY_CACHE,
    SpellingMatch,
    clear_caches,
)
from abicheck.compare.spelling_pattern import (
    _build_spelling_pattern,
    finditer_allow_nested,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
from abicheck.type_reachability import (
    _compile_spelling_pattern,
    _finditer_allow_nested,
    directly_referenced_stdlib_types,
    spelling_matches,
    type_string_references_name,
)


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_caches()
    yield
    clear_caches()


def _shape(matches) -> list[tuple[str, int, int]]:
    """The observable shape of a match list: what every caller reads."""
    return [(m.group(0), m.start(), m.end()) for m in matches]


class TestLexicalTransparency:
    """Cached results agree with the uncached matcher, always."""

    @staticmethod
    def _random_case(rng: random.Random) -> tuple[list[str], str, int, int]:
        atoms = ["Foo", "Bar", "std::string", "Inner", "Wrapper", "ns::Baz", "T"]
        vocab = rng.sample(atoms, rng.randint(1, len(atoms)))
        if rng.random() < 0.5:
            # A deliberate outer/inner pair *both* in the vocabulary: the
            # only shape that exercises nested matching at all, which the
            # class's own vacuity guard then insists actually occurred.
            inner = rng.choice(atoms)
            vocab.extend([f"Wrapper<{inner}>", inner])
        if rng.random() < 0.3:
            vocab.extend(["std::vector<std::string>", "std::string"])
        pieces = []
        for _ in range(rng.randint(0, 6)):
            pieces.append(
                rng.choice(
                    [
                        *atoms,
                        "std::vector<std::string>",
                        "Wrapper<Inner>",
                        "const ",
                        " *",
                        ", ",
                        "unrelated_token",
                        "FooBar",
                        "ns::FooExtra",
                    ]
                )
            )
        text = "".join(pieces)
        start = rng.randint(0, len(text)) if text else 0
        end = rng.randint(start, len(text)) if text else 0
        return vocab, text, start, end

    def test_cached_matches_equal_uncached_over_randomized_inputs(self) -> None:
        rng = random.Random(20260917)
        empties = 0
        nested = 0
        for _ in range(1000):
            vocab, text, start, end = self._random_case(rng)
            pattern = _compile_spelling_pattern(vocab)
            assert pattern is not None
            expected = _shape(_finditer_allow_nested(pattern, text, start, end))
            # Cold, then warm: both must equal the uncached oracle.
            cold = _shape(spelling_matches(pattern, text, start, end))
            warm = _shape(spelling_matches(pattern, text, start, end))
            assert cold == expected
            assert warm == expected
            if not expected:
                empties += 1
            spans = {(m[1], m[2]) for m in expected}
            if any(
                a != b and a[0] <= b[0] and a[1] >= b[1] for a in spans for b in spans
            ):
                nested += 1
        # Vacuity guards: the sweep must actually have produced the two
        # interesting shapes, or it proved transparency only for the easy
        # case. A generator that stopped emitting either one would otherwise
        # keep this test passing while asserting much less.
        assert empties > 0, "no empty-result case generated"
        assert nested > 0, "no nested-match case generated"

    def test_empty_result_is_cached_rather_than_recomputed(self) -> None:
        pattern = _compile_spelling_pattern(["Absent"])
        assert pattern is not None
        assert spelling_matches(pattern, "nothing here") == ()
        misses = MATCH_CACHE.misses
        assert spelling_matches(pattern, "nothing here") == ()
        assert MATCH_CACHE.misses == misses, "empty result was not retained"
        assert MATCH_CACHE.hits >= 1


class TestKeyScoping:
    """No two distinct queries share one answer."""

    def test_distinct_vocabularies_do_not_share_results(self) -> None:
        text = "Foo Bar"
        foo = _compile_spelling_pattern(["Foo"])
        bar = _compile_spelling_pattern(["Bar"])
        assert foo is not None and bar is not None
        assert _shape(spelling_matches(foo, text)) == [("Foo", 0, 3)]
        assert _shape(spelling_matches(bar, text)) == [("Bar", 4, 7)]

    def test_cache_key_includes_the_window_bounds(self) -> None:
        # The "an untested key component is not an unnecessary one" lesson:
        # every production caller today passes the default full window, so a
        # key that dropped start/end would pass every other test here.
        text = "Foo Foo"
        pattern = _compile_spelling_pattern(["Foo"])
        assert pattern is not None
        assert _shape(spelling_matches(pattern, text)) == [("Foo", 0, 3), ("Foo", 4, 7)]
        assert _shape(spelling_matches(pattern, text, 1)) == [("Foo", 4, 7)]
        assert _shape(spelling_matches(pattern, text, 0, 3)) == [("Foo", 0, 3)]

    def test_cache_key_includes_the_text(self) -> None:
        pattern = _compile_spelling_pattern(["Foo"])
        assert pattern is not None
        assert _shape(spelling_matches(pattern, "Foo")) == [("Foo", 0, 3)]
        assert _shape(spelling_matches(pattern, "Bar")) == []

    def test_cache_that_caches_nothing_is_distinguishable(self) -> None:
        # Output equivalence alone cannot tell a correct cache from an absent
        # one, so assert the retained state directly.
        pattern = _compile_spelling_pattern(["Foo"])
        assert pattern is not None
        spelling_matches(pattern, "Foo")
        assert len(MATCH_CACHE) == 1
        assert MATCH_CACHE.retained_bytes > 0


class TestVocabularyReuse:
    """One vocabulary compiles to one pattern, whatever its order."""

    def test_same_vocabulary_in_any_order_reuses_one_pattern_object(self) -> None:
        a = _compile_spelling_pattern(["Foo", "Bar", "Baz"])
        b = _compile_spelling_pattern(["Baz", "Foo", "Bar"])
        assert a is b

    def test_builder_is_order_independent_for_equal_length_candidates(self) -> None:
        """The *builder* must be order-independent, not just the cache.

        Length-only ordering left equal-length alternatives in incoming
        order, so an identical vocabulary from two differently-ordered
        sources built two different pattern strings -- a genuine regex
        recompilation, not merely a second helper call. This drives
        ``_build_spelling_pattern`` directly and with *sequences*, because
        ``_compile_spelling_pattern`` normalizes through a ``frozenset``
        first and so hides an order-dependent builder entirely: written
        against the cached entry point, this assertion passed even with the
        tie-break reverted.
        """
        rng = random.Random(7)
        vocab = ["Aaa", "Bbb", "Ccc", "Dddd", "Eeee", "F"]
        texts = set()
        for _ in range(25):
            shuffled = vocab[:]
            rng.shuffle(shuffled)
            pattern = _build_spelling_pattern(shuffled)
            assert pattern is not None
            texts.add(pattern.pattern)
        assert len(texts) == 1

    def test_builder_ordering_never_changes_which_spellings_match(self) -> None:
        """Whatever the alternation order, the match set is identical.

        The boundary anchors on both sides are what make this true, so it
        must hold for a vocabulary where one spelling is a strict prefix of
        another and for one where two spellings tie on length.
        """
        rng = random.Random(11)
        vocab = ["Foo", "FooBar", "Bar", "Baz", "std::string", "std::stringstream"]
        text = "FooBar Foo Bar Baz std::stringstream std::string Fo"
        shapes = set()
        for _ in range(25):
            shuffled = vocab[:]
            rng.shuffle(shuffled)
            pattern = _build_spelling_pattern(shuffled)
            assert pattern is not None
            shapes.add(tuple(sorted(_shape(_finditer_allow_nested(pattern, text)))))
        assert len(shapes) == 1
        assert shapes.pop(), "the ordering sweep matched nothing at all"

    def test_cached_entry_point_is_order_insensitive(self) -> None:
        rng = random.Random(13)
        vocab = ["Aaa", "Bbb", "Ccc", "Dddd", "Eeee", "F"]
        patterns = set()
        for _ in range(10):
            shuffled = vocab[:]
            rng.shuffle(shuffled)
            patterns.add(id(_compile_spelling_pattern(shuffled)))
        assert len(patterns) == 1

    def test_different_vocabularies_compile_to_different_patterns(self) -> None:
        a = _compile_spelling_pattern(["Foo"])
        b = _compile_spelling_pattern(["Foo", "Bar"])
        assert a is not None and b is not None
        assert a is not b
        assert a.pattern != b.pattern

    def test_empty_vocabulary_is_none_and_is_itself_cached(self) -> None:
        assert _compile_spelling_pattern([]) is None
        assert _compile_spelling_pattern(set()) is None
        assert VOCABULARY_CACHE.hits >= 1

    def test_vocabulary_cache_is_bounded(self) -> None:
        from abicheck.compare.spelling_match_cache import MAX_CACHED_VOCABULARIES

        for i in range(MAX_CACHED_VOCABULARIES * 2):
            _compile_spelling_pattern([f"Type{i}"])
        assert len(VOCABULARY_CACHE) <= MAX_CACHED_VOCABULARIES


class TestReachabilityOperationStillRuns:
    """Reuse is lexical only -- the scan's own state updates are not skipped."""

    @staticmethod
    def _snapshot(origin: ScopeOrigin) -> AbiSnapshot:
        return AbiSnapshot(
            library="libx.so",
            version="1.0",
            functions=[
                Function(
                    name="f",
                    mangled="f",
                    return_type="void",
                    params=[Param(name="s", type="std::string")],
                    visibility=Visibility.PUBLIC,
                    origin=origin,
                )
            ],
            types=[RecordType(name="std::string", kind="class")],
        )

    def test_same_lexical_text_under_two_origins_keeps_two_answers(self) -> None:
        """A public-header and a private-header root name the identical type string.

        The lexical answer for ``"std::string"`` is the same in both runs,
        so a cache keyed on the text alone would serve the first run's
        *finding* to the second. It must not: the origins differ, so the
        second run reaches nothing.
        """
        public = directly_referenced_stdlib_types(
            self._snapshot(ScopeOrigin.PUBLIC_HEADER)
        )
        internal = directly_referenced_stdlib_types(
            self._snapshot(ScopeOrigin.PRIVATE_HEADER)
        )
        assert public == frozenset({"std::string"})
        assert internal == frozenset()

    def test_repeated_scans_are_identical_cold_and_warm(self) -> None:
        snap = AbiSnapshot(
            library="libx.so",
            version="1.0",
            functions=[
                Function(
                    name="f",
                    mangled="f",
                    return_type="Wrapper<std::string>",
                    params=[Param(name="v", type="std::vector<std::string>")],
                    visibility=Visibility.PUBLIC,
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[
                RecordType(name="std::string", kind="class"),
                RecordType(
                    name="vector",
                    qualified_name="std::vector<std::string>",
                    kind="class",
                ),
                RecordType(
                    name="Wrapper",
                    qualified_name="Wrapper",
                    kind="class",
                    fields=[TypeField(name="s", type="std::string")],
                ),
            ],
        )
        clear_caches()
        cold = directly_referenced_stdlib_types(snap)
        warm = directly_referenced_stdlib_types(snap)
        assert cold == warm
        assert MATCH_CACHE.hits > 0, "the warm scan never reused a cached result"


class TestRetentionBounds:
    """Retention is capped three ways, and eviction is incremental."""

    def test_oversized_text_is_analyzed_but_not_retained(self) -> None:
        pattern = _compile_spelling_pattern(["Foo"])
        assert pattern is not None
        text = "x" * (MAX_CACHED_TEXT_CHARS + 1) + " Foo"
        assert _shape(spelling_matches(pattern, text)) == _shape(
            _finditer_allow_nested(pattern, text)
        )
        assert len(MATCH_CACHE) == 0
        assert MATCH_CACHE.bypasses == 1
        # Still correct on the second call, just recomputed.
        assert _shape(spelling_matches(pattern, text)) == _shape(
            _finditer_allow_nested(pattern, text)
        )

    def test_oversized_result_is_not_retained(self) -> None:
        from abicheck.compare.spelling_match_cache import MAX_CACHED_RESULT_MATCHES

        pattern = _compile_spelling_pattern(["A"])
        assert pattern is not None
        text = " ".join(["A"] * (MAX_CACHED_RESULT_MATCHES + 5))
        assert len(text) <= MAX_CACHED_TEXT_CHARS
        got = spelling_matches(pattern, text)
        assert len(got) == MAX_CACHED_RESULT_MATCHES + 5
        assert len(MATCH_CACHE) == 0

    def test_budget_is_never_exceeded_and_eviction_is_incremental(self) -> None:
        pattern = _compile_spelling_pattern(["Foo"])
        assert pattern is not None
        # The byte budget has to actually bind for this to assert anything:
        # 400 near-maximal entries retain ~3.4 MiB against the production
        # 8 MiB budget, and 400 is far under MAX_ENTRIES, so neither
        # eviction condition could ever run. Shrink the byte budget instead
        # of inflating the experiment, so the cliff-vs-incremental question
        # is asked cheaply and deterministically.
        import abicheck.compare.spelling_match_cache as cache_mod

        original = cache_mod.MAX_RETAINED_BYTES
        cache_mod.MAX_RETAINED_BYTES = 64 * 1024
        submitted = 400
        try:
            filler = "Foo " + "y" * (MAX_CACHED_TEXT_CHARS - 16)
            peak = 0
            for i in range(submitted):
                spelling_matches(pattern, f"{i:06d}{filler}")
                assert MATCH_CACHE.retained_bytes <= cache_mod.MAX_RETAINED_BYTES
                assert len(MATCH_CACHE) <= MAX_ENTRIES
                peak = max(peak, len(MATCH_CACHE))
                # Incremental, not a cliff: recording one more entry at the
                # limit must never drop the cache to (near) empty the way a
                # clear-the-whole-thing overflow policy does.
                assert len(MATCH_CACHE) > peak // 2 or peak < 4
            retained = len(MATCH_CACHE)
        finally:
            cache_mod.MAX_RETAINED_BYTES = original
        assert peak > 1, "the budget experiment never populated the cache"
        assert retained < submitted, (
            f"byte-budget eviction never ran: all {submitted} entries retained"
        )
        assert original == MAX_RETAINED_BYTES, "the budget was not restored"

    def test_eviction_is_least_recently_used(self) -> None:
        pattern = _compile_spelling_pattern(["Foo"])
        assert pattern is not None
        # Shrink the budget for a deterministic, cheap experiment.
        import abicheck.compare.spelling_match_cache as cache_mod

        original = cache_mod.MAX_ENTRIES
        cache_mod.MAX_ENTRIES = 2
        try:
            spelling_matches(pattern, "Foo a")
            spelling_matches(pattern, "Foo b")
            spelling_matches(pattern, "Foo a")  # refresh 'a'
            spelling_matches(pattern, "Foo c")  # evicts 'b', not 'a'
            misses = MATCH_CACHE.misses
            spelling_matches(pattern, "Foo a")
            assert MATCH_CACHE.misses == misses, "'a' was evicted despite recent use"
            spelling_matches(pattern, "Foo b")
            assert MATCH_CACHE.misses == misses + 1, "'b' survived eviction"
        finally:
            cache_mod.MAX_ENTRIES = original

    def test_a_pattern_reference_is_released_with_its_last_entry(self) -> None:
        """Eviction returns the match cache's reference to the pattern owner.

        Stated against ``_entries_per_token`` -- the match cache's own
        bookkeeping -- rather than against the registry, because the
        vocabulary cache also holds a reference to both patterns here, so
        the registry legitimately still holds them.
        """
        import abicheck.compare.spelling_match_cache as cache_mod

        original = cache_mod.MAX_ENTRIES
        cache_mod.MAX_ENTRIES = 1
        try:
            first = _compile_spelling_pattern(["Alpha"])
            second = _compile_spelling_pattern(["Beta"])
            assert first is not None and second is not None
            spelling_matches(first, "Alpha")
            spelling_matches(second, "Beta")
            # One entry survives, so the match cache references exactly one
            # pattern: the evicted entry gave its reference back.
            assert len(MATCH_CACHE._entries_per_token) == 1
        finally:
            cache_mod.MAX_ENTRIES = original

    def test_clear_releases_everything(self) -> None:
        pattern = _compile_spelling_pattern(["Foo"])
        assert pattern is not None
        spelling_matches(pattern, "Foo")
        clear_caches()
        assert len(MATCH_CACHE) == 0
        assert MATCH_CACHE.retained_bytes == 0
        assert len(VOCABULARY_CACHE) == 0


class TestSpellingMatchValue:
    """Cached results are immutable and hold no regex/subject references."""

    def test_result_is_an_immutable_tuple_of_slotted_values(self) -> None:
        pattern = _compile_spelling_pattern(["Foo"])
        assert pattern is not None
        got = spelling_matches(pattern, "Foo")
        assert isinstance(got, tuple)
        assert not any(isinstance(m, re.Match) for m in got)
        with pytest.raises(AttributeError):
            got[0].extra = 1  # type: ignore[attr-defined]

    def test_group_start_end_reject_other_groups(self) -> None:
        m = SpellingMatch("Foo", 0, 3)
        assert (m.group(), m.group(0), m.start(), m.end()) == ("Foo", "Foo", 0, 3)
        for call in (m.group, m.start, m.end):
            with pytest.raises(IndexError):
                call(1)

    def test_value_equality_and_hashing(self) -> None:
        assert SpellingMatch("Foo", 0, 3) == SpellingMatch("Foo", 0, 3)
        assert SpellingMatch("Foo", 0, 3) != SpellingMatch("Foo", 1, 4)
        assert SpellingMatch("Foo", 0, 3) != "Foo"
        assert len({SpellingMatch("Foo", 0, 3), SpellingMatch("Foo", 0, 3)}) == 1
        assert "Foo" in repr(SpellingMatch("Foo", 0, 3))


class TestBoundarySemanticsAgree:
    """The single-name check and the compiled alternation decide alike.

    ``BOUNDARY_CHARS`` is documented as existing so these two
    implementations "cannot silently drift apart" -- but
    ``type_string_references_name`` spelled the character class out
    literally as ``"_:"`` for its whole life, so the claim had no
    executable content and the constant was load-bearing for only one of
    the two. It now reads the constant, and this states the agreement over
    generated inputs rather than trusting that one string literal matches
    another.

    The oracle here is deliberately the *other implementation*, which is
    legitimate precisely because neither is derived from the other: one is
    a manual index walk, the other a compiled lookaround. A disagreement
    means one of them is wrong, which is the thing worth knowing.
    """

    @staticmethod
    def _cases() -> list[tuple[str, str]]:
        names = ["std::string", "Foo", "ns::Bar", "T", "a_b"]
        contexts = [
            "{0}",
            "const {0} &",
            "std::vector<{0}>",
            "x{0}",
            "{0}x",
            "{0}::inner",
            "outer::{0}",
            "_{0}",
            "{0}_",
            "{0}{0}",
            "({0}, int)",
            "{0}*",
            "a {0} b",
            ":{0}",
            "{0}:",
            "9{0}",
            "{0}9",
        ]
        return [(ctx.format(name), name) for name in names for ctx in contexts]

    def test_single_name_and_compiled_pattern_agree(self) -> None:
        disagreements = []
        for text, name in self._cases():
            manual = type_string_references_name(text, name)
            pattern = _build_spelling_pattern([name])
            assert pattern is not None
            compiled = bool(finditer_allow_nested(pattern, text))
            if manual != compiled:
                disagreements.append((text, name, manual, compiled))
        assert not disagreements, disagreements

    def test_the_case_sweep_covers_both_answers(self) -> None:
        """Vacuity guard: a sweep where every case agreed on ``True`` (or on
        ``False``) would pass the test above against two broken
        implementations.
        """
        answers = {
            type_string_references_name(text, name) for text, name in self._cases()
        }
        assert answers == {True, False}

    def test_a_divergent_boundary_class_is_detectable(self) -> None:
        """The agreement above is sensitive to the character class itself.

        Asserted by building a pattern with a deliberately narrower class
        and showing it then disagrees -- so the test above is checking the
        boundary rule, not merely that both sides find the substring.
        """
        narrow = re.compile(
            rf"(?<![A-Za-z0-9])(?:{re.escape('std::string')})(?![A-Za-z0-9])"
        )
        # `_` is in the real class but not the narrow one, so the narrow
        # pattern matches where the documented rule says it must not.
        assert not type_string_references_name("_std::string", "std::string")
        assert bool(finditer_allow_nested(narrow, "_std::string"))


class TestPatternReferencesDoNotLeak:
    """A pattern is referenced only while an entry actually needs it.

    The retention this module exists to bound includes its own bookkeeping:
    an implementation that took the registry reference at *lookup* time
    would hold every compiled pattern it was ever asked about forever --
    including ones whose every result was too large to cache, so the cache
    stored nothing and retained the pattern anyway.
    """

    def test_a_lookup_that_stores_nothing_retains_nothing(self) -> None:
        pattern = _compile_spelling_pattern(["Foo"])
        assert pattern is not None
        oversized = "x" * (MAX_CACHED_TEXT_CHARS + 1) + " Foo"
        spelling_matches(pattern, oversized)
        assert len(MATCH_CACHE) == 0
        assert MATCH_CACHE._entries_per_token == {}

    def test_many_bypassed_patterns_do_not_accumulate(self) -> None:
        """Built outside the vocabulary cache, so nothing else holds them."""
        oversized = "y" * (MAX_CACHED_TEXT_CHARS + 1)
        for i in range(50):
            pattern = _build_spelling_pattern([f"Type{i}"])
            assert pattern is not None
            spelling_matches(pattern, oversized)
        assert MATCH_CACHE._entries_per_token == {}
        assert len(PATTERN_REGISTRY) == 0, (
            "a pattern nothing stored a result for stayed registered"
        )
        assert PATTERN_REGISTRY.retained_bytes == 0

    def test_a_stored_entry_does_retain_its_pattern(self) -> None:
        pattern = _compile_spelling_pattern(["Foo"])
        assert pattern is not None
        spelling_matches(pattern, "Foo")
        token = PATTERN_REGISTRY.token_for(pattern)
        assert token is not None
        assert MATCH_CACHE._entries_per_token == {token: 1}
        assert PATTERN_REGISTRY.pattern_for(token) is pattern


class TestCacheBookkeepingEdges:
    """The bookkeeping paths a single-threaded lookup never reaches.

    ``put`` and ``_PatternRegistry.release`` are the classes' own API, and
    both carry a guard that the ``matches_for`` path cannot exercise (it
    checks ``get`` before ``put``, and evicts one entry per pattern in these
    tests). They are not dead code — each prevents a specific corruption of
    the byte budget or the pattern refcount — so they are stated here
    against the classes directly rather than left as unexercised lines.
    """

    def test_putting_one_key_twice_does_not_double_count(self) -> None:
        """Re-inserting a key must not charge its bytes or its pattern twice.

        Without the guard, the aggregate budget would drift upward by one
        entry's size on every repeat and the pattern refcount would never
        reach zero, so the pattern would be retained after its last entry
        was evicted — a slow leak of exactly what the budget bounds.
        """
        pattern = _compile_spelling_pattern(["Foo"])
        assert pattern is not None
        matches = (SpellingMatch("Foo", 0, 3),)
        MATCH_CACHE.put("Foo", 0, 3, matches, pattern)
        token = MATCH_CACHE.token_for(pattern)
        assert token is not None
        bytes_after_first = MATCH_CACHE.retained_bytes
        registry_after_first = PATTERN_REGISTRY.retained_bytes
        refs_after_first = MATCH_CACHE._entries_per_token[token]
        MATCH_CACHE.put("Foo", 0, 3, matches, pattern)
        assert len(MATCH_CACHE) == 1
        assert MATCH_CACHE.retained_bytes == bytes_after_first
        assert PATTERN_REGISTRY.retained_bytes == registry_after_first
        assert MATCH_CACHE._entries_per_token[token] == refs_after_first

    def test_a_pattern_with_two_entries_survives_losing_one(self) -> None:
        """Eviction releases one reference, not the whole pattern.

        The refcount exists because several entries share one pattern: a
        release that dropped the registry holder outright would free a
        pattern that other live entries still key on, and its ``id()`` could
        then be reused by a different pattern while those entries remained —
        the one way the ``id()``-keyed token lookup can go wrong.
        """
        pattern = _build_spelling_pattern(["Foo"])
        assert pattern is not None
        spelling_matches(pattern, "Foo a")
        spelling_matches(pattern, "Foo b")
        token = MATCH_CACHE.token_for(pattern)
        assert token is not None
        assert MATCH_CACHE._entries_per_token[token] == 2
        PATTERN_REGISTRY.release(token, holder="match")
        assert PATTERN_REGISTRY.pattern_for(token) is pattern
        PATTERN_REGISTRY.release(token, holder="match")
        assert PATTERN_REGISTRY.pattern_for(token) is None
        assert PATTERN_REGISTRY.token_for(pattern) is None

    def test_releasing_an_unheld_token_is_a_no_op(self) -> None:
        """Release must be idempotent against an already-cleared registry.

        ``clear()`` drops every holder wholesale while eviction releases per
        entry, so a release can legitimately arrive for a token nothing
        holds; it must not raise.
        """
        PATTERN_REGISTRY.release(123456789, holder="match")
        PATTERN_REGISTRY.release(123456789, holder="vocabulary")
        assert len(PATTERN_REGISTRY) == 0

    def test_token_for_does_not_register_or_mutate(self) -> None:
        """Looking a pattern up must not start charging for it.

        A ``token_for`` that registered would leave a holder behind for
        every pattern ever *asked about*, including ones nothing stores a
        result for — the leak the lookup/store split exists to prevent.
        """
        pattern = _build_spelling_pattern(["Foo"])
        assert pattern is not None
        assert MATCH_CACHE.token_for(pattern) is None
        assert MATCH_CACHE.token_for(pattern) is None
        assert len(MATCH_CACHE) == 0
        assert len(PATTERN_REGISTRY) == 0
        assert PATTERN_REGISTRY.retained_bytes == 0

    def test_a_token_is_stable_and_never_recycled(self) -> None:
        """Generation tokens, not addresses.

        ``id()`` is reused the moment an object is freed; a monotonic token
        is not. Asserted by releasing a pattern and registering many more —
        an ``id()``-derived token would very plausibly collide, a generation
        token cannot.
        """
        seen: set[int] = set()
        for i in range(200):
            pattern = _build_spelling_pattern([f"Type{i}"])
            assert pattern is not None
            token = PATTERN_REGISTRY.acquire(pattern, holder="match")
            assert PATTERN_REGISTRY.token_for(pattern) == token
            assert token not in seen, "a token was reused for a second pattern"
            seen.add(token)
            # Drop it, so the next iteration may well reuse the address.
            PATTERN_REGISTRY.release(token, holder="match")
            del pattern
        assert len(seen) == 200


class TestNestedSearchLosesASameStartShorterSpelling:
    """An executable pin on a *documented limitation*, not a passing feature.

    ``finditer_allow_nested`` re-searches ``(m.start() + 1, m.end())`` after
    each match, a window that excludes ``m.start()``. So a shorter registered
    spelling beginning at the same offset as a longer match is never
    reported. These assertions state what the matcher does **today** so the
    next reader finds the limitation instead of rediscovering it, and so the
    change that fixes it fails here loudly rather than silently altering
    findings inside some other patch.

    Fixing it is a behaviour change -- it adds reachability edges, which can
    change findings and release obligation counts -- so it is tracked in
    ``docs/contribute/known-gaps.md`` and must land isolated, with its own
    rebaseline. When it does, replace these with the superset expectation.
    """

    @pytest.mark.parametrize(
        ("vocabulary", "text", "reported", "lost"),
        [
            (["Foo", "Foo<int>"], "Foo<int>", "Foo<int>", "Foo"),
            (
                ["dal::Table", "dal::Table<float>"],
                "const dal::Table<float>& x",
                "dal::Table<float>",
                "dal::Table",
            ),
            (
                ["std::vector", "std::vector<int>"],
                "std::vector<int>",
                "std::vector<int>",
                "std::vector",
            ),
            (["Node", "Node*"], "Node* next", "Node*", "Node"),
            (["A", "A&&"], "A&& r", "A&&", "A"),
        ],
    )
    def test_the_shorter_same_start_spelling_is_not_reported(
        self, vocabulary, text, reported, lost
    ) -> None:
        """Realistic C++ spellings, not randomized atoms.

        Each case is a class template and an instantiation of it -- the
        shape ``type_reachability`` actually produces, since it registers
        record spellings and typedef targets into one vocabulary.
        """
        pattern = _compile_spelling_pattern(vocabulary)
        assert pattern is not None
        found = {m.group(0) for m in _finditer_allow_nested(pattern, text)}
        assert reported in found
        assert lost not in found, (
            f"{lost!r} is now reported in {text!r} -- the known gap in "
            "docs/contribute/known-gaps.md appears to be fixed. That change "
            "adds reachability edges and needs a rebaseline; update this "
            "test to the superset expectation rather than deleting it."
        )
        # The lost spelling really is a boundary-valid occurrence, so this
        # is an under-report and not merely a boundary rule doing its job.
        # Without this the test would pass for a vocabulary whose shorter
        # entry was never legitimately present at all.
        start = text.index(lost)
        after = start + len(lost)
        boundary = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_:"
        )
        assert start == 0 or text[start - 1] not in boundary
        assert after >= len(text) or text[after] not in boundary
