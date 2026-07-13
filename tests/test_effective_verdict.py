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

"""Tests for the per-finding effective-category override (ADR-025 A4/D4.1)."""

from __future__ import annotations

from abicheck.checker_policy import (
    ChangeKind,
    EvidenceStatus,
    Verdict,
    compute_verdict,
    effective_category,
    evidence_status_for_change,
    policy_kind_sets,
)
from abicheck.checker_types import Change, DiffResult


def _change(kind: ChangeKind, **kw: object) -> Change:
    return Change(kind=kind, symbol="s", description="d", **kw)


def test_no_override_is_noop() -> None:
    # Without an override, classification is purely kind-based (today's behaviour).
    sets = policy_kind_sets("strict_abi")
    c = _change(ChangeKind.TYPE_SIZE_CHANGED)
    assert effective_category(c, *sets) == Verdict.BREAKING
    assert compute_verdict([c]) == Verdict.BREAKING


def test_override_demotes_category() -> None:
    sets = policy_kind_sets("strict_abi")
    c = _change(ChangeKind.TYPE_SIZE_CHANGED, effective_verdict=Verdict.COMPATIBLE)
    # A breaking kind whose finding is demoted reads compatible everywhere.
    assert effective_category(c, *sets) == Verdict.COMPATIBLE
    assert compute_verdict([c]) == Verdict.COMPATIBLE


def test_override_only_affects_its_own_finding() -> None:
    demoted = _change(
        ChangeKind.TYPE_SIZE_CHANGED, effective_verdict=Verdict.COMPATIBLE
    )
    sibling = _change(ChangeKind.TYPE_SIZE_CHANGED)  # same kind, no override
    # The sibling stays breaking; the worst category wins overall.
    assert compute_verdict([demoted, sibling]) == Verdict.BREAKING


def test_override_can_raise_category() -> None:
    c = _change(
        ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE, effective_verdict=Verdict.BREAKING
    )
    assert compute_verdict([c]) == Verdict.BREAKING


def test_diffresult_properties_honor_override() -> None:
    demoted = _change(
        ChangeKind.TYPE_SIZE_CHANGED, effective_verdict=Verdict.COMPATIBLE
    )
    breaking_kept = _change(ChangeKind.FUNC_REMOVED)
    dr = DiffResult(
        old_version="1", new_version="2", library="l", changes=[demoted, breaking_kept]
    )
    # The demoted finding moves out of `breaking` into `compatible`; the real
    # break stays in `breaking`.
    assert demoted not in dr.breaking
    assert demoted in dr.compatible
    assert breaking_kept in dr.breaking


def test_compute_verdict_empty_is_no_change() -> None:
    assert compute_verdict([]) == Verdict.NO_CHANGE


# ---------------------------------------------------------------------------
# evidence_status_for_change (the epistemic-status label, derived from verdict)
# ---------------------------------------------------------------------------


def test_evidence_status_breaking_is_artifact_proven() -> None:
    c = _change(ChangeKind.FUNC_REMOVED)
    assert (
        evidence_status_for_change(c, Verdict.BREAKING)
        is EvidenceStatus.ARTIFACT_PROVEN
    )


def test_evidence_status_api_break_is_source_contract() -> None:
    c = _change(ChangeKind.FIELD_RENAMED)
    assert (
        evidence_status_for_change(c, Verdict.API_BREAK)
        is EvidenceStatus.SOURCE_CONTRACT
    )


def test_evidence_status_risk_is_contextual_risk() -> None:
    c = _change(ChangeKind.TYPE_SIZE_CHANGED)
    assert (
        evidence_status_for_change(c, Verdict.COMPATIBLE_WITH_RISK)
        is EvidenceStatus.CONTEXTUAL_RISK
    )


def test_evidence_status_none_for_compatible_and_no_change() -> None:
    c = _change(ChangeKind.FUNC_ADDED)
    assert evidence_status_for_change(c, Verdict.COMPATIBLE) is None
    assert evidence_status_for_change(c, Verdict.NO_CHANGE) is None


def test_evidence_status_missing_evidence_kind_is_not_checkable_regardless_of_verdict() -> (
    None
):
    c = _change(ChangeKind.EVIDENCE_REQUIRED_MISSING)
    # A missing-evidence finding always reads not_checkable, whatever verdict
    # its category resolves to (it is the "we don't know" signal, not a break).
    assert (
        evidence_status_for_change(c, Verdict.API_BREAK) is EvidenceStatus.NOT_CHECKABLE
    )
    assert (
        evidence_status_for_change(c, Verdict.BREAKING) is EvidenceStatus.NOT_CHECKABLE
    )


def test_evidence_status_follows_effective_verdict_override() -> None:
    # A demoted finding's evidence_status follows its *resolved* verdict, not
    # its kind's default — consistent with effective_category by construction.
    demoted = _change(
        ChangeKind.TYPE_SIZE_CHANGED, effective_verdict=Verdict.COMPATIBLE
    )
    sets = policy_kind_sets("strict_abi")
    resolved = effective_category(demoted, *sets)
    assert evidence_status_for_change(demoted, resolved) is None
