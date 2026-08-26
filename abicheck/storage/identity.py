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
import enum
import functools
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "EntityId",
    "EntityKind",
    "IdentityConflict",
    "OccurrenceId",
    "OccurrenceSet",
    "ObservationKind",
    "elf_symbol_occurrence",
]


def _packed(*parts: str) -> str:
    """Join identity parts into a key no part's content can forge.

    Each part is length-prefixed (``"<len>:<part>"``) rather than joined by a
    separator, so a key decomposes into exactly one part sequence regardless
    of what any part contains — including a nested key produced by this same
    function, which the outer length prefix delimits exactly.

    A separator was tried first and was wrong. The reasoning behind it went:
    every *printable* separator is legal inside some real spelling (a mangled
    name contains ``@``, a qualified name ``::``, a path ``/``), so use a
    control character (U+001F) that no real C++ or ELF spelling can contain.
    That argument holds for the parts derived from real spellings and fails
    for the rest: ``OccurrenceId.attributes`` carries arbitrary
    producer-supplied strings, so a value containing the separator could
    forge a part boundary. Codex review found the concrete counterexample —
    ``(("a", "x\\x1fb=y"),)`` and ``(("a", "x"), ("b", "y"))`` are unequal
    occurrences that encoded to the same key, so ``OccurrenceSet.add`` saw
    the second as a duplicate and **silently discarded it**. That is the one
    thing this module exists to make impossible, reached through the key
    function rather than through the set logic.

    Length-prefixing removes the question rather than narrowing it: there is
    no byte a part can contain that changes how the key parses.
    """
    return "".join(f"{len(part)}:{part}" for part in parts)


class EntityKind(enum.Enum):
    """What kind of logical thing an :class:`EntityId` names."""

    FUNCTION = "function"
    VARIABLE = "variable"
    TYPE = "type"
    ENUM = "enum"
    TYPEDEF = "typedef"
    CONSTANT = "constant"
    SYMBOL = "symbol"
    FIELD = "field"
    BASE = "base"


class ObservationKind(enum.Enum):
    """Where an :class:`OccurrenceId` was observed.

    This is the axis that makes multiplicity legible: the same logical
    function observed by Clang, by CastXML, in DWARF, and in the export
    table is four occurrences of one entity, not four competing answers.
    """

    AST = "ast"
    DWARF = "dwarf"
    PDB = "pdb"
    EXPORT_TABLE = "export_table"
    TRANSLATION_UNIT = "translation_unit"
    SOURCE_LOCATION = "source_location"
    BUILD_UNIT = "build_unit"


def _identity_text(value: Any, field_name: str) -> str:
    """An identity-bearing field, rejected rather than coerced if not a string.

    ``str()`` looks harmless on a field that is a string in every well-formed
    document, and it is not: ``1`` and ``"1"`` are two distinct values in a
    JSON document that both become ``"1"`` here, so two genuinely different
    occurrences produce one key and :meth:`OccurrenceSet.add` drops the second
    as an exact duplicate (Codex review). That is this module's one invariant
    — never discard an observation — defeated by a type coercion at the
    document boundary rather than by any logic in the set.

    Rejecting matches what the neighbouring primitives already do with
    malformed identity-bearing input: ``canonical_form`` refuses a non-string
    mapping key, and ``FactAvailability.from_dict`` raises on an unknown
    status rather than downgrading it. The informational version axes parse
    defensively instead, and the distinction is deliberate — no decision reads
    those, whereas everything here keys on these.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} must be a string, not {type(value).__name__} "
            f"({value!r}); identity-bearing fields are never coerced, because "
            "two distinct values sharing one string form would silently "
            "collapse into one occurrence"
        )
    return value


def _attribute_pair(pair: Any) -> tuple[str, str]:
    """One ``(name, value)`` attribute row from a document.

    The length is checked as well as the types: indexing ``pair[0]``/
    ``pair[1]`` accepts a row of any length and silently ignores whatever
    follows, which is the same "malformation read as a valid answer" shape as
    the coercion above.
    """
    if isinstance(pair, (str, bytes)) or not isinstance(pair, Sequence):
        raise TypeError(f"attribute row must be a two-element sequence, got {pair!r}")
    if len(pair) != 2:
        raise ValueError(
            f"attribute row must have exactly two elements, got {len(pair)}: {pair!r}"
        )
    return (
        _identity_text(pair[0], "attribute name"),
        _identity_text(pair[1], "attribute value"),
    )


@functools.total_ordering
@dataclass(frozen=True)
class EntityId:
    """A logical entity's identity.

    ``qualified_name`` is the most specific spelling available, and
    ``discriminator`` carries whatever additionally separates two entities
    that share it — a mangled name, a template argument list, an overload
    signature. Both are plain strings so that a producer that can only supply
    a weaker identity can still name an entity, rather than being forced to
    fabricate a stronger one; that a weak identity is weak is then visible in
    the key instead of hidden behind an invented uniqueness.
    """

    kind: EntityKind
    qualified_name: str
    discriminator: str = ""

    @property
    def key(self) -> str:
        """Flat, collision-free string key. Stable across runs and releases."""
        return _packed(self.kind.value, self.qualified_name, self.discriminator)

    def __lt__(self, other: object) -> bool:
        """Order by :attr:`key`, which is total and stable.

        The dataclass-generated ordering (``order=True``) was wrong and
        advertised as working (Codex review): it compares field by field, so
        it reaches ``kind`` — a plain ``enum.Enum``, which does not implement
        ``<`` — and ``sorted()`` raised ``TypeError`` for any two entities of
        different kinds. Comparing keys instead is total over every pair, and
        agrees with the order every accessor in this module already uses.
        """
        if not isinstance(other, EntityId):
            return NotImplemented
        return self.key < other.key

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind.value,
            "qualified_name": self.qualified_name,
        }
        if self.discriminator:
            out["discriminator"] = self.discriminator
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EntityId:
        return cls(
            kind=EntityKind(data["kind"]),
            qualified_name=_identity_text(data["qualified_name"], "qualified_name"),
            discriminator=_identity_text(
                data.get("discriminator", ""), "discriminator"
            ),
        )


@functools.total_ordering
@dataclass(frozen=True)
class OccurrenceId:
    """One observation of an entity.

    ``attributes`` carries the observation-specific facts that make two
    occurrences of one entity genuinely distinct — for an ELF symbol, its
    version, binding, and default-ness; for a TU occurrence, the TU path. It
    participates in the key, so two occurrences differing only in an
    attribute remain two occurrences rather than collapsing into one.
    """

    entity: EntityId
    observation: ObservationKind
    #: Which artifact/translation unit/object this was observed in. Empty when
    #: the observation is not scoped to one (e.g. a whole-project graph node).
    container: str = ""
    attributes: tuple[tuple[str, str], ...] = ()
    #: Which tool produced this observation (``"clang"``, ``"castxml"``, ...).
    #: A first-class part of the observation *site*, not one attribute among
    #: many, because two producers answering about one entity in one
    #: translation unit is an ordinary configuration — this codebase ships
    #: ``--ast-frontend hybrid`` and records per-fact ``fact_provenance``
    #: precisely for it. Keyword-only so that adding it cannot change what any
    #: existing positional call means, following the same per-field
    #: ``kw_only`` precedent as ``Change`` and ``AbiSnapshot``.
    producer: str = field(default="", kw_only=True)

    def __post_init__(self) -> None:
        # Sort attributes so two occurrences built with the same facts in a
        # different order are the *same* occurrence. Without this, whether a
        # duplicate is detected would depend on producer traversal order,
        # which is exactly the incidental-order dependence ADR-062 D5 rules
        # out for digests and D4 rules out for identity.
        object.__setattr__(
            self,
            "attributes",
            tuple(
                sorted(
                    (
                        _identity_text(k, "attribute name"),
                        _identity_text(v, "attribute value"),
                    )
                    for k, v in self.attributes
                )
            ),
        )

    @property
    def key(self) -> str:
        # Each attribute contributes its key and value as two separate parts.
        # Flattening them into the same length-prefixed sequence is safe for
        # the same reason the nested entity key is: every part is delimited by
        # its own prefix, so no attribute content can forge a boundary.
        flat_attributes = [item for pair in self.attributes for item in pair]
        return _packed(
            self.entity.key,
            self.observation.value,
            self.container,
            self.producer,
            *flat_attributes,
        )

    @property
    def site(self) -> tuple[str, str, str]:
        """Where this was observed: observation kind, container, producer.

        The unit :meth:`OccurrenceSet.conflicts` groups by. Producer belongs
        here rather than in ``attributes`` because two *different* producers
        describing one entity are two independent answers, not one producer
        contradicting itself.
        """
        return (self.observation.value, self.container, self.producer)

    def __lt__(self, other: object) -> bool:
        """Order by :attr:`key`. See :meth:`EntityId.__lt__` for why."""
        if not isinstance(other, OccurrenceId):
            return NotImplemented
        return self.key < other.key

    def attribute_values(self, name: str) -> tuple[str, ...]:
        """Every value recorded under ``name``, in canonical order.

        A repeated attribute name is legal and deliberately preserved: the
        serialized form is a list of pairs rather than a mapping precisely so
        that a producer recording two values does not silently lose one. This
        is the accessor that shows both.
        """
        return tuple(value for key, value in self.attributes if key == name)

    def attribute(self, name: str, default: str = "") -> str:
        """The single value recorded under ``name``.

        Raises when the name is recorded more than once, rather than returning
        one of them. The previous implementation returned the first match, and
        since ``__post_init__`` sorts the pairs, "first" meant *lexicographically
        smallest value* — so an occurrence recording ``size=8`` and ``size=16``
        answered ``"16"`` and discarded the other, with nothing to indicate a
        choice had been made (Codex review).

        That is this module's own defect in miniature: it exists because a
        first-wins index discarded losers, and an accessor doing the same
        thing one layer down is no better for being smaller. A caller that
        genuinely expects several values has :meth:`attribute_values`; one
        that expects a single value gets told when its expectation is wrong,
        which is the only outcome that cannot silently corrupt a
        caller-supplied :meth:`OccurrenceSet.conflicts` predicate.

        Note the review's second claim does not hold, and it is worth being
        precise about which half was real: two occurrences differing in a
        repeated attribute do **not** compare equal, because every pair
        contributes to :attr:`key`. Verified directly. The defect was confined
        to this accessor.
        """
        values = self.attribute_values(name)
        if len(values) > 1:
            raise ValueError(
                f"attribute {name!r} is recorded {len(values)} times "
                f"({values!r}); use attribute_values() — returning one of them "
                "would discard an observation this format deliberately kept"
            )
        return values[0] if values else default

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "entity": self.entity.to_dict(),
            "observation": self.observation.value,
        }
        if self.container:
            out["container"] = self.container
        if self.producer:
            out["producer"] = self.producer
        if self.attributes:
            # A list of pairs, not a mapping: ADR-062 D5 reserves maps for
            # keys that are unique and order-free, and round-tripping through
            # a JSON object would silently drop a repeated attribute key.
            out["attributes"] = [[k, v] for k, v in self.attributes]
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OccurrenceId:
        return cls(
            entity=EntityId.from_dict(data["entity"]),
            observation=ObservationKind(data["observation"]),
            container=_identity_text(data.get("container", ""), "container"),
            producer=_identity_text(data.get("producer", ""), "producer"),
            attributes=tuple(
                _attribute_pair(pair) for pair in data.get("attributes", ())
            ),
        )


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
        object.__setattr__(
            self, "occurrences", tuple(sorted(self.occurrences, key=lambda o: o.key))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "occurrences": [o.to_dict() for o in self.occurrences],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IdentityConflict:
        return cls(
            reason=str(data["reason"]),
            occurrences=tuple(
                OccurrenceId.from_dict(raw) for raw in data.get("occurrences", ())
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

    _by_entity: dict[str, list[OccurrenceId]] = field(default_factory=dict)
    _entities: dict[str, EntityId] = field(default_factory=dict)

    def add(self, occurrence: OccurrenceId) -> None:
        """Record an occurrence. Never replaces or discards an existing one.

        An exactly-identical occurrence (same key) is idempotent — that is a
        re-observation of one thing, not a second thing — so a producer that
        walks the same DIE twice does not inflate the set.
        """
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
        """
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
        """
        found: list[IdentityConflict] = []
        for group in self.same_site_observations():
            members = [
                occurrence
                for index, occurrence in enumerate(group)
                if any(
                    irreconcilable(occurrence, other)
                    for position, other in enumerate(group)
                    if position != index
                )
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
        result = cls()
        result.extend(
            OccurrenceId.from_dict(raw) for raw in data.get("occurrences", ())
        )
        return result


def elf_symbol_occurrence(
    *,
    artifact_id: str,
    name: str,
    version: str = "",
    default_version: bool = False,
    binding: str = "",
    symbol_type: str = "",
    visibility: str = "",
    defined: bool = True,
) -> OccurrenceId:
    """Build an ELF symbol occurrence keyed by everything that separates one.

    ``ElfMetadata.symbol_map`` maps a *bare* name to exactly one
    ``ElfSymbol``, last-entry-wins, because neither abicheck's ELF parser nor
    pyelftools puts the ``@``/``@@`` version suffix into ``ElfSymbol.name``.
    A library exporting ``foo@GLIBC_2.2`` (``GLOBAL``) alongside
    ``foo@@GLIBC_2.14`` (``WEAK``) therefore collapses to whichever parsed
    last, and any consumer reading a binding or visibility off that map gets
    a coin flip — which matters, because a ``binding: weak`` suppression rule
    can then match a removal that is a real break from the surviving
    version's point of view.

    Version and default-ness are in the key for the same reason binding is:
    ``foo@GLIBC_2.2`` and ``foo@@GLIBC_2.14`` are two distinct exports with
    independent lifetimes, and a format that cannot say so cannot report the
    removal of one of them.
    """
    return OccurrenceId(
        entity=EntityId(
            kind=EntityKind.SYMBOL,
            qualified_name=name,
            # The version belongs to the *entity*, not only the observation:
            # two versioned definitions are two exports a consumer can bind to
            # separately, not two views of one export.
            discriminator=version,
        ),
        observation=ObservationKind.EXPORT_TABLE,
        container=artifact_id,
        attributes=(
            ("binding", binding),
            ("default_version", "1" if default_version else "0"),
            ("defined", "1" if defined else "0"),
            ("symbol_type", symbol_type),
            ("visibility", visibility),
        ),
    )


def group_by_entity(
    occurrences: Sequence[OccurrenceId],
) -> dict[EntityId, tuple[OccurrenceId, ...]]:
    """Group occurrences by entity, preserving every one.

    A convenience over :class:`OccurrenceSet` for callers that already hold a
    complete sequence. Deliberately returns tuples rather than single values
    so that no call site can be written as if grouping produced a winner.
    """
    result = OccurrenceSet()
    result.extend(occurrences)
    return {entity: result.occurrences_of(entity) for entity in result.entities()}
