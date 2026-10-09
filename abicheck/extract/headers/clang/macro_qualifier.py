"""Drops clang's ``MacroQualifiedType`` spelling from a function's type.

When a type attribute is written through an object-like macro
(``#define CALL __attribute__((ms_abi))`` / ``CALL int f(int);``), clang
keeps the macro as sugar and prints its *name* in front of the type:
``qualType`` reads ``"CALL int (int) __attribute__((ms_abi))"`` while
``desugaredQualType`` reads ``"int (int) __attribute__((ms_abi))"``. Read
naively, the return type becomes ``CALL int`` and replacing a literal
``__attribute__((ms_abi))`` with an equivalent macro reports a false
``func_return_changed`` although the compiler sees the identical type.

The attribute itself is still spelled in the trailing ``__attribute__``
tail, so only the leading macro name is redundant. It is recognised
structurally, not by a word list: a valid type spelling never puts a bare
identifier directly before another type-name token (clang prints
qualifiers first and joins nested names with ``::``), so a leading bare
identifier that is followed by another identifier or keyword -- and is
absent from the desugared spelling -- can only be a macro qualifier. A
typedef'd return type (``size_t (int)``) is never followed by another
token and is kept.
"""

from __future__ import annotations

import re
from typing import Any

from .context import qualtype

__all__ = ["node_qualtype_without_macro_qualifiers", "strip_macro_qualifiers"]

_LEADING_WORD_RE = re.compile(r"([A-Za-z_]\w*)\s+(?=[A-Za-z_])")

#: Keywords that may legitimately lead a type spelling.
_TYPE_KEYWORDS = frozenset(
    {
        "const", "volatile", "restrict", "__restrict", "__restrict__",
        "_Atomic", "unsigned", "signed", "short", "long", "int", "char",
        "void", "float", "double", "bool", "_Bool", "_Complex", "struct",
        "union", "enum", "class", "typename", "wchar_t", "char8_t",
        "char16_t", "char32_t", "__int128", "auto", "decltype",
    }
)  # fmt: skip


def strip_macro_qualifiers(qualtype: str, desugared: str | None) -> str:
    """*qualtype* without leading macro-qualifier names (see module doc)."""
    if not desugared or desugared == qualtype:
        return qualtype
    desugared_words = set(re.findall(r"[A-Za-z_]\w*", desugared))
    out = qualtype
    while m := _LEADING_WORD_RE.match(out):
        word = m.group(1)
        if word in _TYPE_KEYWORDS or word in desugared_words:
            break
        out = out[m.end() :]
    return out


def node_qualtype_without_macro_qualifiers(node: dict[str, Any]) -> str:
    """*node*'s ``qualType`` (as :func:`.context.qualtype` reads it) with
    leading macro-qualifier names dropped."""
    type_obj = node.get("type")
    desugared = (
        type_obj.get("desugaredQualType") if isinstance(type_obj, dict) else None
    )
    return strip_macro_qualifiers(
        qualtype(node), desugared if isinstance(desugared, str) else None
    )
