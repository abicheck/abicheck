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

"""ADR-066 D4/D5/S2: the versioning policy model and its two evaluators.

Covers the plan's "Tests" table entry for this workstream: "strict versus
relaxed policy on the same raw delta (identical findings and evidence,
different acceptance)" -- as both a fixed-example test and a Hypothesis
property test stating the invariant generally, per this repo's own
bug-class-regression-testing guidance (a fixed example alone only forecloses
the one input it names).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from abicheck.checker_policy import ChangeKind, Confidence, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.model import AbiSnapshot, Function
from abicheck.policy.versioning_policy import (
    CompatibilityPromise,
    DeprecationWindow,
    SupportWindow,
    VersioningEnforcement,
    VersioningPolicy,
    VersioningScheme,
    built_in_default_versioning_policy,
    evaluate_release_acceptance,
    integrate_policy_acceptance,
)
from abicheck.semver import SemverBump, recommend_release
from abicheck.serialization import save_snapshot
from abicheck.workflows.history import run_history_request


def _diff_result(
    verdict: Verdict, *, changes: list[Change] | None = None
) -> DiffResult:
    return DiffResult(
        verdict=verdict,
        changes=changes or [],
        confidence=Confidence.HIGH,
        evidence_tiers={"elf"},
        old_version="1.0",
        new_version="2.0",
        library="libfoo",
    )


def _breaking_result() -> DiffResult:
    change = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="foo",
        description="removed",
        old_value="void foo()",
        new_value=None,
    )
    return _diff_result(Verdict.BREAKING, changes=[change])


class TestVersioningPolicyModel:
    def test_built_in_default_matches_current_behavior(self) -> None:
        policy = built_in_default_versioning_policy()
        assert policy.scheme is VersioningScheme.STRICT_SEMVER
        assert policy.promise is CompatibilityPromise.NONE
        assert policy.support_window == SupportWindow()
        assert policy.deprecation_window == DeprecationWindow(min_releases=0)
        assert policy.enforcement is VersioningEnforcement.WARN

    def test_rejects_wrong_types(self) -> None:
        with pytest.raises(TypeError):
            VersioningPolicy(scheme="strict_semver")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            VersioningPolicy(enforcement="warn")  # type: ignore[arg-type]

    def test_support_window_validates_shape(self) -> None:
        with pytest.raises(ValueError):
            SupportWindow(kind="last_n_minors")  # missing last_n
        with pytest.raises(ValueError):
            SupportWindow(kind="compatibility_line")  # missing line
        with pytest.raises(ValueError):
            SupportWindow(kind="explicit")  # missing versions
        with pytest.raises(ValueError):
            SupportWindow(kind="not_a_real_kind")

    def test_deprecation_window_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            DeprecationWindow(min_releases=-1)

    def test_to_dict_round_trips_shape(self) -> None:
        policy = VersioningPolicy(
            scheme=VersioningScheme.CALVER,
            promise=CompatibilityPromise.API_WITHIN_MINOR,
            support_window=SupportWindow(kind="last_n_minors", last_n=2),
            deprecation_window=DeprecationWindow(min_releases=1),
            enforcement=VersioningEnforcement.BLOCK,
        )
        d = policy.to_dict()
        assert d["scheme"] == "calver"
        assert d["promise"] == "api_within_minor"
        assert d["support_window"] == {
            "kind": "last_n_minors",
            "last_n": 2,
            "line": None,
            "versions": [],
        }
        assert d["deprecation_window"] == {"min_releases": 1}
        assert d["enforcement"] == "block"

    def test_policy_is_hashable(self) -> None:
        # Required for FieldCandidate.value (ADR-049 D7 resolver).
        assert isinstance(hash(built_in_default_versioning_policy()), int)
        assert isinstance(
            hash(
                VersioningPolicy(
                    support_window=SupportWindow(kind="explicit", versions=("1.0",))
                )
            ),
            int,
        )


class TestReleaseAcceptance:
    """D5: 'policy changes acceptance; it never changes facts.'"""

    def test_non_breaking_verdict_always_accepted(self) -> None:
        strict = VersioningPolicy(
            promise=CompatibilityPromise.ABI_WITHIN_MAJOR,
            enforcement=VersioningEnforcement.BLOCK,
        )
        result = _diff_result(Verdict.COMPATIBLE)
        acceptance = evaluate_release_acceptance(result, strict)
        assert acceptance.accepted is True

    def test_no_promise_always_accepts_a_break(self) -> None:
        policy = VersioningPolicy(
            promise=CompatibilityPromise.NONE,
            enforcement=VersioningEnforcement.BLOCK,
        )
        acceptance = evaluate_release_acceptance(_breaking_result(), policy)
        assert acceptance.accepted is True

    def test_strict_promise_with_block_rejects_a_break(self) -> None:
        policy = VersioningPolicy(
            promise=CompatibilityPromise.ABI_WITHIN_MAJOR,
            enforcement=VersioningEnforcement.BLOCK,
        )
        acceptance = evaluate_release_acceptance(_breaking_result(), policy)
        assert acceptance.accepted is False

    def test_strict_promise_with_warn_accepts_but_notes_deviation(self) -> None:
        policy = VersioningPolicy(
            promise=CompatibilityPromise.ABI_WITHIN_MAJOR,
            enforcement=VersioningEnforcement.WARN,
        )
        acceptance = evaluate_release_acceptance(_breaking_result(), policy)
        assert acceptance.accepted is True
        assert "deviat" in acceptance.detail

    def test_relaxed_vs_strict_same_result_different_acceptance(self) -> None:
        """The exact fixed-example version of the plan's mandated property."""
        result = _breaking_result()
        strict = VersioningPolicy(
            promise=CompatibilityPromise.ABI_WITHIN_MAJOR,
            enforcement=VersioningEnforcement.BLOCK,
        )
        relaxed = VersioningPolicy(
            promise=CompatibilityPromise.NONE,
            enforcement=VersioningEnforcement.WARN,
        )
        strict_rec = recommend_release(result, versioning_policy=strict)
        relaxed_rec = recommend_release(result, versioning_policy=relaxed)

        # Same observed incompatibilities: identical bump/soname/state/rationale.
        assert strict_rec.bump == relaxed_rec.bump
        assert strict_rec.soname == relaxed_rec.soname
        assert strict_rec.state == relaxed_rec.state
        assert strict_rec.rationale == relaxed_rec.rationale
        # Same raw finding set: recommend_release never touches result.changes.
        assert result.changes == result.changes

        # Different acceptance.
        assert strict_rec.policy_acceptance is not None
        assert relaxed_rec.policy_acceptance is not None
        assert strict_rec.policy_acceptance.accepted is False
        assert relaxed_rec.policy_acceptance.accepted is True


class TestRecommendReleaseIntegration:
    def test_no_policy_means_unchanged_behavior(self) -> None:
        result = _breaking_result()
        with_none = recommend_release(result)
        assert with_none.policy_acceptance is None
        # to_dict() carries the additive null key, nothing else changes.
        d = with_none.to_dict()
        assert d["policy_acceptance"] is None
        assert d["version_bump"] == SemverBump.MAJOR.value

    def test_policy_never_changes_bump_or_soname(self) -> None:
        result = _breaking_result()
        baseline = recommend_release(result)
        for policy in (
            VersioningPolicy(
                promise=CompatibilityPromise.ABI_WITHIN_MAJOR,
                enforcement=VersioningEnforcement.BLOCK,
            ),
            VersioningPolicy(
                promise=CompatibilityPromise.NONE,
                enforcement=VersioningEnforcement.WARN,
            ),
        ):
            rec = recommend_release(result, versioning_policy=policy)
            assert rec.bump == baseline.bump
            assert rec.soname == baseline.soname
            assert rec.state == baseline.state
            assert rec.rationale == baseline.rationale

    def test_integrate_policy_acceptance_with_none_is_identity(self) -> None:
        result = _breaking_result()
        rec = recommend_release(result)
        assert integrate_policy_acceptance(rec, result, None) is rec


# ---------------------------------------------------------------------------
# Hypothesis property test: the mandated invariant, generalized.
# ---------------------------------------------------------------------------

_VERDICTS = st.sampled_from(list(Verdict))
_PROMISES = st.sampled_from(list(CompatibilityPromise))
_ENFORCEMENTS = st.sampled_from(list(VersioningEnforcement))


@given(verdict=_VERDICTS, promise=_PROMISES, enforcement=_ENFORCEMENTS)
def test_policy_never_changes_observed_recommendation(
    verdict: Verdict, promise: CompatibilityPromise, enforcement: VersioningEnforcement
) -> None:
    """ADR-066 D5, as an executable property over the whole input space:
    for *any* verdict and *any* versioning policy, the observed
    recommendation (bump/soname/state/rationale) is bit-for-bit identical
    to the policy-unaware recommendation. Only ``policy_acceptance`` may
    differ."""
    changes = (
        [
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="foo",
                description="removed",
                old_value="void foo()",
                new_value=None,
            )
        ]
        if verdict in (Verdict.BREAKING, Verdict.API_BREAK)
        else []
    )
    result = _diff_result(verdict, changes=changes)
    baseline = recommend_release(result)
    policy = VersioningPolicy(promise=promise, enforcement=enforcement)
    policy_aware = recommend_release(result, versioning_policy=policy)

    assert policy_aware.bump == baseline.bump
    assert policy_aware.soname == baseline.soname
    assert policy_aware.state == baseline.state
    assert policy_aware.rationale == baseline.rationale


@given(
    promise=_PROMISES,
    enforcement=_ENFORCEMENTS,
)
def test_relaxed_policy_never_rejects_what_no_promise_accepts(
    promise: CompatibilityPromise, enforcement: VersioningEnforcement
) -> None:
    """A ``promise=NONE`` policy always accepts a break -- monotonicity: no
    combination of ``promise``/``enforcement`` is *less* accepting than the
    'no promise' policy for the same observed break."""
    no_promise = VersioningPolicy(
        promise=CompatibilityPromise.NONE, enforcement=enforcement
    )
    other = VersioningPolicy(promise=promise, enforcement=enforcement)
    result = _breaking_result()
    no_promise_acceptance = evaluate_release_acceptance(result, no_promise)
    other_acceptance = evaluate_release_acceptance(result, other)

    assert no_promise_acceptance.accepted is True
    if promise is CompatibilityPromise.NONE:
        assert other_acceptance.accepted is True
    if enforcement is VersioningEnforcement.WARN:
        # WARN never blocks, regardless of promise.
        assert other_acceptance.accepted is True


def _fn(name: str, *, deprecated: str | None = None) -> Function:
    kwargs: dict[str, object] = {}
    if deprecated is not None:
        kwargs["deprecated"] = deprecated
    return Function(name=name, mangled=name, return_type="int", **kwargs)


def _save(tmp_path: Path, version: str, functions: list[Function]) -> str:
    snap = AbiSnapshot(
        library="libmath.so",
        version=version,
        functions=functions,
        from_headers=True,
        ast_producer="castxml",
    )
    out = tmp_path / f"{version}.json"
    save_snapshot(snap, out)
    return str(out)


class TestDeprecationCompliance:
    def test_conforming_deprecation_window(self, tmp_path: Path) -> None:
        add = _fn("add")
        mul = _fn("multiply")
        mul_dep = _fn("multiply", deprecated="use scale() instead")

        p1 = _save(tmp_path, "1.0.0", [add, mul])
        p2 = _save(tmp_path, "1.1.0", [add, mul_dep])
        p3 = _save(tmp_path, "1.2.0", [add])

        policy = VersioningPolicy(deprecation_window=DeprecationWindow(min_releases=1))
        result = run_history_request([p1, p2, p3], versioning_policy=policy)

        findings = {f.display_name: f for f in result.deprecation_compliance}
        assert findings["multiply"].status == "conforming"
        assert findings["multiply"].observed_releases == 1
        # Orthogonal: events/gaps/pairwise are unaffected by the policy.
        assert len(result.events) > 0

    def test_non_conforming_deprecation_window(self, tmp_path: Path) -> None:
        add = _fn("add")
        mul = _fn("multiply")
        mul_dep = _fn("multiply", deprecated="use scale() instead")

        p1 = _save(tmp_path, "1.0.0", [add, mul])
        p2 = _save(tmp_path, "1.1.0", [add, mul_dep])
        p3 = _save(tmp_path, "1.2.0", [add])

        policy = VersioningPolicy(deprecation_window=DeprecationWindow(min_releases=2))
        result = run_history_request([p1, p2, p3], versioning_policy=policy)

        findings = {f.display_name: f for f in result.deprecation_compliance}
        assert findings["multiply"].status == "non_conforming"

    def test_unknown_when_never_deprecated(self, tmp_path: Path) -> None:
        fast = _fn("fast")
        p1 = _save(tmp_path, "2.0.0", [fast])
        p2 = _save(tmp_path, "2.1.0", [])

        policy = VersioningPolicy(deprecation_window=DeprecationWindow(min_releases=1))
        result = run_history_request([p1, p2], versioning_policy=policy)

        findings = {f.display_name: f for f in result.deprecation_compliance}
        assert findings["fast"].status == "unknown"
        assert findings["fast"].deprecated_at_version is None

    def test_conforming_when_no_window_required_and_no_deprecation(
        self, tmp_path: Path
    ) -> None:
        fast = _fn("fast")
        p1 = _save(tmp_path, "2.0.0", [fast])
        p2 = _save(tmp_path, "2.1.0", [])

        result = run_history_request(
            [p1, p2], versioning_policy=built_in_default_versioning_policy()
        )

        findings = {f.display_name: f for f in result.deprecation_compliance}
        assert findings["fast"].status == "conforming"

    def test_no_versioning_policy_means_no_compliance_findings(
        self, tmp_path: Path
    ) -> None:
        add = _fn("add")
        p1 = _save(tmp_path, "1.0.0", [add])
        p2 = _save(tmp_path, "1.1.0", [add])
        result = run_history_request([p1, p2])
        assert result.deprecation_compliance == ()

    def test_reintroduction_leaves_no_stale_deprecation_record(
        self, tmp_path: Path
    ) -> None:
        """A function already deprecated at the history's very first
        snapshot never gets its own ``deprecated`` event (there is no prior
        entry to observe a transition against -- it is only
        ``first_observed``), so its first removal is correctly ``unknown``.
        After reintroduction with no fresh deprecation, the second removal
        must also be ``unknown`` -- not a stale carry-over of the first
        cycle's (nonexistent) deprecation record."""
        keep = _fn("keep")
        flappy = _fn("flappy")
        flappy_dep = _fn("flappy", deprecated="going away")

        p1 = _save(tmp_path, "1.0.0", [keep, flappy_dep])
        p2 = _save(tmp_path, "1.1.0", [keep])  # removed right after deprecation
        p3 = _save(tmp_path, "1.2.0", [keep, flappy])  # reintroduced, not deprecated
        p4 = _save(tmp_path, "1.3.0", [keep])  # removed again, with no new deprecation

        policy = VersioningPolicy(deprecation_window=DeprecationWindow(min_releases=1))
        result = run_history_request([p1, p2, p3, p4], versioning_policy=policy)

        flappy_findings = [
            f for f in result.deprecation_compliance if f.display_name == "flappy"
        ]
        assert len(flappy_findings) == 2
        assert flappy_findings[0].status == "unknown"
        assert flappy_findings[1].status == "unknown"

    def test_deprecated_then_reintroduced_then_removed_is_unknown_again(
        self, tmp_path: Path
    ) -> None:
        """A *real* deprecation-then-removal cycle followed by reintroduction
        and a second removal must not let the first cycle's deprecation
        record satisfy the second, unrelated removal."""
        keep = _fn("keep")
        flappy = _fn("flappy")
        flappy_dep = _fn("flappy", deprecated="going away")

        p1 = _save(tmp_path, "1.0.0", [keep, flappy])
        p2 = _save(tmp_path, "1.1.0", [keep, flappy_dep])  # deprecated
        p3 = _save(tmp_path, "1.2.0", [keep])  # removed: conforming
        p4 = _save(tmp_path, "1.3.0", [keep, flappy])  # reintroduced
        p5 = _save(tmp_path, "1.4.0", [keep])  # removed again, no new deprecation

        policy = VersioningPolicy(deprecation_window=DeprecationWindow(min_releases=1))
        result = run_history_request([p1, p2, p3, p4, p5], versioning_policy=policy)

        flappy_findings = [
            f for f in result.deprecation_compliance if f.display_name == "flappy"
        ]
        assert len(flappy_findings) == 2
        assert flappy_findings[0].status == "conforming"
        assert flappy_findings[1].status == "unknown"

    def test_to_dict_includes_deprecation_compliance(self, tmp_path: Path) -> None:
        add = _fn("add")
        mul = _fn("multiply")
        mul_dep = _fn("multiply", deprecated="use scale() instead")

        p1 = _save(tmp_path, "1.0.0", [add, mul])
        p2 = _save(tmp_path, "1.1.0", [add, mul_dep])
        p3 = _save(tmp_path, "1.2.0", [add])

        policy = VersioningPolicy(deprecation_window=DeprecationWindow(min_releases=1))
        result = run_history_request([p1, p2, p3], versioning_policy=policy)
        d = result.to_dict()
        assert "deprecation_compliance" in d
        assert len(d["deprecation_compliance"]) == 1  # type: ignore[arg-type]
