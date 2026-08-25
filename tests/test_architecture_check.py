"""Focused tests for ADR-061's machine-readable architecture gate."""

from __future__ import annotations

import json
from pathlib import Path

import scripts.check_architecture as architecture
from scripts.check_architecture import check_repository


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(tmp_path: Path) -> Path:
    layers = {
        "model": {"path": "abicheck/model", "may_import": []},
        "storage": {"path": "abicheck/storage", "may_import": ["model"]},
        "extract": {
            "path": "abicheck/extract",
            "may_import": ["model", "storage"],
        },
        "compare": {"path": "abicheck/compare", "may_import": ["model"]},
        "policy": {
            "path": "abicheck/policy",
            "may_import": ["model", "compare"],
        },
        "workflows": {
            "path": "abicheck/workflows",
            "may_import": ["model", "storage", "extract", "compare", "policy"],
        },
        "report": {
            "path": "abicheck/report",
            "may_import": ["model", "compare", "policy", "workflows"],
        },
        "frontends": {
            "path": "abicheck/frontends",
            "may_import": ["model", "workflows", "report"],
        },
    }
    modules = {
        "schema_version": 1,
        "limits": {
            "production": 8,
            "test": 12,
            "facade": 6,
            "package_agents": 15,
            "root_agents": 35,
        },
        "layers": layers,
        "public_root_surfaces": ["abicheck.errors"],
        "facades": [],
        "frozen_root_families": {"cli_": ["cli_old.py"]},
        "legacy_root_directories": [],
        "legacy_generic_modules": [],
        "parser_or_catalog_roots": [],
    }
    _write(tmp_path / "architecture/modules.yaml", json.dumps(modules))
    _write(
        tmp_path / "architecture/debt.yaml",
        json.dumps({"schema_version": 1, "files": []}),
    )
    _write(tmp_path / "abicheck/__init__.py")
    return tmp_path


def _add_package(root: Path, name: str, source: str = "") -> None:
    _write(root / f"abicheck/{name}/AGENTS.md", "# Instructions\n")
    _write(root / f"abicheck/{name}/__init__.py", source)


def _rules(root: Path) -> set[str]:
    return {finding.rule for finding in check_repository(root)}


def test_valid_miniature_tree_passes(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _add_package(root, "model", "VALUE = 1\n")
    _add_package(root, "compare", "from abicheck.model import VALUE\n")

    assert check_repository(root) == []


def test_new_forbidden_prefix_sibling_is_actionable(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/cli_more.py", "VALUE = 1\n")

    findings = check_repository(root)

    assert "frozen-root-family" in {finding.rule for finding in findings}
    assert any("cli_more.py" in finding.message for finding in findings)


def test_oversized_new_module_requires_no_adoption_exception(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _add_package(root, "model", "\n".join(f"V{i} = {i}" for i in range(9)))

    assert "new-file-size" in _rules(root)


def test_oversized_new_test_requires_no_adoption_exception(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(
        root / "tests/test_too_large.py",
        "\n".join(f"V{i} = {i}" for i in range(13)),
    )

    assert "new-test-size" in _rules(root)


def test_debt_baseline_cannot_grow(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    path = root / "abicheck/legacy.py"
    _write(path, "\n".join(f"V{i} = {i}" for i in range(10)) + "\n")
    debt = {
        "schema_version": 1,
        "files": [
            {
                "path": "abicheck/legacy.py",
                "baseline_lines": 9,
                "target": "model",
                "rule": "no_growth",
                "category": "legacy_monolith",
                "owner": "maintainers",
                "rationale": "Move it as a tested vertical slice.",
                "review_by": "2026-11-30",
            }
        ],
    }
    _write(root / "architecture/debt.yaml", json.dumps(debt))

    findings = check_repository(root)

    assert "debt-no-growth" in {finding.rule for finding in findings}
    assert any("adoption baseline 9" in finding.message for finding in findings)


def test_concurrent_growth_already_on_pr_base_is_not_attributed_to_branch(
    tmp_path: Path, monkeypatch
) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/legacy.py", "VALUE = 1\n" * 10)
    debt = {
        "schema_version": 1,
        "files": [
            {
                "path": "abicheck/legacy.py",
                "baseline_lines": 9,
                "target": "model",
                "rule": "no_growth",
                "category": "legacy_monolith",
                "owner": "maintainers",
                "rationale": "Move it as a tested vertical slice.",
                "review_by": "2026-11-30",
            }
        ],
    }
    _write(root / "architecture/debt.yaml", json.dumps(debt))
    monkeypatch.setattr(
        architecture, "_base_has_architecture_contract", lambda *_: True
    )
    monkeypatch.setattr(architecture, "_git_file_line_count", lambda *_: 10)

    findings = check_repository(root, base_revision="base")

    assert "debt-no-growth" not in {finding.rule for finding in findings}


def test_branch_growth_beyond_pr_base_still_fails(tmp_path: Path, monkeypatch) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/legacy.py", "VALUE = 1\n" * 10)
    debt = {
        "schema_version": 1,
        "files": [
            {
                "path": "abicheck/legacy.py",
                "baseline_lines": 9,
                "target": "model",
                "rule": "no_growth",
                "category": "legacy_monolith",
                "owner": "maintainers",
                "rationale": "Move it as a tested vertical slice.",
                "review_by": "2026-11-30",
            }
        ],
    }
    _write(root / "architecture/debt.yaml", json.dumps(debt))
    monkeypatch.setattr(
        architecture, "_base_has_architecture_contract", lambda *_: True
    )
    monkeypatch.setattr(architecture, "_git_file_line_count", lambda *_: 9)

    findings = check_repository(root, base_revision="base")

    assert "debt-no-growth" in {finding.rule for finding in findings}


def test_initial_adoption_accepts_concurrent_pre_contract_growth(
    tmp_path: Path, monkeypatch
) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/legacy.py", "VALUE = 1\n" * 10)
    debt = {
        "schema_version": 1,
        "files": [
            {
                "path": "abicheck/legacy.py",
                "baseline_lines": 9,
                "target": "model",
                "rule": "no_growth",
                "category": "legacy_monolith",
                "owner": "maintainers",
                "rationale": "Move it as a tested vertical slice.",
                "review_by": "2026-11-30",
            }
        ],
    }
    _write(root / "architecture/debt.yaml", json.dumps(debt))
    monkeypatch.setattr(
        architecture, "_base_has_architecture_contract", lambda *_: False
    )

    findings = check_repository(root, base_revision="base")

    assert "debt-no-growth" not in {finding.rule for finding in findings}


def test_unresolvable_base_cannot_bypass_no_growth(tmp_path: Path, monkeypatch) -> None:
    root = _tree(tmp_path)
    monkeypatch.setattr(
        architecture, "_base_has_architecture_contract", lambda *_: None
    )

    findings = check_repository(root, base_revision="missing")

    assert "base-revision" in {finding.rule for finding in findings}


def test_undeclared_cross_package_import_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _add_package(root, "model")
    _add_package(root, "extract")
    _add_package(root, "compare", "from abicheck.extract import read\n")

    findings = check_repository(root)

    assert "dependency-direction" in {finding.rule for finding in findings}
    assert any("compare -> extract" in finding.message for finding in findings)


def test_legacy_module_is_classified_by_its_target_owner(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["layers"]["model"]["legacy_paths"] = ["abicheck/legacy_model.py"]
    _write(root / "architecture/modules.yaml", json.dumps(config))
    _write(root / "abicheck/legacy_model.py", "VALUE = 1\n")
    _add_package(root, "workflows", "from abicheck.legacy_model import VALUE\n")

    assert check_repository(root) == []


def test_legacy_module_cannot_have_two_target_owners(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    for layer in ("model", "compare"):
        config["layers"][layer]["legacy_paths"] = ["abicheck/legacy.py"]
    _write(root / "architecture/modules.yaml", json.dumps(config))

    findings = check_repository(root)

    assert any(
        finding.rule == "schema" and "classified by both" in finding.message
        for finding in findings
    )


def test_migrated_package_cannot_import_legacy_facade(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _add_package(root, "workflows", "from abicheck.service import run_dump\n")

    assert "unclassified-import" in _rules(root)


def test_observed_cycle_is_reported_even_if_contract_is_tampered(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["layers"]["model"]["may_import"] = ["storage"]
    _write(root / "architecture/modules.yaml", json.dumps(config))
    _add_package(root, "model", "from abicheck.storage import load\n")
    _add_package(root, "storage", "from abicheck.model import VALUE\n")

    findings = check_repository(root)

    assert "dependency-cycle" in {finding.rule for finding in findings}


def test_new_generic_module_name_is_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _add_package(root, "model")
    _write(root / "abicheck/model/helpers.py", "VALUE = 1\n")

    assert "generic-module-name" in _rules(root)


def test_created_responsibility_package_needs_scoped_instructions(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/model/__init__.py", "VALUE = 1\n")

    findings = check_repository(root)

    assert "scoped-instructions" in {finding.rule for finding in findings}
    assert any("abicheck/model/" in finding.message for finding in findings)


def test_facade_requires_explicit_exports_and_delegation_only(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["facades"] = ["abicheck.legacy_api"]
    _write(root / "architecture/modules.yaml", json.dumps(config))
    _write(root / "abicheck/legacy_api.py", "def calculate():\n    return 1\n")

    rules = _rules(root)

    assert {"facade-exports", "facade-logic"} <= rules


def test_invalid_debt_path_and_review_date_fail_schema(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    debt = {
        "schema_version": 1,
        "files": [
            {
                "path": "../escape.py",
                "baseline_lines": 8,
                "target": "model",
                "rule": "no_growth",
                "category": "legacy",
                "owner": "maintainers",
                "rationale": "legacy",
                "review_by": "later",
            }
        ],
    }
    _write(root / "architecture/debt.yaml", json.dumps(debt))

    findings = check_repository(root)

    assert sum(finding.rule == "schema" for finding in findings) >= 2
