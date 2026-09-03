# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Whether a ledger's stored state depends on the order it was built in.

Split out of `test_availability.py` when that file crossed the 1200-line
test cap. The subject is narrow enough to stand alone and general enough to
be worth finding: this is the third place on this branch where a canonical
*view* sat over non-canonical *state*, so the module also sweeps the
package's other stateful containers rather than pinning only the ledger.
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.storage.availability import AvailabilityLedger, FactAvailability
from abicheck.storage.availability_status import FactStatus


class TestLedgerStateIsCanonicalNotJustItsViews:
    """Insertion order must not survive into the stored state.

    Two producers declaring identical records in different orders built
    ledgers that compared equal and serialized identically while their
    `repr`s differed, so a diagnostic depended on collection order (Codex
    review).

    This is the **third** time this branch has broken the same invariant —
    `OccurrenceSet` kept insertion order behind a sorted `__iter__`, and
    `StorageVersions` normalized only in `to_dict`. `__eq__` agreed here
    only because dict equality ignores key order; `repr` had nothing to
    hide behind. The rule for a value type is: canonicalize the state,
    never the view.
    """

    @staticmethod
    def _build(order: list[tuple[str, FactAvailability]]) -> AvailabilityLedger:
        ledger = AvailabilityLedger()
        for family, availability in order:
            ledger.declare(family, availability)
        for family, availability in order:
            ledger.override(family, "e1", availability)
        return ledger

    def test_declaration_order_does_not_reach_the_repr(self) -> None:
        present = FactAvailability(status=FactStatus.PRESENT)
        failed = FactAvailability(status=FactStatus.FAILED)

        forward = self._build([("layout", present), ("graph", failed)])
        reverse = self._build([("graph", failed), ("layout", present)])

        # The premise first: if these ever stop agreeing, the assertion
        # below is measuring something else and should fail loudly.
        assert forward == reverse
        assert forward.to_dict() == reverse.to_dict()

        assert repr(forward) == repr(reverse)
        assert list(forward.families) == list(reverse.families)
        assert list(forward.overrides) == list(reverse.overrides)

    @given(st.permutations(["layout", "graph", "types", "exports"]))
    def test_every_permutation_produces_one_state(self, names: list[str]) -> None:
        """A property, not two hand-picked orders.

        The previous two instances of this defect were each fixed for the
        one ordering that had been demonstrated; a permutation property is
        what makes "order-independent" a claim about the contract rather
        than about the example.
        """
        present = FactAvailability(status=FactStatus.PRESENT)
        reference = self._build([(name, present) for name in sorted(names)])
        candidate = self._build([(name, present) for name in names])

        assert candidate == reference
        assert repr(candidate) == repr(reference)
        assert candidate.to_dict() == reference.to_dict()

    def test_mutating_after_construction_stays_canonical(self) -> None:
        """`declare` reassigns rather than writing in place.

        An in-place write appends at the end and leaves the state in
        collection order — the sort in `__setattr__` would never run, which
        is exactly how the first version of this fix failed.
        """
        present = FactAvailability(status=FactStatus.PRESENT)
        ledger = AvailabilityLedger()
        for name in ("zebra", "alpha", "middle"):
            ledger.declare(name, present)

        assert list(ledger.families) == ["alpha", "middle", "zebra"]

    def test_the_other_containers_were_already_canonical(self) -> None:
        """Why this is the last one, rather than the next one found.

        Swept the package's other stateful containers rather than fixing
        only what was reported: `OccurrenceSet`, `IdentityConflict` and
        `StorageVersions.section_schema_versions` each already produce one
        state per permutation, so the ledger was the remaining member.
        """
        import itertools

        from abicheck.storage.entity_ids import (
            EntityId,
            EntityKind,
            ObservationKind,
            OccurrenceId,
        )
        from abicheck.storage.identity import IdentityConflict, OccurrenceSet
        from abicheck.storage.versioning import StorageVersions

        first = EntityId(kind=EntityKind.FUNCTION, qualified_name="a")
        second = EntityId(kind=EntityKind.FUNCTION, qualified_name="b")
        occurrences = [
            OccurrenceId(entity=first, observation=ObservationKind.AST),
            OccurrenceId(entity=second, observation=ObservationKind.DWARF),
            OccurrenceId(entity=first, observation=ObservationKind.DWARF),
        ]

        def occurrence_set(order: tuple[OccurrenceId, ...]) -> OccurrenceSet:
            built = OccurrenceSet()
            for occurrence in order:
                built.add(occurrence)
            return built

        for build in (
            occurrence_set,
            lambda order: IdentityConflict(reason="r", occurrences=tuple(order)),
        ):
            assert (
                len({repr(build(p)) for p in itertools.permutations(occurrences)}) == 1
            )

        sections = [("a", 1), ("b", 2), ("c", 3)]
        assert (
            len(
                {
                    repr(StorageVersions(section_schema_versions=dict(p)))
                    for p in itertools.permutations(sections)
                }
            )
            == 1
        )
