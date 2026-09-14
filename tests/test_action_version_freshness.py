"""Tests for scripts/check_ai_readiness.py's `action-version-freshness` check.

Split out of tests/test_ai_readiness.py so that module stays inside its
`architecture/debt.yaml` no-growth baseline: per CLAUDE.md, the way to shrink
a debt entry is to move responsibility to a properly-owned module, not to trim
the file to fit. This module owns exactly one check's tests.

The check's anchor -- `repo_facts.json`'s `project_version`, not
`latest_release` -- is itself asserted here
(`test_action_version_freshness_anchors_to_project_version_not_latest_release`),
since the two facts differ precisely while `main` is prepared for an
unpublished release, which is when a wrong anchor does damage.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from _canonical_lane import is_canonical_lane

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_ai_readiness.py"

pytestmark = pytest.mark.skipif(
    not is_canonical_lane(), reason="canonical Linux lane only — see tests/CLAUDE.md"
)


@pytest.fixture(scope="module")
def car():
    spec = importlib.util.spec_from_file_location("check_ai_readiness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_ai_readiness"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_action_version_freshness_catches_stale_reference(car, tmp_path, monkeypatch):
    import json

    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"latest_release": "0.4.0", "project_version": "0.5.0"}),
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").write_text(
        "uses: abicheck/abicheck@v0.3.0\n", encoding="utf-8"
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "DOCS", docs)
    f = car.Findings()
    car.check_action_version_freshness(f)
    assert any("v0.3.0" in msg and "0.5.0" in msg for _, msg in f.errors), f.errors


def test_action_version_freshness_passes_when_current(car, tmp_path, monkeypatch):
    import json

    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"latest_release": "0.4.0", "project_version": "0.5.0"}),
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").write_text(
        "uses: abicheck/abicheck@v0.5.0\n", encoding="utf-8"
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "DOCS", docs)
    f = car.Findings()
    car.check_action_version_freshness(f)
    assert f.errors == []


def test_action_version_freshness_anchors_to_project_version_not_latest_release(
    car, tmp_path, monkeypatch
):
    """While `main` is prepared for an unpublished release, a docs example
    must pin the version it documents, not the older published one.

    Guards the anchor choice itself: with the two facts deliberately
    disagreeing, a `@v<project_version>` reference passes and a
    `@v<latest_release>` one fails. A gate anchored to `latest_release`
    would invert both assertions, so neither can pass by accident.
    """
    import json

    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"latest_release": "0.5.0", "project_version": "0.6.0"}),
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "DOCS", docs)

    (tmp_path / "README.md").write_text(
        "uses: abicheck/abicheck@v0.6.0\n", encoding="utf-8"
    )
    f = car.Findings()
    car.check_action_version_freshness(f)
    assert f.errors == [], "the documented (project) version must be accepted"

    (tmp_path / "README.md").write_text(
        "uses: abicheck/abicheck@v0.5.0\n", encoding="utf-8"
    )
    f = car.Findings()
    car.check_action_version_freshness(f)
    assert any("v0.5.0" in msg and "0.6.0" in msg for _, msg in f.errors), f.errors


def test_action_version_freshness_exempts_adr_dir(car, tmp_path, monkeypatch):
    import json

    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"latest_release": "0.4.0", "project_version": "0.5.0"}),
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    adr_dir = docs / "contribute" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "001-historical.md").write_text(
        "uses: abicheck/abicheck@v0.3.0\n", encoding="utf-8"
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "DOCS", docs)
    # _ACTION_VERSION_EXEMPT_DIRS is computed from DOCS at module-load time
    # (like REQUIRED_CLAUDE_MD_DIRS etc.), so monkeypatching DOCS alone
    # doesn't retroactively change it — patch it directly, same pattern used
    # for the other module-level dir tuples in this file.
    monkeypatch.setattr(car, "_ACTION_VERSION_EXEMPT_DIRS", (adr_dir,))
    f = car.Findings()
    car.check_action_version_freshness(f)
    assert f.errors == []
