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

"""Encode/decode ``AbiSnapshot.semantic_ir`` for the wire (ADR-063 Phase 6,
schema v38).

**Why ``dataclasses.asdict()`` cannot do this, and why it is a hard blocker
rather than a formatting preference.** ``SemanticIR.occurrences`` is keyed by
an ``OccurrenceId`` *dataclass*, and ``asdict()`` recurses into a dict's keys
exactly as it does into its values — so the key becomes a nested dict, and a
dict is unhashable: ``asdict()`` itself raises while building the converted
mapping, for every snapshot carrying a populated ``semantic_ir``, long before
``json.dump()`` runs. ``serialization.snapshot_to_dict`` therefore clears the
field for the ``asdict()`` call (the same special-casing ``surface_graph``
already needs) and hands the original, still-typed object here.

**Encoded as a list of entries, not a dict.** Flattening ``OccurrenceId``
into a string key is the same lossy move ADR-063 Phase 2 already rejected for
``EntityId``'s own ``ScopePath``, for the identical structural reason: an
``OccurrenceId`` carries an ``EntityId`` carrying a ``ScopePath``, so a
rendered key cannot be reversed. Each entry is
``{"occurrence": {...}, "entity": {...}}``, with the occurrence's own
``EntityId`` going through :mod:`abicheck.storage.entity_ids`' existing
``domain_entity_id_to_dto``/``domain_entity_id_from_dto`` bridge rather than a
second, parallel encoding of the same structure.

**Owned by ``storage``, not ``model``.** The plan's own text put these two
functions in ``serialization.py`` for one specific reason — a ``model``-
resident ``to_dict()`` would need ``model -> storage``, reversing ADR-061's
fixed direction. This module satisfies that constraint the way this package's
two sibling codecs (``entity_id_codec.py``, ``surface_graph_codec.py``)
already do: ``storage`` may depend on ``model``, so the conversion lives here
and ``serialization.py`` calls it, keeping that module (already at its
``architecture/debt.yaml`` no-growth baseline) to the call-site plumbing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..model.availability import FactStatus
from ..model.fact import Fact
from ..model.occurrence import OccurrenceId, canonical_key
from ..model.semantic_ir import CanonicalEntity, SemanticIR
from .entity_ids import domain_entity_id_from_dto, domain_entity_id_to_dto
from .guards import (
    diagnostics_from,
    identity_text,
    mapping as _require_mapping,
    provenance_text,
    required_field,
    row_sequence,
)

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = ["decode_semantic_ir", "encode_semantic_ir"]

#: The ``CanonicalEntity`` fields carrying a tuple-valued ``Fact``. JSON has
#: no tuple, so these come back as lists and are re-tupled on load — without
#: this, a saved-then-loaded IR would compare unequal to the one that was
#: written, for no semantic reason.
_TUPLE_VALUED_FACTS = ("template_arguments", "cv_qualification")


def _mapping(raw: Any, field_name: str) -> Mapping[str, Any]:
    """*raw*, checked to be a mapping first — :func:`storage.guards.mapping`
    validates without returning, and every read below needs both."""
    _require_mapping(raw, field_name)
    assert isinstance(raw, Mapping)  # narrowed by the guard above, for mypy
    return raw


def _fact_to_dict(fact: Fact[Any]) -> dict[str, Any]:
    value = fact.value
    return {
        "status": fact.status.value,
        "value": list(value) if isinstance(value, tuple) else value,
        "diagnostics": list(fact.diagnostics),
    }


def _fact_from_dict(raw: Any, *, as_tuple: bool) -> Fact[Any]:
    data = _mapping(raw, "semantic_ir fact")
    value = data.get("value")
    if as_tuple and isinstance(value, list):
        value = tuple(value)
    return Fact(
        status=FactStatus(data["status"]),
        value=value,
        # diagnostics_from, never bare tuple(): a string is a Sequence, so
        # ``"diagnostics": "parse error"`` would decode to eleven
        # single-character diagnostics and be written back that way, and a
        # non-string member would be coerced into a manufactured one.
        # `_present_or` rather than `... or ()`: a FALSEY malformed value
        # (`False`, `0`, `{}`) would otherwise skip the guard entirely and
        # be rewritten as "no diagnostics" -- the same silent-discard this
        # guard exists to refuse, reached by a different route.
        diagnostics=diagnostics_from(_present_or(data, "diagnostics", ())),
    )


def _present_or(document: Mapping[str, Any], key: str, default: Any) -> Any:
    """*document*'s value for *key*, or *default* when the key is genuinely
    absent.

    Absence and a falsey-but-present value are different documents and this
    codec must not conflate them: `document.get(key) or default` hands a
    malformed `False`/`0`/`{}`/`[]` straight past whichever guard was meant
    to inspect it, turning "this field is corrupt" into "this field says
    nothing" with no error anywhere. Every guarded read below goes through
    here, so the distinction cannot be forgotten at one site the way it was
    at three (Codex review).
    """
    return document[key] if key in document else default


def _disambiguator(raw: Any) -> str:
    """An occurrence's disambiguator: absent/``null`` is the ordinary empty
    case, anything else must genuinely be a string."""
    if raw is None or raw == "":
        return ""
    return identity_text(raw, "semantic_ir occurrence disambiguator")


def _entity_to_dict(entity: CanonicalEntity) -> dict[str, Any]:
    document: dict[str, Any] = {
        name: _fact_to_dict(fact) for name, fact in entity.fact_items()
    }
    if entity.producer:
        document["producer"] = entity.producer
    return document


def _entity_from_dict(raw: Any) -> CanonicalEntity:
    data = _mapping(raw, "semantic_ir entity")
    facts = {
        name: _fact_from_dict(value, as_tuple=name in _TUPLE_VALUED_FACTS)
        for name, value in data.items()
        if name != "producer"
    }
    producer = data.get("producer", "")
    return CanonicalEntity(
        producer=provenance_text(producer, "semantic_ir entity producer")
        if producer != ""
        else "",
        **facts,
    )


def encode_semantic_ir(d: dict[str, Any], snap: AbiSnapshot) -> None:
    """In-place: write ``snap.semantic_ir``'s list-of-entries encoding into
    ``d["semantic_ir"]``, or drop the key when the snapshot carries none.

    Dropping (rather than writing ``null``) matches every other sparse,
    only-present-when-meaningful field this package's wire format already
    uses, so a snapshot from a backend not yet narrowed onto the normalizer
    serializes exactly as it did before this field existed.
    """
    # Same sparse convention for the conflict map: written only when a merge
    # actually recorded one, so a snapshot with nothing to say carries no key
    # rather than an empty object. Without this, every v38 document would
    # differ from the v37 one it would otherwise have been -- for a field
    # only the hybrid path can ever populate.
    if not snap.semantic_ir_conflicts:
        d.pop("semantic_ir_conflicts", None)
    ir = snap.semantic_ir
    if ir is None:
        d.pop("semantic_ir", None)
        return
    d["semantic_ir"] = {
        "occurrences": [
            {
                "occurrence": {
                    "entity_id": domain_entity_id_to_dto(occ_id.entity_id),
                    "disambiguator": occ_id.disambiguator,
                },
                "entity": _entity_to_dict(entity),
            }
            # Sorted, never the mapping's incidental insertion order: two
            # equal ``SemanticIR`` values built in opposite orders must
            # serialize identically, or a content hash over the document
            # reports a difference the stored state does not have
            # (``storage/AGENTS.md`` invariant 4).
            for occ_id, entity in sorted(
                ir.occurrences.items(), key=lambda item: canonical_key(item[0])
            )
        ]
    }


def decode_semantic_ir(d: dict[str, Any], snap: AbiSnapshot) -> None:
    """In-place: rebuild ``snap.semantic_ir`` and ``snap.semantic_ir_conflicts``
    from *d*, leaving the IR ``None`` for a document that carries no
    ``semantic_ir`` key (every snapshot written before schema v38, and every
    one written since by a backend with no IR to record).

    The conflict map is decoded independently of the IR: a merged snapshot
    can legitimately record conflicts while a *reader* of that document has
    no IR key to attach them to only if the writer dropped one, so tying the
    two together would silently discard evidence in the one case the pairing
    is not guaranteed.
    """
    if "semantic_ir_conflicts" in d:
        snap.semantic_ir_conflicts = {
            identity_text(key, "semantic_ir_conflicts key"): identity_text(
                value, "semantic_ir_conflicts value"
            )
            for key, value in _mapping(
                d["semantic_ir_conflicts"], "semantic_ir_conflicts"
            ).items()
        }
    if "semantic_ir" not in d:
        # Key absent: this snapshot predates v38, or its backend produced no
        # IR. A key that is PRESENT but malformed (`[]`, `""`, `0`) is a
        # different document and is rejected below rather than read as
        # "no backend produced one".
        return
    document = _mapping(d["semantic_ir"], "semantic_ir")
    occurrences: dict[OccurrenceId, CanonicalEntity] = {}
    # `required_field`, not a default: this codec always writes the key (an
    # IR that observed nothing is `{"occurrences": []}`), so a present
    # `semantic_ir` without it is a truncated document. Substituting `()`
    # would turn "the occurrence evidence is missing" into the *claim* that
    # a backend ran and established there are none -- the "absence is not
    # evidence" reading this package exists to refuse (Codex review).
    for entry_raw in row_sequence(
        required_field(document, "occurrences", "semantic_ir"),
        "semantic_ir occurrences",
    ):
        entry = _mapping(entry_raw, "semantic_ir occurrence entry")
        occurrence = _mapping(entry.get("occurrence"), "semantic_ir occurrence")
        occ_id = OccurrenceId(
            entity_id=domain_entity_id_from_dto(
                _mapping(occurrence.get("entity_id"), "semantic_ir entity_id")
            ),
            # identity_text, never str(): a JSON ``1`` and ``"1"`` would
            # otherwise become one ``OccurrenceId``, and the assignment
            # below would silently drop one of the two occurrences
            # (``storage/AGENTS.md`` invariant 6).
            disambiguator=_disambiguator(occurrence.get("disambiguator")),
        )
        if occ_id in occurrences:
            # The list encoding can spell a duplicate that the mapping cannot
            # hold; assigning over it would report a successful load having
            # discarded an occurrence, which is the one thing this package
            # never does (``storage/AGENTS.md``: never resolve identity by
            # discarding an occurrence).
            raise ValueError(
                f"semantic_ir names the same occurrence twice "
                f"({canonical_key(occ_id)!r}); one of the two entities would "
                "be discarded on load"
            )
        occurrences[occ_id] = _entity_from_dict(entry.get("entity"))
    snap.semantic_ir = SemanticIR(occurrences=occurrences)
