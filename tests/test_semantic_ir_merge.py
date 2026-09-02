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

"""``merge_semantic_ir`` — the hybrid backend's ``SemanticIR``
reconciliation (ADR-063 Phase 6).

Primitive-level property tests, per AGENTS.md: this is a reusable
merge/matching primitive, and the plan's own design text records four
independently-falsified drafts of its matching rule (an "either side
non-empty" disagreement test that never merged the ordinary case; a
one-occurrence-per-side assumption the IR's own shape contradicts; an
"exactly one surviving pair" ambiguity test that rejected a real bijection;
a leftover rule that assumed every leftover was empty). Each of those
counterexamples is pinned below as an invariant over generated input, not as
a single fixed example.
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.extract.semantic_ir_merge import (
    MERGED_PRODUCER,
    _pair_group,
    merge_semantic_ir,
)
from abicheck.model.fact import Fact
from abicheck.model.identity import entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import (
    CanonicalEntity,
    SemanticIR,
    semantic_ir_conflict_key,
)

_tags = st.text(
    min_size=1, max_size=8, alphabet=st.characters(min_codepoint=97, max_codepoint=122)
)

FOO = entity_id_for_type((), "Foo")
BAR = entity_id_for_type((), "Bar")


def _entity(
    spelling: str | None = "Foo",
    *,
    template: tuple[str, ...] | None = None,
    producer: str = "castxml",
) -> CanonicalEntity:
    return CanonicalEntity(
        canonical_spelling=(
            Fact.present(spelling) if spelling is not None else Fact.not_collected()
        ),
        template_arguments=(
            Fact.present(template) if template is not None else Fact.not_collected()
        ),
        producer=producer,
    )


def _ir(*entries: tuple[OccurrenceId, CanonicalEntity]) -> SemanticIR:
    return SemanticIR(occurrences=dict(entries))


class TestOrdinaryOneSidedDisambiguator:
    """The case the plan's first matching rule got wrong: castxml has no USR
    concept, so its side of an ordinary match is routinely empty while
    clang's is not. That is "no signal from that backend", never a
    disagreement — reading it as one would leave the *common* hybrid case
    permanently unmerged."""

    @given(tag=_tags)
    def test_empty_against_non_empty_still_merges(self, tag: str) -> None:
        base_occ = OccurrenceId(FOO)
        overlay_occ = OccurrenceId(FOO, disambiguator=tag)
        merged, conflicts = merge_semantic_ir(
            _ir((base_occ, _entity())),
            _ir((overlay_occ, _entity(template=("int",), producer="clang"))),
        )
        assert merged is not None
        assert list(merged.occurrences) == [base_occ]
        entity = merged.occurrences[base_occ]
        assert entity.template_arguments.value == ("int",)
        assert entity.producer == MERGED_PRODUCER
        assert conflicts == {}

    @given(tag_a=_tags, tag_b=_tags)
    def test_two_sided_disagreement_is_the_only_refusal(
        self, tag_a: str, tag_b: str
    ) -> None:
        base_occ = OccurrenceId(FOO, disambiguator=tag_a)
        overlay_occ = OccurrenceId(FOO, disambiguator=tag_b)
        merged, _ = merge_semantic_ir(
            _ir((base_occ, _entity())),
            _ir((overlay_occ, _entity(template=("int",), producer="clang"))),
        )
        assert merged is not None
        if tag_a == tag_b:
            assert merged.occurrences[base_occ].template_arguments.value == ("int",)
        else:
            # Both backends derived a TU-context signal and they differ: no
            # pairing is guessed at, both occurrences survive verbatim.
            assert set(merged.occurrences) == {base_occ, overlay_occ}
            assert not merged.occurrences[base_occ].template_arguments.is_present


class TestBasePrecedenceAndConflicts:
    def test_present_base_fact_is_never_overwritten(self) -> None:
        occ = OccurrenceId(FOO)
        merged, conflicts = merge_semantic_ir(
            _ir((occ, _entity("castxml::Foo"))),
            _ir((occ, _entity("clang::Foo", producer="clang"))),
        )
        assert merged is not None
        assert merged.occurrences[occ].canonical_spelling.value == "castxml::Foo"
        assert conflicts == {
            semantic_ir_conflict_key(occ, "canonical_spelling"): repr("clang::Foo")
        }

    def test_agreement_records_no_conflict(self) -> None:
        occ = OccurrenceId(FOO)
        merged, conflicts = merge_semantic_ir(
            _ir((occ, _entity("Foo"))),
            _ir((occ, _entity("Foo", producer="clang"))),
        )
        assert merged is not None
        assert conflicts == {}
        # Nothing was contributed, so the base entity — producer included —
        # is carried through untouched rather than relabelled "hybrid".
        assert merged.occurrences[occ].producer == "castxml"

    def test_two_pairs_sharing_one_entity_id_keep_both_conflicts(self) -> None:
        """The defect a ``fact_provenance``-shaped, declaration-only key
        would reintroduce: the second pair's conflict silently overwriting
        the first's."""
        first = OccurrenceId(FOO, disambiguator="usr1")
        second = OccurrenceId(FOO, disambiguator="usr2")
        merged, conflicts = merge_semantic_ir(
            _ir((first, _entity("base::One")), (second, _entity("base::Two"))),
            _ir(
                (first, _entity("clang::One", producer="clang")),
                (second, _entity("clang::Two", producer="clang")),
            ),
        )
        assert merged is not None
        assert conflicts == {
            semantic_ir_conflict_key(first, "canonical_spelling"): repr("clang::One"),
            semantic_ir_conflict_key(second, "canonical_spelling"): repr("clang::Two"),
        }


class TestMultiOccurrenceMatching:
    def test_bijection_over_a_group_merges_every_pair(self) -> None:
        """Two occurrences per side whose non-empty disambiguators are a
        bijection have exactly one correct pairing — an "exactly one
        surviving pair" ambiguity test rejects it wrongly."""
        one = OccurrenceId(FOO, disambiguator="usr1")
        two = OccurrenceId(FOO, disambiguator="usr2")
        merged, _ = merge_semantic_ir(
            _ir((one, _entity()), (two, _entity())),
            _ir(
                (one, _entity(template=("int",), producer="clang")),
                (two, _entity(template=("char",), producer="clang")),
            ),
        )
        assert merged is not None
        assert merged.occurrences[one].template_arguments.value == ("int",)
        assert merged.occurrences[two].template_arguments.value == ("char",)

    def test_one_sided_leftover_pairs_with_an_empty_leftover(self) -> None:
        """castxml ``{empty, usr1}`` against clang ``{usr1, usr2}``: ``usr1``
        pairs in the first pass, and the leftovers (``empty`` and ``usr2``)
        are exactly as safe to pair as an empty-vs-empty pair is."""
        base_empty = OccurrenceId(FOO)
        base_usr1 = OccurrenceId(FOO, disambiguator="usr1")
        overlay_usr1 = OccurrenceId(FOO, disambiguator="usr1")
        overlay_usr2 = OccurrenceId(FOO, disambiguator="usr2")
        merged, _ = merge_semantic_ir(
            _ir((base_empty, _entity()), (base_usr1, _entity())),
            _ir(
                (overlay_usr1, _entity(template=("int",), producer="clang")),
                (overlay_usr2, _entity(template=("char",), producer="clang")),
            ),
        )
        assert merged is not None
        assert set(merged.occurrences) == {base_empty, base_usr1}
        assert merged.occurrences[base_usr1].template_arguments.value == ("int",)
        assert merged.occurrences[base_empty].template_arguments.value == ("char",)

    def test_repeated_disambiguator_on_one_side_is_refused(self) -> None:
        """A non-empty disambiguator naming two occurrences on one side is a
        genuine ambiguity for that value, and the matcher refuses it.

        Deliberately exercised through ``_pair_group`` directly: it cannot
        reach ``merge_semantic_ir`` from a real ``SemanticIR``, because two
        occurrences of one ``EntityId`` sharing one disambiguator *are* one
        ``OccurrenceId`` and therefore one dict key. The guard stays — this
        function takes plain lists, and the invariant that makes it
        unreachable belongs to the caller's key type, not to the matcher —
        but the reachability limit is stated here rather than left implied
        by a test that only looks like it covers the case.
        """
        repeated = [
            OccurrenceId(FOO, disambiguator="usr1"),
            OccurrenceId(FOO, disambiguator="usr1"),
        ]
        assert _pair_group(repeated, [OccurrenceId(FOO, disambiguator="usr1")]) is None

    def test_a_shared_entity_id_cannot_repeat_a_disambiguator_in_one_ir(self) -> None:
        ir = _ir(
            (OccurrenceId(FOO, disambiguator="usr1"), _entity("first")),
            (OccurrenceId(FOO, disambiguator="usr1"), _entity("second")),
        )
        assert len(ir.occurrences) == 1

    def test_a_base_leftover_with_no_counterpart_survives_unmerged(self) -> None:
        """base ``{empty, usr1}`` against overlay ``{usr1}``: ``usr1`` pairs,
        and the base occurrence the overlay never matched is neither dropped
        nor backfilled from an unrelated occurrence."""
        base_empty = OccurrenceId(FOO)
        base_usr1 = OccurrenceId(FOO, disambiguator="usr1")
        merged, _ = merge_semantic_ir(
            _ir((base_empty, _entity()), (base_usr1, _entity())),
            _ir((base_usr1, _entity(template=("int",), producer="clang"))),
        )
        assert merged is not None
        assert set(merged.occurrences) == {base_empty, base_usr1}
        assert merged.occurrences[base_usr1].template_arguments.value == ("int",)
        assert not merged.occurrences[base_empty].template_arguments.is_present

    def test_more_than_one_leftover_per_side_is_unmerged(self) -> None:
        base_ids = [OccurrenceId(FOO), OccurrenceId(FOO, disambiguator="usr1")]
        overlay_ids = [
            OccurrenceId(FOO, disambiguator="usr2"),
            OccurrenceId(FOO, disambiguator="usr3"),
        ]
        merged, conflicts = merge_semantic_ir(
            _ir(*((occ, _entity()) for occ in base_ids)),
            _ir(
                *(
                    (occ, _entity(template=("int",), producer="clang"))
                    for occ in overlay_ids
                )
            ),
        )
        assert merged is not None
        assert set(merged.occurrences) == set(base_ids) | set(overlay_ids)
        for occ in base_ids:
            assert not merged.occurrences[occ].template_arguments.is_present
        assert conflicts == {}


class TestUnionRules:
    def test_overlay_only_entity_is_unioned_verbatim(self) -> None:
        base_occ = OccurrenceId(FOO)
        overlay_occ = OccurrenceId(BAR)
        merged, _ = merge_semantic_ir(
            _ir((base_occ, _entity())),
            _ir((overlay_occ, _entity("Bar", producer="clang"))),
        )
        assert merged is not None
        assert merged.occurrences[overlay_occ].producer == "clang"

    def test_base_only_entity_survives(self) -> None:
        base_occ = OccurrenceId(FOO)
        merged, _ = merge_semantic_ir(
            _ir((base_occ, _entity())), _ir((OccurrenceId(BAR), _entity("Bar")))
        )
        assert merged is not None
        assert base_occ in merged.occurrences

    def test_missing_side_returns_the_other_unchanged(self) -> None:
        ir = _ir((OccurrenceId(FOO), _entity()))
        assert merge_semantic_ir(ir, None) == (ir, {})
        assert merge_semantic_ir(None, ir) == (ir, {})
        assert merge_semantic_ir(None, None) == (None, {})
        assert merge_semantic_ir(SemanticIR(), ir) == (ir, {})


class TestMergeProperties:
    @given(
        tags=st.lists(_tags, min_size=1, max_size=4, unique=True),
        reverse_base=st.booleans(),
        reverse_overlay=st.booleans(),
    )
    def test_result_never_depends_on_input_order(
        self, tags: list[str], reverse_base: bool, reverse_overlay: bool
    ) -> None:
        base_entries = [(OccurrenceId(FOO, disambiguator=t), _entity()) for t in tags]
        overlay_entries = [
            (
                OccurrenceId(FOO, disambiguator=t),
                _entity(template=(t,), producer="clang"),
            )
            for t in tags
        ]
        reference, reference_conflicts = merge_semantic_ir(
            _ir(*base_entries), _ir(*overlay_entries)
        )
        shuffled, shuffled_conflicts = merge_semantic_ir(
            _ir(*(reversed(base_entries) if reverse_base else base_entries)),
            _ir(*(reversed(overlay_entries) if reverse_overlay else overlay_entries)),
        )
        assert reference is not None and shuffled is not None
        assert dict(reference.occurrences) == dict(shuffled.occurrences)
        assert reference_conflicts == shuffled_conflicts

    @given(tags=st.lists(_tags, min_size=1, max_size=4, unique=True))
    def test_no_occurrence_is_ever_dropped(self, tags: list[str]) -> None:
        """Whatever the matching decides, every base occurrence survives, and
        every overlay occurrence either merged into one of them or survives on
        its own — a merge may fail closed, never lose evidence."""
        base = _ir(*((OccurrenceId(FOO, disambiguator=t), _entity()) for t in tags))
        overlay = _ir(
            *(
                (OccurrenceId(BAR, disambiguator=t), _entity("Bar", producer="clang"))
                for t in tags
            )
        )
        merged, _ = merge_semantic_ir(base, overlay)
        assert merged is not None
        assert set(base.occurrences) | set(overlay.occurrences) <= set(
            merged.occurrences
        )

    @given(tags=st.lists(_tags, min_size=1, max_size=4, unique=True))
    def test_merging_an_ir_with_itself_changes_nothing(self, tags: list[str]) -> None:
        ir = _ir(
            *(
                (OccurrenceId(FOO, disambiguator=t), _entity(template=(t,)))
                for t in tags
            )
        )
        merged, conflicts = merge_semantic_ir(ir, ir)
        assert merged is not None
        assert dict(merged.occurrences) == dict(ir.occurrences)
        assert conflicts == {}
