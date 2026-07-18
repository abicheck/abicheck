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

"""Shared ``--dry-run`` contract behavior tests (ADR-043 D4).

``dump``, ``compare``, ``scan``, ``deps tree``, and ``deps compare`` all share
one ``DryRunResult`` model/renderer (``abicheck/dry_run.py``). This module
pins the cross-command contract behaviorally: deterministic output, no file
written, ``-o/--output`` rejected, and an exit code drawn only from
``{0, 1, 64}`` — never a verdict code (``2``/``4``). ``scan --dry-run`` has
its own dedicated coverage in ``test_cli_scan.py``/``test_scan_estimate.py``;
this file focuses on the three commands (``dump``, ``compare``, ``deps
tree``/``deps compare``) that previously had none.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

_CONTRACT_FOOTER = "Dry run only -- no analysis performed, nothing written."


def _write_snapshot(path: Path, version: str = "1.0") -> None:
    snap = AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=[
            Function(
                name="f", mangled="_Zf", return_type="void",
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    path.write_text(snapshot_to_json(snap), encoding="utf-8")


class TestDumpDryRun:
    def test_writes_nothing_and_exits_zero(self, tmp_path: Path) -> None:
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        out = tmp_path / "would-not-be-written.json"
        result = CliRunner().invoke(
            main, ["dump", str(snap), "--dry-run", "-o", str(out)]
        )
        # --dry-run + -o is a usage error (mutually exclusive), not a silent
        # no-write success — confirms the rejection wires up on `dump` too.
        assert result.exit_code == 64
        assert not out.exists()

    def test_deterministic_and_reports_contract(self, tmp_path: Path) -> None:
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        runner = CliRunner()
        first = runner.invoke(main, ["dump", str(snap), "--dry-run"])
        second = runner.invoke(main, ["dump", str(snap), "--dry-run"])
        assert first.exit_code == 0
        assert first.output == second.output
        assert _CONTRACT_FOOTER in first.output
        assert "Command: dump" in first.output

    def test_depth_source_with_no_evidence_input_blocks(self, tmp_path: Path) -> None:
        # Codex review: the real (non-dry) run's check_requested_depth_satisfied
        # strict gate now hard-fails `--depth source`/`--depth build` with no
        # way to reach that depth, but a --dry-run used to exit 0 for the
        # identical inputs (only a soft "would carry only L0-L2 data"
        # warning) -- silently accepting a baseline invocation that the real
        # run would then reject. --depth source has no path but --sources/
        # --build-info (a -p/--compile-db only ever supplies "build"
        # context), so this is cheaply, deterministically known to fail
        # without running anything.
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        result = CliRunner().invoke(
            main, ["dump", str(snap), "--dry-run", "--depth", "source"]
        )
        assert result.exit_code == 1, result.output
        assert "Exit code: 1" in result.output

    def test_depth_build_with_no_evidence_input_blocks(self, tmp_path: Path) -> None:
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        result = CliRunner().invoke(
            main, ["dump", str(snap), "--dry-run", "--depth", "build"]
        )
        assert result.exit_code == 1, result.output
        assert "Exit code: 1" in result.output

    def test_depth_build_with_compile_db_does_not_block(self, tmp_path: Path) -> None:
        # A -p/--compile-db might satisfy --depth build -- whether it
        # actually matches these headers is real work (load + header-
        # inclusion scan) a dry run must not perform, so this stays the
        # softer "would carry only L0-L2 data" warning rather than a
        # blocker; the real run resolves it for real.
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        header = tmp_path / "api.h"
        header.write_text("void f(void);\n", encoding="utf-8")
        db = tmp_path / "compile_commands.json"
        db.write_text("[]", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "dump", str(snap), "--dry-run", "--depth", "build",
                "-H", str(header), "-p", str(db),
            ],
        )
        assert result.exit_code == 0, result.output

    def test_depth_source_with_build_info_but_no_sources_blocks(
        self, tmp_path: Path
    ) -> None:
        # Codex review: a raw --build-info compile database supplies L3
        # "build" context only -- L4 source-ABI replay only ever runs over
        # a --sources tree (buildsource.inline._run_inline_source_abi
        # returns (None, []) whenever `sources` is None). The blocker below
        # used to be nested under a "sources AND build_info both absent"
        # warn condition, so --depth source with --build-info given (but no
        # --sources) fell through untouched and the dry run exited 0 even
        # though the real dump's strict depth gate would raise.
        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        header = tmp_path / "api.h"
        header.write_text("void f(void);\n", encoding="utf-8")
        db = tmp_path / "compile_commands.json"
        db.write_text("[]", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            [
                "dump", str(snap), "--dry-run", "--depth", "source",
                "-H", str(header), "--build-info", str(db),
            ],
        )
        assert result.exit_code == 1, result.output
        assert "Exit code: 1" in result.output
        assert "no --sources was given" in result.output

    def test_depth_source_with_prebuilt_pack_build_info_does_not_block(
        self, tmp_path: Path
    ) -> None:
        # Codex review, second finding: a raw compile-DB --build-info never
        # carries L4 facts, but a *pack-shaped* --build-info (e.g. from a
        # previous `collect` or the abicheck-cc wrapper) can carry its own
        # source_abi -- cli_buildsource.embed_build_source's _combine_packs
        # falls back to that pack's source_abi when no --sources pack is
        # given, so --depth source --build-info <pack> (no --sources) can
        # genuinely succeed for real. The blocker above must not fire for
        # this case -- unlike a raw compile database, checked via
        # buildsource.inline.is_pack_dir (cheap manifest-shape read).
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.buildsource.source_abi import SourceAbiSurface

        snap = tmp_path / "lib.abi.json"
        _write_snapshot(snap)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        surface = SourceAbiSurface()
        surface.coverage["compile_units_selected"] = 1
        surface.coverage["compile_units_parsed"] = 1
        BuildSourcePack(root=pack_dir, source_abi=surface).write()
        result = CliRunner().invoke(
            main,
            [
                "dump", str(snap), "--dry-run", "--depth", "source",
                "--build-info", str(pack_dir),
            ],
        )
        assert result.exit_code == 0, result.output


class TestCompareDryRun:
    def test_rejects_output_flag(self, tmp_path: Path) -> None:
        old = tmp_path / "old.abi.json"
        new = tmp_path / "new.abi.json"
        _write_snapshot(old, "1.0")
        _write_snapshot(new, "2.0")
        out = tmp_path / "would-not-be-written.json"
        result = CliRunner().invoke(
            main,
            ["compare", str(old), str(new), "--dry-run", "-o", str(out)],
        )
        assert result.exit_code == 64
        assert not out.exists()

    def test_deterministic_never_a_verdict_exit_code(self, tmp_path: Path) -> None:
        old = tmp_path / "old.abi.json"
        new = tmp_path / "new.abi.json"
        _write_snapshot(old, "1.0")
        _write_snapshot(new, "2.0")
        runner = CliRunner()
        first = runner.invoke(main, ["compare", str(old), str(new), "--dry-run"])
        second = runner.invoke(main, ["compare", str(old), str(new), "--dry-run"])
        assert first.exit_code in (0, 1, 64)
        assert first.output == second.output
        assert _CONTRACT_FOOTER in first.output
        assert "Command: compare" in first.output

    def test_reports_effective_depth_not_just_raw_requested(self, tmp_path: Path) -> None:
        # Regression (CLI-audit P1/P2): a dry run must report the *effective*
        # depth the real run will use, not just echo back the raw --depth
        # string. With no --depth given but a raw --sources tree, the real
        # run now infers "source" (see TestResolveCompareCollectMode) --
        # the dry run must show that inference, not "requested depth: (not
        # given)" alone with no indication of what will actually happen.
        old = tmp_path / "old.abi.json"
        new = tmp_path / "new.abi.json"
        _write_snapshot(old, "1.0")
        _write_snapshot(new, "2.0")
        tree = tmp_path / "src"
        tree.mkdir()
        result = CliRunner().invoke(
            main,
            [
                "compare", str(old), str(new), "--dry-run",
                "--sources", "old=" + str(tree),
            ],
        )
        assert result.exit_code == 0
        assert "requested depth: (not given)" in result.output
        assert "effective depth: source" in result.output
        assert "inferred" in result.output


class TestDepsTreeDryRun:
    def test_writes_nothing_and_rejects_output(self, tmp_path: Path) -> None:
        binary = tmp_path / "libfoo.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 60)
        out = tmp_path / "would-not-be-written.json"
        result = CliRunner().invoke(
            main, ["deps", "tree", str(binary), "--dry-run", "-o", str(out)]
        )
        assert result.exit_code == 64
        assert not out.exists()

    def test_deterministic_and_reports_contract(self, tmp_path: Path) -> None:
        binary = tmp_path / "libfoo.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 60)
        runner = CliRunner()
        first = runner.invoke(main, ["deps", "tree", str(binary), "--dry-run"])
        second = runner.invoke(main, ["deps", "tree", str(binary), "--dry-run"])
        assert first.exit_code == 0
        assert first.output == second.output
        assert _CONTRACT_FOOTER in first.output
        assert "Command: deps tree" in first.output

    def test_non_elf_binary_rejected_even_under_dry_run(self, tmp_path: Path) -> None:
        # Regression: the ELF-format check used to run *after* the dry-run
        # emit, so `deps tree --dry-run` on a non-ELF file reported "ok" for
        # an input the real run immediately rejects (post-merge PR #566
        # review). The dry run must agree with the real run.
        not_elf = tmp_path / "not-a-lib.so"
        not_elf.write_bytes(b"not an elf at all")
        result = CliRunner().invoke(main, ["deps", "tree", str(not_elf), "--dry-run"])
        assert result.exit_code != 0
        assert "requires an ELF binary" in result.output
        assert _CONTRACT_FOOTER not in result.output


class TestDepsCompareDryRun:
    def test_writes_nothing_and_rejects_output(self, tmp_path: Path) -> None:
        old_root = tmp_path / "old-root"
        new_root = tmp_path / "new-root"
        old_root.mkdir()
        new_root.mkdir()
        out = tmp_path / "would-not-be-written.json"
        result = CliRunner().invoke(
            main,
            [
                "deps", "compare", "usr/bin/myapp",
                "--old-root", str(old_root), "--new-root", str(new_root),
                "--dry-run", "-o", str(out),
            ],
        )
        assert result.exit_code == 64
        assert not out.exists()

    def test_deterministic_and_reports_contract(self, tmp_path: Path) -> None:
        old_root = tmp_path / "old-root"
        new_root = tmp_path / "new-root"
        old_root.mkdir()
        new_root.mkdir()
        args = [
            "deps", "compare", "usr/bin/myapp",
            "--old-root", str(old_root), "--new-root", str(new_root), "--dry-run",
        ]
        runner = CliRunner()
        first = runner.invoke(main, args)
        second = runner.invoke(main, args)
        assert first.exit_code == 0
        assert first.output == second.output
        assert _CONTRACT_FOOTER in first.output
        assert "Command: deps compare" in first.output

    def test_same_root_is_a_usage_error_even_under_dry_run(self, tmp_path: Path) -> None:
        # The no-op-comparison guard fires before the dry-run branch — a dry
        # run still catches a plainly-useless invocation (exit 64, not a
        # silent "would compare nothing" report).
        root = tmp_path / "same-root"
        root.mkdir()
        result = CliRunner().invoke(
            main,
            [
                "deps", "compare", "usr/bin/myapp",
                "--old-root", str(root), "--new-root", str(root), "--dry-run",
            ],
        )
        assert result.exit_code == 64

    def test_non_elf_binary_rejected_even_under_dry_run(self, tmp_path: Path) -> None:
        # Regression: the per-root ELF-format check used to run *after* the
        # dry-run emit, so `deps compare --dry-run` could report "ok" for a
        # binary that isn't ELF in either root even though the real run
        # immediately rejects it (post-merge PR #566 review).
        old_root = tmp_path / "old-root"
        new_root = tmp_path / "new-root"
        old_root.mkdir()
        new_root.mkdir()
        rel = Path("usr/bin/myapp")
        (old_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (old_root / rel).write_bytes(b"not an elf at all")
        result = CliRunner().invoke(
            main,
            [
                "deps", "compare", str(rel),
                "--old-root", str(old_root), "--new-root", str(new_root),
                "--dry-run",
            ],
        )
        assert result.exit_code != 0
        assert "requires an ELF binary" in result.output
        assert _CONTRACT_FOOTER not in result.output

    def test_absolute_binary_resolved_under_sysroot_not_host(
        self, tmp_path: Path
    ) -> None:
        # Regression (CodeRabbit review): `root / binary` (pathlib) drops
        # `root` entirely when `binary` is absolute, so an absolute BINARY
        # argument used to silently escape old-root/new-root and resolve
        # against the host filesystem instead. The dry-run's displayed
        # resolved paths must stay under the sysroots.
        old_root = tmp_path / "old-root"
        new_root = tmp_path / "new-root"
        old_root.mkdir()
        new_root.mkdir()
        result = CliRunner().invoke(
            main,
            [
                "deps", "compare", "/usr/bin/myapp",
                "--old-root", str(old_root), "--new-root", str(new_root),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"old resolved path: {old_root / 'usr/bin/myapp'}" in result.output
        assert f"new resolved path: {new_root / 'usr/bin/myapp'}" in result.output
        # The raw argument is echoed under "Inputs" -- only the *resolved*
        # paths must stay confined to the sysroots.
        assert "resolved path: /usr/bin/myapp" not in result.output
