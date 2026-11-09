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
A type-constraint (``template <std::integral T>``, ``std::integral auto``)
counts too; so does a non-type parameter of a user type, which the text
cannot tell apart from one. A macro that expands to ``concept``/``requires`` is not seen, and a header
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


#: A constrained placeholder: ``std::integral auto``, ``C<int> auto``. The
#: specifiers that can stand before a plain ``auto`` do not count.
_CONSTRAINED_AUTO = re.compile(
    r"(?<![\w:])((?:::)?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)(?:<[^;{}()]*?>)?\s+auto\b"
)
_AUTO_SPECIFIERS = frozenset(
    {
        "const",
        "volatile",
        "static",
        "inline",
        "constexpr",
        "consteval",
        "constinit",
        "extern",
        "virtual",
        "friend",
        "return",
        "typedef",
        "mutable",
        "thread_local",
        "explicit",
        "register",
        "else",
        "do",
        "case",
        "co_return",
        "co_yield",
        "throw",
        "typename",
        "using",
    }
)
#: ``template <`` -- the start of a template parameter list.
_TEMPLATE_HEAD = re.compile(r"\btemplate\s*<")
#: A parameter that is a type or a non-type parameter of a builtin type.
_PLAIN_PARAM_HEAD = frozenset(
    {
        "typename",
        "class",
        "template",
        "auto",
        "bool",
        "char",
        "char8_t",
        "char16_t",
        "char32_t",
        "wchar_t",
        "short",
        "int",
        "long",
        "signed",
        "unsigned",
        "float",
        "double",
        "const",
        "volatile",
        "decltype",
        "size_t",
        "std::size_t",
        "ptrdiff_t",
        "std::ptrdiff_t",
        "nullptr_t",
        "std::nullptr_t",
    }
)
_PARAM_HEAD = re.compile(
    r"\s*((?:::)?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\b\s*(?:<[^,]*>)?\s*(?:\.\.\.)?\s*[A-Za-z_]"
)


def _template_params(code: str, start: int) -> list[str]:
    """The top-level parameters of the template list opening at *start*."""
    depth = 1
    params: list[str] = []
    current: list[str] = []
    for ch in code[start:]:
        if ch == "<" or ch == "(":
            depth += 1
        elif ch in ">)":
            depth -= 1
            if depth == 0:
                break
        elif ch == "," and depth == 1:
            params.append("".join(current))
            current = []
            continue
        elif ch in ";{":
            return []
        current.append(ch)
    params.append("".join(current))
    return params


def _spells_type_constraint(code: str) -> bool:
    """Whether *code* constrains a template parameter (``template <C T>``) or
    an ``auto`` placeholder (``C auto``). A parameter whose head is not
    ``typename``/``class`` or a builtin type counts -- a non-type parameter
    of a user type (``template <Color C>``) does too: the scan errs toward
    naming the dimension, never toward a clean pass."""
    for m in _CONSTRAINED_AUTO.finditer(code):
        if m.group(1) not in _AUTO_SPECIFIERS:
            return True
    for m in _TEMPLATE_HEAD.finditer(code):
        for param in _template_params(code, m.end()):
            head = _PARAM_HEAD.match(param)
            if head is not None and head.group(1) not in _PLAIN_PARAM_HEAD:
                return True
    return False


def header_declares_concept_or_requires(text: str) -> bool:
    """Whether the header *text* defines a concept, spells a ``requires``
    clause or expression, or constrains a template parameter or ``auto``
    placeholder with a concept, outside comments, string literals, ``#if 0``
    blocks and preprocessor directives."""
    code = _DIRECTIVE.sub(" ", _drop_if0_blocks(_strip_lexemes(text)))
    return bool(
        _CONCEPT_DEFINITION.search(code)
        or _REQUIRES.search(code)
        or _spells_type_constraint(code)
    )


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
