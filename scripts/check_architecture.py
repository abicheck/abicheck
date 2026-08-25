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
    config: dict[str, Any], findings: list[Finding]
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
    package = module.split(".")[:-1]
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
    return result


def _layer_for(module: str, layers: Mapping[str, dict[str, Any]]) -> str | None:
    for name, layer in layers.items():
        path = layer.get("path")
        if isinstance(path, str):
            prefix = path.replace("/", ".")
            if module == prefix or module.startswith(prefix + "."):
                return name
        for legacy_path in layer.get("legacy_paths", []):
            legacy_module = legacy_path.removesuffix(".py").replace("/", ".")
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
    return None


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
        elif not isinstance(node, ast.If) or not any(
            isinstance(child, ast.ImportFrom) and child.module == "__future__"
            for child in ast.walk(node)
        ):
            findings.append(
                Finding(
                    "facade-logic",
                    f"{path}:{node.lineno}: facade contains {type(node).__name__}, not delegation",
                )
            )


def check_repository(root: Path) -> list[Finding]:
    """Return all ADR-061 violations below ``root``."""
    findings: list[Finding] = []
    modules = _load_mapping(root / "architecture/modules.yaml", findings)
    debt = _load_mapping(root / "architecture/debt.yaml", findings)
    layers = _validate_modules(modules, findings)
    limits = modules.get("limits", {})
    production_limit = (
        limits.get("production", 800) if isinstance(limits, dict) else 800
    )
    test_limit = limits.get("test", 1200) if isinstance(limits, dict) else 1200
    if not isinstance(production_limit, int) or production_limit <= 0:
        findings.append(
            Finding("schema", "limits.production must be a positive integer")
        )
        production_limit = 800
    if not isinstance(test_limit, int) or test_limit <= 0:
        findings.append(Finding("schema", "limits.test must be a positive integer"))
        test_limit = 1200
    baselines = _validate_debt(debt, production_limit, test_limit, findings)

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
        if lines > baseline:
            findings.append(
                Finding(
                    "debt-no-growth",
                    f"{relative}: {lines} lines exceeds adoption baseline {baseline}; move responsibility instead of raising the baseline",
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
        agents = root / layers[source_layer]["path"] / "AGENTS.md"
        if not agents.is_file():
            findings.append(
                Finding(
                    "scoped-instructions",
                    f"{layers[source_layer]['path']}/: migrated package requires AGENTS.md",
                )
            )
        for lineno, target in _imports(path, module, findings):
            if not target.startswith("abicheck"):
                continue
            target_layer = _layer_for(target, layers)
            if target_layer == source_layer:
                continue
            if target_layer is None:
                if not any(
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    args = parser.parse_args(argv)
    findings = check_repository(args.root.resolve())
    for finding in findings:
        print(f"ERROR: {finding.render()}")
    print(f"Architecture: {len(findings)} error(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
