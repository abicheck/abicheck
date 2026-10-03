#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Which unreached functions still have a production reference.

``usecase_paths.py dead`` builds on this: a recording says which functions
no use case ran; this module says which of those are still *referenced* by
production code, and which are referenced only by other unreferenced code.
It implements the rule of ``docs/contribute/plans/dead-code-and-single-owner.md``:

* **Production** is ``abicheck/``, ``scripts/``, ``action/``, ``actions/``,
  ``.github/`` and ``pyproject.toml``. A test-only caller does not make code
  live.
* A function is **dead** when every production reference to its name sits
  inside the body of a function that is itself dead -- a greatest fixpoint,
  so two unused helpers calling each other are both reported, and a helper
  only an unused function calls goes with it.
* An unreferenced function is **documented** when ``docs/use``,
  ``docs/reference`` or ``docs/learn`` names it (documented Python API is
  kept until a decision says otherwise), and **named by a decision** when
  only an ADR or a plan does. Both are listed apart from dead code, and
  both are roots: a helper only kept API calls is kept with it.

References are matched by name, not resolved: ``obj.foo()`` keeps every
function called ``foo`` alive. That errs towards *keeping* code, which is
the safe direction for a review list. Not counted as references: an
``import`` statement (a re-export is not a use; ``import f as g`` is, since
``g`` is then used under another name), ``__all__`` entries, docstrings and
comments. Counted: every identifier and attribute name, and identifier-like
words inside other string literals (``getattr`` dispatch, entry points such
as ``abicheck.cli:main``, lazy ``__getattr__`` shims).

Some functions are never called by name and are left out as
**unverifiable**: dunder methods, functions behind a registering decorator
(``@main.command(...)``, ``@registry.detector(...)``), and methods of a
class with a base defined outside the package (``ast.NodeVisitor.visit_*``,
``json.JSONEncoder.default``), which a framework calls. A ``getattr`` whose
name is computed (``getattr(self, f"_on_{kind}")``) is not seen either; the
report is a list to review, never a deletion list.
"""

from __future__ import annotations

import ast
import builtins
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE = "abicheck"
PRODUCTION_DIRS = ("abicheck", "scripts", "action", "actions", ".github")
PRODUCTION_FILES = ("pyproject.toml",)
TEXT_SUFFIXES = (".yml", ".yaml", ".sh", ".toml", ".cfg", ".ini", ".json")
USER_DOC_DIRS = ("docs/use", "docs/reference", "docs/learn")
DECISION_DOC_DIRS = ("docs/contribute/adr", "docs/contribute/plans")
TEST_DIR = "tests"
LOCALS = ".<locals>."

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Decorators that leave a function callable only by its own name.
_TRANSPARENT_DECORATORS = frozenset(
    {
        "property",
        "staticmethod",
        "classmethod",
        "cached_property",
        "cache",
        "lru_cache",
        "abstractmethod",
        "override",
        "overload",
        "contextmanager",
        "asynccontextmanager",
        "setter",
        "getter",
        "deleter",
        "wraps",
        "final",
        "singledispatchmethod",
    }
)
# Bases that do not call methods on their own: subclass methods of these are
# called by name like any other function.
_NEUTRAL_BASES = frozenset(dir(builtins)) | {
    "ABC",
    "Generic",
    "Protocol",
    "Enum",
    "IntEnum",
    "StrEnum",
    "Flag",
    "IntFlag",
    "NamedTuple",
    "TypedDict",
}


@dataclass(frozen=True)
class Site:
    """One reference to a name: where it is, and which function's body (a
    ``abicheck/x.py::qual`` id) holds it -- ``None`` at module level, in a
    non-package file, or in a ``scripts/`` file (scripts are entry points)."""

    path: str
    line: int
    enclosing: str | None


@dataclass
class FunctionInfo:
    fid: str
    name: str
    unverifiable: str | None = None  # the reason, when not checkable by name


@dataclass
class DeadReport:
    dead: list[str] = field(default_factory=list)
    documented: dict[str, list[str]] = field(default_factory=dict)
    decided: dict[str, list[str]] = field(default_factory=dict)
    live: dict[str, Site] = field(default_factory=dict)
    unverifiable: dict[str, str] = field(default_factory=dict)
    tests: dict[str, list[str]] = field(default_factory=dict)


# ── package function metadata ───────────────────────────────────────────────


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return ""


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Subscript):  # Generic[T], Mapping[str, int]
        node = node.value
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _package_class_names(root: Path) -> set[str]:
    names: set[str] = set()
    for path in sorted((root / PACKAGE).rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        names.update(n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    return names


def function_infos(
    source: str, rel: str, package_classes: set[str]
) -> list[FunctionInfo]:
    """Every ``def`` of one package file that is not nested in another
    function, with the reason it cannot be judged by name, if any. Nested
    functions go with their container."""
    out: list[FunctionInfo] = []

    def visit(node: ast.AST, prefix: str, external_base: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                ext = next(
                    (
                        b
                        for b in map(_base_name, child.bases)
                        if b and b not in package_classes and b not in _NEUTRAL_BASES
                    ),
                    None,
                )
                visit(child, f"{prefix}{child.name}.", ext)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                info = FunctionInfo(f"{rel}::{prefix}{child.name}", child.name)
                registering = [
                    d
                    for d in map(_decorator_name, child.decorator_list)
                    if d not in _TRANSPARENT_DECORATORS
                ]
                if child.name.startswith("__") and child.name.endswith("__"):
                    info.unverifiable = "dunder"
                elif registering:
                    info.unverifiable = f"decorator @{registering[0]}"
                elif prefix and external_base:
                    info.unverifiable = f"method of a {external_base} subclass"
                out.append(info)
            elif not isinstance(child, ast.Lambda):
                visit(child, prefix, external_base)

    visit(ast.parse(source), "", None)
    return out


# ── reference sites ─────────────────────────────────────────────────────────


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _skipped_subtrees(tree: ast.AST) -> set[int]:
    """Imports (unless renamed) and ``__all__`` values: not uses."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                if node.value is not None:
                    ids.update(id(n) for n in ast.walk(node.value))
    return ids


def python_names(source: str) -> list[tuple[str, int]]:
    """``(name, line)`` for every reference-shaped name in *source*."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    skipped = _skipped_subtrees(tree)
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.Name):
            out.append((node.id, node.lineno))
        elif isinstance(node, ast.Attribute):
            out.append((node.attr, node.end_lineno or node.lineno))
        elif isinstance(node, ast.alias) and node.asname and node.asname != node.name:
            out.append((node.name.rsplit(".", 1)[-1], node.lineno))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            out += [(w, node.lineno) for w in _WORD.findall(node.value)]
    return out


def _text_names(text: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out += [(w, lineno) for w in _WORD.findall(line)]
    return out


def _production_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for d in PRODUCTION_DIRS:
        base = root / d
        if base.is_dir():
            files += [
                p
                for p in sorted(base.rglob("*"))
                if p.is_file()
                and (p.suffix == ".py" or p.suffix in TEXT_SUFFIXES)
                and "__pycache__" not in p.parts
            ]
    files += [root / f for f in PRODUCTION_FILES if (root / f).is_file()]
    return files


def reference_sites(root: Path, names: set[str]) -> dict[str, list[Site]]:
    """Every production reference to one of *names*, with the package
    function whose body holds it."""
    from usecase_paths import function_spans, line_owner_map

    sites: dict[str, list[Site]] = defaultdict(list)
    for path in _production_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        owner: dict[int, str] = {}
        if path.suffix == ".py":
            try:
                found = python_names(text)
            except SyntaxError:
                found = _text_names(text)
            else:
                if rel.startswith(f"{PACKAGE}/"):
                    owner = line_owner_map(function_spans(text))
        else:
            found = _text_names(text)
        for name, line in found:
            if name in names:
                qual = owner.get(line)
                sites[name].append(Site(rel, line, f"{rel}::{qual}" if qual else None))
    return sites


def _outer(fid: str) -> str:
    """``x.py::f.<locals>.g`` -> ``x.py::f``: a nested body is its
    container's body."""
    return fid.split(LOCALS, 1)[0]


def dead_fixpoint(
    candidates: set[str], name_of: dict[str, str], sites: dict[str, list[Site]]
) -> tuple[set[str], dict[str, Site]]:
    """The greatest set of *candidates* every reference to which lies inside
    a member of the set. Returns it and, for each candidate it excludes, one
    reference that keeps it live."""
    dead = set(candidates)
    live: dict[str, Site] = {}
    changed = True
    while changed:
        changed = False
        for fid in sorted(dead):
            for site in sites.get(name_of[fid], ()):
                holder = _outer(site.enclosing) if site.enclosing else None
                if holder is not None and holder in dead:
                    continue  # inside a dead body, its own included
                dead.discard(fid)
                live[fid] = site
                changed = True
                break
    return dead, live


# ── documentation and tests ─────────────────────────────────────────────────


def _mentions(
    root: Path, dirs: tuple[str, ...], names: set[str], suffixes: tuple[str, ...]
) -> dict[str, list[str]]:
    hits: dict[str, set[str]] = defaultdict(set)
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            try:
                words = set(_WORD.findall(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                continue
            rel = path.relative_to(root).as_posix()
            for name in words & names:
                hits[name].add(rel)
    return {n: sorted(p) for n, p in hits.items()}


def dead_report(root: Path, unreached: set[str]) -> DeadReport:
    """Classify the *unreached* function ids of a recording."""
    package_classes = _package_class_names(root)
    infos: dict[str, FunctionInfo] = {}
    for path in sorted((root / PACKAGE).rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        try:
            for info in function_infos(
                path.read_text(encoding="utf-8"), rel, package_classes
            ):
                infos[info.fid] = info
        except (SyntaxError, UnicodeDecodeError):
            continue
    report = DeadReport()
    candidates: set[str] = set()
    for fid in unreached:
        info = infos.get(fid)
        if info is None:  # nested: judged with its container
            continue
        if info.unverifiable:
            report.unverifiable[fid] = info.unverifiable
        else:
            candidates.add(fid)
    name_of = {fid: infos[fid].name for fid in candidates}
    sites = reference_sites(root, set(name_of.values()))
    unreferenced, _ = dead_fixpoint(candidates, name_of, sites)
    names = {name_of[f] for f in unreferenced}
    user_docs = _mentions(root, USER_DOC_DIRS, names, (".md",))
    decisions = _mentions(root, DECISION_DOC_DIRS, names, (".md",))
    for fid in unreferenced:
        name = name_of[fid]
        if name in user_docs:
            report.documented[fid] = user_docs[name]
        elif name in decisions:
            report.decided[fid] = decisions[name]
    # Kept API is a root like any production caller: what only it calls is
    # kept with it, and is not dead.
    kept = set(report.documented) | set(report.decided)
    dead, report.live = dead_fixpoint(candidates - kept, name_of, sites)
    report.dead = sorted(dead)
    tests = _mentions(root, (TEST_DIR,), {name_of[f] for f in dead | kept}, (".py",))
    for fid in sorted(dead | kept):
        if name_of[fid] in tests:
            report.tests[fid] = tests[name_of[fid]]
    return report


def render_markdown(report: DeadReport, *, limit: int | None = None) -> str:
    def lines_for(fids: list[str], extra: dict[str, list[str]] | None) -> list[str]:
        out = []
        for fid in fids[:limit]:
            note = ""
            if extra and fid in extra:
                note = " -- named in " + ", ".join(f"`{p}`" for p in extra[fid][:3])
            elif fid in report.tests:
                note = f" -- test-only ({len(report.tests[fid])} test files)"
            out.append(f"- `{fid}`{note}")
        if limit is not None and len(fids) > limit:
            out.append(f"- ... and {len(fids) - limit} more")
        return out

    by_module: dict[str, int] = defaultdict(int)
    for fid in report.dead:
        by_module[fid.split("::", 1)[0]] += 1
    parts = [
        "# Unreached code with no production reference\n",
        "| class | functions |",
        "|---|---|",
        f"| dead (no production reference, undocumented) | {len(report.dead)} |",
        f"| documented in docs/use, docs/reference, docs/learn | {len(report.documented)} |",
        f"| named only by an ADR or plan | {len(report.decided)} |",
        f"| still referenced by production code | {len(report.live)} |",
        f"| not checkable by name | {len(report.unverifiable)} |",
        "",
        "## Dead, by module\n",
    ]
    parts += [
        f"- `{m}` ({n})"
        for m, n in sorted(by_module.items(), key=lambda t: (-t[1], t[0]))
    ]
    parts += ["", "## Dead\n", *lines_for(report.dead, None)]
    parts += [
        "",
        "## Documented\n",
        *lines_for(sorted(report.documented), report.documented),
    ]
    parts += [
        "",
        "## Named by a decision\n",
        *lines_for(sorted(report.decided), report.decided),
    ]
    return "\n".join(parts) + "\n"
