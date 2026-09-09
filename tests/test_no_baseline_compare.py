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

"""ADR-068 D2 / plan §5 P1, §6 Phase 2e: the ``declared_absent`` acquisition
state and ``abicheck compare --no-baseline NEW``.

Covers the model primitive directly (never incomplete, never a proven
removal input), the workflow (self-diff identity, always an empty change
set), the report shape (no verdict, no compatibility contribution), and the
CLI's arity enforcement -- complementing (not duplicating)
``tests/parity/test_no_baseline_parity.py``'s end-to-end scan/compare
parity assertions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.model import AbiSnapshot, Function
from abicheck.model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    MemberAcquisition,
    ScopeAcquisitionRecord,
    SideInventory,
)


def _declared_absent_record(member: str = "libfoo.so") -> ScopeAcquisitionRecord:
    return ScopeAcquisitionRecord(
        members=(
            MemberAcquisition(
                member=member,
                state=AcquisitionState.DECLARED_ABSENT,
                old_present=False,
                new_present=True,
                reason="--no-baseline",
            ),
        ),
        old_inventory=SideInventory(
            completeness=InventoryCompleteness.UNPROVEN, provenance="--no-baseline"
        ),
        new_inventory=SideInventory(
            completeness=InventoryCompleteness.UNPROVEN, provenance="candidate build"
        ),
        selection="no_baseline",
    )


class TestDeclaredAbsentAcquisitionState:
    """P1: the model primitive's completeness/outcome consequences."""

    def test_never_incomplete(self) -> None:
        record = _declared_absent_record()
        assert not record.is_incomplete
        assert record.unchecked_members == ()

    def test_never_no_comparison_completed(self) -> None:
        """D7 must not fire for a run that never intended a two-sided
        comparison in the first place -- it completed the (different-shaped)
        outcome it declared it would."""
        record = _declared_absent_record()
        assert not record.no_comparison_completed
        assert record.completed_members == record.members

    def test_never_a_proven_removal_or_addition_input(self) -> None:
        """Only ``not_supplied`` members feed the proven-removed/-added
        properties -- a declared-absent OLD side must never manufacture a
        removal finding, however complete NEW's inventory is."""
        record = _declared_absent_record()
        assert record.proven_removed_members == ()
        assert record.proven_added_members == ()

    def test_round_trips_through_dict(self) -> None:
        record = _declared_absent_record()
        rebuilt = ScopeAcquisitionRecord.from_dict(record.to_dict())
        assert rebuilt.members[0].state is AcquisitionState.DECLARED_ABSENT
        assert not rebuilt.is_incomplete

    def test_distinct_from_not_supplied(self) -> None:
        """Never conflated with NOT_SUPPLIED: that state IS counted as
        unchecked/incomplete, DECLARED_ABSENT never is."""
        not_supplied = ScopeAcquisitionRecord(
            members=(
                MemberAcquisition(
                    member="libfoo.so",
                    state=AcquisitionState.NOT_SUPPLIED,
                    old_present=True,
                    new_present=False,
                ),
            ),
            old_inventory=SideInventory(
                completeness=InventoryCompleteness.UNPROVEN, provenance="dir"
            ),
            new_inventory=SideInventory(
                completeness=InventoryCompleteness.UNPROVEN, provenance="dir"
            ),
            selection="all_expected",
        )
        assert not_supplied.is_incomplete
        assert _declared_absent_record().is_incomplete is False


class TestRunNoBaselineCompare:
    """Phase 2e: the workflow that turns a resolved NEW snapshot into a
    candidate-only audit."""

    def _snapshot(self) -> AbiSnapshot:
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[Function(name="foo", mangled="foo", return_type="void")],
        )

    def test_empty_change_set_by_construction(self) -> None:
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        result = run_no_baseline_compare(self._snapshot())
        assert result.diff.changes == []

    def test_acquisition_record_marks_old_declared_absent(self) -> None:
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        result = run_no_baseline_compare(self._snapshot())
        member = result.acquisition.members[0]
        assert member.state is AcquisitionState.DECLARED_ABSENT
        assert member.old_present is False
        assert member.new_present is True


class TestNoBaselineReport:
    """The report shape: no verdict, no compatibility contribution."""

    def _result(self):
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        snapshot = AbiSnapshot(library="libfoo.so", version="1.0")
        return run_no_baseline_compare(snapshot)

    def test_json_report_has_no_verdict_or_additions(self) -> None:
        from abicheck.report.no_baseline import no_baseline_json_report

        report = no_baseline_json_report(self._result())
        assert report["verdict"] is None
        assert report["changes"] == []
        assert report["run_outcome"]["compatibility"] is None
        assert report["run_outcome"]["gate"] == "none"
        assert report["run_outcome"]["operational"] == "none"
        assert report["run_outcome"]["scope"] == "complete"
        assert report["comparison_scope"]["members"][0]["state"] == "declared_absent"

    def test_exit_code_contributes_zero_by_default(self) -> None:
        from abicheck.report.no_baseline import no_baseline_exit_code

        assert no_baseline_exit_code(self._result()) == 0

    def test_markdown_report_names_declared_absent(self) -> None:
        from abicheck.report.no_baseline import no_baseline_markdown_report

        text = no_baseline_markdown_report(self._result())
        assert "declared_absent" in text
        assert "no baseline" in text.lower()


class TestNoBaselineCli:
    """CLI-layer arity enforcement (ADR-068 D2) -- see
    ``tests/parity/test_no_baseline_parity.py`` for the full end-to-end
    coverage; these are the fast, direct-import cases."""

    def test_single_artifact_audit_exits_zero(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        path = tmp_path / "libfoo.so.abi.json"
        path.write_text(snapshot_to_json(snap), encoding="utf-8")

        result = CliRunner().invoke(
            main, ["compare", "--no-baseline", str(path), "--format", "json"]
        )
        assert result.exit_code == 0, result.output

    @pytest.mark.parametrize(
        "extra_args",
        [
            pytest.param((), id="no_flag_one_operand"),
        ],
    )
    def test_missing_new_operand_is_usage_error(
        self, tmp_path: Path, extra_args: tuple[str, ...]
    ) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        path = tmp_path / "libfoo.so.abi.json"
        path.write_text(snapshot_to_json(snap), encoding="utf-8")

        result = CliRunner().invoke(main, ["compare", str(path), *extra_args])
        assert result.exit_code == 64


class TestNoBaselineRejectsViewTokens:
    """Codex review, fresh evidence ("Reject unsupported views for
    no-baseline audits"): a `--no-baseline` audit reports an empty change
    set by construction -- no root-cause graph, no findings list, no
    symbol table, no pattern-modulation ledger -- so every `--view` token
    used to silently do nothing (`_run_no_baseline_compare_cmd` never read
    the resolved report_mode/show_only/demangle/explain_patterns values at
    all) instead of being rejected the way `_dispatch_release_compare`
    already rejects its own unsupported view modes."""

    @staticmethod
    def _snapshot_path(tmp_path: Path) -> Path:
        from abicheck.serialization import snapshot_to_json

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        path = tmp_path / "libfoo.so.abi.json"
        path.write_text(snapshot_to_json(snap), encoding="utf-8")
        return path

    @pytest.mark.parametrize(
        "view_token",
        [
            "leaf", "root-cause", "impact", "show=breaking", "demangle", "patterns",
            # Codex review, PR #1180, fresh evidence: these two were added
            # to --view in the same PR and missed this rejection entirely --
            # a no-baseline audit has no scope/disposition ledger and no
            # suppression audit either.
            "filtered", "suppressions",
        ],
    )
    def test_any_view_token_is_rejected(
        self, tmp_path: Path, view_token: str
    ) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        path = self._snapshot_path(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", "--no-baseline", str(path), "--view", view_token],
        )
        assert result.exit_code == 64, result.output
        assert "--view is not available together with --no-baseline" in (
            result.output
        )

    def test_no_view_flag_still_succeeds(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        path = self._snapshot_path(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", "--no-baseline", str(path), "--format", "json"]
        )
        assert result.exit_code == 0, result.output
