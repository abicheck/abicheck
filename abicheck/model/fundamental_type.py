# Copyright 2026 Nikolay Petrov
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

"""Fundamental (builtin) type spellings -- a predicate, not an identity.

A fundamental type (``int``, ``float _Complex``, ``__int128``, ``_BitInt(8)``,
a SIMD vector, and any pointer, cv-qualified or array form of one) is spelled
by the language itself. It has no declaration, no declaring file and no
user-given name, so two different fundamental spellings can never be "the same
entity under a new name". Graph reconciliation (``buildsource.graph_reconcile``)
pairs removed/added nodes as a rename or a move only between user-declared
entities; this predicate is how it recognises the entities that are not.

The check is over the spelling as written. A tokenizer, not a lookup table of
whole spellings, so every ordering and cv/pointer/array decoration of a
fundamental keyword set is covered: ``unsigned long long const *`` and
``const long double _Complex[4]`` are both fundamental, while ``struct S *``,
``std::string`` and a user typedef name are not.
"""

from __future__ import annotations

import re

#: Keywords that, in any combination, spell a fundamental C/C++ type. A
#: ``struct``/``class``/``enum``/``union`` tag or any identifier is not here, so
#: a user type never matches.
FUNDAMENTAL_KEYWORDS: frozenset[str] = frozenset(
    {
        "void",
        "bool",
        "_Bool",
        "char",
        "signed",
        "unsigned",
        "short",
        "int",
        "long",
        "float",
        "double",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
        "_Complex",
        "__complex__",
        "_Imaginary",
        "_Float16",
        "_Float32",
        "_Float32x",
        "_Float64",
        "_Float64x",
        "_Float128",
        "_Float128x",
        "__float128",
        "__bf16",
        "__fp16",
        "__int128",
        "__uint128",
        "__int128_t",
        "__uint128_t",
        "_BitInt",
    }
)

_CV_TOKENS: frozenset[str] = frozenset(
    {"const", "volatile", "restrict", "__restrict", "__restrict__"}
)

#: Vector types: ``T __attribute__((vector_size(N)))``, the clang/GCC
#: ``__vector`` keyword, and ``ext_vector_type``. None of them is ever a
#: user-declared entity spelled by its own name.
_VECTOR_MARKERS: tuple[str, ...] = ("vector_size", "__vector", "ext_vector_type")

#: The x86 SIMD vector typedefs (``__m64``, ``__m128i``, ``__m256d``, ...).
_SIMD_TYPEDEF_RE = re.compile(r"__m(?:64|128|256|512)[did]?")

_BITINT_WIDTH_RE = re.compile(r"_BitInt\s*\(\s*\d+\s*\)")
_ARRAY_RE = re.compile(r"\[[^\[\]]*\]")


def is_fundamental_type_spelling(spelling: str | None) -> bool:
    """Whether *spelling* names a fundamental (builtin) type, possibly decorated.

    ``True`` for ``int``, ``const char *``, ``float _Complex``, ``_Float16[4]``,
    ``void (*)(int)``, ``float __attribute__((vector_size(16)))``, ``__m128``.
    ``False`` for ``struct S *``, ``std::string``, ``myint`` (a user typedef),
    and for any spelling that is empty or not a type at all.
    """
    if not spelling:
        return False
    text = spelling.strip()
    if not text:
        return False
    if any(marker in text for marker in _VECTOR_MARKERS):
        return True
    text = _BITINT_WIDTH_RE.sub("_BitInt", text)
    if "(" in text:
        # A function-declarator spelling (``void (*)(int)``): the return type
        # is the text before the first parenthesis, and it decides the answer.
        text = text[: text.index("(")]
    text = _ARRAY_RE.sub(" ", text)
    text = text.replace("*", " ").replace("&", " ")
    tokens = [tok for tok in text.split() if tok not in _CV_TOKENS]
    if not tokens:
        return False
    if len(tokens) == 1 and _SIMD_TYPEDEF_RE.fullmatch(tokens[0]):
        return True
    return all(tok in FUNDAMENTAL_KEYWORDS for tok in tokens)
