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

"""Encode/decode the ADR-063 Phase 2 ``entity_id`` carrier for the wire.

``RecordType``/``EnumType``/``Function``/``Variable`` each carry a
parse-time-resolved ``model.identity.EntityId`` since ADR-063 Phase 2's
third slice (the plan's option (a)). Until this module's persistence slice
(the plan's Phase 2 "(c1)"), the field was dropped outright before reaching
the wire — see this module's own git history (``drop_entity_ids``) for that
interim state. It is now encoded through :mod:`abicheck.storage.entity_ids`'
``domain_entity_id_to_dto``/``domain_entity_id_from_dto`` bridge (schema
v2), which the interim state was waiting on: a rendered ``qualified_name``
string cannot losslessly carry ``ScopePath``'s typed segments (a record
nested in a record and the same bare names nested in a namespace render
identically), which is why a stopgap encoding was refused rather than
invented in the interim slice.

``encode_entity_ids`` cannot operate on the already-``dataclasses.asdict()``-
ed snapshot dict alone, unlike this package's other codecs
(``fact_codec.encode_fact_fields``, ``enum_codec``'s siblings): ``asdict()``
recurses into a ``ScopeSegment``'s own dataclass fields, but a plain dict has
no record of *which* segment dataclass (``Namespace``/``Record``/
``InlineNamespace``/``Anonymous``/``LocalToFunction``) produced it — exactly
the type information ``domain_entity_id_to_dto`` needs to tag each segment.
So this codec is given the ORIGINAL, still-typed :class:`~abicheck.model.
snapshot.AbiSnapshot` alongside the ``asdict()``-ed dict, and re-derives each
declaration's wire-encoded ``entity_id`` from the original typed object
rather than from what ``asdict()`` already flattened.

Mirrors ``storage/fact_codec.py``'s ``encode_fact_fields``/``decode_fact``
shape otherwise: an in-place fix-up over the already-``asdict()``-ed
snapshot dict on the encode side, owned by ``storage`` (which may depend on
``model``) rather than inlined into ``serialization.py``, itself already at
this repo's file-size cap.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from .entity_ids import domain_entity_id_from_dto, domain_entity_id_to_dto
from .guards import identity_text, mapping

if TYPE_CHECKING:
    from ..model.declarations import Function, Variable
    from ..model.entities import EnumType, RecordType
    from ..model.identity import EntityId
    from ..model.snapshot import AbiSnapshot

    class _HasEntityIdCarrier(Protocol):
        """Structural stand-in for the four carrier-bearing model dataclasses
        (`Function`/`Variable`/`RecordType`/`EnumType`) — they share no
        declared base class, so a single typed helper over "any one of the
        four" needs a `Protocol`, not a `Union`, to stay a plain assignment
        target (`decl.entity_id = ...`) rather than needing a cast at every
        call site.
        """

        entity_id: EntityId | None


__all__ = [
    "decode_entity_ids",
    "decode_sidecar_entity_ids",
    "encode_entity_ids",
    "encode_sidecar_entity_ids",
]

#: Snapshot keys holding lists of declaration dicts/objects that carry the
#: field, paired with the matching ``AbiSnapshot`` attribute name.
#: ``typedefs``/``constants`` are plain ``dict[str, str]`` with no
#: declaration object to carry the field on, so they are not listed here —
#: their identity travels in the separate ``AbiSnapshot.typedef_entity_ids``/
#: ``constant_entity_ids`` sidecars instead (schema v31), encoded by
#: :func:`encode_sidecar_entity_ids` below.
_DECLARATION_LIST_KEYS = (
    ("types", "types"),
    ("enums", "enums"),
    ("functions", "functions"),
    ("variables", "variables"),
)


def encode_entity_ids(d: dict[str, Any], snap: AbiSnapshot) -> dict[str, Any]:
    """In-place: replace each declaration dict's ``entity_id`` with its
    wire-schema-v2 document, derived from *snap*'s own still-typed carrier.
    Returns *d* itself, so a caller can chain it the same way this package's
    other in-place fix-ups already do (``_sets_to_lists(encode_entity_ids(d,
    snap))``).

    A declaration with no resolved identity (``entity_id is None``) gets no
    key at all — matching every other sparse, only-present-when-meaningful
    field this package's wire format already uses (e.g.
    ``OccurrenceId.to_dict``'s ``container``/``producer``) — rather than an
    explicit ``null``, so a pre-persistence snapshot and a persisted one
    whose declarations never resolved an identity serialize identically.

    Declaration lists are paired by *position*: ``dataclasses.asdict()``
    maps a list field element-by-element, preserving order, so the Nth dict
    in ``d[list_key]`` always corresponds to the Nth object in
    ``getattr(snap, attr_name)``.
    """
    for list_key, attr_name in _DECLARATION_LIST_KEYS:
        decls = getattr(snap, attr_name)
        decl_dicts = d.get(list_key, []) or []
        # Both sides are derived from the same list in the same call
        # (`snapshot_to_dict` builds `d` via `asdict(snap)` before this
        # runs), so a length mismatch means a caller passed a `d`/`snap`
        # pair that were never `asdict()` of each other — a programming
        # error in this codec's own caller, not malformed input to tolerate.
        assert len(decl_dicts) == len(decls), (
            f"{list_key}: {len(decl_dicts)} encoded dicts but "
            f"{len(decls)} typed declarations — d and snap must be "
            "asdict()-paired"
        )
        for decl_dict, decl in zip(decl_dicts, decls, strict=True):
            entity_id = decl.entity_id
            if entity_id is None:
                decl_dict.pop("entity_id", None)
            else:
                decl_dict["entity_id"] = domain_entity_id_to_dto(entity_id)
    return d


#: The ``AbiSnapshot`` attributes (and identically-named wire keys) holding a
#: ``dict[str, EntityId]`` sidecar rather than a declaration list.
_SIDECAR_KEYS = ("typedef_entity_ids", "constant_entity_ids")


def encode_sidecar_entity_ids(d: dict[str, Any], snap: AbiSnapshot) -> dict[str, Any]:
    """In-place: replace each ``dict[str, EntityId]`` sidecar with a plain
    ``{key: entity-id document}`` mapping. Returns *d*, for chaining alongside
    :func:`encode_entity_ids`.

    Needed for the same reason that function is given the still-typed *snap*:
    ``dataclasses.asdict()`` flattens each ``EntityId``'s ``ScopePath``
    segments into anonymous dicts with no record of which segment dataclass
    produced them. An empty sidecar is written as an empty mapping rather than
    dropped, matching how ``typedefs_qualified`` itself is written.
    """
    for key in _SIDECAR_KEYS:
        d[key] = {
            name: domain_entity_id_to_dto(entity_id)
            for name, entity_id in getattr(snap, key).items()
        }
    return d


def decode_sidecar_entity_ids(d: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The inverse of :func:`encode_sidecar_entity_ids`: each sidecar's
    reconstructed ``{key: EntityId}`` mapping, ready to hand straight to the
    ``AbiSnapshot(...)`` reconstruction as keyword arguments.

    Absent on every pre-v31 snapshot, which loads as ``{}`` — the same value a
    v31 snapshot with no header-resolved typedef/constant identity carries, so
    no migration adapter is needed (identical reasoning to the v25
    ``typedefs_qualified`` addition).

    ``raw is None`` is the only degrade-to-empty case, matching
    :func:`_decode_one`'s own rule: a present-but-malformed sidecar (a list,
    a string, a mapping with a non-string key) is refused via ``guards.
    mapping``/``guards.identity_text`` rather than silently read as "this
    snapshot predates the sidecar" (Codex review) — a falsy-but-present
    value like ``[]`` is exactly the shape a truthiness check would have
    let through as an empty mapping.
    """
    result: dict[str, dict[str, Any]] = {}
    for key in _SIDECAR_KEYS:
        raw = d.get(key)
        if raw is None:
            result[key] = {}
            continue
        mapping(raw, key)
        result[key] = {
            identity_text(name, f"{key} key"): domain_entity_id_from_dto(value)
            for name, value in raw.items()
        }
    return result


def _decode_one(raw: Any) -> Any:
    """Reconstruct one declaration's ``entity_id`` carrier from its wire
    document, or ``None`` when the declaration never resolved one.

    ``raw is None``, not a truthiness check, is what means "absent" — a
    malformed wire value that happens to be falsy (``{}``, ``[]``, ``""``,
    ``False``, ``0``) must still reach ``domain_entity_id_from_dto`` so its
    own mapping/required-field validation rejects it, rather than being
    silently read as an ordinary, honestly-unresolved carrier (Codex
    review). Unlike ``Fact[T]`` fields, an absent ``entity_id`` never means
    "this snapshot predates the carrier" versus "malformed" — the field is
    genuinely optional even on a snapshot written by the current build (a
    direct, non-producer construction of ``RecordType``/etc., or a
    declaration kind no producer resolves an identity for yet), so there is
    no schema-version-gated distinction to make here the way
    ``fact_codec.decode_fact`` has to draw one.
    """
    if raw is None:
        return None
    return domain_entity_id_from_dto(raw)


def decode_entity_ids(
    d: dict[str, Any],
    *,
    functions: Sequence[Function],
    variables: Sequence[Variable],
    types: Sequence[RecordType],
    enums: Sequence[EnumType],
) -> None:
    """In-place: set each already-constructed declaration's ``.entity_id``
    from *d*'s own raw wire documents. The inverse of :func:`encode_entity_ids`.

    Takes the four already-built declaration lists rather than constructing
    them itself, the same "given the typed side, not just the dict" shape
    :func:`encode_entity_ids` uses — `RecordType`/`EnumType`/`Function`/
    `Variable` are not frozen, so setting the field post-construction (one
    call here, instead of one `entity_id=...` keyword argument threaded
    through each of the four separate reconstruction sites in
    `serialization.py`, itself already at this repo's file-size debt
    ceiling) is a real simplification, not merely a smaller diff. Declaration
    lists are paired by *position* against ``d``'s own raw dicts, the same
    convention :func:`encode_entity_ids` uses (`snapshot_from_dict` builds
    each list via one list comprehension over `d[list_key]`, preserving
    order) — a length mismatch is this function's own caller's error, so it
    is asserted rather than tolerated, same as the encode side.
    """
    _decode_one_list(d, "functions", functions)
    _decode_one_list(d, "variables", variables)
    _decode_one_list(d, "types", types)
    _decode_one_list(d, "enums", enums)


def _decode_one_list(
    d: dict[str, Any], list_key: str, decls: Sequence[_HasEntityIdCarrier]
) -> None:
    raw_decls = d.get(list_key, []) or []
    assert len(raw_decls) == len(decls), (
        f"{list_key}: {len(raw_decls)} raw documents but {len(decls)} typed "
        "declarations — d and the decl lists must be built from the same "
        "source list"
    )
    for raw_decl, decl in zip(raw_decls, decls, strict=True):
        decl.entity_id = _decode_one(raw_decl.get("entity_id"))
