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

"""A finding nobody introduced is never reported as this change's own.

**Bug class:** ``report.unestablished_result_reads_as_success`` — its mirror
image. That class is "a result nobody established reads as a pass"; this is
"a result somebody established as *pre-existing* reads as this pull
request's". Both are the renderer asserting something the comparison layer
did not.

**Reported on a real build.** A PVXS self-compare of one snapshot against
*itself* — byte-identical operands, verdict ``NO_CHANGE`` — reported 339
changes: 336 ``exported_not_public`` findings on C++
template-instantiation guard variables and function-local statics, plus 3
``public_not_exported``. Every one of them is a standing property of the
library, present identically on both sides. Under the default
``--on=changes`` the PR comment posted all 339 on every run of an unchanged
library.

**Where the answer already lived.** ADR-068 D3's
``workflows/cross_source_evolution.py`` re-runs each one-sided hygiene check
against the baseline and stamps every finding ``introduced`` / ``resolved``
/ ``persistent`` / ``not_evaluated``; the JSON report has carried the value
per finding all along. The comment renderer simply never read it. So this is
a *reader* fix, and the invariant below is stated over the stamped value
rather than over anything re-derived from severity or kind — the renderer
must not form a second opinion about what a comparison introduced
(ADR-072 D1).

**General invariant:** for any report, the compatibility buckets, the
headline, the "What changed" rollup and ``should_post`` account for exactly
the findings the comparison layer stamped as *not* pre-existing. Stated
below as an exhaustive enumeration over the four evolution states crossed
with every severity, rather than as the one 336-finding shape that exposed
it.
"""

from __future__ import annotations

import pytest

from abicheck.pr_comment import build_model, render_comment, should_post

#: ADR-068 D3's four states, plus the unstamped case every ordinary
#: two-sided diff finding is in.
EVOLUTIONS = ("introduced", "persistent", "resolved", "not_evaluated", "")

#: Which of them mean "this comparison's operands did not introduce it".
PRE_EXISTING = frozenset({"persistent", "resolved"})

#: The severities a hygiene finding can carry. Crossed with the states
#: above because the defect was in the *bucketing* path, and a severity
#: promotion (``severity-quality_issues: error``) is exactly the kind of
#: rule that could re-admit a pre-existing finding to the Breaking bucket.
SEVERITIES = ("breaking", "api_break", "risk", "compatible")


def hygiene_report(
    *,
    evolution: str,
    severity: str = "risk",
    count: int = 1,
    with_real_change: bool = False,
    levels: dict[str, str] | None = None,
) -> dict[str, object]:
    """A ``compare`` report carrying *count* stamped hygiene findings."""
    changes: list[dict[str, object]] = [
        {
            "kind": "exported_not_public",
            "symbol": f"_ZGVZN4pvxs3ioc12IOCShCommandIJPKciEE{i}E",
            "severity": severity,
            "description": "Exported symbol is not declared in any public header",
            **({"cross_source_evolution": evolution} if evolution else {}),
        }
        for i in range(count)
    ]
    if with_real_change:
        changes.append(
            {
                "kind": "func_removed",
                "symbol": "pvxs_genuinely_removed",
                "severity": "breaking",
                "description": "Function removed",
            }
        )
    report: dict[str, object] = {
        "library": "libpvxsIoc.so.1.5",
        "old_version": "snapshot",
        "new_version": "snapshot",
        "verdict": "BREAKING" if with_real_change else "NO_CHANGE",
        "changes": changes,
    }
    if levels:
        report["severity"] = {"config": levels}
    return report


class TestPreExistingHygieneIsNeverThisChange:
    """The cross-product, against an oracle derived from the stamp alone."""

    @pytest.mark.parametrize("evolution", EVOLUTIONS)
    @pytest.mark.parametrize("severity", SEVERITIES)
    def test_only_introduced_findings_reach_the_compatibility_buckets(
        self, evolution: str, severity: str
    ) -> None:
        model = build_model(hygiene_report(evolution=evolution, severity=severity))
        pre_existing = evolution in PRE_EXISTING
        in_buckets = sum(model.counts)
        if pre_existing:
            assert in_buckets == 0, (
                f"{evolution}/{severity} was counted as a change this "
                "comparison introduced"
            )
            assert len(model.background) == 1
        elif evolution == "not_evaluated":
            # The comparison layer refused to say whether it is new. So does
            # this renderer: it is an analysis limitation, not a bucket.
            assert in_buckets == 0
            assert model.has_incomplete
            assert not model.background
        else:
            assert in_buckets == 1
            assert not model.background

    @pytest.mark.parametrize("evolution", sorted(PRE_EXISTING))
    @pytest.mark.parametrize(
        "levels",
        [
            {"quality_issues": "error"},
            {"addition": "error"},
            {"potential_breaking": "error"},
            {"abi_breaking": "error"},
        ],
        ids=["quality-error", "addition-error", "potential-error", "abi-error"],
    )
    def test_a_severity_promotion_cannot_re_admit_a_pre_existing_finding(
        self, evolution: str, levels: dict[str, str]
    ) -> None:
        """A gate makes a finding *block*; it cannot make standing debt into
        something this pull request did. The routing therefore happens ahead
        of every severity rule, and this pins that ordering."""
        model = build_model(
            hygiene_report(evolution=evolution, severity="risk", levels=levels),
            gate_api_break=True,
        )
        assert model.counts == (0, 0, 0)
        assert len(model.background) == 1

    def test_the_reported_self_compare_posts_nothing_under_on_changes(self) -> None:
        """The exact reported shape: a snapshot compared against itself, 336
        persistent hygiene findings, verdict NO_CHANGE."""
        model = build_model(hygiene_report(evolution="persistent", count=336))
        assert model.counts == (0, 0, 0)
        assert model.total_changes == 0
        assert model.background_counts == (336, 0)
        assert not should_post(model, "changes")
        assert should_post(model, "always")

    def test_a_real_change_beside_them_still_posts_and_is_not_buried(self) -> None:
        model = build_model(
            hygiene_report(evolution="persistent", count=336, with_real_change=True)
        )
        assert model.counts == (1, 0, 0)
        assert should_post(model, "changes")
        body = render_comment(model, sha="0123456789ab", detail="full")
        assert "ABI BREAKING" in body
        assert "pvxs_genuinely_removed" in body
        # Summarised, never itemized beside the finding a reviewer must act on.
        assert "336 pre-existing" in body
        assert "not introduced by this change" in body
        assert body.count("IOCShCommand") == 0

    def test_the_what_changed_rollup_counts_only_introduced_findings(self) -> None:
        """The same misattribution, one table over: a rollup that counted
        standing debt reported 336 modifications for a byte-identical
        rebuild."""
        model = build_model(
            hygiene_report(evolution="persistent", count=336, with_real_change=True)
        )
        assert model.change_summary is not None
        assert model.change_summary.counted == 1

    def test_resolved_findings_are_reported_as_good_news_not_as_changes(self) -> None:
        model = build_model(hygiene_report(evolution="resolved", count=4))
        assert model.counts == (0, 0, 0)
        assert model.background_counts == (0, 4)
        body = render_comment(model, sha="0123456789ab", detail="full")
        assert "4 resolved since the baseline" in body

    def test_an_unstamped_finding_is_unaffected(self) -> None:
        """Every ordinary two-sided diff finding carries no stamp at all, and
        must bucket exactly as it did before this rule existed."""
        model = build_model(hygiene_report(evolution="", severity="breaking"))
        assert model.counts == (1, 0, 0)
        assert not model.background

    def test_an_unrecognised_stamp_is_treated_as_this_change_s_own(self) -> None:
        """Fail toward reporting. A state this build does not know cannot be
        assumed pre-existing -- that direction hides findings, and the other
        merely shows one a reviewer can dismiss."""
        model = build_model(hygiene_report(evolution="some_future_state"))
        assert sum(model.counts) == 1
        assert not model.background


class TestBackgroundSurvivesTheAggregateFold:
    """A fan-in may not re-decide what a member already established."""

    @staticmethod
    def _aggregate(tmp_path, *, with_real_change: bool):
        import json

        (tmp_path / "linux.json").write_text(
            json.dumps(
                hygiene_report(
                    evolution="persistent", count=50, with_real_change=with_real_change
                )
            ),
            encoding="utf-8",
        )
        document = {
            "aggregate_schema_version": "1.4",
            "status": "pass",
            "compatibility": {"verdict": "COMPATIBLE", "analyzed_targets": 1},
            "coverage": {
                "status": "complete",
                "required_targets": 1,
                "analyzed_required_targets": 1,
                "missing_required_targets": [],
                "blocking": False,
            },
            "gate": {
                "passed": True,
                "exit_code": 0,
                "blocking_targets": [],
                "coverage_blocking": False,
            },
            "contract_coverage": {"exit_contribution": 0, "incomplete_targets": []},
            "analysis_assurance": {"exit_contribution": 0, "incomplete_targets": []},
            "scope_completeness": {"exit_contribution": 0, "incomplete_targets": []},
            "disposition_audit_missing_targets": [],
            "effective_policy": {
                "missing_required": "fail",
                "unexpected_target": "include",
                "source": "default",
            },
            "targets": [
                {
                    "target_id": "linux-x86_64",
                    "required": True,
                    "state": "analyzed",
                    "compatibility_verdict": "COMPATIBLE",
                    "gate": {
                        "exit_code": 0,
                        "blocking": False,
                        "blocking_categories": [],
                        "from_report": True,
                    },
                    "contract_coverage_exit": 0,
                    "analysis_assurance_exit": 0,
                    "scope_completeness_exit": 0,
                    "report_path": "linux.json",
                }
            ],
            "unexpected_targets": [],
            "profile_matrix": [],
            "finding_matrix": [],
        }
        path = tmp_path / "aggregate.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return build_model(document, report_dir=path.parent)

    def test_a_clean_fan_in_over_standing_debt_posts_nothing(self, tmp_path) -> None:
        model = self._aggregate(tmp_path, with_real_change=False)
        assert model.counts == (0, 0, 0)
        assert model.background_counts == (50, 0)
        assert not should_post(model, "changes")

    def test_a_fan_in_with_a_real_change_reports_it_and_summarises_the_rest(
        self, tmp_path
    ) -> None:
        model = self._aggregate(tmp_path, with_real_change=True)
        assert model.counts == (1, 0, 0)
        assert should_post(model, "changes")
        body = render_comment(model, sha="0123456789ab", detail="full")
        assert "50 pre-existing" in body
        assert "pvxs_genuinely_removed" in body


def test_the_fixture_actually_stamps_what_it_claims() -> None:
    """Vacuity guard on this module's own builder.

    Every assertion above rests on the report carrying the stamp; a builder
    that silently dropped it would make the whole cross-product pass against
    a renderer with no rule at all.
    """
    stamped = hygiene_report(evolution="persistent")["changes"]
    assert isinstance(stamped, list)
    assert stamped[0]["cross_source_evolution"] == "persistent"
    unstamped = hygiene_report(evolution="")["changes"]
    assert isinstance(unstamped, list)
    assert "cross_source_evolution" not in unstamped[0]
