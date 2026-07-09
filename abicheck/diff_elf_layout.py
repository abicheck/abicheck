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

import re

from .checker_policy import ChangeKind, Confidence
from .checker_types import Change
from .demangle import demangle
from .detector_registry import registry
from .diff_helpers import make_change
from .model import AbiSnapshot, stdlib_namespaces_excluded
from .name_classification import STDLIB_RTTI_PREFIXES as _RUNTIME_RTTI_PREFIXES


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


# ── G23 Phase B1 — Itanium thunk / VTT surface (L0) ──────────────────────────
# Virtual-override thunks encode a `this`-pointer adjustment in their mangled
# name: `_ZThn<off>_<base>` (non-virtual), `_ZTv<o1>_<o2>_<base>` (virtual),
# `_ZTc<call-off><call-off>_<base>` (covariant, whose two call-offsets each carry
# their own `h`/`v` adjustment-kind letter, e.g. `_ZTch0_h8_N1D5cloneEv`). The
# base is the target method's mangled encoding, stable across versions; the
# offset is what shifts when a base subobject moves. The three thunk kinds carry
# a different NUMBER of `_`-separated offset components, so a single non-greedy
# split cannot find the base boundary reliably — a virtual thunk's second offset
# starts with a digit (`_ZTv0_12_N…`) and would be mistaken for the base. Each
# kind is therefore parsed with its own call-offset grammar (Itanium §5.1.4.2):
#   h  non-virtual : h <nv-offset> _                      → one component
#   v  virtual     : v <offset> _ <virtual-offset> _      → two components
#   c  covariant   : c <call-offset> <call-offset> _base  → two h/v call-offsets
# `_ZTV`/`_ZTI`/`_ZTS`/`_ZTT`/`_ZTC` start with an uppercase letter, so the
# lowercase `[hvc]` marker never collides with them.
_THUNK_H_RE = re.compile(r"^_ZTh(?P<offset>n?\d+)_(?P<base>.+)$")
_THUNK_V_RE = re.compile(r"^_ZTv(?P<offset>n?\d+_n?\d+)_(?P<base>.+)$")
# One covariant call-offset: `h<nv-offset>_` or `v<offset>_<virtual-offset>_`.
_THUNK_C_CALL = r"(?:hn?\d+_|vn?\d+_n?\d+_)"
_THUNK_C_RE = re.compile(rf"^_ZTc(?P<offset>{_THUNK_C_CALL}{_THUNK_C_CALL})(?P<base>.+)$")


def _parse_thunk(name: str) -> tuple[str, str] | None:
    """Split a thunk symbol into ``(base_encoding, offset_signature)`` or None.

    ``base_encoding`` identifies the target method (stable across versions);
    ``offset_signature`` (kind + offset) is what a base-subobject move changes.
    Each kind is matched with its own grammar so the base is extracted correctly
    even when an offset component (or the base) starts with a digit.
    """
    if name.startswith("_ZTh"):
        m = _THUNK_H_RE.match(name)
        return (m.group("base"), f"h:{m.group('offset')}") if m else None
    if name.startswith("_ZTv"):
        m = _THUNK_V_RE.match(name)
        return (m.group("base"), f"v:{m.group('offset')}") if m else None
    if name.startswith("_ZTc"):
        m = _THUNK_C_RE.match(name)
        if m is None:
            return None
        # Drop the trailing `_` between the last call-offset and the base.
        return m.group("base"), f"c:{m.group('offset').rstrip('_')}"
    return None


def _base_is_runtime(base: str) -> bool:
    """True if a thunk's target method belongs to the std:: runtime."""
    # A std:: member nests as `NSt…`/`NKSt…`, or appears via the `St`/`Ss`/`Si`/
    # `So` substitution abbreviations for std:: names.
    return base.startswith(("NSt", "NKSt", "St", "Ss", "Si", "So"))


def _thunks_by_base(
    snap: AbiSnapshot, *, skip_runtime: bool
) -> dict[str, set[str]]:
    """Map ``base_encoding → {offset_signatures}`` for every thunk symbol."""
    elf = snap.elf
    if elf is None:
        return {}
    out: dict[str, set[str]] = {}
    for sym in elf.symbols:
        parsed = _parse_thunk(sym.name)
        if parsed is None:
            continue
        base, off_sig = parsed
        if skip_runtime and _base_is_runtime(base):
            continue
        out.setdefault(base, set()).add(off_sig)
    return out


def _method_name(base: str) -> str:
    """Human-readable name for a thunk's target method (``_Z`` + base)."""
    return _class_name("_Z" + base) if not base.startswith("_Z") else _class_name(base)


def _diff_thunks(old: AbiSnapshot, new: AbiSnapshot, *, skip_runtime: bool) -> list[Change]:
    """Detect thunk offset / set drift (B1)."""
    old_t = _thunks_by_base(old, skip_runtime=skip_runtime)
    new_t = _thunks_by_base(new, skip_runtime=skip_runtime)
    old_syms = old.elf.symbol_map if old.elf else {}
    new_syms = new.elf.symbol_map if new.elf else {}
    changes: list[Change] = []

    # Offset change: the target method has a thunk on both sides but the encoded
    # this-adjustment offset(s) differ — a base subobject moved.
    for base in sorted(old_t.keys() & new_t.keys()):
        o_offs, n_offs = old_t[base], new_t[base]
        if o_offs == n_offs:
            continue
        changes.append(
            make_change(
                ChangeKind.VTABLE_THUNK_OFFSET_CHANGED,
                # Report the target method's own mangled symbol (`_Z`+base) — a
                # real, stable identifier — rather than fabricating a `_ZTh…`
                # non-virtual-thunk name that misstates the kind for a v/c thunk
                # (the true kind lives in the offset signatures below).
                symbol="_Z" + base,
                name=_method_name(base),
                old=", ".join(sorted(o_offs)),
                new=", ".join(sorted(n_offs)),
                confidence=Confidence.MEDIUM,
            )
        )

    # Set change: a method whose *plain* symbol persists across versions gained
    # or lost a thunk entirely (secondary-base override added/removed). Requiring
    # the plain method symbol on both sides avoids double-counting a pure thunk
    # symbol add/remove that rides along with the method itself changing.
    for base in sorted(old_t.keys() ^ new_t.keys()):
        plain = "_Z" + base
        if plain not in old_syms or plain not in new_syms:
            continue
        gained = base in new_t
        changes.append(
            make_change(
                ChangeKind.VTABLE_THUNK_SET_CHANGED,
                symbol=plain,
                name=_method_name(base),
                detail="thunk added" if gained else "thunk removed",
                old="(none)" if gained else ", ".join(sorted(old_t[base])),
                new=", ".join(sorted(new_t[base])) if gained else "(none)",
                confidence=Confidence.MEDIUM,
            )
        )

    return changes


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

    # ── VTT slot count (_ZTT) ────────────────────────────────────────────────
    # The virtual-table-table's size encodes the number of construction
    # sub-vtables; a change means the virtual-inheritance shape changed (B1).
    old_vtt = _sized_rtti(old, "_ZTT", skip_runtime=skip_runtime)
    new_vtt = _sized_rtti(new, "_ZTT", skip_runtime=skip_runtime)
    for key in sorted(old_vtt.keys() & new_vtt.keys()):
        o_size, n_size = old_vtt[key], new_vtt[key]
        if o_size == n_size:
            continue
        sym = "_ZTT" + key
        changes.append(
            make_change(
                ChangeKind.VTT_SLOT_COUNT_CHANGED,
                symbol=sym,
                name=_class_name(sym),
                detail=f"~{o_size // pointer_size} → ~{n_size // pointer_size} sub-vtables",
                old=str(o_size),
                new=str(n_size),
                confidence=Confidence.MEDIUM,
            )
        )

    # ── Thunk offset / set drift (_ZTh / _ZTv / _ZTc) ────────────────────────
    changes.extend(_diff_thunks(old, new, skip_runtime=skip_runtime))

    return changes
