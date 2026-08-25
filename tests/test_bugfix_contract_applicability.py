# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Applicability classification for the bug-fix test contract."""

from __future__ import annotations

import pytest

from scripts import check_bugfix_test_contract as gate


class TestApplicability:
    @pytest.mark.parametrize(
        "subject",
        ["fix: thing", "fix(cli): thing", "fix!: thing", "perf: thing", "security: x"],
    )
    def test_fix_shaped_subjects_are_in_scope(self, subject: str) -> None:
        assert gate.is_bugfix([subject], None)

    @pytest.mark.parametrize(
        "subject", ["feat: thing", "docs: thing", "refactor: thing", "test: thing"]
    )
    def test_other_conventional_types_are_out_of_scope(self, subject: str) -> None:
        assert not gate.is_bugfix([subject], None)

    def test_the_pr_title_alone_can_bring_it_into_scope(self) -> None:
        assert gate.is_bugfix(["chore: wip"], "fix: the real subject")

    def test_no_signal_anywhere_is_out_of_scope(self) -> None:
        assert not gate.is_bugfix(["chore: wip"], "Update docs")

    def test_non_fix_pr_title_is_authoritative_over_review_fixups(self) -> None:
        assert not gate.is_bugfix(
            ["feat: add architecture gate", "fix: address review feedback"],
            "ADR-061 Phase 1: migrate aggregation ownership",
        )

    def test_local_run_still_classifies_fixup_commit_without_pr_title(self) -> None:
        assert gate.is_bugfix(["fix: address review feedback"], None)
