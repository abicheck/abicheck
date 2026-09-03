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

**``EntityKind``/``ObservationKind`` are relocated, not redefined, as of
ADR-063 Phase 2.** They are domain vocabulary (what kind of thing an
identity names), not a storage wire concern, so they now live in
:mod:`abicheck.model.identity` — the leaf module ADR-061's ``storage ->
model`` import direction requires them to live in, and re-exported here
under their original names so every existing ``storage.entity_ids.
EntityKind``/``storage.entity_ids.ObservationKind`` import keeps resolving
unchanged. ``model.identity`` also defines a second, independent
``EntityId``/``ScopePath``-based domain identity type of its own.

**The two ``EntityId``s are bridged, not merged, as of Phase 2's storage v2
wire-schema slice.** ``EntityId``/``OccurrenceId`` below remain the packed-
key wire DTO this module has always been (``kind``/``qualified_name``/
``discriminator``, unchanged) — that shape stays the one thing a caller
that only needs a flat, orderable, key-producing identity reaches for.
:func:`domain_entity_id_to_dto`/:func:`domain_entity_id_from_dto` are a
*separate* bridge pair operating on ``model.identity.EntityId`` directly,
producing its own wire-schema-versioned JSON document (schema version 2)
that encodes ``ScopePath`` as an explicit list of typed segment records —
see their own docstrings for why a rendered ``qualified_name`` string
cannot be the wire shape for that type (two distinct ``ScopePath``s can
render identically) and for the version-1-document migration adapter.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..model.identity import (
    Anonymous,
    EntityId as _DomainEntityId,
    EntityKind,
    InlineNamespace,
    LocalToFunction,
    Namespace,
    ObservationKind,
    Record,
    ScopePath as _DomainScopePath,
    ScopeSegment as _DomainScopeSegment,
)
from .guards import (
    decision_key as _decision_key,
    enum_member as _enum_member,
    identity_text as _identity_text,
    instance_of as _instance_of,
    mapping as _mapping,
    required_field as _required_field,
    row_sequence as _row_sequence,
    strict_int as _strict_int,
)

__all__ = [
    "DOMAIN_ENTITY_ID_SCHEMA_VERSION",
    "EntityId",
    "EntityKind",
    "ObservationKind",
    "OccurrenceId",
    "domain_entity_id_from_dto",
    "domain_entity_id_to_dto",
    "elf_symbol_occurrence",
]

#: Wire-schema version for :func:`domain_entity_id_to_dto`'s own document
#: shape. Version 1 is the pre-existing bare ``kind``/``qualified_name``/
#: ``discriminator`` shape (what this module's own :class:`EntityId`
#: produces) — never written by :func:`domain_entity_id_to_dto` itself, but
#: still readable by :func:`domain_entity_id_from_dto` via
#: :func:`_domain_entity_id_from_v1_dto`, D8's "a migration adapter per DTO
#: version."
DOMAIN_ENTITY_ID_SCHEMA_VERSION = 2


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
        # The container is checked before any field is read. `required_field`
        # alone is not that check: it reaches a key by subscript, so an object
        # supplying `__getitem__` without `.get` cleared it and then leaked
        # `AttributeError` from the optional field below — which a caller
        # separating a malformed package from a broken reader classifies as
        # the second (Codex review). Every sibling `from_dict` in this package
        # already validated its container first; these two were the gap, and
        # `test_every_from_dict_validates_its_container_first` now pins the
        # rule rather than leaving it to hold by habit.
        _mapping(data, "an entity document")
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
        # The *container*, before its rows — the same boundary-only-guard gap
        # the comment above records for row shape, repeated one level out. A
        # mapping iterates its keys, so `attributes={("size", "8"): "x"}`
        # became that attribute with the value dropped, and since attributes
        # are part of `key`, `OccurrenceSet.add` then deduplicated under an
        # identity no adapter supplied (Codex review). `from_dict` grew this
        # check first and the constructor did not, which is two doors into
        # one field disagreeing about what a valid container is.
        object.__setattr__(
            self,
            "attributes",
            tuple(
                sorted(
                    _attribute_pair(row)
                    for row in _row_sequence(self.attributes, "attributes")
                )
            ),
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
        # Same rule, same reason as `EntityId.from_dict` above.
        _mapping(data, "an occurrence document")
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
                # Kept alongside `__post_init__`'s check rather than
                # delegated to it: this comprehension *materializes* a
                # mapping's keys into a tuple, so by the time the
                # constructor sees the value it is a perfectly valid
                # sequence. Removing this one and relying on the assignment
                # door reopened the parse path — verified, not assumed.
                _attribute_pair(pair)
                for pair in _row_sequence(data.get("attributes", ()), "attributes")
            ),
        )


# --------------------------------------------------------------------------
# model.identity.EntityId <-> wire-schema-v2 document bridge
#
# See this module's own docstring for why a rendered `qualified_name` string
# (this module's pre-existing v1 EntityId shape, above) cannot be the wire
# form for the `ScopePath`-based domain EntityId: two distinct `ScopePath`s
# can render to the identical string, so `from_dict` reconstructing a domain
# `EntityId` from one could not recover which one it was. `to_dto`/`from_dto`
# below encode `ScopePath` as an explicit list of typed segment records
# instead, one entry per segment, so no information `ScopePath` itself
# carries is lost in the round trip.
# --------------------------------------------------------------------------


def _domain_segment_to_dict(segment: _DomainScopeSegment) -> dict[str, Any]:
    """One ``ScopePath`` segment, encoded with its own kind tag.

    A discriminated union of five segment types has no single "the" shape —
    tagging each with its own ``"kind"`` string (distinct from
    :class:`EntityKind`'s vocabulary; a scope segment and the entity it
    contains are never the same kind of thing) is what lets
    :func:`_domain_segment_from_dict` dispatch back to the right dataclass
    rather than guessing from which optional keys happen to be present.
    """
    if isinstance(segment, Namespace):
        return {"kind": "namespace", "name": segment.name}
    if isinstance(segment, Record):
        return {"kind": "record", "name": segment.name, "access": segment.access}
    if isinstance(segment, InlineNamespace):
        return {
            "kind": "inline_namespace",
            "name": segment.name,
            "version_tag": segment.version_tag,
        }
    if isinstance(segment, Anonymous):
        # `segment.kind` is that segment's OWN payload field (what kind of
        # anonymous scope it is — "struct"/"union"/"namespace"/"enum"), not
        # this dict's own discriminator tag — spelled `scope_kind` here so
        # the two `"kind"`-shaped things sharing one document never collide.
        return {
            "kind": "anonymous",
            "scope_kind": segment.kind,
            "ordinal": segment.ordinal,
        }
    if isinstance(segment, LocalToFunction):
        return {
            "kind": "local_to_function",
            # Recursive, not a bare string: `owner` is itself a full domain
            # `EntityId` (this module's own docstring on `LocalToFunction`
            # explains why a bare name would collide two overloads' same-named
            # locals), so it needs the identical typed round trip this
            # function's own caller is providing for the outer entity.
            "owner": domain_entity_id_to_dto(segment.owner),
            "block_ordinal": segment.block_ordinal,
        }
    raise TypeError(f"unrecognized ScopePath segment type: {type(segment).__name__}")


def _ordinal_field(document: Mapping[str, Any], key: str, record: str) -> int:
    """A required, strictly-integer ordinal field — never a ``bool``.

    Both :class:`Anonymous`/:class:`LocalToFunction`'s own ordinal fields
    are identity, so two structurally distinct wire values (``true`` and
    the integer ``1``) must not reconstruct to equal, same-hash segments —
    see :func:`~abicheck.storage.guards.strict_int` for why plain
    ``_instance_of(value, int, ...)`` is not enough.
    """
    return _strict_int(_required_field(document, key, record), key)


def _domain_segment_from_dict(data: Any) -> _DomainScopeSegment:
    """The inverse of :func:`_domain_segment_to_dict`.

    Every field the writer (:func:`_domain_segment_to_dict`) always emits is
    read via :func:`_required_field`, never ``.get(key, default)`` — a v2
    document is written with ``access``/``version_tag`` present on every
    record/inline-namespace segment regardless of value, so a document
    missing one is truncated or hand-edited, not a producer that legitimately
    had nothing to say (Codex review; the same "absence is not evidence"
    rule :func:`~abicheck.storage.guards.row_sequence` already states for a
    sibling field shape).
    """
    _mapping(data, "a scope-segment document")
    segment_kind = _required_field(data, "kind", "a scope-segment document")
    if segment_kind == "namespace":
        return Namespace(
            name=_identity_text(
                _required_field(data, "name", "a namespace segment document"),
                "name",
            )
        )
    if segment_kind == "record":
        return Record(
            name=_identity_text(
                _required_field(data, "name", "a record segment document"), "name"
            ),
            access=_identity_text(
                _required_field(data, "access", "a record segment document"), "access"
            ),
        )
    if segment_kind == "inline_namespace":
        return InlineNamespace(
            name=_identity_text(
                _required_field(data, "name", "an inline-namespace segment document"),
                "name",
            ),
            version_tag=_identity_text(
                _required_field(
                    data, "version_tag", "an inline-namespace segment document"
                ),
                "version_tag",
            ),
        )
    if segment_kind == "anonymous":
        return Anonymous(
            kind=_identity_text(
                _required_field(data, "scope_kind", "an anonymous segment document"),
                "scope_kind",
            ),
            ordinal=_ordinal_field(data, "ordinal", "an anonymous segment document"),
        )
    if segment_kind == "local_to_function":
        return LocalToFunction(
            owner=domain_entity_id_from_dto(
                _required_field(data, "owner", "a local-to-function segment document")
            ),
            block_ordinal=_ordinal_field(
                data, "block_ordinal", "a local-to-function segment document"
            ),
        )
    raise ValueError(
        f"unrecognized scope-segment kind {segment_kind!r} in a scope-segment document"
    )


def domain_entity_id_to_dto(entity_id: _DomainEntityId) -> dict[str, Any]:
    """``model.identity.EntityId`` -> a wire-schema-v2 JSON document.

    Every field ``model.identity.EntityId`` carries is represented as its
    own typed entry — ``scope`` as a list of :func:`_domain_segment_to_dict`
    records, ``leaf_name``/``extra`` kept as their own fields rather than
    folded into one ``discriminator`` string — so
    :func:`domain_entity_id_from_dto` can reconstruct the identical domain
    object, not merely some string that happens to describe it.
    """
    return {
        "schema_version": DOMAIN_ENTITY_ID_SCHEMA_VERSION,
        "scope": [_domain_segment_to_dict(segment) for segment in entity_id.scope],
        "kind": entity_id.kind.value,
        "leaf_name": entity_id.leaf_name,
        "extra": list(entity_id.extra),
    }


def _domain_entity_id_from_v1_dto(data: Mapping[str, Any]) -> _DomainEntityId:
    """Best-effort reconstruction of a version-1 (``kind``/``qualified_name``/
    ``discriminator``) document as a domain ``EntityId``.

    Version 1 never recorded which kind a scope segment was — it only ever
    stored one flat, already-rendered ``qualified_name`` string — so this is
    necessarily lossy: every ``::``-separated component becomes an untyped
    :class:`Namespace` segment (the closest v2 shape a v1 document's own
    information supports), even when the real declaration nested inside a
    record, an inline namespace, or an anonymous scope instead. A v1-loaded
    ``EntityId`` is a best-effort reconstruction, not guaranteed equal to the
    v2 encoding the same logical declaration would produce today — this is an
    accepted, one-time migration-boundary gap (D8's "a migration adapter per
    DTO version"), not a property the wire format promises going forward.

    Empty ``::``-separated components are preserved, not discarded --
    ``storage.entity_ids.EntityId.qualified_name`` places no grammar
    restriction on this identity-bearing string (only that it is a plain
    ``str``), so ``"A::B"`` and ``"A::::B"`` are two structurally different,
    equally legal values a v1 producer could have written, and dropping the
    empty component collapsed both onto the identical scope/leaf, colliding
    two distinct identities rather than merely losing which *kind* each
    segment was (Codex review). Preserving it as an explicit, empty-named
    ``Namespace`` segment keeps the two distinguishable without asserting
    anything about what an empty component originally meant.
    """
    kind = EntityKind(_required_field(data, "kind", "an entity-id document"))
    qualified_name = _identity_text(
        _required_field(data, "qualified_name", "an entity-id document"),
        "qualified_name",
    )
    discriminator = _identity_text(data.get("discriminator", ""), "discriminator")
    # str.split always returns at least one element (even for "" or "::"
    # alone), so no separate empty-input branch is needed.
    *scope_names, leaf_name = qualified_name.split("::")
    scope: _DomainScopePath = tuple(Namespace(name=name) for name in scope_names)
    extra = (discriminator,) if discriminator else ()
    return _DomainEntityId(scope=scope, kind=kind, leaf_name=leaf_name, extra=extra)


def _entity_id_schema_version(data: Mapping[str, Any]) -> int:
    """The document's ``schema_version``, defaulting to ``1`` when absent.

    Compared for dispatch by identity, not by ``==`` on the raw value: a
    numeric ``==`` treats JSON ``true`` as equal to ``1`` and ``2.0`` as
    equal to ``2`` (``bool`` subclasses ``int`` in Python, and ``int``/
    ``float`` compare across type), so either would silently dispatch to a
    real version's own parser on a value that was never that version (Codex
    review) — see :func:`~abicheck.storage.guards.strict_int`. Rejected
    outright rather than coerced, matching every other identity-bearing
    field in this module.
    """
    if "schema_version" not in data:
        return 1
    return _strict_int(data["schema_version"], "schema_version")


def domain_entity_id_from_dto(data: Mapping[str, Any]) -> _DomainEntityId:
    """The inverse of :func:`domain_entity_id_to_dto`.

    Dispatches on ``schema_version``: an absent version, or an explicit
    ``1``, is this module's own pre-existing v1 shape, handled by
    :func:`_domain_entity_id_from_v1_dto`'s best-effort migration adapter; the
    current :data:`DOMAIN_ENTITY_ID_SCHEMA_VERSION` is the lossless round
    trip :func:`domain_entity_id_to_dto` produces. Any other version is
    refused outright rather than guessed at — a document from a wire schema
    this build has never written is not this function's to interpret.

    ``extra`` is read via :func:`~abicheck.storage.guards.required_field`,
    not ``.get(key, default)`` — :func:`domain_entity_id_to_dto` always
    emits it, empty list included, so a v2 document missing it entirely is
    truncated or hand-edited rather than a producer that legitimately had
    nothing to say (Codex review, same reasoning as
    :func:`_domain_segment_from_dict`'s per-segment fields).
    """
    _mapping(data, "an entity-id document")
    version = _entity_id_schema_version(data)
    if version == 1:
        return _domain_entity_id_from_v1_dto(data)
    if version != DOMAIN_ENTITY_ID_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported entity-id wire schema version: {version!r} "
            f"(this build reads v1 and v{DOMAIN_ENTITY_ID_SCHEMA_VERSION})"
        )
    scope = tuple(
        _domain_segment_from_dict(segment)
        for segment in _row_sequence(
            _required_field(data, "scope", "an entity-id document"), "scope"
        )
    )
    kind = EntityKind(_required_field(data, "kind", "an entity-id document"))
    leaf_name = _identity_text(
        _required_field(data, "leaf_name", "an entity-id document"), "leaf_name"
    )
    extra = tuple(
        _identity_text(entry, "an extra entry")
        for entry in _row_sequence(
            _required_field(data, "extra", "an entity-id document"), "extra"
        )
    )
    return _DomainEntityId(scope=scope, kind=kind, leaf_name=leaf_name, extra=extra)


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
