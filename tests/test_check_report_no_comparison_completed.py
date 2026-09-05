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

"""``actions/check-target``'s handling of a release/bundle fan-out's
``no_comparison_completed`` outcome (ADR-065 D7).

Split out of ``tests/test_check_report.py`` rather than added there -- that
file sits at its own ``architecture/debt.yaml`` adoption baseline and has no
headroom left, mirroring this repo's own established split-for-size
precedent (e.g. ``tests/test_check_report_run_outcome_backfill.py``).
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.check_report import augment_report, final_exit_code


class TestAugmentReportNoComparisonCompleted:
    """A release/bundle fan-out's ``no_comparison_completed`` outcome
    (ADR-065 D7) keeps an ordinary legacy top-level ``verdict`` (typically
    ``"NO_CHANGE"``, from the vacuous bundle/matrix pass over disjoint
    library sets) so older readers still parse it. Before this fix,
    ``_classify_verdict`` read only that raw ``verdict`` string, so
    ``gate-mode: advisory``/``deferred`` classified it as an ordinary clean
    compatibility pass instead of an operational failure -- `final_exit_code`
    silently returned 0 for a release that completed zero comparisons.
    ``gate-mode: local`` was unaffected (it reads the real ``exit_code``,
    which already carries the contribution)."""

    @staticmethod
    def _no_comparison_report(
        *, release_global_verdict: str | None = None
    ) -> dict[str, object]:
        return {
            "verdict": "NO_CHANGE",
            "old_dir": "/old",
            "new_dir": "/new",
            "libraries": [],
            "no_comparison_completed": True,
            "run_outcome": {
                "schema_version": "1.1",
                "compatibility": release_global_verdict,
                "assurance": None,
                "gate": "none",
                "operational": "no_comparison_completed",
                "lifecycle": "existing",
                "scope": "complete",
            },
            "exit": {
                "code": 1,
                "reasons": ["no_comparison_completed"],
                "compatibility_contribution": 0,
                "contract_coverage_contribution": 0,
                "analysis_assurance_contribution": 0,
                "crosscheck_promotion_contribution": 0,
                "operational_error_contribution": 0,
                "evidence_contract_error_contribution": 0,
                "budget_overflow_contribution": 0,
                "not_comparable_contribution": 0,
                "removed_required_library_contribution": 0,
                "incomplete_scope_contribution": 0,
                "no_comparison_completed_contribution": 1,
            },
        }

    @pytest.mark.parametrize("gate_mode", ["local", "deferred", "advisory"])
    def test_classified_as_an_operational_error_under_every_gate_mode(self, gate_mode):
        out = augment_report(
            self._no_comparison_report(),
            name="libfoo",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode=gate_mode,
        )
        assert out["operational_errors"] == [
            {
                "kind": "no_comparison_completed",
                "message": "no comparison completed: zero library-name "
                "pairs matched between OLD and NEW",
            }
        ]
        assert (
            final_exit_code(
                gate_mode,
                real_exit_code=0,
                operational_error=bool(out["operational_errors"]),
            )
            == 1
        )

    def test_compatibility_verdict_stays_unset_not_the_vacuous_raw_verdict(self):
        """``run_outcome.compatibility`` is correctly ``None`` here (no
        release-global pass observed a real result) -- ``compatibility_
        verdict`` must not be backfilled from the vacuous raw ``"NO_CHANGE"``
        verdict, which would make this envelope simultaneously claim no
        comparison completed and a clean compatibility result."""
        out = augment_report(
            self._no_comparison_report(),
            name="libfoo",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert "compatibility_verdict" not in out

    def test_compatibility_verdict_preserves_a_real_mixed_axis_result(self):
        """A release-global bundle/probe-matrix break can dominate the exit
        code while zero per-library pairs matched -- `run_outcome.
        compatibility` carries that real result, and it must not be lost
        just because the independent no_comparison_completed axis also
        fired."""
        out = augment_report(
            self._no_comparison_report(release_global_verdict="BREAKING"),
            name="libfoo",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["compatibility_verdict"] == "BREAKING"
        assert out["operational_errors"] == [
            {
                "kind": "no_comparison_completed",
                "message": "no comparison completed: zero library-name "
                "pairs matched between OLD and NEW",
            }
        ]

    def test_advisory_preserves_the_contribution_others_are_zeroed(self):
        """`_neutralize_gate`'s advisory rewrite must carry
        `no_comparison_completed_contribution` through unchanged -- unlike
        `compatibility_contribution`/`incomplete_scope_contribution`, this
        axis is policy-independent (always 1, never a deferrable finding)."""
        out = augment_report(
            self._no_comparison_report(),
            name="libfoo",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["exit"]["no_comparison_completed_contribution"] == 1
        assert out["exit"]["compatibility_contribution"] == 0
        assert out["exit"]["incomplete_scope_contribution"] == 0

    def test_a_real_compatibility_verdict_is_not_reclassified(self):
        """Negative control: an ordinary completed release (no
        no_comparison_completed operational status) is unaffected."""
        out = augment_report(
            {
                "verdict": "BREAKING",
                "old_dir": "/old",
                "new_dir": "/new",
                "libraries": [{"library": "libfoo.so", "verdict": "BREAKING"}],
                "run_outcome": {
                    "schema_version": "1.1",
                    "compatibility": "BREAKING",
                    "assurance": None,
                    "gate": "abi_breaking",
                    "operational": "none",
                    "lifecycle": "existing",
                    "scope": "complete",
                },
            },
            name="libfoo",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["compatibility_verdict"] == "BREAKING"
        assert out["operational_errors"] == []
