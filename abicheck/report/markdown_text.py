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


"""Markdown-escaping primitives shared by every renderer in this package.

One owner for "this value came from outside the report -- a file name, an
extractor's error text, a detector's description -- and must not be able to
close a code span, end a table row, or start a heading of its own". Lifted
out of ``comparison_scope.py``'s own private helper when a second renderer
(``no_baseline.py``'s finding tables) needed the identical rule: a second
copy is how the two drift, and only one of them then gets the next fix.

Deliberately a dependency-free leaf -- it imports nothing from this package
-- so any renderer can depend on it without a cycle.
"""

from __future__ import annotations


def md_cell(value: object) -> str:
    """One Markdown table-cell/code-span-safe line for a value the report
    does not control: control characters flattened to spaces, a table pipe
    escaped, a backtick neutralized, so neither can close a span, end a row,
    or start a heading of its own (Codex review, twenty-first round).

    Newlines are control characters by this rule, so a multi-line detector
    description collapses to one spaced line rather than breaking the table
    apart at its first ``\n`` (CodeRabbit review of the audit renderer).

    A **run of backslashes immediately before a pipe** is doubled, and it is
    the one case a raw backslash is structural rather than cosmetic: GFM
    pairs backslashes off before splitting cells, so an *odd*-length run
    swallows the escaping backslash this function emits for the following pipe and leaves
    a live cell separator -- one extra cell in the row, from a value the
    report does not control. Doubling the run restores the pairing for any
    length. Found by the generated-input property tests in
    ``tests/test_markdown_cell.py``, which falsified two successively
    narrower attempts at this rule (no escaping at all, then escaping only
    the run's last backslash) -- neither of which a hand-written example had
    caught in the years the original helper shipped. Every *other* backslash
    is left exactly as written, so a Windows path or a mangled name still
    renders as itself inside its code span.
    """
    text = str(value)
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if ch == "|":
            out.append("\\|")
            index += 1
        elif ch == "`":
            out.append("'")
            index += 1
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(" ")
            index += 1
        elif ch == "\\":
            run = index
            while run < length and text[run] == "\\":
                run += 1
            count = run - index
            structural = run < length and text[run] == "|"
            out.append("\\" * (count * 2 if structural else count))
            index = run
        else:
            out.append(ch)
            index += 1
    return " ".join("".join(out).split())
