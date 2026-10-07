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

**Parameters.** :func:`dead_parameters` asks the same question one level
down, independently of any recording: which parameters with a default does
no production call pass? Every production call of such a function runs the
default, so the parameter is dead or test-only -- typically what is left
when the last caller that needed it is deleted. Calls are matched by name
as functions are, and the pass errs the same way: ``**kw`` unpacking passes
every parameter, ``*args`` every positional slot, a method's positional
arguments are counted from ``self`` as well as after it, and a function
whose name has any production use that is not a call (``callback=f``,
``partial(f, ...)``, ``getattr(m, "f")``, a mention in a workflow) is
reported as not checkable rather than judged. ``import f as g`` is followed
within its file, a word in a prose string (a log message) is not a use, and
calls inside a dead function do not count.
"""

from __future__ import annotations

import ast
import builtins
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

PACKAGE = "abicheck"
PRODUCTION_DIRS = ("abicheck", "scripts", "action", "actions", ".github")
PRODUCTION_FILES = ("pyproject.toml",)
# Files under a production directory that only *list* names and are not
# uses: the usage ratchet's own baseline and exceptions (a baselined key
# would otherwise keep the very function it records alive).
NOT_REFERENCES = (
    "scripts/usage_ratchet_baseline.json",
    "scripts/usage_exceptions.yaml",
)
TEXT_SUFFIXES = (".yml", ".yaml", ".sh", ".toml", ".cfg", ".ini", ".json")
USER_DOC_DIRS = ("docs/use", "docs/reference", "docs/learn")
DECISION_DOC_DIRS = ("docs/contribute/adr", "docs/contribute/plans")
TEST_DIR = "tests"
LOCALS = ".<locals>."

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A string that may name a function for dynamic lookup: an identifier, a
# dotted path, or an entry point (``abicheck.cli:main``).
_NAME_STRING = re.compile(r"[A-Za-z_][\w.]*(?::[A-Za-z_][\w.]*)?")

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
    # How the name is used, for the parameter pass (the function pass counts
    # every kind): ``call`` (the callee of a call, whose arguments *call*
    # records), ``alias`` (``import f as g``; uses of ``g`` in the same file
    # are reported as uses of ``f``), ``prose`` (a word inside a string that
    # is not itself a name, such as a log message) or ``use`` (anything
    # else: ``callback=f``, ``getattr(m, "f")``, a mention in a workflow).
    kind: str = "use"
    call: CallShape | None = None


class Ref(NamedTuple):
    name: str
    line: int
    kind: str  # as Site.kind
    call: ast.Call | None = None
    asname: str | None = None


@dataclass(frozen=True)
class CallShape:
    positional: int
    keywords: frozenset[str]
    star: bool  # ``f(*args)``: any positional slot may be filled
    double_star: bool  # ``f(**kw)``: any parameter may be filled

    @classmethod
    def of(cls, call: ast.Call) -> CallShape:
        return cls(
            positional=sum(not isinstance(a, ast.Starred) for a in call.args),
            keywords=frozenset(k.arg for k in call.keywords if k.arg),
            star=any(isinstance(a, ast.Starred) for a in call.args),
            double_star=any(k.arg is None for k in call.keywords),
        )


@dataclass
class FunctionInfo:
    fid: str
    name: str
    unverifiable: str | None = None  # the reason, when not checkable by name
    # The call signature, for the parameter pass: positional slots in order
    # (``self``/``cls`` included) and the parameters that have a default.
    slots: tuple[str, ...] = ()
    defaulted: tuple[str, ...] = ()
    bound: bool = False  # a method: ``obj.f(a)`` passes *a* to slot 1


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


def _signature(args: ast.arguments, *, bound: bool) -> dict:
    positional = [a.arg for a in (*args.posonlyargs, *args.args)]
    with_default = positional[len(positional) - len(args.defaults) :]
    with_default += [
        a.arg
        for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)
        if d is not None
    ]
    return {
        "slots": tuple(positional),
        "defaulted": tuple(with_default),
        "bound": bound,
    }


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
                decorators = set(map(_decorator_name, child.decorator_list))
                info = FunctionInfo(
                    f"{rel}::{prefix}{child.name}",
                    child.name,
                    **_signature(
                        child.args,
                        bound=bool(prefix) and "staticmethod" not in decorators,
                    ),
                )
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


def _is_reexport_tuple(target: ast.expr, value: ast.expr | None) -> bool:
    """``_ = (f, g)``: names listed only so a linter keeps an import other
    modules (usually tests) reach for -- a re-export, not a use."""
    return (
        isinstance(target, ast.Name)
        and target.id == "_"
        and isinstance(value, ast.Tuple | ast.List)
        and all(isinstance(e, ast.Name | ast.Attribute) for e in value.elts)
    )


def _skipped_subtrees(tree: ast.AST) -> set[int]:
    """Imports (unless renamed), ``__all__`` values and ``_ = (f, g)``
    re-export tuples: not uses."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                (isinstance(t, ast.Name) and t.id == "__all__")
                or _is_reexport_tuple(t, node.value)
                for t in targets
            ):
                if node.value is not None:
                    ids.update(id(n) for n in ast.walk(node.value))
    return ids


def python_references(source: str) -> list[Ref]:
    """Every reference-shaped name in *source*, with how it is used."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    skipped = _skipped_subtrees(tree)
    callee_of = {id(n.func): n for n in ast.walk(tree) if isinstance(n, ast.Call)}
    out: list[Ref] = []

    def named(name: str, line: int, node: ast.AST) -> Ref:
        call = callee_of.get(id(node))
        return Ref(name, line, "call" if call else "use", call)

    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.Name):
            out.append(named(node.id, node.lineno, node))
        elif isinstance(node, ast.Attribute):
            out.append(named(node.attr, node.end_lineno or node.lineno, node))
        elif isinstance(node, ast.alias) and node.asname and node.asname != node.name:
            name = node.name.rsplit(".", 1)[-1]
            out.append(Ref(name, node.lineno, "alias", asname=node.asname))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            kind = "use" if _NAME_STRING.fullmatch(node.value.strip()) else "prose"
            out += [Ref(w, node.lineno, kind) for w in _WORD.findall(node.value)]
    return out


def python_names(source: str) -> list[tuple[str, int]]:
    """``(name, line)`` for every reference-shaped name in *source*."""
    return [(r.name, r.line) for r in python_references(source)]


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
                and p.relative_to(root).as_posix() not in NOT_REFERENCES
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
        found: list[Ref]
        if path.suffix == ".py":
            try:
                found = python_references(text)
            except SyntaxError:
                found = [Ref(n, ln, "use") for n, ln in _text_names(text)]
            else:
                if rel.startswith(f"{PACKAGE}/"):
                    owner = line_owner_map(function_spans(text))
        else:
            found = [Ref(n, ln, "use") for n, ln in _text_names(text)]
        # ``import f as g``: in this file, a use of ``g`` is a use of ``f``.
        renamed = {
            r.asname: r.name for r in found if r.kind == "alias" and r.name in names
        }
        for ref in found:
            targets = [ref.name] if ref.name in names else []
            if ref.kind != "alias" and ref.name in renamed:
                targets.append(renamed[ref.name])
            for target in targets:
                qual = owner.get(ref.line)
                sites[target].append(
                    Site(
                        rel,
                        ref.line,
                        f"{rel}::{qual}" if qual else None,
                        ref.kind,
                        CallShape.of(ref.call) if ref.call is not None else None,
                    )
                )
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


def package_function_infos(root: Path) -> dict[str, FunctionInfo]:
    """Every :class:`FunctionInfo` under the package, keyed by function id.
    Unparsable or undecodable files are skipped."""
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
    return infos


def dead_report(
    root: Path,
    unreached: set[str],
    infos: dict[str, FunctionInfo] | None = None,
) -> DeadReport:
    """Classify the *unreached* function ids of a recording. *infos* (from
    :func:`package_function_infos`) skips re-parsing the package."""
    if infos is None:
        infos = package_function_infos(root)
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


# ── keyword parameters ──────────────────────────────────────────────────────


@dataclass
class ParameterReport:
    """Parameters with a default that no production call passes: the
    parameter-level counterpart of :class:`DeadReport`. Every production call
    of such a function runs the default, so the parameter is either dead or
    test-only. Keyed by function id, parameters in signature order."""

    dead: dict[str, list[str]] = field(default_factory=dict)
    documented: dict[str, list[str]] = field(default_factory=dict)
    # A function its callers cannot all be seen for: one production use of
    # its name that is not a call (``callback=f``, ``partial(f, ...)``, a
    # string handed to ``getattr``).
    unverifiable: dict[str, Site] = field(default_factory=dict)


def passed_parameters(info: FunctionInfo, call: CallShape) -> set[str]:
    """Which of *info*'s defaulted parameters *call* may pass. Errs towards
    "passed": ``**`` unpacking passes all of them, ``*`` unpacking every
    positional slot, and a method's positional arguments are counted from
    slot 0 as well as slot 1, since ``Cls.f(obj, a)`` and ``obj.f(a)`` both
    reach it under one name."""
    if call.double_star:
        return set(info.defaulted)
    passed = set(call.keywords)
    if call.star:
        passed.update(info.slots)
    else:
        passed.update(info.slots[: call.positional + (1 if info.bound else 0)])
    return passed & set(info.defaulted)


def dead_parameters(
    root: Path, *, dead_functions: set[str] | frozenset[str] = frozenset()
) -> ParameterReport:
    """Every defaulted parameter of a package function that no production
    call passes. Calls are matched by name, as functions are, so a call of
    any function of that name counts. A function with no production call at
    all is the function pass's question and is skipped here, as is every
    member of *dead_functions*; calls inside their bodies do not count."""
    package_classes = _package_class_names(root)
    by_name: dict[str, list[FunctionInfo]] = defaultdict(list)
    for path in sorted((root / PACKAGE).rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        try:
            infos = function_infos(
                path.read_text(encoding="utf-8"), rel, package_classes
            )
        except (SyntaxError, UnicodeDecodeError):
            continue
        for info in infos:
            if (
                info.defaulted
                and not info.unverifiable
                and info.fid not in dead_functions
            ):
                by_name[info.name].append(info)
    sites = reference_sites(root, set(by_name))
    report = ParameterReport()
    unpassed: dict[str, list[str]] = {}
    for name, infos in sorted(by_name.items()):
        live = [
            s
            for s in sites.get(name, ())
            if not (s.enclosing and _outer(s.enclosing) in dead_functions)
        ]
        other = next((s for s in live if s.kind == "use"), None)
        calls = [s.call for s in live if s.call is not None]
        for info in infos:
            if other is not None:
                report.unverifiable[info.fid] = other
            elif calls:
                passed = set().union(*(passed_parameters(info, c) for c in calls))
                missing = [p for p in info.defaulted if p not in passed]
                if missing:
                    unpassed[info.fid] = missing
    user_docs = _mentions(
        root,
        USER_DOC_DIRS,
        {fid.rsplit("::", 1)[1].rsplit(".", 1)[-1] for fid in unpassed},
        (".md",),
    )
    for fid, params in sorted(unpassed.items()):
        name = fid.rsplit("::", 1)[1].rsplit(".", 1)[-1]
        (report.documented if name in user_docs else report.dead)[fid] = params
    return report


def render_parameters_markdown(
    report: ParameterReport, *, limit: int | None = None
) -> str:
    def lines_for(entries: dict[str, list[str]]) -> list[str]:
        fids = sorted(entries)
        out = [
            f"- `{fid}`: " + ", ".join(f"`{p}`" for p in entries[fid])
            for fid in fids[:limit]
        ]
        if limit is not None and len(fids) > limit:
            out.append(f"- ... and {len(fids) - limit} more")
        return out

    parts = [
        "# Keyword parameters no production call passes\n",
        "| class | functions | parameters |",
        "|---|---|---|",
        f"| dead (undocumented function) | {len(report.dead)} | "
        f"{sum(map(len, report.dead.values()))} |",
        f"| function documented in docs/use, docs/reference, docs/learn | "
        f"{len(report.documented)} | {sum(map(len, report.documented.values()))} |",
        f"| callers not all visible (a non-call use of the name) | "
        f"{len(report.unverifiable)} | |",
        "",
        "## Dead\n",
        *lines_for(report.dead),
        "",
        "## Documented\n",
        *lines_for(report.documented),
    ]
    return "\n".join(parts) + "\n"


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
