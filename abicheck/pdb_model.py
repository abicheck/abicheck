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

"""Bridge PDB-derived :class:`DwarfMetadata` layouts into model types.

On the ELF path, model :class:`~abicheck.model.RecordType` / ``EnumType``
objects are built directly from DWARF DIEs (``dwarf_snapshot.py``), which
already resolve a ``decl_file``.  The PE/PDB path has no such builder — PDB
layout detail lives in a parallel :class:`DwarfMetadata` consumed by the
layout detectors — so declared types never reach the model, and therefore
never reach public-surface resolution (``surface.py``).

This module converts those PDB layouts into model types, carrying the
``decl_file`` recorded by ``pdb_metadata`` (from ``LF_UDT_SRC_LINE`` /
``LF_UDT_MOD_SRC_LINE``) onto ``source_location`` so that
``apply_provenance`` can classify their ``ScopeOrigin`` (ADR-024 Phase 1).

It is intentionally narrow: the dumper only calls it on the PE
header-scoping *fallback* branch (headers requested, castxml could not
resolve a surface — the MSVC C++-mangling gap), keeping default PE diffs
unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .model import EnumMember, EnumType, RecordType, TypeField

if TYPE_CHECKING:
    from .dwarf_metadata import DwarfMetadata

# Map an enum's underlying integer byte size to a representative type name,
# matching the model's default of ``"int"`` when the size is unknown/atypical.
_ENUM_UNDERLYING_BY_SIZE: dict[int, str] = {
    1: "char",
    2: "short",
    4: "int",
    8: "long long",
}


def _record_from_layout(name: str, layout: object) -> RecordType:
    byte_size = getattr(layout, "byte_size", 0) or 0
    is_union = bool(getattr(layout, "is_union", False))
    alignment = getattr(layout, "alignment", 0) or 0
    fields: list[TypeField] = []
    for fi in getattr(layout, "fields", []) or []:
        bit_size = getattr(fi, "bit_size", 0) or 0
        offset_bits = (getattr(fi, "byte_offset", 0) or 0) * 8 + (
            getattr(fi, "bit_offset", 0) or 0
        )
        fields.append(
            TypeField(
                name=fi.name,
                type=fi.type_name,
                offset_bits=offset_bits,
                is_bitfield=bit_size > 0,
                bitfield_bits=bit_size if bit_size > 0 else None,
            )
        )
    return RecordType(
        name=name,
        kind="union" if is_union else "struct",
        size_bits=byte_size * 8 if byte_size else None,
        alignment_bits=alignment * 8 if alignment else None,
        fields=fields,
        is_union=is_union,
        source_location=getattr(layout, "decl_file", None),
    )


def _enum_from_info(name: str, info: object) -> EnumType:
    size = getattr(info, "underlying_byte_size", 0) or 0
    members = [
        EnumMember(name=mname, value=mval)
        for mname, mval in (getattr(info, "members", {}) or {}).items()
    ]
    return EnumType(
        name=name,
        members=members,
        underlying_type=_ENUM_UNDERLYING_BY_SIZE.get(size, "int"),
        source_location=getattr(info, "decl_file", None),
    )


def model_types_from_dwarf_metadata(
    meta: DwarfMetadata | None,
) -> tuple[list[RecordType], list[EnumType]]:
    """Convert PDB/DWARF layout metadata into model record/enum types.

    Returns ``([], [])`` when *meta* is empty.  ``source_location`` is set to
    each layout's ``decl_file`` (``None`` when the debug info did not record
    one), so downstream :func:`apply_provenance` can tag a ``ScopeOrigin``.
    Iteration order follows the source dict insertion order for determinism.
    """
    if meta is None or not getattr(meta, "has_dwarf", False):
        return [], []
    records = [_record_from_layout(name, layout) for name, layout in meta.structs.items()]
    enums = [_enum_from_info(name, info) for name, info in meta.enums.items()]
    return records, enums
