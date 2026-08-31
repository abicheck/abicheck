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

"""Added-side ambiguity resolution for ``diff_symbols_renames.
find_namespace_move_groups`` (ADR-061 D1: new rename-matching logic routes
to ``compare/``, not the debt-baselined flat ``diff_symbols_renames.py``).

That function already resolves cross-position ambiguity via global-support
corroboration when ONE removed symbol has multiple candidate added targets
(``removed_id_to_added_symbols``) -- one candidate key independently reused
by a DIFFERENT removed symbol is real corroborating evidence the other
candidate lacks. This module runs the mirror test for the symmetric case:
ONE added symbol claimed by multiple distinct removed identities
(``added_id_to_removed_symbols``), previously rejected outright,
unconditionally. That asymmetry silently dropped a well-supported
namespace-move batch member -- or, when the collateral loss dropped a group
below its 2+-pair threshold, an entire batch -- whenever an unrelated,
uncorroborated removed identity happened to coincidentally collide with one
of the batch's own added targets.

This CANNOT reuse the removed-side test's exact shape: there, every
competing key belongs to the SAME symbol (its own alternate masking
positions), so subtracting that one ``symbol_id`` from a key's supporter set
is enough to ask "does anyone ELSE back this option". Here, each competing
key belongs to a DIFFERENT removed identity, and that competitor may itself
never have resolved THIS SPECIFIC added identity to any single key at all --
exactly the round-4 scenario ``TestFindNamespaceMoveGroupsRetainsLocally
AmbiguousCandidatesGlobally`` (``tests/test_batch_rename_namespace_move.py``)
pins: a removed symbol stuck between two candidates at its only masking
position never gets an ``entries`` row for either, and remains a live,
irreducible threat, not a dismissible coincidence.

A competitor is therefore scoped per (competitor, added_id) pair (Codex
review, fresh evidence) -- NOT merely "did the competitor resolve to any key
anywhere": a removed symbol can have several masking positions, each
possibly targeting a DIFFERENT added identity. A competitor resolved
cleanly at one position (an unrelated added identity) is not thereby vouched
for at ANOTHER position where its candidacy toward THIS added identity was
itself locally ambiguous and never got its own ``entries`` row -- checking
"has any resolved key at all" let an unrelated, resolved claim dismiss a
genuinely live, unresolved threat on the target actually in question,
fabricating a batch.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence


def added_side_ambiguity_resolver(
    entries: Sequence[tuple[tuple[str, ...], list[str], int, list[str]]],
    added_id_to_removed_symbols: Mapping[str, set[str]],
    raw_symbol_key_targets: Mapping[tuple[str, tuple[str, str]], set[str]],
) -> tuple[
    dict[tuple[str, str], set[str]], Callable[[str, str, tuple[str, str]], bool]
]:
    """Build ``key_support`` (which removed symbol(s) raised each
    ``(old_segment, new_segment)`` key via *entries*) and the
    ``is_acceptable(symbol_id, added_id, key)`` predicate
    ``find_namespace_move_groups``'s Phase 2 gates each entry's added-side
    ambiguity on. Returns both since the caller's own removed-side
    corroboration check (right after the added-side one) reuses
    ``key_support`` too, rather than rebuilding it a second time.

    *entries* is that function's Phase 1 output (masked context, removed
    scope components, differing position, added scope components) --
    already-computed, locally-unambiguous candidacies only.
    *added_id_to_removed_symbols*/*raw_symbol_key_targets* are the caller's
    raw, pre-Phase-2 candidacy trackers (populated from every raw candidacy,
    including one a masking position's own local ambiguity check discards
    from *entries*). ``raw_added_key_targets`` (this module's own added-
    identity-keyed mirror of *raw_symbol_key_targets*) is derived here by
    inverting it, rather than asking the caller to also build and pass a
    second raw tracker: every ``(symbol_id, key) -> {cand_id, ...}`` entry
    *raw_symbol_key_targets* carries names exactly the same raw candidacies
    an added-identity-keyed ``(cand_id, key) -> {symbol_id, ...}`` view
    needs, just grouped the other way.
    """
    raw_added_key_targets: dict[tuple[str, tuple[str, str]], set[str]] = {}
    for (symbol_id, rkey), cand_ids in raw_symbol_key_targets.items():
        for cand_id in cand_ids:
            raw_added_key_targets.setdefault((cand_id, rkey), set()).add(symbol_id)

    # key_support: which removed symbol(s) raised each key via `entries`.
    # resolved_added_claims: (symbol_id, added_id) -> the key this exact
    # pairing resolved to via `entries` -- i.e. this removed identity's claim
    # on THIS SPECIFIC added identity survived its own local per-position
    # ambiguity check. A competitor with no entry here for the added_id in
    # question never resolved that specific rivalry and must always veto.
    key_support: dict[tuple[str, str], set[str]] = {}
    resolved_added_claims: dict[tuple[str, str], tuple[str, str]] = {}
    for _masked, r_comps, i, a_comps in entries:
        sid = "::".join(r_comps)
        aid = "::".join(a_comps)
        k = (r_comps[i], a_comps[i])
        key_support.setdefault(k, set()).add(sid)
        resolved_added_claims[(sid, aid)] = k

    def _competitor_is_dismissible(competitor_id: str, added_id: str) -> bool:
        resolved_key = resolved_added_claims.get((competitor_id, added_id))
        if resolved_key is None:
            return False
        # Resolved on this target; dismissible only if that key carries no
        # support from anyone besides this competitor itself.
        return not (key_support.get(resolved_key, set()) - {competitor_id})

    def is_acceptable(symbol_id: str, added_id: str, key: tuple[str, str]) -> bool:
        if len(added_id_to_removed_symbols[added_id]) == 1:
            return True
        # This key itself may be reachable from >1 distinct removed symbol
        # for this SAME added identity (the added-side mirror of the
        # caller's repeated-segment collision guard) -- reject before
        # considering corroboration.
        if len(raw_added_key_targets[(added_id, key)]) != 1:
            return False
        if not key_support[key] - {symbol_id}:
            return False
        competitors = added_id_to_removed_symbols[added_id] - {symbol_id}
        return all(_competitor_is_dismissible(c, added_id) for c in competitors)

    return key_support, is_acceptable
