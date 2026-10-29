# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""The one checker-side reader of the debug layout collections (ADR-063,
"no backend-specific collection").

The debug layout lives in the snapshot's IR store
(``semantic_ir.declarations.debug_layout``/``debug_advanced``,
``model/declaration_store.py``) -- what every debug carrier reduces to at
extraction: DWARF directly, BTF/CTF/PDB through their ``to_dwarf_metadata``
reductions. Detectors read a :class:`DebugLayoutView` over it: a
read-only view of one side's identity-less debug layout (record and enum
layouts keyed by their debug spelling, base-type sizes) plus the two channel
payloads the layout detector families diff whole.
No checker reads a backend-named snapshot collection
(``scripts/semantic_ir_cutover.py``'s backend-declaration rule, no baseline).

Evidence-tier questions ("was there debug info at all?") are not layout
questions; they live in :mod:`abicheck.model.debug_evidence`.
"""

from __future__ import annotations

from collections.abc import Container, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..model.dwarf_facts import (
    AdvancedDwarfMetadata,
    AdvancedExtractionStatus,
    DwarfMetadata,
    EnumInfo,
    StructLayout,
    advanced_facts_collected,
    debug_info_present,
)
from ..model.entities import (
    EnumMember,
    EnumType,
    RecordType,
    TypeField,
    resolve_vptr_offset_bits,
)
from ..model.fact import Fact

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "DebugLayoutView",
    "advanced_dwarf_support",
    "debug_layout_declarations",
    "debug_layout_view",
    "restrict_debug_layout",
]


@dataclass(frozen=True)
class DebugLayoutView:
    """One snapshot side's debug layout, independent of the debug format."""

    #: Debug info was actually collected on the layout channel.
    present: bool
    #: Record layouts keyed by their debug spelling (possibly qualified).
    records: Mapping[str, StructLayout]
    #: Enum layouts keyed by their debug spelling.
    enums: Mapping[str, EnumInfo]
    #: Base-type byte sizes keyed by spelling (``"long double"`` -> 16).
    base_type_sizes: Mapping[str, int]
    #: The advanced channel's facts were genuinely parsed (not presence-only).
    advanced_collected: bool
    #: What the advanced extraction recorded (``UNRECORDED`` when it did not).
    advanced_status: AdvancedExtractionStatus
    #: Whole layout payload for the layout detector family; an empty one when
    #: the side carries none, so a detector never branches on ``None``.
    layout: DwarfMetadata
    #: Whole advanced payload (calling convention, packing, toolchain);
    #: empty when absent.
    advanced: AdvancedDwarfMetadata


def _layout_channel(snap: AbiSnapshot) -> DwarfMetadata | None:
    """The raw layout-channel payload, read from the IR's declaration store."""
    return snap.declarations.debug_layout


def debug_layout_view(snap: AbiSnapshot) -> DebugLayoutView:
    """*snap*'s :class:`DebugLayoutView`."""
    basic = _layout_channel(snap)
    advanced = snap.declarations.debug_advanced
    layout = basic or DwarfMetadata()
    return DebugLayoutView(
        present=debug_info_present(basic),
        records=layout.structs,
        enums=layout.enums,
        base_type_sizes=layout.base_types,
        advanced_collected=advanced_facts_collected(advanced),
        advanced_status=(
            advanced.extraction
            if advanced is not None
            else AdvancedExtractionStatus.UNRECORDED
        ),
        layout=layout,
        advanced=advanced or AdvancedDwarfMetadata(),
    )


def restrict_debug_layout(
    snap: AbiSnapshot,
    record_scope: Container[str] | None,
    enum_scope: Container[str] | None,
) -> None:
    """Narrow *snap*'s debug record/enum layouts in place to the given scopes.

    ``None`` leaves that half untouched; a side with no layout is left as is.
    Depth projection uses this to pre-scope the layout pool."""
    dwarf = _layout_channel(snap)
    if dwarf is None:
        return
    if record_scope is not None:
        dwarf.structs = {k: v for k, v in dwarf.structs.items() if k in record_scope}
    if enum_scope is not None:
        dwarf.enums = {k: v for k, v in dwarf.enums.items() if k in enum_scope}


#: Representative underlying-type spelling per enum byte size (the model's
#: own ``"int"`` default for an unknown size), as the BTF/CTF/PDB producers use.
_ENUM_UNDERLYING_BY_SIZE: dict[int, str] = {
    1: "char",
    2: "short",
    4: "int",
    8: "long long",
}


def _layout_record(name: str, layout: StructLayout) -> RecordType:
    vptr_offsets = [fi.byte_offset * 8 for fi in layout.fields if fi.is_vptr_member]
    record = RecordType(
        name=name,
        kind="union" if layout.is_union else "struct",
        size_bits=layout.byte_size * 8 if layout.byte_size else None,
        alignment_bits=layout.alignment * 8 if layout.alignment else None,
        fields=[
            TypeField(
                name=fi.name,
                type=fi.type_name,
                offset_bits=fi.byte_offset * 8 + fi.bit_offset,
                is_bitfield=fi.bit_size > 0,
                bitfield_bits=fi.bit_size if fi.bit_size > 0 else None,
                is_const_fact=Fact.unsupported(),
                is_volatile_fact=Fact.unsupported(),
                is_mutable_fact=Fact.unsupported(),
                default_fact=Fact.unsupported(),
                deprecated_fact=Fact.unsupported(),
                # The member's ABI fact is debug-info evidence the layout
                # carries (schema v69); a field without one stays unset.
                type_abi_fact=(
                    None if fi.type_abi_fact is None else Fact.present(fi.type_abi_fact)
                ),
            )
            for fi in layout.fields
            if not fi.is_vptr_member
        ],
        is_union=layout.is_union,
    )
    # Only a local vptr member is evidence; its absence is not (a derived
    # class's vptr lives in its base subobject), so it stays not collected.
    if vptr_offsets:
        resolve_vptr_offset_bits(record, vptr_offsets[0])
    return record


def debug_layout_declarations(
    snap: AbiSnapshot,
) -> tuple[list[RecordType], list[EnumType]]:
    """*snap*'s debug record/enum layouts as layout-only model declarations.

    What a binary-only (debug info, no headers) dump puts in
    ``declarations.types``/``.enums`` for the same layouts, reduced to the
    facts the layout channel itself carries: size, alignment, field
    names/spellings/offsets/bitfields, enumerators. Every other field fact
    reads unsupported, never a confirmed negative; bases and the vtable are
    not collected. The compiler's artificial vptr member is the record's
    vptr offset, not a data member -- the DWARF record builder never lists it
    as a field, and listing it would read a class turning polymorphic as a
    compatible field addition. Depth projection uses it
    so a header-derived snapshot projected to ``binary`` is classified by the
    same type detectors a binary-only dump is, not by the raw-layout
    fallback (bug class ``evidence.depth_projection_diverges_from_shallow_dump``).
    """
    view = debug_layout_view(snap)
    if not view.present:
        return [], []
    records = [_layout_record(name, layout) for name, layout in view.records.items()]
    enums = [
        EnumType(
            name=name,
            members=[EnumMember(name=m, value=v) for m, v in info.members.items()],
            underlying_type=_ENUM_UNDERLYING_BY_SIZE.get(
                info.underlying_byte_size, "int"
            ),
        )
        for name, info in view.enums.items()
    ]
    return records, enums


def advanced_dwarf_support(old: AbiSnapshot, new: AbiSnapshot) -> tuple[bool, str]:
    """Support gate for the ``advanced_dwarf`` detector.

    A side whose advanced extraction failed, or never ran, has no usable facts,
    so the detector is *not evaluated* on that pair. The reason names the state:
    a failure must never read like a side that simply has no debug info.
    """
    sides = (("old", debug_layout_view(old)), ("new", debug_layout_view(new)))
    if all(view.advanced_collected for _side, view in sides):
        return True, "DWARF advanced extraction completed on both sides"
    for side, view in sides:
        if view.advanced_status is AdvancedExtractionStatus.FAILED:
            return False, (
                f"DWARF advanced extraction failed on the {side} side; its facts "
                "are unknown, not absent"
            )
    for side, view in sides:
        if view.advanced_status is AdvancedExtractionStatus.NOT_RUN:
            return False, f"DWARF advanced extraction did not run on the {side} side"
    return False, "missing DWARF advanced metadata"
