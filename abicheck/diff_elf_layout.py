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

from .checker_policy import ChangeKind, Confidence
from .checker_types import Change
from .demangle import demangle
from .detector_registry import registry
from .diff_helpers import make_change
from .model import AbiSnapshot, stdlib_namespaces_excluded

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


def _is_class_type_rtti(type_key: str) -> bool:
    """True iff the Itanium type encoding names a class / struct / union / enum.

    A class-type RTTI encoding starts with a digit (an unqualified source name,
    e.g. ``6Widget``), ``N`` (a nested-name ``N…E``), or ``S`` (the ``std::``
    substitution / standard abbreviations, e.g. ``St9type_info`` / ``Ss``).

    Fundamental types (``_ZTIi`` int, ``_ZTIc`` char, …) and compound types
    (``_ZTIPc`` char*, ``_ZTIKc`` const char, ``_ZTIRi`` int&, ``_ZTIA…``
    arrays, ``_ZTIF…`` functions, ``_ZTIM…`` pointer-to-member, ``_ZTID…``)
    begin with a builtin letter or a compound operator and carry **no** base
    classes — decoding their ``_ZTI`` size as inheritance shape would be
    nonsense (e.g. reporting that ``int`` gained a base class). Such symbols can
    leak from a statically-linked / re-exported runtime, so guard against them.
    """
    return bool(type_key) and (type_key[0].isdigit() or type_key[0] in ("N", "S"))


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
    *,
    skip_runtime: bool,
) -> dict[str, int]:
    """Map ``type_key → st_size`` for every ``prefix`` symbol with a size.

    ``skip_runtime`` mirrors :func:`abicheck.model.stdlib_namespaces_excluded`:
    when comparing the C++ runtime *itself* (libstdc++ / libc++) it is False, so
    the runtime's own ``_ZTVSt*`` / ``_ZTISt*`` vtables and typeinfo stay in the
    surface and their size changes are reported; otherwise those symbols are
    transitive runtime noise leaked into an ordinary library and are skipped.
    """
    elf = snap.elf
    if elf is None:
        return {}
    out: dict[str, int] = {}
    for sym in elf.symbols:
        name = sym.name
        if not name.startswith(prefix):
            continue
        if skip_runtime and _is_runtime(name):
            continue
        if sym.size <= 0:
            continue
        key = _type_key(name, prefix)
        # Only decode RTTI that actually names a class/struct/union/enum.
        # Fundamental (_ZTIi) and pointer/compound (_ZTIPc, _ZTIKc …) typeinfo
        # carry no inheritance and would otherwise be misread as class layout.
        if not _is_class_type_rtti(key):
            continue
        # First definition wins (weak vtables can appear once); ignore dupes.
        out.setdefault(key, sym.size)
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

    # When either side IS the C++ runtime (libstdc++/libc++), its own std:: RTTI
    # is the surface under test — keep it. Otherwise std:: RTTI is leaked
    # dependency noise and is filtered. Single source of truth shared with the
    # type detectors (model.stdlib_namespaces_excluded).
    skip_runtime = stdlib_namespaces_excluded(old, new)

    changes: list[Change] = []

    # ── Vtable slot count (_ZTV) ─────────────────────────────────────────────
    old_vt = _sized_rtti(old, "_ZTV", skip_runtime=skip_runtime)
    new_vt = _sized_rtti(new, "_ZTV", skip_runtime=skip_runtime)
    for key in sorted(old_vt.keys() & new_vt.keys()):
        o_size, n_size = old_vt[key], new_vt[key]
        if o_size == n_size:
            continue
        sym = "_ZTV" + key
        cls = _class_name(sym)
        o_slots = _vtable_slots(o_size, pointer_size)
        n_slots = _vtable_slots(n_size, pointer_size)
        changes.append(
            make_change(
                ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
                symbol=sym,
                name=cls,
                detail=f"~{o_slots} → ~{n_slots} virtual slots",
                old=str(o_size),
                new=str(n_size),
                # Derived from symbol size alone (no DWARF/headers): the slot
                # count is inferred, not authoritative, so label it MEDIUM.
                confidence=Confidence.MEDIUM,
            )
        )

    # ── RTTI inheritance shape (_ZTI) ────────────────────────────────────────
    old_ti = _sized_rtti(old, "_ZTI", skip_runtime=skip_runtime)
    new_ti = _sized_rtti(new, "_ZTI", skip_runtime=skip_runtime)
    for key in sorted(old_ti.keys() & new_ti.keys()):
        o_size, n_size = old_ti[key], new_ti[key]
        if o_size == n_size:
            continue
        sym = "_ZTI" + key
        cls = _class_name(sym)
        o_shape = _inheritance_shape(o_size, pointer_size)
        n_shape = _inheritance_shape(n_size, pointer_size)
        changes.append(
            make_change(
                ChangeKind.RTTI_INHERITANCE_CHANGED,
                symbol=sym,
                name=cls,
                detail=f"{o_shape} → {n_shape}",
                old=str(o_size),
                new=str(n_size),
                # Inheritance shape inferred from _ZTI symbol size alone.
                confidence=Confidence.MEDIUM,
            )
        )

    return changes
