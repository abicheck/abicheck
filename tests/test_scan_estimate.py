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
    # pr = source-changed → intrinsic L0-L2 + L3 build + L4 replay, no L5 graph.
    assert {"L0_binary", "L1_debug", "L2_header", "L3_build", "L4_source_abi"} <= layers
    assert "L5_source_graph" not in layers


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
