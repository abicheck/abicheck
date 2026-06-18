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


def test_source_method_s2_runs_preprocessor_tier(runner, new_snap_compatible):
    # S2 is now the conditional preprocessor pre-scan (ADR-035 D2). With no L3
    # build evidence (snapshot-only input) it runs but reports the preprocessor
    # tier as skipped — honest coverage, not a hard reject and not a clean pass.
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--source-method",
            "s2",
            "--audit",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["preprocessor_scan"]["ran"] is False
    rows = {row["layer"]: row for row in payload["coverage"]}
    assert rows["preprocessor_scan"]["status"] == "not_collected"


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


def test_unseeded_s5_with_sources_emits_headers_only_advisory(
    runner, source_tree_with_compile_db, new_snap_compatible
):
    # ADR-035 P3: an unseeded s5 scan *with a source tree* falls back to a
    # headers-only replay; the result must carry an advisory naming
    # --since/--changed-path (text + JSON), not silently pay broad-replay cost.
    # The advisory rides the structured result so it never pollutes JSON stdout.
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--sources",
            str(source_tree_with_compile_db),
            "--source-method",
            "s5",
            "--format",
            "json",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert any("--since" in a for a in payload["advisories"])


def test_unseeded_s5_advisory_rendered_in_text_output(
    runner, source_tree_with_compile_db, new_snap_compatible
):
    # The advisory must also render as a `note:` line in the default text report
    # (not only JSON) so an interactive user sees it.
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--sources",
            str(source_tree_with_compile_db),
            "--source-method",
            "s5",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "note: no --since/--changed-path seed" in res.output


def test_unseeded_s5_without_sources_has_no_advisory(runner, new_snap_compatible):
    # No --sources tree → L4 replay never runs, so the headers-only advisory must
    # NOT fire (it would report a replay that never happened — CodeRabbit review).
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--source-method",
            "s5",
            "--format",
            "json",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert not any("--since" in a for a in payload["advisories"])


def test_seeded_s5_with_sources_has_no_headers_only_advisory(
    runner, source_tree_with_compile_db, new_snap_compatible
):
    # Because this test is seeded (--changed-path), the L4 replay runs in focused
    # mode rather than falling back to headers-only, so the P3 advisory must NOT
    # fire even with a source tree present.
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--sources",
            str(source_tree_with_compile_db),
            "--source-method",
            "s5",
            "--changed-path",
            "src/foo.cpp",
            "--format",
            "json",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert not any("--since" in a for a in payload["advisories"])


def test_deep_method_without_compile_db_emits_l3_advisory(
    runner, tmp_path, new_snap_compatible
):
    # A deep --source-method over a pristine source tree with no
    # compile_commands.json collects no L3, so L3/L4/L5 are skipped. The user who
    # asked for a deep level must get a pointed advisory naming the level and the
    # remedy — not just silent `not_collected` coverage rows (UX gap fix).
    bare = tmp_path / "bare_src"
    bare.mkdir()
    (bare / "foo.cpp").write_text("int foo() { return 0; }\n", encoding="utf-8")
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--sources",
            str(bare),
            "--source-method",
            "s5",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "needs an L3 compile database" in res.output


def test_compile_db_present_suppresses_l3_advisory(
    runner, source_tree_with_compile_db, new_snap_compatible
):
    # With a real compile_commands.json L3 collects cleanly, so the missing-L3
    # advisory must NOT fire. Seeded (--changed-path) so the unrelated headers-only
    # advisory also stays silent.
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--sources",
            str(source_tree_with_compile_db),
            "--source-method",
            "s5",
            "--changed-path",
            "foo.cpp",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "needs an L3 compile database" not in res.output


def test_binary_only_deep_method_has_no_l3_advisory(runner, new_snap_compatible):
    # A deep --source-method with NO source input (no --sources/--build-info) is the
    # obvious binary-only case; the advisory is gated on a source input so a plain
    # binary scan at a deep level isn't nagged.
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--source-method",
            "s1",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "needs an L3 compile database" not in res.output


def test_l2_coverage_hint_mentions_clang_backend():
    # The L2 skip hint must mention clang, not only castxml — the clang L2 backend
    # now covers header parsing (ADR-003 extension), so "needs castxml" was stale.
    from abicheck.cli_scan import _intrinsic_coverage

    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=False,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov"),
    )
    rows = _intrinsic_coverage(snap)
    l2 = next(r for r in rows if r["layer"] == "L2_header")
    assert l2["status"] == "skipped"
    assert "clang" in l2["detail"]


@pytest.mark.parametrize(
    "pack, expected",
    [
        (None, False),  # no embedded pack at all
        ([{"layer": "L3_build", "status": "present"}], True),  # plain-dict row
        ([{"layer": "L3_build", "status": "partial"}], True),  # partial still counts
        ([{"layer": "L3_build", "status": "not_collected"}], False),  # ran, empty
        ([{"layer": "L2_header", "status": "present"}], False),  # no L3 row at all
    ],
)
def test_l3_collected_branches(pack, expected):
    # Direct coverage of every _l3_collected branch: absent pack, L3 present/partial
    # (collected), L3 not_collected, and a pack with no L3 row. Rows expose both the
    # dataclass (.to_dict) and plain-dict shapes; cover the dataclass path too.
    from abicheck.cli_scan import _l3_collected

    class _Cov:
        def __init__(self, d):
            self._d = d

        def to_dict(self):
            return self._d

    class _Pack:
        def __init__(self, rows):
            self.manifest = type("M", (), {"coverage": rows})()

    class _Snap:
        def __init__(self, p):
            self.build_source = p

    rows = None if pack is None else [_Cov(d) for d in pack]
    assert _l3_collected(_Snap(_Pack(rows) if rows is not None else None)) is expected
    # Same rows as plain dicts (no .to_dict) — exercises the dict branch.
    if pack is not None:
        assert _l3_collected(_Snap(_Pack(list(pack)))) is expected


def test_level_implies_query_auto_enables_with_trusted_config(
    runner, tmp_path, source_tree_with_compile_db, new_snap_compatible
):
    # ADR-037 D4: an explicit (trusted) --config defining build.query + a pinned
    # deep level is consent to run the query — auto-enable it (advisory), no
    # separate --allow-build-query needed. --build-info carries a real compile DB
    # so the query string is never actually executed (build_info resolves first),
    # keeping the test hermetic while still exercising the auto-enable path.
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        "build:\n  query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON\n",
        encoding="utf-8",
    )
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--build-info",
            str(source_tree_with_compile_db),
            "--config",
            str(cfg),
            "--source-method",
            "s5",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "auto-enabled the query" in res.output


def test_level_implies_query_silent_without_trusted_config(
    runner, source_tree_with_compile_db, new_snap_compatible
):
    # No explicit --config → nothing is trusted for query execution, so the
    # auto-enable advisory must NOT fire even at a deep level.
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--build-info",
            str(source_tree_with_compile_db),
            "--source-method",
            "s5",
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "auto-enabled the query" not in res.output


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


# ── A1: --public-header-dir threads provenance into the scan ──────────────────


def test_public_provenance_set_lone_header_file_no_boundary(tmp_path):
    # A lone -H umbrella *file* with no directory cannot establish a public
    # boundary, so provenance stays off (origins remain UNKNOWN) — preserving the
    # prior default-scan behaviour (abicheck A1).
    from abicheck.cli_scan import _public_provenance_set

    umbrella = tmp_path / "all.hpp"
    umbrella.write_text("// umbrella\n", encoding="utf-8")
    files, dirs = _public_provenance_set([umbrella], [])
    assert files == []
    assert dirs == []


def test_public_provenance_set_dir_activates(tmp_path):
    # --public-header-dir establishes the boundary; a -H *file* rides along as an
    # explicit public header once a directory is present.
    from abicheck.cli_scan import _public_provenance_set

    inc = tmp_path / "include"
    inc.mkdir()
    umbrella = tmp_path / "all.hpp"
    umbrella.write_text("// umbrella\n", encoding="utf-8")
    files, dirs = _public_provenance_set([umbrella], [inc])
    assert files == [umbrella]
    assert dirs == [inc]


def test_public_provenance_set_header_dir_counts_as_boundary(tmp_path):
    # A directory passed via -H is itself a boundary, even without
    # --public-header-dir.
    from abicheck.cli_scan import _public_provenance_set

    hdr_dir = tmp_path / "pub"
    hdr_dir.mkdir()
    files, dirs = _public_provenance_set([hdr_dir], [])
    assert files == []
    assert dirs == [hdr_dir]


def test_scan_public_header_dir_forwarded_to_snapshot(
    monkeypatch, runner, new_snap_compatible, tmp_path
):
    # The CLI flag must reach the snapshot builder as public_header_dirs so
    # apply_provenance can classify origins (unlocking the leakage/RTTI/exported-
    # vs-public cross-checks).
    import abicheck.cli_scan as cs

    pub = tmp_path / "pub"
    pub.mkdir()
    captured: dict[str, object] = {}
    original = cs._build_new_snapshot

    def _spy(*args, **kwargs):
        captured["public_header_dirs"] = kwargs.get("public_header_dirs")
        captured["public_headers"] = kwargs.get("public_headers")
        return original(*args, **kwargs)

    monkeypatch.setattr(cs, "_build_new_snapshot", _spy)
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "--public-header-dir",
            str(pub),
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["public_header_dirs"] == [pub]
    assert captured["public_headers"] == []


def test_scan_lone_header_file_does_not_activate_provenance(
    monkeypatch, runner, new_snap_compatible, tmp_path
):
    # A lone -H file (no dir) must NOT activate provenance — empty sets reach the
    # snapshot builder so behaviour is unchanged.
    import abicheck.cli_scan as cs

    umbrella = tmp_path / "all.hpp"
    umbrella.write_text("// umbrella\n", encoding="utf-8")
    captured: dict[str, object] = {}
    original = cs._build_new_snapshot

    def _spy(*args, **kwargs):
        captured["public_header_dirs"] = kwargs.get("public_header_dirs")
        captured["public_headers"] = kwargs.get("public_headers")
        return original(*args, **kwargs)

    monkeypatch.setattr(cs, "_build_new_snapshot", _spy)
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(new_snap_compatible),
            "-H",
            str(umbrella),
            "--audit",
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["public_header_dirs"] == []
    assert captured["public_headers"] == []


def test_load_exports_for_poi_degrades_to_none(tmp_path):
    # Best-effort: a missing/garbage path (or None) must never raise — the POI
    # export-delta walk simply has no candidate/baseline view and degrades to
    # changed-paths/triggers/risk focusing.
    import abicheck.cli_scan as cs

    assert cs._load_exports_for_poi(None, "auto") is None
    bogus = tmp_path / "nope.abi.json"
    assert cs._load_exports_for_poi(bogus, "auto") is None


def test_export_delta_resolves_tu_into_replay_seed(monkeypatch, runner, tmp_path):
    # ADR-035 D7 (the focusing half): a baseline that carries an L5 graph mapping
    # `_Z3barv` → src/bar.cpp, and a candidate that *removes* that export, must
    # point the replay at src/bar.cpp — even though git only changed an unrelated
    # file. Proves the cheap L0 export delta steers the expensive scan's scope.
    import abicheck.cli_scan as cs
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import (
        GraphEdge,
        GraphNode,
        SourceGraphSummary,
    )

    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="binary_symbol://_Z3barv", kind="binary_symbol", label="_Z3barv"
            ),
            GraphNode(id="decl://bar", kind="source_decl", label="bar"),
            GraphNode(id="header://src/bar.cpp", kind="header", label="src/bar.cpp"),
        ],
        edges=[
            GraphEdge(
                src="decl://bar",
                dst="binary_symbol://_Z3barv",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            ),
            GraphEdge(
                src="header://src/bar.cpp", dst="decl://bar", kind="SOURCE_DECLARES"
            ),
        ],
    )
    base = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov"), _func("bar", "_Z3barv")],
        elf=_elf("_Z3foov", "_Z3barv"),
    )
    base.build_source = BuildSourcePack(root="", source_graph=graph)
    base_path = _write_snapshot(tmp_path / "old.abi.json", base)
    # Candidate removed `bar` → `_Z3barv` is a removed export (the L0 delta).
    cand = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov"),
    )
    cand_path = _write_snapshot(tmp_path / "new.abi.json", cand)

    captured: dict[str, object] = {}
    original = cs._build_new_snapshot

    def _spy(*args, **kwargs):
        captured["changed_paths"] = kwargs.get("changed_paths")
        return original(*args, **kwargs)

    monkeypatch.setattr(cs, "_build_new_snapshot", _spy)
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(cand_path),
            "--baseline",
            str(base_path),
            "--source-method",
            "s5",
            "--changed-path",
            "src/unrelated.cpp",
        ],
    )
    assert res.exit_code in (0, 4), res.output  # removed export → BREAKING is fine
    seed = captured["changed_paths"]
    assert seed is not None
    # The git-changed file (floor) AND the export-delta-resolved TU are both in.
    assert "src/unrelated.cpp" in seed
    assert "src/bar.cpp" in seed
