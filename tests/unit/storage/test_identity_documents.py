# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""What an identity document — or a direct constructor call — may contain.

Split out of ``test_identity.py`` when that file crossed this repo's
1200-line test cap, by subject rather than size. Everything here is about the
*boundary*: which malformed inputs are refused rather than coerced, whether a
record survives a round trip intact, and whether a conflict has anything to
disagree about. The sibling file keeps the in-memory contracts — retention,
keys, ordering, and who judges a contradiction.

Two of these classes exist because a rule was written for ``from_dict`` and
not for the constructor, so the two doors are tested side by side.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.identity import (
    EntityId,
    EntityKind,
    IdentityConflict,
    ObservationKind,
    OccurrenceId,
    OccurrenceSet,
    elf_symbol_occurrence,
)


def _occurrence(
    name: str,
    observation: ObservationKind = ObservationKind.AST,
    container: str = "",
    **attributes: str,
) -> OccurrenceId:
    return OccurrenceId(
        entity=EntityId(EntityKind.FUNCTION, name),
        observation=observation,
        container=container,
        attributes=tuple(attributes.items()),
    )


def _any_attribute_disagrees(left: OccurrenceId, right: OccurrenceId) -> bool:
    """A stand-in domain predicate for `conflicts`.

    Roughly what `conflicts()` used to decide by itself, supplied explicitly
    now that the semantic judgement belongs to the caller. Real callers will
    know which attributes may legitimately differ (a forward declaration and
    its definition disagree on `is_definition`, and that is ordinary); this
    one treats every difference as a contradiction, which is what the tests
    below that assert conflict *detection* need.
    """
    return left.attributes != right.attributes


# --------------------------------------------------------------------------
# The central invariant: nothing is ever dropped.
# --------------------------------------------------------------------------


class TestConflictRoundTrip:
    def test_conflict_survives_serialization(self) -> None:
        conflict = IdentityConflict(
            reason="two irreconcilable ast observations",
            occurrences=(
                _occurrence("f", container="a.cpp", ret="int"),
                _occurrence("f", container="a.cpp", ret="long"),
            ),
        )

        assert IdentityConflict.from_dict(conflict.to_dict()) == conflict

    @given(st.permutations(["int", "long", "short"]))
    def test_conflict_occurrence_order_is_normalized(self, returns: list[str]) -> None:
        conflict = IdentityConflict(
            reason="r",
            occurrences=tuple(
                _occurrence("f", container="a.cpp", ret=r) for r in returns
            ),
        )
        reference = IdentityConflict(
            reason="r",
            occurrences=tuple(
                _occurrence("f", container="a.cpp", ret=r)
                for r in ("int", "long", "short")
            ),
        )

        assert conflict == reference


# --------------------------------------------------------------------------
# The concrete case the format loses today: versioned ELF symbols.
# --------------------------------------------------------------------------


class TestRoundTrip:
    @given(
        st.lists(
            st.tuples(
                st.sampled_from(["f", "ns::g"]),
                st.sampled_from(list(ObservationKind)),
                st.sampled_from(["", "a.cpp"]),
            ),
            max_size=10,
        )
    )
    def test_occurrence_set_round_trips(
        self, raw: list[tuple[str, ObservationKind, str]]
    ) -> None:
        original = OccurrenceSet()
        original.extend(_occurrence(n, o, c) for n, o, c in raw)

        restored = OccurrenceSet.from_dict(original.to_dict())

        assert list(restored) == list(original)
        assert len(restored) == len(original)

    def test_entity_and_occurrence_round_trip_with_all_fields(self) -> None:
        occurrence = OccurrenceId(
            entity=EntityId(EntityKind.TYPE, "ns::Foo", "<int>"),
            observation=ObservationKind.DWARF,
            container="lib.so",
            attributes=(("size", "8"),),
        )

        assert OccurrenceId.from_dict(occurrence.to_dict()) == occurrence

    def test_repeated_attribute_keys_survive_serialization(self) -> None:
        """Attributes are pairs, not a mapping — a repeat must not collapse.

        ADR-062 D5 reserves maps for keys that are unique and order-free; a
        base list carrying one entry per repeated base subobject is exactly
        the case a mapping cannot express.
        """
        occurrence = OccurrenceId(
            entity=EntityId(EntityKind.BASE, "Derived"),
            observation=ObservationKind.AST,
            attributes=(("base", "B"), ("base", "B")),
        )

        restored = OccurrenceId.from_dict(occurrence.to_dict())

        assert restored == occurrence
        assert len(restored.attributes) == 2


class TestIdentityFieldsAreRejectedNotCoerced:
    """Codex review: `str()` at the document boundary destroyed multiplicity.

    A document holding two occurrences whose `qualified_name` values are `1`
    and `"1"` — two distinct JSON values — coerced both to `"1"`, produced one
    key, and `OccurrenceSet.add` dropped the second as an exact duplicate.
    This module's one invariant, defeated by a type coercion rather than by
    anything in the set logic.

    Rejecting matches the neighbouring primitives: `canonical_form` refuses a
    non-string mapping key, and `FactAvailability.from_dict` raises on an
    unknown status rather than downgrading it.
    """

    @staticmethod
    def _row(name: object) -> dict[str, object]:
        return {
            "entity": {"kind": "function", "qualified_name": name},
            "observation": "dwarf",
        }

    def test_the_reported_collapse_is_refused(self) -> None:
        """The literal case from review: `1` and `"1"` in one document."""
        with pytest.raises(TypeError, match="qualified_name"):
            OccurrenceSet.from_dict({"occurrences": [self._row(1), self._row("1")]})

    @pytest.mark.parametrize("value", [1, 1.0, True, None, ["x"], {"a": 1}])
    @pytest.mark.parametrize(
        "field_name", ["qualified_name", "discriminator", "container", "producer"]
    )
    def test_every_identity_bearing_field_refuses_a_non_string(
        self, field_name: str, value: object
    ) -> None:
        row: dict[str, object] = {
            "entity": {"kind": "function", "qualified_name": "f"},
            "observation": "dwarf",
        }
        if field_name in {"qualified_name", "discriminator"}:
            entity = dict(row["entity"])  # type: ignore[arg-type]
            entity[field_name] = value
            row["entity"] = entity
        else:
            row[field_name] = value

        with pytest.raises(TypeError, match=field_name):
            OccurrenceId.from_dict(row)

    @pytest.mark.parametrize("value", [1, True, None, ["x"]])
    def test_attribute_names_and_values_refuse_a_non_string(
        self, value: object
    ) -> None:
        with pytest.raises(TypeError, match="attribute"):
            OccurrenceId(
                entity=EntityId(EntityKind.FUNCTION, "f"),
                observation=ObservationKind.DWARF,
                attributes=((value, "x"),),  # type: ignore[arg-type]
            )
        with pytest.raises(TypeError, match="attribute"):
            OccurrenceId(
                entity=EntityId(EntityKind.FUNCTION, "f"),
                observation=ObservationKind.DWARF,
                attributes=(("x", value),),  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("row", [["a"], ["a", "b", "c"], [], "ab"])
    def test_a_malformed_attribute_row_is_refused(self, row: object) -> None:
        """Indexing `pair[0]`/`pair[1]` accepted any length and ignored the rest.

        Same shape as the coercion: a malformation read as a valid answer.
        `"ab"` is included because a bare string is a `Sequence` of length two
        whose elements are strings, so it would otherwise pass every check
        while meaning something the producer never wrote.
        """
        with pytest.raises((TypeError, ValueError)):
            OccurrenceId.from_dict(
                {
                    "entity": {"kind": "function", "qualified_name": "f"},
                    "observation": "dwarf",
                    "attributes": [row],
                }
            )

    def test_a_well_formed_document_still_round_trips(self) -> None:
        """The guard must not cost anything a real producer writes."""
        original = elf_symbol_occurrence(
            artifact_id="libfoo.so",
            name="foo",
            version="GLIBC_2.14",
            default_version=True,
            binding="global",
            symbol_type="func",
            visibility="default",
        )

        assert OccurrenceId.from_dict(original.to_dict()) == original


class TestAConflictNeedsSomethingToDisagreeAbout:
    """Codex review: the class docstring said "two or more"; nothing enforced it.

    Zero occurrences, one occurrence, and the same occurrence twice were all
    accepted, so a reader could report — or gate on — an ambiguity with no
    contradictory pair in it.
    """

    @staticmethod
    def _occurrence(size: str) -> OccurrenceId:
        return OccurrenceId(
            entity=EntityId(EntityKind.TYPE, "S"),
            observation=ObservationKind.DWARF,
            attributes=(("size", size),),
        )

    @pytest.mark.parametrize("count", [0, 1])
    def test_fewer_than_two_occurrences_is_refused(self, count: int) -> None:
        occurrences = tuple(self._occurrence("8") for _ in range(count))

        with pytest.raises(ValueError, match="two distinct occurrences"):
            IdentityConflict(reason="r", occurrences=occurrences)

    def test_the_same_occurrence_twice_is_refused(self) -> None:
        """Distinct *keys*, not distinct objects.

        Two equal occurrences are one observation recorded twice — exactly the
        case that looks like a conflict and is not.
        """
        one = self._occurrence("8")

        with pytest.raises(ValueError, match="distinct"):
            IdentityConflict(reason="r", occurrences=(one, one))

    @pytest.mark.parametrize("reason", [1, 1.0, True, None, ["x"], {"k": 1}, b"b"])
    def test_a_non_string_reason_is_refused(self, reason: object) -> None:
        """The last field in this package still guarded only at one door.

        `from_dict` coerced it with `str()` while the constructor accepted
        anything, so `IdentityConflict(reason=1, ...)` wrote `1` and read back
        as `"1"` — an object that does not equal its own round trip
        (CodeRabbit review).
        """
        pair = (self._occurrence("8"), self._occurrence("16"))

        with pytest.raises(TypeError):
            IdentityConflict(reason=reason, occurrences=pair)  # type: ignore[arg-type]

        with pytest.raises(TypeError):
            IdentityConflict.from_dict(
                {"reason": reason, "occurrences": [o.to_dict() for o in pair]}
            )

    def test_a_duplicate_occurrence_is_recorded_once(self) -> None:
        """One observation recorded twice must not inflate the disagreement.

        `OccurrenceSet.add` is already idempotent for an identical key; a
        conflict listing the same occurrence three times reported a
        three-way ambiguity that does not exist. Lossless, because the key is
        a function of every field.
        """
        left, right = self._occurrence("8"), self._occurrence("16")

        conflict = IdentityConflict(reason="r", occurrences=(left, left, right))

        assert (
            conflict.occurrences
            == IdentityConflict(reason="r", occurrences=(left, right)).occurrences
        )

    def test_a_genuine_pair_is_kept_and_ordered(self) -> None:
        conflict = IdentityConflict(
            reason="r", occurrences=(self._occurrence("16"), self._occurrence("8"))
        )

        assert len(conflict.occurrences) == 2
        assert list(conflict.occurrences) == sorted(
            conflict.occurrences, key=lambda o: o.key
        )

    def test_conflicts_still_produces_valid_conflicts(self) -> None:
        """The guard must not reject what the producer legitimately builds."""
        occurrences = OccurrenceSet()
        for size in ("8", "16"):
            occurrences.add(self._occurrence(size))

        def sizes_disagree(left: OccurrenceId, right: OccurrenceId) -> bool:
            sizes = (left.attribute("size"), right.attribute("size"))
            return all(sizes) and sizes[0] != sizes[1]

        found = occurrences.conflicts(sizes_disagree)

        assert len(found) == 1
        assert len(found[0].occurrences) == 2


class TestTheConstructorValidatesAttributeRows:
    """Codex review: a scalar row unpacked into a valid-looking pair.

    `attributes=("ab",)` was unpacked by a bare `for k, v in ...` as
    `("a", "b")`, so it produced the same key as an occurrence that really
    held that pair and `OccurrenceSet.add` dropped one as a duplicate. The
    document path already validated rows; the constructor did not.
    """

    @staticmethod
    def _occurrence(attributes: object) -> OccurrenceId:
        return OccurrenceId(
            entity=EntityId(EntityKind.TYPE, "S"),
            observation=ObservationKind.DWARF,
            attributes=attributes,  # type: ignore[arg-type]
        )

    def test_the_reported_scalar_row_is_refused(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            self._occurrence(("ab",))

    @pytest.mark.parametrize("row", ["ab", ("a",), ("a", "b", "c"), (), 5, None])
    def test_a_malformed_row_is_refused(self, row: object) -> None:
        with pytest.raises((TypeError, ValueError)):
            self._occurrence((row,))

    @pytest.mark.parametrize("field", ["container", "producer"])
    def test_non_string_site_fields_are_refused(self, field: str) -> None:
        with pytest.raises(TypeError, match=field):
            OccurrenceId(
                entity=EntityId(EntityKind.TYPE, "S"),
                observation=ObservationKind.DWARF,
                **{field: 1},  # type: ignore[arg-type]
            )

    def test_a_well_formed_occurrence_is_unaffected(self) -> None:
        occurrence = self._occurrence((("b", "2"), ("a", "1")))

        assert occurrence.attributes == (("a", "1"), ("b", "2"))
        assert OccurrenceId.from_dict(occurrence.to_dict()) == occurrence

    def test_no_scalar_row_can_forge_a_real_pair(self) -> None:
        """The consequence, stated directly: the collision is gone."""
        real = self._occurrence((("a", "b"),))
        occurrences = OccurrenceSet()
        occurrences.add(real)

        with pytest.raises((TypeError, ValueError)):
            occurrences.add(self._occurrence(("ab",)))

        assert len(list(occurrences)) == 1
