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


def _json_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Local names bound to ``json.loads`` / ``json.load`` by ``from json
    import loads [as x]`` (``json.loads`` itself is always recognized)."""
    loads: set[str] = set()
    load: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "json":
            for alias in node.names:
                if alias.name == "loads":
                    loads.add(alias.asname or alias.name)
                elif alias.name == "load":
                    load.add(alias.asname or alias.name)
    return loads, load


def _callee_is(func: ast.expr, attr: str, aliases: set[str]) -> bool:
    if isinstance(func, ast.Attribute):
        return (
            func.attr == attr
            and isinstance(func.value, ast.Name)
            and func.value.id == "json"
        )
    return isinstance(func, ast.Name) and func.id in aliases


def _parsed_text(node: ast.AST, aliases: tuple[set[str], set[str]]) -> ast.expr | None:
    """The text expression a JSON-parse call reads, or ``None``: the
    argument of ``json.loads``/a ``loads`` alias, or the ``StringIO`` text a
    ``json.load``/``load`` alias is handed."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    loads, load = aliases
    if _callee_is(node.func, "loads", loads):
        return node.args[0]
    if _callee_is(node.func, "load", load):
        stream = node.args[0]
        if (
            isinstance(stream, ast.Call)
            and stream.args
            and (
                (isinstance(stream.func, ast.Name) and stream.func.id == "StringIO")
                or (
                    isinstance(stream.func, ast.Attribute)
                    and stream.func.attr == "StringIO"
                )
            )
        ):
            return stream.args[0]
    return None


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


def _bound_from(
    scope: ast.AST, seeds: set[str], helpers: set[str], *, output: bool = True
) -> set[str]:
    """Names in *scope* bound (to a fixed point) from *seeds* -- and, with
    *output*, from a combined-stream value."""
    names = set(seeds)
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
            hit = (
                _tainted(value, names, helpers)
                if output
                else any(
                    isinstance(n, ast.Name) and n.id in names for n in ast.walk(value)
                )
            )
            if not hit:
                continue
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name) and sub.id not in names:
                        names.add(sub.id)
                        changed = True
    return names


def _tainted_names(scope: ast.AST, helpers: set[str]) -> set[str]:
    """Names in *scope* bound (to a fixed point) from a combined-stream value."""
    return _bound_from(scope, set(), helpers)


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [a.arg for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]]


def _sink_args(
    call: ast.Call, sinks: dict[str, set[str]], params: dict[str, list[str]]
) -> list[ast.expr]:
    """The arguments *call* passes into a parameter its callee JSON-parses."""
    if not isinstance(call.func, ast.Name) or call.func.id not in sinks:
        return []
    sink_params, order = sinks[call.func.id], params[call.func.id]
    picked = [
        arg
        for index, arg in enumerate(call.args)
        if index < len(order) and order[index] in sink_params
    ]
    picked += [kw.value for kw in call.keywords if kw.arg in sink_params]
    return picked


def combined_stream_json_parses(source: str) -> list[int]:
    """Line numbers where a ``.output`` stream reaches a JSON parse: directly,
    through a local name, through a helper that returns it, or as the argument
    of a helper that parses one of its parameters."""
    tree = ast.parse(source)
    aliases = _json_aliases(tree)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    params = {fn.name: _params(fn) for fn in functions}
    helpers: set[str] = set()
    sinks: dict[str, set[str]] = {}
    changed = True
    while changed:
        changed = False
        for fn in functions:
            names = _tainted_names(fn, helpers)
            if fn.name not in helpers and any(
                isinstance(node, ast.Return)
                and node.value is not None
                and _tainted(node.value, names, helpers)
                for node in ast.walk(fn)
            ):
                helpers.add(fn.name)
                changed = True
            # A parameter is a sink when a value derived from it reaches a
            # JSON parse, here or in another sink helper.
            for param in params[fn.name]:
                if param in sinks.get(fn.name, set()):
                    continue
                derived = _bound_from(fn, {param}, set(), output=False)
                reached = False
                for node in ast.walk(fn):
                    texts = []
                    text = _parsed_text(node, aliases)
                    if text is not None:
                        texts.append(text)
                    if isinstance(node, ast.Call):
                        texts += _sink_args(node, sinks, params)
                    if any(
                        isinstance(n, ast.Name) and n.id in derived
                        for t in texts
                        for n in ast.walk(t)
                    ):
                        reached = True
                        break
                if reached:
                    sinks.setdefault(fn.name, set()).add(param)
                    changed = True
    hits: set[int] = set()
    for scope in [tree, *functions]:
        names = _tainted_names(scope, helpers) if scope is not tree else set()
        for node in ast.walk(scope):
            text = _parsed_text(node, aliases)
            if text is not None and _tainted(text, names, helpers):
                hits.add(node.lineno)
            if isinstance(node, ast.Call) and any(
                _tainted(arg, names, helpers) for arg in _sink_args(node, sinks, params)
            ):
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
        ("from json import loads\nloads(r.output)", [2]),
        ("from json import loads as parse\nparse(r.output)", [2]),
        ("import io\njson.load(io.StringIO(r.output))", [2]),
        ("from io import StringIO\njson.load(StringIO(r.output))", [2]),
        ("json.load(open(path))", []),
        (
            "def _outcomes(text):\n    return json.loads(text)['libraries']\n"
            "def t(r):\n    return _outcomes(r.output)",
            [4],
        ),
        (
            "def _outcomes(text):\n    doc = json.loads(text)\n    return doc\n"
            "def _wrap(raw):\n    return _outcomes(raw)\n"
            "def _run():\n    r = invoke()\n    return r.exit_code, r.output\n"
            "def t():\n    _, out = _run()\n    return _wrap(raw=out)",
            [11],
        ),
        (
            "def _outcomes(text):\n    return json.loads(text)\n"
            "def t(r):\n    return _outcomes(r.stdout)",
            [],
        ),
        (
            "def _show(text):\n    print(text)\ndef t(r):\n    return _show(r.output)",
            [],
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
