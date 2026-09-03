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

"""ADR-063 Phase 5B: ``diff_types._diff_type_bases``'s ``FactStatus``-aware
gating of ``bases``/``virtual_bases`` — the plan's first fact-semantic-
consumption cohort.

Split out of ``test_diff_types_deep.py`` (already at the file-size hard cap
before this addition — CLAUDE.md's "Files that are large" guidance) rather
than grown there.

Before this change, ``_diff_type_bases`` read both fields through
``resolved_fact_value(fact, [])`` — collapsing ``NOT_COLLECTED``/``FAILED``/
``UNSUPPORTED`` to the same empty list a confirmed-empty ``PRESENT`` value
produces. An incomplete evidence gap on either side then read as "this side
has no bases", fabricating ``TYPE_BASE_CHANGED``/``BASE_CLASS_POSITION_
CHANGED``/``BASE_CLASS_VIRTUAL_CHANGED`` findings against real bases on the
other side purely from a capture gap, never a real hierarchy change. Each
test below pins one incomplete-evidence combination that must decline to
compare, plus a same-shaped "both sides confirmed" control proving the
fully-evidenced case is unchanged.
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Fact, RecordType


def _snap(types: list[RecordType]) -> AbiSnapshot:
    return AbiSnapshot(library="libtest.so.1", version="1.0", types=types)


def _kinds(result: object) -> set[ChangeKind]:
    return {c.kind for c in result.changes}  # type: ignore[attr-defined]


class TestBaseClassChangesEvidenceGating:
    def test_not_collected_bases_on_new_side_does_not_fabricate_base_removed(
        self,
    ) -> None:
        """Old side has a real base; new side's bases were simply never
        collected (e.g. a shallower evidence depth) — must not read as the
        base having been removed."""
        t_old = RecordType(name="Derived", kind="class", bases=["Base"])
        t_new = RecordType(
            name="Derived", kind="class", bases_fact=Fact.not_collected()
        )
        r = compare(_snap([t_old]), _snap([t_new]))
        assert ChangeKind.TYPE_BASE_CHANGED not in _kinds(r)
        assert ChangeKind.BASE_CLASS_POSITION_CHANGED not in _kinds(r)

    def test_failed_bases_on_old_side_does_not_fabricate_base_added(self) -> None:
        t_old = RecordType(
            name="Derived", kind="class", bases_fact=Fact.failed("parse error")
        )
        t_new = RecordType(name="Derived", kind="class", bases=["Base"])
        r = compare(_snap([t_old]), _snap([t_new]))
        assert ChangeKind.TYPE_BASE_CHANGED not in _kinds(r)

    def test_unsupported_bases_declines_to_compare(self) -> None:
        t_old = RecordType(name="Derived", kind="class", bases_fact=Fact.unsupported())
        t_new = RecordType(name="Derived", kind="class", bases_fact=Fact.unsupported())
        r = compare(_snap([t_old]), _snap([t_new]))
        assert ChangeKind.TYPE_BASE_CHANGED not in _kinds(r)

    def test_confirmed_empty_bases_on_both_sides_stays_a_real_comparison(
        self,
    ) -> None:
        """A fully-evidenced, confirmed-empty pair must still compare
        normally (this is real evidence of 'no bases', not a gap) — the
        behavior-preserving control for the tests above."""
        t_old = RecordType(name="Derived", kind="class", bases=[])
        t_new = RecordType(name="Derived", kind="class", bases=[])
        r = compare(_snap([t_old]), _snap([t_new]))
        assert ChangeKind.TYPE_BASE_CHANGED not in _kinds(r)

    def test_both_sides_confirmed_present_still_reports_a_real_base_change(
        self,
    ) -> None:
        """Behavior-preserving control for the fully-evidenced case: an
        actual base removal (confirmed on both sides, not just legacy
        default) must still fire exactly as before this change."""
        t_old = RecordType(name="Derived", kind="class", bases=["Base"])
        t_new = RecordType(name="Derived", kind="class", bases=[])
        r = compare(_snap([t_old]), _snap([t_new]))
        assert ChangeKind.TYPE_BASE_CHANGED in _kinds(r)

    def test_not_collected_virtual_bases_does_not_fabricate_virtual_changed(
        self,
    ) -> None:
        t_old = RecordType(
            name="Derived",
            kind="class",
            bases=["Base"],
            virtual_bases=["Base"],
        )
        t_new = RecordType(
            name="Derived",
            kind="class",
            bases=["Base"],
            virtual_bases_fact=Fact.not_collected(),
        )
        r = compare(_snap([t_old]), _snap([t_new]))
        assert ChangeKind.BASE_CLASS_VIRTUAL_CHANGED not in _kinds(r)
        assert ChangeKind.TYPE_BASE_CHANGED not in _kinds(r)

    def test_incomplete_bases_still_lets_a_comparable_virtual_bases_report(
        self,
    ) -> None:
        """The two facts gate independently: virtual_bases being fully
        evidenced still reports a real virtual-base change even though
        bases itself is incomplete on this pair — just without the finer
        became/lost-virtual classification, which needs the non-virtual
        base set too."""
        t_old = RecordType(
            name="Derived",
            kind="class",
            bases_fact=Fact.not_collected(),
            virtual_bases=["VBase"],
        )
        t_new = RecordType(
            name="Derived",
            kind="class",
            bases_fact=Fact.not_collected(),
            virtual_bases=["VBase", "VBase2"],
        )
        r = compare(_snap([t_old]), _snap([t_new]))
        assert ChangeKind.TYPE_BASE_CHANGED in _kinds(r)

    def test_incomplete_virtual_bases_still_lets_a_comparable_bases_report(
        self,
    ) -> None:
        """The symmetric case: bases fully evidenced, virtual_bases
        incomplete — the plain hierarchy change still fires."""
        t_old = RecordType(
            name="Derived",
            kind="class",
            bases=["Base"],
            virtual_bases_fact=Fact.not_collected(),
        )
        t_new = RecordType(
            name="Derived",
            kind="class",
            bases=[],
            virtual_bases_fact=Fact.not_collected(),
        )
        r = compare(_snap([t_old]), _snap([t_new]))
        assert ChangeKind.TYPE_BASE_CHANGED in _kinds(r)

    def test_virtual_only_change_does_not_duplicate_an_already_emitted_type_base_changed(
        self,
    ) -> None:
        """When a plain base-set change already appended TYPE_BASE_CHANGED,
        a co-occurring virtual-base-only set change (old_virt_set !=
        new_virt_set, but neither became_virtual nor lost_virtual --
        because the base itself was removed entirely, not merely toggled)
        must not append a second, duplicate TYPE_BASE_CHANGED."""
        t_old = RecordType(
            name="Derived",
            kind="class",
            bases=["A", "B"],
            virtual_bases=["B"],
        )
        t_new = RecordType(
            name="Derived",
            kind="class",
            bases=["A"],
            virtual_bases=[],
        )
        r = compare(_snap([t_old]), _snap([t_new]))
        base_changed = [c for c in r.changes if c.kind == ChangeKind.TYPE_BASE_CHANGED]
        assert len(base_changed) == 1

    def test_partial_bases_does_not_fabricate_base_removed(self) -> None:
        """Codex review, PR #1033: PARTIAL means the uncovered part of the
        scope is unknown, not empty -- a base absent from a PARTIAL-covered
        list may simply live in the uncovered part, so this must decline to
        compare exactly like an incomplete (NOT_COLLECTED/FAILED) pair, not
        read the partial list as the real, complete base set."""
        t_old = RecordType(name="Derived", kind="class", bases_fact=Fact.partial([]))
        t_new = RecordType(name="Derived", kind="class", bases=["Base"])
        r = compare(_snap([t_old]), _snap([t_new]))
        assert ChangeKind.TYPE_BASE_CHANGED not in _kinds(r)

    def test_partial_bases_on_new_side_does_not_fabricate_base_removed(self) -> None:
        t_old = RecordType(name="Derived", kind="class", bases=["Base"])
        t_new = RecordType(name="Derived", kind="class", bases_fact=Fact.partial([]))
        r = compare(_snap([t_old]), _snap([t_new]))
        assert ChangeKind.TYPE_BASE_CHANGED not in _kinds(r)

    def test_partial_virtual_bases_does_not_fabricate_virtual_changed(self) -> None:
        t_old = RecordType(
            name="Derived",
            kind="class",
            bases=["Base"],
            virtual_bases_fact=Fact.partial([]),
        )
        t_new = RecordType(
            name="Derived",
            kind="class",
            bases=["Base"],
            virtual_bases=["Base"],
        )
        r = compare(_snap([t_old]), _snap([t_new]))
        assert ChangeKind.BASE_CLASS_VIRTUAL_CHANGED not in _kinds(r)
        assert ChangeKind.TYPE_BASE_CHANGED not in _kinds(r)
