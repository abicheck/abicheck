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

"""``AbiSnapshot.semantic_ir`` for BTF/CTF-sourced struct/enum layouts
(ADR-063 Phase 6, BTF/CTF slice, types only).

**Scoped to BTF/CTF specifically, not DWARF generally.** ``btf_metadata.
parse_btf_metadata``/``ctf_metadata.parse_ctf_metadata`` both convert their
own richer, format-specific parse into the shared ``DwarfMetadata`` shape
(``BtfMetadata.to_dwarf_metadata``/``CtfMetadata.to_dwarf_metadata``) purely
so the checker's existing ``_diff_dwarf``/``_diff_advanced_dwarf`` detectors
work against any of the three formats unmodified — but that conversion
carries only flat, name-keyed ``StructLayout``/``EnumInfo`` dicts, never a
``RecordType``/``EnumType`` model object, so nothing along that path has
ever had an ``entity_id`` to give an occurrence. Real DWARF never hits this
gap: ``dumper.py`` only reaches ``_resolve_debug_metadata``'s BTF/CTF
branches when ``resolved_debug_format`` is *not* ``"dwarf"``, and a real
DWARF resolution instead goes through ``dwarf_snapshot.
build_snapshot_from_dwarf``, which has built real, ``entity_id``-bearing
``RecordType``/``EnumType`` objects since Phase 2 — this module exists
because BTF/CTF never got that equivalent bridge at all, not because this
repeats work DWARF already has.

**Deliberately builds transient model objects, never persisted anywhere
outside this call.** Unlike ``pdb_model.py`` (PDB's own, pre-existing
``DwarfMetadata`` → ``RecordType``/``EnumType`` bridge, wired into the PE
header-scoping fallback so those types reach ``AbiSnapshot.types``/
``.enums`` and, from there, ``surface.py``/vtable/internal-leak detection),
this module's own ``RecordType``/``EnumType`` values are consumed only by
:func:`abicheck.extract.semantic_normalizer.normalize_header_ast` and
discarded immediately after — ``AbiSnapshot.types``/``.enums`` are left
exactly as they already are for a BTF/CTF-sourced snapshot (empty, on the
current headerless fallback path). Every prior Phase 6 slice (DWARF, PDB)
only ever added ``entity_id``/``SemanticIR`` normalization on top of a
model-type bridge that already existed independently of Phase 6 for other
reasons; BTF/CTF has no such existing bridge, and building one that newly
feeds ``AbiSnapshot.types``/``.enums`` would newly expose BTF/CTF structs to
every other ``.types``-consuming detector (vtable/internal-leak/public-
surface scoping) — a materially larger, separately-scoped design question
this slice does not attempt, matching this phase's own "canonicalizes
identity a backend already resolved, does not widen what the backend
produces" boundary. A future slice giving BTF/CTF real model-type
population (mirroring what PDB's own PE-fallback wiring already does) can
consume this same ``entity_id`` assignment without redoing it.

**No scope resolution needed at all, unlike PDB.** BTF (the Linux kernel's
BPF Type Format) and CTF (illumos/Solaris's Compact C Type Format) are both
pure-C debug formats with no namespace/class nesting whatsoever — every
``StructLayout``/``EnumInfo`` name is already a flat, unqualified C
identifier, so :func:`abicheck.model.identity.entity_id_for_type`/
``entity_id_for_enum`` are called with an always-empty ``ScopePath``. No
PDB-style "is this an enclosing namespace or record" heuristic applies, and
none of PDB's own documented limitations (forward-declared enclosing
classes, function-local scopes, nested anonymous aggregates) have a BTF/CTF
analogue: a BTF/CTF anonymous struct/union/enum member is a real gap this
module does not handle either (see :func:`semantic_ir_from_debug_metadata`'s
own note), but for the unrelated reason that ``StructLayout``/``EnumInfo``
carry no anonymous-vs-named distinction for a *nested member's own type* at
all (only ``FieldInfo.type_name``, a plain string) — nothing here to key an
``Anonymous`` segment off regardless of scope.

Leaf module: depends on ``model``/``model.dwarf_facts`` (allowed:
``extract -> model, storage``, ADR-061) and its sibling
``extract.semantic_normalizer`` — nothing above. Deliberately does not
import ``btf_metadata``/``ctf_metadata`` themselves: both already reduce to
the shared ``DwarfMetadata`` shape before this module ever runs, so it only
needs that shape, not either producer's own richer type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model import EnumMember, EnumType, Fact, RecordType, TypeField
from ..model.identity import entity_id_for_enum, entity_id_for_type
from .semantic_normalizer import normalize_header_ast

if TYPE_CHECKING:
    from ..model.dwarf_facts import DwarfMetadata, EnumInfo, StructLayout
    from ..model.semantic_ir import SemanticIR

__all__ = ["semantic_ir_from_debug_metadata"]

# Mirrors pdb_model.py's identical table: map an enum's underlying integer
# byte size to a representative type name, matching the model's default of
# "int" when the size is unknown/atypical.
_ENUM_UNDERLYING_BY_SIZE: dict[int, str] = {
    1: "char",
    2: "short",
    4: "int",
    8: "long long",
}


def _record_type_from_layout(name: str, layout: StructLayout) -> RecordType:
    fields = [
        TypeField(
            name=fi.name,
            type=fi.type_name,
            offset_bits=fi.byte_offset * 8 + fi.bit_offset,
            is_bitfield=fi.bit_size > 0,
            bitfield_bits=fi.bit_size if fi.bit_size > 0 else None,
            # Neither BTF nor CTF's own on-disk encoding carries a member's
            # CV-qualification, `mutable` specifier, default initializer, or
            # deprecation -- each reads UNSUPPORTED, never a bare False/None
            # a detector could mistake for "confirmed absent" (mirrors
            # pdb_model.py's identical reasoning for the identical gap).
            is_const_fact=Fact.unsupported(),
            is_volatile_fact=Fact.unsupported(),
            is_mutable_fact=Fact.unsupported(),
            default_fact=Fact.unsupported(),
            deprecated_fact=Fact.unsupported(),
        )
        for fi in layout.fields
    ]
    return RecordType(
        name=name,
        kind="union" if layout.is_union else "struct",
        size_bits=layout.byte_size * 8 if layout.byte_size else None,
        alignment_bits=layout.alignment * 8 if layout.alignment else None,
        fields=fields,
        is_union=layout.is_union,
        source_location=layout.decl_file,
        # Flat C: no enclosing namespace/class, ever -- see this module's
        # own docstring for why no PDB-style scope heuristic applies here.
        entity_id=entity_id_for_type((), name),
    )


def _enum_type_from_info(name: str, info: EnumInfo) -> EnumType:
    members = [
        EnumMember(name=mname, value=mval) for mname, mval in info.members.items()
    ]
    return EnumType(
        name=name,
        members=members,
        underlying_type=_ENUM_UNDERLYING_BY_SIZE.get(info.underlying_byte_size, "int"),
        source_location=info.decl_file,
        entity_id=entity_id_for_enum((), name),
    )


def semantic_ir_from_debug_metadata(meta: DwarfMetadata, producer: str) -> SemanticIR:
    """Build a :class:`~abicheck.model.semantic_ir.SemanticIR` from a
    BTF/CTF-sourced :class:`~abicheck.model.dwarf_facts.DwarfMetadata`.

    *producer* is ``"btf"``/``"ctf"`` — the same ``resolved_debug_format``
    string ``dumper.py`` already tracks — stamped onto every
    :class:`~abicheck.model.semantic_ir.CanonicalEntity` this call produces,
    mirroring every other producer tag this codebase's ``semantic_ir``
    already carries (``"castxml"``/``"clang"``/``"dwarf"``/``"pdb"``).

    Returns an empty :class:`~abicheck.model.semantic_ir.SemanticIR` (no
    occurrences) when *meta* carries no structs/enums at all — the caller's
    own ``meta.has_dwarf``/emptiness check decides whether to call this at
    all, matching every other producer-specific ``*_semantic_ir`` helper in
    this codebase, none of which re-checks its own caller's gate.

    Functions/variables/typedefs are out of scope for this slice, the same
    way they were for DWARF's and PDB's own first "types only" slices:
    ``BtfMetadata``/``CtfMetadata``'s own ``func_protos``/``typedefs`` are
    not even carried across ``to_dwarf_metadata()`` (see that method's own
    docstring), so there is no *matching* ``EntityId``-bearing evidence
    reaching this function to normalize in the first place — not a case
    this function silently drops, but evidence that never arrives here.
    """
    records = [
        _record_type_from_layout(name, layout) for name, layout in meta.structs.items()
    ]
    enums = [_enum_type_from_info(name, info) for name, info in meta.enums.items()]
    return normalize_header_ast(
        types=records,
        enums=enums,
        typedefs_qualified={},
        typedef_entity_ids={},
        producer=producer,
    )
