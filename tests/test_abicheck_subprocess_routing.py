# SPDX-License-Identifier: Apache-2.0
"""Every test that runs ``abicheck`` runs this checkout's ``abicheck`` (F7).

A literal argv that starts with ``"abicheck"`` or with ``sys.executable -m
abicheck`` runs whatever ``abicheck`` the interpreter or ``PATH`` resolves to,
which can be an installed copy rather than the tree under test. Such an argv
must come from ``tests/_abicheck_subprocess.py``. This scan parses every test
module and counts literal argv lists of that shape per file. The count must
equal the reviewed allowance below, so a new spawn cannot slip in, and a stale
allowance fails too.

An allowance is for an argv that is data, not a spawn: a parser fixture, an
expected-argv assertion, or the tail of an argv handed to a shim that spawns
its own process. Each entry says which.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_scan

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"
HELPER = TESTS / "_abicheck_subprocess.py"
THIS = Path(__file__).resolve()

#: Reviewed literal argv data per test file: (count, why it is not a spawn).
#: Shrink-only in practice: lower a count when a site is routed, never raise one.
DATA_ARGV_ALLOWANCE: dict[str, tuple[int, str]] = {
    "tests/test_l2_cli_perf_contracts.py": (
        3,
        "argv fixtures fed to the perf harness's argv parser; nothing is spawned",
    ),
    "tests/test_main_entrypoint.py": (
        1,
        "sys.argv for an in-process runpy of abicheck.__main__; nothing is spawned",
    ),
    "tests/test_action_run_sh_output_spellings.py": (
        1,
        "expected command text compared against the Action's shell script",
    ),
    "tests/test_workflow_examples.py": (
        1,
        "expected argv recorded in a workflow example fixture; compared, not run",
    ),
    "tests/_family_f7_catalog.py": (
        1,
        "a tuple of top-level directory names to copy; not an argv",
    ),
    "tests/test_gc_census_thread_safety.py": (
        1,
        "a tuple of repository roots to scan; not an argv",
    ),
    "tests/test_release_retired_module_names.py": (
        1,
        "a tuple of repository roots to scan; not an argv",
    ),
}


def _is_str(node: ast.AST, *values: str) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (not values or node.value in values)
    )


def _is_abicheck_module(node: ast.AST) -> bool:
    return _is_str(node) and (
        node.value == "abicheck" or node.value.startswith("abicheck.")  # type: ignore[attr-defined]
    )


def _is_interpreter(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "executable":
        return isinstance(node.value, ast.Name) and node.value.id == "sys"
    return _is_str(node, "python", "python3")


def _literal_abicheck_argv(node: ast.AST) -> bool:
    if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
        return False
    first, *rest = node.elts
    if _is_str(first, "abicheck") and rest:
        return True
    return (
        _is_interpreter(first)
        and len(rest) >= 2
        and _is_str(rest[0], "-m")
        and _is_abicheck_module(rest[1])
    )


def _violations_by_file() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(TESTS.rglob("*.py")):
        if path in (HELPER, THIS) or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = [node.lineno for node in ast.walk(tree) if _literal_abicheck_argv(node)]
        if lines:
            found[path.relative_to(REPO).as_posix()] = sorted(lines)
    return found


def test_literal_abicheck_argv_is_routed_through_the_helper() -> None:
    found = _violations_by_file()
    problems = []
    for rel, lines in found.items():
        allowed = DATA_ARGV_ALLOWANCE.get(rel, (0, ""))[0]
        if len(lines) != allowed:
            problems.append(
                f"{rel}: {len(lines)} literal argv site(s) at lines "
                f"{lines}, allowance {allowed}; route them through "
                "tests/_abicheck_subprocess.py (abicheck_command)"
            )
    for rel, (allowed, _why) in DATA_ARGV_ALLOWANCE.items():
        if rel not in found:
            problems.append(f"{rel}: allowance {allowed} is stale; remove it")
    assert not problems, "\n".join(problems)


def test_helper_pins_this_checkout_on_the_path() -> None:
    from tests._abicheck_subprocess import REPO_ROOT, abicheck_command, abicheck_env

    assert REPO_ROOT == REPO
    assert abicheck_env()["PYTHONPATH"].split(":")[0] == str(REPO)
    assert abicheck_command("--version")[1:3] == ["-m", "abicheck"]
