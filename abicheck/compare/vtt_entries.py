# SPDX-License-Identifier: Apache-2.0
"""Detect a same-size Itanium VTT whose entries point somewhere else.

``vtt_slot_count_changed`` (``diff_elf_layout.py``) compares a VTT's size
only. A VTT holds pointers to vtable address points; constructors of a class
with virtual bases install them while the object is built. Keep the size and
retarget one entry -- from a construction vtable to the virtual base's own
vtable, say -- and an old client's virtual call during construction reaches a
different override, while a fully built object behaves as before.

Each entry is compared by its *logical* target, the table it points into and
the offset of the address point inside that table
(``model/elf_facts.VttEntryTarget``, read by ``extract/elf_vtt_entries.py``).
Addresses, addends and relocation forms that change on an ordinary relink are
resolved away before the comparison, so moving the table is no change.

An entry whose target is unknown on either side is never reported as
changed: the VTT is recorded as declined, so the run's assurance names it. A
snapshot that never read the entries (written before the fact existed, or a
non-x86-64 ELF) declines each matched VTT the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..demangle import demangle
from ..detector_registry import registry
from ..diff_helpers import make_change
from ..model.change_catalog.kinds import ChangeKind
from ..model.cxx_artifact_symbols import cxx_class_artifact_kind
from ..model.evidence_status import Confidence
from .declined_comparisons import record_declined

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from ..model.change import Change
    from ..model.elf_facts import ElfMetadata, VttEntryTarget

__all__ = ["describe_target"]


def _vtts(elf: ElfMetadata) -> dict[str, int]:
    """Exported VTTs and their sizes. A runtime (``std::``) VTT a library
    emits is kept: its entries are compared by the same logical target, so
    it can only differ when that library's copy really changed."""
    return {
        s.name: s.size
        for s in elf.symbols
        if s.size > 0 and cxx_class_artifact_kind(s.name) == "VTT"
    }


def describe_target(target: VttEntryTarget | None) -> str:
    """``construction vtable for L-in-D (_ZTC1D0_1L) + 56`` -- or ``unknown``."""
    if target is None:
        return "unknown"
    readable = demangle(target.table)
    label = f"{readable} ({target.table})" if readable else target.table
    return f"{label} + {target.offset}"


def _owner(vtt: str) -> str:
    readable = demangle(vtt) or ""
    return readable.split(" for ", 1)[1] if " for " in readable else vtt


@registry.detector("elf_vtt_entries")
def _diff_vtt_entries(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """One finding per VTT entry whose logical target changed."""
    if old.elf is None or new.elf is None:
        # VTT entries are read from ELF relocations only; a PE or Mach-O pair
        # has no fact here to compare (its VTT size is elf_layout's).
        return []
    old_vtts = _vtts(old.elf)
    new_vtts = _vtts(new.elf)
    word = new.elf.pointer_size or 8
    old_map, new_map = old.elf.vtt_entries, new.elf.vtt_entries
    changes: list[Change] = []
    for vtt in sorted(old_vtts.keys() & new_vtts.keys()):
        if old_vtts[vtt] != new_vtts[vtt]:
            continue  # a size change is vtt_slot_count_changed's finding
        if old_map is None or new_map is None:
            side = "old" if old_map is None else "new"
            record_declined(
                vtt,
                f"VTT entry targets were not read on the {side} side (a "
                "snapshot written before they were recorded, or not x86-64 ELF)",
            )
            continue
        o_entries, n_entries = old_map.get(vtt), new_map.get(vtt)
        expected = new_vtts[vtt] // word
        if (
            o_entries is None
            or n_entries is None
            or len(o_entries) != expected
            or len(n_entries) != expected
        ):
            record_declined(
                vtt, "VTT entry targets were not read for every slot of this VTT"
            )
            continue
        unknown = []
        for index, (o, n) in enumerate(zip(o_entries, n_entries, strict=True)):
            if o is None or n is None:
                unknown.append(index)
                continue
            if o == n:
                continue
            owner = _owner(vtt)
            changes.append(
                make_change(
                    ChangeKind.VTT_ENTRY_TARGET_CHANGED,
                    symbol=vtt,
                    name=owner,
                    qualified_name=owner,
                    detail=(
                        f"entry {index} (byte offset {index * word}) now points "
                        f"to {describe_target(n)} instead of "
                        f"{describe_target(o)}; the VTT size is unchanged, so "
                        "only virtual dispatch while an object of this class "
                        "is being constructed or destroyed differs"
                    ),
                    old=f"{o.table}+{o.offset}",
                    new=f"{n.table}+{n.offset}",
                    review_evidence={
                        "kind": "elf_vtt_entry_target",
                        "entry_index": index,
                        "byte_offset": index * word,
                        "old_table": o.table,
                        "old_address_point_offset": o.offset,
                        "new_table": n.table,
                        "new_address_point_offset": n.offset,
                    },
                    confidence=Confidence.HIGH,
                )
            )
        if unknown:
            record_declined(
                vtt,
                f"VTT entries {unknown} have no resolvable target on one side "
                "(no symbol covers the relocated address, e.g. a stripped "
                "local construction vtable, or an unsupported relocation)",
            )
    return changes
