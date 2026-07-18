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

"""Unit tests for abicheck.assessment — fan-out builds, fan-in assessment.

Covers the acceptance cases from the fan-out/fan-in RFC (§11): a target that
never produced a valid artifact/comparison must never be treated as an empty,
compatible ABI, and findings/coverage are graded as distinct concepts.
"""

from __future__ import annotations

import pytest

from abicheck.assessment import (
    Assessment,
    AssessmentManifest,
    CoveragePolicy,
    CoverageVerdict,
    FindingsVerdict,
    TargetOutcome,
    TargetSpec,
    TargetState,
    compare_target_sets,
)
from abicheck.change_registry_types import Verdict
from abicheck.checker_types import DiffResult

LINUX = "linux-x86_64"
WINDOWS = "windows-x86_64"
HEAD_SHA = "0123456789abcdef"


def _manifest(
    *,
    required: tuple[str, ...] = (LINUX, WINDOWS),
    optional: tuple[str, ...] = (),
) -> AssessmentManifest:
    targets = tuple(TargetSpec(t, required=True) for t in required) + tuple(
        TargetSpec(t, required=False) for t in optional
    )
    return AssessmentManifest(
        assessment_id="abc123", head_sha=HEAD_SHA, targets=targets
    )


def _diff(verdict: Verdict, library: str = "libfoo.so") -> DiffResult:
    return DiffResult(
        old_version="1.0", new_version="1.1", library=library, verdict=verdict
    )


class TestTargetSpec:
    @pytest.mark.parametrize("bad_required", [None, "false", 0, 1, "yes"])
    def test_rejects_non_boolean_required(self, bad_required: object):
        with pytest.raises(ValueError):
            TargetSpec(LINUX, required=bad_required)

    def test_rejects_empty_id(self):
        with pytest.raises(ValueError):
            TargetSpec("")

    @pytest.mark.parametrize("bad_id", [123, True, 3.14, ["linux"], {"id": "linux"}])
    def test_rejects_non_string_id(self, bad_id: object):
        with pytest.raises(ValueError):
            TargetSpec(bad_id)  # type: ignore[arg-type]


class TestAssessmentManifest:
    def test_from_dict_round_trip(self):
        data = {
            "assessment_id": "abc123",
            "head_sha": HEAD_SHA,
            "targets": [
                {"id": LINUX, "required": True},
                {"id": WINDOWS, "required": False},
            ],
        }
        manifest = AssessmentManifest.from_dict(data)
        assert manifest.assessment_id == "abc123"
        assert manifest.target_ids == {LINUX, WINDOWS}
        assert manifest.required_target_ids == {LINUX}
        assert manifest.to_dict() == data

    def test_from_dict_rejects_missing_targets(self):
        with pytest.raises(ValueError):
            AssessmentManifest.from_dict(
                {"assessment_id": "a", "head_sha": "s", "targets": []}
            )

    def test_from_dict_rejects_duplicate_target_id(self):
        data = {
            "assessment_id": "a",
            "head_sha": "s",
            "targets": [{"id": LINUX}, {"id": LINUX}],
        }
        with pytest.raises(ValueError):
            AssessmentManifest.from_dict(data)

    def test_from_dict_requires_assessment_id_and_head_sha(self):
        with pytest.raises(ValueError):
            AssessmentManifest.from_dict({"targets": [{"id": LINUX}]})

    def test_from_dict_rejects_empty_string_assessment_id(self):
        # Empty strings pass the isinstance(str) check but must still be
        # rejected as "required" — distinct from the non-string-type case.
        with pytest.raises(ValueError):
            AssessmentManifest.from_dict(
                {"assessment_id": "", "head_sha": HEAD_SHA, "targets": [{"id": LINUX}]}
            )

    @pytest.mark.parametrize("bad_assessment_id", [12345, True, 3.14, ["abc"]])
    def test_from_dict_rejects_non_string_assessment_id(
        self, bad_assessment_id: object
    ):
        # str(assessment_id) would previously coerce a numeric id (e.g. a CI
        # run id) into a string silently. TargetOutcome.assessment_id is
        # never coerced, so a matching outcome carrying the same numeric id
        # would then compare unequal and get dropped as stale/foreign.
        with pytest.raises(ValueError):
            AssessmentManifest.from_dict(
                {
                    "assessment_id": bad_assessment_id,
                    "head_sha": HEAD_SHA,
                    "targets": [{"id": LINUX}],
                }
            )

    @pytest.mark.parametrize("bad_head_sha", [12345, True, 3.14])
    def test_from_dict_rejects_non_string_head_sha(self, bad_head_sha: object):
        with pytest.raises(ValueError):
            AssessmentManifest.from_dict(
                {
                    "assessment_id": "abc123",
                    "head_sha": bad_head_sha,
                    "targets": [{"id": LINUX}],
                }
            )

    @pytest.mark.parametrize("bad_required", [None, "false", 0, 1])
    def test_from_dict_rejects_non_boolean_required(self, bad_required: object):
        data = {
            "assessment_id": "a",
            "head_sha": "s",
            "targets": [{"id": LINUX, "required": bad_required}],
        }
        with pytest.raises(ValueError):
            AssessmentManifest.from_dict(data)

    def test_from_dict_rejects_non_dict_input(self):
        with pytest.raises(TypeError):
            AssessmentManifest.from_dict(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_from_dict_rejects_non_list_targets(self):
        with pytest.raises(ValueError):
            AssessmentManifest.from_dict(
                {"assessment_id": "a", "head_sha": "s", "targets": "linux-x86_64"}
            )

    @pytest.mark.parametrize(
        "bad_entry", ["not-a-dict", {}, {"id": ""}, {"id": 123}, {"id": None}]
    )
    def test_from_dict_rejects_malformed_target_entry(self, bad_entry: object):
        with pytest.raises(ValueError):
            AssessmentManifest.from_dict(
                {"assessment_id": "a", "head_sha": "s", "targets": [bad_entry]}
            )

    def test_from_dict_does_not_silently_coerce_non_string_id(self):
        # str(entry["id"]) would previously turn 123 into "123" without
        # complaint. That could make a manifest id fail to match a
        # TargetOutcome.target_id submitted for the same logical target,
        # since TargetOutcome requires a genuine str with no coercion.
        with pytest.raises(ValueError):
            AssessmentManifest.from_dict(
                {"assessment_id": "a", "head_sha": "s", "targets": [{"id": 123}]}
            )

    def test_direct_construction_rejects_empty_targets(self):
        with pytest.raises(ValueError):
            AssessmentManifest(assessment_id="a", head_sha="s", targets=())

    def test_direct_construction_rejects_duplicate_target_id(self):
        with pytest.raises(ValueError):
            AssessmentManifest(
                assessment_id="a",
                head_sha="s",
                targets=(TargetSpec(LINUX), TargetSpec(LINUX)),
            )

    def test_direct_construction_rejects_empty_assessment_id(self):
        with pytest.raises(ValueError):
            AssessmentManifest(
                assessment_id="", head_sha="s", targets=(TargetSpec(LINUX),)
            )

    def test_direct_construction_rejects_empty_head_sha(self):
        with pytest.raises(ValueError):
            AssessmentManifest(
                assessment_id="a", head_sha="", targets=(TargetSpec(LINUX),)
            )

    def test_empty_identity_manifest_cannot_be_smuggled_through_stale_guard(self):
        # Regression for the failure scenario the above guards close: an
        # empty-identity manifest would give record()'s stale-data check
        # nothing to compare against, so a malformed outcome that also
        # carries empty identity fields would match instead of being
        # rejected. Prove the manifest itself can no longer be constructed
        # that way.
        with pytest.raises(ValueError):
            AssessmentManifest(
                assessment_id="", head_sha="", targets=(TargetSpec(LINUX),)
            )

    @pytest.mark.parametrize("bad_id", [12345, True, 3.14])
    def test_direct_construction_rejects_non_string_assessment_id(self, bad_id: object):
        # Truthiness alone accepts assessment_id=12345 (mirrors the
        # from_dict() gap): a well-typed outcome's "12345" would then
        # compare unequal to the numeric manifest id and get dropped as
        # foreign by record().
        with pytest.raises(ValueError):
            AssessmentManifest(
                assessment_id=bad_id, head_sha="s", targets=(TargetSpec(LINUX),)
            )

    @pytest.mark.parametrize("bad_sha", [12345, True, 3.14])
    def test_direct_construction_rejects_non_string_head_sha(self, bad_sha: object):
        with pytest.raises(ValueError):
            AssessmentManifest(
                assessment_id="a", head_sha=bad_sha, targets=(TargetSpec(LINUX),)
            )


class TestTargetOutcome:
    def test_analyzed_requires_findings(self):
        with pytest.raises(ValueError):
            TargetOutcome(target_id=LINUX, state=TargetState.ANALYZED)

    def test_unavailable_rejects_analyzed_state(self):
        with pytest.raises(ValueError):
            TargetOutcome.unavailable(LINUX, TargetState.ANALYZED)

    def test_unavailable_rejects_findings(self):
        with pytest.raises(ValueError):
            TargetOutcome(
                target_id=LINUX,
                state=TargetState.BUILD_FAILED,
                findings=_diff(Verdict.COMPATIBLE),
            )

    def test_is_available(self):
        assert TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)).is_available
        assert not TargetOutcome.unavailable(
            LINUX, TargetState.BUILD_FAILED
        ).is_available

    def test_unavailable_rejects_incomplete_state(self):
        # INCOMPLETE is synthesized by Assessment.finalize(); it must not be
        # constructible as a submitted outcome via the public factory.
        with pytest.raises(ValueError):
            TargetOutcome.unavailable(LINUX, TargetState.INCOMPLETE)

    def test_record_drops_submitted_incomplete_outcome(self):
        # Even a directly-constructed INCOMPLETE outcome (bypassing
        # unavailable()'s guard) must not be accepted by record() — a bogus
        # high-attempt submission must not be able to block a legitimate
        # lower-attempt result that arrives afterward.
        assessment = Assessment(_manifest())
        assessment.record(
            TargetOutcome(target_id=LINUX, state=TargetState.INCOMPLETE, attempt=99)
        )
        assessment.record(
            TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE), attempt=1)
        )
        result = assessment.finalize()

        assert result.outcomes[LINUX].state is TargetState.ANALYZED

    def test_unavailable_rejects_pending_state(self):
        # PENDING isn't a terminal result; it must not be constructible as a
        # submitted outcome via the public factory.
        with pytest.raises(ValueError):
            TargetOutcome.unavailable(LINUX, TargetState.PENDING)

    def test_record_drops_submitted_pending_outcome(self):
        # A still-running target reported as PENDING must not read as a
        # final unavailable result — record() must ignore it, the same as
        # INCOMPLETE, even bypassing unavailable()'s guard via direct
        # construction.
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome(target_id=LINUX, state=TargetState.PENDING))
        result = assessment.finalize()

        assert result.outcomes[LINUX].state is TargetState.INCOMPLETE

    def test_raw_string_state_is_normalized_to_enum(self):
        # A caller hydrating from JSON/CLI input hands us the TargetState's
        # string *value*, not the enum member — it must still behave
        # identically (in particular, outcome.state.value must not crash).
        outcome = TargetOutcome.unavailable(LINUX, "build_failed")

        assert outcome.state is TargetState.BUILD_FAILED
        assert outcome.state.value == "build_failed"

    def test_raw_string_analyzed_state_without_findings_still_raises(self):
        # Going through unavailable() would hit its own "use
        # TargetOutcome.analyzed()" guard first (state coerces to ANALYZED
        # before that check), not the __post_init__ path this test targets —
        # construct directly so the missing-findings validation is what
        # actually fires.
        with pytest.raises(ValueError, match="must carry findings"):
            TargetOutcome(target_id=LINUX, state="analyzed")

    def test_raw_string_incomplete_state_still_rejected(self):
        with pytest.raises(ValueError):
            TargetOutcome.unavailable(LINUX, "incomplete")

    def test_record_drops_incomplete_outcome_even_as_raw_string(self):
        # The is-identity check in record() only works once __post_init__
        # has normalized a raw string state to the real enum member.
        assessment = Assessment(_manifest())
        assessment.record(
            TargetOutcome(target_id=LINUX, state="incomplete", attempt=99)
        )
        assessment.record(
            TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE), attempt=1)
        )
        result = assessment.finalize()

        assert result.outcomes[LINUX].state is TargetState.ANALYZED

    def test_unrecognized_state_string_raises(self):
        with pytest.raises(ValueError):
            TargetOutcome.unavailable(LINUX, "not_a_real_state")

    @pytest.mark.parametrize("bad_attempt", ["2", True, 1.0, None])
    def test_rejects_non_int_attempt(self, bad_attempt: object):
        with pytest.raises(ValueError):
            TargetOutcome.unavailable(
                LINUX, TargetState.BUILD_FAILED, attempt=bad_attempt
            )

    def test_string_attempt_cannot_use_lexicographic_ordering_to_go_stale(self):
        # The exact failure scenario a string attempt enables: "10" < "2"
        # lexicographically, so a stale attempt="2" would otherwise beat a
        # real attempt=10 rerun. Constructing either as a string must raise
        # rather than let record() compare them incorrectly.
        with pytest.raises(ValueError):
            TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE), attempt="10")
        with pytest.raises(ValueError):
            TargetOutcome.unavailable(LINUX, TargetState.BUILD_FAILED, attempt="2")

    @pytest.mark.parametrize("bad_target_id", [123, True, 3.14, None, ""])
    def test_rejects_non_string_target_id(self, bad_target_id: object):
        with pytest.raises(ValueError):
            TargetOutcome.unavailable(bad_target_id, TargetState.BUILD_FAILED)

    def test_non_string_target_id_cannot_masquerade_as_the_string_target(self):
        # An outcome whose target_id was hydrated as an int instead of the
        # manifest's string id would fail `in manifest.target_ids` silently,
        # get routed into additional_outcomes as "unbaselined", and
        # finalize() would then synthesize INCOMPLETE for the real target
        # despite a matching outcome having arrived. Prove it can't be
        # constructed that way at all.
        manifest = _manifest(required=(LINUX,))
        with pytest.raises(ValueError):
            TargetOutcome.analyzed(123, _diff(Verdict.COMPATIBLE))  # type: ignore[arg-type]
        # A well-typed outcome for the same id still records correctly.
        assessment = Assessment(manifest)
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        result = assessment.finalize()
        assert result.outcomes[LINUX].state is TargetState.ANALYZED
        assert not result.additional_outcomes

    @pytest.mark.parametrize("bad_id", [12345, True, 3.14])
    def test_rejects_non_string_assessment_id(self, bad_id: object):
        with pytest.raises(ValueError):
            TargetOutcome.unavailable(
                LINUX, TargetState.BUILD_FAILED, assessment_id=bad_id
            )

    @pytest.mark.parametrize("bad_sha", [12345, True, 3.14])
    def test_rejects_non_string_head_sha(self, bad_sha: object):
        with pytest.raises(ValueError):
            TargetOutcome.unavailable(LINUX, TargetState.BUILD_FAILED, head_sha=bad_sha)

    def test_numeric_assessment_id_cannot_bypass_stale_guard_via_mismatch(self):
        # An outcome hydrated with a numeric assessment_id (matching a
        # manifest whose assessment_id came from the same un-stringified
        # source) must not compare unequal to the manifest's string form
        # and get dropped as a foreign assessment. Since neither side can
        # be constructed with a numeric identity anymore, prove the
        # symmetric string identities actually match end to end.
        manifest = AssessmentManifest.from_dict(
            {"assessment_id": "12345", "head_sha": HEAD_SHA, "targets": [{"id": LINUX}]}
        )
        assessment = Assessment(manifest, require_identity=True)
        assessment.record(
            TargetOutcome.analyzed(
                LINUX,
                _diff(Verdict.COMPATIBLE),
                assessment_id="12345",
                head_sha=HEAD_SHA,
            )
        )
        result = assessment.finalize()
        assert result.outcomes[LINUX].state is TargetState.ANALYZED


class TestAcceptanceCases:
    """One test per row of the RFC §11 acceptance table."""

    def test_both_targets_succeed_clean(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        assessment.record(TargetOutcome.analyzed(WINDOWS, _diff(Verdict.COMPATIBLE)))
        result = assessment.finalize()

        assert not result.is_partial
        assert result.findings_verdict is FindingsVerdict.SUCCESS
        assert result.coverage_verdict() is CoverageVerdict.SUCCESS

    def test_linux_regression_windows_clean(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.BREAKING)))
        assessment.record(TargetOutcome.analyzed(WINDOWS, _diff(Verdict.COMPATIBLE)))
        result = assessment.finalize()

        assert not result.is_partial
        assert result.findings_verdict is FindingsVerdict.FAILURE
        assert result.coverage_verdict() is CoverageVerdict.SUCCESS

    def test_linux_clean_windows_build_fails(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        assessment.record(
            TargetOutcome.unavailable(
                WINDOWS, TargetState.BUILD_FAILED, reason="candidate build failed"
            )
        )
        result = assessment.finalize()

        assert result.is_partial
        assert result.findings_verdict is FindingsVerdict.SUCCESS
        assert result.unavailable_target_ids == {WINDOWS}
        # Linux is still reported even though Windows is unavailable.
        assert result.analyzed_target_ids == {LINUX}

    def test_linux_regression_windows_build_fails(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.BREAKING)))
        assessment.record(TargetOutcome.unavailable(WINDOWS, TargetState.BUILD_FAILED))
        result = assessment.finalize()

        assert result.is_partial
        assert result.findings_verdict is FindingsVerdict.FAILURE
        # No synthetic Windows finding was fabricated.
        assert {tid for tid, _ in result.findings} == {LINUX}

    def test_both_builds_fail_no_abi_verdict(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.unavailable(LINUX, TargetState.BUILD_FAILED))
        assessment.record(TargetOutcome.unavailable(WINDOWS, TargetState.BUILD_FAILED))
        result = assessment.finalize()

        assert result.findings == ()
        assert result.findings_verdict is FindingsVerdict.NEUTRAL
        assert result.coverage_verdict() is CoverageVerdict.FAILURE

    def test_artifact_missing_is_not_empty_abi(self):
        assessment = Assessment(_manifest())
        assessment.record(
            TargetOutcome.unavailable(
                LINUX, TargetState.ARTIFACT_MISSING, reason="no wheel produced"
            )
        )
        assessment.record(TargetOutcome.analyzed(WINDOWS, _diff(Verdict.COMPATIBLE)))
        result = assessment.finalize()

        assert result.outcomes[LINUX].state is TargetState.ARTIFACT_MISSING
        assert LINUX not in {tid for tid, _ in result.findings}

    def test_baseline_missing(self):
        assessment = Assessment(_manifest())
        assessment.record(
            TargetOutcome.unavailable(LINUX, TargetState.BASELINE_MISSING)
        )
        assessment.record(TargetOutcome.analyzed(WINDOWS, _diff(Verdict.COMPATIBLE)))
        result = assessment.finalize()

        assert result.outcomes[LINUX].state is TargetState.BASELINE_MISSING
        assert result.unavailable_target_ids == {LINUX}

    def test_analysis_crash_is_not_a_compatibility_failure(self):
        assessment = Assessment(_manifest())
        assessment.record(
            TargetOutcome.unavailable(
                LINUX, TargetState.ANALYSIS_FAILED, reason="tool crashed"
            )
        )
        assessment.record(TargetOutcome.analyzed(WINDOWS, _diff(Verdict.COMPATIBLE)))
        result = assessment.finalize()

        # analysis_failed is unavailable, not a regression finding.
        assert result.findings_verdict is FindingsVerdict.SUCCESS
        assert result.outcomes[LINUX].state is TargetState.ANALYSIS_FAILED

    def test_cancelled_job_never_reports_is_incomplete(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        # Windows never calls record() at all — e.g. the runner was killed.
        result = assessment.finalize()

        assert result.outcomes[WINDOWS].state is TargetState.INCOMPLETE
        assert WINDOWS in result.unavailable_target_ids

    def test_windows_rerun_moves_partial_to_complete(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        assessment.record(
            TargetOutcome.unavailable(WINDOWS, TargetState.BUILD_FAILED, attempt=1)
        )
        partial = assessment.finalize()
        assert partial.is_partial

        assessment.record(
            TargetOutcome.analyzed(WINDOWS, _diff(Verdict.COMPATIBLE), attempt=2)
        )
        complete = assessment.finalize()
        assert not complete.is_partial
        assert complete.outcomes[WINDOWS].state is TargetState.ANALYZED

    def test_stale_lower_attempt_does_not_clobber_newer_result(self):
        assessment = Assessment(_manifest())
        assessment.record(
            TargetOutcome.analyzed(WINDOWS, _diff(Verdict.COMPATIBLE), attempt=2)
        )
        # A late-arriving duplicate of the old failed attempt must not win.
        assessment.record(
            TargetOutcome.unavailable(WINDOWS, TargetState.BUILD_FAILED, attempt=1)
        )

        result = assessment.finalize()
        assert result.outcomes[WINDOWS].state is TargetState.ANALYZED

    def test_outcome_for_a_different_commit_is_dropped(self):
        assessment = Assessment(_manifest())
        assessment.record(
            TargetOutcome.analyzed(LINUX, _diff(Verdict.BREAKING), head_sha="stale-sha")
        )
        result = assessment.finalize()

        assert result.outcomes[LINUX].state is TargetState.INCOMPLETE

    def test_outcome_for_a_different_assessment_on_the_same_commit_is_dropped(self):
        # A rerun of the same commit under a new assessment id (e.g. after
        # the target matrix changed) must not let a delayed outcome from the
        # superseded assessment win just because the head_sha still matches.
        assessment = Assessment(_manifest())  # assessment_id="abc123"
        assessment.record(
            TargetOutcome.analyzed(
                LINUX,
                _diff(Verdict.BREAKING),
                head_sha=HEAD_SHA,
                assessment_id="stale-assessment-id",
            )
        )
        result = assessment.finalize()

        assert result.outcomes[LINUX].state is TargetState.INCOMPLETE

    def test_require_identity_rejects_outcome_with_no_identity_at_all(self):
        # Simulates ingesting outcomes from an untrusted/async source (a
        # queued artifact, a webhook payload): an outcome that omits both
        # identity fields must not be trusted by default.
        assessment = Assessment(_manifest(), require_identity=True)
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        result = assessment.finalize()

        assert result.outcomes[LINUX].state is TargetState.INCOMPLETE

    def test_require_identity_accepts_outcome_with_matching_identity(self):
        assessment = Assessment(_manifest(), require_identity=True)
        assessment.record(
            TargetOutcome.analyzed(
                LINUX,
                _diff(Verdict.COMPATIBLE),
                head_sha=HEAD_SHA,
                assessment_id="abc123",
            )
        )
        result = assessment.finalize()

        assert result.outcomes[LINUX].state is TargetState.ANALYZED

    def test_required_target_removed_is_a_coverage_change_not_symbol_removal(self):
        previous = _manifest(required=(LINUX, WINDOWS))
        current = _manifest(required=(LINUX,))
        change = compare_target_sets(previous, current)

        assert change.removed == {WINDOWS}
        assert change.added == frozenset()
        assert change.changed


class TestCoveragePolicy:
    def test_missing_required_target_defaults_to_failure(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        result = assessment.finalize()

        assert result.coverage_verdict() is CoverageVerdict.FAILURE

    def test_missing_required_target_policy_can_downgrade_to_neutral(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        result = assessment.finalize()

        lenient = CoveragePolicy(missing_required_target=CoverageVerdict.NEUTRAL)
        assert result.coverage_verdict(lenient) is CoverageVerdict.NEUTRAL

    def test_missing_optional_target_is_always_neutral(self):
        manifest = _manifest(required=(LINUX,), optional=(WINDOWS,))
        assessment = Assessment(manifest)
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        result = assessment.finalize()

        assert result.coverage_verdict() is CoverageVerdict.NEUTRAL

    def test_no_analyzed_targets_defaults_to_failure_even_with_lenient_required_policy(
        self,
    ):
        # A full outage must not slip through just because missing_required_target
        # was relaxed — it is governed by the separate no_analyzed_targets knob.
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.unavailable(LINUX, TargetState.BUILD_FAILED))
        assessment.record(TargetOutcome.unavailable(WINDOWS, TargetState.BUILD_FAILED))
        result = assessment.finalize()

        lenient = CoveragePolicy(missing_required_target=CoverageVerdict.NEUTRAL)
        assert result.coverage_verdict(lenient) is CoverageVerdict.FAILURE

    def test_no_analyzed_targets_policy_can_downgrade_to_neutral(self):
        # Optional-only manifest, nothing analyzed: no required coverage is
        # missing, so this must be governed by no_analyzed_targets, not a
        # hardcoded failure.
        manifest = _manifest(required=(), optional=(LINUX, WINDOWS))
        assessment = Assessment(manifest)
        assessment.record(TargetOutcome.unavailable(LINUX, TargetState.BUILD_FAILED))
        assessment.record(TargetOutcome.unavailable(WINDOWS, TargetState.BUILD_FAILED))
        result = assessment.finalize()

        assert result.coverage_verdict() is CoverageVerdict.FAILURE
        lenient = CoveragePolicy(no_analyzed_targets=CoverageVerdict.NEUTRAL)
        assert result.coverage_verdict(lenient) is CoverageVerdict.NEUTRAL


class TestUnbaselinedTarget:
    def test_target_not_in_manifest_is_tracked_separately(self):
        assessment = Assessment(_manifest(required=(LINUX,)))
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        assessment.record(
            TargetOutcome.analyzed("macos-arm64", _diff(Verdict.COMPATIBLE))
        )
        result = assessment.finalize()

        assert "macos-arm64" not in result.outcomes
        assert "macos-arm64" in result.additional_outcomes
        # It must not count toward the expected-target coverage math.
        assert result.manifest.target_ids == {LINUX}


class TestProgressAndRendering:
    def test_progress_reports_recorded_vs_expected(self):
        assessment = Assessment(_manifest())
        assert assessment.progress() == (0, 2)
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        assert assessment.progress() == (1, 2)

    def test_render_text_partial_mentions_unavailable_target(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        assessment.record(
            TargetOutcome.unavailable(
                WINDOWS, TargetState.BUILD_FAILED, reason="candidate build failed"
            )
        )
        text = assessment.finalize().render_text()

        assert "Partial" in text
        assert "candidate build failed" in text
        assert WINDOWS in text
        assert "No ABI regressions" in text

    def test_render_text_complete_mentions_no_regressions(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        assessment.record(TargetOutcome.analyzed(WINDOWS, _diff(Verdict.COMPATIBLE)))
        text = assessment.finalize().render_text()

        assert "Complete" in text
        assert "No ABI regressions" in text

    def test_render_text_reports_regressed_targets(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.BREAKING)))
        assessment.record(TargetOutcome.analyzed(WINDOWS, _diff(Verdict.COMPATIBLE)))
        text = assessment.finalize().render_text()

        assert "ABI regressions found on" in text
        assert LINUX in text

    def test_render_text_includes_job_url_for_unavailable_target(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.analyzed(LINUX, _diff(Verdict.COMPATIBLE)))
        assessment.record(
            TargetOutcome.unavailable(
                WINDOWS,
                TargetState.BUILD_FAILED,
                job_url="https://ci.example/jobs/42",
            )
        )
        text = assessment.finalize().render_text()

        assert "https://ci.example/jobs/42" in text

    def test_render_text_neutral_when_no_targets_analyzed(self):
        assessment = Assessment(_manifest())
        assessment.record(TargetOutcome.unavailable(LINUX, TargetState.BUILD_FAILED))
        assessment.record(TargetOutcome.unavailable(WINDOWS, TargetState.BUILD_FAILED))
        text = assessment.finalize().render_text()

        assert "No targets could be analyzed." in text
