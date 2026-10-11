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

A function-like macro is printed with its arguments
(``"CALL(ms_abi) int (int) __attribute__((ms_abi))"``), so the leading name
may carry one balanced parenthesised group; switching ``CALL(sysv_abi)`` to
``CALL(ms_abi)`` otherwise read as a ``func_return_changed`` next to the
real calling-convention change (2026-10-11 re-audit).
"""

from __future__ import annotations

import re
from typing import Any

from .context import qualtype

__all__ = ["node_qualtype_without_macro_qualifiers", "strip_macro_qualifiers"]

#: A leading name, then either its macro arguments -- printed with no space
#: before ``(``, unlike a parameter list (``size_t (int)``) -- or a next word.
_LEADING_WORD_RE = re.compile(r"([A-Za-z_]\w*)(?=\(|\s+[A-Za-z_])")
_NEXT_WORD_RE = re.compile(r"\s+(?=[A-Za-z_])")

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
        end = _after_macro_arguments(out, m.end())
        nxt = _NEXT_WORD_RE.match(out, end) if end != -1 else None
        if nxt is None:
            break
        out = out[nxt.end() :]
    return out


def _after_macro_arguments(text: str, at: int) -> int:
    """The index past a function-like macro's balanced ``(...)`` starting at
    *at*, *at* itself when none starts there, or ``-1`` when the group is
    unbalanced."""
    i = at
    if i >= len(text) or text[i] != "(":
        return at
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j + 1
    return -1


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
