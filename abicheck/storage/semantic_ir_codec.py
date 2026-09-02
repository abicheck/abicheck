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
from dataclasses import fields
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

#: Every ``Fact``-typed field on ``CanonicalEntity``, derived from the
#: dataclass itself so a new field cannot be added to the model and silently
#: left unrequired here. The writer emits all of them, so a document short of
#: one is truncated: letting the dataclass default fill it in would turn
#: missing persisted evidence into a valid `NOT_COLLECTED` availability claim.
_FACT_FIELDS = tuple(
    f.name for f in fields(CanonicalEntity) if f.name not in ("producer",)
)

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
    """One ``Fact``'s wire form: status value, JSON-native value, diagnostics."""
    value = fact.value
    return {
        "status": fact.status.value,
        "value": list(value) if isinstance(value, tuple) else value,
        "diagnostics": list(fact.diagnostics),
    }


def _fact_value(raw: Any, field_name: str, *, as_tuple: bool) -> Any:
    """One semantic fact's value, checked against the shape its field
    declares rather than admitted as whatever the document holds.

    ``None`` is legitimate for every status (a confirmed-absent
    ``Fact.present(None)``, and the value-less statuses). Anything else must
    match the field's declared type: a `str` spelling, or a list of `str`
    for a tuple-valued field. Without this, `"cv_qualification": {"value":
    ""}` entered the IR as a present-but-scalar qualification and
    `canonical_cv_qualification` read it as the *empty* one -- malformed
    persisted state reaching reduction and hybrid merging as if a backend
    had established it (Codex review).
    """
    if raw is None:
        return None
    if not as_tuple:
        return identity_text(raw, f"semantic_ir {field_name} value")
    entries = row_sequence(raw, f"semantic_ir {field_name} value")
    return tuple(
        identity_text(entry, f"semantic_ir {field_name} value[{index}]")
        for index, entry in enumerate(entries)
    )


def _fact_from_dict(raw: Any, *, as_tuple: bool, field_name: str) -> Fact[Any]:
    data = _mapping(raw, "semantic_ir fact")
    return Fact(
        # required_field, not `data[...]`/`data.get(...)`: a truncated fact
        # keeping `"status": "present"` while losing `value` would otherwise
        # construct `Fact(PRESENT, None)`, which `resolved_fact_count` reads
        # as usable evidence for a spelling the document no longer carries;
        # and a bare subscript raises `KeyError`, which is neither of the two
        # exceptions this package documents as "the document is malformed".
        status=FactStatus(required_field(data, "status", "semantic_ir fact")),
        value=_fact_value(
            required_field(data, "value", "semantic_ir fact"),
            field_name,
            as_tuple=as_tuple,
        ),
        # diagnostics_from, never bare tuple(): a string is a Sequence, so
        # ``"diagnostics": "parse error"`` would decode to eleven
        # single-character diagnostics and be written back that way, and a
        # non-string member would be coerced into a manufactured one.
        # Required, not defaulted: this writer emits the key for every fact
        # (an empty list when there are none), so a missing one is a
        # truncated document -- and defaulting it erases a FAILED fact's
        # only explanation of why the producer could not establish the
        # value, which is the whole reason diagnostics are persisted.
        diagnostics=diagnostics_from(
            required_field(data, "diagnostics", "semantic_ir fact")
        ),
    )


def _entity_to_dict(entity: CanonicalEntity) -> dict[str, Any]:
    """One ``CanonicalEntity``'s wire form: every ``Fact`` field, plus
    ``producer`` when the entity names one (sparse, like every other
    only-present-when-meaningful key in this format)."""
    document: dict[str, Any] = {
        name: _fact_to_dict(fact) for name, fact in entity.fact_items()
    }
    if entity.producer:
        document["producer"] = entity.producer
    return document


def _entity_from_dict(raw: Any) -> CanonicalEntity:
    """Rebuild a ``CanonicalEntity``, requiring every fact field the writer
    emits — see :data:`_FACT_FIELDS`."""
    data = _mapping(raw, "semantic_ir entity")
    facts = {
        name: _fact_from_dict(
            required_field(data, name, "semantic_ir entity"),
            as_tuple=name in _TUPLE_VALUED_FACTS,
            field_name=name,
        )
        for name in _FACT_FIELDS
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
    else:
        # Validated on the way OUT as strictly as on the way in, and for a
        # reason specific to writing: a non-string key survives in memory
        # but `json.dumps` renders it as a string, so `{1: "first", "1":
        # "second"}` writes one JSON object with two `"1"` keys and the load
        # keeps whichever came last -- a conflict record discarded by the
        # writer, before any reader could refuse it (CodeRabbit review).
        # Sorted for the same reason the occurrence list is: `json.dumps`
        # preserves a mapping's insertion order, so two runs that recorded
        # the same conflicts in a different order would otherwise write
        # different documents for identical state.
        d["semantic_ir_conflicts"] = {
            identity_text(key, "semantic_ir_conflicts key"): identity_text(
                value, "semantic_ir_conflicts value"
            )
            for key, value in sorted(
                _mapping(snap.semantic_ir_conflicts, "semantic_ir_conflicts").items(),
                key=lambda item: identity_text(item[0], "semantic_ir_conflicts key"),
            )
        }
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
            # Required and never coerced. This writer always emits the
            # field (an undisambiguated occurrence is `""`), so `null` or a
            # missing key is a truncated document, and accepting either as
            # `""` would rewrite it as a *genuinely* undisambiguated
            # occurrence -- changing what the hybrid merge's matching does
            # with it. `identity_text` then refuses a coerced value: a JSON
            # `1` and `"1"` would otherwise become one `OccurrenceId` and
            # the assignment below would drop one of the two occurrences
            # (`storage/AGENTS.md` invariant 6).
            disambiguator=identity_text(
                required_field(occurrence, "disambiguator", "semantic_ir occurrence"),
                "semantic_ir occurrence disambiguator",
            ),
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
