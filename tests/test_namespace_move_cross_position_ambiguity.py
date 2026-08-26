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

from abicheck.checker_policy import ChangeKind
from abicheck.diff_symbols_renames import (
    emit_namespace_move_batches,
    find_namespace_move_groups,
)


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
