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

"""The scanner behind ``tests/test_module_cache_gate.py`` (design-hardening
plan, Phase 4).

Two findings, each keyed ``<path>::<name>`` so the allowlist is line-free:

* ``functools`` -- any use of ``functools.cache``/``lru_cache``/
  ``cached_property`` (decorator, call or import). Every memo goes through
  :mod:`abicheck.model.execution_cache`, which honours
  ``ABICHECK_REFERENCE_MODE`` and counts hits.
* ``module_state`` -- a module-level mutable container (a dict/list/set
  display or comprehension, or a call to a mutable container type) that some
  function in the same module mutates: item assignment or deletion, an
  augmented assignment or a mutating method call -- or any module-level
  name a function rebinds through ``global``.
  Process-global mutable state is exactly what a pool worker must not touch:
  workers may read only immutable inputs and request-scoped caches.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_MUTATORS = frozenset(
    {
        "append",
        "appendleft",
        "add",
        "clear",
        "discard",
        "extend",
        "extendleft",
        "insert",
        "move_to_end",
        "pop",
        "popitem",
        "popleft",
        "remove",
        "setdefault",
        "update",
        "__setitem__",
        "__delitem__",
    }
)
_MUTABLE_CTORS = frozenset(
    {
        "dict",
        "list",
        "set",
        "OrderedDict",
        "defaultdict",
        "deque",
        "Counter",
        "WeakKeyDictionary",
        "WeakValueDictionary",
        "WeakSet",
    }
)
_FUNCTOOLS_MEMOS = frozenset({"cache", "lru_cache", "cached_property"})


@dataclass(frozen=True, order=True)
class Finding:
    path: str  # repo-relative, POSIX
    name: str
    rule: str  # "functools" | "module_state"
    line: int = 0

    @property
    def key(self) -> str:
        return f"{self.path}::{self.name}"


def _callee(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_mutable_value(value: ast.expr) -> bool:
    if isinstance(
        value, ast.Dict | ast.List | ast.Set | ast.DictComp | ast.ListComp | ast.SetComp
    ):
        return True
    return isinstance(value, ast.Call) and _callee(value.func) in _MUTABLE_CTORS


def _module_containers(tree: ast.Module) -> dict[str, int]:
    out: dict[str, int] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None or not _is_mutable_value(value):
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                out[t.id] = node.lineno
    return out


def _local_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names *fn* binds locally (parameters, plain assignments), which shadow
    a module-level name of the same spelling."""
    args = fn.args
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    for extra in (args.vararg, args.kwarg):
        if extra is not None:
            names.add(extra.arg)
    declared_global: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Global):
            declared_global.update(node.names)
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.For | ast.With):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign | ast.For)
                else [i.optional_vars for i in node.items if i.optional_vars]
            )
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                        names.add(n.id)
    return names - declared_global


def _mutated_in(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, names: set[str]
) -> set[str]:
    hit: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Global):
            # Any rebinding of module state, container or not: a lazily
            # built singleton or a "probe already failed" flag is a cache.
            hit.update(node.names)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in names
            and node.func.attr in _MUTATORS
        ):
            hit.add(node.func.value.id)
        elif isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.Delete):
            targets = (
                node.targets
                if isinstance(node, ast.Assign | ast.Delete)
                else [node.target]
            )
            for t in targets:
                base = t
                while isinstance(base, ast.Subscript | ast.Attribute):
                    if isinstance(base, ast.Subscript) and isinstance(
                        base.value, ast.Name
                    ):
                        if base.value.id in names:
                            hit.add(base.value.id)
                        break
                    base = base.value  # type: ignore[assignment]
                if (
                    isinstance(node, ast.AugAssign)
                    and isinstance(t, ast.Name)
                    and t.id in names
                ):
                    hit.add(t.id)
    return hit


#: The one wrapper may use ``functools.lru_cache`` as a storage engine behind
#: its own reference-mode check and key normalization.
WRAPPER_MODULE = "abicheck/model/execution_cache.py"


def scan_source(source: str, path: str) -> list[Finding]:
    tree = ast.parse(source, filename=path)
    findings: list[Finding] = []
    for node in ast.walk(tree) if path != WRAPPER_MODULE else ():
        if isinstance(node, ast.ImportFrom) and node.module == "functools":
            for alias in node.names:
                if alias.name in _FUNCTOOLS_MEMOS:
                    findings.append(Finding(path, alias.name, "functools", node.lineno))
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in _FUNCTOOLS_MEMOS
            and isinstance(node.value, ast.Name)
            and node.value.id.lstrip("_") == "functools"
        ):
            findings.append(Finding(path, node.attr, "functools", node.lineno))
    containers = _module_containers(tree)
    mutated: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            visible = set(containers) - _local_names(node)
            mutated |= _mutated_in(node, visible)
    findings.extend(
        Finding(path, name, "module_state", containers.get(name, 0)) for name in mutated
    )
    return sorted(set(findings))


def scan_tree(root: Path, repo: Path) -> list[Finding]:
    out: list[Finding] = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(repo).as_posix()
        out.extend(scan_source(p.read_text(encoding="utf-8"), rel))
    return out
