"""No test parses a ``CliRunner`` result's combined ``.output`` stream as JSON.

The bug class: Click >= 8.2's ``Result.output`` is stdout and stderr
interleaved. abicheck prints real diagnostics to stderr while a ``-o json=-``
document goes to stdout -- the release fan-out's memory-clamp note
("parallel release workers reduced ..."), the ``-H ignored`` warning,
"Report written to ...", the suppression and scope ledgers. A test that does
``json.loads(result.output)`` passes until one of those notes fires, then
fails for a reason unrelated to what it asserts (known gap "Release-fan-out
CLI tests JSON-parse ``CliRunner.output``", closed 2026-10-31). The document
is ``result.stdout`` alone.

The scan is a small intra-module taint walk, so it also catches the indirect
shapes the gap was found in: a helper that returns ``result.output`` (or a
tuple holding it) whose value reaches ``json.loads`` through a local name
or another helper. A test that wants the combined stream for a text
assertion keeps ``.output``; it just must not parse it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"


def _is_json_loads(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "loads"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "json"
        and bool(node.args)
    )


def _tainted(expr: ast.AST, names: set[str], helpers: set[str]) -> bool:
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Attribute) and sub.attr == "output":
            return True
        if isinstance(sub, ast.Name) and sub.id in names:
            return True
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id in helpers
        ):
            return True
    return False


def _tainted_names(scope: ast.AST, helpers: set[str]) -> set[str]:
    """Names in *scope* bound (to a fixed point) from a combined-stream value."""
    names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if not _tainted(value, names, helpers):
                continue
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name) and sub.id not in names:
                        names.add(sub.id)
                        changed = True
    return names


def combined_stream_json_parses(source: str) -> list[int]:
    """Line numbers of ``json.loads`` calls fed by a ``.output`` stream."""
    tree = ast.parse(source)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    helpers: set[str] = set()
    changed = True
    while changed:
        changed = False
        for fn in functions:
            if fn.name in helpers:
                continue
            names = _tainted_names(fn, helpers)
            if any(
                isinstance(node, ast.Return)
                and node.value is not None
                and _tainted(node.value, names, helpers)
                for node in ast.walk(fn)
            ):
                helpers.add(fn.name)
                changed = True
    hits: set[int] = set()
    for scope in [tree, *functions]:
        names = _tainted_names(scope, helpers) if scope is not tree else set()
        for node in ast.walk(scope):
            if _is_json_loads(node) and _tainted(node.args[0], names, helpers):
                hits.add(node.lineno)
    return sorted(hits)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("json.loads(result.output)", [1]),
        ("json.loads(result.output.strip())", [1]),
        ("json.loads(CliRunner().invoke(main, a).output)", [1]),
        ("json.loads(result.stdout)", []),
        ("assert 'x' in result.output\njson.loads(result.stdout)", []),
        (
            "def t(r):\n    out = r.output\n    return json.loads(out[out.find('{'):])",
            [3],
        ),
        (
            "def _run(*a):\n    r = invoke(a)\n    return r.exit_code, r.output\n"
            "def t():\n    code, out = _run('x')\n    return json.loads(out)",
            [6],
        ),
        (
            "def _run(*a):\n    r = invoke(a)\n    return r.exit_code, r.output\n"
            "def _report(*a):\n    return _run(*a)\n"
            "def t():\n    _, out = _report('x')\n    assert json.loads(out)",
            [8],
        ),
        (
            "def _run(*a):\n    r = invoke(a)\n    return r.exit_code, r.output\n"
            "def _run_stdout(*a):\n    r = invoke(a)\n    return r.exit_code, r.stdout\n"
            "def t():\n    code, out = _run('x')\n    assert 'err' in out\n"
            "    _, doc = _run_stdout('x')\n    return json.loads(doc)",
            [],
        ),
    ],
)
def test_the_scan_finds_direct_and_helper_routed_parses(
    source: str, expected: list[int]
) -> None:
    assert combined_stream_json_parses(source) == expected


@pytest.mark.repo_scan
def test_no_test_json_parses_the_combined_output_stream() -> None:
    offenders = [
        f"{path.relative_to(REPO)}:{line}"
        for path in sorted(TESTS.rglob("*.py"))
        for line in combined_stream_json_parses(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "json.loads() of a CliRunner result's combined .output stream (stdout "
        "and stderr interleaved; a stderr note corrupts the document). Parse "
        "result.stdout instead:\n  " + "\n  ".join(offenders)
    )
