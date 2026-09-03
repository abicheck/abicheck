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
Since ADR-063 Phase 6's PDB EntityId slice, it also stamps a real
``entity_id`` onto each type via ``extract.pdb_scope`` — see that module's
own docstring for why PDB's flat, already-``"::"``-qualified type names
need a dedicated qualified-name splitter rather than reusing DWARF's/the
header-AST backends' tree-walk approach, and for the namespace-vs-record
disambiguation heuristic's own documented, unverified-against-real-MSVC
limitation. ``meta.structs``'s own key set (already computed by
``pdb_metadata._extract_struct_layouts``) is passed as the *known record
names* that heuristic consults — no separate pass needed, since both
``RecordType``/``EnumType`` construction below and identity resolution
read the identical, already-built dict.

It is intentionally narrow: the dumper only calls it on the PE
header-scoping *fallback* branch (headers requested, castxml could not
resolve a surface — the MSVC C++-mangling gap), keeping default PE diffs
unchanged.

:func:`pdb_semantic_ir` is this module's other half: once ``RecordType``/
``EnumType`` carry a real ``entity_id``, ``extract.semantic_normalizer.
normalize_header_ast`` needs no PDB-specific carve-out at all to build a
``SemanticIR`` from them — its ``types``/``enums`` loop reads only
``qualified_name``/``name`` and ``entity_id``, never touching
``cv_qualification`` (a functions/variables-only fact this types-only slice
never populates for PDB — see ``AGENTS.md``'s DWARF fifth-slice note for
the shape a producer-specific carve-out module takes once PDB's own
function/variable identity work, still unimplemented, lands and needs one).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .extract.pdb_scope import enum_entity_id, record_entity_id
from .extract.semantic_normalizer import normalize_header_ast
from .model import EnumMember, EnumType, Fact, RecordType, TypeField
from .model.semantic_ir import SemanticIR

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


def _record_from_layout(
    name: str, layout: object, known_record_names: frozenset[str]
) -> RecordType:
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
                # ADR-063 Phase 5 (eighth batch): the PDB layout view carries
                # names, types and offsets only -- it determines no CV
                # qualification, no `mutable` specifier, no default member
                # initializer and no deprecation for a member, so each reads
                # UNSUPPORTED rather than a blanket False/None a detector
                # could mistake for "confirmed not const"/"no initializer".
                is_const_fact=Fact.unsupported(),
                is_volatile_fact=Fact.unsupported(),
                is_mutable_fact=Fact.unsupported(),
                default_fact=Fact.unsupported(),
                deprecated_fact=Fact.unsupported(),
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
        entity_id=record_entity_id(name, known_record_names),
    )


def _enum_from_info(
    name: str, info: object, known_record_names: frozenset[str]
) -> EnumType:
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
        entity_id=enum_entity_id(name, known_record_names),
    )


def model_types_from_dwarf_metadata(
    meta: DwarfMetadata | None,
) -> tuple[list[RecordType], list[EnumType]]:
    """Convert PDB/DWARF layout metadata into model record/enum types.

    Returns ``([], [])`` when *meta* is empty.  ``source_location`` is set to
    each layout's ``decl_file`` (``None`` when the debug info did not record
    one), so downstream :func:`apply_provenance` can tag a ``ScopeOrigin``.
    Iteration order follows the source dict insertion order for determinism.

    Since ADR-063 Phase 6's PDB EntityId slice, each type's ``entity_id`` is
    also stamped via ``extract.pdb_scope`` — ``meta.structs``' own key set
    (the fully-qualified names this same call already has in hand) doubles
    as the *known record names* that module's namespace-vs-record
    disambiguation heuristic consults, computed once and reused for every
    record/enum rather than once per entity.
    """
    if meta is None or not getattr(meta, "has_dwarf", False):
        return [], []
    known_record_names = frozenset(meta.structs)
    records = [
        _record_from_layout(name, layout, known_record_names)
        for name, layout in meta.structs.items()
    ]
    enums = [
        _enum_from_info(name, info, known_record_names)
        for name, info in meta.enums.items()
    ]
    return records, enums


def pdb_semantic_ir(types: list[RecordType], enums: list[EnumType]) -> SemanticIR:
    """``AbiSnapshot.semantic_ir`` for the PDB types/enums
    :func:`model_types_from_dwarf_metadata` just built (ADR-063 Phase 6,
    PDB EntityId slice).

    Mirrors ``dumper_elf_fallback._dwarf_types_semantic_ir``: only the
    types/enums this module can actually give a real ``entity_id`` are
    normalized. This module's own ``funcs`` (the PE export-table entries
    ``_dump_pe`` builds directly) are NOT PDB-derived and are deliberately
    left out, same reasoning as that function's own docstring —
    normalizing them here would misattribute a ``producer="pdb"``
    occurrence to evidence PDB never actually supplied.
    """
    return normalize_header_ast(
        types=types,
        enums=enums,
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="pdb",
    )
