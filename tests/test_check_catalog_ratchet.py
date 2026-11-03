"""Unit tests for scripts/check_catalog_ratchet.py and scripts/catalog_scope_diff.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ratchet = _load("check_catalog_ratchet")
scope_diff = _load("catalog_scope_diff")

BASE_LANES = {
    "debug-headers": {"c1": "PASS", "c2": "PASS", "c3": "XFAIL", "c4": "FAIL"},
    "build-source": {"c1": "PASS", "c4": "SKIP"},
}


def _baseline():
    return ratchet.baseline_from(BASE_LANES)


def _with(lane: str, **changes: str | None):
    lanes = {k: dict(v) for k, v in BASE_LANES.items()}
    for case, status in changes.items():
        if status is None:
            lanes[lane].pop(case)
        else:
            lanes[lane][case] = status
    return lanes


def test_baseline_records_sorted_pass_and_xfail_lists():
    doc = _baseline()
    assert doc["lanes"] == {
        "build-source": {"PASS": ["c1"], "XFAIL": []},
        "debug-headers": {"PASS": ["c1", "c2"], "XFAIL": ["c3"]},
    }


def test_unchanged_run_is_clean():
    assert ratchet.compare(_baseline(), BASE_LANES) == ([], [])


@pytest.mark.parametrize(
    ("case", "status", "expected"),
    [
        ("c1", "FAIL", "debug-headers: c1 PASS -> FAIL"),
        ("c2", "XFAIL", "debug-headers: c2 PASS -> XFAIL"),
        ("c2", "ERROR", "debug-headers: c2 PASS -> ERROR"),
        ("c3", "FAIL", "debug-headers: c3 XFAIL -> FAIL"),
    ],
)
def test_regression_fails(case, status, expected):
    regressions, _ = ratchet.compare(
        _baseline(), _with("debug-headers", **{case: status})
    )
    assert regressions == [expected]


@pytest.mark.parametrize(
    ("case", "status", "note"),
    [
        ("c4", "PASS", "debug-headers: c4 newly PASS (was untracked)"),
        ("c3", "PASS", "debug-headers: c3 newly PASS (was XFAIL)"),
        ("c4", "XFAIL", "debug-headers: c4 newly XFAIL"),
        ("c9", "PASS", "debug-headers: c9 newly PASS (was untracked)"),
    ],
)
def test_improvement_or_new_case_is_note_only(case, status, note):
    regressions, notes = ratchet.compare(
        _baseline(), _with("debug-headers", **{case: status})
    )
    assert (regressions, notes) == ([], [note])


def test_removed_case_is_note_only():
    regressions, notes = ratchet.compare(_baseline(), _with("debug-headers", c2=None))
    assert (regressions, notes) == ([], ["debug-headers: c2 (PASS) not in the report"])


def test_unknown_lane_is_note_and_missing_lane_fails():
    lanes = {"debug-headers": BASE_LANES["debug-headers"], "new-lane": {"c1": "PASS"}}
    regressions, notes = ratchet.compare(_baseline(), lanes)
    assert regressions == ["build-source: lane missing from the reports"]
    assert notes == ["new-lane: lane not in the baseline"]


def _report(tmp_path: Path, name: str, rows: list[dict]) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"results": rows}))
    return p


def test_main_exit_codes_and_write_baseline(tmp_path, capsys):
    rows = [{"case_id": "c1", "mode": "debug-headers", "status": "PASS"}]
    report = _report(tmp_path, "r.json", rows)
    base = tmp_path / "base.json"
    assert ratchet.main([str(report), "--baseline", str(base), "--write-baseline"]) == 0
    assert json.loads(base.read_text())["lanes"]["debug-headers"]["PASS"] == ["c1"]
    assert ratchet.main([str(report), "--baseline", str(base)]) == 0
    rows[0]["status"] = "FAIL"
    report = _report(tmp_path, "r2.json", rows)
    assert ratchet.main([str(report), "--baseline", str(base)]) == 1
    assert "c1 PASS -> FAIL" in capsys.readouterr().err


def test_main_prints_rerecord_command_on_improvement(tmp_path, capsys):
    base = tmp_path / "base.json"
    base.write_text(
        json.dumps(ratchet.baseline_from({"debug-headers": {"c1": "FAIL"}}))
    )
    report = _report(
        tmp_path,
        "r.json",
        [{"case_id": "c1", "mode": "debug-headers", "status": "PASS"}],
    )
    assert ratchet.main([str(report), "--baseline", str(base)]) == 0
    assert "--write-baseline" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("public_row", "differs"),
    [
        (("PASS", "BREAKING", "BREAKING"), False),
        (("FAIL", "COMPATIBLE", "BREAKING"), True),
        (("XFAIL", "BREAKING", "BREAKING"), True),
    ],
)
def test_scope_diff(public_row, differs):
    legacy = {
        "case180": ("PASS", "BREAKING", "BREAKING"),
        "only_legacy": ("PASS", "X", "X"),
    }
    public = {"case180": public_row}
    assert scope_diff.differences(legacy, public) == (["case180"] if differs else [])
    md = scope_diff.render(legacy, public)
    assert f"{int(differs)} of 1 cases differ." in md
    assert ("| case180 |" in md) is differs
