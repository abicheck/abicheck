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

"""``SemanticIR``/``CanonicalEntity`` primitives (ADR-063 Phase 6).

Primitive-level, per AGENTS.md's "Primitive-level property tests" rule: the
IR is a new, reusable shape several backends and one merge algorithm will
key on, so its contract is stated here as invariants over generated input,
independent of any one backend's domain logic — not only through whichever
caller happens to exist first.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given, strategies as st

from abicheck.model.fact import Fact
from abicheck.model.identity import Namespace, entity_id_for_type
from abicheck.model.occurrence import OccurrenceId, canonical_key
from abicheck.model.semantic_ir import (
    CV_QUALIFIER_ORDER,
    CanonicalEntity,
    SemanticIR,
    canonical_cv_qualification,
    renumber_conflict_keys,
    semantic_ir_conflict_key,
)

_names = st.text(
    min_size=1, max_size=10, alphabet=st.characters(min_codepoint=97, max_codepoint=122)
)
_tags = st.text(
    min_size=1, max_size=8, alphabet=st.characters(min_codepoint=97, max_codepoint=122)
)


def _entity(spelling: str = "ns::Foo", **kwargs: object) -> CanonicalEntity:
    return CanonicalEntity(canonical_spelling=Fact.present(spelling), **kwargs)  # type: ignore[arg-type]


class TestIdentityLivesOnlyInTheKey:
    """The Governing-Invariant rule this type was redesigned around: a
    ``CanonicalEntity`` carries no second, independently-settable copy of the
    identity its mapping key already states, so no mapping can exist whose
    key names one scope and whose value reports another."""

    def test_no_identity_field_on_the_value(self) -> None:
        field_names = {f.name for f in dataclasses.fields(CanonicalEntity)}
        assert not field_names & {
            "scope",
            "scope_path",
            "entity_id",
            "occurrence_id",
            "qualified_name",
            "leaf_name",
        }

    def test_scope_is_read_off_the_key(self) -> None:
        eid = entity_id_for_type((Namespace("ns"),), "Foo")
        ir = SemanticIR(occurrences={OccurrenceId(eid): _entity()})
        (occ_id,) = ir.occurrences
        assert occ_id.entity_id.scope == (Namespace("ns"),)


class TestOccurrencesAreNotCollapsed:
    """One ``EntityId`` may legitimately name several occurrences — an
    ODR-duplicate pair, or an incomplete declaration beside its complete
    definition — carrying different availability. The mapping keeps both."""

    def test_two_occurrences_one_entity_id_keep_their_own_facts(self) -> None:
        eid = entity_id_for_type((), "Foo")
        complete = OccurrenceId(eid, disambiguator="tu-a")
        incomplete = OccurrenceId(eid, disambiguator="tu-b")
        ir = SemanticIR(
            occurrences={
                complete: _entity(template_arguments=Fact.present(("int",))),
                incomplete: _entity(template_arguments=Fact.not_collected()),
            }
        )
        assert len(ir.occurrences) == 2
        assert set(ir.occurrences_for(eid)) == {complete, incomplete}
        assert ir.occurrences[complete].template_arguments.is_present
        assert not ir.occurrences[incomplete].template_arguments.is_present

    @given(tag_a=_tags, tag_b=_tags)
    def test_distinct_disambiguators_never_share_a_key(
        self, tag_a: str, tag_b: str
    ) -> None:
        eid = entity_id_for_type((), "Foo")
        occurrences = {
            OccurrenceId(eid, disambiguator=tag_a): _entity(),
            OccurrenceId(eid, disambiguator=tag_b): _entity("other"),
        }
        assert len(occurrences) == (1 if tag_a == tag_b else 2)


class TestCanonicalEntitiesReduction:
    """``canonical_entities()`` is the *explicit* reduction — one entity per
    identity, deterministic, never the shape the legacy snapshot fields are
    projected from."""

    def test_most_resolved_occurrence_wins(self) -> None:
        eid = entity_id_for_type((), "Foo")
        sparse = OccurrenceId(eid, disambiguator="a")
        rich = OccurrenceId(eid, disambiguator="b")
        ir = SemanticIR(
            occurrences={
                sparse: _entity(),
                rich: _entity(
                    template_arguments=Fact.present(("int",)),
                    cv_qualification=Fact.present(("const",)),
                ),
            }
        )
        reduced = ir.canonical_entities()
        assert set(reduced) == {eid}
        assert reduced[eid] is ir.occurrences[rich]

    @given(tags=st.lists(_tags, min_size=2, max_size=5, unique=True))
    def test_reduction_does_not_depend_on_insertion_order(
        self, tags: list[str]
    ) -> None:
        eid = entity_id_for_type((), "Foo")
        # Every occurrence carries the same number of resolved facts, so the
        # tie-break — and only the tie-break — decides: an order-dependent
        # implementation would answer differently for a reversed dict.
        entries = [
            (OccurrenceId(eid, disambiguator=tag), _entity(f"spelling::{tag}"))
            for tag in tags
        ]
        forward = SemanticIR(occurrences=dict(entries)).canonical_entities()
        backward = SemanticIR(occurrences=dict(reversed(entries))).canonical_entities()
        assert forward == backward

    @given(tags=st.lists(_tags, min_size=1, max_size=5, unique=True))
    def test_tie_break_is_canonical_key_order(self, tags: list[str]) -> None:
        eid = entity_id_for_type((), "Foo")
        entries = {
            OccurrenceId(eid, disambiguator=tag): _entity(f"spelling::{tag}")
            for tag in tags
        }
        expected_key = min(canonical_key(occ) for occ in entries)
        winner = next(
            entity
            for occ, entity in entries.items()
            if canonical_key(occ) == expected_key
        )
        assert SemanticIR(occurrences=entries).canonical_entities()[eid] == winner


class TestCanonicalCvQualification:
    @given(
        qualifiers=st.lists(st.sampled_from(CV_QUALIFIER_ORDER), max_size=6),
    )
    def test_order_and_duplicate_independent(self, qualifiers: list[str]) -> None:
        canonical = canonical_cv_qualification(qualifiers)
        assert canonical == canonical_cv_qualification(reversed(qualifiers))
        assert canonical == canonical_cv_qualification(qualifiers + qualifiers)
        assert canonical == canonical_cv_qualification(canonical)  # idempotent
        assert list(canonical) == [q for q in CV_QUALIFIER_ORDER if q in qualifiers]

    def test_blank_entries_are_ignored(self) -> None:
        # A backend that emits "" / " const " for an unqualified or padded
        # spelling must not produce a different canonical value than one that
        # emits nothing at all.
        assert canonical_cv_qualification(["", "  ", " const "]) == ("const",)
        assert canonical_cv_qualification([""]) == ()

    def test_unknown_qualifier_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown CV-qualifier"):
            canonical_cv_qualification(["constexpr"])

    def test_entity_refuses_a_non_canonical_value(self) -> None:
        # Two backends spelling one qualification in two orders must not be
        # able to construct two different entities for the same fact.
        with pytest.raises(ValueError, match="not canonical"):
            CanonicalEntity(
                canonical_spelling=Fact.present("Foo"),
                cv_qualification=Fact.present(("volatile", "const")),
            )

    @pytest.mark.parametrize("constructor", [Fact.present, Fact.partial])
    def test_every_usable_status_is_canonicalized(self, constructor: object) -> None:
        """`PARTIAL` is usable evidence everywhere else in this IR, so a
        status-specific check would accept a non-canonical spelling from one
        constructor and reject the identical value from the other — two
        spellings of one qualification, which is what canonicalization exists
        to prevent."""
        with pytest.raises(ValueError, match="not canonical"):
            CanonicalEntity(
                canonical_spelling=Fact.present("Foo"),
                cv_qualification=constructor(("volatile", "const")),  # type: ignore[operator]
            )
        # ...and the canonical spelling is accepted from both, so the check
        # is not simply rejecting the status.
        entity = CanonicalEntity(
            canonical_spelling=Fact.present("Foo"),
            cv_qualification=constructor(("const", "volatile")),  # type: ignore[operator]
        )
        assert entity.cv_qualification.is_present

    @pytest.mark.parametrize(
        "fact", [Fact.not_collected(), Fact.unsupported(), Fact.failed("no")]
    )
    def test_a_fact_with_no_usable_value_is_not_checked(self, fact: object) -> None:
        entity = CanonicalEntity(
            canonical_spelling=Fact.present("Foo"),
            cv_qualification=fact,  # type: ignore[arg-type]
        )
        assert entity.cv_qualification == fact

    @pytest.mark.parametrize("name", ["canonical_spelling", "cv_qualification"])
    @pytest.mark.parametrize("constructor", [Fact.present, Fact.partial])
    def test_a_usable_fact_must_carry_its_value(
        self, name: str, constructor: object
    ) -> None:
        """`Fact.present(None)` is legitimate in the general `Fact`
        vocabulary, for a field whose `T` includes `None` — but these fields
        are `Fact[str]`/`Fact[tuple[str, ...]]`, and their own docstrings name
        the confirmed-absence spelling as this field's *empty* value. Admitting
        `None` would let `resolved_fact_count` (and through it the reduction
        and the hybrid backfill) treat a value the entity does not carry as
        usable evidence."""
        kwargs = {
            "canonical_spelling": Fact.present("Foo"),
            name: constructor(None),  # type: ignore[operator]
        }
        with pytest.raises(ValueError, match="carries no value"):
            CanonicalEntity(**kwargs)  # type: ignore[arg-type]

    def test_the_declared_empty_value_is_how_absence_is_spelled(self) -> None:
        entity = CanonicalEntity(
            canonical_spelling=Fact.present(""),
            template_arguments=Fact.present(()),
            cv_qualification=Fact.present(()),
        )
        assert entity.resolved_fact_count() == 3

    def test_confirmed_absence_is_present_and_empty(self) -> None:
        entity = CanonicalEntity(
            canonical_spelling=Fact.present("Foo"),
            cv_qualification=Fact.present(()),
        )
        assert entity.cv_qualification.is_present
        assert entity.cv_qualification.value == ()


class TestFactItems:
    def test_covers_every_fact_typed_field(self) -> None:
        entity = _entity()
        names = [name for name, _ in entity.fact_items()]
        assert names == [
            f.name
            for f in dataclasses.fields(CanonicalEntity)
            if isinstance(getattr(entity, f.name), Fact)
        ]
        assert "producer" not in names

    @given(resolved=st.integers(min_value=0, max_value=3))
    def test_resolved_fact_count_matches_present_facts(self, resolved: int) -> None:
        facts = [Fact.present("x"), Fact.present(("a",)), Fact.present(("const",))]
        blanks = [Fact.not_collected(), Fact.unsupported(), Fact.failed("no")]
        chosen = facts[:resolved] + blanks[resolved:]
        entity = CanonicalEntity(
            canonical_spelling=chosen[0],
            template_arguments=chosen[1],
            cv_qualification=chosen[2],
        )
        assert entity.resolved_fact_count() == resolved


class TestConflictKey:
    """Occurrence-keyed, not declaration-keyed: two matched pairs sharing one
    ``EntityId`` must not collide on one key (the defect a ``fact_provenance``
    -shaped key would reintroduce)."""

    @given(tag_a=_tags, tag_b=_tags, fact=_names)
    def test_distinct_occurrences_get_distinct_keys(
        self, tag_a: str, tag_b: str, fact: str
    ) -> None:
        eid = entity_id_for_type((), "Foo")
        key_a = semantic_ir_conflict_key(OccurrenceId(eid, tag_a), fact)
        key_b = semantic_ir_conflict_key(OccurrenceId(eid, tag_b), fact)
        assert (key_a == key_b) == (tag_a == tag_b)

    @given(fact_a=_names, fact_b=_names)
    def test_distinct_facts_get_distinct_keys(self, fact_a: str, fact_b: str) -> None:
        occ = OccurrenceId(entity_id_for_type((), "Foo"), "tu-a")
        keys_equal = semantic_ir_conflict_key(occ, fact_a) == semantic_ir_conflict_key(
            occ, fact_b
        )
        assert keys_equal == (fact_a == fact_b)


class TestRenumberConflictKeys:
    """``renumber_conflict_keys`` (ADR-063 Phase 6 second slice, Codex
    review, PR #1001): re-keying ``semantic_ir_conflicts`` after its
    matching occurrence identity changed, without the length-prefix
    corruption a naive in-place text rewrite of the packed key would cause.
    """

    def test_stale_key_replaced_with_a_freshly_computed_one(self) -> None:
        old_eid = entity_id_for_type((Namespace("ns"),), "OldName")
        new_eid = entity_id_for_type((Namespace("ns"),), "NewName")
        old_occ, new_occ = OccurrenceId(old_eid), OccurrenceId(new_eid)
        old_key = semantic_ir_conflict_key(old_occ, "canonical_spelling")
        conflicts = {old_key: "discarded"}
        new_ir = SemanticIR(
            occurrences={
                new_occ: CanonicalEntity(canonical_spelling=Fact.present("ns::NewName"))
            }
        )

        renumber_conflict_keys(conflicts, [old_occ], new_ir)

        fresh_key = semantic_ir_conflict_key(new_occ, "canonical_spelling")
        assert conflicts == {fresh_key: "discarded"}

    def test_rewrite_value_is_applied_to_the_moved_conflict_value(self) -> None:
        """The discarded backend's own spelling (Codex review, PR #1001,
        third round) can embed the identical closure/anonymous marker the
        retained spelling does -- unlike the key, a value is plain text
        with no packed-length encoding to corrupt, so it's rewritten the
        ordinary way rather than left stale."""
        old_eid = entity_id_for_type((), "Old")
        new_eid = entity_id_for_type((), "New")
        old_occ, new_occ = OccurrenceId(old_eid), OccurrenceId(new_eid)
        old_key = semantic_ir_conflict_key(old_occ, "canonical_spelling")
        conflicts = {old_key: "'(lambda:x.h:20:4)'"}
        new_ir = SemanticIR(
            occurrences={
                new_occ: CanonicalEntity(canonical_spelling=Fact.present("New"))
            }
        )

        renumber_conflict_keys(
            conflicts,
            [old_occ],
            new_ir,
            rewrite_value=lambda v: v.replace(":20:4)'", "#1)'"),
        )

        fresh_key = semantic_ir_conflict_key(new_occ, "canonical_spelling")
        assert conflicts == {fresh_key: "'(lambda:x.h#1)'"}

    def test_default_rewrite_value_is_a_no_op(self) -> None:
        old_eid = entity_id_for_type((), "Old")
        new_eid = entity_id_for_type((), "New")
        old_occ, new_occ = OccurrenceId(old_eid), OccurrenceId(new_eid)
        old_key = semantic_ir_conflict_key(old_occ, "canonical_spelling")
        conflicts = {old_key: "unchanged value"}
        new_ir = SemanticIR(
            occurrences={
                new_occ: CanonicalEntity(canonical_spelling=Fact.present("New"))
            }
        )

        renumber_conflict_keys(conflicts, [old_occ], new_ir)

        fresh_key = semantic_ir_conflict_key(new_occ, "canonical_spelling")
        assert conflicts == {fresh_key: "unchanged value"}

    def test_unchanged_occurrence_is_left_untouched(self) -> None:
        eid = entity_id_for_type((), "Same")
        occ = OccurrenceId(eid)
        key = semantic_ir_conflict_key(occ, "canonical_spelling")
        conflicts = {key: "value"}
        new_ir = SemanticIR(
            occurrences={occ: CanonicalEntity(canonical_spelling=Fact.present("Same"))}
        )

        renumber_conflict_keys(conflicts, [occ], new_ir)

        assert conflicts == {key: "value"}

    def test_mismatched_id_count_is_a_no_op(self) -> None:
        """A rare post-renumber key collision (two old ids -> one new id):
        bail rather than guess a wrong correspondence."""
        eid_a = entity_id_for_type((), "A")
        eid_b = entity_id_for_type((), "B")
        occ_a, occ_b = OccurrenceId(eid_a), OccurrenceId(eid_b)
        key_a = semantic_ir_conflict_key(occ_a, "canonical_spelling")
        key_b = semantic_ir_conflict_key(occ_b, "canonical_spelling")
        conflicts = {key_a: "a", key_b: "b"}
        # Collapsed onto ONE occurrence -- length mismatch against the two
        # old ids passed in.
        new_ir = SemanticIR(
            occurrences={occ_a: CanonicalEntity(canonical_spelling=Fact.present("A"))}
        )

        renumber_conflict_keys(conflicts, [occ_a, occ_b], new_ir)

        assert conflicts == {key_a: "a", key_b: "b"}

    def test_multiple_fact_names_on_one_occurrence_all_move(self) -> None:
        old_eid = entity_id_for_type((), "Old")
        new_eid = entity_id_for_type((), "New")
        old_occ, new_occ = OccurrenceId(old_eid), OccurrenceId(new_eid)
        conflicts = {
            semantic_ir_conflict_key(old_occ, "canonical_spelling"): "spelling",
            semantic_ir_conflict_key(old_occ, "cv_qualification"): "cv",
        }
        new_ir = SemanticIR(
            occurrences={
                new_occ: CanonicalEntity(
                    canonical_spelling=Fact.present("New"),
                    cv_qualification=Fact.present(("const",)),
                )
            }
        )

        renumber_conflict_keys(conflicts, [old_occ], new_ir)

        assert conflicts == {
            semantic_ir_conflict_key(new_occ, "canonical_spelling"): "spelling",
            semantic_ir_conflict_key(new_occ, "cv_qualification"): "cv",
        }
