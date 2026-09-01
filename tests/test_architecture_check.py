"""Focused tests for ADR-061's machine-readable architecture gate."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

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
        },
        "layers": layers,
        "public_root_surfaces": ["abicheck.errors"],
        "facades": [],
        "frozen_root_families": {"cli_": ["cli_old.py"]},
        "legacy_root_directories": [],
        "legacy_root_modules": ["__init__.py"],
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


def test_package_initializer_relative_import_stays_in_package(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _add_package(root, "model", "from .facts import VALUE\n")
    _write(root / "abicheck/model/facts.py", "VALUE = 1\n")

    assert check_repository(root) == []


def test_flat_module_package_collision_is_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _add_package(root, "model")
    _write(root / "abicheck/model.py", "VALUE = 1\n")
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["legacy_root_modules"].append("model.py")
    _write(root / "architecture/modules.yaml", json.dumps(config))

    assert "module-package-collision" in _rules(root)


def test_unlisted_flat_root_module_is_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/orchestration.py", "VALUE = 1\n")

    assert "root-module" in _rules(root)


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


def test_new_ordinary_file_cannot_claim_adoption_debt(
    tmp_path: Path, monkeypatch
) -> None:
    root = _tree(tmp_path)
    _write(root / "abicheck/legacy.py", "VALUE = 1\n" * 10)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["legacy_root_modules"].append("legacy.py")
    _write(root / "architecture/modules.yaml", json.dumps(config))
    debt = {
        "schema_version": 1,
        "files": [
            {
                "path": "abicheck/legacy.py",
                "baseline_lines": 10,
                "target": "model",
                "rule": "no_growth",
                "category": "legacy_monolith",
                "owner": "maintainers",
                "rationale": "Not actually adoption-era debt.",
                "review_by": "2026-11-30",
            }
        ],
    }
    _write(root / "architecture/debt.yaml", json.dumps(debt))
    monkeypatch.setattr(
        architecture, "_base_has_architecture_contract", lambda *_: True
    )
    monkeypatch.setattr(architecture, "_git_file_line_count", lambda *_: None)

    findings = check_repository(root, base_revision="base")

    assert "debt-exemption" in {finding.rule for finding in findings}


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


def test_legacy_package_initializer_is_classified_by_its_package_name(
    tmp_path: Path,
) -> None:
    """A legacy_paths entry naming a package's own ``__init__.py`` must
    classify that package's *import name* (``abicheck.legacy_pkg``), not the
    literal ``abicheck.legacy_pkg.__init__`` no import statement ever
    produces -- a real gap found migrating ``abicheck/policies/__init__.py``,
    which stayed silently unclassified under the naive ``.py``-stripping
    match until ``_layer_for`` learned to strip a trailing ``.__init__`` too.
    """
    root = _tree(tmp_path)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["layers"]["model"]["legacy_paths"] = ["abicheck/legacy_pkg/__init__.py"]
    config["legacy_root_directories"] = ["legacy_pkg"]
    _write(root / "architecture/modules.yaml", json.dumps(config))
    _write(root / "abicheck/legacy_pkg/__init__.py", "VALUE = 1\n")
    _add_package(root, "workflows", "from abicheck.legacy_pkg import VALUE\n")

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


def test_facade_rejects_executable_assignment(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["facades"] = ["abicheck.legacy_api"]
    _write(root / "architecture/modules.yaml", json.dumps(config))
    _write(root / "abicheck/legacy_api.py", "__all__ = []\nIMPL = build_impl()\n")

    assert "facade-logic" in _rules(root)


def test_facade_allows_import_only_type_checking_block(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["facades"] = ["abicheck.legacy_api"]
    _write(root / "architecture/modules.yaml", json.dumps(config))
    _write(
        root / "abicheck/legacy_api.py",
        "from typing import TYPE_CHECKING\n__all__ = []\nif TYPE_CHECKING:\n    from abicheck.errors import Error\n",
    )

    assert "facade-logic" not in _rules(root)


def test_oversized_package_instructions_are_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _add_package(root, "model")
    _write(root / "abicheck/model/AGENTS.md", "instruction\n" * 16)

    assert "agents-size" in _rules(root)


def test_legacy_source_import_direction_is_enforced(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["layers"]["model"]["legacy_paths"] = ["abicheck/legacy_model.py"]
    _write(root / "architecture/modules.yaml", json.dumps(config))
    _write(root / "abicheck/legacy_model.py", "from abicheck.workflows import run\n")
    config["legacy_root_modules"].append("legacy_model.py")
    _write(root / "architecture/modules.yaml", json.dumps(config))
    _add_package(root, "workflows")

    assert "dependency-direction" in _rules(root)


def test_bare_dot_import_of_forbidden_submodule_is_enforced(tmp_path: Path) -> None:
    """`from . import x` / `from .. import x` must not blind the checker to
    a real cross-layer import (Codex review finding, abicheck/abicheck#903:
    ``buildsource/template_graph.py``/``virtual_dispatch_graph.py`` both
    imported the compare-owned ``diff_cxx_rules`` via
    ``from .. import diff_cxx_rules`` while classified `extract` via
    `legacy_paths` (not physically migrated under `abicheck/extract/`), and
    ``check_architecture.py`` reported zero errors -- its import resolver
    only ever looked at ``node.module``, which is empty for this bare-dot
    form, so the target collapsed to the enclosing package and the actual
    imported name (``diff_cxx_rules`` here, potentially itself a submodule,
    not merely a symbol in the package's own ``__init__.py``) was silently
    dropped. Mirrors `test_legacy_source_import_direction_is_enforced`'s
    legacy-path shape exactly -- a `migrated_source` importer trips a
    different rule (`unclassified-import`) pre-fix, since that rule alone
    already catches an import resolving to no layer; a `legacy_paths`
    importer has no such fallback, which is what made this manifest as
    silence in the real PR rather than a differently-worded finding.
    """
    root = _tree(tmp_path)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["layers"]["extract"]["legacy_paths"] = ["abicheck/legacy_extract.py"]
    config["layers"]["compare"]["legacy_paths"] = ["abicheck/legacy_compare.py"]
    config["legacy_root_modules"].extend(["legacy_extract.py", "legacy_compare.py"])
    _write(root / "architecture/modules.yaml", json.dumps(config))
    _write(root / "abicheck/legacy_compare.py", "VALUE = 1\n")
    _write(root / "abicheck/legacy_extract.py", "from . import legacy_compare\n")

    assert "dependency-direction" in _rules(root)


def test_absolute_import_of_forbidden_submodule_is_enforced(tmp_path: Path) -> None:
    """The identical bug, in the absolute-import form (Codex review finding,
    abicheck/abicheck#903, on the bare-dot fix itself): `from abicheck import
    legacy_compare` has a nonempty `node.module` (`"abicheck"`), so the
    narrower fix above -- gated on `node.module` being empty -- never applied
    the `target.<name>` expansion here, leaving this shape exactly as
    invisible to `dependency-direction` as the relative form was pre-fix.
    Same `legacy_paths` shape as the bare-dot test, only the import spelling
    differs.
    """
    root = _tree(tmp_path)
    config = json.loads((root / "architecture/modules.yaml").read_text())
    config["layers"]["extract"]["legacy_paths"] = ["abicheck/legacy_extract.py"]
    config["layers"]["compare"]["legacy_paths"] = ["abicheck/legacy_compare.py"]
    config["legacy_root_modules"].extend(["legacy_extract.py", "legacy_compare.py"])
    _write(root / "architecture/modules.yaml", json.dumps(config))
    _write(root / "abicheck/legacy_compare.py", "VALUE = 1\n")
    _write(root / "abicheck/legacy_extract.py", "from abicheck import legacy_compare\n")

    assert "dependency-direction" in _rules(root)


def test_bare_dot_import_of_own_symbol_is_not_a_false_violation(
    tmp_path: Path,
) -> None:
    """The fix above must not turn every ordinary `from . import x` into a
    spurious finding merely because `x` happens to share a name with some
    submodule elsewhere -- a same-layer bare-dot import (a package's own
    `__init__.py` re-export, or a genuine same-package submodule) stays
    silent, matching what a real repo-wide check found before trusting this
    fix (zero new false positives across the whole codebase).

    Covers both shapes CodeRabbit review flagged as needing separate
    coverage: a symbol defined directly in `__init__.py` (`SOME_CONSTANT`)
    and a genuine same-layer submodule (`helper.py`) -- the fix's own
    `target.<name>` path resolves the latter to a real module in the same
    layer as the importer, which must stay silent exactly like the former.
    """
    root = _tree(tmp_path)
    _add_package(root, "extract", "SOME_CONSTANT = 1\n")
    _write(root / "abicheck/extract/helper.py")
    _write(root / "abicheck/extract/reader.py", "from . import SOME_CONSTANT\n")
    _write(
        root / "abicheck/extract/submodule_reader.py",
        "from . import helper\n",
    )

    assert check_repository(root) == []


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


# ---------------------------------------------------------------------------
# --base / $ARCHITECTURE_BASE / local merge-base auto-resolution
# ---------------------------------------------------------------------------


def test_local_merge_base_returns_none_when_git_command_fails(tmp_path: Path) -> None:
    # A directory that isn't a git repository at all: `git merge-base` exits
    # nonzero rather than raising, and this must degrade to None rather than
    # propagate that failure.
    assert architecture._local_merge_base_with_main(tmp_path) is None


def test_local_merge_base_returns_none_when_git_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(architecture.subprocess, "run", _raise)

    assert architecture._local_merge_base_with_main(tmp_path) is None


def test_local_merge_base_returns_stripped_sha_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd == ["git", "merge-base", "HEAD", "origin/main"]
        return subprocess.CompletedProcess(cmd, 0, stdout="deadbeef\n", stderr="")

    monkeypatch.setattr(architecture.subprocess, "run", _fake_run)

    assert architecture._local_merge_base_with_main(tmp_path) == "deadbeef"


def test_local_merge_base_falls_back_to_local_main_without_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A checkout with no `origin` remote-tracking ref at all (renamed/removed
    # remote, a bare local clone) but a resolvable local `main` branch must
    # still get a base rather than silently reporting nothing (Codex review).
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[-1] == "origin/main":
            return subprocess.CompletedProcess(
                cmd, 128, stdout="", stderr="unknown revision"
            )
        assert cmd == ["git", "merge-base", "HEAD", "main"]
        return subprocess.CompletedProcess(cmd, 0, stdout="cafef00d\n", stderr="")

    monkeypatch.setattr(architecture.subprocess, "run", _fake_run)

    assert architecture._local_merge_base_with_main(tmp_path) == "cafef00d"
    assert [c[-1] for c in calls] == ["origin/main", "main"]


def test_local_merge_base_returns_none_when_neither_ref_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd, 128, stdout="", stderr="unknown revision"
        )

    monkeypatch.setattr(architecture.subprocess, "run", _fake_run)

    assert architecture._local_merge_base_with_main(tmp_path) is None


def _capture_base_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    captured: dict[str, object] = {}

    def _fake_check_repository(root: Path, *, base_revision: str | None = None):
        captured["base_revision"] = base_revision
        return []

    monkeypatch.setattr(architecture, "check_repository", _fake_check_repository)
    return captured


def test_main_prefers_explicit_base_over_env_and_auto_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_base_revision(monkeypatch)
    monkeypatch.setenv("ARCHITECTURE_BASE", "env-sha")
    monkeypatch.setattr(
        architecture, "_local_merge_base_with_main", lambda root: "auto-sha"
    )

    assert architecture.main(["--root", str(tmp_path), "--base", "explicit-sha"]) == 0
    assert captured["base_revision"] == "explicit-sha"


def test_main_prefers_env_over_auto_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_base_revision(monkeypatch)
    monkeypatch.setenv("ARCHITECTURE_BASE", "env-sha")
    monkeypatch.setattr(
        architecture, "_local_merge_base_with_main", lambda root: "auto-sha"
    )

    assert architecture.main(["--root", str(tmp_path)]) == 0
    assert captured["base_revision"] == "env-sha"


def test_main_falls_back_to_auto_detected_merge_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_base_revision(monkeypatch)
    monkeypatch.delenv("ARCHITECTURE_BASE", raising=False)
    monkeypatch.setattr(
        architecture, "_local_merge_base_with_main", lambda root: "auto-sha"
    )

    assert architecture.main(["--root", str(tmp_path)]) == 0
    assert captured["base_revision"] == "auto-sha"


def test_main_falls_back_to_none_when_nothing_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_base_revision(monkeypatch)
    monkeypatch.delenv("ARCHITECTURE_BASE", raising=False)
    monkeypatch.setattr(architecture, "_local_merge_base_with_main", lambda root: None)

    assert architecture.main(["--root", str(tmp_path)]) == 0
    assert captured["base_revision"] is None


def test_main_does_not_auto_detect_when_ci_sets_base_explicitly_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ci.yml sets $ARCHITECTURE_BASE unconditionally to
    # `github.event.pull_request.base.sha`, which is the empty string on a
    # `push`-to-`main`/`workflow_dispatch` run (no PR to read a base sha
    # from) -- as opposed to a bare local invocation, where the variable is
    # simply absent from the environment. Falling back to
    # `_local_merge_base_with_main` in the former case would resolve
    # `origin/main` to HEAD itself (the very ref just pushed) and silently
    # turn the debt-no-growth check into comparing every file against
    # itself (Codex review, fresh evidence) -- so an explicitly-empty
    # $ARCHITECTURE_BASE must fall through to the unscoped comparison
    # instead of ever calling the auto-detection helper at all.
    captured = _capture_base_revision(monkeypatch)
    monkeypatch.setenv("ARCHITECTURE_BASE", "")

    def _unexpected_call(root: Path) -> str:
        raise AssertionError(
            "_local_merge_base_with_main must not be called when "
            "$ARCHITECTURE_BASE is explicitly set to the empty string"
        )

    monkeypatch.setattr(architecture, "_local_merge_base_with_main", _unexpected_call)

    assert architecture.main(["--root", str(tmp_path)]) == 0
    assert captured["base_revision"] is None


def test_main_without_base_resolution_still_catches_real_violations(
    tmp_path: Path,
) -> None:
    """End-to-end (no mocking of check_repository): a real violation in a
    miniature tree is still reported when base auto-detection can't resolve
    anything (this tree isn't a git repository), matching the pre-existing
    unscoped-baseline behavior."""
    root = _tree(tmp_path)
    _add_package(root, "model")
    _write(
        root / "abicheck/model/oversized.py",
        "\n".join(f"x{i} = {i}" for i in range(20)),
    )

    exit_code = architecture.main(["--root", str(root)])

    assert exit_code == 1
