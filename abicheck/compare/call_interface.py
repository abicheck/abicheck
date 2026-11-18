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

"""Does an old caller still call the new function correctly? (ADR-094 D8)

:func:`assess_call_interface` answers ADR-094's ``call`` axis for one
function whose signature changed, on the x86-64 System V convention
(:mod:`abicheck.model.call_interface`):

* ``PRESERVED`` -- every argument the new function reads is in the register
  the old caller wrote it to, in the same representation; no parameter was
  inserted; a hidden sret pointer neither appeared nor disappeared; and the
  old caller reads nothing (``void``) or reads the result where, and in the
  form, the new function leaves it.
* ``BROKEN`` -- the model says some argument or the result is not intact.
* ``UNKNOWN`` -- the signature is outside the modelled domain: another ABI, a
  variadic or unprototyped function, a slot without a modelled
  representation, more arguments than registers, or parameters that cannot
  be matched by name.

Old and new parameters correspond by name, which is how a parameter keeps its
identity across an edit (the fragments' ``p0, p1, …`` and ``ins``). A new
parameter whose name the old function did not have is an insertion: it is
read from a register the old caller never set, so the call is ``BROKEN``. A
rename is indistinguishable from an insertion plus a removal; it is reported
as one, which only ever errs towards ``BROKEN``.

A non-static member function's implicit ``this`` is the leading slot
``this`` (:class:`ImplicitThis`): on Itanium x86-64 it is passed exactly as a
first declared pointer argument, after any hidden sret pointer, so the proved
``locAt``/``argLoc`` placement covers it unchanged (``formal/MEMBER_FRAGMENT.md``,
confirmed by execution). Whether a function is such a member is read from
``Function.member_kind_fact``; without it the axis is ``UNKNOWN``.

The answer is checked case by case against the proved model on every case of
the E13 and E14 fragments (``tests/test_call_interface_fragments.py``). This
module decides no verdict; ADR-094 D9's shadow comparison and switch do.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..model.call_interface import (
    MAX_INTEGER_ARGS,
    MAX_SSE_ARGS,
    SYSV_AMD64,
    ArgClass,
    ArgSlot,
    Rep,
    RetShape,
    arg_locations,
    ret_location,
    ret_shape_of,
    slot_rep,
)
from ..model.cc_attributes import is_cc_attribute
from ..model.declarations import Function
from ..model.availability import FactStatus
from ..model.member_kind import MemberKind
from ..model.owner_recovery import is_mangled_identity
from ..model.snapshot import AbiSnapshot

__all__ = [
    "AxisStatus",
    "CallAxis",
    "CallSignature",
    "ImplicitThis",
    "assess_call_interface",
    "call_signature_of",
    "linkage_evidence_reliable",
]


class AxisStatus(StrEnum):
    """An ADR-094 axis value (D2). ``NOT_APPLICABLE`` is not produced here."""

    PRESERVED = "preserved"
    BROKEN = "broken"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CallAxis:
    """The ``call`` axis for one function, with the reason that decided it."""

    status: AxisStatus
    reason: str


class ImplicitThis(StrEnum):
    """Whether a call passes an implicit ``this`` that ``params`` does not list.

    ``PRESENT`` for a non-static member function: on x86-64 SysV (Itanium C++
    ABI) ``this`` is an object pointer passed exactly like a first declared
    pointer argument -- the first INTEGER register, or the second after a
    hidden sret pointer (``formal/MEMBER_FRAGMENT.md``). ``ABSENT`` for a free
    or static member function. ``UNKNOWN`` when the evidence cannot say.
    """

    ABSENT = "absent"
    PRESENT = "present"
    UNKNOWN = "unknown"


#: The name the implicit ``this`` takes among the slots. A C++ keyword, so no
#: declared parameter can carry it; a function that gains or loses ``this``
#: therefore gains or loses a parameter.
THIS_SLOT = "this"


@dataclass(frozen=True, slots=True)
class CallSignature:
    """A signature reduced to what the convention sees.

    ``params`` is ``None`` when any parameter is outside the modelled domain,
    ``ret`` is ``None`` when the return is, and ``variadic`` is ``None`` when
    it is unknown whether the function takes ``...``. ``convention`` is ``""``
    for the platform default, a calling-convention attribute (``ms_abi``) when
    one is declared, and ``None`` when attributes were not captured.
    ``implicit_this`` says whether the call passes an implicit ``this``;
    when it is ``PRESENT``, ``params`` starts with the ``this`` slot.
    ``unknown_reason`` says why it is ``UNKNOWN``.
    """

    params: tuple[ArgSlot, ...] | None
    ret: RetShape | None
    variadic: bool | None
    convention: str | None = ""
    implicit_this: ImplicitThis = ImplicitThis.ABSENT
    unknown_reason: str = ""


#: The extractor generation from which the clang header backend records a
#: class member declared inside an ``extern "C"`` block as C++ linkage
#: ([dcl.link]/4). Earlier clang-produced snapshots can carry
#: ``is_extern_c=True`` (and, with an asm label, an unmangled symbol) for such
#: a member, so their linkage flag cannot exclude a member.
CLANG_MEMBER_LINKAGE_GENERATION = 84


def linkage_evidence_reliable(snapshot: AbiSnapshot) -> bool:
    """Whether *snapshot*'s ``is_extern_c`` flags reliably exclude members.

    True for a snapshot extracted by this process (``fact_generations`` is
    ``None``), for one extracted at or after
    :data:`CLANG_MEMBER_LINKAGE_GENERATION`, and for a castxml snapshot (never
    affected). A caller passes the result to :func:`call_signature_of`.
    """
    gens = snapshot.fact_generations
    if gens is None or gens[0] >= CLANG_MEMBER_LINKAGE_GENERATION:
        return True
    return snapshot.ast_producer == "castxml"


def call_signature_of(
    fn: Function, ret: RetShape | None = None, *, linkage_reliable: bool
) -> CallSignature:
    """Reduce *fn* to a :class:`CallSignature` from its recorded facts.

    *ret* overrides the return shape for a struct return whose classification
    the caller knows; otherwise a scalar or ``void`` return is read from the
    return slot's ABI fact. *linkage_reliable* is required, and is
    :func:`linkage_evidence_reliable` of the snapshot *fn* came from: there is
    no default, so a caller cannot forget it and trust a stale flag. When it
    is false, no function is taken to have C linkage.
    """
    convention = _convention(fn.contract_attributes)
    this, why = _implicit_this(fn, linkage_reliable)
    if ret is None:
        ret = ret_shape_of(
            fn.return_type_abi_fact, fn.return_pointer_depth, fn.return_type
        )
    slots = [ArgSlot(THIS_SLOT, Rep.I64)] if this is ImplicitThis.PRESENT else []
    params: tuple[ArgSlot, ...] | None = None
    for p in fn.params:
        rep = slot_rep(p.type_abi_fact, p.pointer_depth, p.type)
        if rep is None:
            break
        slots.append(ArgSlot(p.name, rep))
    else:
        params = tuple(slots)
    return CallSignature(params, ret, fn.is_variadic, convention, this, why)


def _implicit_this(fn: Function, linkage_reliable: bool) -> tuple[ImplicitThis, str]:
    """Whether *fn*'s call passes an implicit ``this``, and why not known.

    A C-linkage function (``is_extern_c`` AND an unmangled symbol: a linkage
    specification never changes a member's ``_Z`` symbol) has none. Otherwise
    only ``member_kind_fact`` decides: no older recorded fact identifies a
    member (``is_static`` means internal linkage on a free function, the name
    is the leaf, an uninstantiated template method's symbol is its bare name).
    A virtual function is modelled like any non-static member: a call through
    the vtable passes the same registers, and where the slot sits is the
    vtable's concern, not this axis's. ``const``/``volatile``/ref-qualifiers
    do not change the argument registers (they change the symbol, a ``load``
    axis question). Constructors and destructors stay unknown: their symbol
    variants (C1/C2, D0/D1/D2) differ, and the base-object variant of a class
    with virtual bases takes a hidden VTT argument after ``this``.
    """
    if linkage_reliable and fn.is_extern_c and not is_mangled_identity(fn.mangled):
        return ImplicitThis.ABSENT, ""
    fact = fn.member_kind_fact
    if fact is None or fact.status is not FactStatus.PRESENT:
        return ImplicitThis.UNKNOWN, (
            "member kind not recorded: a possible member function's implicit this is not modelled"
        )
    kind = fact.value
    if kind in (MemberKind.FREE, MemberKind.STATIC):
        return ImplicitThis.ABSENT, ""
    if kind is MemberKind.INSTANCE:
        return ImplicitThis.PRESENT, ""
    return ImplicitThis.UNKNOWN, (
        f"a {kind}: ABI variants and a possible VTT argument are not modelled"
    )


def _convention(attributes: list[str] | None) -> str | None:
    if attributes is None:
        return None
    declared = sorted(
        {a.split("(", 1)[0] for a in attributes if is_cc_attribute(a)} - {"sysv_abi"}
    )
    return ",".join(declared)


def _unknown(why: str) -> CallAxis:
    return CallAxis(AxisStatus.UNKNOWN, why)


def _domain_gap(old: CallSignature, new: CallSignature, abi: str) -> str | None:
    if abi != SYSV_AMD64:
        return f"ABI {abi or 'unknown'} is not modelled"
    for sig in (old, new):
        if sig.convention is None:
            return "calling-convention attributes not captured"
        if sig.convention:
            return f"non-default calling convention ({sig.convention})"
        if sig.implicit_this is ImplicitThis.UNKNOWN:
            return sig.unknown_reason or "implicit this unknown"
    if old.variadic is not False or new.variadic is not False:
        return "variadic or unprototyped function"
    if old.params is None or new.params is None:
        return "a parameter type outside the modelled domain"
    if old.ret is None or new.ret is None:
        return "a return type outside the modelled domain"
    for sig in (old, new):
        assert sig.params is not None
        names = [s.name for s in sig.params]
        if any(not n for n in names) or len(set(names)) != len(names):
            return "unnamed or duplicate parameter names"
        reps = [s.rep for s in sig.params]
        hidden = 1 if sig.ret is not None and sig.ret.is_sret else 0
        n_int = hidden + sum(r.arg_class is ArgClass.INTEGER for r in reps)
        if n_int > MAX_INTEGER_ARGS or len(reps) + hidden - n_int > MAX_SSE_ARGS:
            return "more arguments than registers (stack passing)"
    return None


def assess_call_interface(old: CallSignature, new: CallSignature, abi: str) -> CallAxis:
    """ADR-094's ``call`` axis for a function changed from *old* to *new*."""
    gap = _domain_gap(old, new, abi)
    if gap is not None:
        return _unknown(gap)
    assert old.params is not None and new.params is not None
    assert old.ret is not None and new.ret is not None
    old_locs = arg_locations([s.rep for s in old.params], old.ret.is_sret)
    new_locs = arg_locations([s.rep for s in new.params], new.ret.is_sret)
    old_at = {s.name: (s, loc) for s, loc in zip(old.params, old_locs, strict=True)}
    for slot, loc in zip(new.params, new_locs, strict=True):
        if slot.name not in old_at:
            return CallAxis(
                AxisStatus.BROKEN,
                f"parameter {slot.name} is new: it is read from a register the old caller never set",
            )
        old_slot, old_loc = old_at[slot.name]
        if old_loc != loc:
            return CallAxis(
                AxisStatus.BROKEN,
                f"parameter {slot.name} moved from {_reg(old_loc)} to {_reg(loc)}",
            )
        if old_slot.rep is not slot.rep:
            return CallAxis(
                AxisStatus.BROKEN,
                f"parameter {slot.name} changed representation ({old_slot.rep} -> {slot.rep})",
            )
    if old.ret is RetShape.VOID:
        if new.ret.is_sret:
            return CallAxis(
                AxisStatus.BROKEN,
                "the new function returns through a hidden pointer the old caller never passed",
            )
    elif old.ret is new.ret and old.ret in _AGGREGATE_SHAPES:
        # Same class, but the shape does not carry the member layout: two
        # INT2 structs can be {long, long} and {int, int, long}.
        return _unknown(
            "two struct returns of the same class; member layouts not compared"
        )
    elif old.ret is not new.ret:
        return CallAxis(
            AxisStatus.BROKEN,
            f"the old caller reads its result from {ret_location(old.ret)} as {old.ret}, "
            f"the new function leaves it in {ret_location(new.ret)} as {new.ret}",
        )
    return CallAxis(AxisStatus.PRESERVED, _preserved_reason(old, new))


_AGGREGATE_SHAPES = frozenset({RetShape.INT2, RetShape.SSE2, RetShape.MEMORY})

_INT_REGS = ("rdi", "rsi", "rdx", "rcx", "r8", "r9")


def _reg(loc: tuple[ArgClass, int]) -> str:
    cls, i = loc
    return _INT_REGS[i] if cls is ArgClass.INTEGER else f"xmm{i}"


def _preserved_reason(old: CallSignature, new: CallSignature) -> str:
    assert old.params is not None and new.params is not None
    kept = {s.name for s in new.params}
    dropped = [s.name for s in old.params if s.name not in kept]
    parts = [
        "x86-64 SysV: old callers pass every surviving argument in the register the new function reads"
    ]
    if dropped:
        parts.append(f"removed parameter(s) {', '.join(dropped)} are ignored")
    if old.ret is RetShape.VOID and new.ret is not RetShape.VOID:
        parts.append("the old caller never reads the new result")
    return "; ".join(parts)
