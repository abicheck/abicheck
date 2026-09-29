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

"""Recover ``inline`` from an out-of-line member definition castxml never shows.

C++ makes a function inline if **any** of its declarations says so
([dcl.inline]/6). The everyday header shape that relies on this is::

    struct primitive { void execute(const stream&) const; };   // no `inline`
    inline void primitive::execute(const stream& s) const { ... }

(oneDNN's ``dnnl.hpp`` defines most of its C++ API this way.) castxml emits
exactly one ``Method`` element per member, for the *first* declaration, and
its ``inline`` attribute reflects that declaration alone -- verified against
castxml 0.7.0, which prints no ``inline`` and no definition location for the
member above. The parser therefore recorded ``is_inline=False`` and the
public-header/export cross-check demanded an exported symbol a header-defined
inline never has (a false ``public_not_exported``). The clang backend sees
both declarations and folds them (``extract/headers/clang/functions.py``).

castxml's XML carries no evidence of the later redeclaration at all, so the
only place it survives is the header text. This module indexes the
out-of-line ``inline``/``constexpr``/``consteval`` *qualified* member
declarations in the headers next to the member's own declaring file, keyed
by ``(enclosing class leaf, member leaf)`` plus the parameter count.

**Scope and limits (deliberate).** Only the declaring file's own directory
is indexed (non-recursively) -- a definition placed in a header elsewhere is
not recovered, which keeps the answer at today's (``False``) rather than
guessing. Two overloads with the same class leaf, member name and arity
cannot be told apart textually; both are then read as inline. That errs
toward "owes no export", which for ``public_not_exported`` (a RISK check) is
the quiet direction, and it matches what the language would say for at least
one of them.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree.ElementTree import Element

from .context import CastxmlParserContext

__all__ = ["declared_inline_out_of_line", "index_out_of_line_inline"]

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_INLINE_KEYWORD_RE = re.compile(r"\b(?:inline|constexpr|consteval)\b")
# `Class::member(` or `Class<Args>::member(` -- the *last* two components of a
# qualified member declarator. A regex search yields the leftmost match, and a
# leftmost `A::B` not followed by `(` (a namespace or a qualified return type)
# does not match, so `ns::Cls::f(` resolves to `Cls::f`.
_QUALIFIED_MEMBER_RE = re.compile(
    r"\b([A-Za-z_]\w*)\s*(?:<[^;{}()]*>)?\s*::\s*"
    r"(~\s*[A-Za-z_]\w*|operator\s*(?:\(\s*\)|[^\s(\w][^\s(]*)|[A-Za-z_]\w*)\s*\("
)

#: castxml element tags that name a class member this recovery applies to.
_MEMBER_TAGS = frozenset({"Method", "OperatorMethod", "Constructor", "Destructor"})

Index = dict[tuple[str, str], set[int]]


def _strip_comments(text: str) -> str:
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub(" ", text))


def _matching_close(text: str, open_paren: int) -> int | None:
    """Index of the bracket closing the one at *open_paren*, or ``None``."""
    depth = 0
    for i in range(open_paren, len(text)):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _top_level_commas(inner: str) -> int:
    """Commas in *inner* outside nested brackets and template arguments."""
    depth = angle = commas = 0
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch == "<":
            angle += 1
        elif depth == 0 and ch == ">" and angle:
            angle -= 1
        elif depth == 0 and angle == 0 and ch == ",":
            commas += 1
    return commas


def _arity(text: str, open_paren: int) -> int | None:
    """Top-level parameter count of the list opening at *open_paren*."""
    close = _matching_close(text, open_paren)
    if close is None:
        return None
    inner = text[open_paren + 1 : close].strip()
    if inner in ("", "void"):
        return 0
    return _top_level_commas(inner) + 1


def _normalize_member(member: str) -> str:
    return re.sub(r"\s+", "", member)


def index_out_of_line_inline(text: str) -> Index:
    """Map ``(class leaf, member leaf)`` -> arities of inline qualified decls in *text*."""
    source = _strip_comments(text)
    index: Index = {}
    for kw in _INLINE_KEYWORD_RE.finditer(source):
        end = len(source)
        for stop in ("{", ";"):
            pos = source.find(stop, kw.end())
            if pos != -1:
                end = min(end, pos)
        region_match = _QUALIFIED_MEMBER_RE.search(source, kw.end(), end)
        if region_match is None:
            continue
        arity = _arity(source, region_match.end() - 1)
        if arity is None:
            continue
        key = (region_match.group(1), _normalize_member(region_match.group(2)))
        index.setdefault(key, set()).add(arity)
    return index


def _file_index(ctx: CastxmlParserContext, path: str) -> Index:
    cache = ctx.out_of_line_inline_index
    cached = cache.get(path)
    if cached is not None:
        return cached
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    result = index_out_of_line_inline(text)
    cache[path] = result
    return result


def _directory_files(ctx: CastxmlParserContext, declaring: str) -> list[str]:
    if ctx.files_by_directory is None:
        by_dir: dict[str, list[str]] = {}
        for el in ctx.root.findall("File"):
            name = el.get("name", "")
            if name and not name.startswith("<"):
                by_dir.setdefault(str(Path(name).parent), []).append(name)
        ctx.files_by_directory = by_dir
    siblings = ctx.files_by_directory.get(str(Path(declaring).parent), [])
    return [declaring, *(name for name in siblings if name != declaring)]


def _member_key(ctx: CastxmlParserContext, el: Element) -> tuple[str, str] | None:
    class_el = ctx.resolve(el.get("context", ""))
    if class_el is None or class_el.tag not in ("Class", "Struct", "Union"):
        return None
    class_leaf = class_el.get("name", "")
    if not class_leaf:
        return None
    if el.tag == "Constructor":
        return class_leaf, class_leaf
    if el.tag == "Destructor":
        return class_leaf, "~" + class_leaf
    name = el.get("name", "")
    if el.tag == "OperatorMethod":
        return class_leaf, "operator" + _normalize_member(name)
    return (class_leaf, name) if name else None


def declared_inline_out_of_line(ctx: CastxmlParserContext, el: Element) -> bool:
    """Whether a later, out-of-line declaration of member *el* is ``inline``."""
    if el.tag not in _MEMBER_TAGS or el.get("inline") == "1":
        return False
    key = _member_key(ctx, el)
    if key is None:
        return False
    file_el = ctx.id_map.get(el.get("file", ""))
    declaring = file_el.get("name", "") if file_el is not None else ""
    if not declaring or declaring.startswith("<"):
        return False
    arity = sum(1 for child in el if child.tag in ("Argument", "Ellipsis"))
    return any(
        arity in _file_index(ctx, path).get(key, ())
        for path in _directory_files(ctx, declaring)
    )
