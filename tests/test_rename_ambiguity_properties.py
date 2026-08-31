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

"""Primitive-level property tests for
``compare.rename_ambiguity.added_side_ambiguity_resolver`` (CLAUDE.md's
"Primitive-level property tests" convention, `abicheck/compare/AGENTS.md`'s
own "gets its own standalone property-test class" rule for a new matching
primitive here — Codex review, PR abicheck/abicheck#970).

``tests/test_namespace_move_cross_position_ambiguity.py`` already exercises
this resolver through its one real caller, ``find_namespace_move_groups``,
with hand-picked mangled-name scenarios. This file states the resolver's own
contract as invariants, generated over its actual input shapes directly
(never through a caller), the way ``test_diff_namespaces.py``'s
``TestPairedStableIndicesProperties`` does for its own merge primitive.
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.compare.rename_ambiguity import added_side_ambiguity_resolver

_Entry = tuple[tuple[str, ...], list[str], int, list[str]]

#: Small, disjoint token alphabet -- large enough to build multi-symbol
#: scenarios without every draw colliding, small enough that Hypothesis
#: explores real collisions/sharing rather than always drawing distinct
#: tokens by chance.
_TOKENS = ("p", "q", "r", "s", "t", "u")


def _scopes() -> st.SearchStrategy[tuple[str, ...]]:
    """A removed/added identity's own full scope chain -- 2 or 3 distinct
    segments, mirroring a real mangled name's parsed component list."""
    return st.lists(st.sampled_from(_TOKENS), min_size=2, max_size=3, unique=True).map(
        tuple
    )


def _build(
    symbol_and_substitutions: list[tuple[tuple[str, ...], list[tuple[int, str]]]],
) -> tuple[
    list[_Entry],
    dict[str, set[str]],
    dict[tuple[str, tuple[str, str]], set[str]],
]:
    """Replay ``find_namespace_move_groups``'s own Phase 1 bookkeeping for a
    hand-specified set of (symbol scope, [(position, new_token), ...])
    entries, producing a self-consistent ``(entries,
    added_id_to_removed_symbols, raw_symbol_key_targets)`` triple -- exactly
    the three inputs the caller threads through unchanged from its own raw
    candidacy loop into :func:`added_side_ambiguity_resolver`.
    """
    entries: list[_Entry] = []
    added_id_to_removed_symbols: dict[str, set[str]] = {}
    raw_symbol_key_targets: dict[tuple[str, tuple[str, str]], set[str]] = {}
    for scope, substitutions in symbol_and_substitutions:
        r_comps = list(scope)
        symbol_id = "::".join(r_comps)
        for i, new_token in substitutions:
            a_comps = list(r_comps)
            a_comps[i] = new_token
            added_id = "::".join(a_comps)
            key = (r_comps[i], new_token)
            entries.append(((), r_comps, i, a_comps))
            added_id_to_removed_symbols.setdefault(added_id, set()).add(symbol_id)
            raw_symbol_key_targets.setdefault((symbol_id, key), set()).add(added_id)
    return entries, added_id_to_removed_symbols, raw_symbol_key_targets


@st.composite
def _random_scenarios(
    draw: st.DrawFn,
) -> tuple[
    list[_Entry],
    dict[str, set[str]],
    dict[tuple[str, tuple[str, str]], set[str]],
]:
    """A random, internally-consistent scenario: several symbols, each with
    zero or more (position, new_token) substitutions -- i.e. an entry per
    masking position that resolved to a candidate, the shape Phase 1 hands
    to Phase 2. Symbols may coincidentally share tokens/positions, so
    genuine corroboration and collision cases both arise by construction."""
    scopes = draw(st.lists(_scopes(), min_size=1, max_size=4, unique=True))
    spec: list[tuple[tuple[str, ...], list[tuple[int, str]]]] = []
    for scope in scopes:
        subs = []
        for i in range(len(scope) - 1):
            if draw(st.booleans()):
                new_token = draw(
                    st.sampled_from(_TOKENS).filter(lambda t: t != scope[i])
                )
                subs.append((i, new_token))
        spec.append((scope, subs))
    return _build(spec)


class TestAddedSideAmbiguityResolverProperties:
    @given(scenario=_random_scenarios())
    def test_permutation_independence(self, scenario) -> None:
        """Shuffling `entries`' order must not change `key_support` or any
        `is_acceptable` verdict -- the resolver groups by dict/set, so its
        answer must depend only on which entries exist, never on the order
        they were built or handed in."""
        entries, added_id_to_removed_symbols, raw_symbol_key_targets = scenario
        key_support_a, is_acceptable_a = added_side_ambiguity_resolver(
            entries, added_id_to_removed_symbols, raw_symbol_key_targets
        )
        key_support_b, is_acceptable_b = added_side_ambiguity_resolver(
            list(reversed(entries)), added_id_to_removed_symbols, raw_symbol_key_targets
        )
        assert key_support_a == key_support_b
        for _masked, r_comps, i, a_comps in entries:
            symbol_id = "::".join(r_comps)
            added_id = "::".join(a_comps)
            key = (r_comps[i], a_comps[i])
            assert is_acceptable_a(symbol_id, added_id, key) == is_acceptable_b(
                symbol_id, added_id, key
            )

    @given(scenario=_random_scenarios())
    def test_single_claimant_is_always_accepted(self, scenario) -> None:
        """An added identity claimed by exactly one distinct removed
        identity is never ambiguous on the added side -- the fast path must
        accept it unconditionally, regardless of what else is going on
        elsewhere in the same comparison."""
        entries, added_id_to_removed_symbols, raw_symbol_key_targets = scenario
        _key_support, is_acceptable = added_side_ambiguity_resolver(
            entries, added_id_to_removed_symbols, raw_symbol_key_targets
        )
        for _masked, r_comps, i, a_comps in entries:
            symbol_id = "::".join(r_comps)
            added_id = "::".join(a_comps)
            key = (r_comps[i], a_comps[i])
            if len(added_id_to_removed_symbols[added_id]) == 1:
                assert is_acceptable(symbol_id, added_id, key)

    def test_unresolved_competitor_always_vetoes(self) -> None:
        """A competitor claimed via `added_id_to_removed_symbols` but that
        never resolved its OWN candidacy toward this specific added
        identity into any `entries` row (round-4 scenario,
        `TestFindNamespaceMoveGroupsRetainsLocallyAmbiguousCandidatesGlobally`)
        is a live, irreducible threat -- built directly at the primitive
        level here, not through the caller's mangled-name machinery."""
        entries, added_id_to_removed_symbols, raw_symbol_key_targets = _build(
            [
                (("p", "q", "x"), [(0, "z")]),  # p::q::x -> z::q::x
                (("r", "q", "x"), [(0, "y")]),  # r::q::x -> y::q::x (unrelated)
            ]
        )
        # A phantom competitor claims the SAME added identity ("z::q::x")
        # that "p::q::x" resolved to, but never itself appears in `entries`
        # for that target -- exactly "claimed, never resolved here".
        added_id_to_removed_symbols["z::q::x"].add("ghost::q::x")

        _key_support, is_acceptable = added_side_ambiguity_resolver(
            entries, added_id_to_removed_symbols, raw_symbol_key_targets
        )
        assert not is_acceptable("p::q::x", "z::q::x", ("p", "z"))
        # The unrelated pairing, sharing no added identity with the ghost,
        # is untouched.
        assert is_acceptable("r::q::x", "y::q::x", ("r", "y"))

    def test_a_resolved_uncorroborated_competitor_is_dismissible(self) -> None:
        """A competitor that DID resolve its own candidacy toward this
        added identity, but whose resolved key carries no support beyond
        itself, is a dismissible coincidence -- the well-supported rival
        claim must still be accepted (the exact fix for the fabricated-
        batch regression this resolver replaced an unconditional reject
        with)."""
        entries, added_id_to_removed_symbols, raw_symbol_key_targets = _build(
            [
                (("p", "new", "f"), [(0, "q")]),  # p::new::f -> q::new::f
                (("p", "new", "g"), [(0, "q")]),  # p::new::g -> q::new::g
                (("q", "old", "f"), [(1, "new")]),  # q::old::f -> q::new::f
            ]
        )
        _key_support, is_acceptable = added_side_ambiguity_resolver(
            entries, added_id_to_removed_symbols, raw_symbol_key_targets
        )
        # "q::new::f" is contested by "p::new::f" (key ("p","q"), backed by
        # the sibling "g" pair) and "q::old::f" (key ("old","new"), backed
        # by nothing else) -- the isolated claim must not block the
        # well-supported one.
        assert is_acceptable("p::new::f", "q::new::f", ("p", "q"))
        # "q::old::f"'s own claim has no corroboration of its own key either
        # -- it is not accepted as a group member (checked by the caller's
        # separate corroboration test, not this resolver), but it must not
        # be treated as blocking the other side, which this call alone
        # already confirms above.
