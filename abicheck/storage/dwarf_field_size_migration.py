# SPDX-License-Identifier: Apache-2.0
"""Generation-58 migration: an older DWARF member size of 0 is unknown.

Before extractor generation 58 the DWARF producer wrote a member's type size
as ``0`` when it could not evaluate it. Generation 58 writes ``null`` for that
case and keeps ``0`` for a real zero (a flexible array member of a known
element). A stored ``0`` from an older build therefore cannot be told from a
real zero, and read as one it would fabricate a ``0 -> N`` field type change
against any fresh dump whose member has a known size.

So every stored member ``byte_size: 0`` in the ``dwarf`` block of a snapshot
written before generation 58 loads as unknown (``None``): the size rule then
declines it, and the cost is a missed ``0 -> N`` change on a real flexible
array member, never a fabricated one. Only the DWARF layouts carry a member
``byte_size``; header-backend and binary-only facts are untouched. Re-dumping
the baseline drops the migration.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .versioning import GENERATION_DRIFT_NOTICE_MARKER

__all__ = ["DWARF_FIELD_SIZE_GENERATION", "migrate_dwarf_field_size_unknown"]

#: The extractor generation that began writing an unknown member size as null.
DWARF_FIELD_SIZE_GENERATION = 58


def _lowered_layout(layout: Any, hits: list[int]) -> Any:
    if not isinstance(layout, Mapping) or not isinstance(layout.get("fields"), list):
        return layout
    fields: list[Any] = []
    changed = False
    for member in layout["fields"]:
        if (
            isinstance(member, Mapping)
            and member.get("byte_size") == 0
            and not isinstance(member.get("byte_size"), bool)
        ):
            hits[0] += 1
            fields.append({**member, "byte_size": None})
            changed = True
        else:
            fields.append(member)
    return {**layout, "fields": fields} if changed else layout


def _lowered_dwarf(dwarf: Mapping[str, Any], hits: list[int]) -> dict[str, Any]:
    out = dict(dwarf)
    structs = dwarf.get("structs")
    if isinstance(structs, Mapping):
        out["structs"] = {n: _lowered_layout(s, hits) for n, s in structs.items()}
    conflicts = dwarf.get("struct_odr_conflicts")
    if isinstance(conflicts, Mapping):
        out["struct_odr_conflicts"] = {
            n: [_lowered_layout(v, hits) for v in variants]
            if isinstance(variants, list)
            else variants
            for n, variants in conflicts.items()
        }
    return out


def migrate_dwarf_field_size_unknown(
    document: dict[str, Any], extractor_generation: int
) -> tuple[dict[str, Any], list[str]]:
    """*document* with every pre-58 DWARF member ``byte_size: 0`` read as
    unknown; never mutated in place."""
    if extractor_generation >= DWARF_FIELD_SIZE_GENERATION:
        return document, []
    dwarf = document.get("dwarf")
    if not isinstance(dwarf, Mapping):
        return document, []
    hits = [0]
    lowered = _lowered_dwarf(dwarf, hits)
    if not hits[0]:
        return document, []
    return {**document, "dwarf": lowered}, [
        f"read {hits[0]} stored DWARF member size(s) of 0 as not observed: the "
        f"snapshot was {GENERATION_DRIFT_NOTICE_MARKER} extractor semantics "
        "(an unknown member size was written as 0 before generation "
        f"{DWARF_FIELD_SIZE_GENERATION}); re-dump the baseline to drop this "
        "migration"
    ]
