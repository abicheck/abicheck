# SPDX-License-Identifier: Apache-2.0
"""Whether a castxml ``Variable`` is declared ``inline`` (a C++17 inline variable).

castxml gives a function its ``inline`` attribute but emits none for a variable,
so the declaring line is the only record of the keyword. Used only to decide
whether a non-const header variable's initializer is its definition
(``inline int x = 1;``), which the value channel then carries. The clang
frontend reads the keyword from its own AST (``VarDecl.inline``) instead.

Limit: the keyword must sit on the declaration's own line, before the name, as
it does in practice. A declaration that splits ``inline`` onto an earlier line
is read as not inline, which leaves its value unset (never a fabricated one).
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["declared_inline"]

_INLINE_RE = re.compile(r"\binline\b")


def declared_inline(source_location: str | None, name: str) -> bool:
    """Whether the line of *source_location* (``file:line``) declares *name* inline.

    A comment is ignored, and the keyword must come before the name on the line.
    An unreadable or out-of-range location is not inline.
    """
    if not source_location or not name:
        return False
    path_text, _, line_text = source_location.rpartition(":")
    if not line_text.isdigit():
        return False
    try:
        lines = Path(path_text).read_text(errors="replace").splitlines()
    except OSError:
        return False
    line_no = int(line_text)
    if not 1 <= line_no <= len(lines):
        return False
    code = lines[line_no - 1].split("//", 1)[0]
    keyword = _INLINE_RE.search(code)
    if keyword is None:
        return False
    return re.search(rf"\b{re.escape(name)}\b", code[keyword.end() :]) is not None
