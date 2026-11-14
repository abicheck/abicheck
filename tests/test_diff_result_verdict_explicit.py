"""Every production ``DiffResult(...)`` states its verdict.

``DiffResult.verdict`` defaults to ``NO_CHANGE`` because the dataclass is
public API (``docs/use/python-api.md``) and callers build results with no
changes. Inside abicheck that default is a trap: a result constructed with
findings and no ``verdict=`` reads as "no change" until something recomputes
it. ``checker_types`` is a ``model`` module and cannot compute a verdict
(``policy`` owns that), so the guard is here: each construction site in
``abicheck/`` passes ``verdict=`` (or ``**kwargs``) explicitly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_scan

_ROOT = Path(__file__).resolve().parents[1] / "abicheck"


def _diff_result_names(tree: ast.Module) -> set[str]:
    names = {"DiffResult"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "DiffResult" and alias.asname:
                    names.add(alias.asname)
    return names


def _sites_without_verdict(source: str, label: str) -> list[str]:
    tree = ast.parse(source)
    names = _diff_result_names(tree)
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name not in names:
            continue
        keywords = {k.arg for k in node.keywords}
        # Only a plain positional argument is a guaranteed value: ``*()``
        # supplies none.
        positional = sum(not isinstance(a, ast.Starred) for a in node.args)
        if "verdict" in keywords or None in keywords or positional >= 5:
            continue
        missing.append(f"{label}:{node.lineno}")
    return missing


def test_every_production_diff_result_passes_its_verdict() -> None:
    missing: list[str] = []
    for path in sorted(_ROOT.rglob("*.py")):
        rel = path.relative_to(_ROOT.parent).as_posix()
        missing += _sites_without_verdict(path.read_text(encoding="utf-8"), rel)
    assert missing == []


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("DiffResult('1', '2', 'l', changes=[c])", True),
        ("from x import DiffResult as _D\n_D('1', '2', 'l')", True),
        ("m.DiffResult(old_version='1', new_version='2', library='l')", True),
        ("DiffResult(*(), '1', '2', 'l', [c])", True),
        ("DiffResult('1', '2', 'l', [c], v)", False),
        ("DiffResult('1', '2', 'l', verdict=v)", False),
        ("DiffResult(**fields)", False),
        ("BundleDiffResult('1', '2', 'l')", False),
    ],
)
def test_the_scan_recognises_each_construction_shape(
    source: str, flagged: bool
) -> None:
    assert bool(_sites_without_verdict(source, "t")) is flagged
