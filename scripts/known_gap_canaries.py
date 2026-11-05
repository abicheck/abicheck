#!/usr/bin/env python3
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

"""known_gap_canaries.py — the `known-gap-canaries` AI-readiness gate.

`docs/contribute/known-gaps.md` is prose, so a gap could close, or widen,
with nothing failing. This gate links every open entry to an executable
canary, or to a reasoned exemption:

* Every status line on the main page is one entry. An entry whose status is
  OPEN, PARTIAL or NARROWED carries, inside its block (from its status line
  to the next status line or heading), either one or more
  ``<!-- canary: tests/test_x.py::test_name -->`` markers or exactly one
  ``<!-- no-canary: <reason> -->`` marker, each on its own line (a marker
  quoted inside prose is documentation, not a link). A reason must say why a canary is
  not cheap (for example "needs MSVC", "needs the oneDAL tree"), not "TBD".
* A CLOSED, FIXED or MOOT entry may not stay on the main page: move it to
  `archive/known-gaps-closed.md`. Any status keyword outside
  `ALLOWED_STATUSES` is an error, so a new spelling cannot slip past.
* A named canary must exist: a `test_*.py` file under `tests/`, a test
  function (optionally inside a class) of that name, carrying
  ``@pytest.mark.known_gap_canary`` on itself, its class or the module's
  `pytestmark`. It may not be skipped unconditionally, and an xfail on it
  must be `strict=True`, so the canary fails when the gap closes.
* Every test carrying the marker must be named by a main-page entry, so a
  canary cannot outlive the entry it guards: when a gap is fixed, its canary
  fails, the entry moves to the archive, and the canary becomes an ordinary
  regression test (drop the marker) or is deleted.

The static checks run here (pure stdlib, like the parent script);
`tests/test_known_gap_canaries_gate.py` also asks pytest to collect every
named node id.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
KNOWN_GAPS = ROOT / "docs" / "contribute" / "known-gaps.md"
TESTS = ROOT / "tests"
CHECK = "known-gap-canaries"
MARKER = "known_gap_canary"

#: Statuses that need a canary or a reasoned `no-canary:` note.
NEEDS_CANARY: frozenset[str] = frozenset({"OPEN", "PARTIAL", "NARROWED"})
#: Statuses that belong in the archive, never on the main page.
CLOSED_STATUSES: frozenset[str] = frozenset({"CLOSED", "FIXED", "MOOT", "RESOLVED"})
#: Every status the main page may carry.
ALLOWED_STATUSES: frozenset[str] = NEEDS_CANARY | {"NEGATIVE_RESULT", "POLICY_NOTE"}

#: A reason shorter than this is a placeholder, not a reason.
MIN_REASON_CHARS = 20
_PLACEHOLDER_REASONS = frozenset({"tbd", "todo", "n/a", "none", "later", "skip"})

_STATUS_RE = re.compile(r"^\s*(?:- )?\*\*Status\b[^*:]*:\s*\**\s*([A-Za-z_]+)")
_HEADING_RE = re.compile(r"^#{1,6}\s")
_CANARY_RE = re.compile(r"<!--\s*canary:\s*(.*?)\s*-->")
_NO_CANARY_RE = re.compile(r"<!--\s*no-canary:\s*(.*?)\s*-->", re.DOTALL)
_NODE_RE = re.compile(
    r"^(tests/[\w/.-]*test_[\w-]*\.py)::([A-Za-z_]\w*)(?:::([A-Za-z_]\w*))?$"
)


@dataclass
class Entry:
    """One status-lined entry of the main page."""

    line: int
    title: str
    status: str
    canaries: list[str] = field(default_factory=list)
    no_canary: list[str] = field(default_factory=list)


def _title_for(lines: list[str], index: int) -> str:
    """The nearest heading, or the list item's link text, above *index*."""
    for i in range(index, -1, -1):
        text = lines[i]
        if _HEADING_RE.match(text):
            return text.lstrip("#").strip()
        link = re.match(r"^- \[(.+?)\]\(", text)
        if link:
            return link.group(1)
    return "(no heading)"


def parse_entries(text: str) -> tuple[list[Entry], list[int]]:
    """Return the page's entries and the line numbers of stray markers.

    A stray marker is one above the first entry: it guards nothing.
    """
    lines = text.splitlines()
    entries: list[Entry] = []
    stray: list[int] = []
    current: Entry | None = None
    # A `no-canary:` comment may span lines; gather the whole comment.
    joined_from: dict[int, str] = {}
    i = 0
    while i < len(lines):
        if "<!--" in lines[i] and "-->" not in lines[i][lines[i].index("<!--") :]:
            start = i
            buf = [lines[i]]
            while i + 1 < len(lines) and "-->" not in lines[i]:
                i += 1
                buf.append(lines[i])
            joined_from[start] = " ".join(s.strip() for s in buf)
        i += 1
    for idx, raw in enumerate(lines):
        status = _STATUS_RE.match(raw)
        if status:
            current = Entry(idx + 1, _title_for(lines, idx), status.group(1).upper())
            entries.append(current)
        elif _HEADING_RE.match(raw):
            current = None
        text_line = joined_from.get(idx, raw)
        if not text_line.lstrip().startswith("<!--"):
            continue  # a marker stands on its own line; one quoted in prose is not one
        found = [("c", m) for m in _CANARY_RE.findall(text_line)]
        found += [("n", m) for m in _NO_CANARY_RE.findall(text_line)]
        if not found:
            continue
        if current is None:
            stray.append(idx + 1)
            continue
        for kind, value in found:
            (current.canaries if kind == "c" else current.no_canary).append(value)
    return entries, stray


# --------------------------------------------------------------------------
# Test-side resolution
# --------------------------------------------------------------------------


def _is_marker(node: ast.expr) -> bool:
    """Whether *node* spells `pytest.mark.known_gap_canary` (called or not)."""
    if isinstance(node, ast.Call):
        node = node.func
    return isinstance(node, ast.Attribute) and node.attr == MARKER


def _marker_call(decorators: list[ast.expr], name: str) -> list[ast.expr]:
    out = []
    for dec in decorators:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == name:
            out.append(dec)
    return out


def _module_marked(tree: ast.Module) -> bool:
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets
        ):
            values = (
                stmt.value.elts
                if isinstance(stmt.value, (ast.List, ast.Tuple))
                else [stmt.value]
            )
            if any(_is_marker(v) for v in values):
                return True
    return False


def _strictness_problem(decorators: list[ast.expr]) -> str | None:
    if _marker_call(decorators, "skip"):
        return "is unconditionally skipped, so it cannot fail when the gap closes"
    for dec in _marker_call(decorators, "xfail"):
        strict = isinstance(dec, ast.Call) and any(
            kw.arg == "strict"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in dec.keywords
        )
        if not strict:
            return "has a non-strict xfail, so an unexpected pass stays green"
    return None


@dataclass(frozen=True)
class MarkedTest:
    node_id: str
    marked: bool
    problem: str | None


def _parse(path: Path, cache: dict[Path, ast.Module | None]) -> ast.Module | None:
    if path not in cache:
        try:
            cache[path] = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            cache[path] = None
    return cache[path]


def resolve_canary(
    node_id: str, root: Path, cache: dict[Path, ast.Module | None] | None = None
) -> tuple[MarkedTest | None, str | None]:
    """Resolve *node_id* to its test; return (test, error)."""
    cache = {} if cache is None else cache
    match = _NODE_RE.match(node_id)
    if not match:
        return None, ("is not a `tests/.../test_*.py::[Class::]test_name` node id")
    rel, first, second = match.groups()
    path = root / rel
    if not path.is_file():
        return None, f"names a file that does not exist ({rel})"
    try:
        path.resolve().relative_to((root / "tests").resolve())
    except ValueError:
        return None, f"names a file outside tests/ ({rel})"
    tree = _parse(path, cache)
    if tree is None:
        return None, f"names a file that does not parse ({rel})"
    module_marked = _module_marked(tree)
    scope: list[ast.stmt] = tree.body
    class_decorators: list[ast.expr] = []
    func_name = first
    if second is not None:
        cls = next(
            (s for s in tree.body if isinstance(s, ast.ClassDef) and s.name == first),
            None,
        )
        if cls is None:
            return None, f"names class {first} that {rel} does not define"
        scope, class_decorators, func_name = cls.body, cls.decorator_list, second
    func = next(
        (
            s
            for s in scope
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
            and s.name == func_name
        ),
        None,
    )
    if func is None or not func_name.startswith("test"):
        return None, f"names test {func_name} that {rel} does not define"
    decorators = [*class_decorators, *func.decorator_list]
    marked = module_marked or any(_is_marker(d) for d in decorators)
    return MarkedTest(node_id, marked, _strictness_problem(decorators)), None


def marked_tests(root: Path) -> set[str]:
    """Node ids of every test in `tests/` carrying the canary marker."""
    out: set[str] = set()
    for path in sorted((root / "tests").rglob("test_*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if MARKER not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        rel = path.relative_to(root).as_posix()
        module_marked = _module_marked(tree)

        def visit(body: list[ast.stmt], prefix: str, inherited: bool) -> None:
            for stmt in body:
                if isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test"):
                    visit(
                        stmt.body,
                        f"{prefix}{stmt.name}::",
                        inherited or any(_is_marker(d) for d in stmt.decorator_list),
                    )
                elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    stmt.name.startswith("test")
                ):
                    if inherited or any(_is_marker(d) for d in stmt.decorator_list):
                        out.add(f"{rel}::{prefix}{stmt.name}")

        visit(tree.body, "", module_marked)
    return out


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def find_problems(text: str, root: Path) -> list[str]:
    """Every gate violation for a main page *text* resolved against *root*."""
    entries, stray = parse_entries(text)
    problems = [
        f"known-gaps.md:{n}: canary marker outside any status-lined entry"
        for n in stray
    ]
    cache: dict[Path, ast.Module | None] = {}
    named: set[str] = set()
    for e in entries:
        where = f"known-gaps.md:{e.line} ({e.title[:70]})"
        if e.status in CLOSED_STATUSES:
            problems.append(
                f"{where}: status {e.status} on the main page; move the entry to "
                "archive/known-gaps-closed.md"
            )
            continue
        if e.status not in ALLOWED_STATUSES:
            problems.append(
                f"{where}: unknown status {e.status!r}; use one of {sorted(ALLOWED_STATUSES)}"
            )
            continue
        if e.status not in NEEDS_CANARY:
            continue
        if e.canaries and e.no_canary:
            problems.append(f"{where}: carries both a canary and a no-canary note")
        if len(e.no_canary) > 1:
            problems.append(f"{where}: carries more than one no-canary note")
        if not e.canaries and not e.no_canary:
            problems.append(
                f"{where}: {e.status} entry has neither a `<!-- canary: tests/...::test_x -->` "
                "nor a reasoned `<!-- no-canary: ... -->` marker"
            )
        for reason in e.no_canary:
            if (
                len(reason) < MIN_REASON_CHARS
                or reason.strip().lower() in _PLACEHOLDER_REASONS
            ):
                problems.append(
                    f"{where}: no-canary reason {reason!r} is a placeholder; say why "
                    "a canary is not cheap"
                )
        for node_id in e.canaries:
            test, error = resolve_canary(node_id, root, cache)
            if error is not None:
                problems.append(f"{where}: canary {node_id} {error}")
                continue
            assert test is not None
            named.add(node_id)
            if not test.marked:
                problems.append(
                    f"{where}: canary {node_id} lacks @pytest.mark.{MARKER}"
                )
            if test.problem:
                problems.append(f"{where}: canary {node_id} {test.problem}")
    for orphan in sorted(marked_tests(root) - named):
        problems.append(
            f"{orphan}: carries @pytest.mark.{MARKER} but no known-gaps.md entry names "
            "it; link it from its entry, or drop the marker if the gap was closed"
        )
    return problems


def coverage(text: str) -> dict[str, int]:
    """Counts for the PR/CI summary: entries with a canary, exempted, other."""
    entries, _ = parse_entries(text)
    need = [e for e in entries if e.status in NEEDS_CANARY]
    return {
        "needs_link": len(need),
        "with_canary": sum(1 for e in need if e.canaries),
        "exempted": sum(1 for e in need if e.no_canary and not e.canaries),
    }


def check_known_gap_canaries(f: Any) -> None:
    """AI-readiness entry point (`f` is the parent's `Findings`)."""
    if not KNOWN_GAPS.is_file():
        f.err(CHECK, f"{KNOWN_GAPS} is missing")
        return
    text = KNOWN_GAPS.read_text(encoding="utf-8")
    for problem in find_problems(text, ROOT):
        f.err(CHECK, problem)
    c = coverage(text)
    f.info(
        CHECK,
        f"{c['with_canary']} of {c['needs_link']} open entries have a canary, "
        f"{c['exempted']} carry a reasoned no-canary note",
    )


if __name__ == "__main__":  # pragma: no cover - manual use
    import json
    import sys

    page = KNOWN_GAPS.read_text(encoding="utf-8")
    found = find_problems(page, ROOT)
    print("\n".join(found))
    print(json.dumps(coverage(page)))
    sys.exit(1 if found else 0)
