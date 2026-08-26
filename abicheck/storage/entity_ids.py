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

"""Entity and occurrence identifiers — the leaf half of ADR-062 D4.

Split out of :mod:`abicheck.storage.identity` when that module crossed the
800-line production cap. The split follows the dependency direction rather
than a line count: the identifiers know nothing about the collection that
indexes them, while the collection cannot be expressed without them. Putting
the leaf here and re-exporting from :mod:`~abicheck.storage.identity` keeps
one import for a caller and keeps the edge pointing one way — the reverse
arrangement would have made the two modules a cycle.

The key encoding lives here too, since it is what makes an identifier an
identifier: :func:`_packed` length-prefixes every part, so no content a part
can carry changes how a key parses.
"""

from __future__ import annotations

import enum
import functools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .guards import (
    decision_key as _decision_key,
    enum_member as _enum_member,
    identity_text as _identity_text,
    instance_of as _instance_of,
    required_field as _required_field,
    row_sequence as _row_sequence,
)

__all__ = [
    "EntityId",
    "EntityKind",
    "ObservationKind",
    "OccurrenceId",
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

    def __post_init__(self) -> None:
        """Validate identity text here, not only in :meth:`from_dict`.

        A directly-constructed ``EntityId(EntityKind.TYPE, 1)`` was accepted,
        emitted the integer from ``to_dict``, raised from ``_packed`` on
        :attr:`key`, and was rejected by ``from_dict`` — so the object could
        not survive its own round trip, and ``OccurrenceSet.add`` failed
        before it could preserve the observation (Codex review). Same
        boundary-only-guard gap as the sibling fields on
        :class:`OccurrenceId`, which had been fixed one commit earlier while
        this one was missed.
        """
        object.__setattr__(self, "kind", _enum_member(self.kind, EntityKind, "kind"))
        object.__setattr__(
            self,
            "qualified_name",
            _identity_text(self.qualified_name, "qualified_name"),
        )
        object.__setattr__(
            self, "discriminator", _identity_text(self.discriminator, "discriminator")
        )

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
            kind=EntityKind(_required_field(data, "kind", "an entity document")),
            qualified_name=_identity_text(
                _required_field(data, "qualified_name", "an entity document"),
                "qualified_name",
            ),
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
        # `_attribute_pair`, not a bare `for k, v in ...` unpack: a
        # two-character scalar row such as `("ab",)` unpacked as the valid
        # pair `("a", "b")`, so it produced the same key as an occurrence that
        # really held that pair and `OccurrenceSet.add` dropped one of them as
        # a duplicate (Codex review). The document path already validated rows
        # this way; the constructor did not, which is the same
        # boundary-only-guard gap as the provenance and diagnostics fields.
        object.__setattr__(
            self, "entity", _instance_of(self.entity, EntityId, "entity")
        )
        object.__setattr__(
            self,
            "observation",
            _enum_member(self.observation, ObservationKind, "observation"),
        )
        object.__setattr__(
            self,
            "attributes",
            tuple(sorted(_attribute_pair(row) for row in self.attributes)),
        )
        object.__setattr__(
            self, "container", _identity_text(self.container, "container")
        )
        object.__setattr__(self, "producer", _identity_text(self.producer, "producer"))

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

        The name is validated, not merely compared. An unnormalized name
        misses every stored pair and answers "no such attribute", which a
        conflict predicate or resolver reads as *captured evidence absent* —
        so a real contradiction goes unreported (Codex review). Same shape as
        the ledger's read doors: a key that cannot match resolves past what
        is stored rather than failing.
        """
        _decision_key(name, "name")
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
            entity=EntityId.from_dict(
                _required_field(data, "entity", "an occurrence document")
            ),
            observation=ObservationKind(
                _required_field(data, "observation", "an occurrence document")
            ),
            container=_identity_text(data.get("container", ""), "container"),
            producer=_identity_text(data.get("producer", ""), "producer"),
            attributes=tuple(
                _attribute_pair(pair)
                for pair in _row_sequence(data.get("attributes", ()), "attributes")
            ),
        )


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
    # The two flags are validated rather than read for truthiness, because
    # truthiness is what makes them lossy: an adapter passing a parsed
    # `"false"` encoded it as `"1"`, so the occurrence claimed the symbol was
    # defined and default, took the same key as one built with `True`, and
    # `OccurrenceSet.add` then discarded it as a duplicate (Codex review) —
    # this module's one invariant, defeated by a coercion two lines from the
    # key that enforces it.
    #
    # `isinstance(x, bool)` also rejects `1`/`0`, which is deliberate: the
    # attribute is a flag, and a caller with an int has a parsed value it has
    # not finished parsing. Every string parameter is already checked by
    # `EntityId`/`OccurrenceId` themselves.
    _instance_of(default_version, bool, "default_version")
    _instance_of(defined, bool, "defined")
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
