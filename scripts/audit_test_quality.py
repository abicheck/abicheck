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

"""Find tautological tests, and ratchet them in CI.

A static scan (stdlib ``ast``; nothing is executed) of every ``test_*``
function under ``tests/``. A flag names a test whose asserts cannot fail for
a wrong *behaviour*, only for a renamed attribute or a removed call:

* ``type_only`` -- every ``assert`` is ``x is not None``, ``callable``,
  ``hasattr``, ``isinstance(x, <builtin>)`` or ``len(x) > 0``. A negation
  (``not hasattr``), ``is None``, bare truthiness and ``isinstance`` against
  a project class are behaviour (an absence, a flag, the dispatched class).
* ``mock_echo`` -- the test patches something and only checks *that* a mock
  was called (``assert_called*``, ``called``, ``call_count``), never with
  what (``call_args`` and ``assert_called_with`` carry the behaviour).
* ``set_then_read`` -- every value assert reads ``x.attr == LIT`` straight
  off ``x = Call(attr=LIT)``: the object echoes its constructor.
* ``constant_mirror`` -- every value assert pins an ``abicheck`` constant or
  enum ``.value`` to a literal.
* ``no_assert`` -- no assert, ``pytest.raises``/``warns``, ``assert*`` call,
  or call to a same-file helper that asserts.
* ``trivial_size`` -- at most three statements and one of the first four
  (report only, not gated).

``--check`` is the CI gate (``verify.py``'s ``test-quality`` step): a gated
finding absent from ``scripts/test_quality_baseline.json`` fails, and so does
a baselined key that is no longer found, so the baseline only shrinks. With
``--base``/``$ARCHITECTURE_BASE`` a key the base revision lacks fails too. A
test deliberately shaped like a flag (a wire-format pin, a must-not-raise
smoke test) carries ``# test-quality: ok <reason>`` on its ``def`` or a
decorator line. Without ``--check`` it prints a report (``--json PATH`` for
the rows) and exits 0.
"""

from __future__ import annotations

import argparse
import ast
import collections
import itertools
import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Iterable, Iterator
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

FLAGS: tuple[str, ...] = (
    "type_only",
    "mock_echo",
    "set_then_read",
    "constant_mirror",
    "no_assert",
    "trivial_size",
)
#: Flags that describe *what the asserts check*; ``trivial_size`` is derived
#: from these, and ``no_assert`` is about the absence of any check.
_VALUE_SHAPE_FLAGS: tuple[str, ...] = (
    "type_only",
    "mock_echo",
    "set_then_read",
    "constant_mirror",
)
_TRIVIAL_MAX_STATEMENTS = 3

#: Calls whose result is an existence/type/truthiness check, not a value.
_WEAK_CALLS = frozenset({"callable", "hasattr"})
#: ``isinstance(x, T)`` is weak only for a builtin ``T``: checking that a
#: dispatcher picked ``RpmExtractor`` is behaviour, checking "it's a dict" is not.
_TYPE_CHECK_CALLS = frozenset({"isinstance", "issubclass"})
_BUILTIN_TYPES = frozenset(
    {
        "dict",
        "list",
        "tuple",
        "set",
        "frozenset",
        "str",
        "bytes",
        "int",
        "float",
        "bool",
        "object",
        "type",
    }
)
#: Context managers that express an expected exception or warning.
_RAISES_CALLS = frozenset(
    {"raises", "warns", "deprecated_call", "assertRaises", "assertWarns"}
)
#: Callables that create or install a mock.
_PATCH_CALLS = frozenset(
    {"patch", "MagicMock", "Mock", "AsyncMock", "NonCallableMock", "PropertyMock"}
)
#: Mock attributes that record *whether* a call happened, not with what.
#: ``call_args``/``mock_calls`` carry the arguments, which are often the very
#: behaviour under test (which compiler was picked), so they do not count.
_MOCK_VERIFY_ATTRS = frozenset({"called", "call_count", "await_count", "awaited"})
#: Argument-free mock verification methods.
_MOCK_VERIFY_METHODS = frozenset(
    {
        "assert_called",
        "assert_called_once",
        "assert_not_called",
        "assert_awaited",
        "assert_awaited_once",
        "assert_not_awaited",
    }
)
#: ``len(x) <op> bound`` forms that only say "x is non-empty".
_NONEMPTY_LEN_FORMS = frozenset({(ast.Gt, 0), (ast.GtE, 1), (ast.NotEq, 0)})
_NONEMPTY_LEN_REVERSED_FORMS = frozenset({(ast.Lt, 0), (ast.LtE, 1), (ast.NotEq, 0)})
#: Name fragments check_ai_readiness's test-assertion-density gate treats as
#: assertion-bearing. Mirrored so the two tools agree on what "asserts" means.
_ASSERTION_HINTS: tuple[str, ...] = (
    "assert",
    "check",
    "verify",
    "expect",
    "validate",
    "ensure",
    "roundtrip",
    "raises",
    "warns",
    "fail",
)

#: Nodes whose bodies are a separate scope; a test's own statements stop here.
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


class TestAudit(NamedTuple):
    """One audited test function and the flags it carries (possibly none)."""

    file: str
    line: int
    test: str
    flags: tuple[str, ...]
    exempt: bool = False

    @property
    def key(self) -> str:
        """Line-free identity, so edits elsewhere in a file do not churn it."""
        return f"{self.file}::{self.test}"


#: Flags the CI gate ratchets. ``trivial_size`` is derived from the others.
GATED_FLAGS: tuple[str, ...] = (
    "type_only",
    "mock_echo",
    "set_then_read",
    "constant_mirror",
    "no_assert",
)
#: A test that is deliberately shaped like a flag (a wire-format pin, a
#: "must not raise" smoke test) carries this on its ``def`` line or a line of
#: its decorators, followed by the reason.
EXEMPT_MARKER = "test-quality: ok"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _call_name(node: ast.Call) -> str:
    """The callee's last name: ``f`` for ``f()``, ``m`` for ``x.m()``."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _dotted(node: ast.expr) -> str:
    """``a.b.c`` for an attribute chain rooted at a name, else ``""``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def _root_name(node: ast.expr) -> str:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _own_nodes(body: Iterable[ast.stmt]) -> Iterator[ast.AST]:
    """Every node of a function body, not descending into nested scopes."""
    stack: list[ast.AST] = [n for n in body if not isinstance(n, _SCOPE_NODES)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(
            c for c in ast.iter_child_nodes(node) if not isinstance(c, _SCOPE_NODES)
        )


def _statement_count(body: list[ast.stmt]) -> int:
    """Statements in a body, not counting a leading docstring."""
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return len(body) - 1
    return len(body)


def _is_test_name(name: str) -> bool:
    return name.startswith("test_") or name == "test"


def _test_functions(tree: ast.Module) -> list[tuple[str, _FuncDef]]:
    """Every test function with its qualified name (``Class.test_x`` for methods)."""
    found: list[tuple[str, _FuncDef]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                if _is_test_name(child.name):
                    found.append((prefix + child.name, child))
                walk(child, prefix)
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _abicheck_names(tree: ast.Module) -> frozenset[str]:
    """Names bound by importing from ``abicheck`` anywhere in the file."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "abicheck" or node.module.startswith("abicheck."):
                names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "abicheck" or alias.name.startswith("abicheck."):
                    names.add(alias.asname or alias.name.split(".")[0])
    return frozenset(names)


# ---------------------------------------------------------------------------
# Assert classification
# ---------------------------------------------------------------------------


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_len_call(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and _call_name(node) == "len"
        and len(node.args) == 1
        and not node.keywords
    )


def _nonempty_len(left: ast.expr, op: ast.cmpop, right: ast.expr) -> bool:
    if _is_len_call(left) and isinstance(right, ast.Constant):
        return (type(op), right.value) in _NONEMPTY_LEN_FORMS
    if _is_len_call(right) and isinstance(left, ast.Constant):
        return (type(op), left.value) in _NONEMPTY_LEN_REVERSED_FORMS
    return False


def _is_weak(node: ast.expr) -> bool:
    """True for an existence, type or truthiness check that compares no value."""
    # ``not hasattr(x, "f")`` / ``not isinstance(...)`` assert an absence,
    # which is a contract, so a negation is never weak.
    if isinstance(node, ast.BoolOp):
        return all(_is_weak(v) for v in node.values)
    # Bare truthiness (``assert result.ok``, ``assert not offenders``) and
    # ``x is None`` are *not* weak: a flag, an empty collection and an absent
    # value are all real outcomes a function can get wrong.
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name in _TYPE_CHECK_CALLS and len(node.args) == 2:
            types = node.args[1]
            elts = types.elts if isinstance(types, ast.Tuple) else [types]
            return all(isinstance(t, ast.Name) and t.id in _BUILTIN_TYPES for t in elts)
        return name in _WEAK_CALLS
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        op, right = node.ops[0], node.comparators[0]
        if isinstance(op, ast.IsNot) and _is_none(right):
            return True
        return _nonempty_len(node.left, op, right)
    return False


def _attr_literal(node: ast.expr) -> tuple[str, ast.Constant] | None:
    """``(attr, literal)`` for ``obj.attr == LITERAL`` (either operand order)."""
    if not (isinstance(node, ast.Compare) and len(node.ops) == 1):
        return None
    if not isinstance(node.ops[0], ast.Eq):
        return None
    left, right = node.left, node.comparators[0]
    for attr_side, lit_side in ((left, right), (right, left)):
        if isinstance(attr_side, ast.Attribute) and isinstance(lit_side, ast.Constant):
            return attr_side.attr, lit_side
    return None


def _constructor_inputs(
    nodes: list[ast.AST],
) -> dict[str, set[tuple[str, object, type]]]:
    """``var -> {(kwarg, literal value, literal type)}`` for ``var = Call(kw=LIT)``.

    Only a plain name bound straight from a call counts, so a value that went
    through a transform (a round-trip, a merge, a parser) is never an echo.
    A name bound more than once is dropped: which binding an assert reads is
    not decidable without control flow.
    """
    bound: dict[str, set[tuple[str, object, type]]] = {}
    seen: collections.Counter[str] = collections.Counter()
    for node in nodes:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    seen[target.id] += 1
                    value = node.value
                    if isinstance(value, ast.Call):
                        bound[target.id] = {
                            (kw.arg, kw.value.value, type(kw.value.value))
                            for kw in value.keywords
                            if kw.arg and isinstance(kw.value, ast.Constant)
                        }
    return {name: pairs for name, pairs in bound.items() if seen[name] == 1}


def _echoes_input(
    assert_node: ast.Assert, inputs: dict[str, set[tuple[str, object, type]]]
) -> bool:
    """True for ``x.attr == LITERAL`` where ``x = Call(attr=LITERAL)``.

    The object is read back exactly as the test built it: nothing between
    the constructor and the assert could have changed what is checked.
    """
    pair = _attr_literal(assert_node.test)
    if pair is None:
        return False
    attr, literal = pair
    test = assert_node.test
    assert isinstance(test, ast.Compare)
    attr_node = (
        test.left if isinstance(test.left, ast.Attribute) else test.comparators[0]
    )
    assert isinstance(attr_node, ast.Attribute)
    if not isinstance(attr_node.value, ast.Name):
        return False
    pairs = inputs.get(attr_node.value.id, set())
    return (attr, literal.value, type(literal.value)) in pairs


def _abicheck_constant(node: ast.expr, names: frozenset[str]) -> bool:
    """An ``abicheck`` module constant, or an enum member's ``.value``."""
    if isinstance(node, ast.Name):
        return node.id in names and node.id.isupper()
    if isinstance(node, ast.Attribute):
        if _root_name(node) not in names:
            return False
        return node.attr == "value" or node.attr.isupper()
    return False


def _is_constant_mirror(node: ast.expr, names: frozenset[str]) -> bool:
    if not (isinstance(node, ast.Compare) and len(node.ops) == 1):
        return False
    if not isinstance(node.ops[0], ast.Eq):
        return False
    left, right = node.left, node.comparators[0]
    return (_abicheck_constant(left, names) and isinstance(right, ast.Constant)) or (
        _abicheck_constant(right, names) and isinstance(left, ast.Constant)
    )


# ---------------------------------------------------------------------------
# Mock and expectation classification
# ---------------------------------------------------------------------------


def _is_patching_call(node: ast.Call) -> bool:
    dotted = _dotted(node.func)
    last = dotted.rsplit(".", 1)[-1]
    if last == "setattr" and dotted.startswith("monkeypatch."):
        return True
    if dotted.endswith(("patch.object", "patch.dict")):
        return True
    return last in _PATCH_CALLS


def _is_mock_verify(test: ast.expr) -> bool:
    """True when an assert only reads whether a mock was called."""
    return any(
        isinstance(node, ast.Attribute) and node.attr in _MOCK_VERIFY_ATTRS
        for node in ast.walk(test)
    )


def _is_raises(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and _call_name(node) in _RAISES_CALLS


def _has_hint(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _ASSERTION_HINTS)


def _asserting_helpers(tree: ast.Module) -> frozenset[str]:
    """Names of same-file functions that assert, directly or via other helpers.

    Fixed point over the name-based call graph, the same resolution
    check_ai_readiness's test-assertion-density gate uses.
    """
    defs: dict[str, list[_FuncDef]] = collections.defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            defs[node.name].append(node)

    def direct(fn: _FuncDef) -> bool:
        for node in _own_nodes(fn.body):
            if isinstance(node, ast.Assert):
                return True
            if isinstance(node, ast.With | ast.AsyncWith) and any(
                _is_raises(i.context_expr) for i in node.items
            ):
                return True
            if isinstance(node, ast.Call) and _has_hint(_call_name(node)):
                return True
        return False

    def called(fn: _FuncDef) -> set[str]:
        return {_call_name(n) for n in _own_nodes(fn.body) if isinstance(n, ast.Call)}

    asserting = {name for name, fns in defs.items() if any(direct(f) for f in fns)}
    changed = True
    while changed:
        changed = False
        for name, fns in defs.items():
            if name not in asserting and any(called(f) & asserting for f in fns):
                asserting.add(name)
                changed = True
    return frozenset(asserting)


# ---------------------------------------------------------------------------
# Per-test flags
# ---------------------------------------------------------------------------


def _flags_for(
    fn: _FuncDef, names: frozenset[str], helpers: frozenset[str]
) -> tuple[str, ...]:
    nodes = list(_own_nodes(fn.body))
    asserts = [n for n in nodes if isinstance(n, ast.Assert)]
    value_asserts = [a for a in asserts if not _is_weak(a.test)]
    raises = [
        n
        for n in nodes
        if isinstance(n, ast.With | ast.AsyncWith)
        and any(_is_raises(i.context_expr) for i in n.items)
    ]
    calls = [n for n in nodes if isinstance(n, ast.Call)]
    assert_calls = [c for c in calls if _call_name(c).startswith("assert")]
    has_other = bool(raises or assert_calls)

    # Decorators are outside the body but can patch the whole test.
    decorator_calls = [
        n for dec in fn.decorator_list for n in ast.walk(dec) if isinstance(n, ast.Call)
    ]
    patching = any(
        _is_patching_call(c) for c in itertools.chain(calls, decorator_calls)
    )

    type_only = bool(asserts) and not value_asserts and not has_other

    mock_expectations_ok = (
        bool(asserts or assert_calls)
        and not raises
        and all(_is_mock_verify(a.test) for a in asserts)
        and all(_call_name(c) in _MOCK_VERIFY_METHODS for c in assert_calls)
    )
    mock_echo = patching and mock_expectations_ok

    value_shape_ok = bool(value_asserts) and not has_other
    inputs = _constructor_inputs(nodes)
    set_then_read = value_shape_ok and all(
        _echoes_input(a, inputs) for a in value_asserts
    )
    constant_mirror = value_shape_ok and all(
        _is_constant_mirror(a.test, names) for a in value_asserts
    )

    has_expectation = bool(asserts or raises or assert_calls) or any(
        _has_hint(_call_name(c)) or _call_name(c) in helpers for c in calls
    )
    no_assert = not has_expectation

    shape = {
        "type_only": type_only,
        "mock_echo": mock_echo,
        "set_then_read": set_then_read,
        "constant_mirror": constant_mirror,
    }
    trivial_size = _statement_count(fn.body) <= _TRIVIAL_MAX_STATEMENTS and any(
        shape[f] for f in _VALUE_SHAPE_FLAGS
    )
    flags = {**shape, "no_assert": no_assert, "trivial_size": trivial_size}
    return tuple(f for f in FLAGS if flags[f])


def audit_source(source: str, file: str = "<source>") -> list[TestAudit]:
    """Audit every test function in *source*; flagged or not.

    Raises ``SyntaxError`` for unparsable source.
    """
    tree = ast.parse(source, filename=file)
    names = _abicheck_names(tree)
    helpers = _asserting_helpers(tree)
    lines = source.splitlines()
    return [
        TestAudit(
            file,
            fn.lineno,
            qualname,
            _flags_for(fn, names, helpers),
            _is_exempt(fn, lines),
        )
        for qualname, fn in _test_functions(tree)
    ]


def _is_exempt(fn: _FuncDef, lines: list[str]) -> bool:
    """True when the marker sits on a decorator line or anywhere in the
    signature, which a formatter may wrap over several lines."""
    first = min([d.lineno for d in fn.decorator_list] + [fn.lineno])
    last = max(fn.lineno, fn.body[0].lineno - 1)
    return any(EXEMPT_MARKER in lines[i - 1] for i in range(first, last + 1))


def _display_path(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def audit_tree(tests_dir: pathlib.Path) -> tuple[list[TestAudit], int, int]:
    """Audit ``test_*.py`` under *tests_dir*.

    Returns ``(flagged records, total test functions, files scanned)``.
    """
    flagged: list[TestAudit] = []
    total = 0
    files = 0
    for path in sorted(tests_dir.rglob("test_*.py")):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            results = audit_source(source, _display_path(path))
        except SyntaxError:
            continue
        files += 1
        total += len(results)
        flagged.extend(r for r in results if r.flags)
    return flagged, total, files


def _summary(flagged: list[TestAudit], total: int, files: int, top: int) -> str:
    pct = 100.0 * len(flagged) / total if total else 0.0
    counts = collections.Counter(f for r in flagged for f in r.flags)
    per_file = collections.Counter(r.file for r in flagged)
    lines = [
        f"test functions audited: {total} in {files} files",
        f"flagged (any flag): {len(flagged)} of {total} ({pct:.1f}%)",
        "",
        "flag counts (one test can carry several):",
    ]
    lines += [f"  {flag:<16} {counts[flag]:>5}" for flag in FLAGS]
    lines += ["", f"top {top} files by flagged tests:"]
    lines += [f"  {n:>4}  {file}" for file, n in per_file.most_common(top)]
    return "\n".join(lines)


BASELINE_REL = "scripts/test_quality_baseline.json"


def gated_findings(records: Iterable[TestAudit]) -> dict[str, set[str]]:
    """``flag -> {test key}`` for every gated flag, exempt tests left out."""
    found: dict[str, set[str]] = {flag: set() for flag in GATED_FLAGS}
    for record in records:
        if record.exempt:
            continue
        for flag in record.flags:
            if flag in found:
                found[flag].add(record.key)
    return found


def parse_baseline(text: str) -> dict[str, set[str]]:
    """Read a baseline document; an unknown flag or a non-list is an error."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("baseline must be a JSON object of flag -> [test keys]")
    unknown = sorted(set(data) - set(GATED_FLAGS))
    if unknown:
        raise ValueError(f"baseline names unknown flags: {unknown}")
    baseline: dict[str, set[str]] = {flag: set() for flag in GATED_FLAGS}
    for flag, keys in data.items():
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ValueError(f"baseline[{flag!r}] must be a list of strings")
        baseline[flag] = set(keys)
    return baseline


def _base_baseline(base: str) -> dict[str, set[str]] | None:
    """The baseline as committed at *base*, or ``None`` if it had none."""
    shown = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{base}:{BASELINE_REL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        return None
    return parse_baseline(shown.stdout)


def evaluate(
    found: dict[str, set[str]],
    baseline: dict[str, set[str]],
    base: dict[str, set[str]] | None = None,
) -> list[str]:
    """Every gate violation as a printable line; empty when the gate passes.

    A finding missing from the baseline is new; a baselined key no longer
    found was fixed and must be removed, so the baseline only shrinks. With
    *base*, a key the base revision's baseline lacks is also rejected: a
    branch cannot baseline its own new finding.
    """
    errors: list[str] = []
    for flag in GATED_FLAGS:
        live, known = found.get(flag, set()), baseline.get(flag, set())
        errors += [f"{flag}: NEW: {key}" for key in sorted(live - known)]
        errors += [
            f"{flag}: fixed but still baselined (run --update): {key}"
            for key in sorted(known - live)
        ]
        if base is not None:
            errors += [
                f"{flag}: baselined on this branch, absent on the base: {key}"
                for key in sorted(known - base.get(flag, set()))
            ]
    return errors


def _check(flagged: list[TestAudit], args: argparse.Namespace) -> int:
    found = gated_findings(flagged)
    path = args.baseline or ROOT / BASELINE_REL
    if args.update:
        doc = {flag: sorted(found[flag]) for flag in GATED_FLAGS}
        path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        print(f"test quality: wrote {path} ({sum(map(len, doc.values()))} keys)")
        return 0
    try:
        baseline = parse_baseline(path.read_text(encoding="utf-8"))
        base_ref = args.base or os.environ.get("ARCHITECTURE_BASE") or None
        base = _base_baseline(base_ref) if base_ref else None
    except (OSError, ValueError) as exc:
        print(f"test quality: {exc}", file=sys.stderr)
        return 2
    errors = evaluate(found, baseline, base)
    for line in errors:
        print(line)
    if errors:
        print(
            f"test quality: {len(errors)} problem(s). A NEW finding is a test "
            "that only checks types/existence, echoes its own input, pins a "
            "constant's spelling, only verifies a mock was called, or asserts "
            "nothing. Make it check behaviour, delete it, or -- when the shape "
            f"is deliberate -- add `# {EXEMPT_MARKER} <reason>` to its def "
            "line. See docs/contribute/agent-guide/quality-gates.md "
            '"Test-quality ratchet".',
            file=sys.stderr,
        )
        return 1
    total = sum(map(len, baseline.values()))
    print(f"test quality: OK ({total} baselined finding(s), none new, none stale)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        type=pathlib.Path,
        help="Write the flagged tests to this path as a JSON list of "
        "{file, line, test, flags}.",
    )
    parser.add_argument(
        "--tests-dir",
        type=pathlib.Path,
        default=TESTS,
        help="Directory to screen (default: <repo>/tests).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=40,
        help="How many files to list in the summary (default: 40).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=f"Gate: fail on a finding absent from {BASELINE_REL} or a "
        "baselined key no longer found (exit 1; 2 on a bad baseline).",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="With --check: rewrite the baseline to the current findings.",
    )
    parser.add_argument(
        "--baseline",
        type=pathlib.Path,
        default=None,
        help=f"Baseline path (default: <repo>/{BASELINE_REL}).",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="Git revision whose baseline bounds this one "
        "(default: $ARCHITECTURE_BASE).",
    )
    args = parser.parse_args(argv)

    flagged, total, files = audit_tree(args.tests_dir)
    if args.check:
        return _check(flagged, args)
    print(_summary(flagged, total, files, args.top))

    if args.json:
        rows = [
            {
                "file": r.file,
                "line": r.line,
                "test": r.test,
                "flags": list(r.flags),
                "exempt": r.exempt,
            }
            for r in flagged
        ]
        args.json.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {len(rows)} flagged tests to {args.json}")
    # The report mode always exits 0; only --check gates.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
