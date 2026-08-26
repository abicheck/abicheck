# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for ADR-062 D4's occurrence-preserving identity.

Written as invariants of the primitive rather than as examples of one
caller's usage, per the root `AGENTS.md` "Primitive-level property tests"
guidance: the failure this primitive exists to prevent — a first-wins index
silently discarding real evidence — is exactly the kind that an
example-shaped test written to confirm a fix cannot search for.
"""

from __future__ import annotations

import itertools

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
    group_by_entity,
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


# --------------------------------------------------------------------------
# The central invariant: nothing is ever dropped.
# --------------------------------------------------------------------------


class TestNothingIsEverDropped:
    """`OccurrenceSet.add` is total — the direct answer to first-wins."""

    def test_two_distinct_occurrences_of_one_entity_both_survive(self) -> None:
        occurrences = OccurrenceSet()
        entity = EntityId(EntityKind.FUNCTION, "ns::f")
        first = OccurrenceId(entity, ObservationKind.AST, "a.cpp")
        second = OccurrenceId(entity, ObservationKind.AST, "b.cpp")

        occurrences.add(first)
        occurrences.add(second)

        assert occurrences.occurrences_of(entity) == (first, second)
        assert len(occurrences) == 2
        # One entity, two observations — grouping, not deduplication.
        assert occurrences.entities() == (entity,)

    def test_an_identical_re_observation_is_idempotent(self) -> None:
        occurrences = OccurrenceSet()
        occurrence = _occurrence("f", container="a.cpp")

        occurrences.add(occurrence)
        occurrences.add(occurrence)

        # Idempotence is about the *same* observation seen twice (a producer
        # walking one DIE twice), which is not multiplicity to preserve.
        assert len(occurrences) == 1

    @given(
        st.lists(
            st.tuples(
                st.sampled_from(["f", "g", "ns::f"]),
                st.sampled_from(list(ObservationKind)),
                st.sampled_from(["", "a.cpp", "b.cpp"]),
            ),
            max_size=20,
        )
    )
    def test_length_always_equals_the_distinct_occurrences_added(
        self, raw: list[tuple[str, ObservationKind, str]]
    ) -> None:
        """No input can make the set hold fewer than its distinct members.

        This is the property a first-wins index fails: for it, adding two
        occurrences that share a *name* reduces the count. Here only an
        exactly-repeated occurrence may.
        """
        occurrences = OccurrenceSet()
        built = [_occurrence(name, obs, container) for name, obs, container in raw]
        occurrences.extend(built)

        assert len(occurrences) == len({o.key for o in built})

    @given(st.permutations([0, 1, 2, 3]))
    def test_the_result_never_depends_on_insertion_order(
        self, order: list[int]
    ) -> None:
        built = [
            _occurrence("f", ObservationKind.AST, "a.cpp"),
            _occurrence("f", ObservationKind.DWARF, "a.cpp"),
            _occurrence("g", ObservationKind.AST, "b.cpp"),
            _occurrence("f", ObservationKind.AST, "b.cpp"),
        ]
        shuffled = OccurrenceSet()
        shuffled.extend(built[i] for i in order)
        reference = OccurrenceSet()
        reference.extend(built)

        assert list(shuffled) == list(reference)
        assert shuffled.entities() == reference.entities()
        assert shuffled.conflicts() == reference.conflicts()


class TestAttributesSeparateOccurrences:
    def test_occurrences_differing_only_in_an_attribute_are_distinct(self) -> None:
        occurrences = OccurrenceSet()
        occurrences.add(_occurrence("f", container="lib.so", binding="global"))
        occurrences.add(_occurrence("f", container="lib.so", binding="weak"))

        assert len(occurrences) == 2

    @given(st.permutations([("a", "1"), ("b", "2"), ("c", "3")]))
    def test_attribute_order_does_not_change_identity(
        self, attributes: list[tuple[str, str]]
    ) -> None:
        """Whether a duplicate is *detected* must not depend on producer order.

        Without normalization, two producers reporting the same facts in a
        different order would look like two occurrences — inflating evidence
        rather than losing it, but wrong in the same way.
        """
        occurrence = OccurrenceId(
            entity=EntityId(EntityKind.FUNCTION, "f"),
            observation=ObservationKind.AST,
            attributes=tuple(attributes),
        )
        reference = OccurrenceId(
            entity=EntityId(EntityKind.FUNCTION, "f"),
            observation=ObservationKind.AST,
            attributes=(("a", "1"), ("b", "2"), ("c", "3")),
        )

        assert occurrence == reference
        assert occurrence.key == reference.key


class TestKeysCannotCollide:
    """The separator must not be forgeable from a real C++/ELF spelling."""

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            # `::` appears inside real qualified names, `@` inside versioned
            # symbol spellings, `/` inside paths — each would let one identity
            # impersonate another if it were the separator.
            (("ns", "f"), ("ns::f", "")),
            (("foo", "GLIBC_2.2"), ("foo@GLIBC_2.2", "")),
        ],
    )
    def test_distinct_parts_never_produce_one_key(
        self, left: tuple[str, str], right: tuple[str, str]
    ) -> None:
        a = EntityId(EntityKind.FUNCTION, left[0], left[1])
        b = EntityId(EntityKind.FUNCTION, right[0], right[1])

        assert a.key != b.key

    def test_kind_participates_in_the_key(self) -> None:
        """A type and a function sharing a spelling are two entities.

        C and C++ both allow this (a tag and an ordinary-namespace name), so
        collapsing them would be a real loss, not a hypothetical one.
        """
        assert (
            EntityId(EntityKind.TYPE, "Foo").key
            != EntityId(EntityKind.FUNCTION, "Foo").key
        )


# --------------------------------------------------------------------------
# Conflicts are reported, never resolved.
# --------------------------------------------------------------------------


class TestConflictsAreReportedNotResolved:
    def test_same_producer_same_container_disagreement_is_a_conflict(self) -> None:
        occurrences = OccurrenceSet()
        occurrences.add(_occurrence("f", container="a.cpp", ret="int"))
        occurrences.add(_occurrence("f", container="a.cpp", ret="long"))

        conflicts = occurrences.conflicts()

        assert len(conflicts) == 1
        # Both survive — the point of the design.
        assert len(conflicts[0].occurrences) == 2
        assert len(occurrences) == 2

    def test_different_observation_kinds_are_not_a_conflict(self) -> None:
        """One function seen in DWARF and in the export table is normal."""
        occurrences = OccurrenceSet()
        occurrences.add(_occurrence("f", ObservationKind.DWARF, "lib.so"))
        occurrences.add(_occurrence("f", ObservationKind.EXPORT_TABLE, "lib.so"))

        assert occurrences.conflicts() == ()
        assert occurrences.is_ambiguous(EntityId(EntityKind.FUNCTION, "f"))

    def test_different_containers_are_not_a_conflict(self) -> None:
        """One header declaration reached through two TUs is normal."""
        occurrences = OccurrenceSet()
        occurrences.add(_occurrence("f", container="a.cpp"))
        occurrences.add(_occurrence("f", container="b.cpp"))

        assert occurrences.conflicts() == ()

    @given(
        st.lists(
            st.tuples(st.sampled_from(["f", "g"]), st.sampled_from(["int", "long"])),
            max_size=8,
        )
    )
    def test_a_conflict_never_costs_an_occurrence(
        self, raw: list[tuple[str, str]]
    ) -> None:
        """Reporting a conflict must not consume the occurrences it names."""
        occurrences = OccurrenceSet()
        built = [_occurrence(name, container="a.cpp", ret=ret) for name, ret in raw]
        occurrences.extend(built)
        before = len(occurrences)

        occurrences.conflicts()

        assert len(occurrences) == before == len({o.key for o in built})

    def test_conflicts_do_not_raise(self) -> None:
        """A package must stay writable with an unresolved conflict in it.

        Raising here would abort the capture that found the ambiguity, which
        loses the very evidence the conflict record exists to preserve.
        """
        occurrences = OccurrenceSet()
        for ret in ("int", "long", "short"):
            occurrences.add(_occurrence("f", container="a.cpp", ret=ret))

        conflicts = occurrences.conflicts()

        assert len(conflicts[0].occurrences) == 3


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


class TestElfSymbolOccurrences:
    """`ElfMetadata.symbol_map` is bare-name-keyed and last-entry-wins."""

    def test_two_versions_of_one_bare_name_are_two_occurrences(self) -> None:
        occurrences = OccurrenceSet()
        occurrences.add(
            elf_symbol_occurrence(
                artifact_id="libc.so.6",
                name="foo",
                version="GLIBC_2.2",
                binding="global",
            )
        )
        occurrences.add(
            elf_symbol_occurrence(
                artifact_id="libc.so.6",
                name="foo",
                version="GLIBC_2.14",
                default_version=True,
                binding="weak",
            )
        )

        # Two exports with independent lifetimes: a format that collapses
        # them cannot report the removal of one.
        assert len(occurrences) == 2
        assert len(occurrences.entities()) == 2
        # Not a conflict — both are legitimate, simultaneous definitions.
        assert occurrences.conflicts() == ()

    def test_binding_is_preserved_per_version(self) -> None:
        """The coin-flip a bare-name map produces for `binding` is the bug.

        A `binding: weak` suppression rule matching a removal that is a real
        break from the surviving version's point of view is the concrete
        failure; per-version binding is what makes it answerable.
        """
        occurrences = OccurrenceSet()
        for version, binding in (("GLIBC_2.2", "global"), ("GLIBC_2.14", "weak")):
            occurrences.add(
                elf_symbol_occurrence(
                    artifact_id="libc.so.6",
                    name="foo",
                    version=version,
                    binding=binding,
                )
            )

        bindings = {o.entity.discriminator: o.attribute("binding") for o in occurrences}

        assert bindings == {"GLIBC_2.2": "global", "GLIBC_2.14": "weak"}

    def test_the_same_symbol_in_two_artifacts_stays_separable(self) -> None:
        occurrences = OccurrenceSet()
        for artifact in ("libcore.so", "libthread.so"):
            occurrences.add(
                elf_symbol_occurrence(artifact_id=artifact, name="foo", version="V1")
            )

        assert len(occurrences) == 2
        assert {o.container for o in occurrences} == {"libcore.so", "libthread.so"}
        # Same entity (one logical export), observed in two artifacts.
        assert len(occurrences.entities()) == 1

    def test_defined_and_undefined_are_distinct_observations(self) -> None:
        """A provider's definition and a consumer's import are not one fact."""
        occurrences = OccurrenceSet()
        occurrences.add(
            elf_symbol_occurrence(artifact_id="lib.so", name="foo", defined=True)
        )
        occurrences.add(
            elf_symbol_occurrence(artifact_id="lib.so", name="foo", defined=False)
        )

        assert len(occurrences) == 2


class TestGroupByEntity:
    @given(st.permutations([("f", "a.cpp"), ("f", "b.cpp"), ("g", "a.cpp")]))
    def test_grouping_preserves_every_occurrence(
        self, raw: list[tuple[str, str]]
    ) -> None:
        built = [_occurrence(name, container=c) for name, c in raw]

        grouped = group_by_entity(built)

        assert sum(len(v) for v in grouped.values()) == len(built)

    def test_grouping_returns_tuples_not_winners(self) -> None:
        """The return shape must make a "pick one" call site unwritable."""
        grouped = group_by_entity(
            [_occurrence("f", container="a.cpp"), _occurrence("f", container="b.cpp")]
        )

        assert all(isinstance(v, tuple) for v in grouped.values())
        assert list(grouped.values()) == [
            (_occurrence("f", container="a.cpp"), _occurrence("f", container="b.cpp"))
        ]


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


class TestOrderingIsTotal:
    def test_entities_sort_deterministically(self) -> None:
        """`entities()` must not expose dict insertion order to a caller."""
        names = ["z", "a", "m"]
        forward, backward = OccurrenceSet(), OccurrenceSet()
        forward.extend(_occurrence(n) for n in names)
        backward.extend(_occurrence(n) for n in reversed(names))

        assert forward.entities() == backward.entities()

    def test_every_permutation_agrees(self) -> None:
        built = [_occurrence(n, container=c) for n, c in (("f", "a"), ("g", "b"))]
        results = {
            tuple(o.key for o in _built_set(order))
            for order in itertools.permutations(built)
        }

        assert len(results) == 1


def _built_set(occurrences: tuple[OccurrenceId, ...]) -> OccurrenceSet:
    result = OccurrenceSet()
    result.extend(occurrences)
    return result


class TestKeysAreInjectionProof:
    """Codex review: a separator-joined key could be forged by part content.

    The separator argument was right about spellings and wrong about
    attributes, which carry arbitrary producer-supplied strings. Length
    prefixing removes the question instead of narrowing it.
    """

    def test_the_reported_counterexample_stays_two_occurrences(self) -> None:
        entity = EntityId(EntityKind.FUNCTION, "f")
        forged = OccurrenceId(entity, ObservationKind.AST, "", (("a", "x\x1fb=y"),))
        genuine = OccurrenceId(
            entity, ObservationKind.AST, "", (("a", "x"), ("b", "y"))
        )

        assert forged != genuine
        assert forged.key != genuine.key

        occurrences = OccurrenceSet()
        occurrences.add(forged)
        occurrences.add(genuine)

        # The invariant this module exists for, reached through the key
        # function rather than through the set logic.
        assert len(occurrences) == 2

    @given(
        st.text(alphabet=st.characters(min_codepoint=1, max_codepoint=200), max_size=6),
        st.text(alphabet=st.characters(min_codepoint=1, max_codepoint=200), max_size=6),
    )
    def test_no_attribute_content_can_forge_a_part_boundary(
        self, left: str, right: str
    ) -> None:
        """Two attribute pairs vs one pair holding both, for any content."""
        entity = EntityId(EntityKind.FUNCTION, "f")
        two_pairs = OccurrenceId(
            entity, ObservationKind.AST, "", (("a", left), ("b", right))
        )
        one_pair = OccurrenceId(
            entity, ObservationKind.AST, "", (("a", f"{left}\x1fb={right}"),)
        )

        assert (two_pairs == one_pair) == (two_pairs.key == one_pair.key)

    @given(
        st.text(alphabet=st.characters(min_codepoint=1, max_codepoint=200), max_size=8),
        st.text(alphabet=st.characters(min_codepoint=1, max_codepoint=200), max_size=8),
    )
    def test_entity_parts_never_collide_for_any_content(
        self, name: str, discriminator: str
    ) -> None:
        """Key equality must mean identity equality, for arbitrary strings."""
        a = EntityId(EntityKind.FUNCTION, name, discriminator)
        b = EntityId(EntityKind.FUNCTION, name + discriminator, "")

        assert (a == b) == (a.key == b.key)


class TestProducerIsPartOfTheObservationSite:
    """Codex review: two producers read as one producer contradicting itself.

    Not a corner case in this codebase — `--ast-frontend hybrid` exists to
    have Clang and CastXML both describe one translation unit, and
    `fact_provenance` records which produced what.
    """

    def test_two_ast_producers_in_one_tu_are_not_a_conflict(self) -> None:
        entity = EntityId(EntityKind.FUNCTION, "f")
        occurrences = OccurrenceSet()
        for producer in ("clang", "castxml"):
            occurrences.add(
                OccurrenceId(
                    entity,
                    ObservationKind.AST,
                    "a.cpp",
                    (("size", "8"),),
                    producer=producer,
                )
            )

        assert occurrences.conflicts() == ()
        # Still two occurrences — grouping them is not merging them.
        assert len(occurrences) == 2

    def test_one_producer_contradicting_itself_is_still_a_conflict(self) -> None:
        """The narrowing must not weaken what the rule is actually for."""
        entity = EntityId(EntityKind.FUNCTION, "f")
        occurrences = OccurrenceSet()
        for size in ("8", "16"):
            occurrences.add(
                OccurrenceId(
                    entity,
                    ObservationKind.AST,
                    "a.cpp",
                    (("size", size),),
                    producer="clang",
                )
            )

        conflicts = occurrences.conflicts()

        assert len(conflicts) == 1
        assert "clang" in conflicts[0].reason
        assert len(conflicts[0].occurrences) == 2

    def test_producer_separates_occurrences_in_the_key(self) -> None:
        entity = EntityId(EntityKind.FUNCTION, "f")
        clang = OccurrenceId(entity, ObservationKind.AST, "a.cpp", producer="clang")
        castxml = OccurrenceId(entity, ObservationKind.AST, "a.cpp", producer="castxml")

        assert clang != castxml
        assert clang.key != castxml.key

    def test_producer_is_keyword_only(self) -> None:
        """Adding it must not change what any positional call means."""
        with pytest.raises(TypeError):
            OccurrenceId(  # type: ignore[misc]
                EntityId(EntityKind.FUNCTION, "f"),
                ObservationKind.AST,
                "a.cpp",
                (),
                "clang",
            )

    def test_producer_round_trips(self) -> None:
        occurrence = OccurrenceId(
            EntityId(EntityKind.FUNCTION, "f"),
            ObservationKind.AST,
            "a.cpp",
            producer="clang",
        )

        assert OccurrenceId.from_dict(occurrence.to_dict()) == occurrence

    def test_site_names_all_three_axes(self) -> None:
        occurrence = OccurrenceId(
            EntityId(EntityKind.FUNCTION, "f"),
            ObservationKind.DWARF,
            "lib.so",
            producer="dwarf",
        )

        assert occurrence.site == ("dwarf", "lib.so", "dwarf")


class TestIdentifiersAreOrderable:
    """Codex review: `order=True` advertised an ordering that raised.

    The generated comparison goes field by field and reaches a plain
    `enum.Enum`, which does not implement `<` — so `sorted()` raised
    `TypeError` for any two identifiers differing at the enum field. Both
    types were affected, not only `OccurrenceId`.
    """

    def test_occurrences_differing_by_observation_kind_sort(self) -> None:
        entity = EntityId(EntityKind.FUNCTION, "f")
        pair = [
            OccurrenceId(entity, ObservationKind.DWARF),
            OccurrenceId(entity, ObservationKind.AST),
        ]

        assert sorted(pair) == sorted(pair, key=lambda o: o.key)

    def test_entities_differing_by_kind_sort(self) -> None:
        pair = [EntityId(EntityKind.TYPE, "a"), EntityId(EntityKind.FUNCTION, "a")]

        assert sorted(pair) == sorted(pair, key=lambda e: e.key)

    @given(
        st.lists(
            st.tuples(
                st.sampled_from(list(EntityKind)),
                st.sampled_from(["a", "b", "ns::c"]),
            ),
            max_size=8,
        )
    )
    def test_entity_ordering_is_total_over_every_kind(
        self, raw: list[tuple[EntityKind, str]]
    ) -> None:
        """No pair of identifiers may be unorderable."""
        entities = [EntityId(kind, name) for kind, name in raw]

        assert sorted(entities) == sorted(entities, key=lambda e: e.key)

    def test_ordering_agrees_with_the_module_s_own_accessors(self) -> None:
        """Sorting identifiers must match how the set already orders them."""
        occurrences = OccurrenceSet()
        built = [
            OccurrenceId(EntityId(EntityKind.TYPE, "z"), ObservationKind.AST),
            OccurrenceId(EntityId(EntityKind.FUNCTION, "a"), ObservationKind.DWARF),
        ]
        occurrences.extend(built)

        assert list(occurrences) == sorted(built)

    def test_the_full_comparison_set_is_available(self) -> None:
        entity = EntityId(EntityKind.FUNCTION, "f")
        low = OccurrenceId(entity, ObservationKind.AST)
        high = OccurrenceId(entity, ObservationKind.DWARF)

        assert (low < high) and (low <= high)
        assert (high > low) and (high >= low)
