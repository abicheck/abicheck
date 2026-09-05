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
as a real ELF shared object (not just the magic bytes) before a scan even
starts.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import abicheck.service  # noqa: F401 - force real import before any test

# monkeypatches abicheck.service_scan.run_scan_set: service.py's own
# `from .service_scan import run_scan_set` re-export only executes once, at
# first import -- if that first import happened lazily *after* a test had
# already patched service_scan.run_scan_set, service.run_scan_set would
# permanently bind to the patched value for the rest of the process.
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
from abicheck.service_scan import ScanArtifactResult, ScanResult


def _write_snapshot(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _bypass_discovery_validation(
    monkeypatch: pytest.MonkeyPatch, *binaries: Path
) -> None:
    """Patch discover_artifact_set() to a trivial passthrough for tests that
    exercise run_scan_set()'s per-member scanning, not the bundle-audit
    layer itself -- avoids needing real ELF fixtures (discover_artifact_set
    now propagates ArtifactSetError for non-ELF members instead of
    degrading, Codex review) while still reaching audit_bundle(), whose own
    build_bundle_snapshot() already tolerates non-ELF members by skipping
    them (the separate, still-degrading bundle-snapshot-completeness path
    tested by TestRunScanSetBundleSnapshotCompleteness).
    """
    import abicheck.bundle as bundle_mod

    def _fake_discover(paths, *, explicit):
        return {p.name: p for p in binaries}

    monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)


def _write_elf_shared_object_stub(path: Path) -> None:
    """Write a minimal, structurally-valid ELF64 shared-object (ET_DYN, no
    program headers) -- enough to pass package._is_elf_shared_object's
    magic/class/type/PT_INTERP-absence checks (discover_artifact_set's
    explicit-list form validates against that, not just the 4-byte magic
    sniff, since a bare-magic stub also passes as an executable/relocatable/
    core file -- Codex review), without needing a real compiled binary.
    """
    import struct

    data = bytearray(64)
    data[0:4] = b"\x7fELF"
    data[4] = 2  # ELFCLASS64
    data[5] = 1  # little-endian
    struct.pack_into("<H", data, 16, 3)  # e_type = ET_DYN
    struct.pack_into("<Q", data, 32, 0)  # e_phoff = 0
    struct.pack_into("<H", data, 56, 0)  # e_phnum = 0
    path.write_bytes(bytes(data))


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
            main, ["scan", str(snap_a), "--artifact-set", "x", "--artifact-set", "y"]
        )
        assert result.exit_code != 0
        assert "exactly one of ARTIFACT or --artifact-set" in result.output

    def test_rejects_neither_artifact_nor_artifact_set(
        self, runner: CliRunner
    ) -> None:
        result = runner.invoke(main, ["scan"])
        assert result.exit_code != 0
        assert "exactly one of ARTIFACT or --artifact-set" in result.output

    def test_rejects_empty_artifact_set_even_with_artifact(
        self, runner: CliRunner, snap_a: Path
    ) -> None:
        # P2 regression (CodeRabbit review): the exclusivity check used to
        # test bool(artifact_set) (False for "") while the branch after it
        # tested `is not None` (True for "") -- with ARTIFACT also given,
        # both passed the exclusivity check and ARTIFACT was silently
        # ignored, resolving the empty string to Path("") == Path(".") and
        # auditing the whole CWD instead of erroring.
        result = runner.invoke(
            main, ["scan", str(snap_a), "--artifact-set", ""]
        )
        assert result.exit_code != 0
        assert "--artifact-set must not be empty" in result.output

    def test_run_scan_set_artifact_set_error_becomes_usage_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # P2 regression (Codex review, x2): run_scan_set() propagates
        # audit_bundle()'s ambiguous-duplicate-SONAME ArtifactSetError
        # instead of degrading to a "successful" bundle_incomplete result
        # -- the CLI must convert that into a click.UsageError (exit 64),
        # not let it escape as an unhandled traceback.
        import abicheck.service_scan as service_scan_mod
        from abicheck.bundle import ArtifactSetError

        p1 = tmp_path / "liba.so"
        p2 = tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)

        def _fake_run_scan_set(req):
            raise ArtifactSetError("ambiguous duplicate SONAME provider(s)")

        monkeypatch.setattr(service_scan_mod, "run_scan_set", _fake_run_scan_set)

        result = runner.invoke(
            main, ["scan", "--artifact-set", str(p1), "--artifact-set", str(p2)]
        )
        assert result.exit_code != 0
        assert "ambiguous duplicate SONAME" in result.output

    def test_rejects_against_with_artifact_set(
        self, runner: CliRunner, snap_a: Path
    ) -> None:
        result = runner.invoke(
            main,
            ["scan", "--artifact-set", "x", "--artifact-set", "y", "--against", str(snap_a)],
        )
        assert result.exit_code != 0
        assert "--against is not supported with --artifact-set" in result.output

    def test_bundle_system_providers_flag_was_removed(
        self, runner: CliRunner, snap_a: Path
    ) -> None:
        """CLI cleanup phase two, PR J: --bundle-system-providers is gone,
        no deprecation alias -- the system-provider allow-list extension is
        sourced only from .abicheck.yml's `bundle:` block now, so there is
        no longer a "supplied without --artifact-set" combination to reject
        (the flag itself is what Click rejects)."""
        result = runner.invoke(
            main,
            ["scan", str(snap_a), "--bundle-system-providers", "libfoo.so.1"],
        )
        assert result.exit_code == 64
        assert "No such option" in result.output
        assert "--bundle-system-providers" in result.output

    def test_rejects_fewer_than_two_resolved_libraries(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # A single explicit member (no comma) is a valid --artifact-set
        # *syntax* but not a valid *set* — an audit needs 2+ libraries.
        only = tmp_path / "libonly.so"
        _write_elf_shared_object_stub(only)
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
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        result = runner.invoke(
            main, ["scan", "--artifact-set", str(p1), "--artifact-set", str(p2)]
        )
        assert result.exit_code != 0
        assert "colliding library identities" in result.output


class TestArtifactSetCompileContextForwarding:
    """P1 regression (Codex review): --compiler/--sysroot/etc. must reach
    ScanRequest.compile for --artifact-set, not silently fall back to the
    host toolchain the way an un-forwarded CompileContext() default would.
    """

    def test_forwards_compile_context_options(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.service_scan as service_scan_mod
        from abicheck.service_scan import ScanSetResult

        p1 = tmp_path / "liba.so"
        p2 = tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        sysroot_dir = tmp_path / "sysroot"
        sysroot_dir.mkdir()

        captured: dict[str, object] = {}

        def _fake_run_scan_set(req):
            captured["req"] = req
            return ScanSetResult(verdict="COMPATIBLE", exit_code=0)

        monkeypatch.setattr(service_scan_mod, "run_scan_set", _fake_run_scan_set)

        result = runner.invoke(
            main,
            [
                "scan",
                "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--compiler", "/usr/bin/my-cross-gcc",
                "--sysroot", str(sysroot_dir),
                "--nostdinc",
            ],
        )
        assert result.exit_code == 0, result.output
        req = captured["req"]
        assert req.compile.gcc_path == "/usr/bin/my-cross-gcc"
        assert req.compile.sysroot == sysroot_dir
        assert req.compile.nostdinc is True

    def test_cwd_abicheck_yml_bundle_block_reaches_scan_request(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex review, fresh evidence: --bundle-system-providers has no CLI
        # flag any more (PR J), and its .abicheck.yml replacement was only
        # ever discovered from an explicit --build-config or --sources --
        # never cwd-upward, unlike compare's own directory/package fan-out --
        # so a cwd .abicheck.yml with neither given was silently never read.
        import abicheck.service_scan as service_scan_mod
        from abicheck.service_scan import ScanSetResult

        p1 = tmp_path / "liba.so"
        p2 = tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        (tmp_path / ".abicheck.yml").write_text(
            'bundle:\n  system_providers: ["libvendor.so.1"]\n', encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

        captured: dict[str, object] = {}

        def _fake_run_scan_set(req):
            captured["req"] = req
            return ScanSetResult(verdict="COMPATIBLE", exit_code=0)

        monkeypatch.setattr(service_scan_mod, "run_scan_set", _fake_run_scan_set)

        result = runner.invoke(
            main, ["scan", "--artifact-set", str(p1), "--artifact-set", str(p2)]
        )
        assert result.exit_code == 0, result.output
        assert captured["req"].bundle_system_providers == ("libvendor.so.1",)

    def test_malformed_cwd_bundle_block_is_rejected_not_silently_dropped(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex review: bundle: has no CLI-flag fallback any more (PR J
        # removed --bundle-system-providers), so a malformed ambient config
        # must fail loud, not silently read as "no providers declared".
        p1 = tmp_path / "liba.so"
        p2 = tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        (tmp_path / ".abicheck.yml").write_text(
            "bundle:\n  system_providers: 123\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            main, ["scan", "--artifact-set", str(p1), "--artifact-set", str(p2)]
        )
        assert result.exit_code == 64, result.output
        assert "cannot parse build config" in result.output


class TestArtifactSetSourceMethodSelection:
    """P2 regression (Codex review): omitting --depth must opt an
    --artifact-set scan into risk-driven auto method selection, the same
    as the single-binary path -- source_method was hard-coded to None,
    silently disabling --since/--changed-path risk-driven selection.
    """

    def _capture_req(self, monkeypatch: pytest.MonkeyPatch) -> dict:
        import abicheck.service_scan as service_scan_mod
        from abicheck.service_scan import ScanSetResult

        captured: dict[str, object] = {}

        def _fake_run_scan_set(req):
            captured["req"] = req
            return ScanSetResult(verdict="COMPATIBLE", exit_code=0)

        monkeypatch.setattr(service_scan_mod, "run_scan_set", _fake_run_scan_set)
        return captured

    def test_no_depth_opts_into_auto(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p1 = tmp_path / "liba.so"
        p2 = tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        captured = self._capture_req(monkeypatch)

        result = runner.invoke(
            main, ["scan", "--artifact-set", str(p1), "--artifact-set", str(p2)]
        )
        assert result.exit_code == 0, result.output
        assert captured["req"].source_method == "auto"

    def test_explicit_depth_stays_pinned(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p1 = tmp_path / "liba.so"
        p2 = tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        captured = self._capture_req(monkeypatch)

        result = runner.invoke(
            main,
            ["scan", "--artifact-set", str(p1), "--artifact-set", str(p2), "--depth", "binary"],
        )
        assert result.exit_code == 0, result.output
        assert captured["req"].source_method is None


# ---------------------------------------------------------------------------
# Service layer: run_scan_set acceptance (JSON-snapshot members — no ELF
# parsing needed for the per-member scans; the bundle-audit step's own
# discover_artifact_set(explicit=True) rejects non-ELF members internally,
# which run_scan_set degrades to `bundle_incomplete=True` rather than
# raising — see run_scan_set's own docstring).
# ---------------------------------------------------------------------------


class TestRunScanSetBundleAuditDeadline:
    """P1 regression (Codex review): audit_bundle() has no internal
    deadline, so a slow bundle-audit phase must be caught by re-checking
    elapsed time after it returns -- else run_scan_set could report a
    normal verdict despite having run well past --budget.
    """

    def test_slow_bundle_audit_reports_budget_overflow(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time as time_mod

        import abicheck.bundle as bundle_mod
        from abicheck.change_registry_types import Verdict
        from abicheck.service import Budget, ScanRequest, run_scan_set

        clock = {"t": 1000.0}
        monkeypatch.setattr(time_mod, "monotonic", lambda: clock["t"])

        def _fake_discover(paths, *, explicit):
            return {"liba.so": snap_a, "libb.so": snap_b}

        class _FakeAudit:
            findings = []
            verdict = Verdict.COMPATIBLE

        def _fake_audit_bundle(libraries, *, bundle_system_providers=(), manifest=None):
            # Simulate a pathologically slow resolution-graph build that
            # blows through the budget while audit_bundle is running.
            clock["t"] += 1000.0
            return _FakeAudit()

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)
        monkeypatch.setattr(bundle_mod, "audit_bundle", _fake_audit_bundle)

        result = run_scan_set(
            ScanRequest(
                binaries=[snap_a, snap_b],
                mode="audit",
                budget=Budget(total_timeout=5.0),
            )
        )
        assert result.verdict == "BUDGET_OVERFLOW"
        assert result.exit_code == 5

    def test_deadline_exceeded_inside_audit_bundle_reports_budget_overflow(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # P2 regression (Codex review): the elapsed-time re-check above only
        # catches an overflow *after* audit_bundle() returns -- it doesn't
        # bound the call itself. run_scan_set now runs audit_bundle() under
        # deadline.deadline_scope(), so build_bundle_snapshot()'s per-
        # library deadline.check() (a real deadline-scope trip, tested at
        # the unit level in test_bundle.py) can raise DeadlineExceeded
        # *during* the call; this isolates run_scan_set's own handling of
        # that exception without depending on deadline internals.
        import abicheck.bundle as bundle_mod
        from abicheck import deadline
        from abicheck.service import Budget, ScanRequest, ScanSetResult, run_scan_set

        def _fake_discover(paths, *, explicit):
            return {"liba.so": snap_a, "libb.so": snap_b}

        def _fake_audit_bundle(libraries, *, bundle_system_providers=(), manifest=None):
            raise deadline.DeadlineExceeded(-1.0)

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)
        monkeypatch.setattr(bundle_mod, "audit_bundle", _fake_audit_bundle)

        result = run_scan_set(
            ScanRequest(
                binaries=[snap_a, snap_b],
                mode="audit",
                budget=Budget(total_timeout=5.0),
            )
        )
        assert isinstance(result, ScanSetResult)
        assert result.verdict == "BUDGET_OVERFLOW"
        assert result.exit_code == 5

    def test_slow_discovery_counts_against_budget(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # P2 regression (Codex review): run_scan_set started its shared
        # budget clock *after* discover_artifact_set() already ran, so
        # discovery's own work (stat + ELF program/dynamic-table parsing
        # for every candidate) was invisible to --budget -- even
        # `--budget 0s` could spend real time discovering before ever
        # reporting overflow. The clock must start before discovery, not
        # after, so a slow discovery phase is caught the same way a slow
        # member scan or bundle audit already is.
        import time as time_mod

        import abicheck.bundle as bundle_mod
        from abicheck.service import Budget, ScanRequest, ScanSetResult, run_scan_set

        clock = {"t": 1000.0}
        monkeypatch.setattr(time_mod, "monotonic", lambda: clock["t"])

        def _fake_discover(paths, *, explicit):
            # Simulate a pathologically slow discovery phase that blows
            # through the budget before any member is even scanned.
            clock["t"] += 1000.0
            return {"liba.so": snap_a, "libb.so": snap_b}

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)

        result = run_scan_set(
            ScanRequest(
                binaries=[snap_a, snap_b],
                mode="audit",
                budget=Budget(total_timeout=5.0),
            )
        )
        assert isinstance(result, ScanSetResult)
        assert result.verdict == "BUDGET_OVERFLOW"
        assert result.exit_code == 5


class TestRunScanSetExportUnionDeadline:
    """G35: the export-union pass (`artifact_set_member_exports`) added ahead
    of the per-member loop must honor `--budget` the same way the soname
    check and `audit_bundle` already do -- both a budget already exhausted
    by the time it would run, and a `DeadlineExceeded` raised *during* the
    call itself, must report `BUDGET_OVERFLOW` rather than silently
    proceeding to scan every member past the deadline.
    """

    def test_exhausted_budget_before_export_pass_reports_overflow(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time as time_mod

        import abicheck.bundle as bundle_mod
        from abicheck.service import Budget, ScanRequest, run_scan_set

        clock = {"t": 1000.0}
        monkeypatch.setattr(time_mod, "monotonic", lambda: clock["t"])

        def _fake_discover(paths, *, explicit):
            return {"liba.so": snap_a, "libb.so": snap_b}

        def _fake_soname_check(libraries):
            # Simulate the soname check itself consuming the whole budget,
            # so the export-union pass right after it starts with none left.
            clock["t"] += 1000.0

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)
        monkeypatch.setattr(
            bundle_mod, "check_artifact_set_soname_collisions", _fake_soname_check
        )

        result = run_scan_set(
            ScanRequest(
                binaries=[snap_a, snap_b],
                mode="audit",
                budget=Budget(total_timeout=5.0),
            )
        )
        assert result.verdict == "BUDGET_OVERFLOW"
        assert result.exit_code == 5

    def test_slow_export_pass_completing_over_budget_reports_overflow(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # P2 regression (Codex review): artifact_set_member_exports() has no
        # checkpoint *after* its last member's parse -- a pathologically
        # slow final parse that completes without itself tripping
        # deadline_scope must still be caught by an elapsed-time recheck
        # right after the export pass returns, before the (potentially
        # expensive) per-member scan loop starts.
        import time as time_mod

        import abicheck.bundle as bundle_mod
        from abicheck.service import Budget, ScanRequest, run_scan_set

        clock = {"t": 1000.0}
        monkeypatch.setattr(time_mod, "monotonic", lambda: clock["t"])

        def _fake_discover(paths, *, explicit):
            return {"liba.so": snap_a, "libb.so": snap_b}

        def _fake_member_exports(libraries):
            # Simulate a slow final parse that returns normally (no
            # DeadlineExceeded) but has already blown through the budget.
            clock["t"] += 1000.0
            return {name: frozenset() for name in libraries}

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)
        monkeypatch.setattr(
            bundle_mod, "artifact_set_member_exports", _fake_member_exports
        )

        result = run_scan_set(
            ScanRequest(
                binaries=[snap_a, snap_b],
                mode="audit",
                budget=Budget(total_timeout=5.0),
            )
        )
        assert result.verdict == "BUDGET_OVERFLOW"
        assert result.exit_code == 5

    def test_deadline_exceeded_inside_export_pass_reports_overflow(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.bundle as bundle_mod
        from abicheck import deadline
        from abicheck.service import Budget, ScanRequest, ScanSetResult, run_scan_set

        def _fake_discover(paths, *, explicit):
            return {"liba.so": snap_a, "libb.so": snap_b}

        def _fake_member_exports(libraries):
            raise deadline.DeadlineExceeded(-1.0)

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)
        monkeypatch.setattr(
            bundle_mod, "artifact_set_member_exports", _fake_member_exports
        )

        result = run_scan_set(
            ScanRequest(
                binaries=[snap_a, snap_b],
                mode="audit",
                budget=Budget(total_timeout=5.0),
            )
        )
        assert isinstance(result, ScanSetResult)
        assert result.verdict == "BUDGET_OVERFLOW"
        assert result.exit_code == 5


class TestRunScanSetAmbiguousSoname:
    """P2 regression (Codex review, x2): audit_bundle() rejects an
    ambiguous duplicate-SONAME set (ArtifactSetError). Originally degraded
    to bundle_incomplete=True the same way a discover_artifact_set failure
    does -- but unlike that check (a redundant collision check that's
    supposed to have already run at the CLI/MCP front door), this is the
    *only* point a duplicate SONAME is detectable (it needs the parsed ELF
    metadata), so degrading it let a genuinely invalid set exit 0 with the
    cross-library audit silently skipped. run_scan_set now propagates it
    instead, and the CLI (cli_scan._run_artifact_set) converts it to a
    click.UsageError (exit 64) the same way discover_artifact_set's own
    ArtifactSetError already is.
    """

    def test_ambiguous_soname_propagates(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.bundle as bundle_mod
        from abicheck.bundle import ArtifactSetError
        from abicheck.service import ScanRequest, run_scan_set

        def _fake_discover(paths, *, explicit):
            return {"liba.so": snap_a, "libb.so": snap_b}

        def _fake_audit_bundle(libraries, *, bundle_system_providers=(), manifest=None):
            raise ArtifactSetError("ambiguous duplicate SONAME provider(s)")

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)
        monkeypatch.setattr(bundle_mod, "audit_bundle", _fake_audit_bundle)

        with pytest.raises(ArtifactSetError, match="ambiguous duplicate SONAME"):
            run_scan_set(ScanRequest(binaries=[snap_a, snap_b], mode="audit"))


class TestRunScanSetDiscoveryArtifactSetError:
    """P2 regression (Codex review): run_scan_set() is a public,
    re-exported service entry point -- a direct Python API caller can
    reach discover_artifact_set()'s own ArtifactSetError (e.g. a
    collision under _canonical_library_key) without ever going through
    cli_scan._run_artifact_set's own prevalidation. Degrading it to
    bundle_incomplete=True let such a request return an exit-0-looking
    "successful" result despite the set never having been auditable; must
    propagate, mirroring the audit_bundle() ArtifactSetError fix.
    """

    def test_collision_propagates(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.bundle as bundle_mod
        from abicheck.bundle import ArtifactSetError
        from abicheck.service import ScanRequest, run_scan_set

        def _fake_discover(paths, *, explicit):
            raise ArtifactSetError("colliding library identities")

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)

        with pytest.raises(ArtifactSetError, match="colliding library identities"):
            run_scan_set(ScanRequest(binaries=[snap_a, snap_b], mode="audit"))

    def test_validates_before_scanning_any_member(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # P2 regression (Codex review, x2): discovery must run *before* the
        # per-member scan loop -- besides running needless work for an
        # invalid request, scanning first meant a member could exhaust the
        # budget and return BUDGET_OVERFLOW before discovery ever ran,
        # masking the real ArtifactSetError behind a budget error instead.
        import abicheck.bundle as bundle_mod
        import abicheck.service_scan as service_scan_mod
        from abicheck.bundle import ArtifactSetError
        from abicheck.service import ScanRequest, run_scan_set

        def _fake_discover(paths, *, explicit):
            raise ArtifactSetError("colliding library identities")

        member_scan_called = []

        def _fake_run_scan_one_member(*args, **kwargs):
            member_scan_called.append(True)
            raise AssertionError("member scan must not run before discovery")

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)
        monkeypatch.setattr(
            service_scan_mod, "_run_scan_one_member", _fake_run_scan_one_member
        )

        with pytest.raises(ArtifactSetError):
            run_scan_set(ScanRequest(binaries=[snap_a, snap_b], mode="audit"))
        assert member_scan_called == []


class TestRunScanSetSonameCollisionEarlyCheck:
    """P2 regression (Codex review): an ambiguous duplicate-DT_SONAME set
    was previously only discovered inside audit_bundle(), which runs after
    every member's own scan. If an earlier member scan then exhausted
    --budget, the real ArtifactSetError was masked as an ordinary
    BUDGET_OVERFLOW, and every member's own (potentially expensive) scan
    already ran for a request that was always going to be rejected.
    check_artifact_set_soname_collisions() now runs right after discovery,
    before any member is scanned.
    """

    def test_collision_propagates_before_scanning_any_member(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.bundle as bundle_mod
        import abicheck.service_scan as service_scan_mod
        from abicheck.bundle import ArtifactSetError
        from abicheck.service import ScanRequest, run_scan_set

        _bypass_discovery_validation(monkeypatch, snap_a, snap_b)

        def _fake_soname_check(libraries):
            raise ArtifactSetError("ambiguous duplicate SONAME provider(s)")

        member_scan_called = []

        def _fake_run_scan_one_member(*args, **kwargs):
            member_scan_called.append(True)
            raise AssertionError("member scan must not run before the SONAME check")

        monkeypatch.setattr(
            bundle_mod, "check_artifact_set_soname_collisions", _fake_soname_check
        )
        monkeypatch.setattr(
            service_scan_mod, "_run_scan_one_member", _fake_run_scan_one_member
        )

        with pytest.raises(ArtifactSetError, match="ambiguous duplicate SONAME"):
            run_scan_set(ScanRequest(binaries=[snap_a, snap_b], mode="audit"))
        assert member_scan_called == []


class TestRunScanSetBundleSnapshotCompleteness:
    """P2 regression (Codex review): discover_artifact_set only validates
    that every member *looks like* ELF -- build_bundle_snapshot() can still
    silently drop one whose actual parse failed or came back empty. A
    reduced resolution graph missing a real provider can invent a false
    unresolved-import finding, so a declared member missing from the
    returned snapshot must mark the audit incomplete rather than publish
    findings computed from an incomplete graph.
    """

    def test_dropped_member_marks_bundle_incomplete(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.bundle as bundle_mod
        from abicheck.bundle import BundleSnapshot
        from abicheck.change_registry_types import Verdict
        from abicheck.service import ScanRequest, run_scan_set

        def _fake_discover(paths, *, explicit):
            return {"liba.so": snap_a, "libb.so": snap_b}

        class _FakeAudit:
            # Only liba.so survived parsing -- libb.so silently dropped.
            snapshot = BundleSnapshot(
                root=snap_a.parent, libraries={"liba.so": snap_a},
                metadata={}, resolution=None,
            )
            findings = []
            verdict = Verdict.COMPATIBLE_WITH_RISK

        def _fake_audit_bundle(libraries, *, bundle_system_providers=(), manifest=None):
            return _FakeAudit()

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)
        monkeypatch.setattr(bundle_mod, "audit_bundle", _fake_audit_bundle)

        result = run_scan_set(
            ScanRequest(binaries=[snap_a, snap_b], mode="audit")
        )
        assert result.bundle_incomplete is True
        assert result.bundle_findings == []
        assert result.bundle_verdict is None

    def test_dropped_member_with_clean_scans_sets_nonzero_exit(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P2 regression (Codex review): bundle_incomplete previously left
        the set-level exit code purely a function of the per-member
        verdicts -- the cross-library audit itself never ran, but a set
        whose every member scanned clean (COMPATIBLE here) still exited 0,
        so a CI gate that only checks the exit code silently accepted a
        skipped cross-library audit as a full pass.
        """
        import abicheck.bundle as bundle_mod
        import abicheck.service_scan as service_scan_mod
        from abicheck.bundle import BundleSnapshot
        from abicheck.change_registry_types import Verdict
        from abicheck.service import ScanRequest, run_scan_set

        def _fake_discover(paths, *, explicit):
            return {"liba.so": snap_a, "libb.so": snap_b}

        class _FakeAudit:
            # Only liba.so survived parsing -- libb.so silently dropped.
            snapshot = BundleSnapshot(
                root=snap_a.parent, libraries={"liba.so": snap_a},
                metadata={}, resolution=None,
            )
            findings = []
            verdict = Verdict.COMPATIBLE_WITH_RISK

        def _fake_audit_bundle(libraries, *, bundle_system_providers=(), manifest=None):
            return _FakeAudit()

        def _fake_run_one_member(
            req,
            binary,
            *,
            start,
            budget_s,
            changed_src,
            sibling_exported_symbols=frozenset(),
        ):
            return ScanResult(verdict="COMPATIBLE", exit_code=0)

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)
        monkeypatch.setattr(bundle_mod, "audit_bundle", _fake_audit_bundle)
        monkeypatch.setattr(
            service_scan_mod, "_run_scan_one_member", _fake_run_one_member
        )

        result = run_scan_set(
            ScanRequest(binaries=[snap_a, snap_b], mode="audit")
        )
        assert result.bundle_incomplete is True
        assert result.verdict == "BUNDLE_INCOMPLETE"
        assert result.exit_code == 1


class TestRunScanSetLevelExplicit:
    """P2 regression (Codex review): an explicit --depth with no
    source_method must set level_explicit=True on the run_scan_core call
    for each member -- otherwise a trusted --config's build.query never
    auto-runs for --artifact-set despite the caller explicitly requesting
    a deep depth, unlike the single-binary CLI path.
    """

    def test_explicit_depth_forwards_level_explicit(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.scan_engine as scan_engine_mod
        from abicheck.service import ScanRequest, run_scan_set

        captured: dict[str, object] = {}
        real_run_scan_core = scan_engine_mod.run_scan_core

        def _spy(*args, **kwargs):
            captured["level_explicit"] = kwargs.get("level_explicit")
            return real_run_scan_core(*args, **kwargs)

        monkeypatch.setattr(scan_engine_mod, "run_scan_core", _spy)
        _bypass_discovery_validation(monkeypatch, snap_a, snap_b)

        run_scan_set(
            ScanRequest(binaries=[snap_a, snap_b], mode="audit", depth="binary")
        )
        assert captured["level_explicit"] is True


class TestRunScanSet:
    def test_rejects_single_binary(self, snap_a: Path) -> None:
        from abicheck.service import ScanRequest, run_scan_set

        with pytest.raises(ValueError):
            run_scan_set(ScanRequest(binaries=[snap_a]))

    def test_default_mode_is_normalized_to_audit(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # P2 regression (Codex review): the documented minimal form
        # run_scan_set(ScanRequest(binaries=[...])) left req.mode at
        # ScanRequest's own "pr" default (run_scan_set never overrode it
        # before forwarding to each member) -- every member's report
        # claimed mode: "pr" despite this entry point being audit-only by
        # definition (ADR-056 D2, no baseline accepted).
        from abicheck.service import ScanRequest, run_scan_set

        _bypass_discovery_validation(monkeypatch, snap_a, snap_b)
        result = run_scan_set(ScanRequest(binaries=[snap_a, snap_b]))
        for member in result.per_artifact:
            report = member.result.report
            assert report is not None
            assert report["mode"] == "audit"

    def test_rejects_baseline(self, snap_a: Path, snap_b: Path) -> None:
        from abicheck.service import ScanRequest, run_scan_set

        with pytest.raises(ValueError):
            run_scan_set(
                ScanRequest(binaries=[snap_a, snap_b], baseline=str(snap_a))
            )

    def test_rejects_comparison_only_field_without_baseline(
        self, snap_a: Path, snap_b: Path
    ) -> None:
        """P2 regression (Codex review): run_scan_set() only ever validated
        req.baseline itself -- a direct Python API caller passing a
        comparison-only field like env_matrix/policy_file/suppression to
        this public, re-exported entry point got it silently accepted and
        discarded under default audit classification, unlike an equivalent
        run_scan() call (which already rejects the same fields when there's
        no baseline).
        """
        from abicheck.environment_matrix import EnvironmentMatrix
        from abicheck.errors import ValidationError
        from abicheck.service import ScanRequest, run_scan_set

        with pytest.raises(ValidationError, match="env_matrix"):
            run_scan_set(
                ScanRequest(
                    binaries=[snap_a, snap_b],
                    env_matrix=EnvironmentMatrix(),
                )
            )

    def test_scans_every_member_and_marks_bundle_incomplete(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.service import ScanRequest, ScanSetResult, run_scan_set

        _bypass_discovery_validation(monkeypatch, snap_a, snap_b)
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

    def test_to_dict_roundtrip_shape(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.service import ScanRequest, run_scan_set

        _bypass_discovery_validation(monkeypatch, snap_a, snap_b)
        result = run_scan_set(ScanRequest(binaries=[snap_a, snap_b]))
        d = result.to_dict()
        assert d["verdict"] == result.verdict
        assert d["exit_code"] == result.exit_code
        assert len(d["per_artifact"]) == 2

    def test_rejects_duplicate_binary_even_when_listed_twice(
        self, snap_a: Path
    ) -> None:
        # P2 regression (Codex review): the same path (or a symlink alias)
        # passed twice must not silently satisfy the "2 or more binaries"
        # cardinality check -- discover_artifact_set dedupes via
        # Path.resolve() later, so an undeduplicated pair would be reported
        # as a "complete" 2-member audit of what's really one library.
        from abicheck.service import ScanRequest, run_scan_set

        with pytest.raises(ValueError, match="distinct"):
            run_scan_set(ScanRequest(binaries=[snap_a, snap_a]))

    def test_rejects_symlink_alias_of_same_binary(
        self, snap_a: Path, tmp_path: Path
    ) -> None:
        from abicheck.service import ScanRequest, run_scan_set

        alias = tmp_path / "alias.abi.json"
        try:
            alias.symlink_to(snap_a)
        except OSError:
            pytest.skip("symlinks unsupported in this environment")

        with pytest.raises(ValueError, match="distinct"):
            run_scan_set(ScanRequest(binaries=[snap_a, alias]))

    def test_rejects_hard_link_alias_of_same_binary(
        self, snap_a: Path, tmp_path: Path
    ) -> None:
        # P2 regression (Codex review): Path.resolve() only follows
        # symlinks, not hard links -- two hard-linked aliases of the same
        # binary previously passed the cardinality check as if they were
        # distinct members.
        import os

        from abicheck.service import ScanRequest, run_scan_set

        alias = tmp_path / "alias.abi.json"
        try:
            os.link(snap_a, alias)
        except OSError:
            pytest.skip("hard links unsupported in this environment")

        with pytest.raises(ValueError, match="distinct"):
            run_scan_set(ScanRequest(binaries=[snap_a, alias]))

    def test_forwards_changed_src_to_every_member(
        self, snap_a: Path, snap_b: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # P2 regression (Codex review): run_scan_set previously hard-coded
        # every member's changed_path_source to the literal "run_scan_set",
        # discarding the real --since/--changed-path provenance a caller
        # (e.g. cli_scan._run_artifact_set) computes and passes through
        # ScanRequest.changed_src.
        from abicheck.service import ScanRequest, run_scan_set

        _bypass_discovery_validation(monkeypatch, snap_a, snap_b)
        result = run_scan_set(
            ScanRequest(
                binaries=[snap_a, snap_b],
                mode="audit",
                changed_src="--since origin/main",
            )
        )
        for member in result.per_artifact:
            report = member.result.report
            assert report is not None
            assert report["changed_paths"]["source"] == "--since origin/main"


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


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Uses GNU ld flags (-Wl,-soname); Mach-O ld and link.exe don't accept them.",
)
@pytest.mark.integration
class TestArtifactSetSiblingExports:
    """G35: ``run_scan_set`` must tell each member's own crosscheck about
    what *sibling* members export, so a shared umbrella header naming more
    than one member's own public API doesn't false-positive on
    ``public_not_exported`` (previously an acknowledged limitation, see
    AGENTS.md's "multi-artifact scan" entry).
    """

    def test_member_exports_are_read_per_member(self, tmp_path: Path) -> None:
        from abicheck.bundle import artifact_set_member_exports

        libdir = tmp_path / "libs"
        libdir.mkdir()
        core = _build_tiny_so(libdir, "libcore.so", "int core_fn(void){return 1;}\n")
        algo = _build_tiny_so(libdir, "libalgo.so", "int algo_fn(void){return 2;}\n")
        exports = artifact_set_member_exports({"libcore.so": core, "libalgo.so": algo})
        assert exports["libcore.so"] == {"core_fn"}
        assert exports["libalgo.so"] == {"algo_fn"}

    def test_run_scan_set_forwards_sibling_exports_to_each_member(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A shared umbrella header declares both core_fn (exported only by
        # libcore.so) and algo_fn (exported only by libalgo.so) -- each
        # member's own scan must see the *other* member's export(s) as
        # sibling-satisfied, and never its own (that's already covered by
        # its own export table).
        import abicheck.service_scan as service_scan

        libdir = tmp_path / "libs"
        libdir.mkdir()
        core = _build_tiny_so(libdir, "libcore.so", "int core_fn(void){return 1;}\n")
        algo = _build_tiny_so(libdir, "libalgo.so", "int algo_fn(void){return 2;}\n")

        seen: dict[str, frozenset] = {}
        real_run_one_member = service_scan._run_scan_one_member

        def _spy(req, binary, **kwargs):
            seen[str(binary)] = kwargs.get("sibling_exported_symbols", frozenset())
            return real_run_one_member(req, binary, **kwargs)

        monkeypatch.setattr(service_scan, "_run_scan_one_member", _spy)

        from abicheck.service import ScanRequest

        service_scan.run_scan_set(ScanRequest(binaries=[core, algo]))

        assert seen[str(core)] == {"algo_fn"}
        assert seen[str(algo)] == {"core_fn"}


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Uses GNU ld flags (-Wl,-soname, -Wl,--no-as-needed); "
    "Mach-O ld and link.exe don't accept them. Bundle analysis "
    "itself is ELF/Linux-only per ADR-018 / ADR-023.",
)
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
        # Per ScanSetResult's own aggregation (_SCAN_SET_COMPAT_EXIT), the
        # only possible non-zero exit codes here are 2 (API_BREAK) and 4
        # (BREAKING) -- COMPATIBLE_WITH_RISK (what the unresolved-intra-
        # dependency finding itself maps to) is exit 0.
        assert result.exit_code in (0, 2, 4), out
        i = out.find("{")
        payload = json.loads(out[i:] if i >= 0 else out)
        assert payload["bundle_incomplete"] is False
        assert len(payload["per_artifact"]) == 2
        # The actual point of this fixture: core_missing is deliberately
        # never provided, so the audit must report it as an unresolved
        # intra-bundle dependency (CodeRabbit review -- this was previously
        # unverified, the only assertions were on bundle_incomplete/member
        # count, not the detector's actual output).
        assert any(
            f["kind"] == "bundle_unresolved_intra_dependency"
            and f["symbol"] == "core_missing"
            for f in payload["bundle_findings"]
        ), payload


class TestAggregateScanSetVerdict:
    """P2 regression (Codex review): NO_CHANGE and COMPATIBLE previously
    shared rank 0 in _SCAN_SET_COMPAT_ORDER, and the aggregation loop's
    ``>=`` tie-break means the *last* rank-0 candidate wins. The bundle
    audit's own verdict is always appended last to the candidate list and
    reads NO_CHANGE whenever it simply found nothing to flag -- so a set
    where every member scanned COMPATIBLE, with an uneventful bundle audit,
    misreported the whole set as NO_CHANGE instead of COMPATIBLE.
    """

    def _member(self, verdict: str) -> ScanArtifactResult:
        return ScanArtifactResult(
            artifact=Path("lib.so"), result=ScanResult(verdict=verdict, exit_code=0)
        )

    def test_all_compatible_members_with_no_change_bundle_stays_compatible(
        self,
    ) -> None:
        from abicheck.service_scan import _aggregate_scan_set_verdict

        per_artifact = [self._member("COMPATIBLE"), self._member("COMPATIBLE")]
        verdict, exit_code = _aggregate_scan_set_verdict(per_artifact, "NO_CHANGE")
        assert verdict == "COMPATIBLE"
        assert exit_code == 0

    def test_all_no_change_stays_no_change(self) -> None:
        from abicheck.service_scan import _aggregate_scan_set_verdict

        per_artifact = [self._member("NO_CHANGE"), self._member("NO_CHANGE")]
        verdict, exit_code = _aggregate_scan_set_verdict(per_artifact, "NO_CHANGE")
        assert verdict == "NO_CHANGE"
        assert exit_code == 0

    def test_breaking_still_dominates_compatible(self) -> None:
        from abicheck.service_scan import _aggregate_scan_set_verdict

        per_artifact = [self._member("COMPATIBLE"), self._member("BREAKING")]
        verdict, exit_code = _aggregate_scan_set_verdict(per_artifact, "NO_CHANGE")
        assert verdict == "BREAKING"
        assert exit_code == 4


class TestArtifactSetComparisonOnlyFlagsRejected:
    """P2 regression (Codex review): --artifact-set is always audit-only
    (never has --against), but the "these flags only mean anything with
    --against" guard only ran on the single-binary path -- the
    --artifact-set branch's own early ``return`` bypassed it entirely, so
    e.g. --suppress/--policy/--env-matrix were silently parsed and
    discarded for a set instead of erroring.
    """

    def test_suppress_without_against_is_rejected(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        suppress_file = tmp_path / "suppress.yml"
        suppress_file.write_text("suppressions: []\n", encoding="utf-8")

        result = runner.invoke(
            main,
            [
                "scan",
                "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--suppress", str(suppress_file),
            ],
        )
        assert result.exit_code != 0
        assert "--suppress" in result.output
        assert "only take effect with --against" in result.output

    def test_env_matrix_without_against_is_rejected(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        matrix_file = tmp_path / "matrix.yml"
        matrix_file.write_text("environments: []\n", encoding="utf-8")

        result = runner.invoke(
            main,
            [
                "scan",
                "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--env-matrix", str(matrix_file),
            ],
        )
        assert result.exit_code != 0
        assert "--env-matrix" in result.output
        assert "only take effect with --against" in result.output


class TestArtifactSetMalformedRiskRules:
    """P2 regression (Codex review): run_scan_set() loads --risk-rules via
    the click-free _load_risk_rules_for_service(), which converts the
    single-binary path's click.ClickException into a plain ValueError --
    _run_artifact_set()'s own try/except only caught ArtifactSetError, so a
    malformed/unreadable --risk-rules file surfaced as an unhandled Python
    traceback and exit 1 instead of a clean usage error (exit 64).
    """

    def test_malformed_risk_rules_yaml_is_usage_error(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        bad = tmp_path / "rules.yml"
        bad.write_text("risk_rules: { unclosed: [1, 2", encoding="utf-8")

        result = runner.invoke(
            main,
            ["scan", "--artifact-set", str(p1), "--artifact-set", str(p2), "--risk-rules", str(bad)],
        )
        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "cannot read --risk-rules" in result.output


class TestRenderArtifactSetTextFindings:
    """P2 regression (Codex review): the artifact-set text report's
    per-artifact loop previously printed only ``path: verdict`` -- unlike
    the single-binary scan's own richly-rendered report and the aggregate
    JSON's nested ``report``, it gave no finding descriptions or evidence
    for *why* a member was flagged, leaving CLI/Action-summary users unable
    to act on the result.
    """

    def test_member_crosscheck_and_pattern_findings_are_rendered(self) -> None:
        from abicheck.cli_scan import _render_artifact_set_text
        from abicheck.service_scan import ScanArtifactResult, ScanResult, ScanSetResult

        member_report = {
            "mode": "audit",
            "crosscheck": {"counts_by_check": {"symbol_binding_weakened": 2}},
            "crosscheck_severities": {"symbol_binding_weakened": "warning"},
            "pattern_scan": {"counts_by_kind": {"header_only_macro_leak": 1}},
            "preprocessor_scan": {},
        }
        result = ScanSetResult(
            verdict="COMPATIBLE_WITH_RISK",
            exit_code=0,
            per_artifact=[
                ScanArtifactResult(
                    artifact=Path("liba.so"),
                    result=ScanResult(
                        verdict="COMPATIBLE_WITH_RISK",
                        exit_code=0,
                        report=member_report,
                    ),
                )
            ],
            bundle_incomplete=True,
        )
        text = _render_artifact_set_text(result)
        assert "symbol_binding_weakened: 2" in text
        assert "header_only_macro_leak: 1" in text

    def test_member_with_no_findings_renders_no_extra_lines(self) -> None:
        from abicheck.cli_scan import _render_member_findings_lines
        from abicheck.service_scan import ScanResult

        clean = ScanResult(
            verdict="COMPATIBLE",
            exit_code=0,
            report={
                "mode": "audit",
                "crosscheck": {},
                "crosscheck_severities": {},
                "pattern_scan": {},
                "preprocessor_scan": {},
            },
        )
        assert _render_member_findings_lines(clean) == []

    def test_budget_overflow_renders_bundle_as_not_run(self) -> None:
        """CodeRabbit review: run_scan_set() returns BUDGET_OVERFLOW before
        ever calling audit_bundle() -- bundle_incomplete/bundle_verdict stay
        at their ScanSetResult defaults (False/None), so without a dedicated
        branch the report fell through to the else clause and printed the
        misleading "Bundle analysis: None (0 finding(s))" instead of stating
        the bundle audit never ran.
        """
        from abicheck.cli_scan import _render_artifact_set_text
        from abicheck.service_scan import ScanArtifactResult, ScanResult, ScanSetResult

        result = ScanSetResult(
            verdict="BUDGET_OVERFLOW",
            exit_code=5,
            per_artifact=[
                ScanArtifactResult(
                    artifact=Path("liba.so"),
                    result=ScanResult(verdict="COMPATIBLE", exit_code=0),
                )
            ],
        )
        text = _render_artifact_set_text(result)
        assert "Bundle analysis: not run (budget overflow)" in text
        assert "None" not in text
