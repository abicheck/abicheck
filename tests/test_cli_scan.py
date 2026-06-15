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
    # Reported depth tracks the resolved method, not the requested mode (Codex).
    assert "depth=build" in res.output


def test_pr_deep_is_distinct_from_pr(runner, new_snap_compatible):
    # No --audit here: --audit would force the AUDIT preset and mask --mode.
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--mode",
            "pr-deep",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    # pr-deep keeps GRAPH depth and a distinct graph collect-mode (Codex review).
    assert payload["level"]["depth"] == "graph"
    assert payload["level"]["collect_mode"] == "graph-full"


def test_reported_depth_matches_resolved_source_method(runner, new_snap_compatible):
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--source-method",
            "s6",
            "--format",
            "json",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    # s6 reaches full depth — must not be reported as the pr-preset 'source'.
    assert payload["level"]["source_method"] == "s6"
    assert payload["level"]["depth"] == "full"


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


def _accidental_export_snap(tmp_path: Path) -> Path:
    # `secret` is exported but no public header declares it → exported_not_public.
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov", "_Z6secretv"),
    )
    return _write_snapshot(tmp_path / "lib.abi.json", snap)


def test_crosscheck_error_severity_gates_exit_code(runner, tmp_path):
    # A RISK-class check is advisory by default (exit 0) but gates once the
    # maintainer promotes it to error (ADR-035 UX step 7 / D6).
    p = _accidental_export_snap(tmp_path)
    advisory = runner.invoke(main, ["scan", "--binary", str(p), "--audit"])
    assert advisory.exit_code == 0, advisory.output

    gated = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(p),
            "--audit",
            "--crosscheck",
            "exported_not_public=error",
        ],
    )
    assert gated.exit_code == 2, gated.output


def test_crosscheck_warning_severity_does_not_gate(runner, tmp_path):
    p = _accidental_export_snap(tmp_path)
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(p),
            "--audit",
            "--crosscheck",
            "exported_not_public=warning",
        ],
    )
    assert res.exit_code == 0, res.output


def test_crosscheck_error_gates_even_with_clean_baseline(
    runner, tmp_path, baseline_snap
):
    # Baseline diff is clean (NO_CHANGE) but the promoted check still gates.
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov"), _func("bar", "_Z3barv")],
        elf=_elf("_Z3foov", "_Z3barv", "_Z6secretv"),
    )
    p = _write_snapshot(tmp_path / "new.abi.json", snap)
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(p),
            "--baseline",
            str(baseline_snap),
            "--crosscheck",
            "exported_not_public=error",
        ],
    )
    assert res.exit_code == 2, res.output


def _snap_with_build_flag(tmp_path: Path, name: str, value: str) -> Path:
    # Embed an L3 build-evidence pack carrying one ABI-relevant define, so a
    # baseline compare exercises the embedded-source diff path (no compiler).
    from abicheck.buildsource.build_evidence import BuildEvidence, BuildOption
    from abicheck.buildsource.pack import BuildSourcePack

    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov"),
    )
    snap.build_source = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(
            build_options=[
                BuildOption(key="define:FEATURE_X", value=value, abi_relevant=True)
            ]
        ),
    )
    return _write_snapshot(tmp_path / name, snap)


def test_baseline_compare_folds_embedded_source_findings(runner, tmp_path):
    # ABI-relevant build-flag drift lives only in the embedded L3 pack; the scan
    # baseline compare must route through prepare_embedded_build_source so the
    # finding folds into the verdict (Codex review L738). Write JSON to a file so
    # the assertion is immune to CliRunner's stdout/stderr capture behaviour.
    old = _snap_with_build_flag(tmp_path, "old.abi.json", "0")
    new = _snap_with_build_flag(tmp_path, "new.abi.json", "1")
    out = tmp_path / "scan.json"
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new),
            "--baseline",
            str(old),
            "--format",
            "json",
            "-o",
            str(out),
        ],
    )
    # A scan verdict exit code (0/2/4) — not a crash; the fold may reach API_BREAK.
    assert res.exit_code in (0, 2, 4), res.output
    payload = json.loads(out.read_text())
    # Without the fix the embedded pack is never diffed and every bucket is 0;
    # the fix folds the ABI-relevant define drift in (here: RISK + API_BREAK).
    d = payload["diff"]
    assert d["risk"] >= 1, d
    assert d["risk"] + d["api_break"] + d["breaking"] >= 1, d


def test_promoted_risk_verdict_matches_exit_code(runner, tmp_path, baseline_snap):
    # Baseline diff is clean but a promoted RISK check gates: verdict string must
    # not stay COMPATIBLE_WITH_RISK while the process exits 2 (Codex review).
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov"), _func("bar", "_Z3barv")],
        elf=_elf("_Z3foov", "_Z3barv", "_Z6secretv"),
    )
    p = _write_snapshot(tmp_path / "new.abi.json", snap)
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(p),
            "--baseline",
            str(baseline_snap),
            "--crosscheck",
            "exported_not_public=error",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 2, res.output
    payload = json.loads(res.output)
    assert payload["verdict"] == "API_BREAK"


def test_malformed_risk_rules_yaml_is_click_error(
    runner, tmp_path, new_snap_compatible
):
    bad = tmp_path / "rules.yml"
    # Invalid YAML (unbalanced brackets) → yaml.YAMLError, must be a clean CLI error.
    bad.write_text("risk_rules: { unclosed: [1, 2", encoding="utf-8")
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--audit",
            "--risk-rules",
            str(bad),
        ],
    )
    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert "cannot read --risk-rules" in res.output


def test_source_method_s2_is_rejected_as_unimplemented(runner, new_snap_compatible):
    # S2 has no collection backend yet — reject rather than overstate (Codex).
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--source-method",
            "s2",
            "--audit",
        ],
    )
    assert res.exit_code != 0
    assert "s2" in res.output and "not yet implemented" in res.output


def test_auto_seeded_empty_diff_uses_s0(runner, new_snap_compatible):
    # A *successful* empty diff (no-op PR) is a valid seed → auto picks s0/off,
    # distinct from a missing/failed seed which falls back to the preset (Codex).
    # `HEAD...HEAD` is an empty diff in this repo's git.
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--source-method",
            "auto",
            "--since",
            "HEAD",
            "--format",
            "json",
            "--audit",
        ],
    )
    if res.exit_code != 0 or "seed failed" in res.output:
        pytest.skip("git unavailable / not a repo in this environment")
    payload = json.loads(res.output)
    assert payload["level"]["source_method"] == "s0"
    assert payload["level"]["collect_mode"] == "off"


def test_auto_without_diff_seed_falls_back_to_preset(runner, new_snap_compatible):
    # auto + no --changed-path/--since seed must NOT collapse to s0/off — it falls
    # back to the mode preset so source evidence isn't silently skipped (Codex).
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--source-method",
            "auto",
            "--format",
            "json",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["level"]["source_method"] == "s5"
    assert payload["level"]["collect_mode"] == "source-changed"


def test_header_short_alias_works(runner, tmp_path, new_snap_compatible):
    # The --help example uses `-H`; the alias must actually parse (Codex review).
    header = tmp_path / "inc" / "w.h"
    header.parent.mkdir()
    header.write_text("#pragma pack(1)\nstruct W { int a; };\n", encoding="utf-8")
    res = runner.invoke(
        main,
        ["scan", "--binary", str(new_snap_compatible), "-H", str(header), "--audit"],
    )
    assert res.exit_code == 0, res.output
    assert "Pattern pre-scan facts" in res.output


def test_out_of_tree_compile_db_is_accepted(runner, tmp_path, new_snap_compatible):
    # An explicit --compile-db (out-of-tree, no --sources) must be threaded into
    # evidence collection, not ignored (Codex review). Empty DB → no compiler
    # needed; the run must still succeed and route through the build collection.
    cc = tmp_path / "compile_commands.json"
    cc.write_text("[]", encoding="utf-8")
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--compile-db",
            str(cc),
            "--source-method",
            "s1",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "collect-mode=build" in res.output


def test_malformed_build_config_yaml_is_click_error(
    runner, tmp_path, new_snap_compatible
):
    # Invalid --build-config YAML must surface as a clean CLI error, not a
    # traceback through embed_build_source/load_build_config (Codex review).
    src = tmp_path / "src"
    src.mkdir()
    bad = tmp_path / "abicheck.yml"
    bad.write_text("build: { system: [unclosed", encoding="utf-8")
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--sources",
            str(src),
            "--build-config",
            str(bad),
        ],
    )
    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert "build config" in res.output


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


def test_malformed_binary_input_is_click_error(runner, tmp_path):
    # Unrecognized input must surface as a clean CLI error, not a traceback.
    bad = tmp_path / "bad.abi.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    res = runner.invoke(main, ["scan", "--binary", str(bad), "--audit"])
    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert "Failed to load --binary" in res.output


def test_malformed_baseline_input_is_click_error(runner, tmp_path, new_snap_compatible):
    bad = tmp_path / "bad_base.abi.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    res = runner.invoke(
        main,
        ["scan", "--binary", str(new_snap_compatible), "--baseline", str(bad)],
    )
    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert "Failed to load --baseline" in res.output
