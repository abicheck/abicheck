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

"""Binary-only (no-DWARF / L0) C++ layout detectors.

The Itanium C++ ABI fixes the on-disk size of two emitted objects for every
polymorphic class, and both sizes encode layout facts that are otherwise only
visible in DWARF debug info:

* **vtable** (``_ZTV<type>``) — laid out as ``[offset-to-top, typeinfo*,
  slot0, slot1, …]``.  Its ``st_size`` therefore grows or shrinks by one
  pointer for every virtual function added, removed, or (net) reordered.
  ``slots ≈ size/pointer_size − 2`` for the primary vtable.

* **typeinfo** (``_ZTI<type>``) — its concrete runtime class encodes the
  inheritance shape:

  =====================  =============================  ==================
  Runtime class          Size (64-bit)                  Meaning
  =====================  =============================  ==================
  ``__class_type_info``  2 words (16 B)                 no base classes
  ``__si_class_type_info`` 3 words (24 B)               exactly one public,
                                                        non-virtual base
  ``__vmi_class_type_info`` ≥ 4 words                   multiple / virtual /
                                                        non-public bases
  =====================  =============================  ==================

This means a virtual-method change or a base-class change is observable from
``.dynsym`` symbol sizes **alone** — no debug info, no headers.  That closes
the blind spot a pure symbol-name dump has: swapping a member's type or adding
a virtual method need not rename any mangled symbol, yet it does resize the
class's ``_ZTV`` / ``_ZTI`` object.

Scope: this detector only fires when the *same* ``_ZTV`` / ``_ZTI`` symbol is
present on **both** sides with a **different** size.  A vtable/typeinfo object
that only appears or only disappears is a symbol add/remove already reported by
the generic ELF symbol diff (and, for the class as a whole, by the type
add/remove detectors), so handling it here too would double-count.

See ADR-020b's sibling discussion of evidence tiers; these are L0 signals.
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .demangle import demangle
from .detector_registry import registry
from .model import AbiSnapshot

# Runtime/standard-library RTTI we never want to flag — these belong to
# libstdc++ / libc++ / the Itanium runtime, not to the library under test.
# Matched against the full mangled symbol name.
_RUNTIME_RTTI_PREFIXES: tuple[str, ...] = (
    "_ZTVN10__cxxabiv",
    "_ZTIN10__cxxabiv",
    "_ZTSN10__cxxabiv",
    "_ZTVSt",
    "_ZTISt",
    "_ZTSSt",
    "_ZTVNSt",
    "_ZTINSt",
    "_ZTSNSt",
    "_ZTVN9__gnu_cxx",
    "_ZTIN9__gnu_cxx",
    "_ZTSN9__gnu_cxx",
)


def _type_key(name: str, prefix: str) -> str:
    """Mangled type encoding that identifies the class (``_ZTV4Base`` → ``4Base``)."""
    return name[len(prefix) :]


def _is_runtime(name: str) -> bool:
    return name.startswith(_RUNTIME_RTTI_PREFIXES)


def _class_name(mangled: str) -> str:
    """Human-readable class name for a vtable/typeinfo symbol, best-effort."""
    dem = demangle(mangled)
    if dem:
        # "vtable for Foo" / "typeinfo for Foo" → "Foo"
        for marker in (" for ",):
            if marker in dem:
                return dem.split(marker, 1)[1]
        return dem
    return mangled


def _sized_rtti(
    snap: AbiSnapshot,
    prefix: str,
) -> dict[str, int]:
    """Map ``type_key → st_size`` for every non-runtime ``prefix`` symbol with a size."""
    elf = snap.elf
    if elf is None:
        return {}
    out: dict[str, int] = {}
    for sym in elf.symbols:
        name = sym.name
        if not name.startswith(prefix):
            continue
        if _is_runtime(name):
            continue
        if sym.size <= 0:
            continue
        # First definition wins (weak vtables can appear once); ignore dupes.
        out.setdefault(_type_key(name, prefix), sym.size)
    return out


def _vtable_slots(size_bytes: int, pointer_size: int) -> int:
    """Approximate primary-vtable slot count (``size/ptr − 2``), floored at 0."""
    if pointer_size <= 0:
        pointer_size = 8
    return max(0, size_bytes // pointer_size - 2)


def _inheritance_shape(size_bytes: int, pointer_size: int) -> str:
    """Describe the inheritance shape implied by a typeinfo object's size."""
    if pointer_size <= 0:
        pointer_size = 8
    words = size_bytes // pointer_size
    if words <= 2:
        return "no base class (__class_type_info)"
    if words == 3:
        return "single base class (__si_class_type_info)"
    # __vmi_class_type_info: header is vptr + name + (flags,count) word, then
    # 2 words per base on LP64. base_count is best-effort (LP64 layout).
    base_count = max(2, (words - 3) // 2)
    return f"{base_count} base classes (__vmi_class_type_info)"


@registry.detector(
    "elf_layout",
    requires_support=lambda o, n: (
        o.elf is not None and n.elf is not None,
        "missing ELF metadata on one side",
    ),
)
def _diff_elf_layout(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Binary-only vtable / RTTI layout change detector (no DWARF needed)."""
    assert old.elf is not None and new.elf is not None  # guaranteed by requires_support
    pointer_size = new.elf.pointer_size or old.elf.pointer_size or 8

    changes: list[Change] = []

    # ── Vtable slot count (_ZTV) ─────────────────────────────────────────────
    old_vt = _sized_rtti(old, "_ZTV")
    new_vt = _sized_rtti(new, "_ZTV")
    for key in sorted(old_vt.keys() & new_vt.keys()):
        o_size, n_size = old_vt[key], new_vt[key]
        if o_size == n_size:
            continue
        sym = "_ZTV" + key
        cls = _class_name(sym)
        o_slots = _vtable_slots(o_size, pointer_size)
        n_slots = _vtable_slots(n_size, pointer_size)
        changes.append(
            Change(
                kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
                symbol=sym,
                description=(
                    f"Vtable for '{cls}' changed size: {o_size} → {n_size} bytes "
                    f"(~{o_slots} → ~{n_slots} virtual slots). A virtual method was "
                    f"added, removed, or reordered; existing binaries dispatch through "
                    f"fixed vtable offsets and will call the wrong slot. Detected from "
                    f"the ELF symbol size without debug info."
                ),
                old_value=str(o_size),
                new_value=str(n_size),
            )
        )

    # ── RTTI inheritance shape (_ZTI) ────────────────────────────────────────
    old_ti = _sized_rtti(old, "_ZTI")
    new_ti = _sized_rtti(new, "_ZTI")
    for key in sorted(old_ti.keys() & new_ti.keys()):
        o_size, n_size = old_ti[key], new_ti[key]
        if o_size == n_size:
            continue
        sym = "_ZTI" + key
        cls = _class_name(sym)
        o_shape = _inheritance_shape(o_size, pointer_size)
        n_shape = _inheritance_shape(n_size, pointer_size)
        changes.append(
            Change(
                kind=ChangeKind.RTTI_INHERITANCE_CHANGED,
                symbol=sym,
                description=(
                    f"RTTI typeinfo for '{cls}' changed size: {o_size} → {n_size} bytes "
                    f"({o_shape} → {n_shape}). The base-class shape changed, which shifts "
                    f"this-pointer adjustments, member offsets, and the vtable. Detected "
                    f"from the ELF symbol size without debug info."
                ),
                old_value=str(o_size),
                new_value=str(n_size),
            )
        )

    return changes
