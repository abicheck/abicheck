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


"""Static gate for "mirror tests" under ``tests/``.

A mirror test restates the implementation instead of checking required
behaviour, so it stays green when the behaviour is wrong. Four shapes:

* ``type-only`` -- every assertion only checks a type or presence:
  ``isinstance``/``issubclass``/``hasattr``/``callable``, ``type(x) is ...``,
  ``x is None`` / ``is not None``, ``len(x) > 0``, or a bare truthiness of a
  name. At least one assertion must exist and none may check a value.
* ``constructor-echo`` -- ``x = Cls(name="a")`` followed by only
  ``assert x.name == "a"``: the constructor's own stored attribute read back.
  Every assertion is an echo or a type check, at least one is an echo, and the
  test calls nothing but that constructor and builtin type checks. A call of
  any other code under test makes it a real test.
* ``constant-mirror`` -- ``assert ChangeKind.FOO.value == "foo"`` or
  ``assert MODULE.CONST == 3``: a module-level name's attribute compared to a
  literal, with no call in the test at all.
* ``mock-only`` -- the test patches something (``patch``, ``MagicMock``,
  ``monkeypatch.setattr``, ``mocker.patch``) and every assertion is on a mock
  (``assert_called*``, ``call_count``, ``call_args``, ``.called``). It never
  checks what the code under test returned or changed.

Helper resolution follows ``assertion_resolution.py``: a test's assertions
include those of same-file functions it calls, transitively. A check this gate
does not recognise (a ``with`` block such as ``pytest.raises``, an unrecognised
assertion-named call) makes the test *opaque*, and an opaque test is never
flagged -- the gate errs toward silence rather than flagging real tests.

Each test is reported under at most one rule, the first that matches in the
order above. A deliberate mirror is exempted on its ``def`` line with a
``# mirror-ok: <reason>`` comment; no reason, no exemption.

Existing findings are recorded per ``(file, test, rule)`` **count** in
``scripts/test_mirror_patterns_baseline.json`` -- counts, not line numbers, so
an unrelated edit does not churn the file. A new or grown key is an error. A
key the tree no longer produces is also an error until the baseline is shrunk
with ``python scripts/test_mirror_patterns.py --update``. Registered in
``check_ai_readiness.py`` as ``test-mirror-patterns``.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from assertion_resolution import (
    ASSERTION_CALL_HINTS,
    FunctionNode,
    call_attr_or_name,
    function_index,
)
from baseline_loader import read_baseline
from findings_report import Findings

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
BASELINE_FILE = ROOT / "scripts" / "test_mirror_patterns_baseline.json"
CHECK_NAME = "test-mirror-patterns"

RULES: tuple[str, ...] = (
    "type-only",
    "constructor-echo",
    "constant-mirror",
    "mock-only",
)

# Builtins a test may call without that counting as "calling code under test".
_TYPE_CHECK_BUILTINS = frozenset(
    {"isinstance", "issubclass", "type", "len", "hasattr", "callable"}
)
_TYPE_CHECK_CALLS = frozenset({"isinstance", "issubclass", "hasattr", "callable"})

# Mock-object assertions: ``m.assert_called_once_with(...)`` and friends.
_MOCK_ASSERT_CALLS = frozenset(
    {"assert_any_call", "assert_has_calls", "assert_not_called"}
)
_MOCK_ATTRS = frozenset({"call_count", "call_args", "call_args_list", "called"})

# Callables that create a patch or a mock in the test.
_MOCK_SETUP_LAST = frozenset(
    {"patch", "MagicMock", "Mock", "AsyncMock", "NonCallableMagicMock"}
)
_MOCK_SETUP_SUFFIXES = ("monkeypatch.setattr", "patch.object", "mocker.patch")

_PRAGMA = re.compile(r"#\s*mirror-ok:\s*\S.{2,}")


@dataclass(frozen=True)
class Finding:
    path: str
    test: str
    line: int
    rule: str

    @property
    def key(self) -> str:
        return f"{self.path}::{self.test}::{self.rule}"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _is_const(node: ast.AST, value: object) -> bool:
    return isinstance(node, ast.Constant) and node.value is value


def _is_const_int(node: ast.AST, value: int) -> bool:
    return (
        isinstance(node, ast.Constant)
        and type(node.value) is int
        and node.value == value
    )


def _effective_nodes(fn: FunctionNode, funcs: dict[str, FunctionNode]) -> list[ast.AST]:
    """Every node of *fn* and of the same-file functions it calls, transitively."""
    seen: set[str] = {fn.name}
    pending = [fn]
    nodes: list[ast.AST] = []
    while pending:
        current = pending.pop()
        nodes.extend(ast.walk(current))
        for node in ast.walk(current):
            if isinstance(node, ast.Call):
                name = call_attr_or_name(node)
                if name in funcs and name not in seen:
                    seen.add(name)
                    pending.append(funcs[name])
    return nodes


def _module_names(tree: ast.Module, fn: FunctionNode) -> set[str]:
    """Names bound at module scope, plus names the test itself imports."""
    names: set[str] = set()
    for node in [*tree.body, *ast.walk(fn)]:
        if isinstance(node, ast.Import | ast.ImportFrom):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            if node in tree.body:
                names.add(node.name)
        elif isinstance(node, ast.Assign) and node in tree.body:
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node in tree.body:
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _is_mock_setup(call: ast.Call) -> bool:
    dotted = _dotted(call.func)
    if not dotted:
        return False
    return dotted.split(".")[-1] in _MOCK_SETUP_LAST or dotted.endswith(
        _MOCK_SETUP_SUFFIXES
    )


def _is_mock_setup_expr(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _is_mock_setup(node)


def _is_mock_assert_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    attr = node.func.attr
    return attr.startswith("assert_called") or attr in _MOCK_ASSERT_CALLS


def _is_mock_shaped(test: ast.AST) -> bool:
    """An assertion whose subject is a mock's bookkeeping, not a value."""
    if isinstance(test, ast.Call):
        return _is_mock_assert_call(test)
    if isinstance(test, ast.Attribute):
        return test.attr in _MOCK_ATTRS
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        return isinstance(test.left, ast.Attribute) and test.left.attr in _MOCK_ATTRS
    if isinstance(test, ast.BoolOp):
        return all(_is_mock_shaped(v) for v in test.values)
    return False


def _is_type_only(test: ast.AST) -> bool:
    """An assertion that checks a type or presence, never a value."""
    if isinstance(test, ast.Name):
        return True  # bare truthiness of a name
    if isinstance(test, ast.Call):
        return call_attr_or_name(test) in _TYPE_CHECK_CALLS and isinstance(
            test.func, ast.Name
        )
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _is_type_only(test.operand)
    if isinstance(test, ast.BoolOp):
        return all(_is_type_only(v) for v in test.values)
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        left, op, right = test.left, test.ops[0], test.comparators[0]
        if isinstance(left, ast.Call) and isinstance(left.func, ast.Name):
            if left.func.id == "type":
                return True  # type(x) is Y / type(x) == Y
            if left.func.id == "len":
                # len(x) > 0, len(x) != 0, len(x) >= 1: presence, not size.
                return (
                    isinstance(op, ast.Gt | ast.NotEq) and _is_const_int(right, 0)
                ) or (isinstance(op, ast.GtE) and _is_const_int(right, 1))
        if isinstance(op, ast.Is | ast.IsNot) and _is_const(right, None):
            return True  # x is None / x is not None
    return False


def _is_echo(test: ast.AST, ctor_kwargs: dict[str, dict[str, ast.AST]]) -> bool:
    """``x.attr == V`` where ``x = Cls(attr=V)`` in the same test."""
    if not (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and isinstance(test.left, ast.Attribute)
        and isinstance(test.left.value, ast.Name)
    ):
        return False
    kwargs = ctor_kwargs.get(test.left.value.id, {})
    passed = kwargs.get(test.left.attr)
    return passed is not None and ast.dump(passed) == ast.dump(test.comparators[0])


def _is_constant_mirror(test: ast.AST, module_names: set[str]) -> bool:
    """``MODULE.CONST == literal`` / ``ENUM.MEMBER.value == literal``."""
    if not (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and isinstance(test.left, ast.Attribute)
        and isinstance(test.comparators[0], ast.Constant)
    ):
        return False
    root: ast.AST = test.left
    while isinstance(root, ast.Attribute):
        root = root.value
    return isinstance(root, ast.Name) and root.id in module_names


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass
class _Checks:
    """The recognised checks of one test, after helper resolution."""

    asserts: list[ast.Assert]
    mock_checks: int
    opaque: bool


def _collect_checks(nodes: list[ast.AST], funcs: dict[str, FunctionNode]) -> _Checks:
    asserts: list[ast.Assert] = []
    mock_checks = 0
    opaque = False
    for node in nodes:
        if isinstance(node, ast.Assert):
            if _is_mock_shaped(node.test):
                mock_checks += 1
            else:
                asserts.append(node)
        elif isinstance(node, ast.With | ast.AsyncWith):
            # ``with mock.patch(...)`` sets up a mock; ``with pytest.raises``
            # expresses a behaviour expectation this gate cannot read.
            if not all(_is_mock_setup_expr(i.context_expr) for i in node.items):
                opaque = True
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            name = call_attr_or_name(node.value)
            if _is_mock_assert_call(node.value):
                mock_checks += 1
            elif name in funcs:
                continue  # a same-file helper; its own asserts were collected
            elif any(h in name.lower() for h in ASSERTION_CALL_HINTS):
                opaque = True  # an assertion-named call this gate cannot read
    return _Checks(asserts, mock_checks, opaque)


def classify(
    fn: FunctionNode, tree: ast.Module, funcs: dict[str, FunctionNode]
) -> str | None:
    """The mirror rule *fn* matches, or ``None``."""
    nodes = _effective_nodes(fn, funcs)
    checks = _collect_checks(nodes, funcs)
    if checks.opaque or (not checks.asserts and not checks.mock_checks):
        return None

    calls = [n for n in nodes if isinstance(n, ast.Call)]
    module_names = _module_names(tree, fn)

    if not checks.mock_checks and checks.asserts:
        if all(_is_type_only(a.test) for a in checks.asserts):
            return "type-only"

        ctor_kwargs = _constructor_kwargs(fn)
        echo_names = {
            name
            for a in checks.asserts
            if (name := _echo_subject(a.test, ctor_kwargs)) is not None
        }
        if echo_names and all(
            _is_type_only(a.test) or _is_echo(a.test, ctor_kwargs)
            for a in checks.asserts
        ):
            allowed = {id(c) for c in _constructor_calls(fn, echo_names)}
            if all(id(c) in allowed or _is_type_check_builtin(c) for c in calls):
                return "constructor-echo"

        if all(_is_constant_mirror(a.test, module_names) for a in checks.asserts):
            if all(_is_type_check_builtin(c) for c in calls):
                return "constant-mirror"

    if checks.mock_checks and not checks.asserts:
        if any(isinstance(c, ast.Call) and _is_mock_setup(c) for c in nodes):
            return "mock-only"
    return None


def _is_type_check_builtin(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Name) and call.func.id in _TYPE_CHECK_BUILTINS


def _constructor_calls(fn: FunctionNode, names: set[str]) -> list[ast.Call]:
    """Calls assigned to a name in *names*, e.g. ``x = Cls(...)``."""
    found: list[ast.Call] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
                found.append(node.value)
    return found


def _constructor_kwargs(fn: FunctionNode) -> dict[str, dict[str, ast.AST]]:
    """``{name: {kwarg: value}}`` for every ``name = Call(kw=...)`` in *fn*."""
    table: dict[str, dict[str, ast.AST]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    kwargs = {k.arg: k.value for k in node.value.keywords if k.arg}
                    table.setdefault(target.id, {}).update(kwargs)
    return table


def _echo_subject(
    test: ast.AST, ctor_kwargs: dict[str, dict[str, ast.AST]]
) -> str | None:
    if _is_echo(test, ctor_kwargs):
        assert isinstance(test, ast.Compare) and isinstance(test.left, ast.Attribute)
        assert isinstance(test.left.value, ast.Name)
        return test.left.value.id
    return None


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan_source(source: str, path: str) -> list[Finding]:
    tree = ast.parse(source, filename=path)
    lines = source.splitlines()
    funcs = function_index(tree)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test")
        ):
            continue
        if _PRAGMA.search(lines[node.lineno - 1]):
            continue
        rule = classify(node, tree, funcs)
        if rule is not None:
            findings.append(Finding(path, node.name, node.lineno, rule))
    return findings


def scan_tree(root: Path = TESTS) -> Iterator[Finding]:
    for path in sorted(root.rglob("test_*.py")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            yield from scan_source(source, rel)
        except SyntaxError:
            continue


def rule_counts(findings: list[Finding]) -> dict[str, int]:
    counts = dict.fromkeys(RULES, 0)
    for f in findings:
        counts[f.rule] += 1
    return counts


def load_baseline() -> dict[str, int]:
    return read_baseline(BASELINE_FILE)["findings"]


def compare_to_baseline(findings: list[Finding], baseline: dict[str, int]) -> list[str]:
    """Errors: keys above their baseline count, and keys the tree no longer has."""
    counts = Counter(f.key for f in findings)
    lines_by_key: dict[str, list[int]] = {}
    for f in findings:
        lines_by_key.setdefault(f.key, []).append(f.line)
    errors: list[str] = []
    for key, n in sorted(counts.items()):
        allowed = baseline.get(key, 0)
        if n > allowed:
            errors.append(
                f"{key}: {n} finding(s), baseline {allowed} -- lines "
                f"{', '.join(map(str, lines_by_key[key]))}. Exempt a deliberate "
                "mirror with '# mirror-ok: <reason>' on its def line, or fix the test."
            )
    for key, allowed in sorted(baseline.items()):
        if counts.get(key, 0) < allowed:
            errors.append(
                f"{key}: baseline {allowed}, now {counts.get(key, 0)} -- "
                "shrink the baseline (--update)"
            )
    return errors


def check_test_mirror_patterns(f: Findings) -> None:
    findings = list(scan_tree())
    counts = rule_counts(findings)
    f.info(
        CHECK_NAME,
        "mirror-test findings by rule: " + ", ".join(f"{r}={counts[r]}" for r in RULES),
    )
    for msg in compare_to_baseline(findings, load_baseline()):
        f.err(CHECK_NAME, msg)


def write_baseline() -> int:
    counts = Counter(f.key for f in scan_tree())
    payload = {
        "_comment": "Generated by scripts/test_mirror_patterns.py --update. Per (file::test::rule) finding counts; a new or grown entry needs its reason in the PR.",
        "findings": dict(sorted(counts.items())),
    }
    BASELINE_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return sum(counts.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--update",
        action="store_true",
        help="re-record the baseline from the current tree",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable summary on stdout",
    )
    args = parser.parse_args(argv)
    if args.update:
        print(
            f"recorded {write_baseline()} finding(s) in {BASELINE_FILE.relative_to(ROOT)}"
        )
        return 0

    findings = list(scan_tree())
    counts = rule_counts(findings)
    errors = compare_to_baseline(findings, load_baseline())
    if args.json:
        print(
            json.dumps(
                {
                    "counts": counts,
                    "findings": [
                        {"path": f.path, "test": f.test, "line": f.line, "rule": f.rule}
                        for f in findings
                    ],
                    "errors": errors,
                    "exit_code": 1 if errors else 0,
                }
            )
        )
    else:
        for rule in RULES:
            print(f"{rule}: {counts[rule]}")
        for msg in errors:
            print(f"ERROR {msg}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
