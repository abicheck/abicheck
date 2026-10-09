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

"""Per-file preprocessing for the structural C++20 header scan.

Moved out of ``dumper_ast_config_cpp20`` (which re-exports these names) when
the pass gained a content-digest memo: blanks raw strings, literals (across
line continuations) and comments, then masks inactive ``#if`` regions under
both dialect polarities.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..extract.headers.ast_config_cpp20_chains import _strip_inactive_if_zero_blocks
from .quoted_include_expansion import _strip_raw_strings

# Unrolled ("normal* (special normal*)*") rather than one alternation per
# character: the same language and the same greedy match, but a run of
# ordinary characters is consumed in one step -- 11x faster over MKL's 10 MB
# of headers. tests/test_literal_pattern_unrolling.py holds the original
# spelling as the oracle.
_JOINED_STRING_LITERAL_PATTERN = re.compile(rb'"[^"\\]*(?:\\.[^"\\]*)*"', re.DOTALL)
_JOINED_CHAR_LITERAL_PATTERN = re.compile(rb"'[^'\\]*(?:\\.[^'\\]*)*'", re.DOTALL)


def _strip_literals_crossing_continuations(content: bytes) -> bytes:
    """Like :func:`_strip_literals`, but — unlike it — a literal that spans a
    backslash-newline continuation is still fully blanked, not left behind
    because the plain patterns refuse to cross the embedded newline.

    Safe to call directly on whole-file *content* (unlike
    :func:`_strip_literals_joined`, which additionally tolerates crossing an
    *unrelated* later line and so is only safe on a single already-joined
    logical line): ``\\.`` under ``re.DOTALL`` already consumes a
    continuation's ``\\<newline>`` pair as one escaped character, so the
    match still ends at the literal's real closing quote rather than
    wandering into unrelated later lines. Each replacement preserves the
    literal's embedded newline count (mirrors :func:`_strip_raw_strings`) so
    line numbers reported for code that follows a continued literal stay
    accurate — the plain ``_strip_literals_joined`` replacement (a bare
    ``""``/``''``) would otherwise silently swallow those newlines (Codex
    review: a shadow-name scan run before comment-stripping needs a
    continuation-spanning literal fully blanked, or a fake type name like
    ``struct concept {};`` trapped inside one leaks through and wrongly
    shadows a genuine C++20 declaration elsewhere in the header).
    """
    content = _JOINED_STRING_LITERAL_PATTERN.sub(
        lambda m: b'""' + b"\n" * m.group(0).count(b"\n"), content
    )
    content = _JOINED_CHAR_LITERAL_PATTERN.sub(
        lambda m: b"''" + b"\n" * m.group(0).count(b"\n"), content
    )
    return content


def _preprocessed_header_content(
    path: Path, *, for_language_mode_decision: bool
) -> tuple[bytes, bytes] | None:
    """``(scan_content, shadow_scan_content)`` for one header, or ``None``.

    Raw string literals are blanked first — their body can contain arbitrary
    quotes/backslashes that would otherwise confuse the ordinary string-literal
    stripper. Then string/char literals, so a literal containing comment-like
    text (``"/* not a comment */"``) is never mistaken for a real comment;
    that pass is backslash-newline-continuation-tolerant (Codex review) so a
    literal split across a continuation cannot leave its trapped text — e.g. a
    fake ``struct concept {};`` inside an error message — visible to the shadow
    scan. Block comments are replaced by their own newline count so
    later-reported line numbers stay accurate (CodeRabbit review).

    The two returned copies differ only in how dialect-fallback guards are
    masked, and that difference is load-bearing (Codex review, nineteenth
    round). The shadow scan asks "does ``concept`` name an ordinary type in
    code still reachable *if C++20 were chosen*", so a ``struct concept {};``
    shim confined to ``#if __cplusplus < 202002L`` — content that goes away
    once C++20 is chosen — must not count; for the requirements scan that same
    guarded arm is the unconditionally-relevant one.
    """
    try:
        content = path.read_bytes()
    except OSError:
        return None
    return _prepare_content(content, for_language_mode_decision)


def _prepare_content(
    content: bytes, for_language_mode_decision: bool
) -> tuple[bytes, bytes]:
    content = _strip_raw_strings(content)
    content = _strip_literals_crossing_continuations(content)
    content = re.sub(
        rb"/\*.*?\*/",
        lambda m: b"\n" * m.group(0).count(b"\n"),
        content,
        flags=re.DOTALL,
    )
    # "//" line comments are removed once, up front, before *both* masking
    # passes below (which then differ only in guard-masking polarity). Raw
    # strings/literals/block comments are already blanked above, but "//"
    # comments are otherwise only stripped per-logical-line further down, and a
    # "// struct concept {};" comment must never make a *real* concept
    # declaration elsewhere look ambiguous (Codex review, fifth round).
    # #if 0 / #if false regions go too — a disabled compatibility stub must not
    # shadow a genuine keyword used elsewhere (Codex review).
    no_double_slash = re.sub(rb"//[^\n]*", b"", content)
    scan_content = _strip_inactive_if_zero_blocks(
        no_double_slash, mask_cplusplus_defined_guards=for_language_mode_decision
    )
    shadow_content = _strip_inactive_if_zero_blocks(
        no_double_slash, invert_dialect_fallback_guards=False
    )
    return scan_content, shadow_content
