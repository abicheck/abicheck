# SPDX-License-Identifier: Apache-2.0
"""Whether an abicheck module imports must not depend on import order.

Regression class ``first-import-order-cycle`` (``tests/regressions/manifest.py``):
``name_classification`` imported ``model.execution_cache``, which runs
``model/__init__`` first, and that package init (and several ``model``
submodules) import ``name_classification`` back. Imported after ``model`` that
works; imported *first* in a fresh interpreter it raised ``ImportError`` --
which is how ``tests/test_anon_type_location_properties.py`` failed when run on
its own while passing inside the full suite, where something had already
imported ``model``.

The oracle is a fresh interpreter per module, never this process (whose
``sys.modules`` already holds everything). The population is every
abicheck module the ``model`` package imports from *outside* itself -- the
only modules that can close a cycle through ``model/__init__`` -- derived from
the tree's own import statements rather than listed by hand, plus every
module under ``model`` itself.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "abicheck" / "model"


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")
    return rel.removesuffix(".__init__")


def _external_imports_of_model() -> set[str]:
    """abicheck modules outside ``abicheck.model`` that ``model`` imports."""
    found: set[str] = set()
    for path in MODEL.rglob("*.py"):
        package = (
            _module_name(path)
            if path.name == "__init__.py"
            else _module_name(path).rsplit(".", 1)[0]
        )
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level and node.module:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - (node.level - 1)])
                target = f"{base}.{node.module}"
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("abicheck.")
            ):
                target = node.module
            else:
                continue
            if target.startswith("abicheck.") and not target.startswith(
                "abicheck.model"
            ):
                found.add(target)
    existing = set()
    for name in found:
        rel = Path(*name.split("."))
        if (ROOT / rel.with_suffix(".py")).is_file() or (
            ROOT / rel / "__init__.py"
        ).is_file():
            existing.add(name)
    return existing


def _population() -> list[str]:
    model_modules = {_module_name(p) for p in MODEL.rglob("*.py")}
    return sorted(model_modules | _external_imports_of_model())


def _first_import_failure(module: str) -> str | None:
    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    if proc.returncode == 0:
        return None
    tail = proc.stderr.strip().splitlines()[-1:] or ["<no stderr>"]
    return f"{module}: {tail[0]}"


def test_population_includes_the_known_cycle_participant() -> None:
    # Vacuity guard: the derived population must contain the module whose
    # cycle this test was written for, or the sweep below proves nothing.
    population = _population()
    assert "abicheck.name_classification" in population
    assert "abicheck.model.execution_cache" in population
    assert len(population) > 20


@pytest.mark.repo_scan
def test_every_model_reachable_module_imports_first_in_a_fresh_interpreter() -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        failures = [f for f in pool.map(_first_import_failure, _population()) if f]
    assert failures == [], "\n".join(failures)
