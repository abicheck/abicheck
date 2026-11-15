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

"""What a by-value aggregate's DWARF says about its passing convention, and
how two value-ABI trait fingerprints are compared.

Moved out of ``dwarf_advanced.py`` (at its no-growth ratchet). A trait
fingerprint is ``"ret:<t>|p0:<t>|..."`` with ``<t>`` one of ``trivial``,
``nontrivial`` or :data:`VALUE_ABI_TRAIT_UNKNOWN`.

Unknown is the answer for an aggregate whose definition the unit does not
carry: clang's type homing writes ``std::string`` as a declaration-only DIE
in a unit that only uses libstdc++'s ``extern template`` instantiation, and
a full definition once ``-O2`` inlines its members. Reading the former as
trivial turned an ``-O0`` -> ``-O2`` rebuild into a register<->sret flip on
every function returning ``std::string``.
"""

from __future__ import annotations

import re
from typing import Any

from ..dwarf_utils import attr_str, resolve_type_die

__all__ = [
    "SYSV_MAX_REGISTER_RETURN_BYTES",
    "VALUE_ABI_TRAIT_UNKNOWN",
    "comparable_traits",
    "declared_triviality",
    "is_copy_or_move_ctor",
    "ret_component",
    "returns_in_registers",
]

#: The trait of a by-value aggregate whose definition this unit does not carry.
VALUE_ABI_TRAIT_UNKNOWN = "unknown"

#: ``DW_AT_calling_convention`` values a *type* carries (DWARF 5 §5.7.1).
_DW_CC_PASS_BY_REFERENCE = 0x4
_DW_CC_PASS_BY_VALUE = 0x5

#: SysV AMD64 returns a trivial aggregate in registers only up to two
#: eightbytes (<= 16 bytes); larger aggregates are returned via a hidden pointer
#: regardless of triviality. Used to gate the return-convention classification.
SYSV_MAX_REGISTER_RETURN_BYTES = 16


def declared_triviality(type_die: Any) -> str | None:
    """The triviality *type_die* states itself, or ``None`` to derive it.

    The producer's own ``DW_AT_calling_convention`` on the type is exact:
    ``DW_CC_pass_by_reference`` is "not trivial for the purposes of calls",
    ``DW_CC_pass_by_value`` is "trivial". A declaration-only DIE (no
    ``DW_AT_byte_size``) carries no members, bases or size:
    :data:`VALUE_ABI_TRAIT_UNKNOWN`.
    """
    attrs = type_die.attributes
    type_cc = attrs.get("DW_AT_calling_convention")
    if type_cc is not None:
        if int(type_cc.value) == _DW_CC_PASS_BY_REFERENCE:
            return "nontrivial"
        if int(type_cc.value) == _DW_CC_PASS_BY_VALUE:
            return "trivial"
    declaration = attrs.get("DW_AT_declaration")
    if declaration is not None and bool(declaration.value):
        if "DW_AT_byte_size" not in attrs:
            return VALUE_ABI_TRAIT_UNKNOWN
    return None


def comparable_traits(old_trait: str, new_trait: str) -> tuple[str, str]:
    """*old_trait* and *new_trait* without each slot either side reads as
    :data:`VALUE_ABI_TRAIT_UNKNOWN`: an unknown triviality is no evidence of a
    change, in either direction (the other slots are still compared)."""

    def slots(trait: str) -> dict[str, str]:
        return dict(part.split(":", 1) for part in trait.split("|") if ":" in part)

    old_slots, new_slots = slots(old_trait), slots(new_trait)
    unknown = {
        k
        for k, v in (*old_slots.items(), *new_slots.items())
        if v == VALUE_ABI_TRAIT_UNKNOWN
    }
    if not unknown:
        return old_trait, new_trait

    def join(d: dict[str, str]) -> str:
        return "|".join(f"{k}:{v}" for k, v in d.items() if k not in unknown)

    return join(old_slots), join(new_slots)


def returns_in_registers(
    ret_component: str | None, size: int | None, memory_forced: bool = False
) -> bool:
    """Whether a by-value aggregate return is passed in registers (SysV AMD64).

    A struct is register-returned only when it is **trivial for the purposes of
    calls**, fits in two eightbytes (<= 16 bytes), *and* has no unaligned member.
    A non-trivial aggregate, a large one, or one with an unaligned member (e.g. a
    packed struct, ``memory_forced``) is memory-returned via a hidden sret
    pointer. An unknown size on an otherwise-eligible trivial aggregate is
    treated as register-eligible (stay conservative — preserves the pre-size-
    gating behaviour for snapshots/mocks that carry no size).
    """
    if ret_component is None or memory_forced:
        return False
    # Component is the triviality token: "trivial"/"nontrivial" (or the mock
    # "v(trivial)"/"v(nontrivial)"). It is non-trivial iff it says so.
    if "nontrivial" in ret_component:
        return False
    return size is None or size <= SYSV_MAX_REGISTER_RETURN_BYTES


def ret_component(trait: str) -> str | None:
    """Extract the ``ret:`` component of a value-ABI trait fingerprint.

    Trait strings look like ``"ret:trivial|p0:nontrivial"``; returns the value
    after ``ret:`` (e.g. ``"trivial"``) or ``None`` when the function has no
    by-value aggregate return component.
    """
    for part in trait.split("|"):
        if part.startswith("ret:"):
            return part[4:]
    return None


#: A constructor of the enclosing class taking ``[cv] X&`` or ``[cv] X&&``
#: (Itanium: ``C1``/``C2``/``C4`` then ``R``/``O``, cv, and ``S_`` -- the class).
_OWN_REF_CTOR = re.compile(r"C[124]E(?:R|O)V?K?S_$")

_CV_TAGS = ("DW_TAG_const_type", "DW_TAG_volatile_type", "DW_TAG_typedef")


def is_copy_or_move_ctor(ch: Any, type_die: Any, class_name: str, CU: Any) -> bool:
    """Whether member *ch* of *type_die* is a copy or move constructor.

    Only these (with a destructor) make a class non-trivial for calls; a
    converting or default constructor does not (Itanium C++ ABI 3.1.2). gcc
    names the in-class declaration with the unified ``C4`` constructor and
    clang with ``C1``/``C2``, so the shape is read from the one explicit
    parameter -- a reference to the class itself -- not from the variant.
    Without a *CU* the parameter cannot be resolved and the mangled name's
    own-class reference (``S_``) decides.
    """
    if attr_str(ch, "DW_AT_name") != class_name:
        return False
    if CU is None:
        linkage = attr_str(ch, "DW_AT_linkage_name") or ""
        return _OWN_REF_CTOR.search(linkage) is not None
    params = [
        p
        for p in ch.iter_children()
        if p.tag == "DW_TAG_formal_parameter"
        and not int(getattr(p.attributes.get("DW_AT_artificial"), "value", 0) or 0)
    ]
    if len(params) != 1:
        return False
    ref = resolve_type_die(params[0], CU)
    if ref is None or ref.tag not in (
        "DW_TAG_reference_type",
        "DW_TAG_rvalue_reference_type",
    ):
        return False
    target = resolve_type_die(ref, CU)
    for _ in range(8):
        if target is None or target.tag not in _CV_TAGS:
            break
        target = resolve_type_die(target, CU)
    if target is None:
        return False
    return target.offset == type_die.offset or (
        target.tag == type_die.tag and attr_str(target, "DW_AT_name") == class_name
    )
