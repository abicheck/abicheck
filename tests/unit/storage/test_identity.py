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
from adr062_scope import adr062_module_paths
from hypothesis import given, strategies as st

from abicheck.storage.identity import (
    EntityId,
    EntityKind,
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
        assert shuffled.conflicts(_any_attribute_disagrees) == reference.conflicts(
            _any_attribute_disagrees
        )


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

        conflicts = occurrences.conflicts(_any_attribute_disagrees)

        assert len(conflicts) == 1
        # Both survive — the point of the design.
        assert len(conflicts[0].occurrences) == 2
        assert len(occurrences) == 2

    def test_different_observation_kinds_are_not_a_conflict(self) -> None:
        """One function seen in DWARF and in the export table is normal."""
        occurrences = OccurrenceSet()
        occurrences.add(_occurrence("f", ObservationKind.DWARF, "lib.so"))
        occurrences.add(_occurrence("f", ObservationKind.EXPORT_TABLE, "lib.so"))

        assert occurrences.conflicts(_any_attribute_disagrees) == ()
        assert occurrences.is_ambiguous(EntityId(EntityKind.FUNCTION, "f"))

    def test_different_containers_are_not_a_conflict(self) -> None:
        """One header declaration reached through two TUs is normal."""
        occurrences = OccurrenceSet()
        occurrences.add(_occurrence("f", container="a.cpp"))
        occurrences.add(_occurrence("f", container="b.cpp"))

        assert occurrences.conflicts(_any_attribute_disagrees) == ()

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

        occurrences.conflicts(_any_attribute_disagrees)

        assert len(occurrences) == before == len({o.key for o in built})

    def test_conflicts_do_not_raise(self) -> None:
        """A package must stay writable with an unresolved conflict in it.

        Raising here would abort the capture that found the ambiguity, which
        loses the very evidence the conflict record exists to preserve.
        """
        occurrences = OccurrenceSet()
        for ret in ("int", "long", "short"):
            occurrences.add(_occurrence("f", container="a.cpp", ret=ret))

        conflicts = occurrences.conflicts(_any_attribute_disagrees)

        assert len(conflicts[0].occurrences) == 3


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
        assert occurrences.conflicts(_any_attribute_disagrees) == ()

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

        assert occurrences.conflicts(_any_attribute_disagrees) == ()
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

        conflicts = occurrences.conflicts(_any_attribute_disagrees)

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


class TestStoredStateIsCanonicalNotJustItsViews:
    """CodeRabbit review: `__eq__` and `repr` leaked producer order.

    `add` appended, so a bucket's list order followed observation order. The
    generated `__eq__` compares those lists element by element, so two sets
    holding the same occurrences of one entity compared *unequal* while
    `list()` and `to_dict()` agreed — the documented invariant said they are
    indistinguishable. `repr` leaked the same order across entities.

    Worth recording how this survived the round-1 order-independence property:
    that test asserted `list(a) == list(b)`, which the accessors' own sorting
    made true regardless. The state underneath was never canonical.
    """

    def test_equality_is_order_independent_within_one_entity(self) -> None:
        entity = EntityId(EntityKind.FUNCTION, "f")
        first = OccurrenceId(entity, ObservationKind.AST, "a.cpp")
        second = OccurrenceId(entity, ObservationKind.DWARF, "a.cpp")

        forward, backward = OccurrenceSet(), OccurrenceSet()
        forward.extend([first, second])
        backward.extend([second, first])

        assert forward == backward

    def test_repr_is_order_independent_across_entities(self) -> None:
        a = OccurrenceId(EntityId(EntityKind.FUNCTION, "f"), ObservationKind.AST)
        b = OccurrenceId(EntityId(EntityKind.FUNCTION, "g"), ObservationKind.AST)

        forward, backward = OccurrenceSet(), OccurrenceSet()
        forward.extend([a, b])
        backward.extend([b, a])

        assert repr(forward) == repr(backward)

    def test_the_bucket_itself_is_stored_in_key_order(self) -> None:
        """Canonical *state*, not a canonical view over unsorted state."""
        entity = EntityId(EntityKind.FUNCTION, "f")
        built = [
            OccurrenceId(entity, ObservationKind.SOURCE_LOCATION, "z.cpp"),
            OccurrenceId(entity, ObservationKind.AST, "a.cpp"),
            OccurrenceId(entity, ObservationKind.DWARF, "m.cpp"),
        ]
        occurrences = OccurrenceSet()
        occurrences.extend(built)

        stored = occurrences.occurrences_of(entity)

        assert list(stored) == sorted(built, key=lambda o: o.key)

    @given(st.permutations([0, 1, 2, 3, 4]))
    def test_every_ordering_produces_an_equal_set(self, order: list[int]) -> None:
        """The invariant the earlier property test could not have caught."""
        entity = EntityId(EntityKind.FUNCTION, "f")
        built = [
            OccurrenceId(entity, ObservationKind.AST, "a.cpp"),
            OccurrenceId(entity, ObservationKind.DWARF, "a.cpp"),
            OccurrenceId(entity, ObservationKind.AST, "b.cpp"),
            OccurrenceId(EntityId(EntityKind.TYPE, "T"), ObservationKind.AST),
            OccurrenceId(EntityId(EntityKind.FUNCTION, "g"), ObservationKind.PDB),
        ]
        reference = OccurrenceSet()
        reference.extend(built)
        shuffled = OccurrenceSet()
        shuffled.extend(built[i] for i in order)

        assert shuffled == reference
        assert repr(shuffled) == repr(reference)
        assert shuffled.to_dict() == reference.to_dict()


class TestTheSemanticJudgementBelongsToTheCaller:
    """Codex review, third finding on the same site tuple.

    Earlier versions decided by themselves that every same-site group was a
    contradiction, and the tuple grew a dimension each time that proved wrong:
    observation kind and container, then producer (`--ast-frontend hybrid`),
    then a forward declaration followed by its definition — one producer
    legitimately reporting two *different declarations* of one entity in one
    file, not two answers about one declaration.

    Three rounds of adding a dimension is the signal that the question was in
    the wrong layer, not under-specified. The structural half stays here; the
    semantic half is the caller's.
    """

    @staticmethod
    def _forward_declaration_then_definition() -> OccurrenceSet:
        entity = EntityId(EntityKind.TYPE, "Foo")
        occurrences = OccurrenceSet()
        for line, is_definition in (("3", "0"), ("9", "1")):
            occurrences.add(
                OccurrenceId(
                    entity,
                    ObservationKind.AST,
                    "a.cpp",
                    (("line", line), ("is_definition", is_definition)),
                    producer="clang",
                )
            )
        return occurrences

    def test_the_structural_fact_is_still_reported(self) -> None:
        """This layer can always determine same-site multiplicity."""
        occurrences = self._forward_declaration_then_definition()

        groups = occurrences.same_site_observations()

        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_a_declaration_and_its_definition_are_not_a_conflict(self) -> None:
        """The reported false positive, under a predicate that knows better."""

        def size_disagrees(left: OccurrenceId, right: OccurrenceId) -> bool:
            return bool(left.attribute("size")) and left.attribute(
                "size"
            ) != right.attribute("size")

        assert (
            self._forward_declaration_then_definition().conflicts(size_disagrees) == ()
        )

    def test_a_genuine_contradiction_is_still_reported(self) -> None:
        def size_disagrees(left: OccurrenceId, right: OccurrenceId) -> bool:
            return bool(left.attribute("size")) and left.attribute(
                "size"
            ) != right.attribute("size")

        entity = EntityId(EntityKind.TYPE, "Foo")
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

        conflicts = occurrences.conflicts(size_disagrees)

        assert len(conflicts) == 1
        assert len(conflicts[0].occurrences) == 2

    def test_a_predicate_that_never_fires_reports_nothing(self) -> None:
        """No same-site group is a conflict by structure alone."""
        occurrences = self._forward_declaration_then_definition()

        assert occurrences.conflicts(lambda left, right: False) == ()
        # ...while the occurrences themselves are all still there.
        assert len(occurrences) == 2

    def test_same_site_groups_are_deterministic(self) -> None:
        entity = EntityId(EntityKind.TYPE, "Foo")
        built = [
            OccurrenceId(entity, ObservationKind.AST, "a.cpp", (("n", "2"),)),
            OccurrenceId(entity, ObservationKind.AST, "a.cpp", (("n", "1"),)),
        ]
        forward, backward = OccurrenceSet(), OccurrenceSet()
        forward.extend(built)
        backward.extend(reversed(built))

        assert forward.same_site_observations() == backward.same_site_observations()

    def test_only_the_members_the_predicate_names_are_reported(self) -> None:
        """A group can hold one contradictory pair and one innocent member.

        The predicate is deliberately **symmetric** — both sides must carry a
        size before a disagreement counts. An earlier version of this test used
        an asymmetric one (`bool(left...) and left != right`), which does not
        say what this test claims: under it, an unsized member really *is*
        contradicted by a sized one, so calling it "innocent" was an artifact
        of the old implementation dropping members that did not independently
        qualify. Once `conflicts()` started evaluating unordered pairs, this
        test failed — correctly — and the premise, not the fix, was what
        needed correcting.
        """

        def sizes_disagree(left: OccurrenceId, right: OccurrenceId) -> bool:
            sizes = (left.attribute("size"), right.attribute("size"))
            return all(sizes) and sizes[0] != sizes[1]

        entity = EntityId(EntityKind.TYPE, "Foo")
        occurrences = OccurrenceSet()
        for attributes in ((("size", "8"),), (("size", "16"),), (("note", "x"),)):
            occurrences.add(
                OccurrenceId(entity, ObservationKind.AST, "a.cpp", attributes)
            )

        conflicts = occurrences.conflicts(sizes_disagree)

        assert len(conflicts) == 1
        assert {o.attribute("size") for o in conflicts[0].occurrences} == {"8", "16"}


class TestARepeatedAttributeIsNotSilentlyResolved:
    """Codex review: the accessor discarded a value the format kept.

    The serialized form is a list of pairs rather than a mapping precisely so
    a repeated attribute name does not lose a value. `attribute()` then
    returned the first match — and since `__post_init__` sorts the pairs,
    "first" meant lexicographically smallest *value*, so `size=8` alongside
    `size=16` answered `"16"` with nothing to indicate a choice was made.

    This module exists because a first-wins index discarded losers; an
    accessor doing the same one layer down is no better for being smaller.
    """

    @staticmethod
    def _repeated() -> OccurrenceId:
        return OccurrenceId(
            entity=EntityId(EntityKind.TYPE, "S"),
            observation=ObservationKind.DWARF,
            attributes=(("size", "8"), ("size", "16"), ("binding", "global")),
        )

    def test_every_value_is_reachable(self) -> None:
        assert set(self._repeated().attribute_values("size")) == {"8", "16"}

    def test_the_singular_accessor_refuses_rather_than_choosing(self) -> None:
        with pytest.raises(ValueError, match="recorded 2 times"):
            self._repeated().attribute("size")

    def test_an_unrepeated_name_still_answers_normally(self) -> None:
        """Refusing ambiguity must not cost the ordinary case."""
        occurrence = self._repeated()

        assert occurrence.attribute("binding") == "global"
        assert occurrence.attribute("absent") == ""
        assert occurrence.attribute("absent", "fallback") == "fallback"

    def test_attribute_values_is_empty_for_an_absent_name(self) -> None:
        assert self._repeated().attribute_values("absent") == ()

    def test_repeated_values_still_distinguish_two_occurrences(self) -> None:
        """The review's second claim, checked rather than assumed.

        It said an occurrence with both values "can compare equal to one
        containing only the selected value". It cannot: every pair contributes
        to `key`. The defect was confined to the accessor, and saying so
        precisely matters more than agreeing with the whole comment.
        """
        both = self._repeated()
        one = OccurrenceId(
            entity=EntityId(EntityKind.TYPE, "S"),
            observation=ObservationKind.DWARF,
            attributes=(("size", "16"), ("binding", "global")),
        )

        assert both != one
        assert both.key != one.key

    def test_a_repeated_attribute_survives_a_round_trip(self) -> None:
        """The reason the accessor had to change rather than the storage."""
        original = self._repeated()

        assert OccurrenceId.from_dict(original.to_dict()) == original
        assert OccurrenceId.from_dict(original.to_dict()).attribute_values(
            "size"
        ) == original.attribute_values("size")


class TestAnAsymmetricPredicateStillReportsItsConflict:
    """Codex review: a contradiction the caller named could vanish entirely.

    Nothing in `conflicts()`'s signature promises the predicate is symmetric,
    and an asymmetric one is easy to write by accident. Requiring each
    occurrence to qualify *independently* then dropped the pair: one endpoint
    qualified, the other did not, a group of one is not a conflict, and the
    finding disappeared. Unordered pairs with either direction counting is the
    only direction this module may err in — reporting, never discarding.
    """

    @staticmethod
    def _asymmetric(left: OccurrenceId, right: OccurrenceId) -> bool:
        """True for a sized observation against a differently-sized one.

        The literal predicate from the review. False in reverse when the right
        side carries no size at all.
        """
        return bool(left.attribute("size")) and left.attribute(
            "size"
        ) != right.attribute("size")

    @staticmethod
    def _pair() -> OccurrenceSet:
        entity = EntityId(EntityKind.TYPE, "S")
        occurrences = OccurrenceSet()
        for attributes in ((("size", "8"),), (("note", "x"),)):
            occurrences.add(
                OccurrenceId(entity, ObservationKind.DWARF, "a.o", attributes)
            )
        return occurrences

    def test_the_direction_that_holds_is_enough(self) -> None:
        assert self._asymmetric(*self._pair()) != self._asymmetric(
            *reversed(list(self._pair()))
        ), "fixture must actually be asymmetric or this test proves nothing"

        conflicts = self._pair().conflicts(self._asymmetric)

        assert len(conflicts) == 1

    def test_both_endpoints_are_retained(self) -> None:
        """Reporting one side would name a contradiction without its other half."""
        conflicts = self._pair().conflicts(self._asymmetric)

        assert len(conflicts[0].occurrences) == 2

    def test_a_symmetric_predicate_is_unaffected(self) -> None:
        """The change must be invisible to callers who were already correct."""

        def sizes_disagree(left: OccurrenceId, right: OccurrenceId) -> bool:
            sizes = (left.attribute("size"), right.attribute("size"))
            return all(sizes) and sizes[0] != sizes[1]

        entity = EntityId(EntityKind.TYPE, "S")
        occurrences = OccurrenceSet()
        for attributes in ((("size", "8"),), (("size", "16"),), (("note", "x"),)):
            occurrences.add(
                OccurrenceId(entity, ObservationKind.DWARF, "a.o", attributes)
            )

        conflicts = occurrences.conflicts(sizes_disagree)

        assert len(conflicts) == 1
        assert len(conflicts[0].occurrences) == 2

    def test_a_never_conflicting_predicate_still_reports_nothing(self) -> None:
        """Evaluating both directions must not manufacture conflicts."""
        assert self._pair().conflicts(lambda left, right: False) == ()

    def test_order_of_insertion_does_not_change_the_result(self) -> None:
        """An asymmetric predicate must not become order-sensitive either."""
        entity = EntityId(EntityKind.TYPE, "S")
        rows = ((("size", "8"),), (("note", "x"),))
        forward, backward = OccurrenceSet(), OccurrenceSet()
        for attributes in rows:
            forward.add(OccurrenceId(entity, ObservationKind.DWARF, "a.o", attributes))
        for attributes in reversed(rows):
            backward.add(OccurrenceId(entity, ObservationKind.DWARF, "a.o", attributes))

        assert forward.conflicts(self._asymmetric) == backward.conflicts(
            self._asymmetric
        )


class TestOccurrencesOfChecksItsArgument:
    """The same read-door sweep as the ledger's, applied to this module.

    Not the same severity, and worth being clear about why: passing a
    non-`EntityId` already raised — a bare `AttributeError` from the `.key`
    access — so this never returned the silently-wrong empty tuple that
    would mean "no occurrences of this entity". What it did was name an
    internal attribute instead of the argument, which is the convention
    every other door in this package already follows.
    """

    def test_a_non_entity_is_refused_by_name(self) -> None:
        with pytest.raises(TypeError, match="entity must be a EntityId"):
            OccurrenceSet().occurrences_of("compute")

    def test_a_real_lookup_is_untouched(self) -> None:
        """The control, including the genuinely-absent case.

        An entity with no occurrences must still answer `()` rather than
        raising — "absent" and "malformed" are different questions and the
        guard must not merge them.
        """
        entity = EntityId(kind=EntityKind.FUNCTION, qualified_name="compute")
        absent = EntityId(kind=EntityKind.FUNCTION, qualified_name="missing")
        occurrences = OccurrenceSet()
        occurrences.add(OccurrenceId(entity=entity, observation=ObservationKind.AST))

        assert len(occurrences.occurrences_of(entity)) == 1
        assert occurrences.occurrences_of(absent) == ()


class TestAttributeLookupNamesAreValidated:
    """The same read-door rule, at the attribute accessors.

    An unnormalized name misses every stored pair and answers "no such
    attribute". A conflict predicate or resolver reads that as *captured
    evidence absent*, so a real contradiction goes unreported — the silent
    direction, not the loud one (Codex review).
    """

    @staticmethod
    def _occurrence() -> OccurrenceId:
        return OccurrenceId(
            entity=EntityId(kind=EntityKind.FUNCTION, qualified_name="compute"),
            observation=ObservationKind.AST,
            attributes=(("size", "8"),),
        )

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param(1, id="int"),
            pytest.param(True, id="bool"),
            pytest.param(None, id="none"),
            pytest.param(("size",), id="tuple"),
            pytest.param(b"size", id="bytes"),
        ],
    )
    def test_both_accessors_refuse_a_non_string_name(self, name: object) -> None:
        occurrence = self._occurrence()

        with pytest.raises(TypeError, match="name must be a string"):
            occurrence.attribute_values(name)
        with pytest.raises(TypeError, match="name must be a string"):
            occurrence.attribute(name)

    def test_one_guard_covers_both_accessors(self) -> None:
        """`attribute` delegates to `attribute_values`, so the check is not
        written twice — which is the arrangement that cannot drift.
        """
        assert self._occurrence().attribute("size") == "8"
        assert self._occurrence().attribute_values("size") == ("8",)
        assert self._occurrence().attribute("absent") == ""


class TestIsAmbiguousChecksItsArgument:
    """The door the previous round's sweep missed.

    Worse than its `occurrences_of` sibling: that one raised, while this
    returned a plain `False` for a malformed entity, so a caller gating on
    it proceeded as though identity had been checked.
    """

    def test_a_non_entity_is_refused(self) -> None:
        with pytest.raises(TypeError, match="entity must be a EntityId"):
            OccurrenceSet().is_ambiguous("compute")

    def test_real_answers_are_untouched(self) -> None:
        entity = EntityId(kind=EntityKind.FUNCTION, qualified_name="compute")
        occurrences = OccurrenceSet()
        occurrences.add(OccurrenceId(entity=entity, observation=ObservationKind.AST))
        assert occurrences.is_ambiguous(entity) is False

        occurrences.add(OccurrenceId(entity=entity, observation=ObservationKind.DWARF))
        assert occurrences.is_ambiguous(entity) is True


class TestEveryKeyTakingDoorIsGuarded:
    """The sweep as a test, because doing it by hand missed three doors.

    The `for_family`/`for_entity` round claimed `occurrences_of` was "the
    only other door taking a caller-supplied lookup key". It was not:
    `attribute_values`, `attribute` and `is_ambiguous` were all unguarded,
    and the first two were found by review rather than by that claim. An
    informal sweep is not evidence, so this enumerates the doors instead of
    asserting the conclusion.
    """

    def test_no_public_key_taking_method_is_unguarded(self) -> None:
        import ast

        guards = ("_decision_key", "_identity_text", "_instance_of")
        bodies: dict[str, tuple[str, str]] = {}
        for path in adr062_module_paths():
            if path.name == "guards.py":
                # The guards themselves take a `field_name` label, which is
                # the *subject* of a check rather than a lookup key.
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for fn in node.body:
                    if not isinstance(fn, ast.FunctionDef) or fn.name.startswith("_"):
                        continue
                    # A pure stub -- a docstring followed by nothing but `...`
                    # or `pass`, the shape a `Protocol` method (or an ABC's)
                    # is declared with -- reads nothing itself, so there is no
                    # body for it to guard. `ObjectStore.get`/`.has` are
                    # exactly this: the interface names a `str` key, but only
                    # a concrete implementation ever dereferences one, and
                    # that implementation is swept on its own merits below.
                    # Without this, a `Protocol` could never satisfy this
                    # sweep at all, regardless of what implements it.
                    body_stmts = [
                        stmt
                        for stmt in fn.body
                        if not (
                            isinstance(stmt, ast.Expr)
                            and isinstance(stmt.value, ast.Constant)
                            and isinstance(stmt.value.value, str)
                        )
                    ]
                    if len(body_stmts) == 1 and (
                        (
                            isinstance(body_stmts[0], ast.Expr)
                            and isinstance(body_stmts[0].value, ast.Constant)
                            and body_stmts[0].value.value is Ellipsis
                        )
                        or isinstance(body_stmts[0], ast.Pass)
                    ):
                        continue
                    keyish = [
                        arg.arg
                        # Every parameter kind, not only the plain
                        # positional ones. This package already declares
                        # keyword-only fields (`OccurrenceId.producer`), so
                        # a keyword-only lookup key would have passed a
                        # sweep reading `fn.args.args` alone — the sweep
                        # itself carrying the defect it exists to catch
                        # (CodeRabbit review).
                        for arg in (
                            *fn.args.posonlyargs,
                            *fn.args.args,
                            *fn.args.kwonlyargs,
                        )
                        if arg.arg != "self"
                        and arg.annotation is not None
                        and ast.unparse(arg.annotation) in ("str", "EntityId")
                    ]
                    if not keyish:
                        continue
                    bodies[f"{path.name}:{node.name}.{fn.name}"] = (
                        ast.unparse(fn),
                        node.name,
                    )

        # A method that delegates to a guarded sibling *is* guarded, and that
        # is the arrangement to prefer: `attribute` calls `attribute_values`,
        # so the rule is written once. Resolved to a fixpoint rather than one
        # level deep, so a longer delegation chain is not a false positive.
        guarded: set[str] = set()
        changed = True
        while changed:
            changed = False
            for name, (body, owner) in bodies.items():
                if name in guarded:
                    continue
                delegates = any(
                    f"self.{other.rsplit('.', 1)[1]}(" in body
                    for other in guarded
                    if other.split(":")[1].split(".")[0] == owner
                )
                if any(guard in body for guard in guards) or delegates:
                    guarded.add(name)
                    changed = True

        unguarded = sorted(set(bodies) - guarded)

        assert unguarded == [], (
            "these public methods take a lookup key without validating it, "
            f"so a malformed key resolves past what is stored: {unguarded}"
        )
