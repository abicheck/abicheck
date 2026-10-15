"""File-size and license-header rules of the AI-readiness gate.

Split out of ``test_ai_readiness.py`` (which the gate's own 2000-line hard cap
would otherwise push over): one file-size rule with caps from
``architecture/modules.yaml``, debt and grandfather records, and the
new-files-only license header check.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from _canonical_lane import is_canonical_lane

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_ai_readiness.py"

pytestmark = pytest.mark.skipif(
    not is_canonical_lane(), reason="canonical Linux lane only — see tests/CLAUDE.md"
)


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


# ---------------------------------------------------------------------------
# One file-size rule: caps from architecture/modules.yaml, debt.yaml and the
# recorded grandfather list grandfather over-cap files, growth past a record
# fails, and nothing warns. License header: new files only.
# ---------------------------------------------------------------------------


def _write_lines(path, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"x{i} = {i}" for i in range(n)) + "\n", encoding="utf-8")


def _size_fixture(car, tmp_path, monkeypatch, *, debt_paths=(), grandfathered=None):
    """Point the file-size gate at a synthetic repo: `tmp_path` is ROOT, with
    its own debt ledger and grandfather record."""
    arch = tmp_path / "architecture"
    arch.mkdir()
    (arch / "modules.yaml").write_text(
        json.dumps({"limits": {"production": 800, "test": 1200}}), encoding="utf-8"
    )
    (arch / "debt.yaml").write_text(
        json.dumps({"files": [{"path": p} for p in debt_paths]}), encoding="utf-8"
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "ARCHITECTURE_DIR", arch)
    monkeypatch.setattr(car, "FILE_SIZE_GRANDFATHERED", dict(grandfathered or {}))
    monkeypatch.setattr(car, "LARGE_FILE_ALLOWLIST", frozenset())
    monkeypatch.setattr(
        car, "FIRST_PARTY_PY_ROOTS", (tmp_path / "scripts", tmp_path / "tests")
    )


def test_size_caps_are_read_from_modules_yaml(car, tmp_path):
    """The caps come from architecture/modules.yaml `limits`, not constants."""
    modules = tmp_path / "modules.json"
    modules.write_text(
        json.dumps({"limits": {"production": 333, "test": 444, "facade": 9}}),
        encoding="utf-8",
    )
    assert car._read_size_caps(modules) == {"production": 333, "test": 444}
    real = json.loads((car.ARCHITECTURE_DIR / "modules.yaml").read_text())
    assert car._read_size_caps(car.ARCHITECTURE_DIR / "modules.yaml") == {
        "production": real["limits"]["production"],
        "test": real["limits"]["test"],
    }


def test_size_caps_missing_limits_is_loud(car, tmp_path):
    modules = tmp_path / "modules.json"
    modules.write_text(json.dumps({"limits": {"production": 800}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="limits.production / limits.test"):
        car._read_size_caps(modules)


@pytest.mark.parametrize(
    ("rel", "lines", "debt", "recorded", "expect_error"),
    [
        ("scripts/over.py", 801, (), None, True),  # over cap, no grandfather
        ("scripts/at_cap.py", 800, (), None, False),  # exactly the cap is fine
        ("scripts/in_debt.py", 900, ("scripts/in_debt.py",), None, False),
        ("scripts/recorded.py", 900, (), 900, False),  # at its recorded length
        ("scripts/grown.py", 901, (), 900, True),  # grew past it
    ],
)
def test_production_size_cap_grandfathering(
    car, tmp_path, monkeypatch, rel, lines, debt, recorded, expect_error
):
    grandfathered = {rel: recorded} if recorded is not None else None
    _size_fixture(
        car, tmp_path, monkeypatch, debt_paths=debt, grandfathered=grandfathered
    )
    _write_lines(tmp_path / rel, lines)
    f = car.Findings()
    car.check_file_sizes(f)
    assert f.warnings == [], f"size findings never warn: {f.warnings}"
    assert bool(f.errors) is expect_error, f.errors
    if expect_error:
        assert any(rel in msg for _, msg in f.errors), f.errors


def test_test_files_use_the_test_cap(car, tmp_path, monkeypatch):
    """A test file gets the 1200-line test cap, not the production one."""
    _size_fixture(car, tmp_path, monkeypatch)
    _write_lines(tmp_path / "tests" / "test_ok.py", 1000)
    _write_lines(tmp_path / "tests" / "test_big.py", 1300)
    f = car.Findings()
    car.check_file_sizes(f)
    assert [msg.split(":")[0] for _, msg in f.errors] == ["tests/test_big.py"]


def test_file_between_cap_and_hard_limit_is_not_a_warning(car, tmp_path, monkeypatch):
    """The old 1500-line soft tier is gone: 1700 lines in a grandfathered file
    produces no warning at all."""
    _size_fixture(car, tmp_path, monkeypatch, grandfathered={"scripts/mid.py": 1700})
    _write_lines(tmp_path / "scripts" / "mid.py", 1700)
    f = car.Findings()
    car.check_file_sizes(f)
    assert f.errors == [] and f.warnings == []


def test_hard_limit_still_errors_even_for_a_debt_entry(car, tmp_path, monkeypatch):
    _size_fixture(car, tmp_path, monkeypatch, debt_paths=("scripts/huge.py",))
    _write_lines(tmp_path / "scripts" / "huge.py", car.ERROR_LINES + 1)
    f = car.Findings()
    car.check_file_sizes(f)
    assert any("hard limit" in msg for _, msg in f.errors), f.errors


def test_grandfathered_record_points_at_real_files(car):
    """Every recorded over-cap file still exists and is still over its cap, or
    it is a stale record to delete."""
    for rel, recorded in car.FILE_SIZE_GRANDFATHERED.items():
        path = car.ROOT / rel
        assert path.is_file(), f"grandfathered file missing: {rel}"
        assert (
            recorded
            > car._read_size_caps(car.ARCHITECTURE_DIR / "modules.yaml")["production"]
        ), rel


def _git(cwd, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _license_repo(car, tmp_path, monkeypatch):
    """A git repo whose base commit holds one header-less file (grandfathered)."""
    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "old.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "PKG", pkg)
    return base


_HEADER = "# SPDX-License-Identifier: Apache-2.0\n"


def test_license_header_flags_new_untracked_file_only(car, tmp_path, monkeypatch):
    base = _license_repo(car, tmp_path, monkeypatch)
    monkeypatch.setenv("ARCHITECTURE_BASE", base)
    (tmp_path / "abicheck" / "fresh.py").write_text("y = 2\n", encoding="utf-8")
    f = car.Findings()
    car.check_license_header(f)
    assert f.warnings == []
    assert [msg.split(":")[0] for _, msg in f.errors] == ["abicheck/fresh.py"], f.errors


def test_license_header_flags_file_added_since_base(car, tmp_path, monkeypatch):
    base = _license_repo(car, tmp_path, monkeypatch)
    (tmp_path / "abicheck" / "added.py").write_text("y = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "add")
    monkeypatch.setenv("ARCHITECTURE_BASE", base)
    f = car.Findings()
    car.check_license_header(f)
    assert [msg.split(":")[0] for _, msg in f.errors] == ["abicheck/added.py"], f.errors


def test_license_header_accepts_new_file_with_header(car, tmp_path, monkeypatch):
    base = _license_repo(car, tmp_path, monkeypatch)
    monkeypatch.setenv("ARCHITECTURE_BASE", base)
    (tmp_path / "abicheck" / "fresh.py").write_text(
        _HEADER + "y = 2\n", encoding="utf-8"
    )
    f = car.Findings()
    car.check_license_header(f)
    assert f.errors == [] and f.warnings == []


def test_license_header_empty_base_never_flags_existing_files(
    car, tmp_path, monkeypatch
):
    """No base revision (CI's empty ARCHITECTURE_BASE) and no untracked file:
    the header-less existing file is grandfathered, not reported."""
    _license_repo(car, tmp_path, monkeypatch)
    monkeypatch.setenv("ARCHITECTURE_BASE", "")
    f = car.Findings()
    car.check_license_header(f)
    assert f.errors == [] and f.warnings == []


def test_findings_info_is_printed_but_never_counted(car, capsys):
    f = car.Findings()
    f.info("adr-status-sync", "commit heuristic")
    assert f.errors == [] and f.warnings == []
    assert car.Findings().report() == 0
    assert f.report() == 0
    assert "INFO:  commit heuristic" in capsys.readouterr().out
