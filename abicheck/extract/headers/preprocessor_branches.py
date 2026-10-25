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

"""Function declarations a header parse could not see: they sit in a
preprocessor branch the parse's configuration excluded.

A header-AST parse sees one preprocessor configuration. A declaration under
``#ifdef FOO`` is removed before the parser runs when ``FOO`` is not defined,
and the export it declares then reads as "undeclared" with no stated reason
(field report H5, generalized beyond ``sycl::``).

**Which branches were excluded is decided by the compiler, not by this
module.** The castxml backend already re-runs its exact command with
``-E -dD`` (:mod:`.castxml.preprocessed_text`): every source line that
survived preprocessing appears in that output under a line marker naming its
``file:line``. This module only scans each public header's *raw* text for its
``#if``/``#ifdef``/``#ifndef``/``#elif``/``#else``/``#endif`` structure and
asks, per branch, whether any of its lines survived. Evaluating the
conditions here instead would mean re-implementing the preprocessor
(``__has_include``, function-like macros, macros one header defines for a
later one, the compiler's predefined set) and getting some of it wrong.

A branch is **excluded** when it has at least one line that produces output
when taken (code, or a ``#define``/``#undef``, which ``-dD`` keeps in place)
and none of them did.

A branch's **guard macros** are the macros whose *definition* would enable
it: each identifier its own condition requires defined (``#ifdef FOO``,
``#if defined(A) && defined(B)``, ``#if FOO > 2``, ``#elif BAR``). A negated
requirement (``#ifndef X``, ``!defined(X)``), and the earlier arms an
``#elif``/``#else`` needs *false*, name no guard: "pass ``-D X``" would be
the wrong advice for them, so their declarations stay unattributed. A
classic include guard (``#ifndef X`` directly followed by ``#define X``) is
transparent: never excluded, never a guard.

A hidden declaration is attributed to the guard macros of every excluded
branch enclosing it, up to the first enclosing branch that produced output,
minus each macro the same run *defined* (``-dD`` lists them all, predefined
and ``-D`` ones included). That subtraction is what keeps the attribution
exact where the surviving lines alone are ambiguous: under
``#ifdef WRAP`` / ``#ifdef INNER`` with ``INNER`` defined, both arms lose
every line, but only ``WRAP`` is a macro the configuration lacks. A guard
that was defined yet still excluded its arm (``#if FOO > 2`` with
``FOO=1``) names nothing: "pass ``-D FOO``" would not help.

Declared names are found by a lightweight token scan (an identifier followed
by ``(`` on a code line): evidence for attribution, never a declaration of
record.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_DIRECTIVE = re.compile(r"^\s*#\s*(\w+)\s*(.*)$")
_IDENT = re.compile(r"[A-Za-z_]\w*")
_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
#: A condition's tokens: identifiers, numbers (with suffixes), and any other
#: single non-space character (``!``, ``(``, ``)``, operators).
_COND_TOKEN = re.compile(r"[A-Za-z_]\w*|\d\w*|\S")
#: ``__has_include(<a/b.h>)`` and friends: the argument is a path, not macros.
_HAS_CALL = re.compile(r"\b__has_\w+\s*\([^()]*\)")
#: Directives whose own line ``-E -dD`` keeps in place.
_EMITTED_DIRECTIVES = frozenset({"define", "undef"})
#: Identifiers in a condition that are operators or literals, not macros.
_CONDITION_NOISE = frozenset({"defined", "true", "false"})
#: Identifiers followed by ``(`` that never name a declared function.
_NOT_A_DECLARATION = frozenset(
    {
        "if",
        "while",
        "for",
        "switch",
        "return",
        "sizeof",
        "alignof",
        "alignas",
        "decltype",
        "noexcept",
        "static_assert",
        "_Static_assert",
        "__attribute__",
        "__declspec",
        "__typeof__",
        "typeof",
        "operator",
        "throw",
        "catch",
        "defined",
        "requires",
        "explicit",
    }
)


@dataclass
class Branch:
    """One arm of an ``#if`` group."""

    #: Macros whose definition would enable this arm (see the module doc).
    guard_macros: tuple[str, ...]
    parent: int | None
    include_guard: bool = False
    #: Lines that produce output when taken, nested arms' lines included.
    all_lines: list[int] = field(default_factory=list)
    #: Call-shaped identifiers directly in this arm.
    names: set[str] = field(default_factory=set)


def _strip_comments(text: str) -> str:
    """Blank comments, keeping every newline so line numbers hold."""
    return _COMMENT.sub(lambda m: "\n" * m.group(0).count("\n") + " ", text)


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """``(first physical line, joined text)`` with ``\\``-continuations folded."""
    out: list[tuple[int, str]] = []
    pending: list[str] = []
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        if not pending:
            start = number
        if raw.endswith("\\"):
            pending.append(raw[:-1])
            continue
        pending.append(raw)
        out.append((start, " ".join(pending)))
        pending = []
    if pending:
        out.append((start, " ".join(pending)))
    return out


def required_macros(expr: str) -> tuple[str, ...]:
    """The macros an ``#if``/``#elif`` *expr* requires defined (or non-zero).

    An identifier counts unless an odd number of ``!`` applies to it -- one
    directly before it (or before its ``defined``), or before an enclosing
    parenthesis. ``__has_include(...)``-style builtins and their arguments
    name no macro.
    """
    expr = _HAS_CALL.sub(" 1 ", expr)
    out: dict[str, None] = {}  # insertion-ordered set
    #: Negation parity of each open parenthesis.
    stack: list[bool] = [False]
    pending_not = False
    for tok in _COND_TOKEN.findall(expr):
        if tok == "!":
            pending_not = not pending_not
            continue
        if tok == "defined":
            continue  # a `!` before it applies to the operand that follows
        if tok == "(":
            stack.append(stack[-1] ^ pending_not)
        elif tok == ")":
            if len(stack) > 1:
                stack.pop()
        elif _IDENT.fullmatch(tok) and tok not in _CONDITION_NOISE:
            if not (stack[-1] ^ pending_not):
                out.setdefault(tok)
        pending_not = False
    return tuple(out)


def _first_ident(rest: str) -> str:
    found = _IDENT.search(rest)
    return found.group(0) if found else ""


def _arm_guards(kind: str, rest: str) -> tuple[str, ...]:
    """Guard macros of the arm a directive opens (none for a negated one)."""
    if kind in ("if", "elif"):
        return required_macros(rest)
    if kind in ("ifdef", "elifdef") and _first_ident(rest):
        return (_first_ident(rest),)
    return ()  # #ifndef, #elifndef, #else


def scan_branches(text: str) -> list[Branch]:
    """Every conditional arm of one header's raw *text*."""
    branches: list[Branch] = []
    #: Index of the current arm of each open group.
    stack: list[int] = []
    guard_candidate: tuple[int, str] | None = None  # (arm index, macro)

    def record(line_no: int, names: Iterable[str] = ()) -> None:
        if not stack:
            return
        branches[stack[-1]].names.update(names)
        idx: int | None = stack[-1]
        while idx is not None:
            branches[idx].all_lines.append(line_no)
            idx = branches[idx].parent

    for line_no, line in _logical_lines(_strip_comments(text)):
        directive = _DIRECTIVE.match(line)
        if directive is None:
            if line.strip():
                calls = _CALL.findall(line)
                record(line_no, (n for n in calls if n not in _NOT_A_DECLARATION))
            guard_candidate = None
            continue
        kind, rest = directive.group(1), directive.group(2).strip()
        candidate, guard_candidate = guard_candidate, None
        if kind in ("if", "ifdef", "ifndef") or (
            kind in ("elif", "elifdef", "elifndef", "else") and stack
        ):
            if kind not in ("if", "ifdef", "ifndef"):
                stack.pop()
            branches.append(
                Branch(_arm_guards(kind, rest), stack[-1] if stack else None)
            )
            stack.append(len(branches) - 1)
            if kind == "ifndef" and _first_ident(rest):
                guard_candidate = (stack[-1], _first_ident(rest))
        elif kind == "endif" and stack:
            stack.pop()
        elif kind in _EMITTED_DIRECTIVES:
            if (
                kind == "define"
                and stack
                and candidate == (stack[-1], _first_ident(rest))
            ):
                branches[stack[-1]].include_guard = True
            record(line_no)
    return branches


def _excluded(branch: Branch, present: set[int]) -> bool:
    return (
        not branch.include_guard
        and bool(branch.all_lines)
        and present.isdisjoint(branch.all_lines)
    )


def hidden_declarations(
    text: str, present: Iterable[int], defined: Iterable[str] = ()
) -> dict[str, set[str]]:
    """``{guard macro: declared names}`` for one header.

    *present* is the set of the header's line numbers that produced output
    in the parse's own preprocessing run; *defined* every macro that run
    defined.
    """
    present_set, defined_set = set(present), set(defined)
    branches = scan_branches(text)
    out: dict[str, set[str]] = {}
    for b in branches:
        if not b.names or not _excluded(b, present_set):
            continue
        macros: set[str] = set()
        node: Branch | None = b
        while node is not None and not node.include_guard:
            if not _excluded(node, present_set):
                break
            macros.update(node.guard_macros)
            node = branches[node.parent] if node.parent is not None else None
        for macro in macros - defined_set:
            out.setdefault(macro, set()).update(b.names)
    return out


def _path_key(path: str | os.PathLike[str]) -> str:
    """One spelling for a header path and a line marker's spelling of it."""
    return os.path.normcase(os.path.normpath(os.path.realpath(path)))


def hidden_declarations_for_headers(
    headers: Iterable[str | os.PathLike[str]],
    present_lines: Mapping[str, Iterable[int]],
    defined: Iterable[str] = (),
) -> dict[str, tuple[str, ...]] | None:
    """Merge :func:`hidden_declarations` over the public *headers*.

    *present_lines* maps each file the preprocessing run entered (a line
    marker named it, even with no surviving line) to its surviving line
    numbers, and *defined* lists every macro the run defined. A header the
    run never entered -- left out of the parse, or
    spelled differently than this side can match -- is skipped: its branches
    are unknown, never all excluded. ``None`` when a header cannot be read,
    never "nothing hidden".
    """
    entered = {_path_key(name): set(lines) for name, lines in present_lines.items()}
    defined_set = frozenset(defined)
    merged: dict[str, set[str]] = {}
    for header in headers:
        try:
            with open(header, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return None
        present = entered.get(_path_key(header))
        if present is None:
            continue
        for macro, names in hidden_declarations(text, present, defined_set).items():
            merged.setdefault(macro, set()).update(names)
    return {m: tuple(sorted(n)) for m, n in sorted(merged.items())}


__all__ = [
    "Branch",
    "hidden_declarations",
    "hidden_declarations_for_headers",
    "required_macros",
    "scan_branches",
]
