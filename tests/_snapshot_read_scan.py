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

"""AST oracle: which ``AbiSnapshot`` attributes a detector's code reads.

Independent of ``compare/detector_inputs.py`` (the declaration it checks):
starting from a function or class, it collects ``<snap>.<attr>``,
``<snap>.declarations.<field>`` and ``getattr(<snap>, "<attr>")`` reads,
where ``<snap>`` is a parameter annotated ``AbiSnapshot``, named like a
snapshot (``old``/``new``/``snap``/``snapshot``/...), the detector's own two
positional parameters, or ``self._old``/``self._new``-style attributes of a
rule class. It follows calls it can resolve through the calling module's
globals into any ``abicheck`` function or class (a class: all its methods).

An over-approximation by design (any same-named parameter counts), and
blind to a snapshot passed through a container or a renamed local -- the
two known ways a read can escape it.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import sys
import textwrap
import types
from typing import Any

from abicheck.model.snapshot import AbiSnapshot

SNAPSHOT_ATTRS = frozenset(f.name for f in dataclasses.fields(AbiSnapshot)) | {
    n for n, v in vars(AbiSnapshot).items() if isinstance(v, property)
}
_SNAP_PARAM_NAMES = frozenset(
    {"old", "new", "snap", "snapshot", "old_snap", "new_snap", "subject"}
)
_SELF_SNAP_ATTRS = frozenset(
    {"_new", "_old", "new", "old", "_snap", "_snapshot", "snapshot"}
)


def _parse(obj: Any) -> ast.AST | None:
    try:
        src = inspect.getsource(obj)
    except (OSError, TypeError):
        return None
    body = ast.parse(textwrap.dedent(src)).body
    return body[0] if body else None


def _own_reads(fn: ast.FunctionDef | ast.AsyncFunctionDef, entry: bool):
    args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
    params = {
        a.arg
        for i, a in enumerate(args)
        if (a.annotation is not None and "AbiSnapshot" in ast.unparse(a.annotation))
        or a.arg in _SNAP_PARAM_NAMES
        or (entry and i < 2)
    }

    def is_snap(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in params
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr in _SELF_SNAP_ATTRS
        )

    reads: set[str] = set()
    calls: list[ast.expr] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute):
            if is_snap(node.value):
                reads.add(node.attr)
            elif (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "declarations"
                and is_snap(node.value.value)
            ):
                reads.add(f"declarations.{node.attr}")
        elif isinstance(node, ast.Call):
            calls.append(node.func)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and is_snap(node.args[0])
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                reads.add(node.args[1].value)
    return reads, calls


def _resolve(func: ast.expr, module_globals: dict[str, Any]) -> Any:
    if isinstance(func, ast.Name):
        return module_globals.get(func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        base = module_globals.get(func.value.id)
        if isinstance(base, types.ModuleType):
            return getattr(base, func.attr, None)
    return None


def snapshot_reads(root: Any) -> frozenset[str]:
    """Every ``AbiSnapshot`` attribute (or ``declarations.<field>``) the code
    reachable from *root* reads."""
    seen: set[int] = set()
    out: set[str] = set()
    stack: list[tuple[Any, bool]] = [(root, True)]
    while stack:
        obj, entry = stack.pop()
        if callable(obj) and not inspect.isclass(obj):
            obj = inspect.unwrap(obj)
        module = getattr(obj, "__module__", "") or ""
        if not module.startswith("abicheck") or id(obj) in seen:
            continue
        seen.add(id(obj))
        if inspect.isclass(obj):
            stack.extend(
                (v, False) for v in vars(obj).values() if inspect.isfunction(v)
            )
            continue
        if not inspect.isfunction(obj):
            continue
        tree = _parse(obj)
        if not isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        reads, calls = _own_reads(tree, entry)
        out |= reads
        module_globals = vars(sys.modules[module])
        for call in calls:
            target = _resolve(call, module_globals)
            if target is not None:
                stack.append((target, False))
    return frozenset(
        r for r in out if r in SNAPSHOT_ATTRS or r.startswith("declarations.")
    )
