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

"""Occurrence-preserving identity — ADR-062 D4.

The identifiers themselves (:class:`EntityId`, :class:`OccurrenceId`, their
two vocabularies and :func:`elf_symbol_occurrence`) live in
:mod:`abicheck.storage.entity_ids` and are re-exported here, so a caller
still has one import to reach for. This module owns the collection that
indexes them and the conflicts it can report. The edge points one way: the
identifiers know nothing about the set.

Two identities, kept separate on purpose:

``EntityId``
    The *logical* thing — a declaration, symbol, type, or variable believed
    to be the same across observations and across releases.

``OccurrenceId``
    One *observation* of it: an AST declaration, a translation-unit
    occurrence, a binary symbol version, a DWARF DIE, a PDB record.

The rule this module enforces is that resolution may **group** occurrences
under an entity but may never **drop** one. Today's ``AbiSnapshot.index()``
is first-wins: a duplicate mangled function, variable, or type name is
warned about and then omitted from the lookup map, so real, valid evidence
disappears while the snapshot still validates. The same shape recurs
wherever a bare name is used as a key — ``typedefs`` (bare name, so two
classes' ``value_type`` member aliases collide), ``ElfMetadata.symbol_map``
(one ``ElfSymbol`` per bare name, though ELF legitimately carries several
versions and bindings of one name), and ``base name -> offset`` mappings
(which cannot express a repeated base subobject at all).

Where occurrences genuinely cannot be reconciled, the answer here is an
:class:`IdentityConflict` recorded alongside *both* occurrences — never a
silent choice between them. The multi-TU merge already documents several
cases that reach exactly this state and have no sound automatic answer
(MSVC decorated names that encode return type, uninstantiated template
methods with no mangled name, plain-C statics with no TU scoping); this
module gives them somewhere honest to land.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .entity_ids import (
    EntityId,
    EntityKind,
    ObservationKind,
    OccurrenceId,
    elf_symbol_occurrence,
)
from .guards import (
    identity_text as _identity_text,
    instance_of as _instance_of,
    item_iterable as _item_iterable,
    mapping as _mapping,
    required_field as _required_field,
    row_sequence as _row_sequence,
)

__all__ = [
    "EntityId",
    "EntityKind",
    "IdentityConflict",
    "OccurrenceId",
    "OccurrenceSet",
    "ObservationKind",
    "elf_symbol_occurrence",
]


@dataclass(frozen=True)
class IdentityConflict:
    """Two or more occurrences that could not be reconciled to one entity.

    Recorded *instead of* choosing a winner. ``occurrences`` holds every
    occurrence involved, so a consumer can report the ambiguity, refuse the
    comparison, or apply its own domain rule — none of which is possible once
    a first-wins index has already discarded the losers.
    """

    reason: str
    occurrences: tuple[OccurrenceId, ...]

    def __post_init__(self) -> None:
        # `reason` is validated here and not only in `from_dict`, which used
        # to coerce it with `str()` — so `IdentityConflict(reason=1, ...)`
        # was accepted, wrote `1` into the document, and read back as `"1"`:
        # an object that does not equal its own round trip (CodeRabbit
        # review). Same boundary-only-guard gap already closed on
        # `EntityId.qualified_name`, at the one site that still had it.
        object.__setattr__(self, "reason", _identity_text(self.reason, "reason"))
        # The container and every element, before anything reads `.key`. A
        # non-`OccurrenceId` in the sequence leaked `AttributeError` out of
        # the sort, which a caller separating a malformed package from a
        # broken reader classifies as the second (Codex review). A bare
        # string is a `Sequence` too, so it is refused explicitly rather than
        # iterated one character at a time — the same shape `diagnostics_from`
        # guards against.
        rows = _row_sequence(self.occurrences, "occurrences")
        for index, occurrence in enumerate(rows):
            _instance_of(occurrence, OccurrenceId, f"occurrences[{index}]")
        # Sorted so a conflict's own membership does not depend on the order
        # its occurrences were collected in, and deduplicated by key for the
        # same reason `OccurrenceSet.add` is idempotent: one observation
        # recorded twice is one observation, and listing it twice inflates
        # what a reader sees as the size of the disagreement. Lossless — the
        # key is a function of every field, so two occurrences sharing one
        # are equal.
        ordered = tuple({o.key: o for o in sorted(rows, key=lambda o: o.key)}.values())
        if len({o.key for o in ordered}) < 2:
            # A conflict needs two occurrences that actually disagree. Zero,
            # one, or the same occurrence twice were all accepted, so a reader
            # could report — or gate on — an ambiguity with no contradictory
            # pair in it (Codex review). The class docstring already said "two
            # or more"; nothing enforced it.
            #
            # Distinct *keys*, not distinct objects: two equal occurrences are
            # one observation recorded twice, which is exactly the case that
            # looks like a conflict and is not.
            raise ValueError(
                f"an identity conflict needs at least two distinct occurrences, "
                f"got {len(ordered)} ({len({o.key for o in ordered})} distinct); "
                "a conflict with nothing to disagree about would let a reader "
                "gate on an ambiguity that does not exist"
            )
        object.__setattr__(self, "occurrences", ordered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "occurrences": [o.to_dict() for o in self.occurrences],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IdentityConflict:
        _mapping(data, "an identity conflict document")
        return cls(
            # Not `str(...)`: the constructor validates it, and coercing
            # here would reintroduce exactly the round-trip asymmetry that
            # validation exists to remove.
            reason=_required_field(data, "reason", "an identity conflict document"),
            occurrences=tuple(
                OccurrenceId.from_dict(raw)
                for raw in _row_sequence(
                    _required_field(
                        data, "occurrences", "an identity conflict document"
                    ),
                    "occurrences",
                )
            ),
        )


@dataclass
class OccurrenceSet:
    """Every occurrence, grouped by entity, with nothing dropped.

    The direct replacement for a first-wins lookup map. ``add`` is total: an
    occurrence is always retained, whether or not its entity already has one
    and whether or not it conflicts with a sibling. Conflicts are *reported*
    by :meth:`conflicts` rather than resolved.

    Every ordered output sorts by occurrence key, so two runs that observed
    the same facts in a different order are indistinguishable. Producer
    observation order is deliberately *not* preserved: it is incidental, and
    letting it reach :meth:`to_dict` would make a set's semantic digest
    depend on traversal order — the exact incidental-order dependence
    ADR-062 D5 rules out. A producer for which observation order is genuinely
    meaningful must record it as an explicit attribute, where it is visible
    and comparable, rather than as implicit list position.

    (This class's first cut preserved insertion order within an entity, on
    the theory that it was a free extra record. A property test asserting
    order-independence falsified that immediately: :meth:`__iter__` fed
    :meth:`to_dict`, so the serialized form — and any digest over it —
    varied with producer traversal order.)
    """

    # `init=False`: these are implementation state, and exposing them as
    # constructor parameters let a caller install state that `add` would
    # have refused. `OccurrenceSet(_by_entity={key: ["bad"]})` constructed
    # happily and `to_dict()` then leaked `AttributeError` — the wrong error
    # kind for a malformed record, per this package's own contract.
    #
    # The sharper half is that the two mappings are one index in two parts,
    # so a caller could desynchronize them: `_by_entity` holding an
    # occurrence whose entity is absent from `_entities` made `len()` report
    # it while `entities()` could not expose it (Codex review). `__len__` is
    # documented here as "the number nothing may reduce", so that is this
    # module's own invariant failing through a door it never meant to open.
    #
    # Validating the supplied state instead would mean rebuilding it through
    # `add`, which is the same thing as not accepting it — `add` is already
    # the only way in, and it is public.
    _by_entity: dict[str, list[OccurrenceId]] = field(default_factory=dict, init=False)
    _entities: dict[str, EntityId] = field(default_factory=dict, init=False)

    def add(self, occurrence: OccurrenceId) -> None:
        """Record an occurrence. Never replaces or discards an existing one.

        An exactly-identical occurrence (same key) is idempotent — that is a
        re-observation of one thing, not a second thing — so a producer that
        walks the same DIE twice does not inflate the set.

        The record is checked here, at the mutation boundary, for the same
        reason the ledger's `declare`/`override` check theirs: a non-record
        otherwise raised `AttributeError` from `occurrence.entity`, which
        classifies a malformed input as a reader crash. `extend` inherits
        this, since it is a loop over `add`.
        """
        _instance_of(occurrence, OccurrenceId, "occurrence")
        entity_key = occurrence.entity.key
        bucket = self._by_entity.setdefault(entity_key, [])
        self._entities.setdefault(entity_key, occurrence.entity)
        if any(existing.key == occurrence.key for existing in bucket):
            return
        # Inserted in key order so the *stored state* is canonical, not merely
        # the views over it. Appending left `_by_entity`'s bucket lists in
        # producer order, which the dataclass-generated `__eq__` compares
        # element by element — so two sets holding the same occurrences of one
        # entity, added in a different order, compared unequal even though
        # `list()` and `to_dict()` agreed (CodeRabbit review). Making the state
        # canonical fixes equality at the source rather than adding a third
        # accessor that sorts.
        bisect.insort(bucket, occurrence, key=lambda o: o.key)

    def extend(self, occurrences: Iterable[OccurrenceId]) -> None:
        """Add every occurrence in an iterable.

        A `Mapping` is refused rather than iterated. Its keys really can be
        `OccurrenceId`s, so every per-item guard passes while the values are
        dropped — the same silent loss as `attributes={pair: value}`, and
        this class has exactly one invariant it cannot lose.

        Deliberately `item_iterable` and not `row_sequence`: this takes an
        `Iterable`, so a generator is a legitimate caller and that guard
        would reject one. A `set` is accepted for a reason worth stating
        rather than assuming — `add` keeps each bucket in key order, so the
        resulting state is canonical no matter what order a set iterated in.

        An earlier version of this guard refused only a `Mapping`, on the
        reasoning that a bare string was "already loud" because
        `extend("abc")` raises from the per-item check. That holds for a
        *non-empty* string and fails for `extend("")`, which iterates zero
        times and leaves the set serializing as `{"occurrences": []}`
        (Codex review). A per-item guard is never a container guard,
        because an empty container has no items.
        """
        _item_iterable(occurrences, "occurrences")
        for occurrence in occurrences:
            self.add(occurrence)

    def __len__(self) -> int:
        """Total occurrences, not entities — the number nothing may reduce."""
        return sum(len(bucket) for bucket in self._by_entity.values())

    def __iter__(self) -> Iterator[OccurrenceId]:
        for entity_key in sorted(self._by_entity):
            yield from self._by_entity[entity_key]

    def __repr__(self) -> str:
        """Render the canonical occurrence sequence, not the internal dicts.

        The generated repr printed `_by_entity`/`_entities` in dict insertion
        order, so two sets holding identical occurrences rendered differently
        depending on which entity was added first — the same leak of producer
        order that `__eq__` had, in the one place a reader looks to check
        whether two sets agree.
        """
        return f"{type(self).__name__}({list(self)!r})"

    def entities(self) -> tuple[EntityId, ...]:
        return tuple(self._entities[k] for k in sorted(self._entities))

    def occurrences_of(self, entity: EntityId) -> tuple[OccurrenceId, ...]:
        """Every retained occurrence of one entity, in canonical key order.

        The argument is checked rather than duck-typed. Passing something
        else raised a bare ``AttributeError`` from the attribute access
        below, which fails loudly enough not to be the silent-wrong-answer
        the availability ledger's read doors had — but it names an internal
        attribute rather than the argument, and this package checks a value
        where it is used rather than where it happens to break.
        """
        _instance_of(entity, EntityId, "entity")
        # Already canonical: `add` maintains each bucket in key order, so no
        # accessor needs to re-sort. A caller must never see producer
        # traversal order through any accessor, or it becomes an accidental
        # part of this class's contract.
        return tuple(self._by_entity.get(entity.key, ()))

    def is_ambiguous(self, entity: EntityId) -> bool:
        """Whether this entity has more than one distinct occurrence.

        Note this is *not* the same question as "is this a conflict" — a
        function legitimately observed in both DWARF and the export table is
        ambiguous by this predicate and not a conflict by :meth:`conflicts`.

        The argument is checked for the reason :meth:`occurrences_of` gives.
        This one is the worse of the pair: it returns ``False`` for a
        malformed entity — a plain "no, not ambiguous" — where its sibling at
        least raised. A caller gating on it would proceed as though identity
        had been checked.
        """
        _instance_of(entity, EntityId, "entity")
        return len(self._by_entity.get(entity.key, ())) > 1

    def same_site_observations(self) -> tuple[tuple[OccurrenceId, ...], ...]:
        """Groups of two or more occurrences of one entity from one site.

        A purely *structural* fact — one producer, one observation kind and
        one container reported several distinct observations of one entity —
        which this layer can always determine from identity alone. Whether
        such a group is a genuine contradiction is a different question, and
        not one identity data can answer: see :meth:`conflicts`.

        Deterministic: groups are ordered by entity key then by site, and the
        occurrences within each group are already in key order.
        """
        found: list[tuple[OccurrenceId, ...]] = []
        for entity_key in sorted(self._by_entity):
            by_site: dict[tuple[str, str, str], list[OccurrenceId]] = {}
            for occurrence in self._by_entity[entity_key]:
                by_site.setdefault(occurrence.site, []).append(occurrence)
            for _site, group in sorted(by_site.items()):
                if len(group) > 1:
                    found.append(tuple(group))
        return tuple(found)

    def conflicts(
        self,
        irreconcilable: Callable[[OccurrenceId, OccurrenceId], bool],
    ) -> tuple[IdentityConflict, ...]:
        """Same-site groups the caller's predicate judges contradictory.

        ``irreconcilable(a, b)`` is **required**, and that is a deliberate
        narrowing of what this method used to do (Codex review, third finding
        on the same rule). Earlier versions decided by themselves that every
        same-site group was a contradiction, and the site tuple grew a
        dimension each time that turned out to be wrong: first the observation
        kind and container, then the producer — because ``--ast-frontend
        hybrid`` has two producers describe one translation unit — and then a
        forward declaration followed by its definition, which is one producer
        legitimately reporting two *different declarations* of one entity in
        one file rather than two answers about one declaration.

        Three rounds of adding a dimension is the signal that the question was
        misplaced rather than under-specified. "Do these two observations
        contradict each other" requires knowing what the attributes *mean* —
        that ``is_definition`` differing is ordinary while a differing size is
        not — and this package deliberately holds no such domain knowledge
        (``AGENTS.md``: a storage module that needs to know a verdict or a
        ``ChangeKind`` is in the wrong layer). So the structural half stays
        here, in :meth:`same_site_observations`, and the semantic half is the
        caller's.

        Returning conflicts rather than raising is unchanged and intentional:
        a package must remain writable with conflicts in it, so the ambiguity
        reaches a reader instead of aborting the capture that found it.

        The predicate is evaluated over **unordered pairs**, and either
        direction answering ``True`` puts *both* endpoints in the conflict.
        Nothing in this signature promises symmetry, and an asymmetric
        predicate is easy to write by accident — ``bool(left.attribute("size"))
        and left.attribute("size") != right.attribute("size")`` is true for a
        sized observation against an unsized one and false in reverse.
        Requiring each occurrence to qualify independently then dropped that
        pair entirely: one endpoint qualified, the other did not, and a group
        of one is not a conflict, so a contradiction the caller had explicitly
        identified vanished (Codex review). A symmetric predicate is
        unaffected; only the asymmetric case changes, and it changes from
        "silently discarded" to "reported", which is the only direction this
        module may err in.
        """
        found: list[IdentityConflict] = []
        for group in self.same_site_observations():
            flagged = set()
            for index in range(len(group)):
                for position in range(index + 1, len(group)):
                    left, right = group[index], group[position]
                    if irreconcilable(left, right) or irreconcilable(right, left):
                        flagged.add(index)
                        flagged.add(position)
            members = [
                occurrence for index, occurrence in enumerate(group) if index in flagged
            ]
            if len(members) < 2:
                continue
            first = members[0]
            where = f" in {first.container}" if first.container else ""
            by_whom = f" from {first.producer}" if first.producer else ""
            found.append(
                IdentityConflict(
                    reason=(
                        f"{len(members)} irreconcilable "
                        f"{first.observation.value} observations"
                        f"{by_whom}{where} for {first.entity.qualified_name}"
                    ),
                    occurrences=tuple(members),
                )
            )
        return tuple(found)

    def to_dict(self) -> dict[str, Any]:
        return {"occurrences": [o.to_dict() for o in self]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OccurrenceSet:
        # The outer container is checked before `.get` reaches it: a scalar or
        # a list otherwise raised `AttributeError`, which a caller separating
        # a malformed package from a broken reader classifies as the second
        # (Codex review). Same rule the availability documents already apply.
        _mapping(data, "an occurrence set document")
        result = cls()
        # Required, not defaulted: `to_dict` writes this key unconditionally,
        # so an absent one means the document did not come from this writer —
        # and defaulting it turned a truncated set into a valid empty one,
        # indistinguishable from the writer's explicit claim that the producer
        # found no observations (`AGENTS.md` invariant 3, Codex review).
        result.extend(
            OccurrenceId.from_dict(raw)
            for raw in _row_sequence(
                _required_field(data, "occurrences", "an occurrence set document"),
                "occurrences",
            )
        )
        return result


def group_by_entity(
    occurrences: Sequence[OccurrenceId],
) -> dict[EntityId, tuple[OccurrenceId, ...]]:
    """Group occurrences by entity, preserving every one.

    Takes a `Sequence`, so `row_sequence` is the right guard here where
    `OccurrenceSet.extend` needs the weaker `item_iterable` — the contract
    differs, not the caution. This had the same empty-scalar gap `extend`
    did (`group_by_entity("")` returned `{}`) and was not reported; it came
    out of re-checking the claim that finding falsified.

    A convenience over :class:`OccurrenceSet` for callers that already hold a
    complete sequence. Deliberately returns tuples rather than single values
    so that no call site can be written as if grouping produced a winner.
    """
    result = OccurrenceSet()
    result.extend(_row_sequence(occurrences, "occurrences"))
    return {entity: result.occurrences_of(entity) for entity in result.entities()}
