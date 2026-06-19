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

"""Tests for the ADR-035 D10 typed scan API + dry-run cost estimate (G19.7).

Covers ``service.estimate_scan`` (the project cost probe) and the ``scan
--estimate`` / ``scan --audit`` CLI surfaces. Default lane — no compiler.
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
from abicheck.service import Budget, ScanRequest, estimate_scan


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def snap_path(tmp_path: Path) -> Path:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="void",
                visibility=Visibility.PUBLIC,
                access=AccessLevel.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        elf=ElfMetadata(
            symbols=[ElfSymbol(name="_Z3foov"), ElfSymbol(name="_Z6secretv")]
        ),
    )
    p = tmp_path / "new.abi.json"
    p.write_text(snapshot_to_json(snap), encoding="utf-8")
    return p


@pytest.fixture
def header(tmp_path: Path) -> Path:
    h = tmp_path / "foo.h"
    h.write_text("#pragma pack(1)\nstruct X { virtual void v(); };\n", encoding="utf-8")
    return h


# ── service.estimate_scan ────────────────────────────────────────────────────


def test_estimate_pr_mode_layers(snap_path: Path) -> None:
    req = ScanRequest(binaries=[snap_path], mode="pr")
    layers = {e.layer for e in estimate_scan(req)}
    # pr = source-changed → intrinsic L0-L2 + L3 build + L4 replay + the L5 graph
    # fold and call-graph clang pass (both run for source-changed, so the estimate
    # must price them — Codex review).
    assert {
        "L0_binary",
        "L1_debug",
        "L2_header",
        "L3_build",
        "L4_source_abi",
        "L5_source_graph",
    } <= layers


def test_estimate_baseline_mode_includes_graph(snap_path: Path) -> None:
    req = ScanRequest(binaries=[snap_path], mode="baseline")
    layers = {e.layer for e in estimate_scan(req)}
    assert "L5_source_graph" in layers  # graph-full


def test_estimate_headers_depth_has_no_source_layers(snap_path: Path) -> None:
    req = ScanRequest(binaries=[snap_path], depth="headers")
    layers = {e.layer for e in estimate_scan(req)}
    assert layers == {"L0_binary", "L1_debug", "L2_header"}


def test_estimate_counts_compile_db_tus(snap_path: Path, tmp_path: Path) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": "a.cpp", "command": "c++ a.cpp", "directory": "."},
                {"file": "b.cpp", "command": "c++ b.cpp", "directory": "."},
                {"file": "a.cpp", "command": "c++ a.cpp -DX", "directory": "."},
            ]
        ),
        encoding="utf-8",
    )
    req = ScanRequest(binaries=[snap_path], compile_db=cdb, mode="baseline")
    l3 = next(e for e in estimate_scan(req) if e.layer == "L3_build")
    assert l3.tus == 2  # unique files
    assert l3.method == "s1"


def test_estimate_focused_replay_smaller_than_full(
    snap_path: Path, tmp_path: Path
) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(10)
            ]
        ),
        encoding="utf-8",
    )
    full = next(
        e
        for e in estimate_scan(
            ScanRequest(binaries=[snap_path], compile_db=cdb, mode="baseline")
        )
        if e.layer == "L4_source_abi"
    )
    focused = next(
        e
        for e in estimate_scan(
            ScanRequest(
                binaries=[snap_path],
                compile_db=cdb,
                mode="pr",
                changed_paths=["f1.cpp"],
            )
        )
        if e.layer == "L4_source_abi"
    )
    assert focused.tus < full.tus


def test_estimate_resolves_build_info_directory(
    snap_path: Path, tmp_path: Path
) -> None:
    # --build-info given as a build *directory* must resolve to its compile DB,
    # not report 0 TUs from an unreadable directory (Codex review).
    build = tmp_path / "build"
    build.mkdir()
    (build / "compile_commands.json").write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(7)
            ]
        ),
        encoding="utf-8",
    )
    req = ScanRequest(binaries=[snap_path], build_info=build, mode="baseline")
    l3 = next(e for e in estimate_scan(req) if e.layer == "L3_build")
    assert l3.tus == 7


def test_estimate_header_change_fans_out_to_all_tus(
    snap_path: Path, tmp_path: Path
) -> None:
    # A changed header with no include graph fails open to all TUs in the real
    # scan, so the estimate must charge total_tus, not 1 (Codex review).
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(8)
            ]
        ),
        encoding="utf-8",
    )
    req = ScanRequest(
        binaries=[snap_path],
        compile_db=cdb,
        mode="pr",
        changed_paths=["include/foo.h"],
    )
    l4 = next(e for e in estimate_scan(req) if e.layer == "L4_source_abi")
    assert l4.tus == 8


def test_estimate_counts_collect_pack_tus(snap_path: Path, tmp_path: Path) -> None:
    # A --build-info pointed at an `abicheck collect` pack dir must count the
    # pack's build_evidence compile units, not report 0 (Codex review).
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack

    pack_dir = tmp_path / "pack"
    be = BuildEvidence(
        compile_units=[
            CompileUnit(id=f"cu://f{i}", source=f"f{i}.cpp", language="CXX")
            for i in range(4)
        ]
    )
    BuildSourcePack(root=pack_dir, build_evidence=be).write()

    req = ScanRequest(binaries=[snap_path], build_info=pack_dir, mode="baseline")
    l3 = next(e for e in estimate_scan(req) if e.layer == "L3_build")
    assert l3.tus == 4


def test_estimate_auto_seeded_empty_diff_resolves_to_s0(
    snap_path: Path, tmp_path: Path
) -> None:
    # A seeded-but-empty diff under --source-method auto must resolve to the s0
    # floor (no L3/L4), mirroring the real scan — not fall back to the PR preset
    # (Codex review).
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps([{"file": "a.cpp", "command": "c++", "directory": "."}]),
        encoding="utf-8",
    )
    req = ScanRequest(
        binaries=[snap_path],
        compile_db=cdb,
        source_method="auto",
        changed_paths=[],
        seeded=True,
    )
    layers = {e.layer for e in estimate_scan(req)}
    # s0 = off → only intrinsic L0-L2, no source layers.
    assert "L3_build" not in layers
    assert "L4_source_abi" not in layers


def test_estimate_inline_header_change_fans_out(
    snap_path: Path, tmp_path: Path
) -> None:
    # A changed .inl/.tcc inline header fans out to all TUs in the real replay
    # selector, so the estimate must charge total_tus (Codex review).
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(6)
            ]
        ),
        encoding="utf-8",
    )
    req = ScanRequest(
        binaries=[snap_path],
        compile_db=cdb,
        mode="pr",
        changed_paths=["include/foo.inl"],
    )
    l4 = next(e for e in estimate_scan(req) if e.layer == "L4_source_abi")
    assert l4.tus == 6


def test_estimate_compile_db_dedup_by_resolved_path(
    snap_path: Path, tmp_path: Path
) -> None:
    # Two TUs with the same relative `file` under different `directory` entries are
    # distinct and must not collapse on the bare basename (Codex review).
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": "main.cpp", "command": "c++", "directory": "/proj/a"},
                {"file": "main.cpp", "command": "c++", "directory": "/proj/b"},
            ]
        ),
        encoding="utf-8",
    )
    req = ScanRequest(binaries=[snap_path], compile_db=cdb, mode="baseline")
    l3 = next(e for e in estimate_scan(req) if e.layer == "L3_build")
    assert l3.tus == 2


def test_estimate_budget_max_tus_caps_replay(snap_path: Path, tmp_path: Path) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {"file": f"f{i}.cpp", "command": "c++", "directory": "."}
                for i in range(20)
            ]
        ),
        encoding="utf-8",
    )
    req = ScanRequest(
        binaries=[snap_path], compile_db=cdb, mode="baseline", budget=Budget(max_tus=5)
    )
    l4 = next(e for e in estimate_scan(req) if e.layer == "L4_source_abi")
    assert l4.tus == 5


# ── CLI: scan --estimate / --audit ───────────────────────────────────────────


def test_cli_estimate_scans_nothing(
    runner: CliRunner, snap_path: Path, header: Path
) -> None:
    res = runner.invoke(
        main, ["scan", "--binary", str(snap_path), "-H", str(header), "--estimate"]
    )
    assert res.exit_code == 0
    assert "dry run" in res.output
    assert "L4_source_abi" in res.output


def test_cli_estimate_json(runner: CliRunner, snap_path: Path) -> None:
    res = runner.invoke(
        main, ["scan", "--binary", str(snap_path), "--estimate", "--format", "json"]
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["mode"] == "pr"
    assert payload["estimate"]
    assert "total_est_seconds" in payload


def _minimal_compile_db(tmp_path: Path) -> Path:
    """A minimal compile_commands.json (L3 build metadata; pure parsing).

    Supplies source evidence so a pinned deep --source-method does not trip
    auto-strict (ADR-037 D5: a pinned depth with no source input errors).
    """
    src = tmp_path / "u.c"
    src.write_text("int u(void){return 0;}\n", encoding="utf-8")
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [{"directory": str(tmp_path), "file": str(src), "command": "cc -c u.c"}]
        ),
        encoding="utf-8",
    )
    return cdb


def test_replay_seed_empty_without_diff_seed(
    monkeypatch, runner: CliRunner, snap_path: Path, header: Path, tmp_path: Path
) -> None:
    # No --since/--changed-path → broad scope. Pattern-trigger POIs must NOT
    # narrow the replay seed (would skip source-only checks in other TUs) — the
    # seed stays empty so collect_inline_pack keeps the broad fallback (Codex).
    import abicheck.cli_scan as cs

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
            str(snap_path),
            "-H",
            str(header),
            "--source-method",
            "s5",
            "--build-info",
            str(_minimal_compile_db(tmp_path)),
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["changed_paths"] == ()


def test_replay_seed_used_when_changed_path_given(
    monkeypatch, runner: CliRunner, snap_path: Path, header: Path, tmp_path: Path
) -> None:
    # An explicit --changed-path is a real diff seed → the POI floor feeds the
    # replay scope.
    import abicheck.cli_scan as cs

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
            str(snap_path),
            "-H",
            str(header),
            "--source-method",
            "s5",
            "--build-info",
            str(_minimal_compile_db(tmp_path)),
            "--changed-path",
            "src/a.cpp",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "src/a.cpp" in (captured["changed_paths"] or ())


def test_seeded_empty_diff_scans_nothing(
    runner: CliRunner, snap_path: Path, header: Path
) -> None:
    # --since HEAD is a *seeded* but empty diff (no-op PR). The pattern pre-scan
    # must honour the empty scope (scan nothing) rather than fall back to a
    # whole-tree scan that would surface unrelated pattern triggers (Codex).
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(snap_path),
            "-H",
            str(header),
            "--since",
            "HEAD",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["pattern_scan"]["files_scanned"] == 0
    # No pattern triggers → no pattern-trigger POIs from a no-op PR.
    assert payload["poi"]["counts_by_reason"].get("pattern_trigger", 0) == 0


def test_cli_audit_emits_hygiene_catalog(
    runner: CliRunner, snap_path: Path, header: Path
) -> None:
    res = runner.invoke(
        main, ["scan", "--binary", str(snap_path), "-H", str(header), "--audit"]
    )
    assert res.exit_code == 0
    assert "ABI-hygiene catalog" in res.output
    # The accidental export _Z6secretv is flagged.
    assert "exported_not_public" in res.output


def test_cli_audit_json_carries_poi(
    runner: CliRunner, snap_path: Path, header: Path
) -> None:
    res = runner.invoke(
        main,
        [
            "scan",
            "--binary",
            str(snap_path),
            "-H",
            str(header),
            "--audit",
            "--format",
            "json",
        ],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert "poi" in payload
    assert payload["poi"]["version"] == 1


# --------------------------------------------------------------------------- #
# run_scan / run_audit typed engine (ADR-035 D10 / Phase 3b tail)
# --------------------------------------------------------------------------- #


def test_run_audit_returns_typed_result_with_findings(snap_path: Path) -> None:
    from abicheck.service import ScanResult, run_audit

    res = run_audit(ScanRequest(binaries=[snap_path]))
    assert isinstance(res, ScanResult)
    assert res.exit_code == 0  # RISK-only hygiene findings stay advisory
    # _Z6secretv is exported but no public header declares it.
    kinds = {f.kind.value for f in res.findings}
    assert "exported_not_public" in kinds
    assert res.layers  # per-layer coverage rows present
    assert res.estimate  # projected cost folded in
    d = res.to_dict()
    assert d["verdict"] == res.verdict
    assert d["findings"] == len(res.findings)


def test_run_scan_no_baseline_matches_audit_findings(snap_path: Path) -> None:
    from abicheck.service import run_scan

    # mode=audit with no baseline is the single-release path.
    res = run_scan(ScanRequest(binaries=[snap_path], mode="audit"))
    assert res.verdict in ("COMPATIBLE", "API_BREAK")
    assert any(f.kind.value == "exported_not_public" for f in res.findings)


def test_run_scan_rejects_multiple_binaries(snap_path: Path) -> None:
    from abicheck.service import run_scan

    with pytest.raises(ValueError):
        run_scan(ScanRequest(binaries=[snap_path, snap_path]))


def test_run_scan_confidence_matrix_present(snap_path: Path) -> None:
    from abicheck.service import run_audit

    res = run_audit(ScanRequest(binaries=[snap_path]))
    # The provider-agreement matrix is populated for run checks.
    assert isinstance(res.confidence, dict)
    assert "exported_not_public" in res.confidence


def test_run_scan_pinned_depth_without_evidence_is_contract_error(snap_path: Path) -> None:
    # ADR-037 D5 auto-strict applies to the programmatic API too: a pinned deep
    # depth with no source input maps to a failed ScanResult (not a silent shallow
    # scan), mirroring the CLI (CodeRabbit/Codex review).
    from abicheck.service import run_scan

    res = run_scan(ScanRequest(binaries=[snap_path], depth="source"))
    assert res.exit_code == 1
    assert res.verdict == "EVIDENCE_CONTRACT_ERROR"


def test_run_scan_auto_default_without_evidence_is_best_effort(snap_path: Path) -> None:
    # The unpinned default never trips the contract — best-effort binary scan.
    from abicheck.service import run_scan

    res = run_scan(ScanRequest(binaries=[snap_path], mode="audit"))
    assert res.verdict != "EVIDENCE_CONTRACT_ERROR"


def test_run_scan_binary_depth_suppresses_headers(
    monkeypatch, snap_path: Path, header: Path
) -> None:
    # Codex P2: a programmatic ScanRequest(depth="binary", headers=[...]) must not
    # parse the L2 header AST — the service mirrors the CLI's `--depth binary`
    # header suppression so the collected evidence matches the reported depth.
    import abicheck.cli_scan as cs
    from abicheck.service import run_scan

    captured: dict[str, object] = {}
    original = cs.run_scan_core

    def _spy(*args, **kwargs):
        captured["headers"] = kwargs.get("headers")
        return original(*args, **kwargs)

    monkeypatch.setattr(cs, "run_scan_core", _spy)
    res = run_scan(
        ScanRequest(
            binaries=[snap_path],
            depth="binary",
            headers=[header],
            mode="audit",
        )
    )
    assert res.verdict != "EVIDENCE_CONTRACT_ERROR"
    # Headers were dropped before reaching the core — no L2 header parse.
    assert captured["headers"] == []


def test_service_accepts_symbols_depth_alias(snap_path: Path) -> None:
    # The deprecated `symbols` depth spelling must not crash the programmatic API
    # (it's only normalized by the CLI DEPTH_PARAM otherwise) — Codex review.
    from abicheck.service import run_scan
    from abicheck.service_scan import estimate_scan

    # estimate_scan + run_scan both construct EvidenceDepth from req.depth.
    est = estimate_scan(ScanRequest(binaries=[snap_path], depth="symbols"))
    assert est  # non-empty cost estimate, no ValueError
    res = run_scan(ScanRequest(binaries=[snap_path], depth="symbols", mode="audit"))
    # `symbols`→`binary` is L0/L1 only (collect_mode off) → no contract error.
    assert res.verdict != "EVIDENCE_CONTRACT_ERROR"
