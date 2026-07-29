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

"""CLI/service tests for ``scan --artifact-set`` (ADR-056, G34).

Mirrors ``tests/test_cli_scan.py``'s JSON-snapshot-input style for the
fast, default-marker tests (mutual exclusion, discovery validation, the
service-layer ``run_scan_set`` acceptance path) and follows
``tests/test_bundle.py::TestCompareReleaseBundleE2E``'s real-gcc pattern
(``@pytest.mark.integration``) for the one true end-to-end CLI success
case, since ``--artifact-set``'s explicit-path form validates every member
against real ELF magic bytes before a scan even starts.
"""

from __future__ import annotations

import json
import shutil
import subprocess
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


def _func(name: str, mangled: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def snap_a(tmp_path: Path) -> Path:
    snap = AbiSnapshot(
        library="liba.so",
        version="1.0",
        from_headers=True,
        functions=[_func("a_run", "_Z5a_runv")],
        elf=_elf("_Z5a_runv"),
    )
    return _write_snapshot(tmp_path / "a.abi.json", snap)


@pytest.fixture
def snap_b(tmp_path: Path) -> Path:
    snap = AbiSnapshot(
        library="libb.so",
        version="1.0",
        from_headers=True,
        functions=[_func("b_run", "_Z5b_runv")],
        elf=_elf("_Z5b_runv"),
    )
    return _write_snapshot(tmp_path / "b.abi.json", snap)


# ---------------------------------------------------------------------------
# CLI mutual-exclusion / usage validation (no ELF parsing needed — these all
# fail before discover_artifact_set is reached).
# ---------------------------------------------------------------------------


class TestArtifactSetCliValidation:
    def test_rejects_artifact_and_artifact_set_together(
        self, runner: CliRunner, snap_a: Path
    ) -> None:
        result = runner.invoke(
            main, ["scan", str(snap_a), "--artifact-set", "x,y"]
        )
        assert result.exit_code != 0
        assert "exactly one of ARTIFACT or --artifact-set" in result.output

    def test_rejects_neither_artifact_nor_artifact_set(
        self, runner: CliRunner
    ) -> None:
        result = runner.invoke(main, ["scan"])
        assert result.exit_code != 0
        assert "exactly one of ARTIFACT or --artifact-set" in result.output

    def test_rejects_against_with_artifact_set(
        self, runner: CliRunner, snap_a: Path
    ) -> None:
        result = runner.invoke(
            main,
            ["scan", "--artifact-set", "x,y", "--against", str(snap_a)],
        )
        assert result.exit_code != 0
        assert "--against is not supported with --artifact-set" in result.output

    def test_rejects_bundle_system_providers_without_artifact_set(
        self, runner: CliRunner, snap_a: Path
    ) -> None:
        result = runner.invoke(
            main,
            ["scan", str(snap_a), "--bundle-system-providers", "libfoo.so.1"],
        )
        assert result.exit_code != 0
        assert "--bundle-system-providers requires --artifact-set" in result.output

    def test_rejects_fewer_than_two_resolved_libraries(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # A single explicit member (no comma) is a valid --artifact-set
        # *syntax* but not a valid *set* — an audit needs 2+ libraries.
        only = tmp_path / "libonly.so"
        only.write_bytes(b"\x7fELF" + b"\0" * 12)
        result = runner.invoke(main, ["scan", "--artifact-set", str(only)])
        assert result.exit_code != 0
        assert "2 or more libraries" in result.output

    def test_rejects_colliding_explicit_members(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        d1, d2 = tmp_path / "d1", tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        p1, p2 = d1 / "libfoo.so", d2 / "libfoo.so"
        p1.write_bytes(b"\x7fELF" + b"\0" * 12)
        p2.write_bytes(b"\x7fELF" + b"\0" * 12)
        result = runner.invoke(
            main, ["scan", "--artifact-set", f"{p1},{p2}"]
        )
        assert result.exit_code != 0
        assert "colliding library identities" in result.output


# ---------------------------------------------------------------------------
# Service layer: run_scan_set acceptance (JSON-snapshot members — no ELF
# parsing needed for the per-member scans; the bundle-audit step's own
# discover_artifact_set(explicit=True) rejects non-ELF members internally,
# which run_scan_set degrades to `bundle_incomplete=True` rather than
# raising — see run_scan_set's own docstring).
# ---------------------------------------------------------------------------


class TestRunScanSet:
    def test_rejects_single_binary(self, snap_a: Path) -> None:
        from abicheck.service import ScanRequest, run_scan_set

        with pytest.raises(ValueError):
            run_scan_set(ScanRequest(binaries=[snap_a]))

    def test_rejects_baseline(self, snap_a: Path, snap_b: Path) -> None:
        from abicheck.service import ScanRequest, run_scan_set

        with pytest.raises(ValueError):
            run_scan_set(
                ScanRequest(binaries=[snap_a, snap_b], baseline=str(snap_a))
            )

    def test_scans_every_member_and_marks_bundle_incomplete(
        self, snap_a: Path, snap_b: Path
    ) -> None:
        from abicheck.service import ScanRequest, ScanSetResult, run_scan_set

        result = run_scan_set(
            ScanRequest(binaries=[snap_a, snap_b], mode="audit")
        )
        assert isinstance(result, ScanSetResult)
        assert len(result.per_artifact) == 2
        assert {str(a.artifact) for a in result.per_artifact} == {
            str(snap_a),
            str(snap_b),
        }
        for member in result.per_artifact:
            assert member.result.verdict in ("COMPATIBLE", "API_BREAK")
        # Snapshot JSON members aren't real ELF, so the bundle-audit
        # discovery step can't run — degrades rather than raising.
        assert result.bundle_incomplete is True
        assert result.bundle_verdict is None
        d = result.to_dict()
        assert d["per_artifact"][0]["artifact"] == str(snap_a)
        assert d["bundle_incomplete"] is True

    def test_to_dict_roundtrip_shape(self, snap_a: Path, snap_b: Path) -> None:
        from abicheck.service import ScanRequest, run_scan_set

        result = run_scan_set(ScanRequest(binaries=[snap_a, snap_b]))
        d = result.to_dict()
        assert d["verdict"] == result.verdict
        assert d["exit_code"] == result.exit_code
        assert len(d["per_artifact"]) == 2


# ---------------------------------------------------------------------------
# Real end-to-end: two genuinely compiled, cross-referencing .so files, one
# missing an intra-dependency the other still imports. Needs gcc.
# ---------------------------------------------------------------------------


def _build_tiny_so(out_dir: Path, name: str, src: str, *, extra_ldflags: list[str] | None = None) -> Path:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc unavailable; cannot build artifact-set E2E fixture")
    src_dir = out_dir.parent / f"{out_dir.name}.sources"
    src_dir.mkdir(exist_ok=True)
    src_path = src_dir / f"{name}.c"
    src_path.write_text(src)
    out = out_dir / name
    soname = name.split(".so")[0] + ".so.1"
    cmd = [
        gcc, "-shared", "-fPIC", "-g", "-O0", str(src_path), "-o", str(out),
        "-Wl,-soname," + soname,
    ]
    cmd.extend(extra_ldflags or [])
    subprocess.run(cmd, check=True, capture_output=True)
    return out


@pytest.mark.integration
class TestArtifactSetCliEndToEnd:
    def test_scan_artifact_set_reports_unresolved_intra_dependency(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        libdir = tmp_path / "libs"
        libdir.mkdir()
        _build_tiny_so(
            libdir, "libcore.so", "int core_add(int a, int b){return a+b;}\n",
        )
        _build_tiny_so(
            libdir, "libuser.so",
            "extern int core_add(int, int);\n"
            "extern int core_missing(int, int);\n"
            "int use_add(int a, int b){return core_add(a, b);}\n"
            # Actually called (not just declared) so it lands in the dynamic
            # symbol table as an unresolved import — a mere declaration with
            # no call emits no reference at all.
            "int use_missing(int a, int b){return core_missing(a, b);}\n",
            extra_ldflags=[
                "-L", str(libdir), "-Wl,--no-as-needed", "-lcore",
                # core_missing is never provided — deliberately unresolved.
            ],
        )
        result = runner.invoke(
            main,
            [
                "scan",
                "--artifact-set",
                str(libdir),
                "--format",
                "json",
            ],
        )
        out = result.output
        i = out.find("{")
        payload = json.loads(out[i:] if i >= 0 else out)
        assert payload["bundle_incomplete"] is False
        assert len(payload["per_artifact"]) == 2
