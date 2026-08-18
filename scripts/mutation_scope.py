#!/usr/bin/env python3
"""Narrow PR mutmut runs to detector modules implicated by the diff.

The scheduled and dispatch lanes deliberately retain the complete configured
scope.  On a pull request, `--diff-scoped` can only gate changed detector
modules, so generating mutants for unrelated modules only spends the job's
wall-clock budget and can prevent any measurement from completing.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = REPO_ROOT / "pyproject.toml"

# These define how the lane selects, runs, or interprets mutation coverage.
# Their changes can affect every module, so a PR must retain the full scope.
MUTATION_INFRASTRUCTURE_PATHS = frozenset(
    {
        ".github/workflows/mutation.yml",
        "pyproject.toml",
        "scripts/check_mutation_score.py",
        "scripts/mutation_results.py",
        "scripts/mutation_scope.py",
        "tests/test_mutation_results.py",
        "tests/test_mutation_scope.py",
        "tests/test_mutation_workflow_contract.py",
    }
)


def selected_modules(changed_paths: set[str], only_mutate: list[str]) -> list[str] | None:
    """Return a safe PR mutation subset, or ``None`` for the full scope.

    A changed detector module selects itself.  A conventional detector-test
    path selects its paired module.  Changes to mutation infrastructure or an
    unclassified test keep the full scope: such a change can affect every
    module's measurement and must not be made cheaper by assumption.
    """
    if changed_paths & MUTATION_INFRASTRUCTURE_PATHS:
        return None

    selected = {path for path in changed_paths if path in only_mutate}
    known_test_paths = set()
    for module in only_mutate:
        pattern = f"tests/test_{Path(module).stem}*.py"
        matches = {path for path in changed_paths if fnmatch.fnmatch(path, pattern)}
        known_test_paths.update(matches)
        if matches:
            selected.add(module)

    changed_tests = {path for path in changed_paths if path.startswith("tests/") and path.endswith(".py")}
    # When the PR changes a detector module, `--diff-scoped` can only gate
    # that module's changed functions.  An unrelated test edit cannot enlarge
    # that function set, so retain the source-derived subset.  With no source
    # module selected, an unclassified test could weaken any module and needs
    # the full drift measurement.
    if changed_tests - known_test_paths and not selected:
        return None
    return sorted(selected) or None


def rewrite_only_mutate(config_path: Path, modules: list[str]) -> None:
    """Replace only the TOML array, preserving the repository's comments."""
    text = config_path.read_text(encoding="utf-8")
    start = text.index("only_mutate = [")
    end = text.index("]\n", start) + 2
    rendered = "only_mutate = [\n" + "".join(f'    "{module}",\n' for module in modules) + "]\n"
    config_path.write_text(text[:start] + rendered + text[end:], encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", required=True)
    args = parser.parse_args()

    config = tomllib.loads(_CONFIG.read_text(encoding="utf-8"))["tool"]["mutmut"]
    only_mutate = config["only_mutate"]
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{args.base_ref}...HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    modules = selected_modules(set(result.stdout.splitlines()), only_mutate)
    if modules is None:
        print("mutation scope: full configured scope")
    else:
        rewrite_only_mutate(_CONFIG, modules)
        print("mutation scope: " + ", ".join(modules))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
