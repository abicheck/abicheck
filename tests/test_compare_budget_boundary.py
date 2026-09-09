# SPDX-License-Identifier: Apache-2.0
"""``compare --budget``'s resolve/classify boundary checks (ADR-068 §3 #19).

Codex review, fresh evidence, PR #1178: a stored-snapshot-only comparison
needs no subprocess/extraction work at all, so nothing inside resolution or
classification ever called ``deadline.check()`` -- an already-expired budget
(``--budget 0s``) silently completed with exit 0 instead of reporting exit 5.
``compare a.abi.json a.abi.json --budget 0s`` was the exact reproduction.

Also covers the sibling finding on the same review: a budget-overflow abort
must render its structured refusal document to every ``--write`` target, not
just the primary ``--format``/``-o``, the same way a normal run does.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _make_snapshot(version: str = "1.0") -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version=version,
        functions=[
            Function(
                name="foo", mangled="_Z3foov", return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
    )


def _write_snapshots(tmp_path: Path) -> tuple[Path, Path]:
    old_p = tmp_path / "old.abi.json"
    new_p = tmp_path / "new.abi.json"
    old_p.write_text(snapshot_to_json(_make_snapshot("1.0")), encoding="utf-8")
    new_p.write_text(snapshot_to_json(_make_snapshot("2.0")), encoding="utf-8")
    return old_p, new_p


class TestBudgetBoundaryCheck:
    def test_zero_budget_aborts_a_stored_snapshot_only_comparison(
        self, tmp_path: Path
    ) -> None:
        # The exact Codex repro: neither side needs any subprocess/extraction
        # work, so without an explicit boundary check nothing would ever call
        # deadline.check() and the run would silently complete at exit 0.
        old_p, new_p = _write_snapshots(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["compare", str(old_p), str(new_p), "--budget", "0s"]
        )
        assert result.exit_code == 5, result.output

    def test_positive_budget_stored_snapshot_only_comparison_unaffected(
        self, tmp_path: Path
    ) -> None:
        # A real, unexpired budget must not be tripped by the same boundary
        # check -- it only fires once the deadline has actually passed.
        old_p, new_p = _write_snapshots(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["compare", str(old_p), str(new_p), "--budget", "5m"]
        )
        assert result.exit_code != 5, result.output

    def test_zero_budget_still_renders_a_json_abort_document(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = _write_snapshots(tmp_path)
        out_p = tmp_path / "out.json"
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compare", str(old_p), str(new_p), "--budget", "0s",
                "--format", "json", "-o", str(out_p),
            ],
        )
        assert result.exit_code == 5, result.output
        doc = json.loads(out_p.read_text(encoding="utf-8"))
        assert doc["run_outcome"]["operational"] == "budget_overflow"

    def test_zero_budget_renders_abort_to_every_write_target(
        self, tmp_path: Path
    ) -> None:
        # `--write` is repeatable and, on a normal run, every target renders
        # the same result -- an abort must do the same, not just the primary
        # `--format`/`-o` (Codex review, fresh evidence, PR #1178).
        old_p, new_p = _write_snapshots(tmp_path)
        primary_p = tmp_path / "primary.json"
        secondary_p = tmp_path / "secondary.junit.xml"
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compare", str(old_p), str(new_p), "--budget", "0s",
                "--format", "json", "-o", str(primary_p),
                "--write", f"junit={secondary_p}",
            ],
        )
        assert result.exit_code == 5, result.output
        assert primary_p.exists() and primary_p.stat().st_size > 0
        # Before the fix, the secondary target was never written at all.
        assert secondary_p.exists() and secondary_p.stat().st_size > 0
        assert b"<testsuite" in secondary_p.read_bytes()
