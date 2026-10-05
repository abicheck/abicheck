# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The persisted view of a ``SemanticIR``: what a save/load round trip keeps.

The function-signature/parameter/qualifier/declaration facts on
``CanonicalEntity`` are a comparison-time projection the codec never writes
(``storage/semantic_ir_codec.PROJECTION_FACTS``), so a reloaded IR equals the
written one with exactly those facts reset to their ``NOT_COLLECTED``
default. Computed here from the dataclass defaults, not from the codec's own
filter, so the assertion is not the implementation restated."""

from __future__ import annotations

import dataclasses

from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_declaration_facts import DECLARATION_FIELDS
from abicheck.model.semantic_ir_function_signature import SIGNATURE_FIELDS

#: The projection fields, named independently of the codec.
PROJECTION_FIELDS = frozenset((*SIGNATURE_FIELDS, *DECLARATION_FIELDS))


def _default(name: str):
    field = {f.name: f for f in dataclasses.fields(CanonicalEntity)}[name]
    return field.default


def persisted_view(ir: SemanticIR | None) -> SemanticIR | None:
    if ir is None:
        return None
    reset = {name: _default(name) for name in PROJECTION_FIELDS}
    return dataclasses.replace(
        ir,
        occurrences={
            occ: dataclasses.replace(entity, **reset)
            for occ, entity in ir.occurrences.items()
        },
    )
