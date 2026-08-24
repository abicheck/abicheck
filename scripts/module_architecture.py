#!/usr/bin/env python3
"""Enforce bounded-module growth and target-package dependency direction.

The check is delta-based: with ``--base-ref``, an existing large file may
shrink but may not grow, while new files must fit the smaller limits declared
in ``architecture/module-boundaries.json``. It also freezes new top-level
``cli_*``/``service_*``/similar overflow modules and checks imports for code
already migrated into the target packages.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "architecture" / "module-boundaries.json"
DEFAULT_BASE_REF = "origin/main"
BASE_REF_ENV = "MODULE_ARCHITECTURE_BASE_REF"


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    old_path: str | None = None

    @property
    def is_new(self) -> bool:
        return self.status == "A" or self.status.startswith(("R", "C"))


@dataclass(frozen=True)
class Finding:
    level: str
    check: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "check": self.check,
            "path": self.path,
            "message": self.message,
        }


def resolve_base_ref(
    cli_base_ref: str | None, environ: Mapping[str, str] | None = None
) -> str:
    """Resolve the comparison base from CLI, CI environment, or local default."""
    values = os.environ if environ is None else environ
    return cli_base_ref or values.get(BASE_REF_ENV) or DEFAULT_BASE_REF


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("unsupported module-boundaries schema_version")
    for key in ("line_policies", "target_layers"):
        if not data.get(key):
            raise ValueError(f"missing non-empty {key}")
    known = set(data["target_layers"])
    for name, layer in data["target_layers"].items():
        allowed = layer.get("may_import_layers", [])
        unknown = set(allowed) - known
        if unknown:
            raise ValueError(
                f"target_layers.{name} references unknown layers: "
                + ", ".join(sorted(unknown))
            )
    for index, mapping in enumerate(data.get("legacy_import_layers", [])):
        layer = mapping.get("layer")
        patterns = mapping.get("patterns")
        if layer not in known:
            raise ValueError(
                f"legacy_import_layers[{index}] references unknown layer: {layer}"
            )
        if not isinstance(patterns, list) or not patterns:
            raise ValueError(
                f"legacy_import_layers[{index}] requires non-empty patterns"
            )
    return data


def _git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=False
    )
    if check and proc.returncode:
        detail = proc.stderr.strip() or proc.stdout.strip() or "unknown git error"
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return proc


def parse_name_status(output: str) -> list[Change]:
    changes: list[Change] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        status = fields[0]
        if status.startswith(("R", "C")) and len(fields) == 3:
            changes.append(Change(status, fields[2], fields[1]))
        elif len(fields) == 2:
            changes.append(Change(status, fields[1]))
        else:
            raise ValueError(f"malformed git name-status record: {line!r}")
    return changes


def merge_base(root: Path, base_ref: str) -> str:
    commit = _git(root, "merge-base", base_ref, "HEAD").stdout.strip()
    if not commit:
        raise RuntimeError(f"git merge-base {base_ref} HEAD returned no commit")
    return commit


def changed_paths(root: Path, base_commit: str) -> list[Change]:
    return parse_name_status(
        _git(
            root,
            "diff",
            "--name-status",
            "--find-renames=90%",
            f"{base_commit}..HEAD",
            "--",
        ).stdout
    )


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def line_policy(path: str, config: dict[str, Any]) -> dict[str, Any] | None:
    if _matches(path, config.get("ignore_globs", [])):
        return None
    for policy in config["line_policies"]:
        if _matches(path, policy["globs"]):
            return policy
    return None


def count_lines(text: str) -> int:
    return len(text.splitlines())


def _read_at_ref(root: Path, ref: str, path: str) -> str | None:
    proc = _git(root, "show", f"{ref}:{path}", check=False)
    return proc.stdout if proc.returncode == 0 else None


def _top_level_module(path: str) -> bool:
    parts = Path(path).parts
    return len(parts) == 2 and parts[0] == "abicheck" and path.endswith(".py")


def _top_level_name(path: str) -> str | None:
    """Return the module/package name directly below ``abicheck/``."""
    parts = Path(path).parts
    if len(parts) < 2 or parts[0] != "abicheck":
        return None
    first = parts[1]
    return Path(first).stem if first.endswith(".py") else first


def check_size(
    *,
    path: str,
    current: str,
    base: str | None,
    is_new: bool,
    config: dict[str, Any],
) -> list[Finding]:
    policy = line_policy(path, config)
    if policy is None:
        return []
    findings: list[Finding] = []
    now = count_lines(current)

    if is_new:
        top_level_name = _top_level_name(path)
        target_package_names = {
            Path(str(layer["path"])).name
            for layer in config["target_layers"].values()
        }
        if _top_level_module(path) and top_level_name in target_package_names:
            findings.append(
                Finding(
                    "error",
                    "target-layer-must-be-package",
                    path,
                    f"{top_level_name} is a target package; create "
                    f"abicheck/{top_level_name}/ with real code",
                )
            )
        prefix = next(
            (
                p
                for p in config.get("forbidden_new_top_level_prefixes", [])
                if top_level_name and top_level_name.startswith(p)
            ),
            None,
        )
        if prefix:
            findings.append(
                Finding(
                    "error",
                    "top-level-overflow-module",
                    path,
                    f"new top-level {prefix}* module/package is frozen; "
                    "use a bounded package",
                )
            )

    new_max = int(policy["new_file_max_lines"])
    no_growth = int(policy["legacy_no_growth_above_lines"])
    if is_new or base is None:
        if now > new_max:
            findings.append(
                Finding(
                    "error",
                    "new-file-size",
                    path,
                    f"{now} lines exceeds the {policy['name']} maximum of {new_max}",
                )
            )
    else:
        before = count_lines(base)
        if before > no_growth and now > before:
            findings.append(
                Finding(
                    "error",
                    "legacy-file-growth",
                    path,
                    f"legacy module grew {before} -> {now}; files above "
                    f"{no_growth} may only shrink",
                )
            )
        elif before <= new_max < now:
            findings.append(
                Finding(
                    "error",
                    "file-crossed-size-limit",
                    path,
                    f"module crossed the {new_max}-line ceiling: {before} -> {now}",
                )
            )

    warn = int(policy["warning_lines"])
    if now > warn:
        findings.append(
            Finding(
                "warning",
                "file-size-pressure",
                path,
                f"{now} lines exceeds the {policy['name']} review target of {warn}",
            )
        )
    return findings


def _module_parts(path: Path, root: Path) -> list[str]:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return parts


def imported_modules(path: Path, root: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    current = _module_parts(path, root)
    package = current if path.name == "__init__.py" else current[:-1]
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                resolved_package = package
                trim = node.level - 1
                resolved_package = (
                    resolved_package[:-trim] if trim else resolved_package
                )
                if node.module:
                    resolved_package += node.module.split(".")
                base = ".".join(resolved_package)
            if base:
                modules.append(base)
            # ``from abicheck import interfaces`` names the target layer in the
            # imported alias rather than ``node.module``. Recording both forms
            # is conservative but safe: only configured layer prefixes are
            # interpreted by the dependency checker.
            modules.extend(f"{base}.{a.name}".strip(".") for a in node.names)
    return list(dict.fromkeys(modules))


def _layer_for_path(path: str, config: dict[str, Any]) -> str | None:
    for name, layer in config["target_layers"].items():
        prefix = str(layer["path"]).rstrip("/")
        if path.startswith(f"{prefix}/"):
            return name
    return None


def _module_matches_pattern(module: str, pattern: str) -> bool:
    if any(token in pattern for token in "*?["):
        return fnmatch.fnmatch(module, pattern)
    return module == pattern or module.startswith(f"{pattern}.")


def _layer_for_module(module: str, config: dict[str, Any]) -> str | None:
    for name, layer in config["target_layers"].items():
        dotted = str(layer["path"]).replace("/", ".").rstrip(".")
        if module == dotted or module.startswith(f"{dotted}."):
            return name
    for mapping in config.get("legacy_import_layers", []):
        if any(
            _module_matches_pattern(module, str(pattern))
            for pattern in mapping["patterns"]
        ):
            return str(mapping["layer"])
    return None


def target_python_paths(root: Path, config: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for layer in config["target_layers"].values():
        base = root / str(layer["path"])
        if base.is_dir():
            paths.extend(p.relative_to(root).as_posix() for p in base.rglob("*.py"))
    return paths


def check_imports(
    root: Path, paths: Iterable[str], config: dict[str, Any]
) -> list[Finding]:
    findings: list[Finding] = []
    for rel in sorted(set(paths)):
        source = _layer_for_path(rel, config)
        path = root / rel
        if source is None or not path.is_file() or path.suffix != ".py":
            continue
        try:
            imports = imported_modules(path, root)
        except (OSError, SyntaxError) as exc:
            findings.append(
                Finding("error", "architecture-import-parse", rel, str(exc))
            )
            continue
        allowed = set(config["target_layers"][source]["may_import_layers"])
        seen_forbidden_targets: set[str] = set()
        seen_unclassified: set[str] = set()
        for module in imports:
            target = _layer_for_module(module, config)
            if (
                target is None
                and config.get("first_party_imports_must_be_classified", False)
                and module.startswith("abicheck.")
            ):
                import_root = ".".join(module.split(".")[:2])
                if import_root not in seen_unclassified:
                    seen_unclassified.add(import_root)
                    findings.append(
                        Finding(
                            "error",
                            "architecture-unclassified-first-party-import",
                            rel,
                            f"{source} imports unclassified first-party module "
                            f"{import_root}; add it to legacy_import_layers or "
                            "move it into a target package",
                        )
                    )
                continue
            if (
                target
                and target != source
                and target not in allowed
                and target not in seen_forbidden_targets
            ):
                seen_forbidden_targets.add(target)
                choices = ", ".join(sorted(allowed)) or "none"
                findings.append(
                    Finding(
                        "error",
                        "architecture-import-direction",
                        rel,
                        f"{source} imports {target} via {module}; allowed: {choices}",
                    )
                )
    return findings


def run_checks(
    root: Path, config: dict[str, Any], base_ref: str | None
) -> list[Finding]:
    findings: list[Finding] = []
    base_commit = merge_base(root, base_ref) if base_ref else None
    changed = changed_paths(root, base_commit) if base_commit else []
    changed_python: list[str] = []
    for change in changed:
        if change.status == "D" or not change.path.endswith(".py"):
            continue
        path = root / change.path
        if not path.is_file():
            continue
        current = path.read_text(encoding="utf-8")
        old_path = change.old_path or change.path
        base = _read_at_ref(root, base_commit, old_path) if base_commit else None
        findings.extend(
            check_size(
                path=change.path,
                current=current,
                base=base,
                is_new=change.is_new,
                config=config,
            )
        )
        changed_python.append(change.path)
    findings.extend(
        check_imports(
            root, [*target_python_paths(root, config), *changed_python], config
        )
    )
    return findings


def render_text(findings: Sequence[Finding]) -> str:
    if not findings:
        return "module-architecture: clean"
    lines = [
        f"{f.level.upper()}: [{f.check}] {f.path}: {f.message}" for f in findings
    ]
    errors = sum(f.level == "error" for f in findings)
    warnings = sum(f.level == "warning" for f in findings)
    lines.append(f"module-architecture: {errors} error(s), {warnings} warning(s)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--base-ref")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config_path = args.config if args.config.is_absolute() else root / args.config
    try:
        config = load_config(config_path)
        base_ref = resolve_base_ref(args.base_ref)
        findings = run_checks(root, config, base_ref)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(
            f"module-architecture: configuration/runtime error: {exc}",
            file=sys.stderr,
        )
        return 2
    if args.format == "json":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "errors": sum(f.level == "error" for f in findings),
                    "warnings": sum(f.level == "warning" for f in findings),
                    "findings": [f.to_dict() for f in findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(render_text(findings))
    return 1 if any(f.level == "error" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
