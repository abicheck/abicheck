# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Coverage-gap tests for ``scan --artifact-set`` (CLI cleanup phase two,
PR 5), split out of ``tests/test_scan_artifact_set.py`` rather than added
there: that module is a ``no_growth``-debt-tracked file
(``architecture/debt.yaml``), so new coverage for the repeatable-option
refactor's own branches -- the directory-discovery return, the
member-not-found rejection, and the ``--dry-run`` preview/``--output``
rejection -- lives here instead of raising that file's line-count baseline.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main


def _write_elf_shared_object_stub(path: Path) -> None:
    """Minimal, structurally-valid ELF64 shared object (ET_DYN, no program
    headers) -- see ``tests/test_scan_artifact_set.py``'s identical helper
    for why this beats a bare 4-byte magic sniff."""
    data = bytearray(64)
    data[0:4] = b"\x7fELF"
    data[4] = 2  # ELFCLASS64
    data[5] = 1  # little-endian
    struct.pack_into("<H", data, 16, 3)  # e_type = ET_DYN
    struct.pack_into("<Q", data, 32, 0)  # e_phoff = 0
    struct.pack_into("<H", data, 56, 0)  # e_phnum = 0
    path.write_bytes(bytes(data))


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestArtifactSetRepeatableOptionBranches:
    def test_dry_run_with_artifact_set_previews_without_scanning(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # CLI cleanup phase two, PR 5: --dry-run used to be hard-rejected
        # with --artifact-set; it is now a real preview (member list, shared
        # inputs, and a per-member-summed cost projection) that never calls
        # run_scan_set() at all.
        import abicheck.service_scan as service_scan_mod

        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)

        def _fail_if_called(req):
            raise AssertionError("--dry-run must not run the real scan")

        monkeypatch.setattr(service_scan_mod, "run_scan_set", _fail_if_called)

        result = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "liba.so" in result.output
        assert "libb.so" in result.output
        assert "members (2)" in result.output
        assert "projected total:" in result.output
        assert "Dry run only" in result.output

    def test_dry_run_reflects_manifest_ownership_check(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # PR H follow-up (Codex review, fresh evidence): --dry-run must
        # name the manifest ownership check when --manifest is given (and
        # say plainly when it isn't) -- the preview was previously
        # indistinguishable either way, hiding both the requested contract
        # and the always-on duplicate-provider check from the operator.
        import abicheck.service_scan as service_scan_mod

        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        monkeypatch.setattr(
            service_scan_mod,
            "run_scan_set",
            lambda req: (_ for _ in ()).throw(
                AssertionError("--dry-run must not run the real scan")
            ),
        )

        without_manifest = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--dry-run",
            ],
        )
        assert without_manifest.exit_code == 0, without_manifest.output
        assert "duplicate-provider ownership ambiguity" in without_manifest.output
        assert "--manifest: not given" in without_manifest.output

        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(
            "provides:\n  - symbol: shared_util\n    library: liba.so\n"
        )
        with_manifest = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--manifest", str(manifest_path), "--dry-run",
            ],
        )
        assert with_manifest.exit_code == 0, with_manifest.output
        assert "1 expected-provider entry will be checked" in with_manifest.output

    def test_dry_run_fails_the_same_way_as_a_real_run_on_bad_risk_rules(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # Codex review: the per-member estimate must resolve --risk-rules
        # itself (service_scan._resolve_member_scan_level, shared with the
        # real run), not silently fall back to RiskRules.default() -- a
        # malformed profile must fail the dry-run exactly like it fails the
        # real run (click.UsageError, not a "successful" preview of a run
        # that would actually error out).
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        bad_rules = tmp_path / "bad.yml"
        bad_rules.write_text("risk_rules: [1, 2\n  - broken")

        dry_run_result = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--dry-run", "--risk-rules", str(bad_rules),
            ],
        )
        real_run_result = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--risk-rules", str(bad_rules),
            ],
        )
        assert dry_run_result.exit_code == real_run_result.exit_code == 64
        assert "cannot read --risk-rules" in dry_run_result.output
        assert "cannot read --risk-rules" in real_run_result.output

    def test_dry_run_preserves_estimator_notes(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # Codex review: estimate_scan()'s per-estimate `note` (e.g. the
        # --build-target workspace-wide-TU-count caveat) must survive into
        # the aggregated preview, not be dropped in favor of bare totals.
        # --sources is required here so this pinned --depth build request
        # isn't itself the EVIDENCE_CONTRACT_ERROR case under test elsewhere
        # (--build-target alone supplies no source/build evidence).
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.c").write_text("int f(void) { return 0; }\n")
        result = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--dry-run", "--depth", "build", "--build-target", "//pkg:lib",
                "--sources", str(src),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "UNSCOPED" in result.output

    def test_dry_run_warns_on_pinned_depth_with_no_evidence(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # Codex review: --depth source with no --sources/--build-info/
        # --build-config would fail the real run with EVIDENCE_CONTRACT_ERROR
        # (exit 1); the dry-run must flag it (as a DryRunResult.block(), not
        # a plain note -- so its own exit code matches the real run's) rather
        # than silently price a run that would never actually execute.
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        result = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--dry-run", "--depth", "source",
            ],
        )
        assert result.exit_code == 1, result.output
        assert "EVIDENCE_CONTRACT_ERROR" in result.output

    def test_dry_run_blocks_on_an_inert_build_config_with_no_query(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # Codex review: a bare --config with no build.query gives the real
        # run nothing to collect either (scan_engine._check_scan_evidence_
        # contract's gave_source_input never counts build_config itself) --
        # treating its mere presence as evidence was optimistic and let the
        # preview report exit 0 for a run that would fail.
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        config = tmp_path / "abicheck.yml"
        config.write_text("build:\n  compile_db: build/compile_commands.json\n")
        result = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--dry-run", "--depth", "source", "--config", str(config),
            ],
        )
        assert result.exit_code == 1, result.output
        assert "EVIDENCE_CONTRACT_ERROR" in result.output

    def test_dry_run_does_not_block_a_build_config_that_declares_a_query(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        config = tmp_path / "abicheck.yml"
        config.write_text("build:\n  query: cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON .\n")
        result = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--dry-run", "--depth", "source", "--config", str(config),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "EVIDENCE_CONTRACT_ERROR" not in result.output

    def test_dry_run_does_not_block_a_shallow_pinned_depth(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # Codex review: a shallow pinned depth like --depth binary resolves
        # collect_mode == "off", which scan_engine._check_scan_evidence_
        # contract itself short-circuits on -- the real run never raises
        # EVIDENCE_CONTRACT_ERROR for it, so the dry-run must not block it
        # either, even with no --sources/--build-info/--build-config given.
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        result = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--dry-run", "--depth", "binary",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "EVIDENCE_CONTRACT_ERROR" not in result.output

    def test_dry_run_rejects_ambiguous_duplicate_soname(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex review: run_scan_set() rejects an ambiguous duplicate-
        # DT_SONAME set up front (exit 64); the dry-run must fail the same
        # way, not report a successful preview of a request that was always
        # going to be rejected.
        import abicheck.bundle as bundle_mod
        from abicheck.bundle import ArtifactSetError

        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)

        def _fake_check(libraries):
            raise ArtifactSetError(
                "--artifact-set has ambiguous duplicate SONAME provider(s): ..."
            )

        monkeypatch.setattr(
            bundle_mod, "check_artifact_set_soname_collisions", _fake_check
        )
        result = runner.invoke(
            main,
            ["scan", "--artifact-set", str(p1), "--artifact-set", str(p2), "--dry-run"],
        )
        assert result.exit_code != 0
        assert "ambiguous duplicate SONAME" in result.output

    def test_dry_run_does_not_warn_when_sources_are_given(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.c").write_text("int f(void) { return 0; }\n")
        result = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--dry-run", "--depth", "source", "--sources", str(src),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "EVIDENCE_CONTRACT_ERROR" not in result.output

    def test_dry_run_prices_the_cross_library_bundle_audit_pass(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # Codex review: the projected total previously excluded run_scan_set's
        # own cross-library bundle-audit pass entirely, understating a
        # --budget plan for a large set. It must now appear as its own line
        # and be folded into the total.
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        result = runner.invoke(
            main,
            ["scan", "--artifact-set", str(p1), "--artifact-set", str(p2), "--dry-run"],
        )
        assert result.exit_code == 0, result.output
        assert "bundle_audit:" in result.output

    def test_rejects_dry_run_with_artifact_set_and_output(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # --dry-run + --output is rejected the same way it is for every
        # other scan/compare/dump variant (reject_dry_run_with_output) --
        # a dry-run report has nowhere useful to be written to disk.
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        result = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--dry-run", "--output", str(tmp_path / "out.json"),
            ],
        )
        assert result.exit_code != 0

    def test_rejects_explicit_member_that_does_not_exist(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        p1 = tmp_path / "liba.so"
        _write_elf_shared_object_stub(p1)
        missing = tmp_path / "missing.so"
        result = runner.invoke(
            main, ["scan", "--artifact-set", str(p1), "--artifact-set", str(missing)]
        )
        assert result.exit_code != 0
        assert f"--artifact-set member not found: {missing}" in result.output

    def test_directory_form_resolves_via_discover_shared_libraries(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A single value that is a directory (len(spec) == 1) takes the
        # directory-discovery branch, not the explicit-path-list branch --
        # mocked here (rather than requiring real ELF fixtures under
        # @pytest.mark.integration) to exercise it in the fast unit lane too.
        import abicheck.service_scan as service_scan_mod
        from abicheck.service_scan import ScanSetResult
        from abicheck.workflows import extraction as extraction_mod

        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)

        monkeypatch.setattr(
            extraction_mod, "discover_shared_libraries", lambda d: [p1, p2]
        )
        captured: dict[str, object] = {}

        def _fake_run_scan_set(req):
            captured["req"] = req
            return ScanSetResult(verdict="COMPATIBLE", exit_code=0)

        monkeypatch.setattr(service_scan_mod, "run_scan_set", _fake_run_scan_set)

        result = runner.invoke(main, ["scan", "--artifact-set", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert sorted(captured["req"].binaries) == sorted([p1, p2])
