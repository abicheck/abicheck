# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""Candidate type-name tokens referenced by a C/C++ type spelling.

The one owner of the small, pure regex scan that ``compare/surface_graph.py``
and ``policy/public_surface.py`` (and, through them, ``surface.py`` and
``export_surface.py``) all need. It previously lived as two leaf-local
copies, because ``compare/`` may not import a ``policy``-layer module; both
layers may import ``model``, so the shared scan lives here instead.
"""

from __future__ import annotations

import functools
import re

#: Tokens that are type qualifiers / builtin keywords, not type names.
TYPE_NOISE: frozenset[str] = frozenset(
    {
        "const",
        "volatile",
        "unsigned",
        "signed",
        "struct",
        "class",
        "union",
        "enum",
        "typename",
        "mutable",
        "restrict",
        "register",
        "void",
        "bool",
        "char",
        "short",
        "int",
        "long",
        "float",
        "double",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
    }
)

#: One identifier token, ``::``-qualified spellings kept whole.
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:]*")


def type_identifiers(type_str: str | None) -> set[str]:
    """Candidate type names referenced by *type_str*: every non-noise
    identifier token, plus the unqualified tail of a ``::``-qualified one.

    Returns a fresh set per call: callers routinely ``|=`` into the result,
    so the memoized value underneath must stay immutable."""
    if not type_str:
        return set()
    return set(_type_identifiers_cached(type_str))


# Pure str -> frozenset, hit millions of times over a few hundred distinct
# spellings on a release-scale compare tail; bounded like
# `name_classification`'s own spelling caches.
@functools.lru_cache(maxsize=1 << 18)
def _type_identifiers_cached(type_str: str) -> frozenset[str]:
    out: set[str] = set()
    for tok in IDENT_RE.findall(type_str):
        if tok in TYPE_NOISE:
            continue
        out.add(tok)
        if "::" in tok:
            out.add(tok.rsplit("::", 1)[1])
    return frozenset(out)
