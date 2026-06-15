# Copyright 2026 Nikolay Petrov
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

"""Fine-grained class-layout descriptor diff (layout-closure work).

The coarse type detectors compare ``sizeof`` (``TYPE_SIZE_CHANGED``) and field
offsets, but a class layout has more moving parts that those under-represent:

* a **base-class subobject** can move within the derived object (e.g. an
  empty-base optimization is lost, or a member/base is inserted ahead of it)
  without the *declaration order* of bases changing;
* a previously non-polymorphic class can gain its first virtual function, so the
  compiler **prepends a vtable pointer** — sizeof grows and every member shifts;
* a type can stop being **trivially copyable** (changing how it is passed/returned
  by value — in registers vs. via a hidden reference) or **standard-layout**
  (changing ``offsetof`` / C interop / tail-padding-reuse rules);
* the **data size** (``dsize``: the bytes the object's own members occupy,
  excluding trailing tail padding) can change while ``sizeof`` stays the same — a
  derived class may reuse a base's tail padding, so this silently shifts a
  derived layout even when the base's ``sizeof`` is unchanged.

This detector reads the optional layout fields on :class:`~abicheck.model.RecordType`
(``base_offsets``, ``vptr_offset_bits``, ``data_size_bits``, ``is_standard_layout``,
``is_trivially_copyable``) and emits the corresponding ``ChangeKind``. Every
comparison is **tri-state guarded** — it fires only when *both* sides carry the
relevant evidence — so an evidence-tier downgrade (DWARF-only or symbols-only
dump, or an older snapshot whose schema predates these fields) never fabricates a
finding. On any snapshot that doesn't populate the descriptor (all fields default
to ``None``/empty), the detector is completely inert.

``LAYOUT_UNVERIFIABLE`` is the calm, non-escalating counterpart: when one side
carries a populated layout descriptor but the other has *no* layout evidence at
all (``size_bits is None``), we cannot confirm or rule out a layout change, so we
say so without raising an alarm.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change

if TYPE_CHECKING:
    from .model import AbiSnapshot, RecordType


def _index(snap: AbiSnapshot, *, exclude_stdlib: bool) -> dict[str, RecordType]:
    """Index a snapshot's record types by name, skipping non-ABI-surface types.

    Standard-library / compiler-internal records (``std::``, ``__gnu_cxx::`` …)
    are toolchain-owned and excluded from public-surface reasoning, mirroring the
    other type detectors. ``exclude_stdlib`` is threaded from
    :func:`abicheck.model.stdlib_namespaces_excluded`: when abicheck compares the
    C++ runtime *itself* (e.g. ``libstdc++.so`` / ``libc++.so``) that toggle is
    False, so the runtime's own ``std::`` records stay in the surface and their
    layout changes are reported (Codex review #345).
    """
    from .model import is_non_abi_surface_type

    out: dict[str, RecordType] = {}
    for rec in snap.types:
        if is_non_abi_surface_type(rec.name, exclude_stdlib_namespaces=exclude_stdlib):
            continue
        out[rec.name] = rec
    return out


def _has_layout_descriptor(rec: RecordType) -> bool:
    """Return True if any v7 layout-descriptor field is populated on ``rec``.

    Used to gate ``LAYOUT_UNVERIFIABLE`` so it only activates once a dump
    actually carries the richer descriptor — keeping the detector inert on
    snapshots that predate it.
    """
    return (
        rec.data_size_bits is not None
        or rec.is_standard_layout is not None
        or rec.is_trivially_copyable is not None
        or rec.vptr_offset_bits is not None
        or bool(rec.base_offsets)
    )


def _check_base_offsets(name: str, old_rec: RecordType, new_rec: RecordType) -> list[Change]:
    """Emit BASE_CLASS_OFFSET_CHANGED for each base whose offset shifted."""
    changes: list[Change] = []
    for base, new_off in new_rec.base_offsets.items():
        old_off = old_rec.base_offsets.get(base)
        if old_off is not None and old_off != new_off:
            changes.append(
                make_change(
                    ChangeKind.BASE_CLASS_OFFSET_CHANGED,
                    symbol=name,
                    name=name,
                    detail=base,
                    old=str(old_off),
                    new=str(new_off),
                )
            )
    return changes


def _check_vptr_introduced(name: str, old_rec: RecordType, new_rec: RecordType) -> list[Change]:
    """Emit VPTR_INTRODUCED when the type gains its first virtual function.

    Use the long-standing ``vtable`` list (populated by every dump path) as
    the polymorphism witness, NOT ``size_bits``: a pre-layout-descriptor
    snapshot has ``size_bits`` set but ``vptr_offset_bits`` defaulting to
    None even for an already-polymorphic type, so keying on size would
    falsely report an introduction against such a baseline (Codex #345).
    An empty old vtable is positive evidence the old side was
    non-polymorphic; require the new side to be positively polymorphic too.
    """
    if (
        not old_rec.vtable
        and old_rec.vptr_offset_bits is None
        and new_rec.vtable
        and new_rec.vptr_offset_bits is not None
    ):
        return [
            make_change(
                ChangeKind.VPTR_INTRODUCED,
                symbol=name,
                name=name,
                old_value="non-polymorphic",
                new_value=f"vptr@{new_rec.vptr_offset_bits}",
            )
        ]
    return []


def _check_trivially_copyable_lost(name: str, old_rec: RecordType, new_rec: RecordType) -> list[Change]:
    """Emit TRIVIALLY_COPYABLE_LOST when the trait is removed."""
    if old_rec.is_trivially_copyable is True and new_rec.is_trivially_copyable is False:
        return [
            make_change(
                ChangeKind.TRIVIALLY_COPYABLE_LOST,
                symbol=name,
                name=name,
                old_value="trivially_copyable",
                new_value="non_trivially_copyable",
            )
        ]
    return []


def _check_standard_layout_lost(name: str, old_rec: RecordType, new_rec: RecordType) -> list[Change]:
    """Emit STANDARD_LAYOUT_LOST when the trait is removed."""
    if old_rec.is_standard_layout is True and new_rec.is_standard_layout is False:
        return [
            make_change(
                ChangeKind.STANDARD_LAYOUT_LOST,
                symbol=name,
                name=name,
                old_value="standard_layout",
                new_value="non_standard_layout",
            )
        ]
    return []


def _check_tail_padding_reuse(name: str, old_rec: RecordType, new_rec: RecordType) -> list[Change]:
    """Emit TAIL_PADDING_REUSE_CHANGED when dsize changes at stable sizeof."""
    if (
        old_rec.data_size_bits is not None
        and new_rec.data_size_bits is not None
        and old_rec.data_size_bits != new_rec.data_size_bits
        and old_rec.size_bits is not None
        and new_rec.size_bits is not None
        and old_rec.size_bits == new_rec.size_bits
    ):
        return [
            make_change(
                ChangeKind.TAIL_PADDING_REUSE_CHANGED,
                symbol=name,
                name=name,
                old=str(old_rec.data_size_bits),
                new=str(new_rec.data_size_bits),
                detail=str(new_rec.size_bits),
            )
        ]
    return []


def _check_layout_unverifiable(name: str, old_rec: RecordType, new_rec: RecordType) -> list[Change]:
    """Emit LAYOUT_UNVERIFIABLE when evidence is present on one side only.

    One side carries a populated layout descriptor, the other has no layout
    evidence at all (size unknown). Calm, non-escalating: we cannot confirm
    or rule out a change. Gated on the descriptor so it never fires on
    snapshots predating the v7 layout fields.
    """
    old_has = old_rec.size_bits is not None or _has_layout_descriptor(old_rec)
    new_has = new_rec.size_bits is not None or _has_layout_descriptor(new_rec)
    descriptor_in_play = _has_layout_descriptor(old_rec) or _has_layout_descriptor(new_rec)
    if descriptor_in_play and old_has != new_has:
        return [
            make_change(
                ChangeKind.LAYOUT_UNVERIFIABLE,
                symbol=name,
                name=name,
            )
        ]
    return []


@registry.detector("layout_descriptor")
def _diff_layout_descriptor(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Emit fine-grained class-layout findings from the schema-v7 descriptor.

    Each comparison is tri-state guarded (both sides must carry the evidence),
    so the detector is inert on snapshots without the layout descriptor.
    """
    from .model import stdlib_namespaces_excluded

    changes: list[Change] = []
    # Respect the runtime-self-comparison toggle: when comparing libstdc++/libc++
    # to itself this is False, keeping the runtime's own std:: records in surface.
    excl = stdlib_namespaces_excluded(old, new)
    old_idx = _index(old, exclude_stdlib=excl)
    new_idx = _index(new, exclude_stdlib=excl)

    for name, new_rec in new_idx.items():
        old_rec = old_idx.get(name)
        if old_rec is None:
            continue  # added type — handled by the structural type diff
        # Opaque/forward-declared types carry no real layout; skip them so the
        # incomplete-type detectors own that signal.
        if old_rec.is_opaque or new_rec.is_opaque:
            continue

        changes.extend(_check_base_offsets(name, old_rec, new_rec))
        changes.extend(_check_vptr_introduced(name, old_rec, new_rec))
        changes.extend(_check_trivially_copyable_lost(name, old_rec, new_rec))
        changes.extend(_check_standard_layout_lost(name, old_rec, new_rec))
        changes.extend(_check_tail_padding_reuse(name, old_rec, new_rec))
        changes.extend(_check_layout_unverifiable(name, old_rec, new_rec))

    return changes
