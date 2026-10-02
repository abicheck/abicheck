# Copyright 2026 Nikolay Petrov
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

"""Comment/string-literal blanking for the lexical pattern pre-scan.

Split out of ``pattern_facts.py`` (which re-exports these names) so the scan
module stays inside its size baseline. Every function here preserves text
length and newlines, so byte offsets and line numbers survive blanking.
"""

from __future__ import annotations

import re

from ..extract.cxx_digit_separator import (
    is_digit_separator as _is_digit_separator,
)

#: Hex-digit set used to tell a C++14 digit separator (`1'000`) from a
#: char-literal opener in the comment/string blanker.

_RAW_STRING_PREFIXES = ("u8R", "uR", "UR", "LR", "R")


def _raw_string_end(text: str, quote_index: int) -> int:
    """Return the closing quote offset for a C++ raw string, or ``-1``."""
    prefix_start = -1
    prefix = ""
    for candidate in _RAW_STRING_PREFIXES:
        start = quote_index - len(candidate)
        if start >= 0 and text[start:quote_index] == candidate:
            prefix_start = start
            prefix = candidate
            break
    if prefix_start < 0:
        return -1
    if prefix_start > 0 and (
        text[prefix_start - 1].isalnum() or text[prefix_start - 1] == "_"
    ):
        return -1
    if prefix != "R" and not prefix.endswith("R"):
        return -1

    open_paren = text.find("(", quote_index + 1, quote_index + 18)
    if open_paren < 0:
        return -1
    delimiter = text[quote_index + 1 : open_paren]
    if any(ch.isspace() or ch in "()\\" for ch in delimiter):
        return -1
    close = ")" + delimiter + '"'
    close_start = text.find(close, open_paren + 1)
    if close_start < 0:
        return -1
    return close_start + len(close) - 1


def _blank_comments_and_strings(text: str, blank_strings: bool = True) -> str:
    """Replace comment (and optionally string/char-literal) *contents* with spaces.

    Preserves every newline and the overall length so byte offsets (and thus
    line numbers) are unchanged — the scan can then match only real code and
    never trips on an ABI keyword mentioned inside a comment or string literal.
    A single forward state machine handles ``//`` / ``/* */`` comments and
    ``"..."`` / ``'...'`` literals with backslash escapes, plus C++ raw string
    literals so embedded quotes do not desynchronize the scanner.

    With ``blank_strings=False`` only comments are blanked and string/char
    literals are preserved verbatim — needed for the ``extern "C"`` rule, whose
    target *is* a string literal.
    """
    # The per-state step functions below define the semantics; this loop only
    # fast-forwards over the runs each state copies or blanks uniformly (plain
    # code, comment bodies, literal bodies), so the steps run once per
    # *interesting* character instead of once per character -- the per-char
    # loop dominated the always-on compare-time pre-scan on large header sets.
    out: list[str] = []
    i, n = 0, len(text)
    state = "code"  # code | line_comment | block_comment | string | char
    while i < n:
        if state == "code":
            m = _CODE_STOP_RE.search(text, i)
            j = m.start() if m else n
            if j > i:
                out.append(text[i:j])
                i = j
                continue
            i, state = _blank_scan_code(text, i, out)
        elif state == "line_comment":
            j = text.find("\n", i)
            j = n if j < 0 else j
            if j > i:
                out.append(_blank_run(text[i:j]))
                i = j
                continue
            i, state = _blank_scan_line_comment(text, i, out)
        elif state == "block_comment":
            j = text.find("*", i)
            j = n if j < 0 else j
            if j > i:
                out.append(_blank_run(text[i:j]))
                i = j
                continue
            i, state = _blank_scan_block_comment(text, i, out)
        else:  # string | char
            m = (_STRING_STOP_RE if state == "string" else _CHAR_STOP_RE).search(
                text, i
            )
            j = m.start() if m else n
            if j > i:
                chunk = text[i:j]
                out.append(_blank_run(chunk) if blank_strings else chunk)
                i = j
                continue
            i, state = _blank_scan_literal(text, i, out, state, blank_strings)
    return "".join(out)


_CODE_STOP_RE = re.compile(r"[/\"']")
_STRING_STOP_RE = re.compile(r'["\\]')
_CHAR_STOP_RE = re.compile(r"['\\]")
_NON_NEWLINE_RE = re.compile(r"[^\n]")


def _blank_run(chunk: str) -> str:
    """*chunk* with every character but ``\\n`` replaced by a space."""
    return _NON_NEWLINE_RE.sub(" ", chunk)


def _blank_scan_code(text: str, i: int, out: list[str]) -> tuple[int, str]:
    """One scanner step in the ``code`` state; returns ``(next_offset, next_state)``."""
    ch = text[i]
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if ch == "/" and nxt == "/":
        out.append("  ")
        return i + 2, "line_comment"
    if ch == "/" and nxt == "*":
        out.append("  ")
        return i + 2, "block_comment"
    if ch == '"':
        raw_end = _raw_string_end(text, i)
        if raw_end >= 0:
            raw = text[i : raw_end + 1]
            # Raw-string bodies are never code, even on the
            # string-preserving path used by `_Pragma`/`extern "C"`.
            out.append("".join("\n" if c == "\n" else " " for c in raw))
            return raw_end + 1, "code"
        out.append('"')
        return i + 1, "string"
    if ch == "'":
        # Distinguish a C++14 digit separator (`1'000`, `0xFF'FF`) from a
        # char-literal opener — misreading a literal as a separator (or
        # vice-versa) would blank the rest of the file.
        out.append("'")
        if not _is_digit_separator(text, i):
            return i + 1, "char"
        return i + 1, "code"
    out.append(ch)
    return i + 1, "code"


def _blank_scan_line_comment(text: str, i: int, out: list[str]) -> tuple[int, str]:
    """One scanner step inside a ``//`` comment; returns ``(next_offset, next_state)``."""
    ch = text[i]
    if ch == "\n":
        # A backslash immediately before the newline (optionally across
        # a CRLF `\r`) splices the next physical line into the `//`
        # comment via C/C++ line continuation, so stay in the comment.
        prev = text[i - 1] if i > 0 else ""
        prev2 = text[i - 2] if i > 1 else ""
        spliced = prev == "\\" or (prev == "\r" and prev2 == "\\")
        out.append("\n")
        return i + 1, ("line_comment" if spliced else "code")
    out.append(" ")
    return i + 1, "line_comment"


def _blank_scan_block_comment(text: str, i: int, out: list[str]) -> tuple[int, str]:
    """One scanner step inside a ``/* */`` comment; returns ``(next_offset, next_state)``."""
    ch = text[i]
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if ch == "*" and nxt == "/":
        out.append("  ")
        return i + 2, "code"
    out.append("\n" if ch == "\n" else " ")
    return i + 1, "block_comment"


def _blank_scan_literal(
    text: str, i: int, out: list[str], state: str, blank_strings: bool
) -> tuple[int, str]:
    """One scanner step inside a string/char literal; returns ``(next_offset, next_state)``."""
    ch = text[i]
    nxt = text[i + 1] if i + 1 < len(text) else ""
    quote = '"' if state == "string" else "'"
    if ch == "\\":
        # Keep the escape + escaped char verbatim when preserving strings,
        # else blank both (newlines always survive for line accounting).
        if not blank_strings:
            out.append(ch + (nxt if nxt else ""))
        else:
            out.append("  " if nxt != "\n" else " \n")
        return i + 2, state
    if ch == quote:
        out.append(quote)
        return i + 1, "code"
    if not blank_strings:
        out.append(ch)
    else:
        out.append("\n" if ch == "\n" else " ")
    return i + 1, state
