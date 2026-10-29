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

"""The ABI fact of a fundamental-typed slot, and when two such facts agree.

A parameter, field, return or variable whose type is a fundamental arithmetic
type (or an enum, an ``_Atomic`` or a GCC/Clang vector of one) is passed,
stored and returned by the compiler according to a small set of properties,
not according to the name the header gave it. ``long`` and ``long long`` on
LP64, ``char`` and ``signed char`` on x86-64, ``enum E`` and ``unsigned int``
are the same ABI under two spellings; ``int`` and ``unsigned int`` are not,
because a sub-word argument or return is sign- or zero-extended by the
caller's convention.

:class:`AbiFundamental` records those properties, read from the compiler's own
answer (DWARF ``DW_AT_encoding``/``DW_AT_byte_size``, castxml's sized
fundamental nodes). :func:`abi_equivalent` decides equality from them alone.
It is deliberately conservative: a pair it cannot prove equal is not equal,
so an absent or undecidable fact never turns a change into a compatible one.

The classes below follow the SysV x86-64 and AAPCS64 register-class rules at
the level a caller can observe:

* ``INTEGER`` -- a scalar of at most 16 bytes in general-purpose registers.
  Signedness matters only below 8 bytes, where the convention extends the
  value (a 4-byte ``unsigned`` returned to a caller that widens it is read
  differently from a 4-byte ``int``). An 8- or 16-byte scalar is the same
  register image either way.
* ``SSE`` -- a float of 4 or 8 bytes, and a vector of at most 16 bytes. Both
  travel in a vector register. Two vectors are equal only with the same lane
  count and element encoding: ``float4`` and ``int4`` share the register, but
  the callee reads the caller's float bits as integers.
* ``BOOLEAN`` -- ``_Bool``. Its callers normalise the value to 0 or 1, so it
  is never equal to an integer, not even an unsigned char of the same size.
* ``OPAQUE`` -- an x87 or quad float (16 bytes), or a vector over 16 bytes.
  Its convention is not one this module can prove, so only an identical fact
  matches it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "AbiFundamental",
    "FundamentalEncoding",
    "RegisterClass",
    "abi_equivalent",
    "register_class",
]


class FundamentalEncoding(StrEnum):
    """The encoding of a fundamental scalar: the DWARF ``DW_ATE`` class, reduced
    to what a calling convention distinguishes. A plain ``char`` takes the
    signedness its target gives it (``SIGNED`` on x86-64, ``UNSIGNED`` on
    AArch64); ``char16_t``/``char32_t`` are ``UNSIGNED``."""

    SIGNED = "signed"
    UNSIGNED = "unsigned"
    BOOLEAN = "boolean"
    FLOAT = "float"


class RegisterClass(StrEnum):
    """The class a fundamental fact is passed in (see the module docstring)."""

    INTEGER = "integer"
    SSE = "sse"
    BOOLEAN = "boolean"
    OPAQUE = "opaque"


@dataclass(frozen=True, slots=True)
class AbiFundamental:
    """What the compiler decides about a fundamental-typed slot.

    ``encoding`` is the scalar's own class (for a vector, its element's),
    ``size`` and ``align`` are its size and alignment in bytes, and ``lanes``
    is the vector lane count, or ``0`` for a scalar.
    """

    encoding: FundamentalEncoding
    size: int
    align: int
    lanes: int = 0

    def __post_init__(self) -> None:
        if self.size <= 0 or self.align <= 0 or self.lanes < 0:
            raise ValueError(
                f"AbiFundamental needs positive size/align and non-negative lanes, "
                f"got size={self.size} align={self.align} lanes={self.lanes}"
            )


def register_class(fact: AbiFundamental) -> RegisterClass:
    """The register class *fact* is passed in, per the module's rules."""
    if fact.lanes > 0:
        return RegisterClass.SSE if fact.size <= 16 else RegisterClass.OPAQUE
    if fact.encoding is FundamentalEncoding.BOOLEAN:
        return RegisterClass.BOOLEAN
    if fact.encoding is FundamentalEncoding.FLOAT:
        return RegisterClass.SSE if fact.size in (2, 4, 8) else RegisterClass.OPAQUE
    return RegisterClass.INTEGER if fact.size <= 16 else RegisterClass.OPAQUE


def abi_equivalent(old: AbiFundamental, new: AbiFundamental) -> bool:
    """Whether a slot of *old* and a slot of *new* have the same ABI.

    Identical facts are equal. Otherwise both must have the same size and
    alignment and the same register class; within ``INTEGER`` the signedness
    must also match unless the size is 8 bytes or more, and within ``SSE`` the
    lane count and element encoding must match.
    """
    if old == new:
        return True
    if old.size != new.size or old.align != new.align:
        return False
    old_class = register_class(old)
    if old_class is not register_class(new):
        return False
    if old_class is RegisterClass.INTEGER:
        return old.encoding is new.encoding or old.size >= 8
    if old_class is RegisterClass.SSE:
        return old.lanes == new.lanes and old.encoding is new.encoding
    # BOOLEAN with the same size and alignment is the same fact apart from
    # lanes, which a boolean scalar does not have; OPAQUE only matches itself.
    return old_class is RegisterClass.BOOLEAN and old.encoding is new.encoding
