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

"""Static gate for performance anti-patterns inside loops (``abicheck/``).

Each rule names a shape that turns linear work quadratic, or repeats fixed
work per item, when it sits inside a loop or comprehension:

* ``list-membership-in-loop`` -- ``x in seq`` / ``seq.index(x)`` /
  ``seq.count(x)`` where ``seq`` was bound in the same function to a list
  (literal, comprehension, ``list(...)``, ``sorted(...)``): an O(len) scan
  per iteration. Use a ``set``/``dict``.
* ``regex-compile-in-loop`` -- ``re.compile(...)`` per iteration. Hoist it.
* ``parse-or-copy-in-loop`` -- ``json.loads`` / ``copy.deepcopy`` per
  iteration.
* ``quadratic-accumulation`` -- ``acc = acc + [...]``, ``acc = [*acc, ...]``,
  ``acc = {**acc, ...}`` in a loop: each step copies everything so far.
  Use ``append``/``extend``/``update``.
* ``subprocess-in-loop`` -- ``subprocess.run``/``Popen``/``check_output``/
  ``check_call``/``call`` per iteration: sequential process spawns (batch
  them, as the demangler does).
* ``sort-in-loop`` -- ``sorted(x)`` / ``x.sort()`` inside a ``for``/``while``
  body, where ``x`` is a plain name the loop never rebinds, mutates or
  iterates as its target: the same loop-invariant collection re-sorted every
  iteration. Sort it once before the loop. (Sorting a small per-item value
  for deterministic output -- ``", ".join(sorted(item.names))`` -- is not
  this shape and is not flagged.)
* ``str-concat-in-loop`` -- ``s += ...`` on a name the function bound to a
  string: each step copies the whole string so far. Collect parts and
  ``"".join`` them.

``parse-or-copy-in-loop`` also covers ``copy.copy`` and
``dataclasses.replace`` -- but only of a loop-invariant name (the same object
copied every iteration), since copying each item once is linear.

A comprehension counts as a loop for everything evaluated per element; its
first iterable is evaluated once and does not.

Existing sites are recorded per (file, function, rule) **count** in
``scripts/perf_antipatterns_baseline.json`` -- counts, not line numbers, so
an unrelated edit that moves code does not churn the file. A function whose
count rises above its baseline is an error; a baseline entry above the real
count is a warning to shrink it (the debt went down). Re-record with
``python scripts/perf_antipatterns.py --write-baseline``. Registered in
``check_ai_readiness.py`` as ``perf-antipatterns``.

A rule is a heuristic, not proof: a list that stays tiny is harmless. When
a new site is genuinely fine, record it in the baseline in the same PR and
say why.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "abicheck"
BASELINE_FILE = ROOT / "scripts" / "perf_antipatterns_baseline.json"
CHECK_NAME = "perf-antipatterns"

_LIST_BUILDERS = {"list", "sorted"}
_SUBPROCESS_CALLS = {"run", "Popen", "check_output", "check_call", "call"}


class Findings(Protocol):
    def err(self, check: str, msg: str) -> None: ...
    def warn(self, check: str, msg: str) -> None: ...


@dataclass(frozen=True)
class Site:
    path: str
    function: str
    rule: str
    line: int

    @property
    def key(self) -> str:
        return f"{self.path}::{self.function}::{self.rule}"


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _is_list_value(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.ListComp)):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _LIST_BUILDERS
    )


def _list_names(func: ast.AST) -> set[str]:
    """Names bound to a list somewhere in *func* and never to anything else.

    Deliberately conservative: a name also bound to a non-list (a set, a
    parameter, a loop target) is dropped, so the rule fires only where the
    container is a list on every path the function shows.
    """
    listy: set[str] = set()
    other: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    (listy if _is_list_value(node.value) else other).add(t.id)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            (listy if _is_list_value(node.value) else other).add(node.target.id)
        elif isinstance(node, (ast.For, ast.comprehension)):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    other.add(n.id)
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and not isinstance(node.value, (ast.List, ast.ListComp))
        ):
            other.add(node.target.id)
    if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = func.args
        for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
            if arg is not None:
                other.add(arg.arg)
    return listy - other


def _is_str_value(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant) and isinstance(node.value, str)
    ) or isinstance(node, ast.JoinedStr)


def _str_names(func: ast.AST) -> set[str]:
    """Names bound in *func* to a string literal/f-string and to nothing
    else -- the same conservative rule as :func:`_list_names`."""
    strs: set[str] = set()
    other: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    (strs if _is_str_value(node.value) else other).add(t.id)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            (strs if _is_str_value(node.value) else other).add(node.target.id)
        elif isinstance(node, (ast.For, ast.comprehension)):
            other.update(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))
    if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = func.args
        other.update(
            arg.arg
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]
            if arg is not None
        )
    return strs - other


def _grows_itself(node: ast.Assign) -> bool:
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return False
    target, v = node.targets[0].id, node.value
    return bool(
        (
            isinstance(v, ast.BinOp)
            and isinstance(v.op, ast.Add)
            and isinstance(v.left, ast.Name)
            and v.left.id == target
            and isinstance(v.right, (ast.List, ast.ListComp, ast.Tuple))
        )
        or (
            isinstance(v, ast.List)
            and any(
                isinstance(e, ast.Starred)
                and isinstance(e.value, ast.Name)
                and e.value.id == target
                for e in v.elts
            )
        )
        or (
            isinstance(v, ast.Dict)
            and any(
                k is None and isinstance(val, ast.Name) and val.id == target
                for k, val in zip(v.keys, v.values, strict=True)
            )
        )
    )


_MUTATORS = frozenset(
    {
        "append",
        "add",
        "extend",
        "update",
        "insert",
        "remove",
        "discard",
        "pop",
        "clear",
        "setdefault",
    }
)


def _varying_in(loop: ast.For | ast.AsyncFor | ast.While) -> set[str]:
    """Names whose value can differ between iterations of *loop*: assigned,
    augmented, used as a loop/comprehension target, or mutated through a
    method in its body (plus the loop's own target)."""
    out: set[str] = set()
    if not isinstance(loop, ast.While):
        out.update(n.id for n in ast.walk(loop.target) if isinstance(n, ast.Name))
    for stmt in loop.body:
        for node in ast.walk(stmt):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for t in targets:
                    out.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                out.update(
                    n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)
                )
            elif isinstance(node, ast.NamedExpr):
                out.add(node.target.id)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _MUTATORS
                and isinstance(node.func.value, ast.Name)
            ):
                out.add(node.func.value.id)
    return out


def _rebound_in(body: list[ast.stmt]) -> set[str]:
    """Names a statement in *body* binds other than by growing itself."""
    out: set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Assign) and not _grows_itself(node):
                out.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                out.add(node.target.id)
    return out


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.scope: list[str] = []
        self.list_names: list[set[str]] = [set()]
        self.str_names: list[set[str]] = [set()]
        self.loop_depth = 0
        # Per enclosing loop: names some *other* statement in the loop body
        # rebinds. ``key = key + (x,)`` after ``key = (...)`` in the same
        # body builds a fresh value each iteration; it accumulates nothing.
        self.rebound: list[set[str]] = []
        # Per enclosing *statement* loop: names that vary between iterations.
        # ``None`` marks a comprehension, where the sort rule does not apply.
        self.varying: list[set[str] | None] = []
        # Inside a ``raise``: an error path runs at most once, so nothing in
        # it can repeat per iteration (``sorted(ALLOWED_KEYS)`` in a message).
        self.raise_depth = 0
        self.sites: list[Site] = []

    # -- scopes ---------------------------------------------------------
    def _enter_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.list_names.append(_list_names(node))
        self.str_names.append(_str_names(node))
        saved, self.loop_depth = (
            self.loop_depth,
            0,
        )  # a nested def runs when called, not per iteration
        self.generic_visit(node)
        self.loop_depth = saved
        self.list_names.pop()
        self.str_names.pop()
        self.scope.pop()

    visit_FunctionDef = _enter_function
    visit_AsyncFunctionDef = _enter_function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    # -- loops ----------------------------------------------------------
    def _loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        if isinstance(node, ast.While):
            self.visit(node.test)
        else:
            self.visit(node.iter)  # evaluated once
            self.visit(node.target)
        self.loop_depth += 1
        self.rebound.append(_rebound_in(node.body))
        self.varying.append(_varying_in(node))
        for stmt in [*node.body, *node.orelse]:
            self.visit(stmt)
        self.varying.pop()
        self.rebound.pop()
        self.loop_depth -= 1

    visit_For = _loop
    visit_AsyncFor = _loop
    visit_While = _loop

    def _comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp
    ) -> None:
        first, *rest = node.generators
        self.visit(first.iter)  # the outermost iterable is evaluated once
        self.loop_depth += 1
        self.varying.append(None)
        self.visit(first.target)
        for cond in first.ifs:
            self.visit(cond)
        for gen in rest:
            self.visit(gen)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.varying.pop()
        self.loop_depth -= 1

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_GeneratorExp = _comprehension
    visit_DictComp = _comprehension

    # -- rules ----------------------------------------------------------
    def visit_Raise(self, node: ast.Raise) -> None:
        self.raise_depth += 1
        self.generic_visit(node)
        self.raise_depth -= 1

    def _hit(self, node: ast.AST, rule: str) -> None:
        if self.raise_depth:
            return
        self.sites.append(
            Site(
                self.path,
                ".".join(self.scope) or "<module>",
                rule,
                getattr(node, "lineno", 0),
            )
        )

    def visit_Compare(self, node: ast.Compare) -> None:
        if self.loop_depth:
            for op, right in zip(node.ops, node.comparators, strict=True):
                if (
                    isinstance(op, (ast.In, ast.NotIn))
                    and isinstance(right, ast.Name)
                    and right.id in self.list_names[-1]
                ):
                    self._hit(node, "list-membership-in-loop")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.loop_depth:
            name = _dotted(node.func) or ""
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"index", "count"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in self.list_names[-1]
            ):
                self._hit(node, "list-membership-in-loop")
            elif name == "re.compile":
                self._hit(node, "regex-compile-in-loop")
            elif name in {"json.loads", "copy.deepcopy", "deepcopy"} or (
                name in {"copy.copy", "dataclasses.replace"}
                and self._loop_invariant_first_arg(node)
            ):
                self._hit(node, "parse-or-copy-in-loop")
            elif (
                name.startswith("subprocess.")
                and name.split(".", 1)[1] in _SUBPROCESS_CALLS
            ):
                self._hit(node, "subprocess-in-loop")
            elif self._sorts_loop_invariant(node, name):
                self._hit(node, "sort-in-loop")
        self.generic_visit(node)

    def _loop_invariant_first_arg(self, node: ast.Call) -> bool:
        varying = self.varying[-1] if self.varying else None
        return (
            varying is not None
            and bool(node.args)
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id not in varying
        )

    def _sorts_loop_invariant(self, node: ast.Call, name: str) -> bool:
        varying = self.varying[-1] if self.varying else None
        if varying is None:
            return False
        if name == "sorted" and node.args and isinstance(node.args[0], ast.Name):
            target = node.args[0].id
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "sort"
            and not node.args
            and isinstance(node.func.value, ast.Name)
        ):
            target = node.func.value.id
        else:
            return False
        return target not in varying

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if (
            self.loop_depth
            and isinstance(node.op, ast.Add)
            and isinstance(node.target, ast.Name)
            and node.target.id in self.str_names[-1]
        ):
            self._hit(node, "str-concat-in-loop")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self.loop_depth and _grows_itself(node):
            target = node.targets[0].id  # type: ignore[attr-defined]
            if not (self.rebound and target in self.rebound[-1]):
                self._hit(node, "quadratic-accumulation")
        self.generic_visit(node)


def scan_source(source: str, path: str) -> list[Site]:
    visitor = _Visitor(path)
    visitor.visit(ast.parse(source, filename=path))
    return visitor.sites


def scan_tree(root: Path = PACKAGE) -> Iterator[Site]:
    for file in sorted(root.rglob("*.py")):
        rel = file.relative_to(ROOT).as_posix()
        yield from scan_source(file.read_text(encoding="utf-8"), rel)


def site_counts(sites: list[Site]) -> dict[str, int]:
    return dict(sorted(Counter(s.key for s in sites).items()))


def load_baseline() -> dict[str, int]:
    if not BASELINE_FILE.exists():
        return {}
    return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))["sites"]


def compare_to_baseline(
    sites: list[Site], baseline: dict[str, int]
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings): sites above baseline, baseline entries above reality."""
    counts = site_counts(sites)
    by_key: dict[str, list[Site]] = {}
    for s in sites:
        by_key.setdefault(s.key, []).append(s)
    errors = []
    for key, n in counts.items():
        allowed = baseline.get(key, 0)
        if n > allowed:
            lines = ", ".join(str(s.line) for s in by_key[key])
            path, func, rule = key.split("::")
            errors.append(
                f"{path} ({func}): {n} {rule} site(s), baseline {allowed} -- lines {lines}"
            )
    warnings = [
        f"{key}: baseline {allowed}, now {counts.get(key, 0)} -- shrink the baseline (--write-baseline)"
        for key, allowed in baseline.items()
        if counts.get(key, 0) < allowed
    ]
    return errors, warnings


def check_perf_antipatterns(f: Findings) -> None:
    errors, warnings = compare_to_baseline(list(scan_tree()), load_baseline())
    for msg in errors:
        f.err(CHECK_NAME, msg)
    for msg in warnings:
        f.warn(CHECK_NAME, msg)


def write_baseline() -> int:
    counts = site_counts(list(scan_tree()))
    payload = {
        "_comment": "Generated by scripts/perf_antipatterns.py --write-baseline. Per (file::function::rule) site counts; a new or grown entry needs its reason in the PR.",
        "sites": counts,
    }
    BASELINE_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return sum(counts.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="re-record the baseline from the current tree",
    )
    parser.add_argument(
        "--all", action="store_true", help="list every site, baseline or not"
    )
    args = parser.parse_args(argv)
    if args.write_baseline:
        print(
            f"recorded {write_baseline()} site(s) in {BASELINE_FILE.relative_to(ROOT)}"
        )
        return 0
    sites = list(scan_tree())
    if args.all:
        for s in sites:
            print(f"{s.path}:{s.line}: {s.rule} in {s.function}")
    errors, warnings = compare_to_baseline(sites, load_baseline())
    for msg in warnings:
        print(f"WARN  {msg}")
    for msg in errors:
        print(f"ERROR {msg}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
