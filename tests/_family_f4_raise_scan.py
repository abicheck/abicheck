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

"""Which breaking findings a registered name heuristic can reach (H4,
design-hardening Phase 5).

For every function in the H4-scanned modules that consults a registered
handle (``.matches``/``.apply``/``.confirmed``), follow its calls -- same
module and ``from .x import y`` imports across the scanned modules -- and
collect every ``ChangeKind.<NAME>`` in a BREAKING or API_BREAK partition the
reachable code references. :func:`raise_violations` then demands each such
kind be

* declared in ``lowers_from`` by a lowering handle consulted at the root
  (the kind the name *demotes*, emitted when the name does not match), or
* declared in ``raises`` by a severity-raising handle whose ``.confirmed``
  is called somewhere in the same reach set.

A name that can lead to a breaking finding through any other path is a
violation. Static, intra-repo call resolution only: a call through an
attribute (``obj.method()``) or a function passed as a value is not
followed, so the scan under-approximates reach rather than inventing it.
"""

from __future__ import annotations

import ast
import importlib
from collections.abc import Iterator
from dataclasses import dataclass, field

from _family_f4_inventory import _module_name, scanned_files

from abicheck.change_registry import API_BREAK_KINDS, BREAKING_KINDS
from abicheck.model.name_heuristics import NameHeuristic, SeverityRaisingNameHeuristic

_RAISING_KINDS = frozenset(k.name for k in BREAKING_KINDS | API_BREAK_KINDS)
_HANDLE_CALLS = frozenset({"matches", "apply", "confirmed"})

FuncKey = tuple[str, str]


@dataclass
class _Func:
    lowering: set[str] = field(default_factory=set)
    raising: set[str] = field(default_factory=set)
    kinds: set[str] = field(default_factory=set)
    calls: set[FuncKey] = field(default_factory=set)


def _imports(mod: str, is_pkg: bool, tree: ast.Module) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        parts = mod.split(".")
        keep = len(parts) - node.level + (1 if is_pkg else 0)
        base = ".".join(parts[:keep])
        src = f"{base}.{node.module}" if node.module else base
        for alias in node.names:
            out[alias.asname or alias.name] = (src, alias.name)
    return out


def _functions(tree: ast.Module) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    yield item


def _resolve(mod: str, name: str, imports: dict[str, tuple[str, str]]) -> object:
    src, attr = imports.get(name, (mod, name))
    try:
        return getattr(importlib.import_module(src), attr, None)
    except ImportError:
        return None


def function_index() -> dict[FuncKey, _Func]:
    index: dict[FuncKey, _Func] = {}
    for path in scanned_files():
        mod = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = _imports(mod, path.name == "__init__.py", tree)
        for fn in _functions(tree):
            info = _Func()
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    if node.value.id == "ChangeKind" and node.attr in _RAISING_KINDS:
                        info.kinds.add(node.attr)
                    elif node.attr in _HANDLE_CALLS:
                        h = _resolve(mod, node.value.id, imports)
                        if isinstance(h, SeverityRaisingNameHeuristic):
                            if node.attr == "confirmed":
                                info.raising.add(h.id)
                        elif isinstance(h, NameHeuristic):
                            info.lowering.add(h.id)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    info.calls.add(imports.get(node.func.id, (mod, node.func.id)))
            index[(mod, fn.name)] = info
    # A pure predicate (it emits no kind) that calls ``.confirmed`` gates its
    # caller's emission: lift its raising handles one call level up, to a
    # fixpoint, so ``if _is_tag(...)`` counts like an inline ``.confirmed``.
    changed = True
    while changed:
        changed = False
        for info in index.values():
            for callee in info.calls:
                c = index.get(callee)
                if c is not None and not c.kinds and not c.raising <= info.raising:
                    info.raising |= c.raising
                    changed = True
    return index


def reach(index: dict[FuncKey, _Func], root: FuncKey) -> set[FuncKey]:
    seen: set[FuncKey] = set()
    stack = [root]
    while stack:
        key = stack.pop()
        if key in seen or key not in index:
            continue
        seen.add(key)
        stack.extend(index[key].calls)
    return seen


def raise_violations(registry: dict[str, object]) -> list[str]:
    index = function_index()
    out: list[str] = []
    for root, info in sorted(index.items()):
        if not info.lowering:
            continue
        reached = reach(index, root)
        allowed = {
            k
            for hid in info.lowering
            for k in getattr(registry[hid], "lowers_from", ())
        }
        allowed |= {
            k
            for key in reached
            for hid in index[key].raising
            for k in getattr(registry[hid], "raises", ())
        }
        for key in sorted(reached):
            for kind in sorted(index[key].kinds - allowed):
                out.append(
                    f"{root[0]}.{root[1]} consults {sorted(info.lowering)} and "
                    f"reaches {key[0]}.{key[1]} emitting {kind}"
                )
    return out


def raising_declarations_reached() -> dict[str, set[str]]:
    """For each severity-raising heuristic, the kinds actually referenced in
    code reachable from a ``.confirmed`` call on it."""
    index = function_index()
    out: dict[str, set[str]] = {}
    for root, info in index.items():
        for hid in info.raising:
            kinds = out.setdefault(hid, set())
            for key in reach(index, root):
                kinds |= index[key].kinds
    return out
