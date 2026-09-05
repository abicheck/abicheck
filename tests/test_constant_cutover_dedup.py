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

"""The constant detector cohort's ODR-duplicate/occurrence-identity and
post-processing dedup regression coverage (Codex review, PR #1078, fifteenth
through seventeenth rounds), split out of ``test_constant_cutover.py`` once
that file reached the architecture gate's 1200-line test-file cap -- a
mechanical extraction, not a redesign; each class's own docstring still
names the round that introduced it and mirrors
``tests/test_typedef_cutover.py``'s identical sibling class for cohort 1.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.compare.constants import diff_constants
from abicheck.finding_identity import report_finding_id
from abicheck.model.fact import Fact
from abicheck.model.identity import Anonymous, Namespace, entity_id_for_constant
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_index import SemanticIRIndex


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


class TestOdrDuplicateOccurrencesSurviveReduction:
    """Regression coverage for Codex review, PR #1078, fifteenth round --
    mirrors ``tests.test_typedef_cutover.
    TestOdrDuplicateOccurrencesSurviveReduction`` exactly; see that class's
    own docstring for the full account. ``_values``/``_value`` used to read
    through ``SemanticIRIndex``'s reduced, one-entry-per-``EntityId`` view,
    which silently collapsed two genuine occurrences sharing one identity
    onto a single "most facts present" winner -- hiding a real value change
    on whichever occurrence did not win that reduction.
    """

    def _ir_with_two_occurrences(
        self, eid, *, value_a: str, value_b: str
    ) -> SemanticIR:
        return SemanticIR(
            occurrences={
                OccurrenceId(eid, "tu-a"): CanonicalEntity(
                    canonical_spelling=Fact.present(value_a)
                ),
                OccurrenceId(eid, "tu-b"): CanonicalEntity(
                    canonical_spelling=Fact.present(value_b)
                ),
            }
        )

    def test_values_keeps_both_odr_duplicate_occurrences_distinct(self) -> None:
        from abicheck.compare.constants import _values

        eid = entity_id_for_constant((Namespace("ns"),), "X")
        ir = self._ir_with_two_occurrences(eid, value_a="1", value_b="1")
        grouped = _values(SemanticIRIndex(ir))
        assert set(grouped) == {"ns::X"}
        assert set(grouped["ns::X"]) == {
            OccurrenceId(eid, "tu-a"),
            OccurrenceId(eid, "tu-b"),
        }

    def test_a_value_change_on_one_odr_duplicate_occurrence_is_detected(
        self,
    ) -> None:
        """Two occurrences share one ``EntityId`` (an ODR-duplicate pair).
        Only one of them changes value between snapshots -- the reduced-view
        bug would have let the ``canonical_entities()`` "most facts present"
        tie-break pick the same, *unchanged* occurrence as the
        representative on both sides, reporting no change at all despite a
        real value change on the sibling occurrence."""
        eid = entity_id_for_constant((Namespace("ns"),), "X")
        old_index = SemanticIRIndex(
            self._ir_with_two_occurrences(eid, value_a="1", value_b="1")
        )
        new_index = SemanticIRIndex(
            self._ir_with_two_occurrences(eid, value_a="1", value_b="2")
        )
        changes = _run(old_index, new_index)
        assert len(changes) == 1
        change = changes[0]
        assert change.kind is ChangeKind.CONSTANT_CHANGED
        assert change.symbol == "ns::X"
        assert change.old_value == "1"
        assert change.new_value == "2"


class TestWholeGroupRemovalSurvivesPostProcessingDedup:
    """Regression coverage for Codex review, PR #1078, sixteenth round --
    mirrors ``tests.test_typedef_cutover.
    TestWholeGroupRemovalSurvivesPostProcessingDedup`` exactly; see that
    class's own docstring for the full account. ``diff_constants`` emits one
    ``CONSTANT_REMOVED`` per contributing entity when a whole colliding
    group vanishes, but the public pipeline's ``diff_filtering._dedup_exact``
    used to key only on ``(kind, description)`` -- identical for every
    entity in the group -- silently collapsing them back to one.
    """

    def test_two_colliding_removals_both_survive_dedup_exact(self) -> None:
        from abicheck.diff_filtering import _dedup_exact

        first = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        second = entity_id_for_constant((Anonymous("namespace", 1),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(second): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                }
            )
        )
        changes = _run(old_index, SemanticIRIndex(SemanticIR()))
        assert len(changes) == 2
        assert {c.old_value for c in changes} == {"1"}
        deduped = _dedup_exact(changes)
        assert len(deduped) == 2


class TestOdrDuplicateRemovalsSurviveDedupExact:
    """Regression coverage for Codex review, PR #1078, seventeenth round --
    mirrors ``tests.test_typedef_cutover.
    TestOdrDuplicateRemovalsSurviveDedupExact`` exactly; see that class's
    own docstring for the full account.
    """

    def test_two_odr_duplicate_removals_with_the_same_value_both_survive(
        self,
    ) -> None:
        from abicheck.diff_filtering import _dedup_exact

        eid = entity_id_for_constant((Namespace("ns"),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(eid, "tu-a"): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(eid, "tu-b"): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                }
            )
        )
        changes = _run(old_index, SemanticIRIndex(SemanticIR()))
        assert len(changes) == 2
        assert {c.old_value for c in changes} == {"1"}
        assert {c.entity_id for c in changes} == {eid}
        assert {c.disambiguator for c in changes} == {"tu-a", "tu-b"}
        deduped = _dedup_exact(changes)
        assert len(deduped) == 2


class TestCollisionSafeDisambiguatorClosesReportFindingIdGap:
    """Regression coverage for Codex review, PR #1078, twentieth round: two
    entity-distinct occurrences sharing one rendered alias, both with a
    blank source disambiguator -- the common case for two anonymous-scope
    entities -- used to both carry ``Change.disambiguator=None`` and so
    collide on ``finding_identity.report_finding_id`` even though
    ``diff_filtering._dedup_exact`` already told them apart via
    ``entity_id.key``. Fixed at the source: ``compare.constants.
    _collision_safe_disambiguator`` (used everywhere this module emits a
    per-occurrence finding for a colliding group) now falls back to the
    occurrence's own real entity id when the producer supplied no
    disambiguator -- safe to do here, unlike folding ``entity_id`` directly
    into ``report_finding_id``, since ``Change.disambiguator`` is a field
    this PR introduces with nothing pre-existing to rehash.
    """

    def test_two_whole_group_removals_get_distinct_report_finding_ids(
        self,
    ) -> None:
        first = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        second = entity_id_for_constant((Anonymous("namespace", 1),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(first): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(second): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                }
            )
        )
        changes = diff_constants(
            old_index,
            SemanticIRIndex(SemanticIR()),
            is_fingerprint_comparison_unreliable=lambda o, n: False,
            old_constants={},
            new_constants={},
        )
        assert len(changes) == 2
        assert all(c.disambiguator for c in changes)
        assert len({c.disambiguator for c in changes}) == 2
        ids = {report_finding_id(c) for c in changes}
        assert len(ids) == 2


class TestAmbiguousResidualsGetNoAttributedIdentity:
    """Regression coverage for Codex review, PR #1078, twentieth round: a
    partial removal within an equal-valued, entity-unstable colliding group
    used to attribute the residual ``CONSTANT_REMOVED``/``CONSTANT_ADDED``
    finding to an arbitrary list-prefix occurrence's real ``entity_id`` --
    presenting unrecoverable evidence as if it were observed attribution,
    and potentially stamping a still-*present* declaration's identity onto
    a finding claiming it vanished. When only some (not all) of a value
    bucket's occurrences are excess, the residual now carries no
    ``entity_id``/``disambiguator`` at all -- honest about what the
    evidence can and cannot support.
    """

    def test_a_partial_removal_from_an_equal_valued_group_has_no_identity(
        self,
    ) -> None:
        # Old: two anonymous X=1 occurrences (ordinals 0, 1). New: one
        # anonymous X=1 occurrence -- which physical declaration persisted
        # is unrecoverable from a bare value match.
        old_a = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        old_b = entity_id_for_constant((Anonymous("namespace", 1),), "X")
        new_a = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(old_a): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(old_b): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(new_a): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                }
            )
        )
        changes = diff_constants(
            old_index,
            new_index,
            is_fingerprint_comparison_unreliable=lambda o, n: False,
            old_constants={},
            new_constants={},
        )
        assert len(changes) == 1
        change = changes[0]
        assert change.kind is ChangeKind.CONSTANT_REMOVED
        assert change.old_value == "1"
        # No real identity is attributed -- but a synthetic,
        # non-identity-claiming disambiguator is still assigned, so a
        # *second* ambiguous residual in the same value bucket wouldn't
        # silently collapse into this one via `_dedup_exact` (Codex review,
        # PR #1078, twenty-first round; see `TestPartialRemovalsPreserve
        # MultiplicityAcrossDedup` below).
        assert change.entity_id is None
        assert change.disambiguator == "ambiguous:0"

    def test_a_whole_bucket_removal_keeps_its_real_identity(self) -> None:
        # Old: two anonymous X=1 occurrences. New: none of value 1 at all --
        # every occurrence in the bucket unambiguously vanished, so each
        # keeps its own real identity (contrast with the partial case
        # above).
        old_a = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        old_b = entity_id_for_constant((Anonymous("namespace", 1),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(old_a): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                    OccurrenceId(old_b): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    ),
                }
            )
        )
        new_index = SemanticIRIndex(SemanticIR())
        changes = diff_constants(
            old_index,
            new_index,
            is_fingerprint_comparison_unreliable=lambda o, n: False,
            old_constants={},
            new_constants={},
        )
        assert len(changes) == 2
        assert all(c.kind is ChangeKind.CONSTANT_REMOVED for c in changes)
        assert all(c.entity_id is not None for c in changes)
        assert {c.entity_id for c in changes} == {old_a, old_b}


class TestPartialRemovalsPreserveMultiplicityAcrossDedup:
    """Regression coverage for Codex review, PR #1078, twenty-first round:
    when more than one occurrence of a value bucket is ambiguously
    residual (e.g. four equal-valued, entity-unstable occurrences
    shrinking to one -- three independent removals, not one repeated
    three times), each used to get `entity_id=None`/`disambiguator=None`
    alike, making them byte-identical and collapsing to one via
    `diff_filtering._dedup_exact` -- silently losing two of the three real
    removals. Each ambiguous residual now gets its own synthetic,
    non-identity-claiming disambiguator so multiplicity survives dedup.
    """

    def test_three_ambiguous_removals_all_survive_dedup_exact(self) -> None:
        from abicheck.diff_filtering import _dedup_exact

        old_ids = [
            entity_id_for_constant((Anonymous("namespace", i),), "X") for i in range(4)
        ]
        new_id = entity_id_for_constant((Anonymous("namespace", 0),), "X")
        old_index = SemanticIRIndex(
            SemanticIR(
                occurrences={
                    OccurrenceId(eid): CanonicalEntity(
                        canonical_spelling=Fact.present("1")
                    )
                    for eid in old_ids
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
        changes = diff_constants(
            old_index,
            new_index,
            is_fingerprint_comparison_unreliable=lambda o, n: False,
            old_constants={},
            new_constants={},
        )
        assert len(changes) == 3
        assert all(c.kind is ChangeKind.CONSTANT_REMOVED for c in changes)
        assert all(c.entity_id is None for c in changes)
        assert len({c.disambiguator for c in changes}) == 3
        deduped = _dedup_exact(changes)
        assert len(deduped) == 3
        assert len({report_finding_id(c) for c in changes}) == 3
