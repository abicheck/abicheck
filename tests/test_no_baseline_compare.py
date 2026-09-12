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


class TestRunNoBaselineCompareEnvMatrix:
    """Codex review finding 4: a ``--no-baseline`` audit of a candidate
    exceeding a declared ``deployment.runtime_floors`` value must produce
    the same finding/verdict a two-sided ``compare`` of the same candidate
    would -- the check (``check_platform_baseline_floor``) is candidate-
    only by construction (it reads only the candidate's own declared
    requirement against the floor, never an OLD side), so there is no
    architectural reason it can't run here too. Previously
    ``run_no_baseline_compare`` had no ``env_matrix`` parameter at all, so a
    real declared floor was silently ignored."""

    def _candidate_requiring(self, required: str) -> AbiSnapshot:
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol

        elf = ElfMetadata(
            soname="libfoo.so",
            needed=["libc.so.6"],
            versions_required={"libc.so.6": [f"GLIBC_{required}"]},
            symbols=[ElfSymbol(name="foo", visibility="default")],
        )
        return AbiSnapshot(library="libfoo.so", version="1.0", elf=elf)

    def test_env_matrix_omitted_produces_no_platform_baseline_finding(
        self,
    ) -> None:
        from abicheck.checker_policy import ChangeKind
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        result = run_no_baseline_compare(self._candidate_requiring("2.34"))
        kinds = {c.kind for c in result.findings}
        assert ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED not in kinds

    def test_env_matrix_declared_floor_produces_finding(self) -> None:
        """``check_platform_baseline_floor`` fires regardless of whether
        anything moved relative to an old snapshot (it reads only the
        candidate's own declared requirement against the floor). Its catalog
        default verdict is RISK, but it only ever fires on an actual floor
        *violation* (it returns nothing when the requirement is within the
        declared floor), so ``diff_versioning.
        promote_baseline_violation_findings`` unconditionally promotes any
        occurrence to BREAKING (Codex review, P1) -- matching what an
        ordinary two-sided ``compare`` of this candidate against an
        unrelated OLD reports for the identical declared floor."""
        from abicheck.checker_policy import ChangeKind, Verdict
        from abicheck.environment_matrix import EnvironmentMatrix
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        result = run_no_baseline_compare(
            self._candidate_requiring("2.34"), env_matrix=matrix
        )
        kinds = {c.kind for c in result.findings}
        assert ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED in kinds
        (finding,) = [
            c
            for c in result.findings
            if c.kind is ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED
        ]
        assert finding.candidate_side_enrichment is True
        assert finding.effective_verdict is Verdict.BREAKING
        assert result.diff.verdict is Verdict.BREAKING
        # The identity half stays absolutely empty -- ADR-068 D3's own
        # invariant, unaffected by this candidate-only finding.
        assert all(
            c.candidate_side_enrichment or c.cross_source_evolution is not None
            for c in result.diff.changes
        )

    def test_env_matrix_declared_floor_matches_two_sided_compare_verdict(
        self,
    ) -> None:
        """The same declared floor over the same candidate must reach the
        same verdict/finding a two-sided ``compare`` of that candidate
        against itself would -- proving the no-baseline path isn't a
        weaker check than the two-sided one that ``run_no_baseline_compare``
        itself wraps."""
        from abicheck.checker import compare
        from abicheck.checker_policy import ChangeKind
        from abicheck.environment_matrix import EnvironmentMatrix
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        candidate = self._candidate_requiring("2.34")
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})

        two_sided = compare(candidate, candidate, env_matrix=matrix)
        two_sided_kinds = {c.kind for c in two_sided.changes}
        assert ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED in two_sided_kinds
        assert two_sided.verdict is not None

        no_baseline = run_no_baseline_compare(candidate, env_matrix=matrix)
        no_baseline_kinds = {c.kind for c in no_baseline.findings}
        assert ChangeKind.PLATFORM_BASELINE_FLOOR_RAISED in no_baseline_kinds
        assert no_baseline.diff.verdict == two_sided.verdict

    def test_env_matrix_omitted_matches_two_sided_verdict_too(self) -> None:
        """Without a declared floor, both paths agree the candidate's own
        (unremarkable) requirement is a clean NO_CHANGE self-compare -- the
        control case for the two tests above."""
        from abicheck.checker import compare
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        candidate = self._candidate_requiring("2.34")
        two_sided = compare(candidate, candidate)
        no_baseline = run_no_baseline_compare(candidate)
        assert no_baseline.diff.verdict == two_sided.verdict

    def test_env_matrix_declared_floor_stamps_source_sha256(self) -> None:
        """Codex review (P2): a declared ``deployment.runtime_floors``
        matrix changes this run's findings/verdict, so a typed caller must
        be able to tell this audit apart from one with no deployment
        contract at all. ``run_no_baseline_compare`` deliberately never
        passes ``env_matrix=`` into its own ``_diff_pair`` call (see that
        function's docstring), so ``DiffResult.env_matrix_source_sha256``
        must be stamped separately, using the identical digest a two-sided
        ``compare(candidate, candidate, env_matrix=...)`` self-compare would
        produce for the same matrix -- not merely "some non-None value"."""
        from abicheck.checker import compare
        from abicheck.environment_matrix import EnvironmentMatrix
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        candidate = self._candidate_requiring("2.34")
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})

        two_sided = compare(candidate, candidate, env_matrix=matrix)
        assert two_sided.env_matrix_source_sha256 is not None

        no_baseline = run_no_baseline_compare(candidate, env_matrix=matrix)
        assert (
            no_baseline.diff.env_matrix_source_sha256
            == two_sided.env_matrix_source_sha256
        )

    def test_env_matrix_omitted_leaves_source_sha256_none(self) -> None:
        """Control case for the test above: with no declared floor at all,
        the digest stays ``None`` -- same as a two-sided ``compare`` given no
        ``env_matrix``."""
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        candidate = self._candidate_requiring("2.34")
        no_baseline = run_no_baseline_compare(candidate)
        assert no_baseline.diff.env_matrix_source_sha256 is None


class TestNoBaselineReportEnvMatrixDigest:
    """Codex review (P2): the digest ``run_no_baseline_compare`` stamps onto
    its ``DiffResult`` must actually reach the rendered report -- carrying
    it only on the in-process ``DiffResult`` left a persisted/rendered audit
    indistinguishable from one run with no deployment contract at all
    whenever the candidate stays within its declared floor (no finding, so
    no other evidence of the matrix in the output)."""

    def _candidate_requiring(self, required: str) -> AbiSnapshot:
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol

        elf = ElfMetadata(
            soname="libfoo.so",
            needed=["libc.so.6"],
            versions_required={"libc.so.6": [f"GLIBC_{required}"]},
            symbols=[ElfSymbol(name="foo", visibility="default")],
        )
        return AbiSnapshot(library="libfoo.so", version="1.0", elf=elf)

    def test_json_report_carries_the_digest(self) -> None:
        from abicheck.environment_matrix import EnvironmentMatrix
        from abicheck.report.no_baseline import (
            compute_no_baseline_document,
            no_baseline_json_report,
        )
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        candidate = self._candidate_requiring("2.34")
        result = run_no_baseline_compare(candidate, env_matrix=matrix)

        doc = compute_no_baseline_document(result)
        assert doc.env_matrix_source_sha256 == result.diff.env_matrix_source_sha256
        assert doc.env_matrix_source_sha256 is not None

        report = no_baseline_json_report(result)
        assert report["env_matrix_source_sha256"] == result.diff.env_matrix_source_sha256

    def test_json_report_digest_matches_two_sided_compare_report(self) -> None:
        """Same field name/value a two-sided ``compare`` JSON report would
        carry for the identical matrix -- proven directly against
        ``reporter.to_json``'s own output, not merely against the in-memory
        ``DiffResult`` field."""
        import json

        from abicheck.checker import compare
        from abicheck.environment_matrix import EnvironmentMatrix
        from abicheck.report.no_baseline import no_baseline_json_report
        from abicheck.reporter import to_json
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        candidate = self._candidate_requiring("2.34")

        two_sided = compare(candidate, candidate, env_matrix=matrix)
        two_sided_report = json.loads(to_json(two_sided))
        assert "env_matrix_source_sha256" in two_sided_report

        no_baseline = run_no_baseline_compare(candidate, env_matrix=matrix)
        no_baseline_report = no_baseline_json_report(no_baseline)
        assert (
            no_baseline_report["env_matrix_source_sha256"]
            == two_sided_report["env_matrix_source_sha256"]
        )

    def test_json_report_omits_the_key_when_no_matrix_was_declared(self) -> None:
        """Omitted, not ``null`` -- matching every other additive optional
        key in this report (e.g. ``contract_coverage_failures``)."""
        from abicheck.report.no_baseline import no_baseline_json_report
        from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

        candidate = self._candidate_requiring("2.34")
        result = run_no_baseline_compare(candidate)
        report = no_baseline_json_report(result)
        assert "env_matrix_source_sha256" not in report


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
            main, ["compare", "--no-baseline", str(path), "-o", "json=-"]
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
            main, ["compare", "--no-baseline", str(path), "-o", "json=-"]
        )
        assert result.exit_code == 0, result.output
