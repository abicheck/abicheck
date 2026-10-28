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


"""Markdown-escaping primitive shared by every renderer in this package.

One owner for "this value came from outside the report -- a symbol, a type
name, a file name, an extractor's error text, a detector's description --
and must not be able to close a code span, end a table row, start a heading,
or be read as inline HTML by a CommonMark reader".

:func:`md_text` has three shapes, chosen by keyword:

* prose (default): inline-special characters are backslash-escaped, so a
  demangled ``std::vector<int>`` reads back as written instead of being
  dropped as an HTML tag, and a backtick cannot open a span;
* ``code=True``: a complete code span. The fence is one backtick longer than
  the longest backtick run inside the value, with padding spaces when the
  value begins or ends with a backtick or a space (CommonMark section 6.1);
* ``table=True``: a table cell. A pipe is escaped, and a run of backslashes
  before a pipe is doubled so GFM's backslash pairing cannot leave a live
  cell separator behind.

Both table and prose shapes flatten control characters to spaces, so no
value can split a row or a bullet. Deliberately a dependency-free leaf -- it
imports nothing from this package -- so any renderer can depend on it.
"""

from __future__ import annotations

import re
import string

_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
_BACKTICK_RUN = re.compile(r"`+")
_PROSE_SPECIAL = frozenset("*[]<>~")
_ASCII_PUNCT = frozenset(string.punctuation)


def _is_word(ch: str) -> bool:
    return bool(ch) and ch.isalnum()


def md_text(value: object, *, code: bool = False, table: bool = False) -> str:
    """*value* as Markdown text a reader sees exactly as written.

    With ``code=True`` the result is a whole code span (backticks included);
    otherwise it is escaped prose, or a table-cell fragment with ``table=True``.
    Control characters become spaces and runs of whitespace collapse, so the
    result is one line.
    """
    flat = " ".join(
        "".join(
            " " if ord(ch) < 0x20 or ord(ch) == 0x7F else ch for ch in str(value)
        ).split()
    )
    out: list[str] = []
    index = 0
    length = len(flat)
    while index < length:
        ch = flat[index]
        if ch == "\\":
            run_end = index
            while run_end < length and flat[run_end] == "\\":
                run_end += 1
            count = run_end - index
            nxt = flat[run_end] if run_end < length else ""
            # A backslash run is structural before a table pipe (GFM pairs
            # backslashes before it splits cells). In prose, CommonMark reads
            # `\\` as one escaped backslash, so a run of two or more must be
            # doubled, and a lone backslash only when it would escape the
            # punctuation after it or end the text. A code span is literal.
            doubled = (table and nxt == "|") or (
                not code and (count > 1 or nxt == "" or nxt in _ASCII_PUNCT)
            )
            out.append("\\" * (count * 2 if doubled else count))
            index = run_end
            continue
        if ch == "|":
            out.append("\\|" if table else "|")
        elif code:
            out.append(ch)
        elif ch == "`":
            out.append("\\`")
        elif ch in _PROSE_SPECIAL:
            out.append("\\" + ch)
        elif ch == "_":
            # An intraword underscore (snake_case, naïve_函数) cannot open or
            # close emphasis, so only a boundary underscore is escaped.
            prev = flat[index - 1] if index else ""
            nxt = flat[index + 1] if index + 1 < length else ""
            out.append("_" if _is_word(prev) and _is_word(nxt) else "\\_")
        elif ch == "&" and _ENTITY.match(flat, index):
            out.append("\\&")
        else:
            out.append(ch)
        index += 1

    body = "".join(out)
    if not code:
        return body
    if not body:
        # CommonMark has no empty code span; a single space is the nearest.
        body = " "
    longest = max((len(run.group()) for run in _BACKTICK_RUN.finditer(body)), default=0)
    fence = "`" * (longest + 1)
    if body.strip(" ") and (body[:1] in (" ", "`") or body[-1:] in (" ", "`")):
        body = f" {body} "
    return f"{fence}{body}{fence}"


__all__ = ["md_text"]
