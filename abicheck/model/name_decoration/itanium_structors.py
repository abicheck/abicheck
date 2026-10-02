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

"""Itanium constructor/destructor variant codec.

One source-level constructor or destructor is emitted as several distinct
symbols that differ only in a two-character ``<ctor-dtor-name>`` code
(Itanium C++ ABI 5.1.4.3): ``C1`` complete-object, ``C2`` base-object,
``C3`` allocating constructor; ``D0`` deleting, ``D1`` complete-object,
``D2`` base-object destructor. Every variant demangles to the same leaf, so
a join must treat them as one *family* -- and only them: the code is a
grammar production, located by walking the nested name's length-prefixed
components, never by substring search (``_ZN6C1EvilIiEC1Ev`` is
``C1Evil<int>::C1Evil()``; a ``"C1E"`` search finds the class name).

:func:`locate` is the one structural locator (it also reports the GCC
unified ``C4``/``C5``/``D4``/``D5`` codes, which are not exports);
:func:`decode` restricts to the six ABI variants above. A Mach-O ``__Z``
spelling is located in place (span relative to the string passed in).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..mangled_name_template_args import (
    read_length_prefixed_name,
    skip_substitution,
    skip_template_args,
)

__all__ = [
    "CTOR_VARIANTS",
    "DTOR_VARIANTS",
    "EMITTED_VARIANTS",
    "Structor",
    "complete_object_spelling",
    "decode",
    "encode",
    "locate",
    "sibling_spellings",
]

CTOR_VARIANTS = ("C1", "C2", "C3")
DTOR_VARIANTS = ("D0", "D1", "D2")
#: The variants GCC and clang actually emit as exports. ``C3`` (allocating
#: constructor) is part of the ABI but not observed emitted in practice, so a
#: producer that *derives* sibling symbols (rather than joining against an
#: observed table) asks for these only.
EMITTED_VARIANTS = ("C1", "C2", "D0", "D1", "D2")
_COMPLETE = {"C": "C1", "D": "D1"}
_GRAMMAR_CODES = {"C": "12345", "D": "012345"}


@dataclass(frozen=True)
class Structor:
    """A ctor/dtor symbol split around its variant code."""

    prefix: str
    code: str
    suffix: str

    @property
    def kind(self) -> str:
        """``"C"`` (constructor) or ``"D"`` (destructor)."""
        return self.code[0]

    @property
    def family(self) -> tuple[str, str, str]:
        """Equal for exactly the variants of one source-level member."""
        return self.prefix, self.kind, self.suffix

    @property
    def variants(self) -> tuple[str, ...]:
        return CTOR_VARIANTS if self.kind == "C" else DTOR_VARIANTS


def locate(symbol: str) -> tuple[int, int] | None:
    """``(start, end)`` of *symbol*'s ``<ctor-dtor-name>`` code, or ``None``
    when *symbol* is not a nested-name Itanium ctor/dtor this walk models
    (a free function, an operator, an inherited ``CI1`` constructor, a
    malformed or truncated mangling)."""
    offset = 1 if symbol.startswith("__Z") else 0
    if not symbol.startswith("_ZN", offset):
        return None
    s = symbol
    n = len(s)
    i = offset + 3
    while i < n and s[i] in "rVKRO":  # implicit-object cv/ref qualifiers
        i += 1
    while i < n:
        c = s[i]
        if "0" <= c <= "9":
            name, i2 = read_length_prefixed_name(s, i)
            if name is None:
                return None
            i = i2
        elif c == "I":
            end = skip_template_args(s, i)
            if end is None:
                return None
            i = end
        elif c == "S":
            i = skip_substitution(s, i)
        elif c == "B":  # ABI tag on the class name: B<source-name>
            name, i2 = read_length_prefixed_name(s, i + 1)
            if name is None:
                return None
            i = i2
        elif c in _GRAMMAR_CODES and i + 1 < n and s[i + 1] in _GRAMMAR_CODES[c]:
            return i, i + 2
        else:
            return None
    return None


def decode(symbol: str) -> Structor | None:
    """*symbol* split around one of the six ABI variant codes, or ``None``."""
    span = locate(symbol)
    if span is None:
        return None
    start, end = span
    code = symbol[start:end]
    if code not in CTOR_VARIANTS and code not in DTOR_VARIANTS:
        return None
    return Structor(symbol[:start], code, symbol[end:])


def encode(structor: Structor) -> str:
    return structor.prefix + structor.code + structor.suffix


def complete_object_spelling(symbol: str) -> str | None:
    """The ``C1``/``D1`` member of *symbol*'s family (the canonical key),
    or ``None`` when *symbol* is not a variant."""
    decoded = decode(symbol)
    if decoded is None:
        return None
    return encode(replace(decoded, code=_COMPLETE[decoded.kind]))


def sibling_spellings(
    symbol: str, *, only: tuple[str, ...] | None = None
) -> tuple[str, ...]:
    """Every *other* variant spelling of *symbol*'s family, in ABI order
    (restricted to the codes in *only* when given); ``()`` when *symbol* is
    not a variant."""
    decoded = decode(symbol)
    if decoded is None:
        return ()
    return tuple(
        encode(replace(decoded, code=c))
        for c in decoded.variants
        if c != decoded.code and (only is None or c in only)
    )
