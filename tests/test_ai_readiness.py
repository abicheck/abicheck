"""Smoke tests for scripts/check_ai_readiness.py.

These verify that the script imports, that its check functions run end-to-end
against the live repository tree, and that the documented invariants (no
errors) still hold.  We deliberately exercise the live tree rather than a
fixture so the script's expectations match reality.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_ai_readiness.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_ai_readiness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_ai_readiness"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def car():
    return _load_module()


def test_script_imports(car):
    assert hasattr(car, "main")
    assert hasattr(car, "CHECKS")
    # All check names registered
    expected = {
        "file-size",
        "claude-md-coverage",
        "test-ratio",
        "future-annotations",
        "changekind-partition",
        "changekind-detector",
        "changekind-docs",
        "import-cycles",
        "mypy-baseline",
        "examples-ground-truth",
        "mkdocs-nav-coverage",
        "banned-imports",
        "license-header",
    }
    assert expected <= set(car.CHECKS)


def test_examples_ground_truth_in_sync(car):
    f = car.Findings()
    car.check_examples_ground_truth(f)
    assert f.errors == [], f"examples/ground_truth.json out of sync: {f.errors}"


def test_no_banned_imports(car):
    f = car.Findings()
    car.check_banned_imports(f)
    assert f.errors == [], f"Banned-import violations: {f.errors}"


def test_changekind_partition_holds(car):
    """The partition invariant documented in CLAUDE.md must hold."""
    f = car.Findings()
    car.check_changekind_partition(f)
    assert f.errors == [], f"ChangeKind partition broken: {f.errors}"


def test_claude_md_coverage_holds(car):
    f = car.Findings()
    car.check_claude_md_coverage(f)
    assert f.errors == [], f"Missing CLAUDE.md files: {f.errors}"


def test_no_import_cycles(car):
    f = car.Findings()
    car.check_import_cycles(f)
    assert f.errors == [], f"Import cycles detected: {f.errors}"


def test_no_hard_file_size_violations(car):
    """Files over ERROR_LINES must be in LARGE_FILE_ALLOWLIST."""
    f = car.Findings()
    car.check_file_sizes(f)
    # Allow warnings (allow-listed large files, soft-limit warnings) — but
    # any file-size ERROR means an un-allowlisted file blew past the hard
    # limit.
    assert f.errors == [], f"File-size hard-limit violations: {f.errors}"


def test_main_returns_zero_on_clean_tree(car, capsys):
    """End-to-end: running the script against the live tree should exit 0.

    We skip the slow mypy check here; it's exercised in CI where mypy is
    available.
    """
    rc = car.main(["--skip", "mypy-baseline"])
    assert rc == 0, capsys.readouterr().out


def test_examples_readme_sync_in_sync(car):
    """The live examples/README.md catalog must agree with ground_truth.json."""
    f = car.Findings()
    car.check_examples_readme_sync(f)
    assert f.errors == [], f"examples/README.md out of sync: {f.errors}"


def _write_synthetic_catalog(tmp_path, *, case02_verdict_cell):
    """Write a minimal ground_truth.json + README.md pair into tmp_path.

    Two single-library cases (one BREAKING, one COMPATIBLE/addition). The
    caller controls case02's verdict cell so a test can inject row drift.
    """
    import json

    (tmp_path / "ground_truth.json").write_text(
        json.dumps(
            {
                "verdicts": {
                    "case01_foo": {"expected": "BREAKING", "category": "breaking"},
                    "case02_bar": {"expected": "COMPATIBLE", "category": "addition"},
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "This directory contains **2 cases**.\n\n"
        "| BREAKING | 1 |\n"
        "| COMPATIBLE (addition) | 1 |\n\n"
        "| [01](case01_foo/README.md) | Foo | Breaking | 🔴 BREAKING |\n"
        f"| [02](case02_bar/README.md) | Bar | Addition | {case02_verdict_cell} |\n",
        encoding="utf-8",
    )


def test_examples_readme_sync_passes_on_correct_synthetic(car, tmp_path, monkeypatch):
    """A synthetic catalog whose rows match ground_truth yields no errors."""
    monkeypatch.setattr(car, "EXAMPLES", tmp_path)
    _write_synthetic_catalog(tmp_path, case02_verdict_cell="🟢 COMPATIBLE")
    f = car.Findings()
    car.check_examples_readme_sync(f)
    assert f.errors == [], f.errors


def test_examples_readme_sync_catches_swapped_row_verdict(car, tmp_path, monkeypatch):
    """A stale per-row verdict (counts unchanged) must fail — the drift the
    aggregate-count checks alone cannot see (Codex review, PR #318)."""
    monkeypatch.setattr(car, "EXAMPLES", tmp_path)
    # case02 is COMPATIBLE/addition in ground_truth, but its row claims BREAKING.
    # The distribution counts still tally, so only row-content parsing catches it.
    _write_synthetic_catalog(tmp_path, case02_verdict_cell="🔴 BREAKING")
    f = car.Findings()
    car.check_examples_readme_sync(f)
    assert any("case02_bar" in msg and "BREAKING" in msg for _, msg in f.errors), (
        f"expected a verdict-mismatch error for case02_bar, got {f.errors}"
    )
