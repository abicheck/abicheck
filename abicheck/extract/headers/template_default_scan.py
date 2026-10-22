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

"""Header-text scan for class-template parameter defaults (ADR-085 D3).

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
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

__all__ = ["header_declares_template_default", "template_default_header_scan"]

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
    angle = parens = 0
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
                return False
            angle -= 1
        elif c == "=" and angle == 0:
            prev = text[i - 1] if i > start else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if prev not in ("=", "!", "<", ">") and nxt != "=":
                return True
    return False


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
