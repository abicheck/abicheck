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

"""C integer built-in spellings: the one owner of specifier-order folding.

A leaf (``re`` only), used by ``name_classification.canonicalize_type_name``
and ``diff_symbols_scalar``.
"""

from __future__ import annotations

import re

# The words that make up a C integer built-in's declaration specifiers. A
# spelling composed *only* of these can be reordered freely by the language
# (``unsigned long int`` == ``long unsigned int`` == ``unsigned long``), and
# different toolchains emit different orderings (DWARF/GCC: ``long unsigned
# int``; castxml/clang: ``unsigned long``), so they are normalized to one
# canonical form. Typedefs (``size_t``) and fixed-width names (``uint32_t``)
# contain other words and pass through unchanged.
_INT_SPECIFIER_WORDS = frozenset({"signed", "unsigned", "short", "long", "int", "char"})
_INT_SPECIFIER_RUN_RE = re.compile(
    r"(?<![\w:])(?:(?:signed|unsigned|short|long|int|char)\b\s*)+(?![\w:])"
)


def canonical_int_spelling(t: str) -> str:
    """Canonicalize a bare integer built-in spelling (specifier order and the
    redundant ``int`` are not significant), or return ``t`` unchanged when it
    is not a pure specifier spelling (typedef, fixed-width, ...)."""
    words = t.split()
    if not words or any(w not in _INT_SPECIFIER_WORDS for w in words):
        return t
    unsigned = "unsigned" in words
    if "char" in words:
        if unsigned:
            return "unsigned char"
        if "signed" in words:
            return "signed char"
        return t  # bare ``char`` -- sign is implementation-defined, leave as-is
    if "short" in words:
        return "unsigned short" if unsigned else "short"
    longs = words.count("long")
    if longs >= 2:
        return "unsigned long long" if unsigned else "long long"
    if longs == 1:
        return "unsigned long" if unsigned else "long"
    return "unsigned int" if unsigned else "int"


def canonical_int_runs(text: str) -> str:
    """Apply :func:`canonical_int_spelling` to every maximal run of integer
    specifier words inside a larger spelling (``long unsigned int const *``,
    ``vector<short int>``)."""

    def _sub(m: re.Match[str]) -> str:
        run = m.group(0)
        trail = run[len(run.rstrip()) :]
        return canonical_int_spelling(run.strip()) + trail

    return _INT_SPECIFIER_RUN_RE.sub(_sub, text)
