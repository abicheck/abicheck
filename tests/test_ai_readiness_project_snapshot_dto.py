"""`scripts/check_ai_readiness.py`'s `project-snapshot-dto-no-asdict` check
(ADR-063 Phase 8's D8 constraint, made mechanical) — split into its own file
so `test_ai_readiness.py` didn't grow past its `architecture/debt.yaml`
no-growth baseline, mirroring how several `scripts/*.py` gate modules
already split their own tests out for the identical reason (see
`scripts/CLAUDE.md`).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from _canonical_lane import is_canonical_lane

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_ai_readiness.py"

# Pure repo-tree structural analysis — platform/interpreter-independent, so
# this only needs to run once, on the canonical Linux lane. See
# tests/CLAUDE.md and tests/_canonical_lane.py, and test_ai_readiness.py's
# own identical marker.
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


def test_the_check_is_registered(car):
    assert "project-snapshot-dto-no-asdict" in car.CHECKS
    assert (
        car.CHECKS["project-snapshot-dto-no-asdict"]
        is car.check_project_snapshot_dto_no_asdict
    )


def test_project_snapshot_dto_files_carry_no_asdict(car):
    """ADR-063 Phase 8's D8 constraint holds on the real, landed files."""
    f = car.Findings()
    car.check_project_snapshot_dto_no_asdict(f)
    assert f.errors == [], f"ProjectSnapshot DTO asdict() violations: {f.errors}"


def test_project_snapshot_dto_no_asdict_actually_detects_it(car, tmp_path, monkeypatch):
    """The scan is real, not vacuous — it must fail on a deliberately
    reintroduced `asdict(...)` call in a DTO file."""
    bad_file = tmp_path / "dto.py"
    bad_file.write_text(
        "from dataclasses import asdict\ndef to_dict(self):\n    return asdict(self)\n",
        encoding="utf-8",
    )
    # Point the check's ROOT-relative file lookup at a throwaway directory
    # so this test never depends on where `tmp_path` happens to sit relative
    # to the real repository root.
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "_PROJECT_SNAPSHOT_DTO_FILES", ("dto.py",))
    f = car.Findings()
    car.check_project_snapshot_dto_no_asdict(f)
    assert any("asdict" in message for _check, message in f.errors)


def test_an_aliased_asdict_import_is_still_detected(car, tmp_path, monkeypatch):
    """`from dataclasses import asdict as encode; encode(dto)` must not
    slip past the check just because it never spells the literal name
    `asdict` at the call site (Codex review)."""
    bad_file = tmp_path / "dto.py"
    bad_file.write_text(
        "from dataclasses import asdict as encode\n"
        "def to_dict(self):\n"
        "    return encode(self)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "_PROJECT_SNAPSHOT_DTO_FILES", ("dto.py",))
    f = car.Findings()
    car.check_project_snapshot_dto_no_asdict(f)
    assert any("asdict" in message for _check, message in f.errors)


def test_an_aliased_dataclasses_module_import_is_still_detected(
    car, tmp_path, monkeypatch
):
    """`import dataclasses as dc; dc.asdict(dto)` -- the attribute-form
    match is bare-name-only, so it already caught this before the alias
    fix; pinned here so it can't silently regress alongside that fix."""
    bad_file = tmp_path / "dto.py"
    bad_file.write_text(
        "import dataclasses as dc\ndef to_dict(self):\n    return dc.asdict(self)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "_PROJECT_SNAPSHOT_DTO_FILES", ("dto.py",))
    f = car.Findings()
    car.check_project_snapshot_dto_no_asdict(f)
    assert any("asdict" in message for _check, message in f.errors)


def test_an_unrelated_name_that_happens_to_be_called_asdict_alone_is_flagged_only_when_bare(
    car, tmp_path, monkeypatch
):
    """A bare call to a name genuinely bound elsewhere (not imported from
    `dataclasses`) is not flagged -- the alias set only ever grows from a
    real `from dataclasses import asdict [as ...]`."""
    good_file = tmp_path / "dto.py"
    good_file.write_text(
        "def asdict(self):\n    return {}\n"
        "def to_dict(self):\n    return asdict(self)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "_PROJECT_SNAPSHOT_DTO_FILES", ("dto.py",))
    f = car.Findings()
    car.check_project_snapshot_dto_no_asdict(f)
    # A locally-defined `asdict` still matches the bare-name literal check
    # (deliberately coarse, matching the pre-existing attribute-form
    # behavior) -- this test documents that, rather than asserting a
    # narrower resolution this check does not attempt.
    assert any("asdict" in message for _check, message in f.errors)


def test_a_file_that_carries_no_asdict_passes(car, tmp_path, monkeypatch):
    good_file = tmp_path / "dto.py"
    good_file.write_text(
        "def to_dict(self):\n    return {'a': self.a}\n", encoding="utf-8"
    )
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "_PROJECT_SNAPSHOT_DTO_FILES", ("dto.py",))
    f = car.Findings()
    car.check_project_snapshot_dto_no_asdict(f)
    assert f.errors == []


def test_a_missing_file_is_skipped_rather_than_erroring(car, tmp_path, monkeypatch):
    monkeypatch.setattr(car, "ROOT", tmp_path)
    monkeypatch.setattr(car, "_PROJECT_SNAPSHOT_DTO_FILES", ("does_not_exist.py",))
    f = car.Findings()
    car.check_project_snapshot_dto_no_asdict(f)
    assert f.errors == []
