# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""End-to-end tests for the ADR-035 D3 ``scan`` orchestrator (G19.3, Phase 3).

Drives the Click command with JSON snapshot inputs (no compiler/castxml needed)
plus on-disk header files for the always-on pattern pre-scan, asserting the
deterministic level resolution, the always-on tier wiring, baseline comparison
exit codes, the budget guard, and the coverage report. Default lane.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    ScopeOrigin,
    Visibility,
)
from abicheck.serialization import snapshot_to_json


def _write_snapshot(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _elf(*names: str) -> ElfMetadata:
    return ElfMetadata(symbols=[ElfSymbol(name=n) for n in names])


def _func(name: str, mangled: str, *, origin=ScopeOrigin.PUBLIC_HEADER) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        origin=origin,
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def baseline_snap(tmp_path: Path) -> Path:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov"), _func("bar", "_Z3barv")],
        elf=_elf("_Z3foov", "_Z3barv"),
    )
    return _write_snapshot(tmp_path / "old.abi.json", snap)


@pytest.fixture
def new_snap_compatible(tmp_path: Path) -> Path:
    # Adds a new exported symbol (`baz`) — a backward-compatible addition.
    snap = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        from_headers=True,
        functions=[
            _func("foo", "_Z3foov"),
            _func("bar", "_Z3barv"),
            _func("baz", "_Z3bazv"),
        ],
        elf=_elf("_Z3foov", "_Z3barv", "_Z3bazv"),
    )
    return _write_snapshot(tmp_path / "new.abi.json", snap)


@pytest.fixture
def new_snap_breaking(tmp_path: Path) -> Path:
    # `bar` removed → a removed exported symbol is a hard ABI break.
    snap = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov"),
    )
    return _write_snapshot(tmp_path / "new_break.abi.json", snap)


def test_scan_compatible_exits_zero(runner, baseline_snap, new_snap_compatible):
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--baseline",
            str(baseline_snap),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Verdict: COMPATIBLE" in res.output
    assert "abicheck scan — pr mode" in res.output


def test_scan_breaking_exits_four(runner, baseline_snap, new_snap_breaking):
    res = runner.invoke(
        main,
        ["scan", "--binary", str(new_snap_breaking), "--baseline", str(baseline_snap)],
    )
    assert res.exit_code == 4, res.output
    assert "Verdict: BREAKING" in res.output


def test_scan_json_format_is_structured(runner, baseline_snap, new_snap_compatible):
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--baseline",
            str(baseline_snap),
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["mode"] == "pr"
    assert payload["level"]["source_method"] == "s5"
    assert payload["verdict"] == "COMPATIBLE"
    # Coverage is mandatory and explicit (ADR-035 §4a): L0-L2 rows always present.
    layers = {row["layer"] for row in payload["coverage"]}
    assert {"L0_binary", "L2_header", "pattern_scan"} <= layers


def test_audit_mode_runs_without_baseline(runner, tmp_path):
    # An exported symbol with no public declaration → exported_not_public (RISK).
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov", "_Z6secretv"),
    )
    p = _write_snapshot(tmp_path / "lib.abi.json", snap)
    res = runner.invoke(main, ["scan", "--binary", str(p), "--audit"])
    assert res.exit_code == 0, res.output
    assert "audit mode" in res.output
    assert "exported_not_public" in res.output


def test_audit_ignores_baseline_with_note(runner, baseline_snap, new_snap_compatible):
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--baseline",
            str(baseline_snap),
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "ignores --baseline" in res.output


def test_pattern_prescan_reports_facts(runner, tmp_path, new_snap_compatible):
    header = tmp_path / "inc" / "widget.h"
    header.parent.mkdir()
    header.write_text(
        "#pragma pack(push, 1)\nstruct W { int a; };\n#pragma pack(pop)\n",
        encoding="utf-8",
    )
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--headers",
            str(header),
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Pattern pre-scan facts" in res.output
    assert "pragma_pack" in res.output


def test_source_method_pin_overrides_mode_in_report(runner, new_snap_compatible):
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--mode",
            "baseline",
            "--source-method",
            "s1",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "source-method=s1" in res.output
    assert "collect-mode=build" in res.output


def test_auto_method_uses_changed_path_risk(runner, new_snap_compatible):
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--source-method",
            "auto",
            "--changed-path",
            "include/foo.h",
            "--format",
            "json",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["level"]["auto"] is True
    # include/** is the public-header signal → auto escalates to s5.
    assert payload["level"]["source_method"] == "s5"
    assert payload["risk"]["total"] == 50


def test_budget_overflow_fails(runner, new_snap_compatible):
    # A zero budget always overflows → the dedicated budget exit code (never a
    # silent scope shrink), ADR-035 D3.
    res = runner.invoke(
        main,
        ["scan", "--binary", str(new_snap_compatible), "--audit", "--budget", "0s"],
    )
    assert res.exit_code == 5, res.output
    assert "budget" in res.output.lower()


def test_invalid_crosscheck_key_is_usage_error(runner, new_snap_compatible):
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--audit",
            "--crosscheck",
            "nonsense=error",
        ],
    )
    assert res.exit_code != 0
    assert "unknown cross-check" in res.output


def test_crosscheck_off_disables_a_check(runner, tmp_path):
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov", "_Z6secretv"),
    )
    p = _write_snapshot(tmp_path / "lib.abi.json", snap)
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(p),
            "--audit",
            "--format",
            "json",
            "--crosscheck",
            "exported_not_public=off",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    counts = payload["crosscheck"]["counts_by_check"]
    assert "exported_not_public" not in counts


def test_multiple_binaries_rejected(runner, baseline_snap, new_snap_compatible):
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--binary",
            str(baseline_snap),
            "--audit",
        ],
    )
    assert res.exit_code != 0
    assert "single --binary" in res.output


def test_invalid_budget_string_is_bad_parameter(runner, new_snap_compatible):
    res = runner.invoke(
        main,
        ["scan", "--binary", str(new_snap_compatible), "--audit", "--budget", "soon"],
    )
    assert res.exit_code != 0
    assert "budget" in res.output.lower()
