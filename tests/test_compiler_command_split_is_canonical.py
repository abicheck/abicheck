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

"""No module under ``abicheck/`` tokenizes compiler command text on its own.

Bug class ``tooling.platform_dependent_record_separator``
(``tests/regressions/manifest_tool_surface.py``): one host-dependent
convention, restated inline at N call sites, is N places to get it wrong.
``test_compiler_command_split_host_parity.py`` pins that every site agrees;
this module is the structural half, so a *new* site cannot reintroduce its
own splitter.

Two rules, both enforced as an AST scan (a ``shlex.split`` inside a docstring
or comment is not a call):

* no ``shlex.split`` call outside :mod:`abicheck._compiler_options` may pass
  a ``posix=`` argument that reads ``os.name`` or ``sys.platform`` -- the
  exact shape that let make, ninja and compiler-record disagree with the
  compile database; and
* no ``shlex.split`` call outside that module at all, unless it is in
  :data:`_REVIEWED_NON_COMPILER_TEXT` with the reason it is not compiler
  command text. The allowlist must stay live: a stale entry fails too.

The scan is only trustworthy if it can see what it is meant to catch, so the
last test feeds it known-bad and known-good shapes directly.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_scan

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "abicheck"
CANONICAL_REL = "abicheck/_compiler_options.py"

#: ``(repo-relative path, enclosing function)`` -> why this ``shlex.split`` is
#: not compiler command text. Each reason is checked by reading the call site.
_REVIEWED_NON_COMPILER_TEXT: dict[tuple[str, str], str] = {
    (
        "abicheck/buildsource/inline.py",
        "_run_build_query",
    ): "the user-configured `build.query` argv (a build-system query command)",
    (
        "abicheck/cli_dump_dry_run_build_query.py",
        "_trusted_query_argv",
    ): "the same `build.query` argv, tokenized for the dry-run preview",
    (
        "abicheck/buildsource/source_extractors/launcher_env.py",
        "_expand_env_split_string",
    ): "GNU `env -S` grammar, which is POSIX-shell-like on every host",
}

_HOST_DEPENDENT_POSIX = re.compile(r"os\.name|sys\.platform")


def _is_shlex_split(call: ast.Call, split_imported: bool) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "split":
        return isinstance(func.value, ast.Name) and func.value.id == "shlex"
    return split_imported and isinstance(func, ast.Name) and func.id == "split"


def _shlex_split_calls(tree: ast.AST) -> list[tuple[ast.Call, str]]:
    """Every ``shlex.split`` call, paired with its innermost enclosing function."""
    split_imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "shlex"
        and any(alias.name == "split" for alias in node.names)
        for node in ast.walk(tree)
    )
    found: list[tuple[ast.Call, str]] = []

    def visit(node: ast.AST, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            scope = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else enclosing
            )
            if isinstance(child, ast.Call) and _is_shlex_split(child, split_imported):
                found.append((child, scope))
            visit(child, scope)

    visit(tree, "<module>")
    return found


def find_violations(source: str, rel: str) -> list[str]:
    """Violations in one module's source; ``rel`` is its repo-relative path."""
    if rel == CANONICAL_REL:
        return []
    violations: list[str] = []
    for call, func in _shlex_split_calls(ast.parse(source)):
        where = f"{rel}:{call.lineno} in {func}()"
        for keyword in call.keywords:
            if keyword.arg == "posix" and _HOST_DEPENDENT_POSIX.search(
                ast.unparse(keyword.value)
            ):
                violations.append(
                    f"{where}: shlex.split(posix=...) reads the host "
                    f"({ast.unparse(keyword.value)}); use "
                    "abicheck._compiler_options.split_command_line"
                )
        if (rel, func) not in _REVIEWED_NON_COMPILER_TEXT:
            violations.append(
                f"{where}: direct shlex.split outside the canonical helper; "
                "route compiler command text through "
                "abicheck._compiler_options.split_command_line"
            )
    return violations


def _package_sources() -> list[tuple[str, str]]:
    """``(repo-relative path, source)`` for every module under ``abicheck/``."""
    sources: list[tuple[str, str]] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "shlex" not in text:
            continue
        sources.append((path.relative_to(REPO_ROOT).as_posix(), text))
    return sources


def test_no_module_tokenizes_compiler_command_text_outside_the_canonical_helper() -> (
    None
):
    violations = [
        violation
        for rel, source in _package_sources()
        for violation in find_violations(source, rel)
    ]
    assert not violations, "\n".join(violations)


def test_scan_is_not_vacuous_the_canonical_helper_is_seen() -> None:
    canonical = (REPO_ROOT / CANONICAL_REL).read_text(encoding="utf-8")
    calls = _shlex_split_calls(ast.parse(canonical))
    assert [func for _, func in calls] == ["split_command_line"]


def test_reviewed_allowlist_entries_are_live() -> None:
    """A reviewed exemption whose call site is gone must not linger."""
    live: set[tuple[str, str]] = set()
    for rel, source in _package_sources():
        for _call, func in _shlex_split_calls(ast.parse(source)):
            live.add((rel, func))
    stale = sorted(set(_REVIEWED_NON_COMPILER_TEXT) - live)
    assert not stale, f"allowlist entries with no matching shlex.split: {stale}"


@pytest.mark.parametrize(
    ("source", "expected_fragment"),
    [
        (
            "import os, shlex\n"
            "def f(text):\n"
            '    return shlex.split(text, posix=os.name != "nt")\n',
            "shlex.split(posix=...) reads the host",
        ),
        (
            "import sys, shlex\n"
            "def f(text):\n"
            '    return shlex.split(text, posix=sys.platform != "win32")\n',
            "shlex.split(posix=...) reads the host",
        ),
        (
            "import shlex\ndef f(command):\n    return shlex.split(command)\n",
            "direct shlex.split outside the canonical helper",
        ),
        (
            "from shlex import split\ndef f(command):\n    return split(command)\n",
            "direct shlex.split outside the canonical helper",
        ),
    ],
)
def test_scanner_flags_the_known_bad_shapes(
    source: str, expected_fragment: str
) -> None:
    violations = find_violations(source, "abicheck/buildsource/adapters/make.py")
    assert len(violations) >= 1, "the scanner missed a known-bad call"
    assert any(expected_fragment in v for v in violations), violations


def test_scanner_passes_the_canonical_helper_and_reviewed_exemptions() -> None:
    bad_shape = "import shlex\ndef f(c):\n    return shlex.split(c)\n"
    assert find_violations(bad_shape, CANONICAL_REL) == []
    exempt = "import shlex\ndef _run_build_query(c):\n    return shlex.split(c)\n"
    assert find_violations(exempt, "abicheck/buildsource/inline.py") == [], (
        "a reviewed exemption must suppress its own call site"
    )
    assert find_violations(exempt, "abicheck/buildsource/adapters/make.py")
