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

"""Header-text scan for C++20 concepts and requires-clauses.

Neither header backend models a concept definition or the constraint a
``requires`` clause places on a template, so a tightened concept compares
``NO_CHANGE`` from headers. This scan answers the narrower question both
backends can be checked against: does a public header spell a ``concept``
definition or a ``requires`` clause/expression at all? When one does, the
snapshot records that the constraints were not observed, and the assurance
rollup reads ``partial`` instead of ``complete``.

It is a text heuristic over the headers the parse was given. Comments and
string/character literals are removed in one lexical pass, so a ``"/*"``
inside a string does not open a comment, and a ``#if 0`` ... ``#endif``
block (the one provably inactive branch a text scan can name) is skipped.
A macro that expands to ``concept``/``requires`` is not seen, and a header
included indirectly is not read; both make the scan say ``False`` (no gap
reported), never a clean pass. A header that cannot be read makes the scan
``True``: it cannot rule a concept out, so the dimension is named. A ``#define`` line is
skipped, so a macro *named* or *defined with* the keyword does not count on
its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

__all__ = ["concept_header_scan", "header_declares_concept_or_requires"]

#: ``template <...> concept Name =`` -- a concept definition. A bare
#: ``concept`` identifier (a pre-C++20 variable or parameter) does not match.
_CONCEPT_DEFINITION = re.compile(r"\bconcept\s+[A-Za-z_]\w*\s*=")
#: ``requires`` followed by what can start a clause or expression: ``(``,
#: ``{``, a name, ``!`` or ``::``. A lone ``requires`` used as an identifier
#: (``int requires;``) is followed by ``;``, ``,``, ``)`` or ``=``.
_REQUIRES = re.compile(r"\brequires\s*(?:[({!]|::|[A-Za-z_])")
_DIRECTIVE = re.compile(r"^[ \t]*#(?:[^\n]*\\\n)*[^\n]*", re.MULTILINE)


#: One lexical pass: whichever of a comment, a string literal or a character
#: literal starts first wins, so delimiters inside the others are inert.
_LEXEME = re.compile(
    r"//[^\n]*|/\*.*?(?:\*/|\Z)|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'",
    re.DOTALL,
)
_CONDITIONAL = re.compile(r"^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|else|endif)\b([^\n]*)")


def _strip_lexemes(text: str) -> str:
    """*text* with comments blanked and string/character literals emptied,
    in a single left-to-right pass."""

    def repl(m: re.Match[str]) -> str:
        tok = m.group(0)
        if tok.startswith("/"):
            # Keep line breaks so directive lines stay on their own lines.
            return " " + "\n" * tok.count("\n")
        return tok[0] * 2

    return _LEXEME.sub(repl, text)


def _drop_if0_blocks(code: str) -> str:
    """*code* without the lines of ``#if 0`` groups (up to their matching
    ``#else``/``#elif``/``#endif``); nested conditionals are tracked."""
    out: list[str] = []
    depth = 0  # nesting depth inside a skipped group, 0 when not skipping
    for line in code.split("\n"):
        m = _CONDITIONAL.match(line)
        if depth:
            if m:
                kw = m.group(1)
                if kw in ("if", "ifdef", "ifndef"):
                    depth += 1
                elif kw == "endif":
                    depth -= 1
                elif depth == 1:  # else/elif of the #if 0 group itself
                    depth = 0
            continue
        if m and m.group(1) == "if" and m.group(2).strip() == "0":
            depth = 1
            continue
        out.append(line)
    return "\n".join(out)


def header_declares_concept_or_requires(text: str) -> bool:
    """Whether the header *text* defines a concept or spells a ``requires``
    clause or expression outside comments, string literals, ``#if 0``
    blocks and preprocessor directives."""
    code = _DIRECTIVE.sub(" ", _drop_if0_blocks(_strip_lexemes(text)))
    return bool(_CONCEPT_DEFINITION.search(code) or _REQUIRES.search(code))


def concept_header_scan(paths: Iterable[str | Path]) -> bool:
    """Whether any header in *paths* defines a concept or spells a
    ``requires`` clause -- or could not be read to rule one out. An
    unreadable header answers ``True``: the constraint dimension is then
    named as unobserved, so a failed read lowers assurance instead of
    reading as a clean ``False``."""
    unreadable = False
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            unreadable = True
            continue
        if header_declares_concept_or_requires(text):
            return True
    return unreadable
