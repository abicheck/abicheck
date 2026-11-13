"""abiforge stays a standalone tool: its import boundaries, checked on the AST.

abiforge is meant to move to its own repository and to evaluate other ABI
checkers, so three rules hold for every module under ``abiforge/``:

* it never imports ``tests`` (the tests consume abiforge, not the reverse);
* it never imports ``pytest`` (test glue lives in ``tests/_abiforge_pytest.py``);
* only ``abiforge/checkers/`` imports ``abicheck`` (the checker under test is
  reached through its adapter).

Imports inside string literals (the regression-test template in
``abiforge/run/cells.py``) are text, not imports, so the check reads the AST.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "abiforge"
MODULES = sorted(ROOT.rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _rel(path: Path) -> str:
    return path.relative_to(ROOT.parent).as_posix()


def test_the_package_has_modules() -> None:
    # A wrong ROOT would make every rule below vacuously true.
    assert len(MODULES) > 20, MODULES


@pytest.mark.parametrize("path", MODULES, ids=_rel)
def test_abiforge_never_imports_tests_or_pytest(path: Path) -> None:
    assert not _imported_roots(path) & {"tests", "pytest"}, _rel(path)


@pytest.mark.parametrize("path", MODULES, ids=_rel)
def test_only_checker_adapters_import_abicheck(path: Path) -> None:
    in_adapters = path.parent == ROOT / "checkers"
    assert in_adapters or "abicheck" not in _imported_roots(path), _rel(path)


def test_the_rules_see_a_real_import() -> None:
    """The AST walk reports imports at any depth and ignores string literals."""
    sample = Path(__file__).with_name("_abiforge_boundary_sample.tmp")
    sample.write_text(
        'TEXT = "import pytest"\n'
        "def f():\n"
        "    from abicheck.checker import compare\n"
        "    import tests.x\n",
        encoding="utf-8",
    )
    try:
        assert _imported_roots(sample) == {"abicheck", "tests"}
    finally:
        sample.unlink()
