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

"""Quoted-``#include`` expansion for the header-text dialect scans.

Moved out of ``dumper_ast_config_cpp20.py`` (at its ADR-061 no-growth
baseline) when that scan gained a memo (``header_scan_memo``). The memo needs
the expansion to learn which files a scan result depends on, and following
``#include "..."`` the way the translation unit would is its own
responsibility, not part of deciding the C++ dialect. Bodies are unchanged.
``_strip_raw_strings`` moves with it because the expansion needs it; the
dialect scan imports it back from here.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..dumper_ast_config_cpp20_chains import _strip_inactive_if_zero_blocks

# C++11 raw string literal: [prefix]R"delim(...)delim" — the standard
# permits an optional encoding prefix (u8, u, U, L) directly before the R,
# e.g. u8R"(...)"; without it, "\bR" never matches after "u8"/"u"/"U"/"L"
# since both characters are \w (no boundary between them), leaving a
# prefixed raw string completely unstripped (Codex review). The delimiter
# (d-char-sequence) grammar permits any basic-source character except
# whitespace, parentheses, and backslash — not just identifier characters
# (a delimiter like "tag-" is valid and was missed by an earlier,
# identifier-only version of this pattern (Codex review) — matching the
# exclusion directly is simpler and more complete than enumerating every
# permitted punctuation character. Not handled by _STRING_LITERAL_PATTERN
# (only ordinary "..." literals) or by the plain-comment stripper, so its
# body was otherwise scanned as ordinary code: text that merely *looks*
# like a requires-expression/concept inside a raw string would force
# -std=gnu++20 unnecessarily — worse once a multi-line construct can span
# into a raw string's later lines too. DOTALL so the (non-greedy) body can
# span newlines.
_RAW_STRING_LITERAL_PATTERN = re.compile(
    rb'\b(?:u8|u|U|L)?R"([^\s()\\]{0,16})\((?:.*?)\)\1"', re.DOTALL
)


def _strip_raw_strings(content: bytes) -> bytes:
    """Blank C++11 raw string literals entirely (delimiter and body alike),
    preserving embedded newlines so line numbers reported for code after a
    multi-line raw string stay accurate (mirrors the block-comment
    stripper's newline-preserving approach)."""
    return _RAW_STRING_LITERAL_PATTERN.sub(
        lambda m: b"\n" * m.group(0).count(b"\n"), content
    )


_QUOTED_INCLUDE_PATTERN = re.compile(
    rb'^[ \t]*#[ \t]*include[ \t]+"([^"]+)"', re.MULTILINE
)


def _expand_with_quoted_includes(
    header_paths: list[Path], *, for_language_mode_decision: bool = False
) -> list[Path]:
    """Expand *header_paths* with files reachable via a quoted ``#include
    "..."``, resolved relative to the including file's own directory
    (Codex review): a caller often designates just one umbrella entry
    point (``#include "concepts.hpp"`` and nothing else) whose only C++20
    signal actually lives in the included file, not the umbrella file
    itself — castxml/clang parse the transitive include as part of the
    same translation unit regardless of whether it was named directly, so
    the dialect decision must see it too.

    Deliberately narrow, matching this file's incremental-per-reported-
    case scope: only the quoted spelling is followed (an angle-bracket
    ``#include <...>`` is typically a system/toolchain header resolved via
    ``-I`` search paths this heuristic doesn't have access to, not a
    project header); resolution checks only the including file's own
    directory (the first location the standard's quoted-include search
    always checks), not any ``-I`` search path. Cycle-safe (visited by
    resolved absolute path) and silently skips an unreadable file or an
    include that doesn't resolve to a real file on disk — the same
    conservative, best-effort spirit as the rest of this scan.

    The ``#include`` line itself is only followed if it is *reachable*
    under the same preprocessor-guard reasoning the rest of this file
    already applies (Codex review, twenty-seventh round): a naive raw-text
    scan for the directive would follow it even when it sits inside an
    inactive or (for ``for_language_mode_decision``) C++-only guard --
    e.g. an otherwise-C header wrapping ``#include "cxx20.hpp"`` in
    ``#ifdef __cplusplus`` -- wrongly pulling the included file's C++20
    syntax into scope for a decision where that guard is actually false.
    ``for_language_mode_decision`` is forwarded unchanged to
    ``_strip_inactive_if_zero_blocks`` (matching the polarity the caller
    uses for its own reachability scan) before searching for the
    directive, so an include line blanked as unreachable there is never
    followed here either.

    Two more Codex-review fixes narrow this scan further, in opposite
    directions: a backslash-newline-continued directive (``#include``
    followed by a trailing backslash, then the filename on the next
    physical line -- valid C/C++, spliced away in translation phase 2
    before the preprocessor ever sees an ``#include`` token split across
    two lines) previously wasn't recognized at all, since the pattern only
    matches within one physical line -- continuations are spliced away
    first so the directive is joined back onto one line before matching.
    And a raw string literal's
    body is blanked before matching (unlike an *ordinary* string literal,
    deliberately left alone above so a genuine include's own argument
    survives) so a ``#include "..."``-looking line trapped inside one
    (never a real directive) is never mistaken for one.

    Continuations are spliced *before* comments are stripped, matching
    genuine translation-phase order (phase 2 splicing precedes phase 3
    comment recognition) -- Codex review, further round: a ``//`` line
    comment ending in a trailing backslash extends over its continuation
    too, so a ``// comment`` with a trailing backslash followed by
    ``#include "cxx20.hpp"`` on the next physical line is really one
    comment covering both lines, and the include inside it is never
    live. Stripping comments first (only within one physical line,
    unaware of the continuation) would instead
    leave that second line looking like a live, unmasked directive.
    """
    seen: set[Path] = set()
    expanded: list[Path] = []
    stack = list(header_paths)
    while stack:
        p = stack.pop(0)
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        if resolved in seen:
            continue
        seen.add(resolved)
        expanded.append(p)
        try:
            content = p.read_bytes()
        except OSError:
            continue
        # Splice backslash-newline continuations first (Codex review):
        # joins a directive spanning one, e.g. ``#include \`` followed by
        # ``"concepts.hpp"`` on the next physical line, back onto a single
        # physical line the pattern below can match -- and, done before
        # comment stripping, correctly extends a ``//`` comment ending in
        # a trailing backslash over its continuation line too.
        content = re.sub(rb"\\\r?\n", b"", content)
        # Raw string literals only -- NOT ordinary string/char literals,
        # unlike the main scan's preprocessing pipeline: an ordinary
        # literal has the exact same lexical shape (a bare double-quote)
        # as a genuine #include's argument, so blanking it here would
        # destroy the include target this loop needs to read next. A raw
        # string's delimiter (``R"delim(...)delim"``) is unambiguous and
        # never collides with ``#include "..."``, so it's safe to strip
        # -- and necessary, so a fake ``#include "x"``-looking line
        # trapped inside a raw string's body is never mistaken for a real
        # directive (Codex review).
        content = _strip_raw_strings(content)
        # Comments next.
        content = re.sub(
            rb"/\*.*?\*/",
            lambda m: b"\n" * m.group(0).count(b"\n"),
            content,
            flags=re.DOTALL,
        )
        content = re.sub(rb"//[^\n]*", b"", content)
        reachable_content = _strip_inactive_if_zero_blocks(
            content, mask_cplusplus_defined_guards=for_language_mode_decision
        )
        for m in _QUOTED_INCLUDE_PATTERN.finditer(reachable_content):
            name = m.group(1).decode("utf-8", "surrogateescape")
            included = p.parent / name
            if included.is_file():
                stack.append(included)
    return expanded
