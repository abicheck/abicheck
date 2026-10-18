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

"""Read test sources at a git revision and label their changed lines.

The parsing and git-reading layer of ``test_refactor_churn.py``, split out
because the report script crossed the file-size cap. Nothing here knows about
commits or edits: a revision's file becomes a ``_Source`` (test spans, assertion
and neutral lines, body digests), and ``_Blobs`` reads ``rev:path`` through one
long-lived ``git cat-file --batch``.
"""

from __future__ import annotations

import ast
import dataclasses
import functools
import hashlib
import os
import pathlib
import subprocess
import textwrap
from collections.abc import Iterator

RAISES_NAMES = frozenset(
    {"raises", "warns", "assertRaises", "assertWarns", "assertRaisesRegex"}
)
EXPECTED_PREFIXES = ("expected", "want")


class ChurnError(RuntimeError):
    """A git call failed, so the report cannot be produced."""


#: Variables that point git at a repository. Inherited from a caller, or set by a
#: git hook, they override ``-C`` and would send the report to another repository.
_REPOSITORY_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_QUARANTINE_PATH",
)


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in _REPOSITORY_ENV:
        env.pop(name, None)
    return env


def _run(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=_git_env(),
    )


def _git(repo: pathlib.Path, *args: str) -> str:
    proc = _run(repo, *args)
    if proc.returncode != 0:
        raise ChurnError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


# ---------------------------------------------------------------------------
# One file at one revision
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Source:
    """One file at one revision, reduced to what attribution needs."""

    #: qualified name (``Class::test_x``) -> (first, last) line of the span,
    #: decorators included.
    tests: dict[str, tuple[int, int]]
    assertion_lines: frozenset[int]
    neutral_lines: frozenset[int]
    #: qualified name -> digest of the test's dedented source, to recognise a move.
    bodies: dict[str, str] = dataclasses.field(default_factory=dict)
    #: The file does not parse at this revision; it attributes nothing.
    broken: bool = False

    def label(self, line: int) -> str | None:
        """``assertion``, ``setup``, or None for a line that is not an edit."""
        if line in self.assertion_lines:
            return "assertion"
        if line in self.neutral_lines:
            return None
        return "setup"

    def test_at(self, line: int) -> str | None:
        """The innermost test whose span holds ``line``."""
        containing = [
            n for n, (first, last) in self.tests.items() if first <= line <= last
        ]
        return max(containing, key=lambda n: self.tests[n][0], default=None)


def _span(node: ast.AST) -> range:
    first = getattr(node, "lineno")
    return range(first, (getattr(node, "end_lineno", None) or first) + 1)


def _callee(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _assigns_expected(node: ast.Assign | ast.AnnAssign) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(
        isinstance(t, ast.Name) and t.id.lower().startswith(EXPECTED_PREFIXES)
        for t in targets
    )


def _test_spans(tree: ast.Module) -> dict[str, tuple[int, int]]:
    """Every ``test*`` function, keyed by ``Class::name``. Not descended into."""
    spans: dict[str, tuple[int, int]] = {}

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}::")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test"):
                    first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                    spans[prefix + node.name] = (first, node.end_lineno or node.lineno)
            else:
                # Compound statements (if/try/with/match) can hold tests too.
                for field in _BLOCK_FIELDS:
                    visit(getattr(node, field, None) or [], prefix)

    visit(tree.body, "")
    return spans


#: Fields that hold nested statements. Every assertion, import and assignment
#: this module labels is a statement, so walking only these skips the expression
#: tree, which ``ast.walk`` visits in full and which dominated the run's time.
_BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")


def _statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        yield node  # type: ignore[misc]
        for field in _BLOCK_FIELDS:
            stack.extend(getattr(node, field, None) or ())


def _scan(tree: ast.Module, text: str) -> tuple[set[int], set[int]]:
    """Assertion lines and neutral lines (imports, blanks, comments) in one pass."""
    assertion: set[int] = set()
    neutral: set[int] = set()
    for node in _statements(tree.body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            neutral.update(_span(node))
        elif isinstance(node, ast.Assert):
            assertion.update(_span(node))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                expr = item.context_expr
                if isinstance(expr, ast.Call) and _callee(expr.func) in RAISES_NAMES:
                    assertion.update(_span(expr))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            if (_callee(node.value.func) or "").startswith("assert"):
                assertion.update(_span(node))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and _assigns_expected(node):
            assertion.update(_span(node))
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            neutral.add(number)
    return assertion, neutral


@functools.lru_cache(maxsize=512)
def _parse_source(text: str) -> _Source:
    """Parse one file version. Cached by content: a file's old side in one
    commit is usually the new side it had in an earlier one."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _Source({}, frozenset(), frozenset(), broken=True)
    assertion, neutral = _scan(tree, text)
    spans = _test_spans(tree)
    lines = text.split("\n")
    bodies = {
        name: hashlib.sha256(
            textwrap.dedent("\n".join(lines[first - 1 : last])).encode("utf-8")
        ).hexdigest()
        for name, (first, last) in spans.items()
    }
    return _Source(
        tests=spans,
        assertion_lines=frozenset(assertion),
        neutral_lines=frozenset(neutral),
        bodies=bodies,
    )


class _Blobs:
    """Reads ``rev:path`` blobs through one long-lived ``git cat-file --batch``.

    One process instead of one ``git show`` per file revision: the walk reads
    about two blobs per changed file, and process start-up dominated the run.
    """

    def __init__(self, repo: pathlib.Path) -> None:
        self.repo = repo
        self._proc = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            env=_git_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def __enter__(self) -> _Blobs:
        return self

    def __exit__(self, *exc: object) -> None:
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        self._proc.wait()

    def source(self, rev: str, path: str) -> _Source:
        """The file at ``rev``, as a commit-independent parsed source."""
        stdin, stdout = self._proc.stdin, self._proc.stdout
        assert stdin is not None and stdout is not None
        stdin.write(f"{rev}:{path}\n".encode())
        stdin.flush()
        header = stdout.readline().decode("utf-8", errors="replace").split()
        if len(header) != 3 or header[1] != "blob":
            raise ChurnError(f"cannot read {rev}:{path}: {' '.join(header)}")
        data = stdout.read(int(header[2]))
        stdout.read(1)  # cat-file prints one LF after every object
        # Decoded without newline translation: git counts only "\n" in line numbers.
        return _parse_source(data.decode("utf-8", errors="replace"))
