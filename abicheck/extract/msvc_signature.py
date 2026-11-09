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

"""Parameter lists decoded from Microsoft C++ ABI function manglings.

``extract.header_export_binding`` pairs a header declaration with an MSVC
export by qualified name, and needs the parameter list to tell overloads
apart and to refuse a pairing whose signatures disagree. This module decodes
the subset of the MSVC type grammar a public C/C++ function signature
commonly uses:

* builtin types (``H`` int, ``N`` double, ``_N`` bool, ...);
* classes, structs, unions and enums by value (``V``/``U``/``T``/``W4`` +
  a qualified name), with the name back-references (``0``-``9``) MSVC uses
  for a fragment the symbol already spelled;
* one level of pointer, lvalue or rvalue reference (``P``/``Q``/``A``/
  ``$$Q``, an optional ``E`` 64-bit marker, a cv code) to either;
* parameter-type back-references (``0``-``9`` in the parameter list).

Each decoded type is a :class:`ParamSpelling`. A header parameter's spelling
is reduced to the same shape by :func:`header_param_spelling`, and
:func:`param_agrees` compares the two, accepting a header spelling that names
a class relative to its scope (clang writes ``A`` where castxml writes
``ns::A``). Anything else (templates, function pointers, arrays, multi-level
pointers) decodes to ``None``, never to a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ParamSpelling",
    "header_param_spelling",
    "msvc_function_params",
    "param_agrees",
]

#: MSVC builtin type codes -> the canonical builtin spellings.
MSVC_BUILTINS = {
    "X": "void",
    "D": "char",
    "C": "signed char",
    "E": "unsigned char",
    "F": "short",
    "G": "unsigned short",
    "H": "int",
    "I": "unsigned int",
    "J": "long",
    "K": "unsigned long",
    "M": "float",
    "N": "double",
    "O": "long double",
    "_N": "bool",
    "_J": "long long",
    "_K": "unsigned long long",
    "_W": "wchar_t",
    "_S": "char16_t",
    "_U": "char32_t",
    "_Q": "char8_t",
}
_INT_WORDS = {"short", "int", "long", "signed", "unsigned"}
_OTHER_BUILTINS = {
    "void",
    "bool",
    "_Bool",
    "wchar_t",
    "char8_t",
    "char16_t",
    "char32_t",
    "float",
}
_TAG_WORDS = re.compile(r"\b(?:struct|class|union|enum)\s+")


@dataclass(frozen=True)
class ParamSpelling:
    """One parameter type: a base type, the pointee's const qualification,
    and an indirection (``""``, ``"*"``, ``"&"`` or ``"&&"``). *base* is a
    canonical builtin spelling or a ``::``-qualified class name."""

    base: str
    const: bool = False
    indirection: str = ""


def canonical_builtin(spelling: str) -> str | None:
    """One spelling for a builtin type, or ``None``."""
    words = spelling.split()
    if not words:
        return None
    if len(words) == 1 and words[0] in _OTHER_BUILTINS:
        return "bool" if words[0] == "_Bool" else words[0]
    if any(w not in _INT_WORDS | {"char", "double"} for w in words):
        return None
    longs = words.count("long")
    if "double" in words:
        return "long double" if longs else "double"
    if "char" in words:
        if "unsigned" in words:
            return "unsigned char"
        return "signed char" if "signed" in words else "char"
    size = (
        "short"
        if "short" in words
        else ("long long" if longs >= 2 else ("long" if longs else "int"))
    )
    return f"unsigned {size}" if "unsigned" in words else size


def header_param_spelling(spelling: str) -> ParamSpelling | None:
    """A header parameter's type reduced to a :class:`ParamSpelling`."""
    text = _TAG_WORDS.sub("", spelling).strip()
    indirection = ""
    for suffix in ("&&", "&", "*"):
        if text.endswith(suffix):
            indirection, text = suffix, text[: -len(suffix)].strip()
            break
    if text.endswith("const") and indirection:
        # `T* const` qualifies the pointer itself, which mangles as the
        # parameter's top-level cv -- not part of the type a caller passes.
        return None
    words = text.split()
    const = "const" in words
    words = [w for w in words if w not in ("const", "volatile")]
    if not words or any(c in "".join(words) for c in "*&<>()[],"):
        return None
    base = canonical_builtin(" ".join(words))
    if base is None:
        if len(words) != 1:
            return None
        base = words[0].lstrip(":")
    return ParamSpelling(base, const if indirection else False, indirection)


def param_agrees(header: ParamSpelling, exported: ParamSpelling) -> bool:
    """Whether a header parameter and a decoded export parameter are one
    type. A class named relative to its scope matches its qualified name."""
    if (header.const, header.indirection) != (exported.const, exported.indirection):
        return False
    if header.base == exported.base:
        return True
    return "::" in exported.base and exported.base.endswith("::" + header.base)


class _Decoder:
    def __init__(self, sym: str) -> None:
        self.s = sym
        self.names: list[str] = []
        self.types: list[ParamSpelling] = []

    def name_fragment(self, i: int) -> tuple[str | None, int]:
        ch = self.s[i : i + 1]
        if ch.isdigit():
            idx = int(ch)
            return (self.names[idx] if idx < len(self.names) else None), i + 1
        end = self.s.find("@", i)
        if end <= i or self.s[i] == "?":
            return None, i
        frag = self.s[i:end]
        self.names.append(frag)
        return frag, end + 1

    def qualified_name(self, i: int) -> tuple[str | None, int]:
        parts: list[str] = []
        while i < len(self.s) and self.s[i] != "@":
            frag, i = self.name_fragment(i)
            if frag is None:
                return None, i
            parts.append(frag)
        if i >= len(self.s) or not parts:
            return None, i
        return "::".join(reversed(parts)), i + 1

    def value(self, i: int) -> tuple[str | None, int]:
        code = self.s
        if code.startswith("_", i):
            return MSVC_BUILTINS.get(code[i : i + 2]), i + 2
        if code[i : i + 1] in MSVC_BUILTINS:
            return MSVC_BUILTINS[code[i]], i + 1
        if code.startswith("W4", i):
            return self.qualified_name(i + 2)
        if code[i : i + 1] in ("V", "U", "T"):
            return self.qualified_name(i + 1)
        return None, i

    def type(self, i: int) -> tuple[ParamSpelling | None, int]:
        code = self.s
        indirection = ""
        if code.startswith("$$Q", i):
            indirection, i = "&&", i + 3
        elif code[i : i + 1] in ("P", "Q"):
            indirection, i = "*", i + 1
        elif code[i : i + 1] == "A":
            indirection, i = "&", i + 1
        const = False
        if indirection:
            if code.startswith("E", i):
                i += 1
            cv = code[i : i + 1]
            if cv not in ("A", "B", "C", "D"):
                return None, i
            const = cv in ("B", "D")
            i += 1
        base, i = self.value(i)
        if base is None:
            return None, i
        return ParamSpelling(base, const, indirection), i

    def head(self) -> int | None:
        """Record the function's own name fragments; index after ``@@``."""
        if not self.s.startswith("?") or self.s[1:2] in ("?", "$"):
            return None
        _, i = self.qualified_name(1)
        if i >= len(self.s) or self.s[i - 1 : i] != "@" or self.s[i : i + 1] == "":
            return None
        return i if self.s[i - 2 : i] == "@@" else None

    def skip_return(self, i: int) -> int | None:
        if self.s.startswith("?A", i):
            i += 2
        ret, i = self.type(i)
        return i if ret is not None else None


def _skip_calling_convention(code: str, i: int) -> int | None:
    head = code[i : i + 1]
    if head == "Y":
        i += 1
    elif head and head in "CKS":
        i += 1
    elif head and head in "AEIMQU":
        i += 1
        if code.startswith("E", i):
            i += 1
        if code[i : i + 1] not in ("A", "B", "C", "D"):
            return None
        i += 1
    else:
        return None
    if code[i : i + 1] not in ("A", "G", "I", "Q"):
        return None
    return i + 1


def msvc_function_params(sym: str) -> tuple[ParamSpelling, ...] | None:
    """The parameter list of an MSVC function mangling, or ``None`` when any
    part of it is outside the decoded subset (see the module docstring)."""
    dec = _Decoder(sym)
    i = dec.head()
    if i is None:
        return None
    j = _skip_calling_convention(sym, i)
    if j is None:
        return None
    k = dec.skip_return(j)
    if k is None:
        return None
    i = k
    if sym[i:] == "XZ":
        return ()
    params: list[ParamSpelling] = []
    while i < len(sym):
        if sym[i:] in ("@Z", "Z"):
            return tuple(params)
        if sym[i].isdigit():
            idx = int(sym[i])
            if idx >= len(dec.types):
                return None
            params.append(dec.types[idx])
            i += 1
            continue
        start = i
        t, i = dec.type(i)
        if t is None or (t.base == "void" and not t.indirection):
            return None
        if i - start > 1:  # only multi-character encodings are back-referenced
            dec.types.append(t)
        params.append(t)
    return None
