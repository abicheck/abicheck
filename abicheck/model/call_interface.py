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

"""Where the x86-64 System V convention puts a call's arguments and result.

This is the platform fact behind ADR-094's ``call`` axis (D8). It states,
for a function signature already reduced to *representations*, which
register each argument and the result occupy. The comparison of two
signatures lives in :mod:`abicheck.compare.call_interface`.

The rules are the ones the closed fragments E13 and E14 proved in Lean
(``formal/lean/AbiLean/FuncFragment.lean``, ``RetFragment.lean``) and
confirmed by execution on gcc and clang:

* a scalar argument goes in the next register of its class: INTEGER
  arguments in ``rdi, rsi, rdx, rcx, r8, r9``; SSE arguments in
  ``xmm0 .. xmm7`` (``FuncFragment.locAt``);
* a result is returned in ``eax`` / ``rax`` / ``xmm0`` for a scalar,
  ``rax:rdx`` or ``xmm0:xmm1`` for a 16-byte struct of two INTEGER or two SSE
  eightbytes, and through caller memory for a struct over 16 bytes. In that
  last case the caller passes the address as a hidden first INTEGER
  argument, so every INTEGER argument moves up one register
  (``RetFragment.argLoc``);
* a struct of one or two eightbytes of one class comes back in ``rax`` /
  ``rax:rdx`` or ``xmm0`` / ``xmm0:xmm1``, each eightbyte in the next
  register of its class. :func:`ret_image` places each struct member (from
  :mod:`abicheck.model.sysv_aggregate`) in its register bits, which is what
  lets two struct returns of the same class be compared member by member.
  In Lean (``formal/lean/AbiLean/StructRetFragment.lean``), over its model
  of structs of ``int``/``long``/``double`` members, the comparison
  predicate is proved equivalent to the member-read oracle for every
  nonempty old member list; this Python code is checked against that model
  on the exported fragment and against execution on gcc and clang
  (``formal/experiments/struct_ret_fragment.py``). Neither is a proof of
  this implementation.

Only the representations the fragments covered are modelled. A slot outside
them has no :class:`Rep`, and the comparison then says it cannot judge.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .abi_fundamental import AbiFundamental, FundamentalEncoding
from .availability import FactStatus
from .fact import Fact

__all__ = [
    "MAX_INTEGER_ARGS",
    "MAX_SSE_ARGS",
    "SYSV_AMD64",
    "ArgClass",
    "ArgSlot",
    "Member",
    "Rep",
    "RetShape",
    "arg_locations",
    "ret_image",
    "ret_location",
    "ret_shape_of",
    "slot_rep",
]

#: The one ABI this module models (ELF64 x86-64, not x32).
SYSV_AMD64 = "sysv_amd64"

#: Registers per class. Beyond them arguments go on the stack, which is not
#: modelled.
MAX_INTEGER_ARGS = 6
MAX_SSE_ARGS = 8


class ArgClass(StrEnum):
    """The SysV register class of a scalar argument."""

    INTEGER = "integer"
    SSE = "sse"


class Rep(StrEnum):
    """How a scalar sits in its register: what a reader of the slot expects.

    ``long`` and an object pointer share ``I64``: same register, same 64 bits.
    ``int`` is ``I32``: a 64-bit reader of it sees undefined upper bits.
    """

    I32 = "i32"
    I64 = "i64"
    F64 = "f64"

    @property
    def arg_class(self) -> ArgClass:
        return ArgClass.SSE if self is Rep.F64 else ArgClass.INTEGER


class RetShape(StrEnum):
    """Where a result comes back, and in what form."""

    VOID = "void"
    I32 = "i32"  # eax
    I64 = "i64"  # rax
    F64 = "f64"  # xmm0
    INT1 = "int1"  # rax, a struct of one INTEGER eightbyte
    SSE1 = "sse1"  # xmm0, a struct of one SSE eightbyte
    INT2 = "int2"  # rax:rdx, two INTEGER eightbytes
    SSE2 = "sse2"  # xmm0:xmm1, two SSE eightbytes
    MEMORY = "memory"  # caller memory, address in a hidden rdi argument

    @property
    def is_sret(self) -> bool:
        return self is RetShape.MEMORY

    @property
    def is_aggregate(self) -> bool:
        return self in _AGGREGATE_SHAPES


_AGGREGATE_SHAPES = frozenset(
    {RetShape.INT1, RetShape.SSE1, RetShape.INT2, RetShape.SSE2, RetShape.MEMORY}
)


_RET_LOCATION = {
    RetShape.VOID: "none",
    RetShape.I32: "eax",
    RetShape.I64: "rax",
    RetShape.F64: "xmm0",
    RetShape.INT1: "rax",
    RetShape.SSE1: "xmm0",
    RetShape.INT2: "rax:rdx",
    RetShape.SSE2: "xmm0:xmm1",
    RetShape.MEMORY: "caller memory",
}


def ret_location(shape: RetShape) -> str:
    """The register (or memory) a result of *shape* is read from."""
    return _RET_LOCATION[shape]


@dataclass(frozen=True, slots=True)
class Member:
    """One scalar member of a struct return, flattened: its offset and size
    in bits from the start of the struct, and its SysV class."""

    offset: int
    size: int
    cls: ArgClass


#: A scalar result's size in bits and class.
_SCALAR_BITS = {
    RetShape.I32: (32, ArgClass.INTEGER),
    RetShape.I64: (64, ArgClass.INTEGER),
    RetShape.F64: (64, ArgClass.SSE),
}

#: One piece of a returned value as its reader sees it: ``scalar`` or
#: ``member``, the register (or ``mem``), the bit offset inside it, the size in
#: bits and the class.
ImagePart = tuple[str, str, int, int, ArgClass]


def ret_image(
    shape: RetShape, members: tuple[Member, ...] | None
) -> frozenset[ImagePart] | None:
    """Where each piece of a result of *shape* comes back, or ``None`` for a
    struct return whose *members* are unknown.

    A struct's eightbytes take the registers of their class in order (``rax``
    then ``rdx``; ``xmm0`` then ``xmm1``); a ``MEMORY`` struct lies in caller
    memory as declared. A scalar is tagged ``scalar`` and a struct member
    ``member``: a scalar reader never expects a struct member's value, even
    one in the same bits (E14 calls every scalar <-> struct change broken).
    """
    if shape is RetShape.VOID:
        return frozenset()
    if shape in _SCALAR_BITS:
        size, cls = _SCALAR_BITS[shape]
        reg = "rax" if cls is ArgClass.INTEGER else "xmm0"
        return frozenset({("scalar", reg, 0, size, cls)})
    if members is None:
        return None
    if shape is RetShape.MEMORY:
        return frozenset(("member", "mem", m.offset, m.size, m.cls) for m in members)
    regs = {
        ArgClass.INTEGER: iter(("rax", "rdx")),
        ArgClass.SSE: iter(("xmm0", "xmm1")),
    }
    reg_of: dict[int, str] = {}
    for eb in sorted({m.offset // 64 for m in members}):
        cls = next(m.cls for m in members if m.offset // 64 == eb)
        reg_of[eb] = next(regs[cls])
    return frozenset(
        ("member", reg_of[m.offset // 64], m.offset % 64, m.size, m.cls)
        for m in members
    )


@dataclass(frozen=True, slots=True)
class ArgSlot:
    """One parameter: its declared name and its representation."""

    name: str
    rep: Rep


def arg_locations(reps: list[Rep], sret: bool) -> list[tuple[ArgClass, int]]:
    """Each argument's register, as (class, index within the class).

    With *sret* the hidden result pointer takes INTEGER register 0, so the
    declared INTEGER arguments start at 1.
    """
    next_index = {ArgClass.INTEGER: 1 if sret else 0, ArgClass.SSE: 0}
    out = []
    for rep in reps:
        cls = rep.arg_class
        out.append((cls, next_index[cls]))
        next_index[cls] += 1
    return out


def _present(fact: Fact[AbiFundamental] | None) -> AbiFundamental | None:
    if fact is None or fact.status is not FactStatus.PRESENT:
        return None
    return fact.value


def slot_rep(
    abi_fact: Fact[AbiFundamental] | None, pointer_depth: int, spelling: str
) -> Rep | None:
    """The representation of a parameter or return slot, or ``None`` when the
    slot is outside the modelled domain.

    A fundamental slot is read from the compiler's own ABI fact
    (:class:`AbiFundamental`): a signed 4-byte integer is ``I32``, a signed
    8-byte integer ``I64``, an 8-byte float ``F64``. An object pointer (any
    depth, not a member pointer or reference) is ``I64``. Everything else
    -- unsigned or sub-word integers, ``_Bool``, ``float``, ``long double``,
    vectors, structs, references -- is not modelled.
    """
    if pointer_depth > 0:
        text = spelling.strip()
        if text.endswith("*") and "::*" not in text and "(" not in text:
            return Rep.I64
        return None
    fact = _present(abi_fact)
    if fact is None or fact.lanes:
        return None
    if fact.encoding is FundamentalEncoding.SIGNED and fact.size == fact.align:
        return {4: Rep.I32, 8: Rep.I64}.get(fact.size)
    if fact.encoding is FundamentalEncoding.FLOAT and fact.size == fact.align == 8:
        return Rep.F64
    return None


def ret_shape_of(
    abi_fact: Fact[AbiFundamental] | None, pointer_depth: int, spelling: str
) -> RetShape | None:
    """The return shape of a scalar or ``void`` return slot, or ``None``.

    Struct returns need the record's classification, which is not a slot
    fact; a caller that has it supplies :class:`RetShape` directly.
    """
    if pointer_depth == 0 and spelling.strip() == "void":
        return RetShape.VOID
    rep = slot_rep(abi_fact, pointer_depth, spelling)
    return None if rep is None else RetShape(rep.value)
