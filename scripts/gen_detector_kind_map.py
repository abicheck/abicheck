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

"""Regenerate ``abicheck/policy/detector_kind_map.py``: which registered
detector can emit which ``ChangeKind`` (ADR-067 P1 addendum, per-rule status).

The configured-rule audit reads ``not_evaluated`` per rule: a zero-match rule
is ``unused`` only when a detector that could produce one of its change kinds
ran. Detectors do not declare their kinds, so the mapping is *derived* here
from the source, once, rather than hand-written:

* the detector list is the live :data:`abicheck.detector_registry.registry`
  (name plus the decorated function object);
* for each detector, the ``ChangeKind.<MEMBER>`` references in its function
  body are collected, then followed transitively through the calls and
  references it makes to first-party functions, resolved through the module's
  own imports (static, AST-based, no code is executed for the walk).

Kinds that no registered detector reaches are emitted by a pipeline step, a
workflow, or a bundle/build/source path that is not a detector, so they are
recorded as ``NON_DETECTOR_KINDS``. A rule scoped to one of them reads
``not_evaluated``, never ``unused``.

The result is an over-approximation of what each detector *can* emit when a
call goes through a helper the walk cannot resolve (a method on an object, a
kind passed as data). ``tests/test_detector_kind_map.py`` checks the committed
module for drift against a fresh derivation, checks it covers every
``ChangeKind`` and every registered detector, and checks it against the kinds
the detectors actually emit on the committed real-binary snapshot pairs.

Usage::

    python scripts/gen_detector_kind_map.py          # rewrite the module
    python scripts/gen_detector_kind_map.py --check  # exit 1 on drift

Then run ``ruff format abicheck/policy/detector_kind_map.py``; the drift check
compares parsed data, not text, so formatting never causes a false drift.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "abicheck" / "policy" / "detector_kind_map.py"
_PACKAGE_ROOT = ROOT / "abicheck"


@dataclass
class _Module:
    """The part of one first-party module the walk reads."""

    name: str
    functions: dict[str, ast.AST] = field(default_factory=dict)
    #: Methods of the module's top-level classes, by name (several classes may
    #: share one). Reached through an attribute call on a name the walk cannot
    #: resolve (``self.check()``, ``helper.emit()``): same module only.
    methods: dict[str, list[ast.AST]] = field(default_factory=dict)
    #: Local name -> (module, attribute). ``attribute`` is ``None`` for a plain
    #: ``import module`` binding, the imported name for a ``from`` import.
    imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    #: Local names that refer to the ``ChangeKind`` enum in this module.
    kind_aliases: set[str] = field(default_factory=set)


def _absolute(package: str, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    parts = package.split(".")
    base = parts[: len(parts) - (level - 1)]
    return ".".join([*base, module] if module else base)


class _Source:
    """Lazily parsed first-party modules, keyed by dotted name."""

    def __init__(self, package_root: Path) -> None:
        self._package_root = package_root
        self._modules: dict[str, _Module | None] = {}

    def module(self, dotted: str) -> _Module | None:
        if dotted in self._modules:
            return self._modules[dotted]
        self._modules[dotted] = self._load(dotted)
        return self._modules[dotted]

    def _load(self, dotted: str) -> _Module | None:
        if not dotted.startswith("abicheck"):
            return None
        rel = Path(*dotted.split("."))
        path = self._package_root.parent / rel.with_suffix(".py")
        is_package = False
        if not path.is_file():
            path = self._package_root.parent / rel / "__init__.py"
            is_package = True
        if not path.is_file():
            return None
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        package = dotted if is_package else dotted.rpartition(".")[0]
        module = _Module(name=dotted)
        # Module-level constants are deliberately not followed: a membership
        # table (``BREAKING_KINDS = frozenset({...})``) names kinds its readers
        # never emit, so following it would over-approximate every detector
        # that tests membership in it.
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module.functions[node.name] = node
            elif isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        module.methods.setdefault(member.name, []).append(member)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = _absolute(package, node.level, node.module)
                for alias in node.names:
                    local = alias.asname or alias.name
                    module.imports.setdefault(local, (base, alias.name))
                    if alias.name == "ChangeKind":
                        module.kind_aliases.add(local)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        module.imports.setdefault(alias.asname, (alias.name, None))
                    else:
                        top = alias.name.split(".")[0]
                        module.imports.setdefault(top, (top, None))
        return module


def _referenced(
    module: _Module, body: ast.AST
) -> tuple[set[str], set[tuple[str, str]]]:
    """``ChangeKind`` member names and first-party ``(module, name)`` targets
    referenced anywhere inside *body*, nested functions included."""
    kinds: set[str] = set()
    targets: set[tuple[str, str]] = set()
    for node in ast.walk(body):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in module.kind_aliases
        ):
            kinds.add(node.attr)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            bound = module.imports.get(node.value.id)
            if bound is not None:
                # ``mod.func`` on a module binding, or ``pkg.sub.func`` where
                # the binding named a submodule of ``pkg``.
                base, attr = bound
                target = base if attr is None else f"{base}.{attr}"
                targets.add((target, node.attr))
            elif node.value.id not in module.functions:
                # An attribute call on a local or ``self``: every same-module
                # method of that name is a candidate callee.
                targets.add((module.name, f".{node.attr}"))
        elif isinstance(node, ast.Name):
            if node.id in module.functions:
                targets.add((module.name, node.id))
            elif node.id in module.imports:
                base, attr = module.imports[node.id]
                if attr is not None:
                    targets.add((base, attr))
    return kinds, targets


def _closure(source: _Source, entry: tuple[str, str]) -> set[str]:
    """Every ``ChangeKind`` member reachable from one detector's function."""
    seen: set[tuple[str, str]] = set()
    stack = [entry]
    kinds: set[str] = set()
    while stack:
        dotted, func = stack.pop()
        if (dotted, func) in seen:
            continue
        seen.add((dotted, func))
        module = source.module(dotted)
        if module is None:
            continue
        if func.startswith("."):
            bodies = module.methods.get(func[1:], [])
        else:
            bodies = [module.functions[func]] if func in module.functions else []
        for body in bodies:
            found_kinds, targets = _referenced(module, body)
            kinds |= found_kinds
            stack.extend(targets)
    return kinds


def derive() -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """``(detector name -> kind values it can emit, kind values no detector emits)``.

    Reads the live registry, so it must run in a process that can import
    :mod:`abicheck`; the detector set and the kind names are never typed here.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from abicheck.detector_registry import registry
    from abicheck.model.change_catalog.kinds import ChangeKind

    registry.ensure_loaded()
    source = _Source(_PACKAGE_ROOT)
    producers: dict[str, frozenset[str]] = {}
    for entry in registry._detectors:  # noqa: SLF001 - the registry's own list
        fn = entry.fn
        names = _closure(source, (fn.__module__, fn.__name__))
        values = frozenset(
            ChangeKind[name].value for name in names if name in ChangeKind.__members__
        )
        producers[entry.name] = values
    covered: set[str] = set().union(*producers.values()) if producers else set()
    every = {member.value for member in ChangeKind}
    return dict(sorted(producers.items())), frozenset(every - covered)


def render(producers: dict[str, frozenset[str]], non_detector: frozenset[str]) -> str:
    lines = [
        "# Copyright 2026 Nikolay Petrov",
        "# SPDX-License-Identifier: Apache-2.0",
        "",
        '"""GENERATED by scripts/gen_detector_kind_map.py -- do not edit by hand.',
        "",
        "Which registered detector can emit which ``ChangeKind`` (ADR-067 P1",
        "addendum). Derived from the detector source once; the drift test",
        "``tests/test_detector_kind_map.py`` fails until this file is regenerated.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from collections.abc import Mapping",
        "from types import MappingProxyType",
        "",
        "#: Detector name -> the ``ChangeKind`` values it can emit.",
        "DETECTOR_KINDS: Mapping[str, frozenset[str]] = MappingProxyType(",
        "    {",
    ]
    for name, kinds in producers.items():
        body = ", ".join(f'"{k}"' for k in sorted(kinds))
        lines.append(f'        "{name}": frozenset({{{body}}}),')
    lines += [
        "    }",
        ")",
        "",
        "#: ``ChangeKind`` values no registered detector emits: a pipeline step,",
        "#: workflow or bundle/build/source path produces them.",
        "NON_DETECTOR_KINDS: frozenset[str] = frozenset(",
        "    {" + ", ".join(f'"{k}"' for k in sorted(non_detector)) + "}",
        ")",
        "",
    ]
    return "\n".join(lines)


def committed(path: Path = OUTPUT) -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """The data the committed module holds, read by importing it from *path*.

    Compared as data, not text, so a formatter's line wrapping never reads as
    drift.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_committed_detector_kind_map", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.DETECTOR_KINDS), frozenset(module.NON_DETECTOR_KINDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed module differs from a fresh derivation",
    )
    args = parser.parse_args(argv)
    producers, non_detector = derive()
    if args.check:
        if not OUTPUT.exists() or committed() != (producers, non_detector):
            print(
                f"{OUTPUT.relative_to(ROOT)} is stale: run "
                "python scripts/gen_detector_kind_map.py",
                file=sys.stderr,
            )
            return 1
        return 0
    OUTPUT.write_text(render(producers, non_detector), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
