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


def _payload(res) -> dict:  # type: ignore[no-untyped-def]
    """Parse the JSON report from a scan result, tolerating a leading stderr note.

    CliRunner mixes stderr into ``output``; the deprecated --mode/--source-method
    aliases now print a one-line note there (ADR-037 D5), so strip anything before
    the first ``{`` before decoding.
    """
    out = res.output
    i = out.find("{")
    return json.loads(out[i:] if i >= 0 else out)


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
            str(new_snap_compatible),
            "--against",
            str(baseline_snap),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Verdict: COMPATIBLE" in res.output
    assert "abicheck scan — pr mode" in res.output


def test_scan_breaking_exits_four(runner, baseline_snap, new_snap_breaking):
    res = runner.invoke(
        main,
        ["scan", str(new_snap_breaking), "--against", str(baseline_snap)],
    )
    assert res.exit_code == 4, res.output
    assert "Verdict: BREAKING" in res.output


def test_scan_json_format_is_structured(runner, baseline_snap, new_snap_compatible):
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--against",
            str(baseline_snap),
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = _payload(res)
    assert payload["mode"] == "pr"
    assert payload["level"]["source_method"] == "s5"
    assert payload["verdict"] == "COMPATIBLE"
    # Coverage is mandatory and explicit (ADR-035 §4a): L0-L2 rows always present.
    layers = {row["layer"] for row in payload["coverage"]}
    assert {"L0_binary", "L2_header", "pattern_scan"} <= layers


def test_audit_mode_runs_without_baseline(runner, tmp_path):
    # An exported symbol with no public declaration → exported_not_public (RISK).
    # Absence of --against already means a one-build audit (no separate flag).
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov", "_Z6secretv"),
    )
    p = _write_snapshot(tmp_path / "lib.abi.json", snap)
    res = runner.invoke(main, ["scan", str(p)])
    assert res.exit_code == 0, res.output
    assert "audit mode" in res.output
    assert "exported_not_public" in res.output


# NOTE: test_audit_ignores_baseline_with_note (--audit + --baseline together) is
# deleted — absence of --against already means audit-only and presence already
# means audit+compare, so that combination is structurally impossible to invoke
# any more (there is no way to force audit-only while also passing --against).


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
            str(new_snap_compatible),
            "-H",
            str(header),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Pattern pre-scan facts" in res.output
    assert "pragma_pack" in res.output


def test_source_method_pin_overrides_mode_in_report(
    runner, new_snap_compatible, compile_db
):
    # --mode/--source-method are gone; --depth build is the only CLI-visible way
    # to reach the s1/build rung.
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--depth",
            "build",
            "--build-info",
            str(compile_db),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "source-method=s1" in res.output
    assert "collect-mode=build" in res.output
    # Reported depth tracks the resolved method, not the requested mode (Codex).
    assert "depth=build" in res.output


# NOTE: test_pr_deep_is_distinct_from_pr (--mode pr-deep, reaching the internal
# GRAPH depth) is deleted — --mode is gone and the public --depth ladder is
# exactly {binary, headers, build, source} (DEPTH_PARAM/USER_DEPTHS); pr-deep's
# (s5, graph) preset is no longer reachable from the CLI at all. It stays fully
# covered at the service layer by test_scan_estimate.py's direct
# ScanRequest(mode="pr-deep", ...) tests.


def test_reported_depth_matches_resolved_source_method(
    runner, new_snap_compatible, tmp_path
):
    # A minimal compile DB supplies L3 build metadata (pure parsing, no compiler),
    # so the pinned deep level can collect its evidence and auto-strict (ADR-037
    # D5) does not fire — letting us assert the honest depth reporting.
    src = tmp_path / "a.c"
    src.write_text("int a(void){return 0;}\n", encoding="utf-8")
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [{"directory": str(tmp_path), "file": str(src), "command": "cc -c a.c"}]
        ),
        encoding="utf-8",
    )
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--depth",
            "source",
            "--build-info",
            str(cdb),
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = _payload(res)
    # --depth source reaches s5 and is reported honestly.
    assert payload["level"]["source_method"] == "s5"
    assert payload["level"]["depth"] == "source"


def test_pinned_depth_without_evidence_errors(runner, new_snap_compatible):
    # ADR-037 D5 (#2 auto-strict): an explicitly pinned source/build depth that
    # can't collect its evidence (no --sources/--build-info) fails loudly with the
    # remedy, instead of silently degrading to a shallow scan.
    res = runner.invoke(
        main,
        ["scan", str(new_snap_compatible), "--depth", "source"],
    )
    assert res.exit_code != 0
    assert "pinned depth 'source'" in res.output and "nothing to collect" in res.output
    # The implicit 'auto' default (no --depth) must NOT error on the same input.
    ok = runner.invoke(main, ["scan", str(new_snap_compatible)])
    assert ok.exit_code == 0, ok.output


def test_auto_method_uses_changed_path_risk(runner, new_snap_compatible):
    # The default dial (no --depth) IS auto (ADR-037 D5): risk-driven when seeded.
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--changed-path",
            "include/foo.h",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = _payload(res)
    assert payload["level"]["auto"] is True
    # include/** is the public-header signal → auto escalates to s5.
    assert payload["level"]["source_method"] == "s5"
    assert payload["risk"]["total"] == 50


def test_budget_overflow_fails(runner, new_snap_compatible):
    # A zero budget always overflows → the dedicated budget exit code (never a
    # silent scope shrink), ADR-035 D3.
    res = runner.invoke(
        main,
        ["scan", str(new_snap_compatible), "--budget", "0s"],
    )
    assert res.exit_code == 5, res.output
    assert "budget" in res.output.lower()


def test_invalid_crosscheck_key_is_usage_error(runner, new_snap_compatible):
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
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
            str(p),
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
    advisory = runner.invoke(main, ["scan", str(p)])
    assert advisory.exit_code == 0, advisory.output

    gated = runner.invoke(
        main,
        [
            "scan",
            str(p),
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
            str(p),
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
            str(p),
            "--against",
            str(baseline_snap),
            "--crosscheck",
            "exported_not_public=error",
        ],
    )
    assert res.exit_code == 2, res.output


def _header_context_mismatch_snap(tmp_path: Path, name: str) -> Path:
    # Candidate-side evidence hygiene: public headers were parsed without the
    # ABI-relevant build context. This is a crosscheck finding, but it is not an
    # old/new ABI/API diff by itself.
    from abicheck.buildsource.build_evidence import BuildEvidence, BuildOption
    from abicheck.buildsource.pack import BuildSourcePack

    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        parsed_with_build_context=False,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov"),
    )
    snap.build_source = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(
            build_options=[
                BuildOption(key="glibcxx_use_cxx11_abi", value="1", abi_relevant=True)
            ]
        ),
    )
    return _write_snapshot(tmp_path / name, snap)


def test_baseline_compare_keeps_crosschecks_advisory_by_default(runner, tmp_path):
    old = _header_context_mismatch_snap(tmp_path, "old.abi.json")
    new = _header_context_mismatch_snap(tmp_path, "new.abi.json")
    res = runner.invoke(
        main,
        [
            "scan",
            str(new),
            "--against",
            str(old),
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = _payload(res)
    assert payload["verdict"] != "API_BREAK"
    assert payload["diff"]["api_break"] == 0
    assert (
        payload["crosscheck"]["counts_by_check"]["header_build_context_mismatch"] == 1
    )


def test_baseline_compare_promoted_crosscheck_still_gates(runner, tmp_path):
    old = _header_context_mismatch_snap(tmp_path, "old.abi.json")
    new = _header_context_mismatch_snap(tmp_path, "new.abi.json")
    res = runner.invoke(
        main,
        [
            "scan",
            str(new),
            "--against",
            str(old),
            "--crosscheck",
            "header_build_context_mismatch=error",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 2, res.output
    payload = _payload(res)
    assert payload["verdict"] == "API_BREAK"


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
            str(new),
            "--against",
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
            str(p),
            "--against",
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
            str(new_snap_compatible),
            "--risk-rules",
            str(bad),
        ],
    )
    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert "cannot read --risk-rules" in res.output


# NOTE: test_source_method_s2_runs_preprocessor_tier (--source-method s2) is
# deleted — --source-method is gone and no --depth rung maps to S2
# (_DEPTH_TO_METHOD has no S2 entry, nor does any --mode preset or the auto
# risk ladder), so the preprocessor tier is no longer reachable from the scan
# CLI at all. It stays covered at the engine level by
# tests/test_preprocessor_scan.py and tests/test_scan_levels.py.


def test_auto_seeded_empty_diff_uses_s0(runner, new_snap_compatible):
    # A *successful* empty diff (no-op PR) is a valid seed → auto picks s0/off,
    # distinct from a missing/failed seed which falls back to the preset (Codex).
    # `HEAD...HEAD` is an empty diff in this repo's git.
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--since",
            "HEAD",
            "--format",
            "json",
        ],
    )
    if res.exit_code != 0 or "seed failed" in res.output:
        pytest.skip("git unavailable / not a repo in this environment")
    payload = _payload(res)
    assert payload["level"]["source_method"] == "s0"
    assert payload["level"]["collect_mode"] == "off"


def test_auto_without_diff_seed_falls_back_to_preset(runner, new_snap_compatible):
    # auto + no --changed-path/--since seed must NOT collapse to s0/off — it falls
    # back to the mode preset so source evidence isn't silently skipped (Codex).
    # The zero-TU fix (this refactor) means an unseeded run resolves to TARGET
    # scope (whole current library), not a zero-TU/changed no-op, so collect_mode
    # is "source-target" here rather than "source-changed" (that's reserved for a
    # real --since/--changed-path seed — see test_seeded_s5_with_sources_has_no_headers_only_advisory).
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = _payload(res)
    assert payload["level"]["source_method"] == "s5"
    assert payload["level"]["collect_mode"] == "source-target"


def test_unseeded_source_depth_resolves_to_target_scope(
    runner, source_tree_with_compile_db, new_snap_compatible
):
    # ADR-043 D3 (the zero-TU fix): an unseeded --depth source scan *with a
    # source tree* now resolves to TARGET scope (the whole current library),
    # never a zero-TU no-op and never the old headers-only-replay fallback —
    # so it must NOT carry the (now retired) "no --since/--changed-path seed"
    # advisory, and collect_mode must say so explicitly.
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--sources",
            str(source_tree_with_compile_db),
            "--depth",
            "source",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = _payload(res)
    assert payload["level"]["collect_mode"] == "source-target"
    assert not any("--since" in a for a in payload["advisories"])
    assert payload["stage_timings"]["pattern_scan"] >= 0.0
    assert payload["stage_timings"]["candidate_snapshot"] >= 0.0


def test_unseeded_source_depth_target_scope_rendered_in_text_output(
    runner, source_tree_with_compile_db, new_snap_compatible
):
    # The resolved target scope must also be visible in the default text report
    # (not only JSON) so an interactive user can see what actually ran.
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--sources",
            str(source_tree_with_compile_db),
            "--depth",
            "source",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "collect-mode=source-target" in res.output
    assert "note: no --since/--changed-path seed" not in res.output


def test_unseeded_s5_without_sources_has_no_advisory(runner, new_snap_compatible):
    # No --sources tree → L4 replay never runs, so the headers-only advisory must
    # NOT fire (it would report a replay that never happened — CodeRabbit review).
    # Uses the default 'auto' dial (not a pinned depth) so auto-strict (ADR-037 D5)
    # does not error on the missing source input — best-effort by design.
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = _payload(res)
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
            str(new_snap_compatible),
            "--sources",
            str(source_tree_with_compile_db),
            "--depth",
            "source",
            "--changed-path",
            "src/foo.cpp",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = _payload(res)
    assert not any("--since" in a for a in payload["advisories"])


def test_deep_method_without_compile_db_emits_l3_advisory(
    runner, tmp_path, new_snap_compatible
):
    # A deep --depth over a pristine source tree with no compile_commands.json
    # collects no L3, so L3/L4/L5 are skipped. The user who asked for a deep
    # level must get a pointed advisory naming the level and the remedy — not
    # just silent `not_collected` coverage rows (UX gap fix).
    bare = tmp_path / "bare_src"
    bare.mkdir()
    (bare / "foo.cpp").write_text("int foo() { return 0; }\n", encoding="utf-8")
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--sources",
            str(bare),
            "--depth",
            "source",
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
            str(new_snap_compatible),
            "--sources",
            str(source_tree_with_compile_db),
            "--depth",
            "source",
            "--changed-path",
            "foo.cpp",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "needs an L3 compile database" not in res.output


def test_binary_only_deep_method_has_no_l3_advisory(runner, new_snap_compatible):
    # The default 'auto' dial over a binary-only input (no --sources/--build-info):
    # the L3 advisory is gated on a source input, so a plain binary scan isn't
    # nagged — and 'auto' (unpinned) never triggers auto-strict (ADR-037 D5). A
    # *pinned* deep depth with no input is covered by
    # test_pinned_depth_without_evidence_errors.
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
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
            str(new_snap_compatible),
            "--build-info",
            str(source_tree_with_compile_db),
            "--config",
            str(cfg),
            "--depth",
            "source",
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
            str(new_snap_compatible),
            "--build-info",
            str(source_tree_with_compile_db),
            "--depth",
            "source",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "auto-enabled the query" not in res.output


def test_level_implies_query_silent_for_default_mode(
    runner, tmp_path, source_tree_with_compile_db, new_snap_compatible
):
    # Codex review: a --config passed only for project settings must NOT trigger
    # build.query in the *default* flow (here --audit, whose preset is a deep
    # collect_mode). Auto-enable requires an EXPLICIT --source-method/--depth, not
    # a default mode preset — otherwise a config-for-settings silently runs a
    # subprocess, bypassing the --allow-build-query action ceiling.
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        "build:\n  query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON\n",
        encoding="utf-8",
    )
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--build-info",
            str(source_tree_with_compile_db),
            "--config",
            str(cfg),
            # default mode preset; no explicit --depth
        ],
    )
    assert res.exit_code == 0, res.output
    assert "auto-enabled the query" not in res.output


# NOTE: test_level_implies_query_silent_for_source_method_auto and
# test_level_implies_query_auto_plus_depth_does_not_consent (both pass an
# explicit `--source-method auto`) are deleted — --source-method is gone
# entirely, so there is no CLI-visible way to request "auto" explicitly any
# more (omitting --depth already means auto). test_level_implies_query_depth_only_consents
# below still exercises the "an explicit --depth alone is consent" half of this
# pair.


def test_explicit_malformed_config_fails_loud(runner, tmp_path, new_snap_compatible):
    # An *explicit* --config that won't parse fails loudly via the shared compile
    # resolver (ADR-037 D3 fail-loud, Codex review) — even with no --sources, where
    # nothing reloads it downstream. A clean CLI error (no traceback), not exit 0
    # with the compile: settings silently dropped. Supersedes the old
    # "does not crash → exit 0" behavior, which let a malformed explicit config
    # through.
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("build: [unterminated\n", encoding="utf-8")  # invalid YAML
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--config",
            str(cfg),
            "--depth",
            "source",
        ],
    )
    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert "cannot parse build config" in res.output


def test_level_implies_query_silent_when_config_defines_no_query(
    runner, tmp_path, new_snap_compatible
):
    # An explicit deep level + a trusted --config that defines NO build.query must
    # not auto-enable anything (the false branch): the config is loaded fine but
    # there is no query to consent to.
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("severity:\n  preset: strict\n", encoding="utf-8")  # no build.query
    src = tmp_path / "u.c"
    src.write_text("int u(void){return 0;}\n", encoding="utf-8")
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [{"directory": str(tmp_path), "file": str(src), "command": "cc -c u.c"}]
        ),
        encoding="utf-8",
    )
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--config",
            str(cfg),
            "--depth",
            "source",
            "--build-info",
            str(cdb),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "auto-enabled the query" not in res.output


def test_level_implies_query_depth_only_consents(
    runner, tmp_path, source_tree_with_compile_db, new_snap_compatible
):
    # An explicit --depth is a concrete pinned level (resolve_level uses it), so
    # it DOES consent to the trusted query.
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        "build:\n  query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON\n",
        encoding="utf-8",
    )
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--build-info",
            str(source_tree_with_compile_db),
            "--config",
            str(cfg),
            "--depth",
            "source",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "auto-enabled the query" in res.output


def test_header_short_alias_works(runner, tmp_path, new_snap_compatible):
    # The --help example uses `-H`; the alias must actually parse (Codex review).
    header = tmp_path / "inc" / "w.h"
    header.parent.mkdir()
    header.write_text("#pragma pack(1)\nstruct W { int a; };\n", encoding="utf-8")
    res = runner.invoke(
        main,
        ["scan", str(new_snap_compatible), "-H", str(header)],
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
            str(new_snap_compatible),
            "--compile-db",
            str(cc),
            "--depth",
            "build",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "collect-mode=build" in res.output


def test_malformed_build_config_yaml_is_click_error(
    runner, tmp_path, new_snap_compatible
):
    # Invalid --config YAML must surface as a clean CLI error, not a
    # traceback through embed_build_source/load_build_config (Codex review).
    src = tmp_path / "src"
    src.mkdir()
    bad = tmp_path / "abicheck.yml"
    bad.write_text("build: { system: [unclosed", encoding="utf-8")
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--sources",
            str(src),
            "--config",
            str(bad),
        ],
    )
    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert "build config" in res.output


def test_multiple_binaries_rejected(runner, baseline_snap, new_snap_compatible):
    # ARTIFACT is a single positional argument now (no --binary); a second
    # positional value is Click's own "unexpected extra argument" usage error.
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            str(baseline_snap),
        ],
    )
    assert res.exit_code != 0
    assert "unexpected extra argument" in res.output.lower()


def test_invalid_budget_string_is_bad_parameter(runner, new_snap_compatible):
    res = runner.invoke(
        main,
        ["scan", str(new_snap_compatible), "--budget", "soon"],
    )
    assert res.exit_code != 0
    assert "budget" in res.output.lower()


def test_malformed_binary_input_is_click_error(runner, tmp_path):
    # Unrecognized input must surface as a clean CLI error, not a traceback.
    bad = tmp_path / "bad.abi.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    res = runner.invoke(main, ["scan", str(bad)])
    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert "Failed to load --binary" in res.output


def test_malformed_baseline_input_is_click_error(runner, tmp_path, new_snap_compatible):
    bad = tmp_path / "bad_base.abi.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    res = runner.invoke(
        main,
        ["scan", str(new_snap_compatible), "--against", str(bad)],
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
    # _build_new_snapshot is called from within run_scan_core, which lives in
    # scan_engine.py — patch it there, not on the cli_scan re-export.
    import abicheck.scan_engine as cs

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
            str(new_snap_compatible),
            "--public-header-dir",
            str(pub),
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
    # _build_new_snapshot lives in scan_engine.py (called from run_scan_core).
    import abicheck.scan_engine as cs

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
            str(new_snap_compatible),
            "-H",
            str(umbrella),
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
    # _build_new_snapshot lives in scan_engine.py (called from run_scan_core).
    import abicheck.scan_engine as cs
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
    # A minimal compile DB so the pinned s5 has source evidence (auto-strict,
    # ADR-037 D5, otherwise errors on the missing input before the seed is built).
    src = tmp_path / "u.c"
    src.write_text("int u(void){return 0;}\n", encoding="utf-8")
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [{"directory": str(tmp_path), "file": str(src), "command": "cc -c u.c"}]
        ),
        encoding="utf-8",
    )
    res = runner.invoke(
        main,
        [
            "scan",
            str(cand_path),
            "--against",
            str(base_path),
            "--depth",
            "source",
            "--build-info",
            str(cdb),
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


def test_public_impact_closure_resolves_tu_into_replay_seed(
    monkeypatch, runner, tmp_path
):
    # ADR-041 P1 #3 (the mirror-image focusing half): a baseline L5 graph
    # where the public entry `foo` (unrelated export, unchanged between old
    # and new) has a TYPE_HAS_FIELD_TYPE edge to an internal decl declared in
    # src/detail/cache.cpp. Changing *only* src/detail/cache.cpp — never
    # foo's own file — must still pull foo's declaring file into the replay
    # seed, since foo transitively depends on what changed even though its
    # own export/declaration did not move at all (isolates
    # resolve_changed_paths_public_impact from resolve_symbol_tus, which
    # this scenario deliberately gives nothing to do — no export delta).
    import abicheck.scan_engine as cs
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import (
        GraphEdge,
        GraphNode,
        SourceGraphSummary,
    )

    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="binary_symbol://_Z3foov", kind="binary_symbol", label="_Z3foov"
            ),
            GraphNode(id="decl://pub", kind="source_decl", label="foo"),
            GraphNode(
                id="decl://internal",
                kind="record_type",
                label="Internal",
                attrs={"visibility": "private_header"},
            ),
            GraphNode(id="header://src/api.cpp", kind="header", label="src/api.cpp"),
            GraphNode(
                id="header://src/detail/cache.cpp",
                kind="header",
                label="src/detail/cache.cpp",
            ),
        ],
        edges=[
            GraphEdge(
                src="decl://pub",
                dst="binary_symbol://_Z3foov",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            ),
            GraphEdge(
                src="header://src/api.cpp", dst="decl://pub", kind="SOURCE_DECLARES"
            ),
            GraphEdge(
                src="header://src/detail/cache.cpp",
                dst="decl://internal",
                kind="SOURCE_DECLARES",
            ),
            GraphEdge(
                src="decl://pub", dst="decl://internal", kind="TYPE_HAS_FIELD_TYPE"
            ),
        ],
    )
    base = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov"),
    )
    base.build_source = BuildSourcePack(root="", source_graph=graph)
    base_path = _write_snapshot(tmp_path / "old.abi.json", base)
    # Candidate's export table is byte-identical to the baseline's — no
    # export delta, so resolve_symbol_tus contributes nothing here.
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
    src = tmp_path / "u.c"
    src.write_text("int u(void){return 0;}\n", encoding="utf-8")
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [{"directory": str(tmp_path), "file": str(src), "command": "cc -c u.c"}]
        ),
        encoding="utf-8",
    )
    res = runner.invoke(
        main,
        [
            "scan",
            str(cand_path),
            "--against",
            str(base_path),
            "--depth",
            "source",
            "--build-info",
            str(cdb),
            "--changed-path",
            "src/detail/cache.cpp",
        ],
    )
    assert res.exit_code in (0, 4), res.output
    seed = captured["changed_paths"]
    assert seed is not None
    # The git-changed file (floor) AND the impact-closure-resolved public
    # entry's own declaring file are both in, even though foo's export never
    # changed and its own file was never directly touched.
    assert "src/detail/cache.cpp" in seed
    assert "src/api.cpp" in seed


def test_public_impact_closure_uses_def_file_fallback_with_no_source_declares():
    # Codex review: an impacted public entry may carry no SOURCE_DECLARES
    # edge at all (the call/type-graph-only shape resolve_symbol_tus already
    # falls back for) and instead only its own def_file/source_location attr
    # names its declaring file. _resolve_public_impact_tus must not silently
    # drop such an entry just because decl_declaring_files (SOURCE_DECLARES-
    # only) has nothing for it.
    from types import SimpleNamespace

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import (
        GraphEdge,
        GraphNode,
        SourceGraphSummary,
    )
    from abicheck.scan_engine import _resolve_public_impact_tus

    graph = SourceGraphSummary(
        nodes=[
            # `pub` is impacted (reaches the internal decl declared in the
            # changed file) but has NO SOURCE_DECLARES edge of its own —
            # only a def_file attr names where it lives.
            GraphNode(
                id="decl://pub",
                kind="source_decl",
                label="pub",
                attrs={
                    "visibility": "public_header",
                    "def_file": "src/api.cpp",
                },
            ),
            GraphNode(
                id="decl://internal",
                kind="record_type",
                label="Internal",
                attrs={"visibility": "private_header"},
            ),
            GraphNode(
                id="header://src/detail/cache.cpp",
                kind="header",
                label="src/detail/cache.cpp",
            ),
        ],
        edges=[
            GraphEdge(
                src="header://src/detail/cache.cpp",
                dst="decl://internal",
                kind="SOURCE_DECLARES",
            ),
            GraphEdge(
                src="decl://pub", dst="decl://internal", kind="TYPE_HAS_FIELD_TYPE"
            ),
        ],
    )
    poi_baseline = SimpleNamespace(
        build_source=BuildSourcePack(root="", source_graph=graph)
    )
    tus = _resolve_public_impact_tus(poi_baseline, ["src/detail/cache.cpp"])
    assert "src/api.cpp" in tus


def test_public_impact_closure_skips_decls_with_no_resolvable_location():
    # Two more impacted-but-unresolvable shapes alongside the working
    # def_file fallback: a decl with neither a SOURCE_DECLARES edge nor any
    # def_file/source_location attr at all (loc falsy), and one whose
    # source_location is a bare in-memory-scan line number with no path
    # component (_path_of_location returns "", path falsy). Both must be
    # silently skipped -- never raise, never appear in the result -- while a
    # resolvable impacted decl in the same graph still comes through.
    from types import SimpleNamespace

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import (
        GraphEdge,
        GraphNode,
        SourceGraphSummary,
    )
    from abicheck.scan_engine import _resolve_public_impact_tus

    graph = SourceGraphSummary(
        nodes=[
            GraphNode(
                id="decl://pub_resolvable",
                kind="source_decl",
                label="pub_resolvable",
                attrs={"visibility": "public_header", "def_file": "src/api.cpp"},
            ),
            GraphNode(
                id="decl://pub_no_location",
                kind="source_decl",
                label="pub_no_location",
                attrs={"visibility": "public_header"},  # no def_file/source_location
            ),
            GraphNode(
                id="decl://pub_bare_line",
                kind="source_decl",
                label="pub_bare_line",
                attrs={"visibility": "public_header", "source_location": "7"},
            ),
            GraphNode(
                id="decl://internal",
                kind="record_type",
                label="Internal",
                attrs={"visibility": "private_header"},
            ),
            GraphNode(
                id="header://src/detail/cache.cpp",
                kind="header",
                label="src/detail/cache.cpp",
            ),
        ],
        edges=[
            GraphEdge(
                src="header://src/detail/cache.cpp",
                dst="decl://internal",
                kind="SOURCE_DECLARES",
            ),
            GraphEdge(
                src="decl://pub_resolvable",
                dst="decl://internal",
                kind="TYPE_HAS_FIELD_TYPE",
            ),
            GraphEdge(
                src="decl://pub_no_location",
                dst="decl://internal",
                kind="TYPE_HAS_FIELD_TYPE",
            ),
            GraphEdge(
                src="decl://pub_bare_line",
                dst="decl://internal",
                kind="TYPE_HAS_FIELD_TYPE",
            ),
        ],
    )
    poi_baseline = SimpleNamespace(
        build_source=BuildSourcePack(root="", source_graph=graph)
    )
    tus = _resolve_public_impact_tus(poi_baseline, ["src/detail/cache.cpp"])
    assert tus == ("src/api.cpp",)


def test_depth_binary_clears_headers_in_scan(
    monkeypatch, runner, new_snap_compatible, tmp_path
):
    # --depth binary is L0/L1-only: -H must be suppressed so the collected
    # evidence matches the reported depth (Codex review). Spy run_scan_core to
    # confirm headers are cleared even when -H is passed.
    import abicheck.cli_scan as cs

    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n", encoding="utf-8")
    captured: dict[str, object] = {}
    original = cs.run_scan_core

    def _spy(*args, **kwargs):
        captured["headers"] = kwargs.get("headers")
        return original(*args, **kwargs)

    monkeypatch.setattr(cs, "run_scan_core", _spy)
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "-H",
            str(header),
            "--depth",
            "binary",
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["headers"] == []  # -H suppressed for the binary rung


def test_depth_binary_clears_baseline_headers(
    monkeypatch, runner, baseline_snap, new_snap_compatible, tmp_path
):
    # --depth binary must also drop the *baseline* header inputs: leaving them would
    # parse the old side with the L2 header AST while the new side has none, yielding
    # spurious header/type removals against a symbols-only scan (Codex review).
    import abicheck.cli_scan as cs

    header = tmp_path / "old.h"
    header.write_text("int old(void);\n", encoding="utf-8")
    captured: dict[str, object] = {}
    original = cs.run_scan_core

    def _spy(*args, **kwargs):
        captured["baseline_headers"] = kwargs.get("baseline_headers")
        captured["headers"] = kwargs.get("headers")
        return original(*args, **kwargs)

    monkeypatch.setattr(cs, "run_scan_core", _spy)
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--against",
            str(baseline_snap),
            "--header",
            f"old={header}",
            "--depth",
            "binary",
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["baseline_headers"] == []  # old side stays symbols-only too
    assert captured["headers"] == []


def test_depth_binary_clears_source_inputs(
    monkeypatch, runner, new_snap_compatible, tmp_path
):
    # Matrix runners often pass --sources/--compile-db to every depth. Effective
    # binary depth must stay L0/L1-only and avoid the always-on source pattern
    # scan / L3 collection cost.
    import abicheck.cli_scan as cs

    src = tmp_path / "src"
    src.mkdir()
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text("[]", encoding="utf-8")
    captured: dict[str, object] = {}
    original = cs.run_scan_core

    def _spy(*args, **kwargs):
        captured["sources"] = kwargs.get("sources")
        captured["effective_build_info"] = kwargs.get("effective_build_info")
        return original(*args, **kwargs)

    monkeypatch.setattr(cs, "run_scan_core", _spy)
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--sources",
            str(src),
            "--compile-db",
            str(cdb),
            "--depth",
            "binary",
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["sources"] is None
    assert captured["effective_build_info"] is None


def test_depth_headers_keeps_source_tree_out_of_pattern_scan(
    monkeypatch, runner, new_snap_compatible, tmp_path
):
    # Matrix runners may pass --sources to every depth. The headers rung is
    # L0/L1/L2-only: it should pattern-scan public headers, not the whole source
    # tree, and it should use the cheap binary surface instead of a DWARF DIE walk.
    # _build_new_snapshot lives in scan_engine.py (called from run_scan_core).
    import abicheck.scan_engine as cs

    include = tmp_path / "include"
    include.mkdir()
    header = include / "foo.h"
    header.write_text("struct Api { virtual ~Api(); };\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "impl.cpp").write_text(
        "\n".join(f"struct Impl{i} {{ virtual ~Impl{i}(); }};" for i in range(20)),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}
    original = cs._build_new_snapshot

    def _spy(*args, **kwargs):
        captured["symbols_only"] = kwargs.get("symbols_only")
        captured["debug_presence_only"] = kwargs.get("debug_presence_only")
        return original(*args, **kwargs)

    monkeypatch.setattr(cs, "_build_new_snapshot", _spy)
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "-H",
            str(include),
            "--sources",
            str(src),
            "--depth",
            "headers",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert captured["symbols_only"] is False
    assert captured["debug_presence_only"] is True
    assert payload["pattern_scan"]["files_scanned"] == 1
    assert payload["pattern_scan"]["counts_by_kind"] == {"virtual_method": 1}


def test_depth_binary_skips_export_delta_poi_loads(
    monkeypatch, runner, baseline_snap, new_snap_compatible
):
    # The export-delta POI walk only focuses source replay. Effective binary
    # depth has no replay, so loading candidate+baseline L0 views would duplicate
    # native binary dumps and make "binary" scans unexpectedly expensive.
    # _load_exports_for_poi lives in scan_engine.py (called from run_scan_core).
    import abicheck.scan_engine as cs

    def _unexpected(*args, **kwargs):
        raise AssertionError("binary depth must not load POI export snapshots")

    monkeypatch.setattr(cs, "_load_exports_for_poi", _unexpected)
    res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_compatible),
            "--against",
            str(baseline_snap),
            "--depth",
            "binary",
        ],
    )
    assert res.exit_code == 0, res.output


def test_export_delta_poi_load_is_symbols_only(monkeypatch, tmp_path):
    import abicheck.service as service

    # _load_exports_for_poi lives in scan_engine.py (called from run_scan_core).
    from abicheck import scan_engine as cs

    captured: dict[str, object] = {}

    def _resolve_input(*args, **kwargs):
        captured["symbols_only"] = kwargs.get("symbols_only")
        return object()

    monkeypatch.setattr(service, "resolve_input", _resolve_input)

    assert cs._load_exports_for_poi(tmp_path / "lib.so", "c") is not None
    assert captured["symbols_only"] is True


def test_normalize_depth_inputs_prunes_only_binary(tmp_path):
    from abicheck import cli_scan as cs
    from abicheck.buildsource.scan_levels import EvidenceDepth

    header = tmp_path / "include"
    baseline_header = tmp_path / "old-include"
    sources = tmp_path / "src"
    build_info = tmp_path / "build"
    compile_db = tmp_path / "compile_commands.json"

    assert cs._normalize_depth_inputs(
        EvidenceDepth.BINARY,
        (header,),
        (baseline_header,),
        sources,
        build_info,
        compile_db,
    ) == ((), (), None, None, None)
    assert cs._normalize_depth_inputs(
        EvidenceDepth.HEADERS,
        (header,),
        (baseline_header,),
        sources,
        build_info,
        compile_db,
    ) == ((header,), (baseline_header,), sources, build_info, compile_db)


# NOTE: test_estimate_uses_resolved_level_not_raw_flags (--estimate) is deleted
# — --estimate is gone (folded into --dry-run, which projects cost via
# service.estimate_scan directly rather than the internal _emit_estimate
# helper cli_scan_baseline re-exports for back-compat only). The "resolved
# level, not raw flags" concern it checked is still covered by
# test_reported_depth_matches_resolved_source_method (--depth source reports
# source_method=s5 in the real, non-dry-run JSON report).

# NOTE: test_source_method_overrides_depth_binary_keeps_headers (--source-method
# s5 --depth binary, testing --source-method's precedence over --depth) is
# deleted — --source-method is gone, so this precedence is no longer reachable
# from the CLI at all.


def test_pinned_depth_with_embedded_l3_snapshot_no_contract_error(runner, tmp_path):
    # Codex review: a cached .abi.json that already carries embedded L3 evidence
    # must satisfy the pinned-depth contract via _l3_collected — not be rejected
    # because no raw --sources/--build-info was passed on the CLI.
    from abicheck.buildsource.model import CoverageStatus, LayerCoverage
    from abicheck.buildsource.pack import BuildSourcePack

    snap = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov"),
    )
    pack = BuildSourcePack(root="")
    pack.manifest.coverage.append(
        LayerCoverage(layer="L3_build", status=CoverageStatus.PRESENT)
    )
    snap.build_source = pack
    p = _write_snapshot(tmp_path / "embedded.abi.json", snap)

    res = runner.invoke(main, ["scan", str(p), "--depth", "source"])
    # The embedded L3 satisfies the contract → no EVIDENCE_CONTRACT error (exit 1).
    assert res.exit_code != 1, res.output
    assert "nothing to collect" not in res.output


# NOTE: test_mode_audit_alias_binary_only_no_contract_error (--mode audit) is
# deleted — --mode is gone entirely, and the plain default invocation (no
# --against, no --depth) it was meant to alias is already covered by the
# second half of test_pinned_depth_without_evidence_errors above.

# NOTE: test_depth_pin_with_auto_method_still_a_contract (--depth source
# --source-method auto) is deleted — --source-method is gone, so an explicit
# "auto" alongside --depth can no longer be expressed; the "an explicit
# --depth alone still pins the contract" behavior it also covered is already
# exercised by test_pinned_depth_without_evidence_errors.
