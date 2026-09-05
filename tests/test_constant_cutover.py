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

"""The constant detector family after its cutover onto ``SemanticIRIndex``
(ADR-063 Phase 6B's second detector cohort), plus the architecture gate that
keeps it migrated.

Mirrors ``tests/test_typedef_cutover.py`` for cohort 1: the bug *class*
under test is "a migrated detector produces different findings depending on
which index backed it", checked as an equivalence over generated constant
map pairs -- the same comparison run once through a real ``SemanticIR`` and
once through the legacy adapter must produce identical findings, against an
oracle (the adapter path, the pre-migration behavior) that is not the
mechanism under test.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.checker_policy import ChangeKind
from abicheck.compare.constants import constant_index_pair, diff_constants
from abicheck.model import AbiSnapshot
from abicheck.model.fact import Fact
from abicheck.model.identity import (
    Anonymous,
    EntityKind,
    Namespace,
    entity_id_for_constant,
)
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_index import SemanticIRIndex
from abicheck.model.semantic_ir_legacy_adapter import legacy_constant_ir


def _snap(**kw) -> AbiSnapshot:
    base = dict(
        library="libfoo.so.1",
        version="1.0.0",
        from_headers=True,
        from_headers_inferred=False,
    )
    base.update(kw)
    return AbiSnapshot(**base)


def _never_unreliable(old_value: str, new_value: str) -> bool:
    return False


def _run(old_index, new_index, **kw):
    return diff_constants(
        old_index,
        new_index,
        is_fingerprint_comparison_unreliable=kw.get("unreliable", _never_unreliable),
        old_constants=kw.get("old_constants", {}),
        new_constants=kw.get("new_constants", {}),
    )


def _adapted(constants: dict[str, str], snapshot: AbiSnapshot | None = None):
    return SemanticIRIndex(legacy_constant_ir(snapshot or _snap(), constants))


def _ir_backed(constants: dict[str, str]) -> SemanticIRIndex:
    """A *real* ``SemanticIR``, built the way ``extract/
    semantic_normalizer.py`` builds one: a resolved ``EntityId`` per
    qualified name, payload in ``canonical_spelling``."""
    occurrences = {}
    for name, value in constants.items():
        *scope, leaf = name.split("::")
        eid = entity_id_for_constant(tuple(Namespace(s) for s in scope), leaf)
        occurrences[OccurrenceId(eid)] = CanonicalEntity(
            canonical_spelling=Fact.present(value)
        )
    return SemanticIRIndex(SemanticIR(occurrences=occurrences))


def _summary(changes):
    """Findings in *emission order*, deliberately not sorted -- see
    ``test_typedef_cutover.py``'s identical helper for why."""
    return [
        (c.kind, c.symbol, c.old_value, c.new_value, c.description) for c in changes
    ]


# -- the equivalence that makes the cutover safe ---------------------------

_name = st.sampled_from(["A", "B", "ns::A", "ns::B", "ns::inner::C", "D"])
_value = st.sampled_from(["42", "0x1", '"hello"', "3.14", "-1"])
_maps = st.dictionaries(_name, _value, max_size=5)


class TestBackendEquivalence:
    @given(old=_maps, new=_maps)
    @settings(max_examples=120, deadline=None)
    def test_ir_backed_and_adapted_indexes_produce_identical_findings(
        self, old: dict[str, str], new: dict[str, str]
    ) -> None:
        """The property the whole cutover rests on. Generated over add/
        remove/change/unchanged combinations across five names (bare,
        singly and doubly qualified) and five value spellings."""
        via_ir = _run(_ir_backed(old), _ir_backed(new))
        via_adapter = _run(_adapted(old), _adapted(new))
        assert _summary(via_ir) == _summary(via_adapter)

    @given(old=_maps, new=_maps)
    @settings(max_examples=60, deadline=None)
    def test_only_the_ir_path_stamps_a_producer_identity(
        self, old: dict[str, str], new: dict[str, str]
    ) -> None:
        """The one deliberate difference between the two paths, pinned so it
        cannot invert: a real backend identity reaches ``Change.entity_id``;
        the adapter's synthesized one never does."""
        for change in _run(_adapted(old), _adapted(new)):
            assert change.entity_id is None
        for change in _run(_ir_backed(old), _ir_backed(new)):
            assert change.entity_id is not None
            assert change.entity_id.kind is EntityKind.CONSTANT

    def test_the_real_ir_own_order_is_emitted_directly(self) -> None:
        """ADR-063 Track T3 -- see ``test_typedef_cutover.py``'s identical
        test for the full reasoning: no more comparison-time adjudication
        against a legacy map's order once both sides carry a real IR."""
        maps = {"A": "1", "B": "1", "C": "1"}
        reordered = {"C": "1", "A": "1", "B": "1"}
        snap = _snap(constants=maps, semantic_ir=_ir_backed(reordered).ir)
        old_index, _ = constant_index_pair(
            snap, snap, old_constants=maps, new_constants=maps
        )
        emitted = [c.symbol for c in _run(old_index, SemanticIRIndex(SemanticIR()))]
        assert emitted == ["C", "A", "B"]


# -- behavior preservation, case by case -----------------------------------


class TestDetectorBehavior:
    def test_removal_change_and_no_op(self) -> None:
        changes = _run(
            _ir_backed({"gone": "1", "moved": "1", "same": "1"}),
            _ir_backed({"moved": "2", "same": "1"}),
        )
        by_kind = {c.kind: c for c in changes}
        assert set(by_kind) == {
            ChangeKind.CONSTANT_REMOVED,
            ChangeKind.CONSTANT_CHANGED,
        }
        assert by_kind[ChangeKind.CONSTANT_REMOVED].symbol == "gone"
        assert by_kind[ChangeKind.CONSTANT_CHANGED].old_value == "1"
        assert by_kind[ChangeKind.CONSTANT_CHANGED].new_value == "2"

    def test_addition_is_reported(self) -> None:
        (change,) = _run(_ir_backed({}), _ir_backed({"new_const": "1"}))
        assert change.kind is ChangeKind.CONSTANT_ADDED
        assert change.symbol == "new_const"
        assert change.new_value == "1"

    def test_an_unreliable_fingerprint_comparison_is_not_a_reported_change(
        self,
    ) -> None:
        def always_unreliable(old_value: str, new_value: str) -> bool:
            return True

        changes = _run(
            _ir_backed({"x": "expr:aaaaaaaaaaaaaaaa"}),
            _ir_backed({"x": "expr:bbbbbbbbbbbbbbbb"}),
            unreliable=always_unreliable,
        )
        assert changes == []

    def test_unsupported_fact_yields_no_comparable_value_with_no_legacy_fallback(
        self,
    ) -> None:
        """A ``Fact.unsupported()`` occurrence (the clang compound-
        initializer-fingerprint/bool-literal case, see ``extract/
        semantic_normalizer.py``) carries no value text on the ``Fact``
        itself -- skipped rather than compared against ``None`` when there
        is also no ``AbiSnapshot.constants`` fallback text to use instead
        (see ``test_a_same_backend_value_change_hidden_by_an_unsupported_
        fact_is_still_caught`` for the case where there is one)."""
        unsupported = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(entity_id_for_constant((), "X")): CanonicalEntity(
                        canonical_spelling=Fact.unsupported("not comparable")
                    )
                }
            )
        )
        assert _run(unsupported, unsupported) == []
        assert _run(unsupported, _ir_backed({"X": "1"})) == []
        assert _run(_ir_backed({"X": "1"}), unsupported) == []

    def test_a_same_backend_value_change_hidden_by_an_unsupported_fact_is_still_caught(
        self,
    ) -> None:
        """Codex review, PR #1078, second round: two same-backend snapshots
        both carrying "X" with an unsupported canonical spelling (e.g. a
        clang compound-initializer fingerprint that genuinely changed) must
        still report ``CONSTANT_CHANGED`` by falling back to each
        snapshot's own flat ``AbiSnapshot.constants`` raw text -- the same
        text the pre-T3 legacy-only path always compared directly, before
        ``SemanticIR`` authority made ``_value`` reach ``None`` for this
        case at all."""
        eid = entity_id_for_constant((), "X")
        unsupported_ir = SemanticIR(
            occurrences={
                OccurrenceId(eid): CanonicalEntity(
                    canonical_spelling=Fact.unsupported("not comparable")
                )
            }
        )
        old_index = SemanticIRIndex(unsupported_ir)
        new_index = SemanticIRIndex(unsupported_ir)
        (change,) = _run(
            old_index,
            new_index,
            old_constants={"X": "expr:aaaaaaaaaaaaaaaa"},
            new_constants={"X": "expr:bbbbbbbbbbbbbbbb"},
        )
        assert change.kind is ChangeKind.CONSTANT_CHANGED
        assert change.old_value == "expr:aaaaaaaaaaaaaaaa"
        assert change.new_value == "expr:bbbbbbbbbbbbbbbb"

    def test_the_legacy_fallback_still_defers_to_the_unreliable_predicate(
        self,
    ) -> None:
        """The fallback value comparison is still gated through
        *is_fingerprint_comparison_unreliable* exactly like any other
        fingerprint comparison -- it does not bypass that safety check."""
        eid = entity_id_for_constant((), "X")
        unsupported_ir = SemanticIR(
            occurrences={
                OccurrenceId(eid): CanonicalEntity(
                    canonical_spelling=Fact.unsupported("not comparable")
                )
            }
        )
        old_index = SemanticIRIndex(unsupported_ir)
        new_index = SemanticIRIndex(unsupported_ir)
        changes = _run(
            old_index,
            new_index,
            old_constants={"X": "expr:aaaaaaaaaaaaaaaa"},
            new_constants={"X": "expr:bbbbbbbbbbbbbbbb"},
            unreliable=lambda old_value, new_value: True,
        )
        assert changes == []

    def test_a_newly_added_unsupported_fact_is_still_reported_as_an_addition(
        self,
    ) -> None:
        """A membership change is real regardless of whether the constant's
        own value is comparable (Codex review, PR #1078): a constant that
        only exists on the *new* side and carries an unsupported fact is
        still a genuine addition -- it must not be silently dropped just
        because ``new_value`` cannot be rendered. Distinct code path from
        ``test_unsupported_fact_yields_no_comparable_value``'s cases, none
        of which exercise a name absent from ``old_values`` entirely."""
        unsupported = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(entity_id_for_constant((), "X")): CanonicalEntity(
                        canonical_spelling=Fact.unsupported("not comparable")
                    )
                }
            )
        )
        (change,) = _run(_ir_backed({}), unsupported)
        assert change.kind is ChangeKind.CONSTANT_ADDED
        assert change.symbol == "X"
        assert change.new_value is None

    def test_a_removed_unsupported_fact_is_still_reported_as_a_removal(
        self,
    ) -> None:
        """The removal-side mirror of the addition test above: a constant
        that only exists on the *old* side and carries an unsupported fact
        is still a genuine removal."""
        unsupported = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(entity_id_for_constant((), "X")): CanonicalEntity(
                        canonical_spelling=Fact.unsupported("not comparable")
                    )
                }
            )
        )
        (change,) = _run(unsupported, _ir_backed({}))
        assert change.kind is ChangeKind.CONSTANT_REMOVED
        assert change.symbol == "X"
        assert change.old_value is None

    def test_a_whole_colliding_group_disappearing_reports_every_removal(
        self,
    ) -> None:
        """Codex review, PR #1078, twelfth round: two anonymous-scoped
        constants collide on ``X`` and the whole group vanishes on the new
        side -- not merely shrinks. Both are independent, real removals,
        not just `old_ids[0]`."""
        first = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        second = entity_id_for_constant((Anonymous("namespace", 1),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(second): CanonicalEntity(
                        canonical_spelling=Fact.present("2")
                    ),
                }
            )
        )
        changes = _run(old_index, SemanticIRIndex(SemanticIR()))
        assert len(changes) == 2
        assert all(c.kind is ChangeKind.CONSTANT_REMOVED for c in changes)
        assert {c.old_value for c in changes} == {"1", "2"}

    def test_a_whole_new_colliding_group_reports_every_addition(self) -> None:
        """The addition-side mirror: an entirely new colliding group (not
        present in `old_values` at all) can carry more than one distinct
        entity, each an independent addition."""
        first = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        second = entity_id_for_constant((Anonymous("namespace", 1),), "X")
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(second): CanonicalEntity(
                        canonical_spelling=Fact.present("2")
                    ),
                }
            )
        )
        changes = _run(SemanticIRIndex(SemanticIR()), new_index)
        assert len(changes) == 2
        assert all(c.kind is ChangeKind.CONSTANT_ADDED for c in changes)
        assert {c.new_value for c in changes} == {"1", "2"}

    def test_an_unrenderable_scope_segment_falls_back_to_its_bare_leaf_name(
        self,
    ) -> None:
        """An entity whose scope contains an ``Anonymous`` segment has no
        *faithful* flat spelling (``render_display_name`` returns ``None``,
        see ``semantic_ir_legacy_adapter.py``'s own docstring), but
        ``_values`` still keys it under its bare leaf name via
        ``render_display_name_or_leaf`` (Codex review, PR #1078, fourth
        round) -- the same name the legacy adapter's own synthetic identity
        already surfaces for it. A same-leaf value change across two
        *different* anonymous scopes is still detected, which the strict
        renderer would have made invisible entirely."""
        old_id = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        new_id = entity_id_for_constant((Anonymous("namespace", 1),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(old_id): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    )
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(new_id): CanonicalEntity(
                        canonical_spelling=Fact.present("2")
                    )
                }
            )
        )
        assert _run(old_index, old_index) == []
        (change,) = _run(old_index, new_index)
        assert change.kind is ChangeKind.CONSTANT_CHANGED
        assert change.symbol == "X"
        assert change.old_value == "1"
        assert change.new_value == "2"

        old = _snap(constants={}, semantic_ir=old_index.ir)
        new = _snap(constants={}, semantic_ir=new_index.ir)
        pair_old_index, pair_new_index = constant_index_pair(
            old, new, old_constants={}, new_constants={}
        )
        # Both sides carry a real SemanticIR, so it is used directly.
        assert isinstance(pair_old_index, SemanticIRIndex)
        assert isinstance(pair_new_index, SemanticIRIndex)
        assert old_id in pair_old_index.entities_of_kind(EntityKind.CONSTANT)

    def test_a_value_change_on_one_of_two_colliding_anonymous_constants_is_still_caught(
        self,
    ) -> None:
        """Mirrors ``test_typedef_cutover.py``'s identical fix (Codex review,
        PR #1078, sixth round): two constants declared in two distinct
        anonymous namespaces both render to the bare leaf name ``X``.
        Picking an arbitrary representative per side used to mean a real
        value change on whichever occurrence didn't become the
        representative was silently read as "unchanged" whenever the
        *other* occurrence's value happened to match across sides."""
        first_old = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        second_old = entity_id_for_constant((Anonymous("namespace", 1),), "X")
        first_new = entity_id_for_constant((Anonymous("namespace", 2),), "X")
        second_new = entity_id_for_constant((Anonymous("namespace", 3),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first_old): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(second_old): CanonicalEntity(
                        canonical_spelling=Fact.present("2")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first_new): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(second_new): CanonicalEntity(
                        canonical_spelling=Fact.present("3")
                    ),
                }
            )
        )
        (change,) = _run(old_index, new_index)
        assert change.kind is ChangeKind.CONSTANT_CHANGED
        assert change.symbol == "X"
        assert change.old_value == "2"
        assert change.new_value == "3"

    def test_a_membership_change_masked_by_an_unsupported_occurrence_is_still_caught(
        self,
    ) -> None:
        """Codex review, PR #1078, eighth round: the old side has two
        occurrences colliding on ``X`` -- one comparable (``"1"``) and one
        ``Fact.unsupported()`` with no legacy fallback text -- while the new
        side has only the comparable one. Filtering out the unsupported
        value before comparing left both sides' *filtered* multisets reading
        ``["1"]``, so the group's own shrinking from two occurrences to one
        was invisible even though a real removal happened.

        Classified as ``CONSTANT_REMOVED`` (not ``CONSTANT_CHANGED``) since
        the ninth round's own ``Counter``-based multiset fix: the
        comparable value (``"1"``) is present on both sides unchanged, and
        only the unresolved occurrence's own net removal differs -- a pure
        shrink, not a value edit."""
        comparable_old = entity_id_for_constant((), "X")
        unsupported_old = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        comparable_new = entity_id_for_constant((), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(comparable_old): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(unsupported_old): CanonicalEntity(
                        canonical_spelling=Fact.unsupported("not comparable")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(comparable_new): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    )
                }
            )
        )
        (change,) = _run(old_index, new_index)
        assert change.kind is ChangeKind.CONSTANT_REMOVED
        assert change.symbol == "X"
        assert change.old_value is None
        assert change.new_value is None

    def test_a_stable_identitys_own_value_change_is_not_masked_by_a_new_arrival(
        self,
    ) -> None:
        """Codex review, PR #1078, thirteenth round: a stable, real-backend
        identity ``X`` changes from ``1`` to ``2`` while a *different*,
        newly-added anonymous-scope ``X`` arrives with value ``1``.
        Value-only multiset matching cancels the stable entity's old ``1``
        against the new entity's ``1``, reporting only a compatible-looking
        ``CONSTANT_ADDED`` for value ``2`` -- silently masking the real,
        breaking change to the stable entity and never reporting the
        genuine addition at all. Matching by shared ``EntityId`` first
        must catch the stable entity's own value change directly, and the
        addition must still be reported separately."""
        stable = entity_id_for_constant((Namespace("ns"),), "X")
        added = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    )
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable): CanonicalEntity(
                        canonical_spelling=Fact.present("2")
                    ),
                    OccurrenceId(added): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                }
            )
        )
        changes = _run(old_index, new_index)
        by_kind = {c.kind: c for c in changes}
        assert set(by_kind) == {ChangeKind.CONSTANT_CHANGED, ChangeKind.CONSTANT_ADDED}
        changed = by_kind[ChangeKind.CONSTANT_CHANGED]
        assert changed.old_value == "1"
        assert changed.new_value == "2"
        assert changed.entity_id == stable
        added_change = by_kind[ChangeKind.CONSTANT_ADDED]
        assert added_change.new_value == "1"
        assert added_change.entity_id == added

    def test_a_duplicate_value_added_to_a_colliding_group_is_an_addition_not_a_change(
        self,
    ) -> None:
        """Codex review, PR #1078, ninth round: the new side adds a second
        anonymous-namespace ``X=1`` alongside the pre-existing ``X=1`` --
        the group grows from one occurrence to two, both sharing the
        identical value. A sorted-list multiset-equality check cannot tell
        this apart from "an equal-length group's value changed and
        happens to coincide" -- with a naive representative pick this
        used to fall through into ``CONSTANT_CHANGED`` (an API break) for a
        purely compatible addition. ``Counter`` subtraction has no such
        blind spot: nothing was removed, one ``"1"`` was net-added."""
        old_id = entity_id_for_constant((), "X")
        new_first = entity_id_for_constant((), "X")
        new_second = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(old_id): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    )
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(new_first): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(new_second): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                }
            )
        )
        (change,) = _run(old_index, new_index)
        assert change.kind is ChangeKind.CONSTANT_ADDED
        assert change.symbol == "X"
        assert change.new_value == "1"

    def test_a_duplicate_value_removed_from_a_colliding_group_is_a_removal(
        self,
    ) -> None:
        """The mirror image of the addition case above: the group shrinks
        from two occurrences (both ``"1"``) to one, and must be classified
        ``CONSTANT_REMOVED``, not ``CONSTANT_CHANGED``."""
        old_first = entity_id_for_constant((), "X")
        old_second = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        new_id = entity_id_for_constant((), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(old_first): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(old_second): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(new_id): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    )
                }
            )
        )
        (change,) = _run(old_index, new_index)
        assert change.kind is ChangeKind.CONSTANT_REMOVED
        assert change.symbol == "X"
        assert change.old_value == "1"

    def test_a_shared_legacy_fallback_is_not_misattributed_across_a_collision(
        self,
    ) -> None:
        """Codex review, PR #1078, ninth round: two anonymous-namespace
        constants collide on ``X`` -- one retains its comparable value
        (``"1"``, genuinely unchanged across sides) and the other is
        ``Fact.unsupported()`` on both sides. ``AbiSnapshot.constants``
        retains only *one* raw value per bare name -- whichever occurrence's
        own parse happened to win that same collision upstream -- and that
        single flat-map entry differs between the old and new snapshot here
        (``"expr:aaaa..."`` vs. ``"expr:bbbb..."``) for reasons entirely
        unrelated to either occurrence's own real value (e.g. which
        occurrence the flat map's own construction happened to retain).
        Applying this name-level text as a *per-occurrence* fallback (the
        pre-ninth-round behavior) would misattribute it to the unsupported
        occurrence and fabricate a ``CONSTANT_CHANGED`` finding from data
        that was never actually this occurrence's own evidence. The
        collision path does not consult the fallback at all, so no such
        finding is fabricated -- the comparable occurrence is genuinely
        unchanged, and the unresolved one contributes no evidence either
        way on either side."""
        stable_old = entity_id_for_constant((), "X")
        unsupported_old = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        stable_new = entity_id_for_constant((), "X")
        unsupported_new = entity_id_for_constant((Anonymous("namespace", 1),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable_old): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(unsupported_old): CanonicalEntity(
                        canonical_spelling=Fact.unsupported("not comparable")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable_new): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(unsupported_new): CanonicalEntity(
                        canonical_spelling=Fact.unsupported("not comparable")
                    ),
                }
            )
        )
        changes = _run(
            old_index,
            new_index,
            old_constants={"X": "expr:aaaaaaaaaaaaaaaa"},
            new_constants={"X": "expr:bbbbbbbbbbbbbbbb"},
        )
        assert changes == []

    def test_a_whole_removed_groups_own_fallback_is_not_shared_across_members(
        self,
    ) -> None:
        """Codex review, PR #1078, thirteenth round: two anonymous-scoped,
        unsupported-valued constants collide on ``X`` and the whole group
        vanishes at once. ``AbiSnapshot.constants`` retains only one raw
        value per bare name -- applying it as a per-occurrence fallback to
        *both* removed entities would credit the same borrowed text to two
        genuinely different declarations. With more than one entity in the
        vanishing group, the fallback must not be consulted at all -- each
        removal reports ``old_value=None``, same as the general collision
        path's own identical rule."""
        first = entity_id_for_constant((), "X")
        second = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first): CanonicalEntity(
                        canonical_spelling=Fact.unsupported("not comparable")
                    ),
                    OccurrenceId(second): CanonicalEntity(
                        canonical_spelling=Fact.unsupported("not comparable")
                    ),
                }
            )
        )
        changes = _run(
            old_index,
            SemanticIRIndex(SemanticIR()),
            old_constants={"X": "expr:aaaaaaaaaaaaaaaa"},
        )
        assert len(changes) == 2
        assert all(c.kind is ChangeKind.CONSTANT_REMOVED for c in changes)
        assert all(c.old_value is None for c in changes)

    def test_a_mixed_group_reports_both_the_change_and_the_residual_addition(
        self,
    ) -> None:
        """Codex review, PR #1078, tenth round: a stable-identity `X=1`
        becomes `X=2` while a *different*, newly-added anonymous-scope
        `X=3` also appears in the same colliding group -- `removed={1}`,
        `added={2, 3}`. Pairing off one removed value with one added value
        as a single ``CONSTANT_CHANGED`` story must not silently drop the
        independently provable ``CONSTANT_ADDED`` for the leftover value."""
        stable_old = entity_id_for_constant((), "X")
        stable_new = entity_id_for_constant((), "X")
        added_new = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable_old): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    )
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable_new): CanonicalEntity(
                        canonical_spelling=Fact.present("2")
                    ),
                    OccurrenceId(added_new): CanonicalEntity(
                        canonical_spelling=Fact.present("3")
                    ),
                }
            )
        )
        changes = _run(old_index, new_index)
        by_kind = {c.kind: c for c in changes}
        assert set(by_kind) == {ChangeKind.CONSTANT_CHANGED, ChangeKind.CONSTANT_ADDED}
        assert by_kind[ChangeKind.CONSTANT_CHANGED].old_value == "1"
        # Since the eleventh round's occurrence-level (dict-ordered, not
        # `set`-ordered) rewrite, which value pairs into the ``CHANGED``
        # finding is deterministic -- the first-encountered added value in
        # insertion order ("2", from `stable_new`, listed first) -- rather
        # than depending on `PYTHONHASHSEED`. Both values are accounted
        # for regardless.
        assert by_kind[ChangeKind.CONSTANT_CHANGED].new_value == "2"
        assert by_kind[ChangeKind.CONSTANT_ADDED].new_value == "3"

    def test_a_mixed_group_reports_both_the_change_and_the_residual_removal(
        self,
    ) -> None:
        """The mirror image: `removed={1, 4}`, `added={2}` -- one pairing
        consumes `1`/`2` as a ``CONSTANT_CHANGED``, leaving `4` as an
        independently provable ``CONSTANT_REMOVED``."""
        stable_old = entity_id_for_constant((), "X")
        removed_old = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        stable_new = entity_id_for_constant((), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable_old): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(removed_old): CanonicalEntity(
                        canonical_spelling=Fact.present("4")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable_new): CanonicalEntity(
                        canonical_spelling=Fact.present("2")
                    )
                }
            )
        )
        changes = _run(old_index, new_index)
        by_kind = {c.kind: c for c in changes}
        assert set(by_kind) == {
            ChangeKind.CONSTANT_CHANGED,
            ChangeKind.CONSTANT_REMOVED,
        }
        assert by_kind[ChangeKind.CONSTANT_CHANGED].new_value == "2"
        # Since the eleventh round's occurrence-level (dict-ordered, not
        # `set`-ordered) rewrite, which value pairs into the ``CHANGED``
        # finding is deterministic -- the first-encountered removed value
        # in insertion order ("1", from `stable_old`, listed first) --
        # rather than depending on `PYTHONHASHSEED`. Both values are
        # accounted for regardless.
        assert by_kind[ChangeKind.CONSTANT_CHANGED].old_value == "1"
        assert by_kind[ChangeKind.CONSTANT_REMOVED].old_value == "4"

    def test_three_colliding_occurrences_shrinking_to_one_reports_two_removals(
        self,
    ) -> None:
        """Codex review, PR #1078, eleventh round: three anonymous-scoped
        occurrences all sharing ``X=1`` on the old side, only one on the
        new side -- the loss of *two* occurrences, not one. Converting the
        multiset difference to a ``set`` (an earlier version of this fix)
        collapsed the repeated value to a single entry, silently dropping
        the second removal."""
        first_old = entity_id_for_constant((), "X")
        second_old = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        third_old = entity_id_for_constant((Anonymous("namespace", 1),), "X")
        new_id = entity_id_for_constant((), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first_old): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(second_old): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(third_old): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(new_id): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    )
                }
            )
        )
        changes = _run(old_index, new_index)
        assert len(changes) == 2
        assert all(c.kind is ChangeKind.CONSTANT_REMOVED for c in changes)
        assert all(c.old_value == "1" for c in changes)

    def test_residual_findings_carry_the_contributing_entitys_own_id(self) -> None:
        """Codex review, PR #1078, eleventh round: the residual
        ``CONSTANT_ADDED`` for a newly-added anonymous-scope ``X=2``
        alongside an unchanged, real-backend-identified ``X=1`` must carry
        *that new occurrence's own* entity_id -- not a single id reused
        across every finding for the name, which would misattribute the
        addition to the pre-existing declaration."""
        stable_old = entity_id_for_constant((Namespace("ns"),), "X")
        stable_new = entity_id_for_constant((Namespace("ns"),), "X")
        added_new = entity_id_for_constant((Namespace("ns2"),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable_old): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    )
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(stable_new): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(added_new): CanonicalEntity(
                        canonical_spelling=Fact.present("2")
                    ),
                }
            )
        )
        (change,) = _run(old_index, new_index)
        assert change.kind is ChangeKind.CONSTANT_ADDED
        assert change.new_value == "2"
        assert change.entity_id == added_new
        assert change.entity_id != stable_old
        assert change.entity_id != stable_new


# -- end to end, through the real detector entry point ---------------------


class TestThroughCompare:
    def test_a_dwarf_only_pair_reports_nothing_rather_than_manufacturing_removals(
        self,
    ) -> None:
        """Constants are header-tier only: a DWARF-only pair has no
        confirmed header evidence, so the header-aware gate short-circuits
        before either index is ever built -- unlike typedefs, which have no
        such gate and instead rely on the adapter."""
        from abicheck.diff_symbols import _diff_constants

        old = _snap(constants={"X": "1"}, from_headers=False)
        new = _snap(constants={"X": "2"}, from_headers=False)
        assert _diff_constants(old, new) == []

    def test_the_real_entry_point_uses_the_ir_when_it_is_faithful(self) -> None:
        from abicheck.diff_symbols import _diff_constants

        eid = entity_id_for_constant((Namespace("ns"),), "X")
        old = _snap(
            constants={"ns::X": "1"},
            constant_entity_ids={"ns::X": eid},
            semantic_ir=_ir_backed({"ns::X": "1"}).ir,
        )
        new = _snap(
            constants={"ns::X": "2"},
            constant_entity_ids={"ns::X": eid},
            semantic_ir=_ir_backed({"ns::X": "2"}).ir,
        )
        (change,) = _diff_constants(old, new)
        assert change.kind is ChangeKind.CONSTANT_CHANGED
        # Proof the IR path ran: the identity reached the finding.
        assert change.entity_id == eid

    def test_selector_and_detector_agree_on_a_real_pair(self) -> None:
        eid = entity_id_for_constant((Namespace("ns"),), "X")
        maps = {"ns::X": "1"}
        snap = _snap(
            constants=maps,
            constant_entity_ids={"ns::X": eid},
            semantic_ir=_ir_backed(maps).ir,
        )
        old_index, new_index = constant_index_pair(
            snap, snap, old_constants=maps, new_constants=maps
        )
        assert _run(old_index, new_index) == []


# -- the architecture gate -------------------------------------------------


class TestSemanticIrCutoverGate:
    def test_the_migrated_module_reads_no_legacy_constant_collection(self) -> None:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from findings_report import Findings
        from semantic_ir_cutover import check_semantic_ir_cutover

        findings = Findings()
        check_semantic_ir_cutover(findings)
        assert findings.errors == []

    def test_the_gate_actually_fires_on_every_forbidden_read_shape(self) -> None:
        """Executable, not prose -- see ``test_typedef_cutover.py``'s
        identical test for why."""
        import ast
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from semantic_ir_cutover import legacy_collection_reads

        forbidden = frozenset({"constants", "constant_entity_ids"})
        for source in (
            "x = snap.constants",
            "x = old.constant_entity_ids",
            "x = self.snapshot.constants",
            'x = getattr(snap, "constants")',
            'x = getattr(snap, "constant_entity_ids", {})',
            "from builtins import getattr as g\nx = g(snap, 'constants')",
            "g = getattr\nx = g(snap, 'constant_entity_ids')",
            "import builtins as b\nx = b.getattr(snap, 'constants')",
            "import builtins\nx = builtins.getattr(snap, 'constant_entity_ids')",
            "import builtins as b\ng = b.getattr\nx = g(snap, 'constants')",
        ):
            found = legacy_collection_reads(ast.parse(source), forbidden)
            assert found, f"gate missed: {source!r}"

    def test_the_gate_does_not_fire_on_a_local_or_a_keyword(self) -> None:
        import ast
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from semantic_ir_cutover import legacy_collection_reads

        forbidden = frozenset({"constants", "constant_entity_ids"})
        for source in (
            "constants = {}\nx = constants",
            "build(snapshot, constants=value_map)",
            'x = getattr(snap, "something_else")',
            'x = some_object.getattr(snap, "constants")',
        ):
            assert legacy_collection_reads(ast.parse(source), forbidden) == []
