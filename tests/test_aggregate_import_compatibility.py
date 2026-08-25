"""Public-facade and canonical-owner contracts for ADR-061 Phase 1."""

from __future__ import annotations

import ast
from pathlib import Path

import abicheck.aggregate as facade
import abicheck.workflows.aggregate as owner


def test_supported_aggregate_facade_exports_canonical_objects() -> None:
    """The documented old path delegates without wrapping or copying behavior."""
    for name in facade.__all__:
        assert getattr(facade, name) is getattr(owner, name)


def test_internal_aggregate_callers_do_not_import_compatibility_facades() -> None:
    package = Path(__file__).parents[1] / "abicheck"
    forbidden = {
        "abicheck.aggregate",
        "abicheck.aggregate_findings",
        "abicheck.aggregate_manifest",
    }
    offenders: list[str] = []
    for path in package.rglob("*.py"):
        if path.name in {
            "aggregate.py",
            "aggregate_findings.py",
            "aggregate_manifest.py",
        }:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden:
                offenders.append(f"{path.relative_to(package)}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden:
                        offenders.append(f"{path.relative_to(package)}:{node.lineno}")
    assert offenders == []


def test_compatibility_facade_is_delegation_only() -> None:
    path = Path(facade.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for node in tree.body
    )
