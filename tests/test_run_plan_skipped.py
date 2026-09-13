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

"""`project plan`'s two empty-plan outcomes (plan slice 7r).

Its own module rather than more of `test_run_plan.py` (which carries an
`architecture/debt.yaml` no-growth baseline): the claim here is one
question -- what happens when a plan resolves no checks -- answered over
the CLI, and it is exactly the question the retired `--allow-empty` could
not answer, which is why the flag existed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from test_run_plan import (  # the sibling module this slice split out of
    _SINGLE_PROFILE_LIBRARY_RAW,
    _write_build_output,
)

from abicheck.buildsource.run_plan import (
    SKIP_CHECKS_DECLARED_NONE_RESOLVED,
    SKIP_NO_CHECKS_DECLARED,
)
from abicheck.cli import main


def _write_config(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / ".abicheck.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


class TestEmptyPlanOutcomes:
    def test_no_checks_declared_is_an_explained_skipped_plan(
        self, tmp_path: Path
    ) -> None:
        """The bootstrap case: nothing declared, nothing to resolve. It
        succeeds on its own now -- and says why, in the artifact, rather
        than needing a human to assert it with a bypass flag."""
        config = _write_config(tmp_path, {"targets": {}})
        result = CliRunner().invoke(main, ["project", "plan", str(config)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["checks"] == []
        assert data["schema"] == "abicheck.run-plan/v3"
        assert data["skipped"]["reason"] == SKIP_NO_CHECKS_DECLARED
        assert data["skipped"]["declared_checks"] == 0
        # Bootstrap validation routes to `project validate`, which is where
        # the config's own well-formedness is actually answered.
        assert "project validate" in data["skipped"]["explanation"]
        assert "project validate" in result.output

    def test_declared_checks_resolving_to_nothing_still_exits_one(
        self, tmp_path: Path
    ) -> None:
        """The dangerous case keeps failing, and now has no bypass at all --
        this is the capability --allow-empty provided, deliberately removed
        rather than renamed."""
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        result = CliRunner().invoke(main, ["project", "plan", str(config)])
        assert result.exit_code == 1, result.output
        data = json.loads(result.stdout)
        assert data["checks"] == []
        assert data["skipped"]["reason"] == SKIP_CHECKS_DECLARED_NONE_RESOLVED
        assert data["skipped"]["declared_checks"] >= 1
        assert "--build-output" in result.output

    def test_retired_allow_empty_spelling_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        """No hidden alias: the old spelling is a usage error (64), on both
        the plan it used to rescue and one it never affected."""
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        empty = _write_config(tmp_path / "a", {"targets": {}})
        declared = _write_config(tmp_path / "b", _SINGLE_PROFILE_LIBRARY_RAW)
        for config in (empty, declared):
            result = CliRunner().invoke(
                main, ["project", "plan", str(config), "--allow-empty"]
            )
            assert result.exit_code == 64, result.output
            assert "--allow-empty" in result.output

    def test_a_resolved_plan_declares_no_skip_at_all(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, _SINGLE_PROFILE_LIBRARY_RAW)
        build_dir = _write_build_output(tmp_path, "linux", ["libfoo"])
        result = CliRunner().invoke(
            main,
            ["project", "plan", str(config), "--build-output", f"linux={build_dir}"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert len(data["checks"]) == 1
        assert "skipped" not in data
        # ... and keeps the unbumped schema, so the v3 stamp really is
        # scoped to the one new capability.
        assert data["schema"] == "abicheck.run-plan/v1"


class TestSkippedBlockReadPath:
    """`RunPlan.from_dict` enforces the same invariants `to_dict` refuses to
    write -- a hand-authored or corrupted artifact is not a way around them
    (Codex review, PR #1278)."""

    def test_skipped_beside_checks_is_rejected(self) -> None:
        from abicheck.buildsource.run_plan import RunPlan
        from abicheck.workflows.aggregate import AggregateError

        document = {
            "schema": "abicheck.run-plan/v3",
            "skipped": {
                "reason": SKIP_NO_CHECKS_DECLARED,
                "declared_checks": 0,
                "explanation": "x",
            },
            "checks": [{"check_id": "libfoo@linux", "required": True}],
        }
        # Accepting it would let `aggregate --manifest` project a perfectly
        # ordinary expected-target set (to_aggregate_manifest reads only
        # `checks`) out of a document that says the run never happened.
        with pytest.raises(AggregateError, match="skipped"):
            RunPlan.from_dict(document)

    def test_skipped_under_a_pre_v3_schema_is_rejected(self) -> None:
        from abicheck.buildsource.run_plan import RunPlan
        from abicheck.workflows.aggregate import AggregateError

        for schema in ("abicheck.run-plan/v1", "abicheck.run-plan/v2"):
            with pytest.raises(AggregateError, match="v3"):
                RunPlan.from_dict(
                    {
                        "schema": schema,
                        "skipped": {
                            "reason": SKIP_CHECKS_DECLARED_NONE_RESOLVED,
                            "declared_checks": 2,
                            "explanation": "x",
                        },
                        "checks": [],
                    }
                )

    def test_writing_the_same_contradiction_is_refused(self) -> None:
        from abicheck.buildsource.run_plan import RunPlan, RunPlanCheck, RunPlanSkip
        from abicheck.workflows.aggregate import AggregateError

        plan = RunPlan(
            checks=[RunPlanCheck(check_id="libfoo@linux")],
            skipped=RunPlanSkip(SKIP_NO_CHECKS_DECLARED, 0, "x"),
        )
        with pytest.raises(AggregateError, match="cannot also declare"):
            plan.to_dict()

    def test_registry_reports_the_highest_schema_this_build_emits(self) -> None:
        """A registry still saying v2 tells a consumer it need only support
        v2 against a build that can emit v3."""
        from abicheck import schemas

        assert schemas.current("run-plan") == "abicheck.run-plan/v3"


class TestSkipEvidenceAgreesWithItsReason:
    """`declared_checks` is the evidence the reason rests on, so the two
    cannot contradict each other (Codex review, PR #1278).

    The whole purpose of the v3 fields is that a consumer can tell a
    legitimate bootstrap skip from declared checks that failed to resolve;
    a block asserting both at once would make that distinction unsafe for
    every consumer downstream.
    """

    @pytest.mark.parametrize(
        ("reason", "declared"),
        [
            (SKIP_NO_CHECKS_DECLARED, 1),
            (SKIP_NO_CHECKS_DECLARED, 7),
            (SKIP_CHECKS_DECLARED_NONE_RESOLVED, 0),
        ],
    )
    def test_contradictory_pairs_are_rejected(self, reason: str, declared: int) -> None:
        from abicheck.buildsource.run_plan import RunPlanSkip
        from abicheck.workflows.aggregate import AggregateError

        with pytest.raises(AggregateError, match="self-contradictory"):
            RunPlanSkip(reason, declared, "x")

    @pytest.mark.parametrize(
        ("reason", "declared"),
        [(SKIP_NO_CHECKS_DECLARED, 0), (SKIP_CHECKS_DECLARED_NONE_RESOLVED, 1)],
    )
    def test_consistent_pairs_are_accepted(self, reason: str, declared: int) -> None:
        from abicheck.buildsource.run_plan import RunPlanSkip

        assert RunPlanSkip(reason, declared, "x").reason == reason

    @pytest.mark.parametrize("reason", ["", "bootstrap", "no_checks", "SKIPPED"])
    def test_unknown_reasons_are_rejected(self, reason: str) -> None:
        """An unrecognized reason is not an opaque label to carry through --
        a consumer would have to guess what it licenses."""
        from abicheck.buildsource.run_plan import RunPlan, RunPlanSkip
        from abicheck.workflows.aggregate import AggregateError

        with pytest.raises(AggregateError):
            RunPlanSkip(reason, 0, "x")
        with pytest.raises(AggregateError):
            RunPlan.from_dict(
                {
                    "schema": "abicheck.run-plan/v3",
                    "skipped": {
                        "reason": reason,
                        "declared_checks": 0,
                        "explanation": "x",
                    },
                    "checks": [],
                }
            )

    def test_the_read_path_enforces_it_too(self) -> None:
        from abicheck.buildsource.run_plan import RunPlan
        from abicheck.workflows.aggregate import AggregateError

        with pytest.raises(AggregateError, match="self-contradictory"):
            RunPlan.from_dict(
                {
                    "schema": "abicheck.run-plan/v3",
                    "skipped": {
                        "reason": SKIP_NO_CHECKS_DECLARED,
                        "declared_checks": 7,
                        "explanation": "x",
                    },
                    "checks": [],
                }
            )

    def test_every_reason_the_generator_emits_is_recognized(self) -> None:
        """Vacuity guard on the registry itself: a reason the classifier can
        produce but the validator does not know would make every generated
        plan unreadable, which no example-shaped test above would catch."""
        from abicheck.buildsource.run_plan_skip import _EXPECTED_DECLARED_CHECKS

        assert set(_EXPECTED_DECLARED_CHECKS) == {
            SKIP_NO_CHECKS_DECLARED,
            SKIP_CHECKS_DECLARED_NONE_RESOLVED,
        }
