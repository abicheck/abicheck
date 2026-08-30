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

from typing import TYPE_CHECKING, Any

from .entity_ids import domain_entity_id_from_dto, domain_entity_id_to_dto

if TYPE_CHECKING:
    from ..model.identity import EntityId
    from ..model.snapshot import AbiSnapshot

__all__ = ["decode_entity_id", "encode_entity_ids"]

#: Snapshot keys holding lists of declaration dicts/objects that carry the
#: field, paired with the matching ``AbiSnapshot`` attribute name.
#: ``typedefs``/``constants`` get no carrier (``AbiSnapshot``'s own
#: `model/entities.py` docstring on `RecordType.entity_id` records why) so
#: they are not listed here.
_DECLARATION_LIST_KEYS = (
    ("types", "types"),
    ("enums", "enums"),
    ("functions", "functions"),
    ("variables", "variables"),
)


def encode_entity_ids(d: dict[str, Any], snap: AbiSnapshot) -> None:
    """In-place: replace each declaration dict's ``entity_id`` with its
    wire-schema-v2 document, derived from *snap*'s own still-typed carrier.

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


def decode_entity_id(raw: Any) -> EntityId | None:
    """Reconstruct a declaration's ``entity_id`` carrier from its wire
    document, or ``None`` when the declaration never resolved one.

    Unlike ``Fact[T]`` fields, a missing/``None`` ``entity_id`` never means
    "this snapshot predates the carrier" versus "malformed" — the field is
    genuinely optional even on a snapshot written by the current build (a
    direct, non-producer construction of ``RecordType``/etc., or a
    declaration kind no producer resolves an identity for yet), so there is
    no schema-version-gated distinction to make here the way
    ``fact_codec.decode_fact`` has to draw one.
    """
    if not raw:
        return None
    return domain_entity_id_from_dto(raw)
