#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Usage ratchet: code kept alive only by tests, and cross-module clones.

The 95% coverage floor counts a test as a caller, so a function whose last
production caller was deleted stays green forever: its unit test keeps it
covered. This gate reads the tree statically (stdlib ``ast``, no import, no
recording) and reports three finding families, each as a stable key:

``test_only_functions``
    ``module#qualname`` of a function defined in ``abicheck/`` that the
    tests name but no production code reaches. The reference rule is
    ``production_references.py``'s (``docs/contribute/plans/
    dead-code-and-single-owner.md``): production is ``abicheck/``,
    ``scripts/``, ``action/``, ``actions/``, ``.github/`` and
    ``pyproject.toml``; a reference counts only when it does not sit inside
    another dead function's body (greatest fixpoint); documented API
    (``docs/use``/``docs/reference``/``docs/learn``) and functions only an
    ADR or plan names are roots, not findings; dunders, registered and
    framework-called methods are not checkable and never reported.
``test_only_modules``
    A module under ``abicheck/`` (not a package ``__init__``/``__main__``)
    that ``tests/`` import but no live production module imports or names
    (``abicheck.x.y`` in a string, an entry point, a workflow). Also a
    greatest fixpoint: a module imported only by test-only modules is one.
``cross_module_exact_clones``
    Functions in two or more different modules whose normalized AST
    (docstring dropped, no positions, the function's own name ignored) is
    identical and at least ``--min-nodes`` nodes large. Key: the sorted
    members joined by ``" = "`` -- no line numbers, so edits elsewhere in a
    file do not churn the baseline.

**Ratchet.** ``scripts/usage_ratchet_baseline.json`` lists the findings
that existed when the gate was adopted. A finding absent from the baseline
fails (new debt); a baseline key that is no longer found fails too (fixed --
remove it with ``--update``), so the baseline only shrinks. ``--base REF``
(default ``$ARCHITECTURE_BASE``) additionally rejects a baseline key that the
base revision's baseline does not carry: a change cannot baseline its own
new finding.

**Exceptions.** ``scripts/usage_exceptions.yaml`` declares keys that are
legitimately test-only or duplicated (public API kept for library users, a
plugin entry point), each with a ``reason``. A declared key that no longer
matches a finding fails as stale.

Name-based, like the module it reuses: ``obj.foo()`` keeps every ``foo``
alive, so it errs towards keeping code. Exit codes: 0 clean, 1 findings or a
stale baseline/exception, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import production_references as pr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASELINE_REL = "scripts/usage_ratchet_baseline.json"
EXCEPTIONS_REL = "scripts/usage_exceptions.yaml"
CATEGORIES = (
    "test_only_functions",
    "test_only_modules",
    "cross_module_exact_clones",
)
DEFAULT_MIN_NODES = 40
_DOTTED = re.compile(rf"\b(?:{re.escape(pr.PACKAGE)})(?:\.[A-Za-z_]\w*)+")


# ── naming ──────────────────────────────────────────────────────────────────


def module_name(rel: str) -> str:
    """``abicheck/a/b.py`` -> ``abicheck.a.b``; a package ``__init__`` is
    the package itself."""
    parts = rel[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def function_key(fid: str) -> str:
    """``abicheck/a/b.py::C.f`` -> ``abicheck.a.b#C.f``."""
    rel, qual = fid.split("::", 1)
    return f"{module_name(rel)}#{qual}"


def _package_files(root: Path) -> list[Path]:
    return [
        p
        for p in sorted((root / pr.PACKAGE).rglob("*.py"))
        if "__pycache__" not in p.parts
    ]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ── test-only functions ─────────────────────────────────────────────────────


def test_only_functions(root: Path) -> set[str]:
    package_classes = pr._package_class_names(root)
    fids: set[str] = set()
    for path in _package_files(root):
        text = _read(path)
        if text is None:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            fids.update(i.fid for i in pr.function_infos(text, rel, package_classes))
        except SyntaxError:
            continue
    report = pr.dead_report(root, fids)
    return {function_key(fid) for fid in report.dead if fid in report.tests}


# ── test-only modules ───────────────────────────────────────────────────────


def _dotted_names(text: str) -> set[str]:
    """Every ``abicheck.x.y`` named in *text*, with its prefixes: a string
    ``abicheck.x.y.func`` names module ``abicheck.x.y``."""
    out: set[str] = set()
    for match in _DOTTED.findall(text):
        parts = match.split(".")
        out.update(".".join(parts[:i]) for i in range(2, len(parts) + 1))
    return out


def _imported_modules(source: str, own: str, is_package: bool) -> set[str]:
    """Dotted module names *source* imports, or names in a string literal
    that is not a docstring (``importlib.import_module("abicheck.x")``)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _dotted_names(source)
    out: set[str] = set()
    docstrings = pr._docstring_nodes(tree)
    pkg_parts = own.split(".") if is_package else own.split(".")[:-1]
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            out |= _dotted_names(node.value)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(pkg_parts) - (node.level - 1)
                if keep <= 0:
                    continue
                base = ".".join(pkg_parts[:keep])
                mod = f"{base}.{node.module}" if node.module else base
            else:
                mod = node.module or ""
            out.add(mod)
            out.update(f"{mod}.{a.name}" for a in node.names)
    return out


def test_only_modules(root: Path) -> set[str]:
    candidates: dict[str, Path] = {}
    for path in _package_files(root):
        if path.name in ("__init__.py", "__main__.py"):
            continue
        candidates[module_name(path.relative_to(root).as_posix())] = path

    # referrer (a candidate module, or None for a live root) per module
    prod_refs: dict[str, set[str | None]] = defaultdict(set)
    for path in pr._production_files(root):
        text = _read(path)
        if text is None:
            continue
        rel = path.relative_to(root).as_posix()
        if path.suffix == ".py" and rel.startswith(f"{pr.PACKAGE}/"):
            own = module_name(rel)
            names = _imported_modules(text, own, path.name == "__init__.py")
            holder: str | None = own if own in candidates else None
        elif path.suffix == ".py":
            names = _imported_modules(text, "", False)
            holder = None
        else:
            names = _dotted_names(text)
            holder = None
        for name in names:
            if name in candidates and name != holder:
                prod_refs[name].add(holder)

    dead = set(candidates)
    changed = True
    while changed:
        changed = False
        for mod in sorted(dead):
            if any(r is None or r not in dead for r in prod_refs.get(mod, ())):
                dead.discard(mod)
                changed = True

    tested: set[str] = set()
    tests = root / pr.TEST_DIR
    if tests.is_dir():
        for path in sorted(tests.rglob("*.py")):
            text = _read(path)
            if text is not None:
                tested |= _imported_modules(text, "", False)
    return dead & tested


# ── cross-module exact clones ───────────────────────────────────────────────


def _node_count(nodes: Iterable[ast.AST]) -> int:
    return sum(1 for n in nodes for _ in ast.walk(n))


def _fingerprint(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, int]:
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    size = _node_count(body)
    dumped = "\n".join(
        [
            type(fn).__name__,
            ast.dump(fn.args, include_attributes=False),
            *(ast.dump(d, include_attributes=False) for d in fn.decorator_list),
            ast.dump(fn.returns, include_attributes=False) if fn.returns else "",
            *(ast.dump(s, include_attributes=False) for s in body),
        ]
    )
    return hashlib.sha256(dumped.encode()).hexdigest(), size


def _functions(
    tree: ast.AST,
) -> Iterable[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    def visit(node: ast.AST, prefix: str) -> Iterable[tuple[str, ast.AST]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield from visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                yield f"{prefix}{child.name}", child
                yield from visit(child, f"{prefix}{child.name}{pr.LOCALS}")
            else:
                yield from visit(child, prefix)

    yield from visit(tree, "")  # type: ignore[misc]


def cross_module_exact_clones(
    root: Path, min_nodes: int = DEFAULT_MIN_NODES
) -> set[str]:
    groups: dict[str, set[str]] = defaultdict(set)
    for path in _package_files(root):
        text = _read(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        mod = module_name(path.relative_to(root).as_posix())
        for qual, fn in _functions(tree):
            digest, size = _fingerprint(fn)
            if size >= min_nodes:
                groups[digest].add(f"{mod}#{qual}")
    out: set[str] = set()
    for members in groups.values():
        if len({m.split("#", 1)[0] for m in members}) > 1:
            out.add(" = ".join(sorted(members)))
    return out


# ── findings, baseline, exceptions ──────────────────────────────────────────


def collect(root: Path, min_nodes: int = DEFAULT_MIN_NODES) -> dict[str, set[str]]:
    return {
        "test_only_functions": test_only_functions(root),
        "test_only_modules": test_only_modules(root),
        "cross_module_exact_clones": cross_module_exact_clones(root, min_nodes),
    }


def _empty() -> dict[str, list[str]]:
    return {c: [] for c in CATEGORIES}


def parse_baseline(text: str) -> dict[str, set[str]]:
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) - set(CATEGORIES):
        raise ValueError(f"baseline keys must be a subset of {list(CATEGORIES)}")
    out: dict[str, set[str]] = {}
    for cat in CATEGORIES:
        items = data.get(cat, [])
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise ValueError(f"baseline {cat!r} must be a list of strings")
        out[cat] = set(items)
    return out


def load_exceptions(path: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {c: set() for c in CATEGORIES}
    if not path.is_file():
        return out
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or set(data) - set(CATEGORIES):
        raise ValueError(f"exception categories must be a subset of {list(CATEGORIES)}")
    for cat, entries in data.items():
        for entry in entries or []:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("key"), str)
                or not str(entry.get("reason") or "").strip()
            ):
                raise ValueError(
                    f"{cat}: every exception needs a `key` and a non-empty `reason`: {entry!r}"
                )
            out[cat].add(entry["key"])
    return out


def base_baseline(root: Path, base: str) -> dict[str, set[str]] | None:
    """The baseline at *base*, or ``None`` when the base has none (the gate
    is being introduced). Raises ``ValueError`` if *base* does not resolve."""
    ok = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "-q", f"{base}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if ok.returncode != 0:
        raise ValueError(f"base revision {base!r} does not resolve")
    shown = subprocess.run(
        ["git", "-C", str(root), "show", f"{base}:{BASELINE_REL}"],
        capture_output=True,
        text=True,
    )
    if shown.returncode != 0:
        return None
    return parse_baseline(shown.stdout)


def evaluate(
    found: dict[str, set[str]],
    baseline: dict[str, set[str]],
    exceptions: dict[str, set[str]],
    base: dict[str, set[str]] | None = None,
) -> list[str]:
    """Every violation, as a printable line; empty when the gate passes."""
    errors: list[str] = []
    for cat in CATEGORIES:
        live = found.get(cat, set())
        excepted = exceptions.get(cat, set())
        for key in sorted(excepted - live):
            errors.append(f"{cat}: stale exception (no longer found): {key}")
        live = live - excepted
        known = baseline.get(cat, set())
        for key in sorted(live - known):
            errors.append(f"{cat}: NEW finding: {key}")
        for key in sorted(known - live):
            errors.append(f"{cat}: fixed but still baselined (run --update): {key}")
        if base is not None:
            for key in sorted(known - base.get(cat, set())):
                errors.append(f"{cat}: baseline key absent on the base revision: {key}")
    return errors


def _default_base() -> str | None:
    env = os.environ.get("ARCHITECTURE_BASE")
    return env or None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--baseline", default=None, help=f"default: <root>/{BASELINE_REL}")
    ap.add_argument(
        "--exceptions", default=None, help=f"default: <root>/{EXCEPTIONS_REL}"
    )
    ap.add_argument(
        "--base",
        default=None,
        help="git revision whose baseline bounds this one (default: $ARCHITECTURE_BASE)",
    )
    ap.add_argument("--min-nodes", type=int, default=DEFAULT_MIN_NODES)
    ap.add_argument(
        "--update",
        action="store_true",
        help="rewrite the baseline to the current findings",
    )
    ap.add_argument(
        "--json", action="store_true", help="print findings as JSON and exit 0"
    )
    args = ap.parse_args(argv)

    root = args.root.resolve()
    baseline_path = Path(args.baseline) if args.baseline else root / BASELINE_REL
    exceptions_path = (
        Path(args.exceptions) if args.exceptions else root / EXCEPTIONS_REL
    )
    try:
        exceptions = load_exceptions(exceptions_path)
    except (ValueError, OSError) as exc:
        print(f"usage ratchet: bad exceptions file: {exc}", file=sys.stderr)
        return 2
    found = collect(root, args.min_nodes)

    if args.json:
        print(json.dumps({c: sorted(found[c]) for c in CATEGORIES}, indent=2))
        return 0
    if args.update:
        data = {c: sorted(found[c] - exceptions[c]) for c in CATEGORIES}
        baseline_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(
            f"usage ratchet: wrote {baseline_path} ({sum(map(len, data.values()))} keys)"
        )
        return 0

    try:
        baseline = (
            parse_baseline(baseline_path.read_text(encoding="utf-8"))
            if baseline_path.is_file()
            else {c: set() for c in CATEGORIES}
        )
        base_ref = args.base or _default_base()
        base = base_baseline(root, base_ref) if base_ref else None
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"usage ratchet: {exc}", file=sys.stderr)
        return 2

    errors = evaluate(found, baseline, exceptions, base)
    for line in errors:
        print(line)
    if errors:
        print(
            f"usage ratchet: {len(errors)} problem(s). A new finding means a "
            "function/module only tests use, or a duplicated function: wire it "
            "into production, delete it, or merge the clone. Declare a real "
            f"public-API exception in {EXCEPTIONS_REL} with a reason.",
            file=sys.stderr,
        )
        return 1
    total = sum(len(v) for v in baseline.values())
    print(f"usage ratchet: OK ({total} baselined finding(s), none new, none stale)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
