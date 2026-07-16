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
