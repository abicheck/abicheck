# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""The one checker-side reader of the debug layout collections (ADR-063,
"no backend-specific collection").

``AbiSnapshot.dwarf`` / ``AbiSnapshot.dwarf_advanced`` are what every debug
carrier reduces to -- DWARF directly, BTF/CTF/PDB through their
``to_dwarf_metadata`` reductions -- but their field names still spell the
first backend. Detectors read a :class:`DebugLayoutView` instead: a
read-only view of one side's identity-less debug layout (record and enum
layouts keyed by their debug spelling, base-type sizes) plus the two channel
payloads the layout detector families diff whole.
``scripts/semantic_ir_cutover.py``'s ``KNOWN_BACKEND_DECLARATION_READERS``
lists this module as the only remaining checker-side reader.

Evidence-tier questions ("was there debug info at all?") are not layout
questions; they live in :mod:`abicheck.model.debug_evidence`.
"""

from __future__ import annotations

from collections.abc import Container, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..model.dwarf_facts import (
    AdvancedDwarfMetadata,
    DwarfMetadata,
    EnumInfo,
    StructLayout,
    advanced_facts_collected,
    debug_info_present,
)

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = ["DebugLayoutView", "debug_layout_view", "restrict_debug_layout"]


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
    #: Whole layout payload for the layout detector family; an empty one when
    #: the side carries none, so a detector never branches on ``None``.
    layout: DwarfMetadata
    #: Whole advanced payload (calling convention, packing, toolchain);
    #: empty when absent.
    advanced: AdvancedDwarfMetadata


def _layout_channel(snap: AbiSnapshot) -> DwarfMetadata | None:
    """The raw layout-channel payload: the single read of ``AbiSnapshot.dwarf``
    this module (and so the whole checker) performs."""
    return getattr(snap, "dwarf", None)


def debug_layout_view(snap: AbiSnapshot) -> DebugLayoutView:
    """*snap*'s :class:`DebugLayoutView`."""
    basic = _layout_channel(snap)
    advanced = getattr(snap, "dwarf_advanced", None)
    layout = basic or DwarfMetadata()
    return DebugLayoutView(
        present=debug_info_present(basic),
        records=layout.structs,
        enums=layout.enums,
        base_type_sizes=layout.base_types,
        advanced_collected=advanced_facts_collected(advanced),
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
