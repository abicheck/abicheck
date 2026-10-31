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

"""Find parameter expansions that bash word-splits AND pathname-expands.

A deliberately small bash lexer, not a parser: it tracks quoting (single,
double, ANSI-C), command substitution, here-documents, comments, and the
word positions where bash performs no field splitting or globbing on an
unquoted expansion -- the right-hand side of an assignment word (including
the operands of ``local``/``export``/``declare``/``readonly``/``typeset``),
the inside of ``[[ ... ]]`` and ``(( ... ))``, the subject of ``case``, and
a here-string -- and where a preceding ``set -f`` (up to its ``set +f``)
has turned pathname expansion off. Everywhere else an unquoted ``$NAME``/``${NAME...}`` is
split on IFS and each field globbed against the working directory, which is
the bug class `tests/test_action_extra_args_noglob.py` guards.

Not a ``test_`` module: shared machinery, per this directory's convention.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")
_DECLARATION_BUILTINS = frozenset({"local", "export", "declare", "readonly", "typeset"})
_COMMAND_RESET_WORDS = frozenset(
    {"then", "do", "else", "elif", "if", "while", "until", "!", "{", "time"}
)
_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


@dataclass(frozen=True)
class UnquotedExpansion:
    """One expansion of a selected name that bash would split and glob."""

    line: int
    name: str
    text: str


class _Lexer:
    def __init__(self, script: str, selected: Callable[[str], bool]) -> None:
        self.s = script
        self.selected = selected
        self.hits: list[UnquotedExpansion] = []
        self.pending_heredocs: list[tuple[str, bool]] = []
        # `set -f` turns pathname expansion off until a later `set +f`;
        # tracked in text order, which is how the scripts scope it.
        self.noglob = False

    def line_of(self, pos: int) -> int:
        return self.s.count("\n", 0, pos) + 1

    def report(self, pos: int, end: int, name: str) -> None:
        if self.selected(name) and not self.noglob:
            self.hits.append(
                UnquotedExpansion(self.line_of(pos), name, self.s[pos:end])
            )

    # -- expansions ---------------------------------------------------------

    def expansion(self, i: int, *, splitting: bool) -> int:
        """Lex the ``$...`` starting at *i*; return the index after it."""
        s = self.s
        nxt = s[i + 1 : i + 2]
        if s.startswith("$((", i):
            return self.arith(i + 3)
        if nxt == "(":
            return self.code(i + 2, closer=")")
        if nxt == "'":
            return self.ansi_c(i + 2)
        if nxt == "{":
            return self.braced(i, splitting=splitting)
        m = _NAME.match(s, i + 1)
        if m:
            if splitting:
                self.report(i, m.end(), m.group())
            return m.end()
        return i + 1

    def braced(self, i: int, *, splitting: bool) -> int:
        """Lex the ``${...}`` at *i*.

        The parameter itself is reported unless the form yields no part of
        its value: ``${#NAME}`` (a length) and ``${NAME+word}``/
        ``${NAME:+word}`` (the alternate *word*, whose own unquoted
        expansions are lexed -- and reported -- as they occur in it).
        """
        s = self.s
        m = re.compile(r"(#?)(!?)([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?").match(s, i + 2)
        if not m:
            return self.brace_body(i + 2, splitting=splitting)
        length, _indirect, name = m.group(1), m.group(2), m.group(3)
        alternate = s.startswith(":+", m.end()) or s.startswith("+", m.end())
        end = self.brace_body(m.end(), splitting=splitting)
        if splitting and not length and not alternate:
            self.report(i, end, name)
        return end

    def brace_body(self, i: int, *, splitting: bool = False) -> int:
        """Lex a ``${...}`` body up to its ``}``; nested unquoted
        expansions are split when the enclosing expansion is."""
        s, depth = self.s, 1
        while i < len(s):
            c = s[i]
            if c == "\\":
                i += 2
                continue
            if c == "'":
                i = self.after_single_quote(i)
                continue
            if c == '"':
                i = self.dquote(i + 1)
                continue
            if c == "$":
                i = self.expansion(i, splitting=splitting)
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return i

    def after_single_quote(self, i: int) -> int:
        """Index after the ``'`` closing the single-quoted string at *i*."""
        end = self.s.find("'", i + 1)
        return len(self.s) if end < 0 else end + 1

    def ansi_c(self, i: int) -> int:
        s = self.s
        while i < len(s) and s[i] != "'":
            i += 2 if s[i] == "\\" else 1
        return i + 1

    def arith(self, i: int) -> int:
        """Skip ``(( ... ))`` arithmetic: no splitting inside."""
        s, depth = self.s, 2
        while i < len(s) and depth:
            c = s[i]
            if c == "$":
                i = self.expansion(i, splitting=False)
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            i += 1
        return i

    def dquote(self, i: int) -> int:
        """Lex a double-quoted string body; return the index after the ``"``."""
        s = self.s
        while i < len(s):
            c = s[i]
            if c == "\\":
                i += 2
                continue
            if c == '"':
                return i + 1
            if c == "$":
                i = self.expansion(i, splitting=False)
                continue
            if c == "`":
                i = self.code(i + 1, closer="`")
                continue
            i += 1
        return i

    # -- command context ----------------------------------------------------

    def code(self, i: int, *, closer: str | None) -> int:
        """Lex shell code until *closer* (``)``/backtick) or end of text."""
        s = self.s
        command_start = True
        declaration = False
        no_split_word = False  # current word is an assignment/case subject/herestring
        next_no_split = False
        dbracket = 0
        paren = 0
        case_depth = 0
        word_start = True
        word = ""
        while i < len(s):
            c = s[i]
            if word_start and c in " \t":
                i += 1
                continue
            if word_start:
                # Classify the word that starts here.
                m = re.match(r"[^\s;&|()<>]*", s[i:])
                raw = m.group() if m else ""
                if raw == "[[":
                    dbracket += 1
                elif raw == "]]" and dbracket:
                    dbracket -= 1
                elif raw == "case":
                    case_depth += 1
                elif raw == "esac" and case_depth:
                    case_depth -= 1
                if s.startswith("((", i) and command_start:
                    i = self.arith(i + 2)
                    continue
                no_split_word = next_no_split or bool(
                    _ASSIGNMENT.match(raw) and (command_start or declaration)
                )
                next_no_split = raw in {"case", "<<<"}
                if raw == "set" and command_start:
                    flag = re.match(r"set[ \t]+([-+])f\b", s[i:])
                    if flag:
                        self.noglob = flag.group(1) == "-"
                if raw in _DECLARATION_BUILTINS and command_start:
                    declaration = True
                command_start = raw in _COMMAND_RESET_WORDS or (
                    command_start and bool(_ASSIGNMENT.match(raw)) and not declaration
                )
                word = raw
                word_start = False
                if no_split_word and _ASSIGNMENT.match(raw):
                    eq = s.index("=", i)
                    if s.startswith("(", eq + 1):
                        # Array assignment: its elements ARE split and globbed.
                        no_split_word = False
                        i = eq + 1
                        continue
            splitting = not (no_split_word or dbracket)
            if c == "\\":
                i += 2
                continue
            if c == "#" and not dbracket and (i == 0 or s[i - 1] in " \t\n;(|&"):
                i = s.find("\n", i)
                if i < 0:
                    return len(s)
                continue
            if c == "'":
                i = self.after_single_quote(i)
                continue
            if c == '"':
                i = self.dquote(i + 1)
                continue
            if c == "`":
                if closer == "`":
                    return i + 1
                i = self.code(i + 1, closer="`")
                continue
            if c == "$":
                i = self.expansion(i, splitting=splitting)
                continue
            if c == "<" and s.startswith("<<", i) and not s.startswith("<<<", i):
                m = _HEREDOC.match(s, i)
                if m:
                    self.pending_heredocs.append((m.group(3), bool(m.group(1))))
                    i = m.end()
                    continue
            if c == "<" and s.startswith("<<<", i):
                next_no_split = True
                i += 3
                word_start = True
                continue
            if dbracket and c in "()":
                pass  # a `[[ =~ ]]` regex group, not a subshell
            elif c == "(":
                paren += 1
            elif c == ")":
                if closer == ")" and paren == 0 and case_depth == 0:
                    return i + 1
                if paren:
                    paren -= 1
            if c == "\n":
                i = self.skip_heredocs(i + 1)
                command_start, declaration, next_no_split = True, False, False
                word_start = True
                continue
            if c in " \t":
                word_start = True
            elif c in ";&|(":
                word_start = True
                command_start, declaration = True, False
            i += 1
        del word
        return i

    def skip_heredocs(self, i: int) -> int:
        """Skip the bodies of here-documents opened on the line just ended."""
        s = self.s
        while self.pending_heredocs:
            delim, strip = self.pending_heredocs.pop(0)
            while i < len(s):
                end = s.find("\n", i)
                end = len(s) if end < 0 else end
                line = s[i:end]
                i = end + 1
                if (line.lstrip("\t") if strip else line) == delim:
                    break
        return i


def unquoted_expansions(
    script: str, selected: Callable[[str], bool]
) -> list[UnquotedExpansion]:
    """Every expansion of a *selected* variable bash would split and glob."""
    lexer = _Lexer(script, selected)
    lexer.code(0, closer=None)
    return lexer.hits
