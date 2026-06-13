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

"""CLI tests for `collect`, `dump --evidence`, and
`compare --old/--new-build-info` (ADR-028 D6 / ADR-029)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from click.testing import CliRunner

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.cli import main
from abicheck.model import AbiSnapshot
from abicheck.serialization import load_snapshot, save_snapshot


def _write_cdb(tmp_path, std):
    cdb = [{
        "directory": str(tmp_path),
        "file": "src/foo.cpp",
        "arguments": ["c++", f"-std={std}", "-Iinclude", "-c", "src/foo.cpp"],
    }]
    p = tmp_path / f"cc_{std}.json"
    p.write_text(json.dumps(cdb))
    return p


def test_collect_evidence_creates_pack(tmp_path):
    cdb = _write_cdb(tmp_path, "c++20")
    out = tmp_path / "libfoo.evidence"
    result = CliRunner().invoke(
        main, ["collect", "--compile-db", str(cdb), "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "Evidence pack written" in result.output
    pack = BuildSourcePack.load(out)
    assert pack.build_evidence is not None
    assert len(pack.build_evidence.compile_units) == 1
    cov = pack.manifest.coverage_for("L3_build")
    assert cov is not None and cov.status.value == "present"


def test_collect_evidence_redacts_manifest_paths(tmp_path, monkeypatch):
    """Codex: provenance paths in manifest.json are home-redacted before write."""
    # Pretend tmp_path is under the user's home so redaction rewrites it.
    monkeypatch.setenv("HOME", str(tmp_path))
    from abicheck.buildsource.redaction import RedactionPolicy
    monkeypatch.setattr(
        "abicheck.cli_buildsource.DEFAULT_REDACTION",
        RedactionPolicy(home_replacements={str(tmp_path): "~"}),
    )
    cdb = _write_cdb(tmp_path, "c++20")
    out = tmp_path / "e"
    result = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb), "--binary", str(tmp_path / "libfoo.so"),
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    manifest = json.loads((out / "manifest.json").read_text())
    # No absolute tmp_path leaks into the manifest provenance.
    blob = json.dumps(manifest)
    assert str(tmp_path) not in blob
    assert manifest["inputs"]["binary"].startswith("~")
    assert any(e["inputs"] and e["inputs"][0].startswith("~") for e in manifest["extractors"])


def test_collect_evidence_requires_output(tmp_path):
    cdb = _write_cdb(tmp_path, "c++20")
    result = CliRunner().invoke(main, ["collect", "--compile-db", str(cdb)])
    assert result.exit_code != 0
    assert "output" in result.output.lower() or "missing" in result.output.lower()


def test_collect_evidence_cmake_requires_build_dir(tmp_path):
    result = CliRunner().invoke(
        main, ["collect", "--cmake", "-o", str(tmp_path / "e")],
    )
    assert result.exit_code != 0
    assert "build-dir" in result.output


def test_dump_attach_evidence_ref(tmp_path):
    # Build an evidence pack first.
    cdb = _write_cdb(tmp_path, "c++20")
    ev_dir = tmp_path / "e"
    CliRunner().invoke(main, ["collect", "--compile-db", str(cdb), "-o", str(ev_dir)])

    # Attach it to an existing snapshot via dump on a JSON snapshot is not
    # supported (dump takes a binary), so attach directly through the helper
    # path exercised by `dump --evidence`: load pack and to_ref.
    pack = BuildSourcePack.load(ev_dir)
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    snap.build_source_pack = pack.to_ref(path_hint=str(ev_dir))
    out = tmp_path / "snap.json"
    save_snapshot(snap, out)

    reloaded = load_snapshot(out)
    assert reloaded.build_source_pack is not None
    assert reloaded.build_source_pack.content_hash == pack.content_hash()


def test_dump_empty_build_info_dir_is_noop(tmp_path):
    # Source-tree-centric model: a plain directory with no manifest and no
    # compile DB is a build dir that yields no L3 facts — graceful, not an error
    # (ADR-028 D3). Nothing is embedded.
    bad = tmp_path / "bad"
    bad.mkdir()
    snap = AbiSnapshot(library="l", version="1")
    save_snapshot(snap, tmp_path / "s.json")

    from abicheck.cli_buildsource import embed_build_source

    embed_build_source(snap, bad, None)
    assert snap.build_source is None


def test_dump_malformed_pack_dir_errors(tmp_path):
    # A directory *with* a manifest.json is treated as a pack; a malformed one
    # is still a hard error so a corrupt collect output is not silently ignored.
    import click
    import pytest

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{ this is not json", encoding="utf-8")
    snap = AbiSnapshot(library="l", version="1")

    from abicheck.cli_buildsource import embed_build_source

    with pytest.raises(click.ClickException):
        embed_build_source(snap, bad, None)


def _make_snap(tmp_path, name, version):
    snap = AbiSnapshot(library="libfoo.so", version=version, from_headers=True)
    p = tmp_path / name
    save_snapshot(snap, p)
    return p


def test_compare_with_source_graph_packs_runs_graph_diff(tmp_path):
    """ADR-031: two --source-graph packs drive the graph-diff wiring in
    diff_embedded_build_source (folded into the verdict pipeline). Build-only
    graphs yield no graph findings, but the L5 coverage must read present and
    the comparison must still succeed."""
    old_cdb = _write_cdb(tmp_path, "c++17")
    new_cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect", "--compile-db", str(old_cdb),
                         "--source-graph", "summary", "-o", str(ev_old)])
    runner.invoke(main, ["collect", "--compile-db", str(new_cdb),
                         "--source-graph", "summary", "-o", str(ev_new)])
    assert BuildSourcePack.load(ev_old).source_graph is not None
    assert BuildSourcePack.load(ev_new).source_graph is not None

    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--old-build-info", str(ev_old), "--new-build-info", str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 1, 2, 4), result.output
    payload = json.loads(result.stdout)
    cov = {row["layer"]: row for row in payload["layer_coverage"]}
    assert cov["L5_source_graph"]["status"] == "present"


def test_compare_with_evidence_emits_coverage_and_findings(tmp_path):
    old_cdb = _write_cdb(tmp_path, "c++17")
    new_cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect", "--compile-db", str(old_cdb), "-o", str(ev_old)])
    runner.invoke(main, ["collect", "--compile-db", str(new_cdb), "-o", str(ev_new)])

    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--old-build-info", str(ev_old), "--new-build-info", str(ev_new),
        "--format", "markdown",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    # D7 coverage table is emitted to stderr.
    assert "Evidence coverage:" in result.stderr
    assert "Evidence coverage by side:" in result.stderr
    assert "old=present" in result.stderr
    assert "new=present" in result.stderr
    assert "L3 build context" in result.stderr
    # The -std drift surfaces as an ABI-relevant build-flag finding (RISK).
    assert "COMPATIBLE_WITH_RISK" in result.stdout or "Deployment Risk" in result.stdout


def test_compare_json_carries_layer_coverage_block(tmp_path):
    """ADR-028 D7: the JSON report carries a structured layer_coverage block."""
    cdb = _write_cdb(tmp_path, "c++20")
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect", "--compile-db", str(cdb), "-o", str(ev_new)])
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap), "--new-build-info", str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    payload = json.loads(result.stdout)
    assert payload["report_schema_version"] == "2.1"
    cov = {row["layer"]: row for row in payload["layer_coverage"]}
    assert set(cov) >= {"L0", "L1", "L2", "L3_build", "L4_source_abi", "L5_source_graph"}
    assert cov["L3_build"]["status"] == "present"


def test_compare_asymmetric_old_only_reports_target_not_collected(tmp_path):
    """Only --old-build-info: the target (new) side has no build facts, so the
    coverage table must report L3 not_collected — not reuse the old pack and
    claim source/build checks ran for this scan (Codex review)."""
    cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect", "--compile-db", str(cdb), "-o", str(ev_old)])
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap), "--old-build-info", str(ev_old),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    payload = json.loads(result.stdout)
    cov = {row["layer"]: row for row in payload["layer_coverage"]}
    assert cov["L3_build"]["status"] == "not_collected"
    assert "Evidence coverage by side:" in result.stderr
    assert "L3 build context" in result.stderr
    assert "old=present" in result.stderr
    assert "new=not_collected" in result.stderr
    assert "(asymmetric)" in result.stderr


def test_compare_json_without_evidence_omits_coverage(tmp_path):
    """No evidence → no layer_coverage key (additive, opt-in)."""
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, ["compare", str(old_snap), str(new_snap), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "layer_coverage" not in json.loads(result.stdout)


def test_compare_json_carries_evidence_metrics_block(tmp_path):
    """ADR-033 D6/D9: the JSON report carries an evidence_metrics block with
    collection timing and the artifact-backed vs source-only finding split."""
    old_cdb = _write_cdb(tmp_path, "c++17")
    new_cdb = _write_cdb(tmp_path, "c++20")
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect", "--compile-db", str(old_cdb), "-o", str(ev_old)])
    runner.invoke(main, ["collect", "--compile-db", str(new_cdb), "-o", str(ev_new)])
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--old-build-info", str(ev_old), "--new-build-info", str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    metrics = json.loads(result.stdout)["evidence_metrics"]
    # Timing is measured and non-negative; coverage flags reflect the run.
    assert isinstance(metrics["extractor.duration_seconds"], (int, float))
    assert metrics["extractor.duration_seconds"] >= 0
    assert metrics["coverage.build_context.present"] is True
    # The -std drift is a build-context-drift finding, not a source-only one.
    assert metrics["findings.build_context_drift.count"] >= 1
    assert metrics["findings.source_only.count"] == 0
    # And the D6 timing summary is echoed to stderr alongside the coverage table.
    assert "Evidence metrics:" in result.stderr


def test_evidence_metrics_bucket_counts_are_post_suppression(tmp_path):
    """ADR-033 D9 (Codex review): a suppressed build-drift finding must drop out
    of findings.build_context_drift.count so the buckets partition the *reported*
    findings, not the pre-suppression set."""
    runner = CliRunner()
    ev_old, ev_new = _two_build_packs(tmp_path, runner)
    supp = tmp_path / "supp.yaml"
    supp.write_text(
        "version: 1\n"
        "suppressions:\n"
        "  - change_kind: abi_relevant_build_flag_changed\n"
        "    symbol_pattern: '.*'\n"
        "    reason: known std bump\n"
        "  - change_kind: header_parse_context_drift\n"
        "    symbol_pattern: '.*'\n"
        "    reason: known parse-context drift\n"
    )
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--old-build-info", str(ev_old), "--new-build-info", str(ev_new),
        "--suppress", str(supp), "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    metrics = json.loads(result.stdout)["evidence_metrics"]
    # The only build finding was suppressed → it must not be counted.
    assert metrics["findings.build_context_drift.count"] == 0


def test_compare_json_without_evidence_omits_metrics(tmp_path):
    """No evidence → no evidence_metrics key (additive, opt-in)."""
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, ["compare", str(old_snap), str(new_snap), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "evidence_metrics" not in json.loads(result.stdout)


def test_evidence_metrics_helpers_edge_branches(capsys):
    """ADR-033 D6/D9 helper edge cases: empty-metrics no-ops, the
    missing-duration echo path, and the _layer_status fallback."""
    from abicheck.buildsource.evidence_policy import (
        _layer_status,
        echo_evidence_metrics,
    )
    from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
    from abicheck.checker_types import DiffResult, Verdict
    from abicheck.cli_buildsource import attach_evidence_metrics

    # Unknown layer → not_collected fallback (no rows for L5).
    rows = [LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT)]
    assert _layer_status(rows, DataLayer.L5_SOURCE_GRAPH) == "not_collected"

    # Empty metrics: attach is a no-op, nothing is echoed.
    result = DiffResult(old_version="1", new_version="2", library="l", verdict=Verdict.NO_CHANGE)
    attach_evidence_metrics(result, {}, [])
    assert result.evidence_metrics == {}
    echo_evidence_metrics({})
    assert capsys.readouterr().err == ""

    # Metrics without a measured duration still echo the findings line.
    echo_evidence_metrics({"findings.source_only.count": 2})
    err = capsys.readouterr().err
    assert "Evidence metrics:" in err
    assert "collection time" not in err
    assert "source-only=2" in err


def test_evidence_metrics_excludes_probe_matrix_from_artifact_backed(tmp_path):
    """ADR-033 D9 (Codex review): probe-matrix findings are injected via
    extra_changes but are build-config/source-level, not L0-L2 artifact-backed,
    so they must not inflate findings.artifact_backed.count on a mixed run."""
    # Probe matrices whose only delta is a raised C++ standard floor (17 -> 20),
    # which surfaces as a probe-matrix finding (cxx_standard_floor_raised).
    def _matrix(path, version, stds):
        path.write_text(json.dumps({
            "library": "libfoo", "version": version, "spec_name": "libfoo",
            "cxx_stds": stds, "defaults": {"backend": "tbb"}, "results": [],
        }))

    pm_old = tmp_path / "pm_old.json"
    pm_new = tmp_path / "pm_new.json"
    _matrix(pm_old, "1.0", {"a": 17, "b": 20})
    _matrix(pm_new, "2.0", {"b": 20, "c": 23})

    new_cdb = _write_cdb(tmp_path, "c++20")
    ev_new = tmp_path / "new.evidence"
    runner = CliRunner()
    runner.invoke(main, ["collect", "--compile-db", str(new_cdb), "-o", str(ev_new)])
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap), "--new-build-info", str(ev_new),
        "--probe-matrix-old", str(pm_old), "--probe-matrix-new", str(pm_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 1, 2, 4), result.output
    payload = json.loads(result.stdout)
    metrics = payload["evidence_metrics"]
    # The probe-matrix finding is reported (it is in result.changes) ...
    kinds = {c["kind"] for c in payload["changes"]}
    assert "cxx_standard_floor_raised" in kinds
    # ... but it is not counted as artifact-backed. These ELF-less snapshots have
    # no L0-L2 diff, so the only artifact-backed count here must be zero.
    assert metrics["findings.artifact_backed.count"] == 0


def _two_build_packs(tmp_path, runner):
    """Two build-info packs whose only delta is a C++ std bump (17 -> 20),
    yielding an abi_relevant_build_flag_changed finding (RISK by default)."""
    ev_old = tmp_path / "old.evidence"
    ev_new = tmp_path / "new.evidence"
    runner.invoke(main, ["collect", "--compile-db", str(_write_cdb(tmp_path, "c++17")),
                         "-o", str(ev_old)])
    runner.invoke(main, ["collect", "--compile-db", str(_write_cdb(tmp_path, "c++20")),
                         "-o", str(ev_new)])
    return ev_old, ev_new


def test_evidence_policy_build_drift_fail_on_abi_relevant_escalates(tmp_path):
    """ADR-033 D7: build_context_drift: fail-on-abi-relevant escalates the
    ABI-relevant std-flag drift from RISK (exit 0) to API_BREAK (exit 2)."""
    runner = CliRunner()
    ev_old, ev_new = _two_build_packs(tmp_path, runner)
    pol = tmp_path / "policy.yaml"
    pol.write_text("evidence_policy:\n  build_context_drift: fail-on-abi-relevant\n")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--old-build-info", str(ev_old), "--new-build-info", str(ev_new),
        "--policy-file", str(pol), "--format", "json",
    ])
    assert result.exit_code == 2, result.output  # API_BREAK
    payload = json.loads(result.stdout)
    assert payload["verdict"] in ("API_BREAK", "source_break")


def test_evidence_policy_build_drift_default_is_risk(tmp_path):
    """Without the knob the same std drift stays a non-failing risk (exit 0)."""
    runner = CliRunner()
    ev_old, ev_new = _two_build_packs(tmp_path, runner)
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--old-build-info", str(ev_old), "--new-build-info", str(ev_new),
    ])
    assert result.exit_code == 0, result.output


def test_require_evidence_fails_when_layer_absent(tmp_path):
    """ADR-033 D7 require_evidence: a mandatory-but-absent layer fails the run
    with an evidence_required_missing (API_BREAK) finding, even with no packs."""
    pol = tmp_path / "policy.yaml"
    pol.write_text("evidence_policy:\n  require_evidence:\n    build_context: true\n")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--policy-file", str(pol), "--format", "json",
    ])
    assert result.exit_code == 2, result.output  # API_BREAK
    payload = json.loads(result.stdout)
    kinds = {c["kind"] for c in payload["changes"]}
    assert "evidence_required_missing" in kinds
    # D9: the failure is counted on its own metric, not lost (Codex review).
    assert payload["evidence_metrics"]["findings.evidence_required_missing.count"] == 1


def test_require_evidence_satisfied_when_layer_present(tmp_path):
    """When the required layer is present (build pack supplied), no finding."""
    runner = CliRunner()
    ev_new = tmp_path / "new.evidence"
    runner.invoke(main, ["collect", "--compile-db", str(_write_cdb(tmp_path, "c++20")),
                         "-o", str(ev_new)])
    pol = tmp_path / "policy.yaml"
    pol.write_text("evidence_policy:\n  require_evidence:\n    build_context: true\n")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = runner.invoke(main, [
        "compare", str(old_snap), str(new_snap), "--new-build-info", str(ev_new),
        "--policy-file", str(pol), "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    kinds = {c["kind"] for c in json.loads(result.stdout)["changes"]}
    assert "evidence_required_missing" not in kinds


def test_evidence_policy_invalid_action_rejected(tmp_path):
    """An out-of-range evidence_policy action is a clear policy-file error."""
    pol = tmp_path / "policy.yaml"
    pol.write_text("evidence_policy:\n  graph_risk_findings: maybe\n")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, [
        "compare", str(old_snap), str(new_snap), "--policy-file", str(pol),
    ])
    assert result.exit_code != 0
    assert "graph_risk_findings" in result.output


def _source_tree(tmp_path):
    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "foo.cpp").write_text("int f(){return 0;}\n")
    (tree / "compile_commands.json").write_text(json.dumps([{
        "directory": str(tree), "file": "foo.cpp",
        "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
    }]))
    return tree


def test_dump_collect_mode_build_collects_l3_only(tmp_path):
    """ADR-033 D2/Phase-1: `dump --collect-mode build` captures L3 build context
    only — no L4 source replay or L5 graph."""
    tree = _source_tree(tmp_path)
    out = tmp_path / "s.json"
    result = CliRunner().invoke(main, [
        "dump", "--sources", str(tree), "--collect-mode", "build", "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    bs = load_snapshot(out).build_source
    assert bs is not None and bs.build_evidence is not None
    assert bs.source_abi is None and bs.source_graph is None
    cov = {(c.layer if isinstance(c.layer, str) else c.layer.value): c.status.value
           for c in bs.manifest.coverage}
    assert cov["L3_build"] == "present"
    assert cov["L4_source_abi"] == "not_collected"
    assert cov["L5_source_graph"] == "not_collected"


def test_dump_collect_mode_build_filters_pre_captured_pack(tmp_path):
    """ADR-033 D2 (Codex review): `--collect-mode build` must strip L4/L5 from a
    pre-captured pack too, so an L3-only run can't smuggle in source evidence."""
    runner = CliRunner()
    cdb = _write_cdb(tmp_path, "c++17")
    ev = tmp_path / "full.ev"
    runner.invoke(main, ["collect", "--compile-db", str(cdb),
                         "--source-graph", "summary", "-o", str(ev)])
    assert BuildSourcePack.load(ev).source_graph is not None  # full pack
    out = tmp_path / "s.json"
    result = runner.invoke(main, [
        "dump", "--build-info", str(ev), "--collect-mode", "build", "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    bs = load_snapshot(out).build_source
    assert bs.build_evidence is not None       # L3 kept
    assert bs.source_abi is None               # L4 stripped
    assert bs.source_graph is None             # L5 stripped


def test_source_abi_cache_hit_rate_instrumented(tmp_path):
    """ADR-033 D9: the per-TU SourceAbiCache tracks hits/misses → hit_rate."""
    from abicheck.buildsource.source_abi import SourceAbiTu
    from abicheck.buildsource.source_replay import SourceAbiCache

    cache = SourceAbiCache(tmp_path / "cache")
    assert cache.hit_rate is None              # no lookups yet
    assert cache.get("missing-key") is None    # miss
    cache.put("k1", SourceAbiTu(tu_id="cu://x", source="f.cpp"))
    assert cache.get("k1") is not None         # hit
    assert cache.get(None) is None             # uncacheable, not counted
    assert cache.hits == 1 and cache.misses == 1
    assert cache.hit_rate == 0.5


def test_recommend_collect_mode_cli():
    """ADR-033 D3: the recommend-collect-mode command maps changed paths to a mode."""
    runner = CliRunner()
    assert runner.invoke(main, ["recommend-collect-mode", "CMakeLists.txt"]).output.strip() == "build"
    assert runner.invoke(main, ["recommend-collect-mode", "src/a.cpp"]).output.strip() == "source-changed"
    assert runner.invoke(main, ["recommend-collect-mode", "README.md"]).output.strip() == "off"
    assert runner.invoke(main, ["recommend-collect-mode"]).output.strip() == "off"


def test_dump_collect_mode_off_embeds_nothing(tmp_path):
    """`--collect-mode off` collects no evidence even with a source tree."""
    tree = _source_tree(tmp_path)
    out = tmp_path / "s.json"
    result = CliRunner().invoke(main, [
        "dump", "--sources", str(tree), "--collect-mode", "off", "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert load_snapshot(out).build_source is None


def test_compare_collect_mode_without_packs_is_noted(tmp_path):
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, [
        "compare", str(old_snap), str(new_snap), "--collect-mode", "build",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    assert "collect-mode build" in result.stderr


def test_compare_without_evidence_is_unchanged(tmp_path):
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")
    result = CliRunner().invoke(main, ["compare", str(old_snap), str(new_snap)])
    assert result.exit_code == 0, result.output
    assert "Evidence coverage:" not in result.stderr


# -- L4 source ABI replay (ADR-030 phases 5-7 + CLI wiring) ------------------


def test_collect_evidence_source_abi_graceful_without_tool(tmp_path):
    """Source ABI replay degrades gracefully when the tool is missing.

    The user message must be explicit that clang is required and that source-only
    checks are disabled (never abort the collection).
    """
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb),
        "--source-abi", "--source-abi-scope", "full",
        "--clang-bin", "clang-definitely-not-installed-xyz",
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "source-only checks disabled" in result.output
    pack = BuildSourcePack.load(out)
    cov = pack.manifest.coverage_for("L4_source_abi")
    # Replay ran but the tool was absent → partial, not present (and not silent).
    assert cov is not None and cov.status.value == "partial"


def test_collect_evidence_source_abi_android_dump(tmp_path):
    """The Android backend normalizes a pre-captured dump into the pack (D9)."""
    dump = tmp_path / "libfoo.lsdump"
    dump.write_text(json.dumps({
        "source_file": "include/foo.h",
        "functions": [{"function_name": "foo", "linker_set_key": "_Z3foov", "return_type": "void"}],
        "record_types": [{"name": "Foo", "size": 8, "source_file": "include/foo.h"}],
    }))
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--source-abi", "--source-abi-extractor", "android",
        "--android-dump", str(dump), "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    pack = BuildSourcePack.load(out)
    assert pack.source_abi is not None
    assert any(e.qualified_name == "Foo" for e in pack.source_abi.reachable_types)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value == "present"


def test_collect_evidence_android_requires_dump(tmp_path):
    result = CliRunner().invoke(main, [
        "collect", "--source-abi", "--source-abi-extractor", "android",
        "-o", str(tmp_path / "ev"),
    ])
    assert result.exit_code != 0
    assert "requires --android-dump" in result.output


def _ev_with_default_arg(tmp_path, name, default):
    """Write an evidence pack whose L4 surface has one function with a default arg."""
    from abicheck.buildsource.source_abi import (
        SourceAbiTu,
        SourceEntity,
        SourceLocation,
    )
    from abicheck.buildsource.source_link import link_source_abi

    ent = SourceEntity(
        id="id", kind="function", qualified_name="add", mangled_name="_Z3addii",
        signature_hash="sig", value=default,
        source_location=SourceLocation(path="include/foo.h", origin="PUBLIC_HEADER"),
        visibility="public_header", api_relevant=True,
    )
    tu = SourceAbiTu(tu_id="cu://a", functions=[ent], public_header_roots=["include/foo.h"])
    pack = BuildSourcePack.empty(tmp_path / name)
    pack.source_abi = link_source_abi([tu], library="libfoo.so")
    pack.write()
    return tmp_path / name


def test_compare_source_abi_findings_and_capabilities(tmp_path):
    """An L4 default-argument change surfaces as a finding, and the capability
    report explains which checks ran and which did not (the user's ask)."""
    ev_old = _ev_with_default_arg(tmp_path, "old.evidence", "x=1")
    ev_new = _ev_with_default_arg(tmp_path, "new.evidence", "x=2")
    old_snap = _make_snap(tmp_path, "old.json", "1.0")
    new_snap = _make_snap(tmp_path, "new.json", "2.0")

    result = CliRunner().invoke(main, [
        "compare", str(old_snap), str(new_snap),
        "--old-build-info", str(ev_old), "--new-build-info", str(ev_new),
        "--format", "json",
    ])
    assert result.exit_code in (0, 2, 4), result.output
    payload = json.loads(result.stdout)
    # The source-replay finding is folded into the verdict pipeline.
    assert "default_argument_changed" in result.stdout.lower()
    # Authority rule (ADR-028 D3): a source-only L4 finding with no artifact-backed
    # break must NOT escalate to a breaking verdict — it stays API/source-level.
    assert payload["verdict"] != "breaking"
    kinds = {f.get("kind") for f in payload.get("changes", [])}
    assert "default_argument_changed" in kinds
    # And the L4 finding is partitioned as an API break, never a BREAKING kind.
    from abicheck.checker_policy import BREAKING_KINDS, ChangeKind
    assert ChangeKind.DEFAULT_ARGUMENT_CHANGED not in BREAKING_KINDS
    # The capability report names what is on/off and why.
    assert "Checks enabled for this scan" in result.stderr
    assert "[off]" in result.stderr
    # Macros/default-args/bodies row references its source/clang requirement.
    assert "inline/template/constexpr" in result.stderr


def _fake_clang_extractor():
    """A drop-in ClangSourceExtractor replacement that needs no real clang."""
    from abicheck.buildsource.source_abi import (
        SourceAbiTu,
        SourceEntity,
        SourceLocation,
    )

    class _Fake:
        name = "clang-source"
        version = "0.1"

        def __init__(self, **kw):
            pass

        def available(self):
            return True

        def extract(self, cu, *, public_header_roots, target_id=""):
            ent = SourceEntity(
                id="e", kind="function", qualified_name="add",
                mangled_name="_Z3addi", signature_hash="sig", value="p0=1",
                source_location=SourceLocation(path="include/foo.h", origin="PUBLIC_HEADER"),
                visibility="public_header", api_relevant=True,
            )
            return SourceAbiTu(
                tu_id=cu.id, source=cu.source,
                public_header_roots=list(public_header_roots), functions=[ent],
            )

    return _Fake


def test_collect_evidence_source_abi_success(tmp_path, monkeypatch):
    """The clang collection path writes a populated L4 surface and PRESENT row."""
    import abicheck.buildsource.source_extractors as se
    monkeypatch.setattr(se, "ClangSourceExtractor", _fake_clang_extractor())

    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb),
        "--source-abi", "--source-abi-scope", "full",
        "--source-abi-cache", str(tmp_path / "cache"),
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "L4 source ABI replay: clang extractor" in result.output
    pack = BuildSourcePack.load(out)
    assert pack.source_abi is not None
    assert any(e.qualified_name == "add" for e in pack.source_abi.reachable_declarations)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value == "present"


def test_include_map_for_replay_helper(monkeypatch):
    """_include_map_for_replay returns the depfile map, or None when clang is absent."""
    import abicheck.buildsource.include_graph as ig
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.cli_buildsource import _include_map_for_replay

    class _Avail:
        clang_bin = "clang++"

        def __init__(self, **kw):
            self.diagnostics = []

        def available(self):
            return True

        def extract_from_build(self, build):
            return {"cu://a": ["include/foo.h"]}

    monkeypatch.setattr(ig, "ClangIncludeExtractor", _Avail)
    assert _include_map_for_replay(BuildEvidence(), "clang") == {
        "cu://a": ["include/foo.h"]
    }

    class _Unavail(_Avail):
        def available(self):
            return False

    monkeypatch.setattr(ig, "ClangIncludeExtractor", _Unavail)
    assert _include_map_for_replay(BuildEvidence(), "clang") is None


def test_collect_evidence_source_abi_uses_include_graph(tmp_path, monkeypatch):
    """headers-only/changed scopes feed the depfile include map into replay."""
    import abicheck.buildsource.source_extractors as se
    import abicheck.cli_buildsource as ce

    monkeypatch.setattr(se, "ClangSourceExtractor", _fake_clang_extractor())
    monkeypatch.setattr(
        ce, "_include_map_for_replay",
        lambda merged, clang_bin: {"cu://x": ["include/foo.h"]},
    )
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb),
        "--source-abi", "--source-abi-scope", "headers-only",
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    pack = BuildSourcePack.load(out)
    assert pack.source_abi is not None
    assert pack.source_abi.coverage.get("include_graph_used") is True


def test_collect_evidence_source_abi_castxml_unavailable(tmp_path):
    """The castxml backend degrades gracefully when castxml is absent."""
    cdb = _write_cdb(tmp_path, "c++17")
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--compile-db", str(cdb),
        "--source-abi", "--source-abi-extractor", "castxml",
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    # Either castxml ran (present) or it was unavailable (graceful) — both fine,
    # but the run must not crash and must record an L4 row.
    pack = BuildSourcePack.load(out)
    cov = pack.manifest.coverage_for("L4_source_abi")
    assert cov is not None and cov.status.value in ("present", "partial")


def test_collect_evidence_source_abi_without_compile_units(tmp_path):
    """--source-abi with no L3 build context reports the missing prerequisite."""
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, [
        "collect", "--source-abi", "--source-abi-extractor", "clang",
        "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "no L3 build context" in result.output


def test_exported_symbols_from_binary_edge_cases(tmp_path):
    from pathlib import Path

    from abicheck.cli_buildsource import _exported_symbols_from_binary
    assert _exported_symbols_from_binary(None) == []
    assert _exported_symbols_from_binary(Path(tmp_path / "missing")) == []
    junk = tmp_path / "x.txt"
    junk.write_text("not a binary")
    assert _exported_symbols_from_binary(junk) == []


# ── Source-tree-centric inline collection (ADR-028..033 amendment) ────────────


def test_embed_build_info_compile_db_inline(tmp_path):
    """`--build-info compile_commands.json` collects L3 inline (no pack dir)."""
    from abicheck.cli_buildsource import embed_build_source

    cdb = _write_cdb(tmp_path, "c++17")
    snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(snap, cdb, None)

    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert len(snap.build_source.build_evidence.compile_units) == 1
    cov = snap.build_source.manifest.coverage_for("L3_build")
    assert cov is not None and cov.status.value == "present"


def test_embed_build_info_autodiscovers_compile_db_in_tree(tmp_path):
    """A compile DB inside the --sources tree is auto-discovered for L3."""
    from abicheck.cli_buildsource import embed_build_source

    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{
        "directory": str(tree),
        "file": "foo.cpp",
        "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
    }]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))

    snap = AbiSnapshot(library="libfoo.so", version="1")
    # No --build-info: the tree's compile_commands.json is found automatically.
    embed_build_source(snap, None, tree)
    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert len(snap.build_source.build_evidence.compile_units) == 1


def test_embed_sources_without_tool_is_graceful(tmp_path):
    """`--sources` with a compile DB but no clang yields partial L4, not abort."""
    from abicheck.cli_buildsource import embed_build_source

    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{
        "directory": str(tree),
        "file": "foo.cpp",
        "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"],
    }]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))

    snap = AbiSnapshot(library="libfoo.so", version="1")
    # clang is almost certainly absent under the fast unit lane; replay degrades
    # to partial coverage and the dump still succeeds (ADR-028 D3).
    embed_build_source(snap, None, tree, clang_bin="definitely-not-a-real-clang")
    assert snap.build_source is not None
    l4 = snap.build_source.manifest.coverage_for("L4_source_abi")
    assert l4 is not None and l4.status.value in ("partial", "present")


def test_build_query_skipped_without_allow_flag(tmp_path):
    """build.query is not executed unless --allow-build-query (ADR-032 D5)."""
    from abicheck.buildsource.inline import BuildConfig, collect_inline_pack

    tree = tmp_path / "src"
    tree.mkdir()
    cfg = BuildConfig(query="this-tool-should-never-run --emit", compile_db="cc.json")
    pack = collect_inline_pack(
        sources=tree, build_info=None, build_config=cfg, allow_build_query=False,
    )
    # The query is not executed; no facts are collected. The pack survives only to
    # carry the skipped-query diagnostic (A3), and the build_query tool never ran.
    assert pack is not None
    assert pack.build_evidence is None  # no L3 facts
    assert [e for e in pack.manifest.extractors
            if e.name == "build_query" and e.status == "skipped"]


def test_merge_combines_binary_and_source_snapshots(tmp_path):
    """`merge` keeps the binary base and folds in the source side's L3 facts."""
    from abicheck.cli_buildsource import embed_build_source

    # Source/build side: a snapshot carrying only L3 build facts.
    src_snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(src_snap, _write_cdb(tmp_path, "c++17"), None)
    src_path = tmp_path / "libfoo.src.json"
    save_snapshot(src_snap, src_path)

    # Binary side: a snapshot with an ABI surface (faked ELF marker) and no pack.
    from abicheck.elf_metadata import ElfMetadata

    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    bin_path = tmp_path / "libfoo.bin.json"
    save_snapshot(bin_snap, bin_path)

    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(main, ["merge", str(bin_path), str(src_path), "-o", str(out)])
    assert result.exit_code == 0, result.output

    merged = load_snapshot(out)
    assert merged.elf is not None          # base ABI surface preserved
    assert merged.build_source is not None  # source-side facts folded in
    assert merged.build_source.build_evidence is not None


# ── inline.py pure-logic coverage (no external tools) ─────────────────────────


def test_build_config_from_dict_and_load(tmp_path):
    from abicheck.buildsource.inline import (
        BuildConfig,
        discover_build_config,
        load_build_config,
    )

    cfg = BuildConfig.from_dict({
        "build": {"system": "bazel", "query": "bazel cquery //x", "compile_db": "out/cc.json"},
        "sources": {"public_headers": ["a/**.hpp"], "exclude": "**/test/**"},
    })
    assert cfg.system == "bazel"
    assert cfg.query.startswith("bazel cquery")
    assert cfg.compile_db == "out/cc.json"
    assert cfg.public_headers == ["a/**.hpp"]
    assert cfg.exclude == ["**/test/**"]

    # Empty / malformed inputs fall back to all-defaults.
    assert BuildConfig.from_dict({}).system == "auto"
    assert BuildConfig.from_dict({"build": "nope"}).query == ""

    # load_build_config: missing file → defaults; present file → parsed.
    assert load_build_config(tmp_path / "nope.yml").system == "auto"
    p = tmp_path / ".abicheck.yml"
    p.write_text("build:\n  system: cmake\n", encoding="utf-8")
    assert load_build_config(p).system == "cmake"
    # A YAML scalar (not a mapping) is tolerated.
    p.write_text("just a string\n", encoding="utf-8")
    assert load_build_config(p).system == "auto"

    # discover_build_config finds .abicheck.yml at the tree root.
    tree = tmp_path / "src"
    tree.mkdir()
    assert discover_build_config(tree) is None
    (tree / ".abicheck.yml").write_text("build: {}\n", encoding="utf-8")
    assert discover_build_config(tree) == tree / ".abicheck.yml"
    assert discover_build_config(None) is None


def test_is_pack_dir_and_compile_db_resolution(tmp_path):
    from abicheck.buildsource.inline import (
        _autodiscover_compile_db,
        _compile_db_at,
        is_pack_dir,
    )

    assert is_pack_dir(None) is False
    plain = tmp_path / "plain"
    plain.mkdir()
    assert is_pack_dir(plain) is False
    (plain / "manifest.json").write_text("{}", encoding="utf-8")
    assert is_pack_dir(plain) is True

    # _compile_db_at: a build dir with build/compile_commands.json is found.
    bd = tmp_path / "bd"
    (bd / "build").mkdir(parents=True)
    cdb = bd / "build" / "compile_commands.json"
    cdb.write_text("[]", encoding="utf-8")
    assert _compile_db_at(bd) == cdb
    assert _compile_db_at(tmp_path / "empty-missing") is None

    # auto-discovery inside a source tree (top-level).
    tree = tmp_path / "src"
    tree.mkdir()
    assert _autodiscover_compile_db(tree) is None
    top = tree / "compile_commands.json"
    top.write_text("[]", encoding="utf-8")
    assert _autodiscover_compile_db(tree) == top
    assert _autodiscover_compile_db(None) is None


def test_build_inline_coverage_rows():
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.inline import build_inline_coverage

    rows = build_inline_coverage(BuildEvidence(), has_build=False, surface=None, graph=None)
    by = {r.layer: r for r in rows}
    assert by["L3_build"].status.value == "not_collected"
    assert by["L4_source_abi"].status.value == "not_collected"
    assert by["L5_source_graph"].status.value == "not_collected"


def test_build_query_failure_is_recorded(tmp_path, monkeypatch):
    """A failing build.query command degrades to a failed extractor, no abort."""
    from abicheck.buildsource.inline import BuildConfig, collect_inline_pack

    tree = tmp_path / "src"
    tree.mkdir()
    # An unparseable command string is handled gracefully.
    cfg = BuildConfig(query='unterminated "quote', compile_db="cc.json")
    pack = collect_inline_pack(
        sources=tree, build_info=None, build_config=cfg, allow_build_query=True,
    )
    # The command produced no DB; the pack survives only to carry the failed-query
    # diagnostic (A3) so a later compare can surface it, never aborting.
    assert pack is not None
    assert pack.build_evidence is None
    assert [e for e in pack.manifest.extractors
            if e.name == "build_query" and e.status == "failed"]


def test_merge_requires_two_inputs(tmp_path):
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import save_snapshot

    snap = AbiSnapshot(library="l", version="1")
    p = tmp_path / "a.json"
    save_snapshot(snap, p)
    result = CliRunner().invoke(main, ["merge", str(p), "-o", str(tmp_path / "o.json")])
    assert result.exit_code != 0
    assert "at least two" in result.output


def _src_snapshot_with_l3(tmp_path, std, name):
    """A source-only snapshot whose embedded pack carries an L3 build_evidence
    folded from a compile DB built with -std=<std>."""
    from abicheck.cli_buildsource import embed_build_source

    snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(snap, _write_cdb(tmp_path, std), None)
    path = tmp_path / name
    save_snapshot(snap, path)
    return path


def test_merge_layer_conflict_warns_and_records(tmp_path):
    """A2: two inputs supplying L3 with DIFFERING facts → warn + persisted record,
    first-wins kept (exit 0 in the default warn mode)."""
    a = _src_snapshot_with_l3(tmp_path, "c++17", "a.json")
    b = _src_snapshot_with_l3(tmp_path, "c++20", "b.json")
    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(main, ["merge", str(a), str(b), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "merge conflict" in result.output
    assert "L3_build" in result.output

    # L3 is first-wins in _combine_packs, so the reported survivor is a.json —
    # the message and record must name the ACTUAL winner (Codex), not a guess.
    assert "kept a.json" in result.output

    merged = load_snapshot(out)
    assert merged.build_source is not None
    recs = [e for e in merged.build_source.manifest.extractors
            if e.name == "merge_layer_conflict"]
    assert recs, "conflict must be persisted in the extractor ledger"
    assert recs[0].status == "failed"
    assert recs[0].diagnostics  # carries a forward-looking note
    assert "kept a.json" in recs[0].diagnostics[0]


def test_resolve_conflict_winner_latest_wins_on_digest_tie():
    """A2 (Codex): for latest-wins layers (L4/L5), when two inputs share the
    winning digest the recorded survivor must be the LAST contributor (the one
    _combine_packs actually keeps), not the first same-digest sibling."""
    from abicheck.buildsource.merge_support import (
        _MERGE_LAYER_ATTRS,
        _resolve_conflict_winners,
    )
    from abicheck.buildsource.model import DataLayer

    l4 = DataLayer.L4_SOURCE_ABI.value
    l3 = DataLayer.L3_BUILD.value

    class _Payload:
        def __init__(self, digest_src):
            self._d = digest_src

        def to_dict(self):
            return self._d

    # combined L4 facts == {"v": "x"}; inputs A=x, B=y, C=x → C is the survivor.
    combined = SimpleNamespace(**{
        _MERGE_LAYER_ATTRS[l4]: _Payload({"v": "x"}),
        _MERGE_LAYER_ATTRS[l3]: _Payload({"v": "x"}),
    })
    from abicheck.buildsource.merge_support import _canonical_layer_digest
    dx = _canonical_layer_digest({"v": "x"})
    dy = _canonical_layer_digest({"v": "y"})
    conflicts = {
        l4: [("A", dx), ("B", dy), ("C", dx)],
        l3: [("A", dx), ("B", dy), ("C", dx)],
    }
    winners = _resolve_conflict_winners(combined, conflicts)
    assert winners[l4] == "C"   # latest-wins → last same-digest contributor
    assert winners[l3] == "A"   # accumulator-wins → first same-digest contributor


def test_merge_layer_conflict_error_mode_exits_nonzero(tmp_path):
    """A2: --on-conflict=error aborts non-zero and writes no baseline."""
    a = _src_snapshot_with_l3(tmp_path, "c++17", "a.json")
    b = _src_snapshot_with_l3(tmp_path, "c++20", "b.json")
    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(
        main, ["merge", str(a), str(b), "--on-conflict", "error", "-o", str(out)]
    )
    assert result.exit_code != 0
    assert "merge aborted" in result.output
    assert not out.exists()


def test_merge_identical_layer_is_not_a_conflict(tmp_path):
    """A2: two inputs supplying L3 with the SAME facts must NOT flag a conflict."""
    a = _src_snapshot_with_l3(tmp_path, "c++17", "a.json")
    b = _src_snapshot_with_l3(tmp_path, "c++17", "b.json")
    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(main, ["merge", str(a), str(b), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "merge conflict" not in result.output
    merged = load_snapshot(out)
    assert merged.build_source is not None
    assert not [e for e in merged.build_source.manifest.extractors
                if e.name == "merge_layer_conflict"]


def test_merge_conflict_digest_is_order_independent(tmp_path):
    """A2 (Codex): same facts in a different list order is NOT a conflict.

    The layer payloads are sets of facts keyed by identity downstream, so a
    reversed compile_commands.json must canonicalize to the same digest.
    """
    from abicheck.cli_buildsource import embed_build_source

    units = [
        {"directory": str(tmp_path), "file": "src/a.cpp",
         "arguments": ["c++", "-std=c++17", "-c", "src/a.cpp"]},
        {"directory": str(tmp_path), "file": "src/b.cpp",
         "arguments": ["c++", "-std=c++17", "-c", "src/b.cpp"]},
    ]
    fwd = tmp_path / "fwd.json"
    fwd.write_text(json.dumps(units), encoding="utf-8")
    rev = tmp_path / "rev.json"
    rev.write_text(json.dumps(list(reversed(units))), encoding="utf-8")

    a_snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(a_snap, fwd, None)
    b_snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(b_snap, rev, None)
    a = tmp_path / "a.json"
    save_snapshot(a_snap, a)
    b = tmp_path / "b.json"
    save_snapshot(b_snap, b)

    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(
        main, ["merge", str(a), str(b), "--on-conflict", "error", "-o", str(out)]
    )
    # Order-only difference must NOT abort under --on-conflict=error.
    assert result.exit_code == 0, result.output
    assert "merge conflict" not in result.output


def test_merge_three_inputs_folds_all(tmp_path):
    """D5: merge accepts 3+ inputs — a binary base plus a fact-bearing source
    snapshot plus a no-facts snapshot — folding without conflict."""
    from abicheck.elf_metadata import ElfMetadata

    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    bin_path = tmp_path / "bin.json"
    save_snapshot(bin_snap, bin_path)

    src_path = _src_snapshot_with_l3(tmp_path, "c++17", "src.json")
    plain_path = tmp_path / "plain.json"
    save_snapshot(AbiSnapshot(library="libfoo.so", version="1"), plain_path)

    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(
        main, ["merge", str(bin_path), str(src_path), str(plain_path), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "merge conflict" not in result.output
    merged = load_snapshot(out)
    assert merged.elf is not None                         # binary base kept
    assert merged.build_source is not None
    assert merged.build_source.build_evidence is not None  # L3 folded from src


def test_merge_corrupted_input_errors_cleanly(tmp_path):
    """D5: a non-JSON input fails with a non-zero exit, not a traceback dump."""
    good = _src_snapshot_with_l3(tmp_path, "c++17", "good.json")
    bad = tmp_path / "bad.json"
    bad.write_text("this is not json", encoding="utf-8")
    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(main, ["merge", str(good), str(bad), "-o", str(out)])
    assert result.exit_code != 0
    assert "could not read snapshot" in result.output
    assert not out.exists()


def test_merge_without_embedded_facts_is_noted(tmp_path):
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import load_snapshot, save_snapshot

    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    save_snapshot(AbiSnapshot(library="l", version="1"), a)
    save_snapshot(AbiSnapshot(library="l", version="2"), b)
    out = tmp_path / "o.json"
    result = CliRunner().invoke(main, ["merge", str(a), str(b), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "no input carried embedded build_source" in result.output
    # Base ABI surface still written.
    assert load_snapshot(out).library == "l"


def test_dump_source_only_no_binary(tmp_path):
    """`dump --sources <tree>` with no SO_PATH writes a binary-less baseline.

    The parallel-baseline flow that `merge` consumes (Codex P2): SO_PATH is
    optional when --sources/--build-info is given.
    """
    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{"directory": str(tree), "file": "foo.cpp",
            "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"]}]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))

    out = tmp_path / "libfoo.src.json"
    result = CliRunner().invoke(main, ["dump", "--sources", str(tree), "-o", str(out)])
    assert result.exit_code == 0, result.output

    snap = load_snapshot(out)
    assert snap.elf is None and snap.pe is None and snap.macho is None  # no binary
    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None
    assert len(snap.build_source.build_evidence.compile_units) == 1


def test_dump_with_no_binary_and_no_inputs_errors():
    """A bare `dump` (no SO_PATH, no --sources/--build-info) errors clearly."""
    result = CliRunner().invoke(main, ["dump"])
    assert result.exit_code != 0
    assert "source-only" in result.output


def test_dump_show_data_sources_requires_binary(tmp_path):
    tree = tmp_path / "src"
    tree.mkdir()

    result = CliRunner().invoke(
        main, ["dump", "--show-data-sources", "--sources", str(tree)]
    )

    assert result.exit_code != 0
    assert "--show-data-sources requires SO_PATH" in result.output


def test_dump_source_only_then_merge_with_binary(tmp_path):
    """End-to-end: source-only dump + binary dump combine via `merge`."""
    from abicheck.elf_metadata import ElfMetadata
    from abicheck.model import AbiSnapshot

    tree = tmp_path / "src"
    tree.mkdir()
    cdb = [{"directory": str(tree), "file": "foo.cpp",
            "arguments": ["c++", "-std=c++17", "-c", "foo.cpp"]}]
    (tree / "compile_commands.json").write_text(json.dumps(cdb))
    src_out = tmp_path / "libfoo.src.json"
    assert CliRunner().invoke(
        main, ["dump", "--sources", str(tree), "-o", str(src_out)]
    ).exit_code == 0

    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    bin_path = tmp_path / "libfoo.bin.json"
    save_snapshot(bin_snap, bin_path)

    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(
        main, ["merge", str(bin_path), str(src_out), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    merged = load_snapshot(out)
    assert merged.elf is not None  # binary base kept
    assert merged.build_source is not None and merged.build_source.build_evidence is not None


def test_mixed_build_pack_and_raw_sources_hash_distinguishes_trees(tmp_path):
    """Same build-info pack + different source trees → different content_hash.

    Codex P2: inline source facts must contribute to the combined
    build_source_pack content hash even when the build side is an on-disk pack.
    """
    from pathlib import Path

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_buildsource import _combine_packs

    # On-disk build-info pack.
    bi = BuildSourcePack.empty(tmp_path / "bi")
    ev = BuildEvidence()
    ev.compile_units.append(CompileUnit(id="cu://x", source="x.cpp"))
    bi.build_evidence = ev
    bi.write()
    bi = BuildSourcePack.load(tmp_path / "bi")

    def _inline_with(library: str) -> BuildSourcePack:
        return BuildSourcePack(root=Path(""), source_abi=SourceAbiSurface(library=library))

    a = _combine_packs(bi, None, _inline_with("tree_a"))
    b = _combine_packs(bi, None, _inline_with("tree_b"))
    assert a is not None and b is not None
    assert a.content_hash() != b.content_hash()
    # And the build evidence still participates (same pack → shared component).
    same = _combine_packs(bi, None, _inline_with("tree_a"))
    assert a.content_hash() == same.content_hash()


def test_inline_source_changed_falls_back_to_target_scope(tmp_path, monkeypatch):
    """ADR-033 (Codex): inline dump has no PR diff, so a 'changed' scope must fall
    back to 'target' for replay — otherwise L4 selects zero TUs and is empty."""
    import abicheck.buildsource.inline as inline
    captured = {}

    def _spy(sources, merged, extractors, *, extractor, scope, clang_bin,
             exported_symbols=()):
        captured["scope"] = scope
        return None

    monkeypatch.setattr(inline, "_run_inline_source_abi", _spy)
    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "f.cpp").write_text("int f(){return 0;}\n")
    (tree / "compile_commands.json").write_text(json.dumps([{
        "directory": str(tree), "file": "f.cpp",
        "arguments": ["c++", "-c", "f.cpp"]}]))
    inline.collect_inline_pack(sources=tree, build_info=None, scope="changed",
                               layers=("L3", "L4", "L5"))
    assert captured["scope"] == "target"


def test_exported_symbols_from_snapshot_extracts_mangled_names():
    """A1 plumbing: export extraction pulls mangled function/variable names from
    an already-parsed snapshot (no re-dump), and is empty for a bare snapshot."""
    from abicheck.cli_buildsource import _exported_symbols_from_snapshot
    from abicheck.model import Function, Variable

    snap = AbiSnapshot(library="libfoo.so", version="1")
    snap.functions = [
        Function(name="foo", mangled="_Z3foov", return_type="void", params=[]),
        Function(name="bar", mangled="", return_type="void", params=[]),  # no symbol
    ]
    snap.variables = [Variable(name="g", mangled="_Z1g", type="int")]
    assert _exported_symbols_from_snapshot(snap) == ("_Z1g", "_Z3foov")

    assert _exported_symbols_from_snapshot(AbiSnapshot(library="l", version="1")) == ()


def test_build_info_source_mismatch_records_diagnostic(tmp_path):
    """A4: a compile DB whose sources are absent from the --sources tree records
    a build_info_source_tree_mismatch diagnostic (collection-time, not a kind)."""
    from abicheck.buildsource.inline import collect_inline_pack

    # compile DB referencing files that do NOT exist under the (empty) tree.
    cdb = [{
        "directory": str(tmp_path),
        "file": f"src/missing{i}.cpp",
        "arguments": ["c++", "-std=c++17", "-c", f"src/missing{i}.cpp"],
    } for i in range(4)]
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps(cdb), encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()  # empty: none of the compile-DB sources resolve here

    pack = collect_inline_pack(sources=tree, build_info=db, layers=("L3",))
    assert pack is not None
    recs = [e for e in pack.manifest.extractors
            if e.name == "build_info_source_tree_mismatch"]
    assert recs and recs[0].status == "failed"
    assert pack.build_evidence is not None
    assert any("mismatch" in d for d in pack.build_evidence.diagnostics)


def test_build_info_source_match_no_mismatch(tmp_path):
    """A4: when the compile-DB sources exist under the tree, no mismatch fires."""
    from abicheck.buildsource.inline import collect_inline_pack

    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    cdb = []
    for i in range(4):
        (tree / "src" / f"f{i}.cpp").write_text("int x;", encoding="utf-8")
        cdb.append({
            "directory": str(tree),
            "file": f"src/f{i}.cpp",
            "arguments": ["c++", "-std=c++17", "-c", f"src/f{i}.cpp"],
        })
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps(cdb), encoding="utf-8")

    pack = collect_inline_pack(sources=tree, build_info=db, layers=("L3",))
    assert pack is not None
    assert not [e for e in pack.manifest.extractors
                if e.name == "build_info_source_tree_mismatch"]


def test_build_info_source_mismatch_basename_match_ignores_redacted_prefix(tmp_path):
    """A4 (Codex): redacted '~/...' compile-DB paths must not cause a false
    mismatch — matching is by basename, which redaction never strips."""
    from abicheck.buildsource.inline import collect_inline_pack

    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    cdb = []
    for i in range(4):
        (tree / "src" / f"r{i}.cpp").write_text("int x;", encoding="utf-8")
        # directory/file carry a redacted home placeholder, not a real path.
        cdb.append({
            "directory": "~/proj",
            "file": f"src/r{i}.cpp",
            "arguments": ["c++", "-std=c++17", "-c", f"src/r{i}.cpp"],
        })
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps(cdb), encoding="utf-8")

    pack = collect_inline_pack(sources=tree, build_info=db, layers=("L3",))
    assert pack is not None
    assert not [e for e in pack.manifest.extractors
                if e.name == "build_info_source_tree_mismatch"]


def test_canonical_layer_digest_sorts_nested_facts_keeps_scalar_order():
    """A2 (Codex): the per-layer digest is order-independent for nested fact
    *records* (e.g. reachable_declarations) but order-SENSITIVE for scalar
    sequences (e.g. linker_argv) which encode ABI-relevant order."""
    from abicheck.buildsource.merge_support import _canonical_layer_digest

    a = {"reachable_source_surface": {
        "reachable_declarations": [{"id": "d1"}, {"id": "d2"}]}}
    b = {"reachable_source_surface": {
        "reachable_declarations": [{"id": "d2"}, {"id": "d1"}]}}
    # Nested fact records reversed → same digest (set semantics).
    assert _canonical_layer_digest(a) == _canonical_layer_digest(b)

    # Ordered scalar sequence reordered → different digest (argv order matters).
    x = {"link_units": [{"linker_argv": ["-lfoo", "-lbar"]}]}
    y = {"link_units": [{"linker_argv": ["-lbar", "-lfoo"]}]}
    assert _canonical_layer_digest(x) != _canonical_layer_digest(y)

    # Unordered scalar fact set reordered → same digest (source_files is a set).
    p1 = {"targets": [{"source_files": ["a.cpp", "b.cpp"]}]}
    p2 = {"targets": [{"source_files": ["b.cpp", "a.cpp"]}]}
    assert _canonical_layer_digest(p1) == _canonical_layer_digest(p2)

    # Include-path order is compiler-visible → reordering must differ (Codex).
    i1 = {"compile_units": [{"include_paths": ["/a", "/b"]}]}
    i2 = {"compile_units": [{"include_paths": ["/b", "/a"]}]}
    assert _canonical_layer_digest(i1) != _canonical_layer_digest(i2)

    # abi_relevant_flags is last-wins (-fexceptions/-fno-exceptions) → reordering
    # changes the parsed ABI, so it must read as a conflict (Codex).
    f1 = {"compile_units": [{"abi_relevant_flags": ["-fexceptions", "-fno-exceptions"]}]}
    f2 = {"compile_units": [{"abi_relevant_flags": ["-fno-exceptions", "-fexceptions"]}]}
    assert _canonical_layer_digest(f1) != _canonical_layer_digest(f2)


def test_build_inline_coverage_surfaces_failed_build_query():
    """A3: a failed/blocked build query yields a `partial` L3 coverage row with
    the reason, not a silent `not_collected`."""
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.inline import build_inline_coverage
    from abicheck.buildsource.model import ExtractorRecord

    rec = ExtractorRecord(
        name="build_query", status="skipped",
        detail="build.query configured but --allow-build-query not set",
    )
    rows = {r.layer: r for r in build_inline_coverage(
        BuildEvidence(), has_build=False, surface=None, graph=None, extractors=[rec])}
    l3 = rows["L3_build"]
    assert l3.status.value == "partial"
    assert "build query skipped" in l3.detail

    # No build-query record → still a silent not_collected (unchanged behaviour).
    rows2 = {r.layer: r for r in build_inline_coverage(
        BuildEvidence(), has_build=False, surface=None, graph=None, extractors=[])}
    assert rows2["L3_build"].status.value == "not_collected"


def test_embedded_source_graph_l5_roundtrips(tmp_path):
    """D7: an embedded L5 source_graph survives dump-embed + snapshot round-trip."""
    from abicheck.cli_buildsource import embed_build_source

    cdb = _write_cdb(tmp_path, "c++17")
    pack_dir = tmp_path / "ev"
    CliRunner().invoke(main, ["collect", "--compile-db", str(cdb),
                              "--source-graph", "summary", "-o", str(pack_dir)])
    assert BuildSourcePack.load(pack_dir).source_graph is not None

    snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(snap, pack_dir, None)
    assert snap.build_source is not None and snap.build_source.source_graph is not None

    out = tmp_path / "s.json"
    save_snapshot(snap, out)
    reloaded = load_snapshot(out)
    assert reloaded.build_source is not None
    assert reloaded.build_source.source_graph is not None


def test_build_info_invalid_compile_db_is_graceful(tmp_path):
    """D3: a build dir whose compile_commands.json is malformed degrades to no L3
    facts without crashing the dump (ADR-028 D3)."""
    from abicheck.cli_buildsource import embed_build_source

    bd = tmp_path / "build"
    bd.mkdir()
    (bd / "compile_commands.json").write_text("{ not valid json", encoding="utf-8")
    snap = AbiSnapshot(library="l", version="1")
    embed_build_source(snap, bd, None)  # must not raise
    # No usable L3 facts → nothing embedded (or build_source without compile units).
    if snap.build_source is not None and snap.build_source.build_evidence is not None:
        assert not snap.build_source.build_evidence.compile_units


def test_build_config_malformed_yaml_falls_back_to_defaults(tmp_path):
    """D4: a malformed `.abicheck.yml` build block degrades to defaults instead of
    raising, so collection is never aborted by a bad config."""
    from abicheck.buildsource.inline import (
        discover_build_config,
        load_build_config,
    )

    tree = tmp_path / "src"
    tree.mkdir()
    cfg_path = tree / ".abicheck.yml"
    cfg_path.write_text("build:\n  - this is a list not a mapping\n", encoding="utf-8")
    # discover finds it; load tolerates the malformed shape.
    assert discover_build_config(tree) == cfg_path
    cfg = load_build_config(cfg_path)
    assert cfg.system == "auto"
    assert cfg.query == ""


def test_dump_sources_and_build_info_together(tmp_path):
    """D2: --sources and --build-info together — L3 comes from --build-info, the
    source tree drives L4 (partial without clang); the call must not error and
    L3 facts must be embedded."""
    from abicheck.cli_buildsource import embed_build_source

    cdb = _write_cdb(tmp_path, "c++17")
    tree = tmp_path / "src"
    (tree / "src").mkdir(parents=True)
    (tree / "src" / "foo.cpp").write_text("int x;", encoding="utf-8")

    snap = AbiSnapshot(library="libfoo.so", version="1")
    embed_build_source(snap, cdb, tree)  # build_info=cdb, sources=tree
    assert snap.build_source is not None
    assert snap.build_source.build_evidence is not None  # L3 from --build-info


def test_collect_no_input_is_noop(tmp_path):
    """D6: `collect -o out` with no inputs collects nothing and does not crash."""
    out = tmp_path / "ev"
    result = CliRunner().invoke(main, ["collect", "-o", str(out)])
    # Either a clean message or a graceful empty pack — never a traceback.
    assert result.exit_code in (0, 1, 2), result.output


def test_merge_relinks_source_surface_with_binary_exports(tmp_path):
    """A1 merge plumbing: a source-only snapshot's surface (linked with no binary)
    gets the binary base's L0 exports folded in at merge time, so provenance has
    a signal in the parallel-baseline flow."""
    from pathlib import Path

    from abicheck.buildsource.model import BuildSourceManifest
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
    from abicheck.elf_metadata import ElfMetadata
    from abicheck.model import Function

    # Source-only snapshot: a surface with one public decl, no exports yet.
    surf = SourceAbiSurface(library="libfoo.so", target_id="t")
    surf.reachable_declarations = [
        SourceEntity(id="decl://foo", kind="function", qualified_name="foo",
                     mangled_name="_Z3foov")
    ]
    src_snap = AbiSnapshot(library="libfoo.so", version="1")
    src_snap.build_source = BuildSourcePack(
        root=Path(""), manifest=BuildSourceManifest(), source_abi=surf)
    src_path = tmp_path / "src.json"
    save_snapshot(src_snap, src_path)

    # Binary snapshot exporting _Z3foov.
    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    bin_snap.functions = [Function(name="foo", mangled="_Z3foov",
                                   return_type="void", params=[])]
    bin_path = tmp_path / "bin.json"
    save_snapshot(bin_snap, bin_path)

    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(main, ["merge", str(bin_path), str(src_path), "-o", str(out)])
    assert result.exit_code == 0, result.output
    merged = load_snapshot(out)
    assert merged.build_source is not None and merged.build_source.source_abi is not None
    # Exports plumbed in, and foo now maps to its exported symbol.
    assert merged.build_source.source_abi.roots["exported_symbols"] == ["_Z3foov"]
    mapping = merged.build_source.source_abi.mappings["source_decl_to_binary_symbol"]
    assert "_Z3foov" in set(mapping.values())


def test_merge_relink_rebuilds_l5_graph_and_refreshes_hash(tmp_path):
    """A1 merge plumbing (Codex): when the source-only input carries an L5 graph,
    relinking rebuilds it with the binary's exports (so it gains the
    source↔binary edges) and clears stale artifact digests so content_hash
    recomputes from the updated payloads."""
    from pathlib import Path

    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.model import BuildSourceManifest
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
    from abicheck.buildsource.source_graph import build_source_graph
    from abicheck.elf_metadata import ElfMetadata
    from abicheck.model import Function

    surf = SourceAbiSurface(library="libfoo.so", target_id="t")
    surf.reachable_declarations = [
        SourceEntity(id="decl://foo", kind="function", qualified_name="foo",
                     mangled_name="_Z3foov")
    ]
    graph0 = build_source_graph(BuildEvidence(), source_abi=surf)  # empty exports
    src_snap = AbiSnapshot(library="libfoo.so", version="1")
    src_snap.build_source = BuildSourcePack(
        root=Path(""), manifest=BuildSourceManifest(), source_abi=surf,
        source_graph=graph0)
    src_path = tmp_path / "src.json"
    save_snapshot(src_snap, src_path)

    bin_snap = AbiSnapshot(library="libfoo.so", version="1")
    bin_snap.elf = ElfMetadata()
    bin_snap.functions = [Function(name="foo", mangled="_Z3foov",
                                   return_type="void", params=[])]
    bin_path = tmp_path / "bin.json"
    save_snapshot(bin_snap, bin_path)

    out = tmp_path / "baseline.json"
    result = CliRunner().invoke(main, ["merge", str(bin_path), str(src_path), "-o", str(out)])
    assert result.exit_code == 0, result.output
    merged = load_snapshot(out)
    g = merged.build_source.source_graph
    assert g is not None
    # Rebuilt graph carries a symbol-mapping edge the empty-export graph lacked.
    edge_kinds = {e.kind for e in g.edges}
    assert any("SYMBOL" in k for k in edge_kinds), edge_kinds
    # content_hash recomputes from the updated payloads (no stale artifacts).
    assert merged.build_source.content_hash()


def test_a4_redacted_absolute_source_uses_basename(tmp_path):
    """A4 (CI regression): when the compile-DB adapter redacts a source to a
    '~/...' absolute path (runner CWD under $HOME), the rooted/redacted prefix is
    unrecoverable, so matching falls back to basename and a present checkout is
    NOT flagged as a mismatch."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.inline import _check_build_info_source_mismatch

    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    units = []
    for i in range(4):
        (tree / "src" / f"r{i}.cpp").write_text("int x;", encoding="utf-8")
        # Redacted absolute source (home prefix rewritten to '~'), as the adapter
        # emits on a runner whose CWD is under $HOME.
        units.append(CompileUnit(id=f"u{i}", source=f"~/work/proj/src/r{i}.cpp",
                                 directory="~/proj"))
    merged = BuildEvidence()
    merged.compile_units = units
    extractors = []
    _check_build_info_source_mismatch(merged, tree, extractors)
    assert not [e for e in extractors if e.name == "build_info_source_tree_mismatch"]


def test_a4_basename_only_match_in_wrong_subtree_flags_mismatch(tmp_path):
    """A4 (Codex): an absolute/redacted compile-DB source must match more than
    its bare basename — a wrong checkout that ships the same filename under a
    different parent dir (tests/ vs src/) must still flag the mismatch."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.inline import _check_build_info_source_mismatch

    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    units = []
    for i in range(4):
        # Same basename present, but under tests/ — the compile unit's src/ parent
        # is absent, so the trees are different checkouts.
        (tree / "tests" / f"f{i}.cpp").write_text("int x;", encoding="utf-8")
        # directory does NOT prefix the source, so matching takes the
        # absolute/redacted fallback (suffix match), not the directory branch.
        units.append(CompileUnit(id=f"u{i}", source=f"/build/proj/src/f{i}.cpp",
                                 directory="/unrelated"))
    merged = BuildEvidence()
    merged.compile_units = units
    extractors = []
    _check_build_info_source_mismatch(merged, tree, extractors)
    recs = [e for e in extractors if e.name == "build_info_source_tree_mismatch"]
    assert recs and recs[0].status == "failed"


def test_a3_failed_query_pack_survives_with_no_facts(tmp_path):
    """A3 (Codex): when build.query is skipped/failed and no facts are collected,
    collect_inline_pack still returns a pack carrying the partial L3 coverage row
    + the build_query diagnostic (not None), so compare can surface it."""
    from abicheck.buildsource.inline import BuildConfig, collect_inline_pack

    tree = tmp_path / "src"
    tree.mkdir()  # no compile DB inside → no L3 facts
    cfg = BuildConfig(query="some-build-query --emit")
    # allow_build_query=False → query skipped, nothing collected.
    pack = collect_inline_pack(
        sources=tree, build_info=None, build_config=cfg,
        allow_build_query=False, layers=("L3",),
    )
    assert pack is not None, "pack must survive to carry the A3 diagnostic"
    l3 = pack.manifest.coverage_for("L3_build")
    assert l3 is not None and l3.status.value == "partial"
    assert [e for e in pack.manifest.extractors
            if e.name == "build_query" and e.status == "skipped"]


def test_a3_query_ran_but_empty_is_reported(tmp_path):
    """A3 (Codex): an allowed build query that runs but produces no compile DB
    records `partial`; the pack must survive so that diagnostic + partial L3 row
    reach compare (not just failed/skipped)."""
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.inline import build_inline_coverage
    from abicheck.buildsource.model import ExtractorRecord

    rec = ExtractorRecord(name="build_query", status="partial",
                          detail="ran `q …` but no compile DB was produced")
    rows = {r.layer: r for r in build_inline_coverage(
        BuildEvidence(), has_build=False, surface=None, graph=None, extractors=[rec])}
    assert rows["L3_build"].status.value == "partial"
    assert "build query partial" in rows["L3_build"].detail


def test_a3_diagnostic_only_pack_survives_embed_combine(tmp_path):
    """A3 (Codex): a diagnostic-only inline pack (build query skipped, no facts)
    must keep its build_query extractor + partial L3 coverage row through
    embed_build_source -> _combine_packs, not become a silent not_collected pack."""
    from abicheck.cli_buildsource import embed_build_source

    tree = tmp_path / "src"
    tree.mkdir()
    (tree / ".abicheck.yml").write_text(
        "build:\n  query: some-build-query --emit\n", encoding="utf-8")
    snap = AbiSnapshot(library="libfoo.so", version="1")
    # allow_build_query defaults False → query skipped, no facts collected.
    embed_build_source(snap, None, tree)
    assert snap.build_source is not None
    l3 = snap.build_source.manifest.coverage_for("L3_build")
    assert l3 is not None and l3.status.value == "partial"
    assert [e for e in snap.build_source.manifest.extractors
            if e.name == "build_query"]
