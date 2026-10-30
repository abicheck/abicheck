#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Static dead-code gate: what is dead by reading the source alone.

``usecase_paths.py dead`` (weekly, coverage-driven, report-only) answers
"which functions does no use case run". It cannot see dead code *inside* a
function that does run, and it needs a recording. This gate is its static,
per-PR complement over ``abicheck/``:

* **unreachable** -- a statement after ``return``/``raise``/``continue``/
  ``break`` in the same block. Never has a reason to exist. Gating.
* **orphan-module** -- a package module no ``import``, relative import,
  ``importlib`` string or ``pyproject.toml`` entry point names. Gating.
* **unread-parameter** -- a parameter its function never reads. Ratcheted
  against ``scripts/static_dead_code_baseline.json``: a new one fails, and a
  baseline entry that no longer reproduces fails too (delete it), so the
  list only shrinks.

Left out of ``unread-parameter`` because a caller outside the function fixes
the signature: ``self``/``cls``, ``_``-prefixed names, ``*args``/``**kwargs``,
stub bodies (docstring, ``pass``, ``...``, ``raise NotImplementedError``),
methods of a class with a base (overrides, protocols, visitors), functions
with any decorator other than ``staticmethod``/``classmethod``/``property``/
``cache``-style ones (registration fixes the signature), functions that call
``locals()``/``vars()``, and a ``def`` line carrying ``# noqa: ARG``.

Usage::

    python scripts/check_static_dead_code.py            # gate
    python scripts/check_static_dead_code.py --list     # every finding
    python scripts/check_static_dead_code.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "abicheck"
BASELINE = ROOT / "scripts" / "static_dead_code_baseline.json"

_TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)
_NEUTRAL_DECORATORS = frozenset(
    {"staticmethod", "classmethod", "property", "cache", "lru_cache", "cached_property"}
)
_IMPORTER_DIRS = ("abicheck", "scripts", "tests", "action", "actions", "contrib")
_FROM_IMPORT = re.compile(
    r"^\s*from\s+(?P<mod>\.*[\w.]*)\s+import\s+(?P<names>\([^)]*\)|[^\n]+)", re.M
)
_PLAIN_IMPORT = re.compile(
    r"^\s*import\s+([\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*)", re.M
)
_QUOTED_MODULE = re.compile(rf"[\"'](\.?{PACKAGE}(?:\.\w+)+|\.\w+(?:\.\w+)*)[\"']")


@dataclass(frozen=True, order=True)
class Finding:
    rule: str
    path: str
    line: int
    name: str

    @property
    def key(self) -> str:
        # Line-free so unrelated edits above a finding do not churn the baseline.
        return f"{self.rule}:{self.path}:{self.name}"

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.name}"


def _unreachable(tree: ast.AST, rel: str) -> list[Finding]:
    out: list[Finding] = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody", "handlers"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for i, stmt in enumerate(block[:-1]):
                if isinstance(stmt, _TERMINATORS):
                    nxt = block[i + 1]
                    out.append(
                        Finding("unreachable", rel, nxt.lineno, type(nxt).__name__)
                    )
                    break
    return out


def _decorator_name(dec: ast.expr) -> str:
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return ""


def _is_stub(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body = body[1:]
    if not body:
        return True
    if len(body) != 1:
        return False
    stmt = body[0]
    if isinstance(stmt, ast.Pass):
        return True
    if (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    ):
        return True
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
        return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    return False


def _unread_parameters(tree: ast.AST, rel: str, lines: list[str]) -> list[Finding]:
    out: list[Finding] = []

    def visit(node: ast.AST, prefix: str, in_based_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", bool(child.bases))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                out.extend(_check_function(child, qual, rel, lines, in_based_class))
                visit(child, f"{qual}.", False)
            else:
                visit(child, prefix, in_based_class)

    visit(tree, "", False)
    return out


def _check_function(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    qual: str,
    rel: str,
    lines: list[str],
    in_based_class: bool,
) -> list[Finding]:
    if in_based_class or _is_stub(fn):
        return []
    if any(_decorator_name(d) not in _NEUTRAL_DECORATORS for d in fn.decorator_list):
        return []
    if "noqa: ARG" in lines[fn.lineno - 1]:
        return []
    args = fn.args
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    is_method = "." in qual and not any(
        _decorator_name(d) == "staticmethod" for d in fn.decorator_list
    )
    if is_method and params and (args.posonlyargs or args.args):
        params = params[1:]  # self / cls
    candidates = {p.arg: p for p in params if not p.arg.startswith("_")}
    if not candidates:
        return []
    read: set[str] = set()
    for stmt in fn.body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Name):
                read.add(n.id)
                if n.id in {"locals", "vars"}:
                    return []
    return [
        Finding("unread-parameter", rel, p.lineno, f"{qual}({name})")
        for name, p in candidates.items()
        if name not in read
    ]


def _module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _referenced_modules(root: Path) -> set[str]:
    """Every dotted module name an import or a module-shaped string names,
    across the whole checkout (a test- or script-only import still counts:
    deciding whether *that* caller is real is ``usecase_paths.py dead``'s job)."""
    # A regex over the text, not an AST walk: the import surface is lexically
    # regular, and walking every node of ~1,200 test modules cost ~45 s. A
    # spurious match (an import spelled inside a string) only keeps a module.
    names: set[str] = set()
    paths = [
        p
        for d in _IMPORTER_DIRS
        if (root / d).is_dir()
        for p in (root / d).rglob("*.py")
    ]
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        pkg = list(path.relative_to(root).with_suffix("").parts)[:-1]
        for m in _FROM_IMPORT.finditer(text):
            base = m.group("mod")
            level = len(base) - len(base.lstrip("."))
            if level:
                anchor = pkg[: len(pkg) - (level - 1)]
                base = ".".join(
                    [*anchor, *([base.lstrip(".")] if base.lstrip(".") else [])]
                )
            names.add(base)
            body = " ".join(
                line.split("#")[0] for line in m.group("names").splitlines()
            )
            for item in body.strip().strip("()").split(","):
                leaf = item.split(" as ")[0].strip()
                if leaf.isidentifier():
                    names.add(f"{base}.{leaf}")
        for m in _PLAIN_IMPORT.finditer(text):
            names.update(n.split(" as ")[0].strip() for n in m.group(1).split(","))
        for m in _QUOTED_MODULE.finditer(text):
            value = m.group(1)
            if value.startswith("."):  # importlib.import_module(".x", __package__)
                value = ".".join(pkg) + value
            names.add(value)
    return names


def _orphan_modules(root: Path, files: list[Path]) -> list[Finding]:
    referenced = _referenced_modules(root)
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    out = []
    for path in files:
        mod = _module_name(path, root)
        if path.name == "__main__.py" or mod == PACKAGE or mod in pyproject:
            continue
        if mod in referenced or any(r.startswith(mod + ".") for r in referenced):
            continue
        out.append(Finding("orphan-module", path.relative_to(root).as_posix(), 1, mod))
    return out


def collect(root: Path = ROOT) -> list[Finding]:
    files = sorted((root / PACKAGE).rglob("*.py"))
    findings: list[Finding] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        findings += _unreachable(tree, rel)
        findings += _unread_parameters(tree, rel, text.splitlines())
    findings += _orphan_modules(root, files)
    return sorted(findings)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--list", action="store_true", help="print every finding and exit 0"
    )
    ap.add_argument(
        "--write-baseline",
        action="store_true",
        help="freeze current unread-parameter findings",
    )
    args = ap.parse_args(argv)

    findings = collect(ROOT)
    if args.list:
        for f in findings:
            print(f)
        print(f"{len(findings)} findings")
        return 0
    ratcheted = sorted({f.key for f in findings if f.rule == "unread-parameter"})
    if args.write_baseline:
        BASELINE.write_text(
            json.dumps({"unread-parameter": ratcheted}, indent=1) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {BASELINE.relative_to(ROOT)} ({len(ratcheted)} entries)")
        return 0

    baseline = (
        set(json.loads(BASELINE.read_text(encoding="utf-8"))["unread-parameter"])
        if BASELINE.exists()
        else set()
    )
    errors = [str(f) for f in findings if f.rule != "unread-parameter"]
    errors += [
        str(f)
        for f in findings
        if f.rule == "unread-parameter" and f.key not in baseline
    ]
    stale = sorted(baseline - set(ratcheted))
    errors += [f"{key}: fixed -- delete it from {BASELINE.name}" for key in stale]
    for line in errors:
        print(line)
    print(
        f"static dead code: {len(errors)} problem(s); {len(baseline) - len(stale)} baselined unread parameter(s) left"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
