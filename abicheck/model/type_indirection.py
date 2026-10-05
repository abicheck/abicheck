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

"""The declarator *indirection shape* of a type spelling, and the one
question a partially-unresolved spelling can still answer: did the
pointer/reference/array structure provably change?

castxml composes an unresolved pointee into the enclosing spelling
(``"?*"``, ``"?&"``, ``"const ?"`` -- see
:func:`castxml_spelling_artifacts.has_unresolved_component`). Such a
spelling has an unknown core but a *known* outer structure: ``"?*"`` is a
pointer to something, so ``int`` -> ``"?*"`` is a change whatever the
pointee is. Two spellings whose known structure is compatible
(``"?*"``/``"?*"``, ``"int **"``/``"?*"`` -- the ``?`` may itself be
``int *``) stay unknown: no finding is fabricated.

The rule, stated once for every caller (variables, function returns and
parameters):

* A shape is the declarator levels read **outermost first** (``*``, ``&``,
  ``&&``, ``[]``), cv qualifiers ignored (they never change indirection).
* A shape is **closed** when its core is a builtin fundamental type --
  then the levels are exact. Any other core (``?``, a class, a typedef
  name -- a typedef may itself be a pointer) is **open**: the real type
  has *at least* those levels, possibly more inside the core.
* Two shapes provably differ iff they cannot describe the same type: both
  closed and unequal, one closed and the open one's levels are not a
  prefix of the closed one's, or both open and neither is a prefix of the
  other.
* A spelling this module cannot parse (function pointers, anything with a
  parenthesis) has no shape, and never provably differs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .castxml_spelling_artifacts import has_unresolved_component

__all__ = [
    "IndirectionShape",
    "indirection_provably_differs",
    "indirection_shape",
    "pointer_depth_provably_differs",
    "unresolved_pair_verdict",
]

#: Tokens that, alone or combined, spell a C/C++ fundamental type. Such a
#: core can never hide a further indirection level.
_BUILTIN_TOKENS = frozenset(
    {
        "void",
        "bool",
        "_Bool",
        "char",
        "wchar_t",
        "char8_t",
        "char16_t",
        "char32_t",
        "short",
        "int",
        "long",
        "signed",
        "unsigned",
        "float",
        "double",
        "__int128",
        "_Float16",
        "__bf16",
        "nullptr_t",
    }
)
_CV_TOKENS = frozenset({"const", "volatile", "restrict", "__restrict", "__restrict__"})
_TRAILING_CV_RE = re.compile(
    r"(?:\s+|^)(?:const|volatile|restrict|__restrict__|__restrict)\s*$"
)
_TRAILING_ARRAY_RE = re.compile(r"\[[^\[\]]*\]\s*$")


@dataclass(frozen=True)
class IndirectionShape:
    """Declarator levels, outermost first, and whether they are exact."""

    levels: tuple[str, ...]
    closed: bool


def _strip_trailing_cv(text: str) -> str:
    while True:
        stripped = _TRAILING_CV_RE.sub("", text).rstrip()
        if stripped == text:
            return text
        text = stripped


def indirection_shape(
    spelling: str | None, *, decay_arrays: bool = False
) -> IndirectionShape | None:
    """*spelling*'s shape, or ``None`` when it cannot be parsed.

    *decay_arrays* applies function-parameter adjustment: an outermost
    array level is the pointer it decays to."""
    if spelling is None or "(" in spelling or ")" in spelling:
        return None
    text = _strip_trailing_cv(spelling.strip())
    arrays: list[str] = []
    while (m := _TRAILING_ARRAY_RE.search(text)) is not None:
        arrays.insert(0, "[]")
        text = text[: m.start()].rstrip()
    pointers: list[str] = []
    while True:
        text = _strip_trailing_cv(text)
        if text.endswith("&&"):
            pointers.append("&&")
            text = text[:-2].rstrip()
        elif text.endswith(("&", "*")):
            pointers.append(text[-1])
            text = text[:-1].rstrip()
        else:
            break
    if "[" in text or "]" in text or not text:
        return None
    core = [t for t in text.split() if t not in _CV_TOKENS]
    if not core:
        return None
    levels = tuple(arrays + pointers)
    if decay_arrays and levels and levels[0] == "[]":
        levels = ("*",) + levels[1:]
    closed = all(t in _BUILTIN_TOKENS for t in core)
    if not closed and any("?" in t for t in core) and core != ["?"]:
        # A sentinel fused into some larger token is not a shape we know.
        return None
    return IndirectionShape(levels=levels, closed=closed)


def _is_prefix(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    return len(short) <= len(long) and long[: len(short)] == short


def indirection_provably_differs(
    old: str | None, new: str | None, *, decay_arrays: bool = False
) -> bool:
    """Whether *old* and *new* cannot be the same type on indirection
    structure alone -- see the module docstring for the rule."""
    a = indirection_shape(old, decay_arrays=decay_arrays)
    b = indirection_shape(new, decay_arrays=decay_arrays)
    if a is None or b is None:
        return False
    if a.closed and b.closed:
        return a.levels != b.levels
    if a.closed:
        return not _is_prefix(b.levels, a.levels)
    if b.closed:
        return not _is_prefix(a.levels, b.levels)
    return not (_is_prefix(a.levels, b.levels) or _is_prefix(b.levels, a.levels))


def pointer_depth_provably_differs(old: str | None, new: str | None) -> bool:
    """Whether the number of pointer (``*``) levels provably differs.

    A closed shape's ``*`` count is exact; an open one's is a lower bound
    (its core may itself be a pointer). So a difference is provable only
    when both are exact and unequal, or an open side already has more
    ``*`` levels than an exact one."""
    a, b = indirection_shape(old), indirection_shape(new)
    if a is None or b is None:
        return False
    da, db = a.levels.count("*"), b.levels.count("*")
    if a.closed and b.closed:
        return da != db
    if a.closed:
        return db > da
    if b.closed:
        return da > db
    return False


def unresolved_pair_verdict(
    old: str | None,
    new: str | None,
    *,
    decay_arrays: bool = False,
    pointer_depth: bool = False,
) -> bool | None:
    """The verdict for a pair where either spelling is (partly) unresolved.

    ``None`` when both are fully resolved -- the caller's ordinary
    comparison applies. Otherwise ``False`` for a missing or wholly unknown
    (``"?"``) side (RD2-5) and for a pair whose known structure is
    compatible (``"?*"``/``"?*"``, ``int **``/``"?*"``), and ``True`` only
    when the known structure proves a change (``int`` -> ``"?*"``).
    *pointer_depth* asks the narrower pointer-level question
    (:func:`pointer_depth_provably_differs`) instead of the whole shape."""
    if old is None or new is None or "?" in (old.strip(), new.strip()):
        return False
    if not (has_unresolved_component(old) or has_unresolved_component(new)):
        return None
    if pointer_depth:
        return pointer_depth_provably_differs(old, new)
    return indirection_provably_differs(old, new, decay_arrays=decay_arrays)
