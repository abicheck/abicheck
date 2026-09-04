#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Enforce ADR-061's package boundaries and no-growth migration baseline.

This focused gate owns architecture configuration validation and Python import
analysis.  It does not assess runtime behavior or replace the temporary
2,000-line readiness backstop.  ``check_repository`` is the canonical entry
point; ``--root`` exists so tests can exercise complete miniature trees.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
GENERIC_NAMES = frozenset(
    {"helpers.py", "utils.py", "common.py", "misc.py", "base.py", "extra.py", "more.py"}
)
GENERIC_SUFFIXES = ("_helpers", "_utils", "_lib")
DEBT_FIELDS = frozenset(
    {
        "path",
        "baseline_lines",
        "target",
        "rule",
        "category",
        "owner",
        "rationale",
        "review_by",
    }
)

# ADR-063 D10 (implementation plan Phase 9): abicheck/policy/selectors.py is
# the shared selector-matching leaf `suppression.py`'s `Suppression` and
# `reclassify.py`'s `ReclassifyRule` both build on. Its whole reason to
# exist is letting `reclassify.py` import it *statically* without recreating
# the import cycle `policy_file -> reclassify -> suppression -> checker_types
# -> policy_file` a static `Suppression` import once closed (the module used
# to work around that with a runtime `importlib.import_module` call). That
# only holds if the leaf itself never imports back into any module on that
# cycle -- and this denylist is strictly narrower than the general
# `policy -> compare` layer edge already permits (`finding_identity.py` is
# classified into the `compare` layer, which `policy` may otherwise import),
# so the general dependency-direction check below would not catch a
# regression here on its own. Checked directly against each leaf file's own
# imports, not modeled as a layer.
#
# Covers `selectors_namespace_glob.py` too, not just `selectors.py` itself
# (Codex review, PR #1002): `selectors.py` imports from that sibling, so a
# denylisted import added there would taint `selectors.py` transitively
# while passing a check scoped to `selectors.py`'s own source text alone.
_SELECTOR_LEAF_PATHS: tuple[str, ...] = (
    "abicheck/policy/selectors.py",
    "abicheck/policy/selectors_namespace_glob.py",
)
_SELECTOR_LEAF_DENYLIST: tuple[str, ...] = (
    "abicheck.policy_file",
    "abicheck.checker_types",
    "abicheck.suppression",
    "abicheck.reclassify",
    "abicheck.finding_identity",
)


@dataclass(frozen=True)
class Finding:
    """One actionable architecture violation."""

    rule: str
    message: str

    def render(self) -> str:
        return f"[{self.rule}] {self.message}"


def _load_mapping(path: Path, findings: list[Finding]) -> dict[str, Any]:
    """Load the JSON-compatible YAML contract without third-party packages."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        findings.append(Finding("config", f"{path}: required file is missing"))
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(Finding("config", f"{path}: cannot load: {exc}"))
        return {}
    if not isinstance(value, dict):
        findings.append(Finding("config", f"{path}: top level must be a mapping"))
        return {}
    return value


def _string_list(value: object, where: str, findings: list[Finding]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        findings.append(Finding("schema", f"{where}: must be a list of strings"))
        return []
    return value


def _safe_relative_path(
    value: object, where: str, findings: list[Finding]
) -> str | None:
    if not isinstance(value, str) or not value:
        findings.append(Finding("schema", f"{where}: must be a non-empty path string"))
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        findings.append(
            Finding("schema", f"{where}: must be a normalized relative path")
        )
        return None
    return value


def _find_cycle(graph: Mapping[str, set[str]]) -> list[str] | None:
    active: list[str] = []
    done: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in active:
            start = active.index(node)
            return [*active[start:], node]
        if node in done:
            return None
        active.append(node)
        for target in sorted(graph.get(node, ())):
            cycle = visit(target)
            if cycle:
                return cycle
        active.pop()
        done.add(node)
        return None

    for node in sorted(graph):
        cycle = visit(node)
        if cycle:
            return cycle
    return None


def _validate_modules(
    config: dict[str, Any], findings: list[Finding], root: Path
) -> dict[str, dict[str, Any]]:
    if config.get("schema_version") != SCHEMA_VERSION:
        findings.append(
            Finding("schema", "architecture/modules.yaml: schema_version must be 1")
        )
    layers = config.get("layers")
    if not isinstance(layers, dict) or not layers:
        findings.append(
            Finding(
                "schema",
                "architecture/modules.yaml: layers must be a non-empty mapping",
            )
        )
        return {}
    validated: dict[str, dict[str, Any]] = {}
    paths: set[str] = set()
    graph: dict[str, set[str]] = {}
    for name, raw in layers.items():
        where = f"architecture/modules.yaml layers.{name}"
        if not isinstance(name, str) or not isinstance(raw, dict):
            findings.append(Finding("schema", f"{where}: must be a named mapping"))
            continue
        path = _safe_relative_path(raw.get("path"), f"{where}.path", findings)
        imports = _string_list(raw.get("may_import"), f"{where}.may_import", findings)
        if path in paths:
            findings.append(
                Finding("schema", f"{where}.path: duplicate layer path {path!r}")
            )
        if path:
            paths.add(path)
        legacy_paths = _string_list(
            raw.get("legacy_paths", []), f"{where}.legacy_paths", findings
        )
        for index, legacy_path in enumerate(legacy_paths):
            valid_legacy = _safe_relative_path(
                legacy_path, f"{where}.legacy_paths[{index}]", findings
            )
            if valid_legacy and (
                not valid_legacy.startswith("abicheck/")
                or not valid_legacy.endswith(".py")
            ):
                findings.append(
                    Finding(
                        "schema",
                        f"{where}.legacy_paths[{index}]: must name an abicheck Python module",
                    )
                )
            elif valid_legacy and not (root / valid_legacy).is_file():
                # P2 review, fresh evidence: an entry naming a file that no
                # longer exists (e.g. deleted/moved during a cleanup) is not
                # rejected by anything above -- it silently keeps
                # pre-authorizing a *future* file at that path to reappear
                # under the flat legacy classification, bypassing the
                # canonical-owner/no-growth gate a genuinely new module
                # would otherwise have to clear. Stale entries must be
                # removed, not left to accumulate.
                findings.append(
                    Finding(
                        "schema",
                        f"{where}.legacy_paths[{index}]: {valid_legacy!r} does not "
                        "exist -- remove the stale entry instead of leaving it to "
                        "pre-authorize a future file at that path",
                    )
                )
        validated[name] = {
            "path": path,
            "may_import": imports,
            "legacy_paths": legacy_paths,
        }
        graph[name] = set(imports)
    for name, imports in graph.items():
        unknown = imports - graph.keys()
        if unknown:
            findings.append(
                Finding(
                    "schema",
                    f"layer {name!r} may_import names unknown layers: {', '.join(sorted(unknown))}",
                )
            )
        if name in imports:
            findings.append(
                Finding("dependency-cycle", f"layer {name!r} imports itself")
            )
    legacy_owners: dict[str, str] = {}
    for name, layer in validated.items():
        for legacy_path in layer["legacy_paths"]:
            previous = legacy_owners.setdefault(legacy_path, name)
            if previous != name:
                findings.append(
                    Finding(
                        "schema",
                        f"legacy path {legacy_path!r} is classified by both {previous!r} and {name!r}",
                    )
                )
    cycle = _find_cycle(
        {name: imports & graph.keys() for name, imports in graph.items()}
    )
    if cycle:
        findings.append(
            Finding("dependency-cycle", "declared layer cycle: " + " -> ".join(cycle))
        )
    return validated


def _validate_debt(
    config: dict[str, Any],
    production_limit: int,
    test_limit: int,
    findings: list[Finding],
) -> dict[str, int]:
    if config.get("schema_version") != SCHEMA_VERSION:
        findings.append(
            Finding("schema", "architecture/debt.yaml: schema_version must be 1")
        )
    records = config.get("files")
    if not isinstance(records, list):
        findings.append(
            Finding("schema", "architecture/debt.yaml: files must be a list")
        )
        return {}
    baselines: dict[str, int] = {}
    for index, raw in enumerate(records):
        where = f"architecture/debt.yaml files[{index}]"
        if not isinstance(raw, dict):
            findings.append(Finding("schema", f"{where}: must be a mapping"))
            continue
        missing = DEBT_FIELDS - raw.keys()
        if missing:
            findings.append(
                Finding("schema", f"{where}: missing {', '.join(sorted(missing))}")
            )
        path = _safe_relative_path(raw.get("path"), f"{where}.path", findings)
        baseline = raw.get("baseline_lines")
        applicable_limit = (
            test_limit
            if isinstance(path, str) and path.startswith("tests/")
            else production_limit
        )
        if (
            not isinstance(baseline, int)
            or isinstance(baseline, bool)
            or baseline < applicable_limit
        ):
            findings.append(
                Finding(
                    "schema",
                    f"{where}.baseline_lines: must be an integer >= {applicable_limit}",
                )
            )
        if raw.get("rule") != "no_growth":
            findings.append(
                Finding("schema", f"{where}.rule: only 'no_growth' is supported")
            )
        for field in ("target", "category", "owner", "rationale"):
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                findings.append(
                    Finding("schema", f"{where}.{field}: must be non-empty")
                )
        try:
            dt.date.fromisoformat(raw.get("review_by", ""))
        except (TypeError, ValueError):
            findings.append(
                Finding("schema", f"{where}.review_by: must be an ISO date")
            )
        if path:
            if not path.startswith(("abicheck/", "tests/")) or not path.endswith(".py"):
                findings.append(
                    Finding(
                        "schema",
                        f"{where}.path: debt must name an abicheck or tests Python module",
                    )
                )
            elif path in baselines:
                findings.append(
                    Finding("schema", f"{where}.path: duplicate debt path {path}")
                )
            elif isinstance(baseline, int):
                baselines[path] = baseline
    return baselines


def _line_count(path: Path) -> int:
    with path.open(encoding="utf-8") as stream:
        return sum(1 for _ in stream)


def _git_file_line_count(root: Path, revision: str, relative: str) -> int | None:
    """Return a file's line count at *revision*, or ``None`` when absent."""
    proc = subprocess.run(
        ["git", "show", f"{revision}:{relative}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return len(proc.stdout.splitlines())


def _base_has_architecture_contract(root: Path, revision: str) -> bool | None:
    revision_check = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=root,
        capture_output=True,
    )
    if revision_check.returncode != 0:
        return None
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}:architecture/debt.yaml"],
        cwd=root,
        capture_output=True,
    )
    return proc.returncode == 0


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(
    path: Path, module: str, findings: list[Finding]
) -> Iterable[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        findings.append(
            Finding(
                "import-parse",
                f"{path}:{getattr(exc, 'lineno', 1)}: cannot parse imports: {exc}",
            )
        )
        return ()
    package = (
        module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    )
    result: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package) - node.level + 1
                prefix = package[: max(keep, 0)]
                target = ".".join([*prefix, *(node.module or "").split(".")]).rstrip(
                    "."
                )
            else:
                target = node.module or ""
            if target:
                result.append((node.lineno, target))
                # Every `from <target> import <name>` -- relative
                # (`from . import x`/`from .. import x`, `node.module` empty)
                # or absolute (`from abicheck import x`,
                # `from abicheck.buildsource import x`, `node.module` set) --
                # is ambiguous from the AST alone: `<name>` may be a plain
                # symbol defined in `<target>`'s own `__init__.py`, or it may
                # itself be a submodule of `<target>`. An earlier revision of
                # this fix only handled the relative, empty-`node.module`
                # case, so an absolute `from abicheck import legacy_compare`
                # (a `legacy_paths`-classified importer, no `unclassified-
                # import` fallback to catch it) stayed silently invisible to
                # `dependency-direction` the identical way (Codex review,
                # fresh evidence). Handling both shapes uniformly -- record
                # `target.<name>` alongside `target` for every `ImportFrom`,
                # not only the bare-dot one -- closes both. Confirmed via a
                # real repo-wide check before trusting this: the identical
                # two known violations the narrower fix found, still zero new
                # false positives from any module's ordinary `from X import
                # <plain symbol>` usage (a symbol name essentially never
                # collides with an unrelated, differently-classified
                # submodule's own name).
                for alias in node.names:
                    result.append((node.lineno, f"{target}.{alias.name}"))
    return result


def _check_selector_leaf_purity(root: Path, findings: list[Finding]) -> None:
    """ADR-063 D10: every path in ``_SELECTOR_LEAF_PATHS`` must import
    nothing from ``_SELECTOR_LEAF_DENYLIST`` -- see that constant's own
    comment for why, and ``_SELECTOR_LEAF_PATHS``' own comment for why both
    files are covered, not just ``selectors.py``.

    A no-op for any path that doesn't exist (e.g. a miniature test tree that
    only builds one of the two), so this check stays silent everywhere
    except these leaf modules and any fixture that deliberately creates one.
    """
    for leaf_path in _SELECTOR_LEAF_PATHS:
        path = root / leaf_path
        if not path.is_file():
            continue
        module = _module_name(root, path)
        for lineno, target in _imports(path, module, findings):
            if target in _SELECTOR_LEAF_DENYLIST or any(
                target.startswith(name + ".") for name in _SELECTOR_LEAF_DENYLIST
            ):
                findings.append(
                    Finding(
                        "selector-leaf-purity",
                        f"{path.relative_to(root)}:{lineno}: must not import "
                        f"{target!r} -- ADR-063 D10 leaf-module contract "
                        f"({leaf_path} may not depend on any of "
                        f"{', '.join(_SELECTOR_LEAF_DENYLIST)})",
                    )
                )


def _layer_for(
    module: str, root: Path, layers: Mapping[str, dict[str, Any]]
) -> str | None:
    for name, layer in layers.items():
        path = layer.get("path")
        if isinstance(path, str):
            prefix = path.replace("/", ".")
            if (root / path).is_dir() and (
                module == prefix or module.startswith(prefix + ".")
            ):
                return name
        for legacy_path in layer.get("legacy_paths", []):
            legacy_module = legacy_path.removesuffix(".py").replace("/", ".")
            # A package's own `__init__.py` is imported under its package
            # name, not `<package>.__init__` -- `import abicheck.policies`
            # never produces the latter. Without this, a legacy_paths entry
            # naming a package `__init__.py` (e.g. "abicheck/policies/
            # __init__.py") could never match that package's own import
            # target, silently leaving it unclassified.
            legacy_module = legacy_module.removesuffix(".__init__")
            if module == legacy_module or module.startswith(legacy_module + "."):
                return name
    return None


def _source_layer_for(
    path: Path, root: Path, layers: Mapping[str, dict[str, Any]]
) -> str | None:
    """Classify a source by its path, avoiding ``model.py``/``model/`` ambiguity."""
    relative = path.relative_to(root).as_posix()
    for name, layer in layers.items():
        prefix = layer.get("path")
        if isinstance(prefix, str) and relative.startswith(prefix.rstrip("/") + "/"):
            return name
        if relative in layer.get("legacy_paths", []):
            return name
    return None


def _inert_facade_value(node: ast.expr | None) -> bool:
    """Return whether an assignment value binds data or an alias without executing."""
    if node is None or isinstance(node, (ast.Constant, ast.Name, ast.Attribute)):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_inert_facade_value(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(_inert_facade_value(item) for item in [*node.keys, *node.values])
    return False


def _check_facade(path: Path, name: str, limit: int, findings: list[Finding]) -> None:
    lines = _line_count(path)
    if lines > limit:
        findings.append(
            Finding(
                "facade-size", f"{path}: {lines} lines exceeds facade maximum {limit}"
            )
        )
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return
    has_all = any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
        for node in tree.body
    )
    if not has_all:
        findings.append(
            Finding(
                "facade-exports",
                f"{path}: configured facade {name!r} must declare __all__",
            )
        )
    allowed = (ast.Expr, ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)
    for node in tree.body:
        if isinstance(node, allowed):
            if isinstance(node, ast.Expr) and not isinstance(node.value, ast.Constant):
                findings.append(
                    Finding(
                        "facade-logic",
                        f"{path}:{node.lineno}: facade contains executable expression",
                    )
                )
            elif isinstance(node, ast.Assign) and not _inert_facade_value(node.value):
                findings.append(
                    Finding(
                        "facade-logic",
                        f"{path}:{node.lineno}: facade assignment executes logic",
                    )
                )
            elif isinstance(node, ast.AnnAssign) and not _inert_facade_value(
                node.value
            ):
                findings.append(
                    Finding(
                        "facade-logic",
                        f"{path}:{node.lineno}: facade assignment executes logic",
                    )
                )
        elif not (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
            and not node.orelse
            and all(
                isinstance(child, (ast.Import, ast.ImportFrom)) for child in node.body
            )
        ):
            findings.append(
                Finding(
                    "facade-logic",
                    f"{path}:{node.lineno}: facade contains {type(node).__name__}, not delegation",
                )
            )


def check_repository(root: Path, *, base_revision: str | None = None) -> list[Finding]:
    """Return all ADR-061 violations below ``root``."""
    findings: list[Finding] = []
    modules = _load_mapping(root / "architecture/modules.yaml", findings)
    debt = _load_mapping(root / "architecture/debt.yaml", findings)
    layers = _validate_modules(modules, findings, root)
    _check_selector_leaf_purity(root, findings)
    limits = modules.get("limits", {})
    production_limit = (
        limits.get("production", 800) if isinstance(limits, dict) else 800
    )
    test_limit = limits.get("test", 1200) if isinstance(limits, dict) else 1200
    package_agents_limit = (
        limits.get("package_agents", 150) if isinstance(limits, dict) else 150
    )
    if not isinstance(production_limit, int) or production_limit <= 0:
        findings.append(
            Finding("schema", "limits.production must be a positive integer")
        )
        production_limit = 800
    if not isinstance(test_limit, int) or test_limit <= 0:
        findings.append(Finding("schema", "limits.test must be a positive integer"))
        test_limit = 1200
    if not isinstance(package_agents_limit, int) or package_agents_limit <= 0:
        findings.append(
            Finding("schema", "limits.package_agents must be a positive integer")
        )
        package_agents_limit = 150
    baselines = _validate_debt(debt, production_limit, test_limit, findings)

    base_has_contract = (
        _base_has_architecture_contract(root, base_revision) if base_revision else None
    )
    if base_revision and base_has_contract is None:
        findings.append(
            Finding(
                "base-revision",
                f"architecture base revision {base_revision!r} cannot be resolved",
            )
        )
    adopting_contract = bool(base_revision and base_has_contract is False)
    exception_roots = _string_list(
        modules.get("parser_or_catalog_roots", []),
        "parser_or_catalog_roots",
        findings,
    )
    for relative, baseline in baselines.items():
        path = root / relative
        if not path.is_file():
            findings.append(
                Finding(
                    "debt-path",
                    f"{relative}: debt entry names a missing file; remove the retired entry",
                )
            )
            continue
        lines = _line_count(path)
        base_lines = (
            _git_file_line_count(root, base_revision, relative)
            if base_revision and not adopting_contract
            else None
        )
        if (
            base_revision
            and base_has_contract
            and base_lines is None
            and not any(
                relative == prefix or relative.startswith(prefix.rstrip("/") + "/")
                for prefix in exception_roots
            )
        ):
            findings.append(
                Finding(
                    "debt-exemption",
                    f"{relative}: new ordinary files cannot be added to the adoption debt ledger",
                )
            )
        if (
            lines > baseline
            and not adopting_contract
            and (base_lines is None or lines > base_lines)
        ):
            comparison = f" and PR base {base_lines}" if base_lines is not None else ""
            findings.append(
                Finding(
                    "debt-no-growth",
                    f"{relative}: {lines} lines exceeds adoption baseline {baseline}{comparison}; move responsibility instead of raising the baseline",
                )
            )

    for path in sorted((root / "abicheck").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        lines = _line_count(path)
        if lines > production_limit and relative not in baselines:
            findings.append(
                Finding(
                    "new-file-size",
                    f"{relative}: {lines} lines exceeds production maximum {production_limit} and has no adoption debt entry",
                )
            )
    tests = root / "tests"
    if tests.is_dir():
        for path in sorted(tests.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            lines = _line_count(path)
            if lines > test_limit and relative not in baselines:
                findings.append(
                    Finding(
                        "new-test-size",
                        f"{relative}: {lines} lines exceeds test maximum {test_limit} and has no adoption debt entry",
                    )
                )

    families = modules.get("frozen_root_families", {})
    if isinstance(families, dict):
        for prefix, allowed_raw in families.items():
            allowed = set(
                _string_list(allowed_raw, f"frozen_root_families.{prefix}", findings)
            )
            if not isinstance(prefix, str) or not prefix:
                findings.append(
                    Finding(
                        "schema", "frozen_root_families keys must be non-empty strings"
                    )
                )
                continue
            for path in sorted((root / "abicheck").glob(f"{prefix}*.py")):
                if path.name not in allowed:
                    findings.append(
                        Finding(
                            "frozen-root-family",
                            f"{path.relative_to(root)}: new root {prefix!r} sibling is forbidden; create the responsibility package owner",
                        )
                    )

    public_root_files = {
        module.removeprefix("abicheck.").replace(".", "/") + ".py"
        for module in _string_list(
            modules.get("public_root_surfaces", []), "public_root_surfaces", findings
        )
    }
    facade_root_files = {
        module.removeprefix("abicheck.").replace(".", "/") + ".py"
        for module in _string_list(modules.get("facades", []), "facades", findings)
    }
    legacy_root_modules = set(
        _string_list(
            modules.get("legacy_root_modules", []), "legacy_root_modules", findings
        )
    )
    layer_legacy_root_files = {
        PurePosixPath(path).name
        for layer in layers.values()
        for path in layer.get("legacy_paths", [])
        if PurePosixPath(path).parent == PurePosixPath("abicheck")
    }
    allowed_root_modules = (
        legacy_root_modules
        | layer_legacy_root_files
        | public_root_files
        | facade_root_files
    )
    for path in sorted((root / "abicheck").glob("*.py")):
        if path.name not in allowed_root_modules:
            findings.append(
                Finding(
                    "root-module",
                    f"{path.relative_to(root)}: undeclared flat root module; create a responsibility-package owner",
                )
            )

    legacy_dirs = set(
        _string_list(
            modules.get("legacy_root_directories", []),
            "legacy_root_directories",
            findings,
        )
    )
    layer_dir_names = {
        PurePosixPath(layer["path"]).name
        for layer in layers.values()
        if layer.get("path")
    }
    for layer in layers.values():
        package_path = root / layer["path"]
        flat_path = package_path.with_suffix(".py")
        if package_path.is_dir() and flat_path.is_file():
            findings.append(
                Finding(
                    "module-package-collision",
                    f"{flat_path.relative_to(root)} and {package_path.relative_to(root)}/ both resolve to the same import name",
                )
            )
    for path in sorted((root / "abicheck").iterdir()):
        if path.is_dir() and path.name not in legacy_dirs | layer_dir_names | {
            "__pycache__"
        }:
            findings.append(
                Finding(
                    "root-package",
                    f"{path.relative_to(root)}/: undeclared root implementation package; assign it to an ADR-061 responsibility",
                )
            )

    for layer in layers.values():
        agents = root / layer["path"] / "AGENTS.md"
        if agents.is_file() and _line_count(agents) > package_agents_limit:
            findings.append(
                Finding(
                    "agents-size",
                    f"{agents.relative_to(root)}: {_line_count(agents)} lines exceeds package instructions maximum {package_agents_limit}",
                )
            )

    legacy_generic = set(
        _string_list(
            modules.get("legacy_generic_modules", []),
            "legacy_generic_modules",
            findings,
        )
    )
    for path in sorted((root / "abicheck").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative in legacy_generic:
            continue
        if path.name in GENERIC_NAMES or path.stem.endswith(GENERIC_SUFFIXES):
            findings.append(
                Finding(
                    "generic-module-name",
                    f"{relative}: generic module name has no stable responsibility",
                )
            )

    public_roots = set(
        _string_list(
            modules.get("public_root_surfaces", []), "public_root_surfaces", findings
        )
    )
    graph: dict[str, set[str]] = {name: set() for name in layers}
    for path in sorted((root / "abicheck").rglob("*.py")):
        module = _module_name(root, path)
        source_layer = _source_layer_for(path, root, layers)
        if source_layer is None:
            continue
        owner_path = layers[source_layer]["path"]
        migrated_source = (
            path.relative_to(root).as_posix().startswith(owner_path.rstrip("/") + "/")
        )
        agents = root / owner_path / "AGENTS.md"
        if migrated_source and not agents.is_file():
            findings.append(
                Finding(
                    "scoped-instructions",
                    f"{layers[source_layer]['path']}/: migrated package requires AGENTS.md",
                )
            )
        for lineno, target in _imports(path, module, findings):
            if target != "abicheck" and not target.startswith("abicheck."):
                continue
            target_layer = _layer_for(target, root, layers)
            if target_layer == source_layer:
                continue
            if target_layer is None:
                if migrated_source and not any(
                    target == surface or target.startswith(surface + ".")
                    for surface in public_roots
                ):
                    findings.append(
                        Finding(
                            "unclassified-import",
                            f"{path.relative_to(root)}:{lineno}: migrated layer {source_layer!r} imports unclassified first-party module {target!r}",
                        )
                    )
                continue
            graph[source_layer].add(target_layer)
            allowed = set(layers[source_layer]["may_import"])
            if target_layer not in allowed:
                findings.append(
                    Finding(
                        "dependency-direction",
                        f"{path.relative_to(root)}:{lineno}: {source_layer} -> {target_layer} is forbidden; allowed: {', '.join(sorted(allowed)) or '(none)'}",
                    )
                )
    cycle = _find_cycle(graph)
    if cycle:
        findings.append(
            Finding(
                "dependency-cycle",
                "observed responsibility cycle: " + " -> ".join(cycle),
            )
        )

    facades = _string_list(modules.get("facades", []), "facades", findings)
    facade_limit = limits.get("facade", 150) if isinstance(limits, dict) else 150
    for facade in facades:
        path = root / (facade.replace(".", "/") + ".py")
        if not path.is_file():
            findings.append(
                Finding("facade-path", f"{facade}: configured facade does not exist")
            )
        else:
            _check_facade(path, facade, facade_limit, findings)
    return findings


def _local_merge_base_with_main(root: Path) -> str | None:
    """Best-effort local stand-in for CI's ``ARCHITECTURE_BASE``.

    CI sets ``ARCHITECTURE_BASE`` to the PR's actual base sha, so the
    debt-no-growth check only flags growth this change itself introduces.
    Without that, a bare local invocation compares every debt-tracked file
    against its ADR-061 *adoption* baseline directly -- which drifts as
    unrelated, individually-compliant PRs each grow a file a little further
    past that original baseline (each one only checked against its own
    base). The cumulative drift then reads as a failure for the next
    contributor who runs this exact command untouched, on a file their own
    change never touched -- exactly the "local and CI definitions of done
    silently diverge" gap `scripts/verify.py`'s own module docstring (M0-3)
    exists to prevent, just not yet closed for this one step.

    Tries ``origin/main`` first (matching what CI's own base sha would
    resolve to), then falls back to a local ``main`` branch -- a checkout
    with no ``origin`` remote-tracking ref at all (the remote renamed or
    removed, a bare local clone) still has a resolvable base then, instead
    of silently reporting nothing (Codex review).

    Returns ``None`` (falling back to the previous unscoped comparison)
    when neither ref is resolvable -- a shallow clone, a detached checkout
    with no local branch either, or a non-git ``--root`` such as this
    script's own miniature-tree tests use via `check_repository()` directly
    (which never calls this function at all). Never raises.
    """
    for ref in ("origin/main", "main"):
        try:
            proc = subprocess.run(
                ["git", "merge-base", "HEAD", ref],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            continue
        sha = proc.stdout.strip()
        if sha:
            return sha
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument(
        "--base",
        default=None,
        help=(
            "PR base revision used to distinguish concurrent pre-adoption "
            "growth (default: $ARCHITECTURE_BASE, else the local merge-base "
            "with origin/main when one is resolvable)"
        ),
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    base = args.base
    if base is None:
        env_base = os.environ.get("ARCHITECTURE_BASE")
        if env_base:
            base = env_base
        elif "ARCHITECTURE_BASE" not in os.environ:
            # Only fall back to a locally-resolved merge-base when nothing
            # set $ARCHITECTURE_BASE at all -- a bare local invocation. CI's
            # own `ci.yml` sets it unconditionally, to
            # `github.event.pull_request.base.sha`, which is the empty
            # string on a `push`-to-`main` or `workflow_dispatch` run (no PR
            # context to read a base sha from). Falling back to
            # `_local_merge_base_with_main` in *that* case would resolve
            # `origin/main` to HEAD itself (the ref just pushed *is*
            # `origin/main` at that point), silently turning the debt-
            # no-growth check into comparing every file against itself --
            # vacuously passing committed growth instead of catching it
            # (Codex review, fresh evidence). An explicitly-empty
            # $ARCHITECTURE_BASE is CI's own signal that no PR base applies
            # to this run, so it must fall through to the previous unscoped
            # comparison instead, exactly like the pre-fallback behavior.
            base = _local_merge_base_with_main(root)
    findings = check_repository(root, base_revision=base)
    for finding in findings:
        print(f"ERROR: {finding.render()}")
    print(f"Architecture: {len(findings)} error(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
