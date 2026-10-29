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

"""The ABI fact of a castxml type node (schema v69, ``model/abi_fundamental.py``).

castxml states a fundamental's size and alignment itself (``FundamentalType``,
``AtomicType`` and ``Enumeration`` carry ``size``/``align`` in bits, and an
enumeration its underlying ``type``). It never states a fundamental's
signedness or float class: its ``FundamentalType.name`` is the compiler's
canonical builtin name (``unsigned int``, ``long long int``, ``_Bool``), which
a user spelling cannot produce, so the encoding class is read off that closed
set of builtin names. The one name that depends on the target is a plain
``char`` (signed on x86-64, unsigned on AArch64); its signedness is probed from
the same compiler and options the header parse used (``__CHAR_UNSIGNED__``).

A vector is ``Unimplemented``; its spelling ``T __attribute__((vector_size(N)))``
is joined from the clang AST (:mod:`.vector_spellings`), so its byte size is the
``N`` that spelling states and its element is the builtin the spelling names.
Complex and bit-precise types are not joined, so they carry no fact.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
from typing import Any
from xml.etree.ElementTree import Element

from ....model.abi_fundamental import AbiFundamental, FundamentalEncoding
from ....model.fact import Fact
from ..ast_config import _resolve_compiler_binary
from .vector_spellings import _ELEMENT_BYTES

__all__ = [
    "castxml_abi_fundamental",
    "castxml_type_abi_fact",
    "plain_char_is_unsigned",
    "probe_plain_char_unsigned",
]

_SIGNED = FundamentalEncoding.SIGNED
_UNSIGNED = FundamentalEncoding.UNSIGNED
_FLOAT = FundamentalEncoding.FLOAT

#: The closed set of C/C++ builtin spellings castxml writes for a fundamental
#: (its canonical names), with the spellings clang writes in a vector element.
#: ``char`` is absent: its encoding is the target's, see
#: :func:`_builtin_encoding`. ``wchar_t`` is absent too: it is a typedef in C and
#: its signedness is the target's, so it carries no fact.
_BUILTIN_ENCODING: dict[str, FundamentalEncoding] = {
    "signed char": _SIGNED,
    "unsigned char": _UNSIGNED,
    "short int": _SIGNED,
    "short": _SIGNED,
    "int": _SIGNED,
    "signed int": _SIGNED,
    "long int": _SIGNED,
    "long": _SIGNED,
    "long long int": _SIGNED,
    "long long": _SIGNED,
    "__int128": _SIGNED,
    "short unsigned int": _UNSIGNED,
    "unsigned short": _UNSIGNED,
    "unsigned int": _UNSIGNED,
    "unsigned": _UNSIGNED,
    "long unsigned int": _UNSIGNED,
    "unsigned long": _UNSIGNED,
    "long long unsigned int": _UNSIGNED,
    "unsigned long long": _UNSIGNED,
    "unsigned __int128": _UNSIGNED,
    "char16_t": _UNSIGNED,
    "char32_t": _UNSIGNED,
    "char8_t": _UNSIGNED,
    "_Bool": FundamentalEncoding.BOOLEAN,
    "bool": FundamentalEncoding.BOOLEAN,
    "float": _FLOAT,
    "double": _FLOAT,
    "long double": _FLOAT,
    "_Float16": _FLOAT,
    "_Float32": _FLOAT,
    "_Float64": _FLOAT,
    "_Float128": _FLOAT,
    "_Float32x": _FLOAT,
    "_Float64x": _FLOAT,
    "__float128": _FLOAT,
}

_VECTOR_SPELLING = re.compile(
    r"^\s*(?P<element>.+?)\s*__attribute__\(\(vector_size\((?P<bytes>\d+)\)\)\)\s*$"
)

#: A type chain deeper than this is not a fundamental this module reads.
_MAX_DEPTH = 16


@functools.cache
def plain_char_is_unsigned(cc_bin: str, option_tokens: tuple[str, ...]) -> bool | None:
    """Whether a plain ``char`` is unsigned under *cc_bin* with *option_tokens*.

    The compiler's own predefined macro (``__CHAR_UNSIGNED__``, defined by GCC
    and Clang exactly when plain ``char`` is unsigned). ``None`` when the
    compiler does not run: then no ``char`` carries a fact.
    """
    argv = [cc_bin, *option_tokens, "-dM", "-E", "-x", "c", os.devnull]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return "#define __CHAR_UNSIGNED__" in proc.stdout


def _builtin_encoding(
    name: str, plain_char_unsigned: bool | None
) -> FundamentalEncoding | None:
    if name == "char":
        if plain_char_unsigned is None:
            return None
        return _UNSIGNED if plain_char_unsigned else _SIGNED
    return _BUILTIN_ENCODING.get(name)


def _bytes(bits: str | None) -> int | None:
    """A castxml bit count as bytes, or None when absent or not a whole byte."""
    if not bits:
        return None
    try:
        value = int(bits)
    except ValueError:
        return None
    return value // 8 if value > 0 and value % 8 == 0 else None


def _fact(
    el: Element, *, encoding: FundamentalEncoding | None, align_bits: str | None
) -> AbiFundamental | None:
    size = _bytes(el.get("size"))
    align = _bytes(align_bits)
    if encoding is None or size is None or align is None:
        return None
    return AbiFundamental(encoding, size, align)


def castxml_abi_fundamental(ctx: Any, type_id: str) -> AbiFundamental | None:
    """The ABI fact of the castxml type *type_id*, or None (no fact).

    *ctx* is the parser context: ``resolve`` finds a node, ``vector_spellings``
    carries the clang spelling of each vector node, and ``plain_char_unsigned``
    is the probed target signedness of a plain ``char`` (None when unprobed).
    """
    return _fact_of(ctx, type_id, 0)


def _fact_of(ctx: Any, type_id: str, depth: int) -> AbiFundamental | None:
    if depth > _MAX_DEPTH:
        return None
    el = ctx.resolve(type_id)
    if el is None:
        return None
    tag = el.tag
    plain_char = getattr(ctx, "plain_char_unsigned", None)
    if tag == "FundamentalType":
        return _fact(
            el,
            encoding=_builtin_encoding(el.get("name", ""), plain_char),
            align_bits=el.get("align"),
        )
    # ElaboratedType is the `enum E` / `struct S` keyword wrapper castxml puts
    # around a named enum reference; it changes no passing convention.
    if tag in ("CvQualifiedType", "Typedef", "ElaboratedType"):
        return _fact_of(ctx, el.get("type", ""), depth + 1)
    if tag == "Enumeration":
        under = _fact_of(ctx, el.get("type", ""), depth + 1)
        if under is None or under.lanes or under.size != _bytes(el.get("size")):
            return None
        return under
    if tag == "AtomicType":
        inner = _fact_of(ctx, el.get("type", ""), depth + 1)
        if inner is None or inner.lanes:
            return None
        return _fact(el, encoding=inner.encoding, align_bits=el.get("align"))
    if tag == "Unimplemented" and el.get("type_class") == "Vector":
        return _vector_fact(ctx.vector_spellings.get(type_id), plain_char)
    return None


def _vector_fact(
    spelling: str | None, plain_char_unsigned: bool | None
) -> AbiFundamental | None:
    """The fact of a joined ``T __attribute__((vector_size(N)))`` spelling."""
    if spelling is None:
        return None
    match = _VECTOR_SPELLING.match(spelling)
    if match is None:
        return None
    element = match.group("element").strip()
    size = int(match.group("bytes"))
    encoding = _builtin_encoding(element, plain_char_unsigned)
    element_bytes = _ELEMENT_BYTES.get(element)
    if encoding is None or not element_bytes or size % element_bytes or size <= 0:
        return None
    # A GCC/Clang vector is aligned to its own size, as in extract/dwarf_abi_fact.
    return AbiFundamental(encoding, size, size, size // element_bytes)


def castxml_type_abi_fact(ctx: Any, type_id: str) -> Fact[AbiFundamental] | None:
    """:func:`castxml_abi_fundamental` as the ``Fact`` a declaration stores:
    ``None`` (not determined) for a type with no fact."""
    fact = castxml_abi_fundamental(ctx, type_id)
    return None if fact is None else Fact.present(fact)


def probe_plain_char_unsigned(
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    option_tokens: tuple[str, ...],
) -> bool | None:
    """:func:`plain_char_is_unsigned` for the compiler a header parse names."""
    cc_bin, _cc_id = _resolve_compiler_binary(compiler, gcc_path, gcc_prefix)
    return plain_char_is_unsigned(shutil.which(cc_bin) or cc_bin, tuple(option_tokens))
