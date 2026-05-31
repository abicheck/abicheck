# Copyright 2026 Nikolay Petrov
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

"""Tests for the semver / SONAME release recommender (abicheck/semver.py)."""

from __future__ import annotations

import json

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.reporter import to_json, to_markdown
from abicheck.semver import (
    ReleaseRecommendation,
    SemverBump,
    SonameAction,
    recommend_release,
)


def _result(verdict: Verdict, *kinds: ChangeKind) -> DiffResult:
    changes = [
        Change(kind=k, symbol=f"sym_{i}", description=k.value)
        for i, k in enumerate(kinds)
    ]
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so",
        changes=changes,
        verdict=verdict,
    )


# ── Verdict → bump/soname mapping ────────────────────────────────────────────


def test_no_change_recommends_nothing() -> None:
    rec = recommend_release(_result(Verdict.NO_CHANGE))
    assert rec.bump is SemverBump.NONE
    assert rec.soname is SonameAction.NO_BUMP_NEEDED


def test_breaking_recommends_major_and_soname_bump() -> None:
    rec = recommend_release(_result(Verdict.BREAKING, ChangeKind.FUNC_REMOVED))
    assert rec.bump is SemverBump.MAJOR
    assert rec.soname is SonameAction.BUMP_REQUIRED
    assert "MAJOR" in rec.rationale


def test_breaking_with_soname_bump_recommended_flags_missing_bump() -> None:
    rec = recommend_release(
        _result(
            Verdict.BREAKING,
            ChangeKind.FUNC_REMOVED,
            ChangeKind.SONAME_BUMP_RECOMMENDED,
        )
    )
    assert rec.bump is SemverBump.MAJOR
    assert rec.soname is SonameAction.BUMP_MISSING


def test_breaking_with_soname_already_changed_is_performed() -> None:
    rec = recommend_release(
        _result(Verdict.BREAKING, ChangeKind.FUNC_REMOVED, ChangeKind.SONAME_CHANGED)
    )
    assert rec.bump is SemverBump.MAJOR
    assert rec.soname is SonameAction.BUMP_PERFORMED


def test_api_break_recommends_major_without_soname_bump() -> None:
    rec = recommend_release(_result(Verdict.API_BREAK, ChangeKind.ENUM_MEMBER_RENAMED))
    assert rec.bump is SemverBump.MAJOR
    # Source-only break keeps the binary loadable → no SONAME change required.
    assert rec.soname is SonameAction.NO_BUMP_NEEDED


def test_risk_without_additions_recommends_patch() -> None:
    rec = recommend_release(
        _result(Verdict.COMPATIBLE_WITH_RISK, ChangeKind.CPU_DISPATCH_ISA_DROPPED)
    )
    assert rec.bump is SemverBump.PATCH
    assert rec.soname is SonameAction.NO_BUMP_NEEDED


def test_risk_with_additions_recommends_minor() -> None:
    rec = recommend_release(
        _result(
            Verdict.COMPATIBLE_WITH_RISK,
            ChangeKind.CPU_DISPATCH_ISA_DROPPED,
            ChangeKind.FUNC_ADDED,
        )
    )
    assert rec.bump is SemverBump.MINOR


def test_compatible_addition_recommends_minor() -> None:
    rec = recommend_release(_result(Verdict.COMPATIBLE, ChangeKind.FUNC_ADDED))
    assert rec.bump is SemverBump.MINOR
    assert rec.soname is SonameAction.NO_BUMP_NEEDED


def test_compatible_quality_only_recommends_patch() -> None:
    rec = recommend_release(_result(Verdict.COMPATIBLE, ChangeKind.SONAME_MISSING))
    assert rec.bump is SemverBump.PATCH


# ── Serialization / headline ─────────────────────────────────────────────────


def test_to_dict_keys() -> None:
    rec = ReleaseRecommendation(SemverBump.MAJOR, SonameAction.BUMP_REQUIRED, "because")
    d = rec.to_dict()
    assert d == {
        "version_bump": "major",
        "soname_action": "bump_required",
        "rationale": "because",
    }


def test_headline_mentions_soname_only_when_relevant() -> None:
    major_break = ReleaseRecommendation(
        SemverBump.MAJOR, SonameAction.BUMP_REQUIRED, ""
    )
    assert "SONAME" in major_break.headline()
    minor = ReleaseRecommendation(SemverBump.MINOR, SonameAction.NO_BUMP_NEEDED, "")
    assert "SONAME" not in minor.headline()


@pytest.mark.parametrize(
    "verdict",
    [
        Verdict.NO_CHANGE,
        Verdict.COMPATIBLE,
        Verdict.COMPATIBLE_WITH_RISK,
        Verdict.API_BREAK,
        Verdict.BREAKING,
    ],
)
def test_every_verdict_yields_a_recommendation(verdict: Verdict) -> None:
    rec = recommend_release(_result(verdict, ChangeKind.FUNC_ADDED))
    assert isinstance(rec, ReleaseRecommendation)
    assert rec.rationale  # never empty


# ── Reporter integration ─────────────────────────────────────────────────────


def _fn(name: str) -> Function:
    return Function(
        name=name, mangled=name, return_type="int", visibility=Visibility.PUBLIC
    )


def test_json_output_always_includes_recommendation() -> None:
    old = AbiSnapshot(
        library="libfoo.so", version="1.0", functions=[_fn("a"), _fn("b")]
    )
    new = AbiSnapshot(library="libfoo.so", version="2.0", functions=[_fn("a")])
    result = compare(old, new)
    payload = json.loads(to_json(result))
    assert "release_recommendation" in payload
    rec = payload["release_recommendation"]
    assert rec["version_bump"] == "major"  # b was removed → breaking
    assert rec["soname_action"] in {
        "bump_required",
        "bump_missing",
        "bump_performed",
    }


def test_markdown_recommendation_is_opt_in() -> None:
    old = AbiSnapshot(library="libfoo.so", version="1.0", functions=[_fn("a")])
    new = AbiSnapshot(
        library="libfoo.so", version="2.0", functions=[_fn("a"), _fn("c")]
    )
    result = compare(old, new)
    assert "Release Recommendation" not in to_markdown(result)
    md = to_markdown(result, show_recommendation=True)
    assert "Release Recommendation" in md
    # Well-formed table: header + delimiter precede the data rows (regression
    # guard — the delimiter must not sit between data rows).
    assert "| Field | Value |\n|---|---|\n| Version bump |" in md


def test_leaf_json_also_includes_recommendation() -> None:
    """report_mode='leaf' must still expose release_recommendation (it has an
    early return that previously bypassed the field)."""
    old = AbiSnapshot(
        library="libfoo.so", version="1.0", functions=[_fn("a"), _fn("b")]
    )
    new = AbiSnapshot(library="libfoo.so", version="2.0", functions=[_fn("a")])
    result = compare(old, new)
    payload = json.loads(to_json(result, report_mode="leaf"))
    assert payload["release_recommendation"]["version_bump"] == "major"


def test_leaf_markdown_honors_recommendation_flag() -> None:
    old = AbiSnapshot(library="libfoo.so", version="1.0", functions=[_fn("a")])
    new = AbiSnapshot(
        library="libfoo.so", version="2.0", functions=[_fn("a"), _fn("c")]
    )
    result = compare(old, new)
    assert "Release Recommendation" not in to_markdown(result, report_mode="leaf")
    assert "Release Recommendation" in to_markdown(
        result, report_mode="leaf", show_recommendation=True
    )
