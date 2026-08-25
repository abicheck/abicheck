"""Public-facade and canonical-owner contracts for ADR-061 Phase 1."""

from __future__ import annotations

import ast
from pathlib import Path

import abicheck.aggregate as facade
import abicheck.aggregate_findings as findings_facade
import abicheck.workflows.aggregate as owner


def test_supported_aggregate_facade_exports_canonical_objects() -> None:
    """The documented old path delegates without wrapping or copying behavior."""
    for name in facade.__all__:
        assert getattr(facade, name) is getattr(owner, name)


def test_finding_facade_preserves_historical_public_names() -> None:
    for name in ("MANGLING_ITANIUM", "MANGLING_MSVC", "ProfileCheckFindings"):
        assert name in findings_facade.__all__
        assert hasattr(findings_facade, name)


def test_internal_aggregate_callers_do_not_import_compatibility_facades() -> None:
    package = Path(__file__).parents[1] / "abicheck"
    forbidden = {
        "abicheck.aggregate",
        "abicheck.aggregate_findings",
        "abicheck.aggregate_manifest",
    }
    offenders: list[str] = []
    facade_paths = {
        Path("aggregate.py"),
        Path("aggregate_findings.py"),
        Path("aggregate_manifest.py"),
    }
    for path in package.rglob("*.py"):
        relative = path.relative_to(package)
        if relative in facade_paths:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    source_package = ["abicheck", *relative.parent.parts]
                    prefix = source_package[: len(source_package) - node.level + 1]
                    imported = ".".join(
                        [*prefix, *(node.module or "").split(".")]
                    ).rstrip(".")
                else:
                    imported = node.module or ""
                if imported in forbidden:
                    offenders.append(f"{relative}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden:
                        offenders.append(f"{relative}:{node.lineno}")
    assert offenders == [], (
        f"internal modules import compatibility facades: {offenders}"
    )


def test_compatibility_facade_is_delegation_only() -> None:
    path = Path(facade.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for node in tree.body
    )
