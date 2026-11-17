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
    RetShape,
    arg_locations,
    ret_location,
    ret_shape_of,
    slot_rep,
)
from ..model.cc_attributes import is_cc_attribute
from ..model.declarations import Function

__all__ = [
    "AxisStatus",
    "CallAxis",
    "CallSignature",
    "assess_call_interface",
    "call_signature_of",
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


@dataclass(frozen=True, slots=True)
class CallSignature:
    """A signature reduced to what the convention sees.

    ``params`` is ``None`` when any parameter is outside the modelled domain,
    ``ret`` is ``None`` when the return is, and ``variadic`` is ``None`` when
    it is unknown whether the function takes ``...``. ``convention`` is ``""``
    for the platform default, a calling-convention attribute (``ms_abi``) when
    one is declared, and ``None`` when attributes were not captured.
    ``maybe_member`` is true when the function may receive an implicit
    ``this`` argument, which ``params`` does not list.
    """

    params: tuple[ArgSlot, ...] | None
    ret: RetShape | None
    variadic: bool | None
    convention: str | None = ""
    maybe_member: bool = False


def call_signature_of(fn: Function, ret: RetShape | None = None) -> CallSignature:
    """Reduce *fn* to a :class:`CallSignature` from its recorded facts.

    *ret* overrides the return shape for a struct return whose classification
    the caller knows; otherwise a scalar or ``void`` return is read from the
    return slot's ABI fact.
    """
    convention = _convention(fn.contract_attributes)
    # A non-static member function receives an implicit `this` that `params`
    # does not list. Neither `is_static` (for a free function it means
    # internal linkage) nor the name (extractors store the leaf, `f`) tells a
    # member reliably. So only C-linkage functions are modelled: extern "C",
    # or an unmangled symbol that is a plain identifier equal to the name.
    # Everything else -- `_Z...` symbols, castxml's synthetic constructor and
    # destructor names -- may be a member and is left to the prior verdict.
    c_linkage = fn.is_extern_c or (fn.mangled == fn.name and fn.name.isidentifier())
    maybe_member = not c_linkage
    if ret is None:
        ret = ret_shape_of(
            fn.return_type_abi_fact, fn.return_pointer_depth, fn.return_type
        )
    slots = []
    for p in fn.params:
        rep = slot_rep(p.type_abi_fact, p.pointer_depth, p.type)
        if rep is None:
            return CallSignature(None, ret, fn.is_variadic, convention, maybe_member)
        slots.append(ArgSlot(p.name, rep))
    return CallSignature(tuple(slots), ret, fn.is_variadic, convention, maybe_member)


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
        if sig.maybe_member:
            return "not a C-linkage symbol: a possible member function's implicit this is not modelled"
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
