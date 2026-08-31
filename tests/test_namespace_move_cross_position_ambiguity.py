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

"""abicheck code-review report item 6: cross-position ambiguity in the
namespace-move roll-up (``diff_symbols_renames.find_namespace_move_groups``)
could silently drop a genuine, fully-supported namespace move to below its
2+-pair reporting threshold, whenever one of its member symbols happened to
also have an unrelated, coincidental candidacy at a different masking
position.

Kept in its own file (not ``tests/test_batch_rename_namespace_move.py``,
which is an ADR-061 no-growth-baselined legacy module already at its
line-budget cap) even though it tests the same function -- see that file's
own extensive test suite for the pre-existing ambiguity-guard coverage this
fix does not disturb (confirmed: its full 104-test suite passes unchanged
alongside this fix).
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.diff_symbols_renames import (
    emit_namespace_move_batches,
    find_namespace_move_groups,
)
from abicheck.model import AbiSnapshot, Function, Visibility


class TestCrossPositionAmbiguityResolvesViaGlobalSupport:
    """A real, 2-pair namespace move (``P::old::{f,g}`` -> ``P::new::{f,g}``)
    was silently reduced to a single pair -- dropping it below the
    2+-pair threshold entirely -- whenever one of its symbols also had an
    unrelated, coincidental candidacy at a DIFFERENT masking position.
    Here ``P::old::f`` masked at position 0 (blanking ``P``) also matches
    the unrelated addition ``Q::old::f``, making
    ``removed_id_to_added_symbols["P::old::f"]`` size 2 -- genuinely
    ambiguous in isolation. But the ``(old, new)`` substitution is
    independently corroborated by ``P::old::g``, which has no competing
    candidacy at all, while the competing key (from the ``Q::old::f``
    match) has no support from any OTHER symbol -- so the ambiguity
    resolves in favor of the corroborated key."""

    def test_the_full_two_pair_move_is_recovered(self) -> None:
        removed = {"_ZN1P3old1fEv", "_ZN1P3old1gEv"}
        added = {"_ZN1P3new1fEv", "_ZN1P3new1gEv", "_ZN1Q3old1fEv"}
        groups = find_namespace_move_groups(removed, added)
        assert ("old", "new") in groups
        assert len(groups[("old", "new")]) == 2
        assert ("P::old::f", "P::new::f") in groups[("old", "new")]
        assert ("P::old::g", "P::new::g") in groups[("old", "new")]

    def test_a_real_batch_is_emitted_end_to_end(self) -> None:
        changes = emit_namespace_move_batches(
            find_namespace_move_groups(
                {"_ZN1P3old1fEv", "_ZN1P3old1gEv"},
                {"_ZN1P3new1fEv", "_ZN1P3new1gEv", "_ZN1Q3old1fEv"},
            )
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.SYMBOL_RENAMED_BATCH

    def test_a_genuine_tie_between_two_corroborated_keys_still_rejects(self) -> None:
        """When BOTH of a symbol's competing keys are independently
        corroborated by other symbols, there is no basis to prefer one --
        the pre-existing, unresolvable-ambiguity behavior must still hold.
        ``P::old::f`` is ambiguous between key ``(P, also)`` [masking
        position 0, corroborated by ``P::old::h -> also::old::h``] and key
        ``(old, new)`` [masking position 1, corroborated by
        ``P::old::g -> P::new::g``] -- a genuine tie, not a one-sided
        resolution."""
        removed = {"_ZN1P3old1fEv", "_ZN1P3old1hEv", "_ZN1P3old1gEv"}
        added = {
            "_ZN4also3old1fEv",
            "_ZN4also3old1hEv",
            "_ZN1P3new1fEv",
            "_ZN1P3new1gEv",
        }
        groups = find_namespace_move_groups(removed, added)
        # P::old::f's own contested pair never joins any group -- only the
        # two OTHER symbols' independent, unambiguous single-pair
        # candidacies form (single-pair groups, below the batch threshold,
        # but real groups nonetheless -- see test_batch_rename_namespace_
        # move.py's test_one_supporting_pair_is_not_a_batch for that
        # threshold, which lives in emit_*, not here).
        for pairs in groups.values():
            assert ("P::old::f", "P::new::f") not in pairs
            assert ("P::old::f", "also::old::f") not in pairs
        assert groups.get(("old", "new")) == [("P::old::g", "P::new::g")]
        assert groups.get(("P", "also")) == [("P::old::h", "also::old::h")]

    def test_no_corroboration_at_all_still_rejects(self) -> None:
        """A symbol ambiguous between two candidates, NEITHER of which any
        other symbol supports, has nothing to resolve the tie with --
        must stay rejected exactly as before this fix."""
        removed = {"_ZN1P3old1fEv"}
        added = {"_ZN1P3new1fEv", "_ZN1Q3old1fEv"}
        groups = find_namespace_move_groups(removed, added)
        assert groups == {}

    def test_a_locally_rejected_key_still_counts_as_a_competitor(self) -> None:
        """Codex review (fresh evidence): ``f``'s own attempt at key
        ``(P, new)`` is locally ambiguous (masking position 0 matches both
        ``new::old::f`` and ``Q::old::f``) and so never gets its own
        ``entries`` row -- but ``h`` independently and unambiguously
        resolves that same key (``P::old::h`` -> ``new::old::h``), which is
        real corroboration for it. ``f``'s OTHER candidacy, key
        ``(old, new)`` at position 1, is independently corroborated by
        ``g``. Both of f's competing keys are therefore corroborated by a
        different symbol -- a genuine tie -- so f must be rejected from
        BOTH groups, leaving each at a single supporting pair (below the
        2+-pair batch threshold), not a false 2-pair
        ``SYMBOL_RENAMED_BATCH``."""
        removed = {"_ZN1P3old1fEv", "_ZN1P3old1gEv", "_ZN1P3old1hEv"}
        added = {
            "_ZN1P3new1fEv",
            "_ZN1P3new1gEv",
            "_ZN3new3old1fEv",
            "_ZN3new3old1hEv",
            "_ZN1Q3old1fEv",
        }
        groups = find_namespace_move_groups(removed, added)
        for pairs in groups.values():
            assert ("P::old::f", "P::new::f") not in pairs
            assert ("P::old::f", "new::old::f") not in pairs
        assert groups.get(("old", "new")) == [("P::old::g", "P::new::g")]
        assert groups.get(("P", "new")) == [("P::old::h", "new::old::h")]
        changes = emit_namespace_move_batches(groups)
        assert changes == []

    def test_a_repeated_segment_producing_the_same_key_stays_ambiguous(self) -> None:
        """Codex review (fresh evidence): removing ``old::old::{f,g}`` while
        adding both ``new::old::{f,g}`` (position-0 substitution) and
        ``old::new::{f,g}`` (position-1 substitution) makes every removed
        symbol resolve to TWO distinct added declarations that both happen
        to key as the identical ``(old, new)`` text -- the corroboration
        check alone cannot tell them apart (both keys are literally the
        same string), so the earlier fix's `other_keys` computation saw no
        competing key at all and let the first-seen candidacy through,
        silently dropping the other target. There is no way to tell
        whether ``old::old`` moved to ``new::old`` or to ``old::new``, so
        this must reject entirely -- not report either as a fabricated
        2-pair batch."""
        removed = {"_ZN3old3old1fEv", "_ZN3old3old1gEv"}
        added = {
            "_ZN3new3old1fEv",
            "_ZN3new3old1gEv",
            "_ZN3old3new1fEv",
            "_ZN3old3new1gEv",
        }
        groups = find_namespace_move_groups(removed, added)
        assert groups == {}
        assert emit_namespace_move_batches(groups) == []


class TestAddedSideCrossPositionAmbiguityAlsoResolvesViaGlobalSupport:
    """Follow-up to a later code-review report's own item 6: the corroboration
    test above (``removed_id_to_added_symbols`` ambiguity -- ONE removed
    symbol with multiple candidate added targets) was never extended to its
    exact mirror -- ``added_id_to_removed_symbols`` ambiguity, ONE added
    symbol claimed by multiple distinct removed identities. That asymmetry
    silently dropped a real batch member (or, in the case below, an entire
    batch under its 2+-pair threshold) whenever an unrelated, isolated,
    uncorroborated removed identity happened to coincidentally collide with
    one of the batch's own added targets -- reported as "batch still cites
    7 of 15" against a real oneTBB comparison.

    The fix cannot just reuse the removed-side test's shape: there, every
    competing key belongs to the SAME removed symbol (its own alternate
    masking positions), so subtracting that one symbol from a key's
    supporter set is enough to ask "does anyone else back this option". On
    the added side, each competing key belongs to a DIFFERENT removed
    identity, and that competitor may itself never have resolved to any
    single key at all (see
    ``TestFindNamespaceMoveGroupsRetainsLocallyAmbiguousCandidatesGlobally``
    in ``test_batch_rename_namespace_move.py`` -- confirmed unaffected by
    this fix) -- an unresolved competitor is a live, irreducible threat and
    must still veto. Only a competitor that DID resolve to its own key can
    be assessed, and only dismissed when that key carries no support from
    anyone besides the competitor itself.
    """

    def test_a_real_batch_below_threshold_is_recovered(self) -> None:
        """Without the fix: `P::new::f` -> `Q::new::f` is unconditionally
        rejected because `Q::new::f` is ALSO claimed by the coincidental,
        single-member `Q::old::f` (an unrelated, uncorroborated "old" ->
        "new" leaf rename) -- dropping the real "P" -> "Q" substitution to
        a single supporting pair (`P::new::g` -> `Q::new::g` alone), below
        `emit_namespace_move_batches`' 2+-pair threshold, so the whole
        batch silently never gets reported at all."""
        removed = {"_ZN1P3new1fEv", "_ZN1P3new1gEv", "_ZN1Q3old1fEv"}
        added = {"_ZN1Q3new1fEv", "_ZN1Q3new1gEv"}
        groups = find_namespace_move_groups(removed, added)
        assert groups.get(("P", "Q")) == [
            ("P::new::f", "Q::new::f"),
            ("P::new::g", "Q::new::g"),
        ]
        # The isolated coincidence itself never forms its own group -- its
        # own key has no corroboration beyond itself either.
        assert ("old", "new") not in groups

        changes = emit_namespace_move_batches(groups)
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.SYMBOL_RENAMED_BATCH

    def test_a_real_batch_below_threshold_is_recovered_through_compare(self) -> None:
        """The same scenario as
        `test_a_real_batch_below_threshold_is_recovered` above, but through
        the real public entry point (`compare`), the same way
        `test_batch_rename_namespace_move.py`'s own end-to-end tests do."""
        old = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="P::new::f",
                    mangled="_ZN1P3new1fEv",
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                ),
                Function(
                    name="P::new::g",
                    mangled="_ZN1P3new1gEv",
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                ),
                Function(
                    name="Q::old::f",
                    mangled="_ZN1Q3old1fEv",
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                ),
            ],
        )
        new = AbiSnapshot(
            library="libfoo.so",
            version="2.0",
            functions=[
                Function(
                    name="Q::new::f",
                    mangled="_ZN1Q3new1fEv",
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                ),
                Function(
                    name="Q::new::g",
                    mangled="_ZN1Q3new1gEv",
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                ),
            ],
        )
        result = compare(old, new)
        batch = [c for c in result.changes if c.kind is ChangeKind.SYMBOL_RENAMED_BATCH]
        assert batch, "the 'P' -> 'Q' namespace move produced no batch roll-up"
        assert "P" in batch[0].description and "Q" in batch[0].description

    def test_a_repeated_segment_collision_on_the_added_side_still_rejects(
        self,
    ) -> None:
        """The added-side mirror of `raw_symbol_key_targets`'s
        repeated-segment guard: `old::new::f` (masked at position 0:
        "old" -> "new") and `new::old::f` (masked at position 1:
        "old" -> "new") both key as the IDENTICAL text ("old", "new") AND
        both converge on the SAME added declaration `new::new::f` -- an
        irresolvable ambiguity about which removed declaration is the real
        source, not real corroboration, so this must reject entirely."""
        removed = {"_ZN3old3new1fEv", "_ZN3new3old1fEv"}
        added = {"_ZN3new3new1fEv"}
        groups = find_namespace_move_groups(removed, added)
        assert groups == {}

    def test_a_genuine_added_side_tie_still_rejects(self) -> None:
        """When the competing removed identity (`Q::old::f`, proposing an
        unrelated "old" -> "new" leaf substitution for the SAME added
        target `Q::new::f` the "P" -> "Q" batch wants) is ITSELF
        independently corroborated by another member (`R::old::h` ->
        `R::new::h`, also "old" -> "new"), the ambiguity is real and
        neither side yields for the contested pair: it drops out of BOTH
        candidate groups, leaving each at a single, unambiguous
        supporting pair -- correctly below the 2+-pair batch-emission
        threshold, not a fabricated 2-pair batch on either side."""
        removed = {
            "_ZN1P3new1fEv",
            "_ZN1P3new1gEv",
            "_ZN1Q3old1fEv",
            "_ZN1R3old1hEv",
        }
        added = {
            "_ZN1Q3new1fEv",
            "_ZN1Q3new1gEv",
            "_ZN1R3new1hEv",
        }
        groups = find_namespace_move_groups(removed, added)
        # The contested pair joins neither group...
        assert ("P::new::f", "Q::new::f") not in groups.get(("P", "Q"), [])
        assert ("Q::old::f", "Q::new::f") not in groups.get(("old", "new"), [])
        # ...but each side's own unambiguous, unrelated member still does.
        assert groups.get(("P", "Q")) == [("P::new::g", "Q::new::g")]
        assert groups.get(("old", "new")) == [("R::old::h", "R::new::h")]
        changes = emit_namespace_move_batches(groups)
        assert changes == []
