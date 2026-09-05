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
