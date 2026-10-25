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

"""Header-text scan for template-parameter defaults (ADR-085 D3).

castxml emits no record for an uninstantiated class template, so a castxml
run cannot see a default-argument change in a header. This scan answers the
narrower question castxml *can* be checked against: does a public header
declare a template parameter list with a default argument at all? When one
does, the castxml snapshot states that the defaults are not recorded, so the
run reads as partial rather than as a clean comparison.

The scan is a text heuristic over the headers the parse was given. Comments
and string literals are stripped first. A macro-generated ``template <...>``
is not seen; a header included indirectly is not read. Both make the scan
say ``False``, which is the same as no gap reported, not a clean pass.

The clang backend records class- and function-template defaults from its AST
but models neither alias nor variable templates, so a clang run scans for
those two families only (:func:`alias_or_variable_template_default_scan`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

__all__ = [
    "alias_or_variable_template_default_scan",
    "header_declares_alias_or_variable_template_default",
    "header_declares_template_default",
    "template_default_header_scan",
]

_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
_TEMPLATE_OPEN = re.compile(r"\btemplate\s*<")


def _list_has_default(text: str, start: int) -> bool:
    """Whether the template parameter list opened just before *start* has a
    top-level ``=`` (a default argument).

    ``<``/``>`` count as brackets only outside parentheses, so a comparison
    inside a default such as ``(N > 2)`` does not close the list. ``==``,
    ``!=``, ``<=`` and ``>=`` are comparisons, not defaults.
    """
    return _scan_list(text, start)[0]


def _scan_list(text: str, start: int) -> tuple[bool, int]:
    """``(has_default, end)`` for the template parameter list opened just
    before *start*: whether it has a top-level default (see
    :func:`_list_has_default`) and the index just past its closing ``>``,
    or ``-1`` when the list never closes (then it has no default either)."""
    angle = parens = 0
    has_default = False
    for i in range(start, len(text)):
        c = text[i]
        if c == "(":
            parens += 1
        elif c == ")":
            parens = max(parens - 1, 0)
        elif parens:
            continue
        elif c == "<":
            angle += 1
        elif c == ">":
            if angle == 0:
                return has_default, i + 1
            angle -= 1
        elif c == "=" and angle == 0:
            prev = text[i - 1] if i > start else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if prev not in ("=", "!", "<", ">") and nxt != "=":
                has_default = True
    return False, -1


def header_declares_template_default(text: str) -> bool:
    """Whether the header *text* declares a class or function template whose
    parameter list carries a default argument."""
    stripped = _STRING.sub('""', _COMMENT.sub(" ", text))
    return any(
        _list_has_default(stripped, match.end())
        for match in _TEMPLATE_OPEN.finditer(stripped)
    )


def template_default_header_scan(paths: Iterable[str | Path]) -> bool:
    """Whether any header in *paths* declares a template parameter default.

    A file that cannot be read is skipped: castxml already parsed every path
    it was given, so an unreadable one is not a reason to guess either way.
    """
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if header_declares_template_default(text):
            return True
    return False


#: A declaration after a template parameter list that is neither an alias nor
#: a variable template: a class/union template, a concept, a friend, or the
#: inner list of a member template of a class template.
_NOT_ALIAS_OR_VARIABLE = frozenset(
    {"class", "struct", "union", "enum", "concept", "friend", "template"}
)
_WORD = re.compile(r"[A-Za-z_]\w*")


def _declares_alias_or_variable(text: str, end: int) -> bool:
    """Whether the declaration that starts at *end* (just past a template
    parameter list) is an alias template (``using X = ...``) or a variable
    template (``constexpr int v = ...``/``int v{...}``).

    A function template reaches ``(`` before any ``=`` or ``{`` at the top
    level; a variable reaches ``=`` or ``{`` first. ``<``/``>`` nest only
    outside parentheses, as in :func:`_list_has_default`.
    """
    word = _WORD.match(text, len(text) - len(text[end:].lstrip()))
    if word is not None:
        if word.group() == "requires":
            return _declares_alias_or_variable(
                text, _skip_requires_clause(text, word.end())
            )
        if word.group() == "using":
            return True
        if word.group() in _NOT_ALIAS_OR_VARIABLE:
            return False
    angle = parens = 0
    for i in range(end, len(text)):
        c = text[i]
        if c == "(":
            if parens == 0 and angle == 0:
                return False
            parens += 1
        elif c == ")":
            parens = max(parens - 1, 0)
        elif parens:
            continue
        elif c == "<":
            angle += 1
        elif c == ">":
            angle = max(angle - 1, 0)
        elif angle:
            continue
        elif c in "={;":
            # ``;`` ends a variable declared without an initializer
            # (``extern T value;``); a function reached ``(`` first.
            return True
    return False


_CONSTRAINT_NAME = re.compile(r"\s*(?:::)?[A-Za-z_][\w:]*")
_CONSTRAINT_JOIN = re.compile(r"\s*(?:&&|\|\|)")


def _skip_balanced(text: str, start: int, open_c: str, close_c: str) -> int:
    """Index just past the bracket at *start* and its balanced partner."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == open_c:
            depth += 1
        elif text[i] == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _skip_requires_clause(text: str, pos: int) -> int:
    """Index just past a C++20 ``requires`` clause whose keyword ends at *pos*:
    primaries (``(expr)`` or ``Concept<T>``/``trait_v<T>``) joined by
    ``&&``/``||``."""
    while True:
        rest = len(text) - len(text[pos:].lstrip())
        if rest < len(text) and text[rest] == "(":
            pos = _skip_balanced(text, rest, "(", ")")
        else:
            name = _CONSTRAINT_NAME.match(text, pos)
            if name is None:
                return pos
            pos = name.end()
            after = len(text) - len(text[pos:].lstrip())
            if after < len(text) and text[after] == "<":
                pos = _skip_balanced(text, after, "<", ">")
        join = _CONSTRAINT_JOIN.match(text, pos)
        if join is None:
            return pos
        pos = join.end()


def header_declares_alias_or_variable_template_default(text: str) -> bool:
    """Whether the header *text* declares an alias or variable template whose
    parameter list carries a default argument.

    The clang backend records class- and function-template defaults but
    models neither alias nor variable templates, so only these two families
    leave a clang run unable to assess a default change (ADR-085 D3).
    """
    stripped = _STRING.sub('""', _COMMENT.sub(" ", text))
    for match in _TEMPLATE_OPEN.finditer(stripped):
        has_default, end = _scan_list(stripped, match.end())
        if has_default and _declares_alias_or_variable(stripped, end):
            return True
    return False


def alias_or_variable_template_default_scan(paths: Iterable[str | Path]) -> bool:
    """Whether any header in *paths* declares an alias or variable template
    with a default template argument. Unreadable files are skipped, as in
    :func:`template_default_header_scan`."""
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if header_declares_alias_or_variable_template_default(text):
            return True
    return False
