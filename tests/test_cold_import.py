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

"""Every ``abicheck`` module imports when it is the *first* import.

A circular import between a package ``__init__`` and a module outside it only
fails in one import order. ``abicheck.name_classification`` imports
``abicheck.model.execution_cache``, which runs ``abicheck/model/__init__``,
which loaded ``model`` modules that bound ``name_classification``'s names at
import time -- so ``import abicheck.name_classification`` (and three modules
that import it before ``model``) failed with a circular ``ImportError``, while
the test suite, whose earlier modules always imported ``abicheck.model``
first, never saw it.

The invariant is stated over the whole package, not the one module that
broke: each module is imported in its own fresh interpreter, which is the
only way to make it the first import. The oracle is the interpreter itself
(the import succeeds or it does not), and ``test_the_check_detects_the_cycle``
proves the same checker rejects a minimal package with the exact
``__init__``-cycle shape, so a green run cannot mean the check ran nothing.
"""

from __future__ import annotations

import os
import pkgutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_scan

ROOT = Path(__file__).resolve().parent.parent


def _cold_import_failures(
    modules: list[str], *, cwd: Path, pythonpath: str | None = None
) -> dict[str, str]:
    """``{module: last stderr line}`` for every module that fails to import
    as the first import of a fresh interpreter."""
    env = dict(os.environ)
    if pythonpath is not None:
        env["PYTHONPATH"] = pythonpath

    def attempt(module: str) -> tuple[str, int, str]:
        proc = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        lines = proc.stderr.strip().splitlines()
        return module, proc.returncode, lines[-1] if lines else ""

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
        results = list(pool.map(attempt, modules))
    return {module: err for module, rc, err in results if rc != 0}


def _abicheck_modules() -> list[str]:
    import abicheck

    return sorted(
        info.name for info in pkgutil.walk_packages(abicheck.__path__, "abicheck.")
    )


def test_every_module_imports_first_in_a_fresh_interpreter(tmp_path: Path) -> None:
    modules = _abicheck_modules()
    # Vacuity guard: the walk must actually have found the package.
    assert len(modules) > 500, len(modules)
    # cwd outside the checkout, so the installed/editable package is what
    # resolves -- the same way a user's `import abicheck.x` does.
    failures = _cold_import_failures(modules, cwd=tmp_path)
    assert not failures, failures


def test_the_check_detects_the_cycle(tmp_path: Path) -> None:
    """Negative control: the ``__init__``-cycle shape that broke
    ``name_classification`` -- a leaf outside the package that imports a
    package submodule, while the package's ``__init__`` binds the leaf's
    names at import -- must be reported for the leaf, and only for it."""
    pkg = tmp_path / "pkg"
    (pkg / "inner").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "leaf.py").write_text(
        "from .inner.helper import decorate\n\n@decorate\ndef f():\n    return 1\n"
    )
    (pkg / "inner" / "__init__.py").write_text("from ..leaf import f\n")
    (pkg / "inner" / "helper.py").write_text("def decorate(fn):\n    return fn\n")
    failures = _cold_import_failures(
        ["pkg.leaf", "pkg.inner", "pkg.inner.helper"],
        cwd=tmp_path,
        pythonpath=str(tmp_path),
    )
    assert set(failures) == {"pkg.leaf"}, failures
    assert "circular import" in failures["pkg.leaf"], failures
