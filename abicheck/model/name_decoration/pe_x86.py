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

"""PE/COFF C calling-convention export decoration codec.

MSVC decorates a C-linkage export by calling convention (MSVC "Argument
Passing and Naming Conventions"):

* ``__cdecl`` ``_name`` -- 32-bit x86 only;
* ``__stdcall`` ``_name@N`` -- 32-bit x86 only;
* ``__fastcall`` ``@name@N`` -- 32-bit x86 only;
* ``__vectorcall`` ``name@@N`` -- **every** machine, with no leading
  ``_`` anywhere: ``_f@@8`` is function ``_f``, never ``f`` (the H3
  defect-family bug class: two distinct vectorcall names decoding to one
  identity).

Off 32-bit x86 a leading ``_`` is part of the real name (an x64 ``_secret``
is not a decorated ``secret``), so only ``__vectorcall`` decodes there. An
unknown machine is treated as not-x86 (fail closed).

``N`` is the argument-list size in bytes: decimal, no leading zero, and a
whole number of 4-byte stack slots. It is checked for shape only -- an L2
snapshot has no reliable per-parameter stack size, so it is never compared
with a declaration.

``name`` must be a C identifier. An i686 MinGW Itanium export ``__Z...`` is
a ``__cdecl`` decoration of ``_Z...`` under this rule; whether a decoded
name is C++-mangled is the caller's question (:func:`is_cxx_mangled`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "PE_MACHINE_I386",
    "CallingConvention",
    "PeDecoded",
    "decode",
    "decode_c_name",
    "encode",
    "is_cxx_mangled",
]

#: ``PeMetadata.machine`` (``pefile.MACHINE_TYPE`` spelling) of 32-bit x86.
PE_MACHINE_I386 = "IMAGE_FILE_MACHINE_I386"

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_ARG_BYTES = r"(?:0|[1-9][0-9]*)"
_STDCALL_RE = re.compile(rf"_({_IDENT})@({_ARG_BYTES})")
_FASTCALL_RE = re.compile(rf"@({_IDENT})@({_ARG_BYTES})")
_VECTORCALL_RE = re.compile(rf"({_IDENT})@@({_ARG_BYTES})")
_CDECL_RE = re.compile(rf"_({_IDENT})")


class CallingConvention(Enum):
    CDECL = "cdecl"
    STDCALL = "stdcall"
    FASTCALL = "fastcall"
    VECTORCALL = "vectorcall"


@dataclass(frozen=True)
class PeDecoded:
    """One decoded PE export spelling. ``arg_bytes`` is ``None`` exactly
    for ``__cdecl``, which carries no ``@N``."""

    name: str
    convention: CallingConvention
    arg_bytes: int | None = None


def is_cxx_mangled(name: str) -> bool:
    """Whether *name* is an MSVC (``?``) or Itanium (``_Z``) mangling --
    a C++ name carries its convention inside the mangling."""
    return name.startswith(("?", "_Z"))


def encode(decoded: PeDecoded) -> str:
    """The exact export spelling :func:`decode` maps back to *decoded*."""
    n, b = decoded.name, decoded.arg_bytes
    if decoded.convention is CallingConvention.CDECL:
        return f"_{n}"
    if decoded.convention is CallingConvention.STDCALL:
        return f"_{n}@{b}"
    if decoded.convention is CallingConvention.FASTCALL:
        return f"@{n}@{b}"
    return f"{n}@@{b}"


def decode(symbol: str, *, x86_32: bool) -> PeDecoded | None:
    """The decoration *symbol* provably carries, or ``None``.

    *x86_32* is the caller's ``machine == PE_MACHINE_I386`` read; on any
    other (or unknown) machine only ``__vectorcall`` decodes.
    """
    m = _VECTORCALL_RE.fullmatch(symbol)
    if m is not None:
        return _with_bytes(m, CallingConvention.VECTORCALL)
    if not x86_32:
        return None
    for pattern, conv in (
        (_STDCALL_RE, CallingConvention.STDCALL),
        (_FASTCALL_RE, CallingConvention.FASTCALL),
    ):
        m = pattern.fullmatch(symbol)
        if m is not None:
            return _with_bytes(m, conv)
    m = _CDECL_RE.fullmatch(symbol)
    if m is not None:
        return PeDecoded(m.group(1), CallingConvention.CDECL)
    return None


def _with_bytes(m: re.Match[str], conv: CallingConvention) -> PeDecoded | None:
    arg_bytes = int(m.group(2))
    if arg_bytes % 4:
        return None
    return PeDecoded(m.group(1), conv, arg_bytes)


def decode_c_name(symbol: str, *, x86_32: bool) -> str:
    """The undecorated **C** name *symbol* decorates, or ``""`` when it is
    not provably a C-linkage decoration (no decoration, or the decoded
    name is a C++ mangling)."""
    decoded = decode(symbol, x86_32=x86_32)
    if decoded is None or is_cxx_mangled(decoded.name):
        return ""
    return decoded.name
