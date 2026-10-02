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

"""The identifier a header would spell to declare an exported symbol.

Pure string parsing, no I/O and no host tools, so a decision built on it
cannot vary by which demangler happens to be installed.
"""

from __future__ import annotations

import re

_C_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def symbol_leaf_identifier(symbol: str) -> str | None:
    """The identifier a header would spell to declare *symbol*, or ``None``.

    Dependency-free on purpose: the external demanglers are host-dependent,
    and a contract decision must not vary by which tool is on ``PATH``. A
    plain C name is its own leaf; an Itanium name is parsed for the shapes
    ``_Z<len><name>...`` and ``_ZN<qual>*<names>E...`` only. Anything else
    -- template arguments, substitutions, operators, local names -- answers
    ``None``, which keeps the caller's decision unresolved.
    """
    name = symbol.split("@", 1)[0]
    if not name:
        return None
    if not name.startswith("_Z"):
        return name if _C_IDENTIFIER.match(name) else None
    rest = name[2:]
    if rest.startswith("N"):
        i = 1
        while i < len(rest) and rest[i] in "rVKRO":
            i += 1
        leaf: str | None = None
        while i < len(rest) and rest[i] != "E":
            if _is_ascii_digit(rest[i]):
                parsed = _source_name(rest, i)
                if parsed is None:
                    return None
                leaf, i = parsed
            elif rest[i] in "CD" and i + 1 < len(rest) and _is_ascii_digit(rest[i + 1]):
                i += 2  # constructor/destructor: the class name is the leaf
            else:
                return None
        return leaf if i < len(rest) else None
    if _is_ascii_digit(rest[:1]):
        parsed = _source_name(rest, 0)
        return parsed[0] if parsed is not None else None
    return None


def _is_ascii_digit(ch: str) -> bool:
    # str.isdigit() accepts e.g. "²", which int() then rejects.
    return len(ch) == 1 and "0" <= ch <= "9"


def _source_name(text: str, start: int) -> tuple[str, int] | None:
    j = start
    while j < len(text) and _is_ascii_digit(text[j]):
        j += 1
    length = int(text[start:j])
    ident = text[j : j + length]
    if len(ident) != length or not _C_IDENTIFIER.match(ident):
        return None
    return ident, j + length


__all__ = ["symbol_leaf_identifier"]
