"""Policy × ChangeKind matrix test — verify every kind is classified correctly under each policy.

This test ensures that:
1. Every ChangeKind appears in exactly one primary classification set per policy.
2. Policy downgrade/upgrade logic is consistent.
3. No kind is accidentally unclassified (would silently default to BREAKING).
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    ChangeKind,
    Confidence,
    Verdict,
    compute_verdict,
    policy_kind_sets,
)

# All known policies.
ALL_POLICIES = ("strict_abi", "sdk_vendor", "plugin_abi")


class TestChangeKindCompleteness:
    """Every ChangeKind must be in exactly one primary set."""

    def test_all_kinds_classified(self):
        """Every ChangeKind must appear in at least one of the four sets."""
        all_classified = (
            frozenset(BREAKING_KINDS)
            | frozenset(COMPATIBLE_KINDS)
            | frozenset(API_BREAK_KINDS)
            | RISK_KINDS
        )
        unclassified = set(ChangeKind) - all_classified
        assert not unclassified, (
            f"Unclassified ChangeKinds (default to BREAKING silently): {unclassified}"
        )

    def test_no_kind_in_multiple_primary_sets(self):
        """No kind should appear in more than one primary classification set."""
        b = frozenset(BREAKING_KINDS)
        c = frozenset(COMPATIBLE_KINDS)
        a = frozenset(API_BREAK_KINDS)
        r = RISK_KINDS

        assert not (b & c), f"BREAKING ∩ COMPATIBLE: {b & c}"
        assert not (b & a), f"BREAKING ∩ API_BREAK: {b & a}"
        assert not (b & r), f"BREAKING ∩ RISK: {b & r}"
        assert not (c & a), f"COMPATIBLE ∩ API_BREAK: {c & a}"
        assert not (c & r), f"COMPATIBLE ∩ RISK: {c & r}"
        assert not (a & r), f"API_BREAK ∩ RISK: {a & r}"


class TestPolicyKindSetsConsistency:
    """For each policy, the four returned sets must be complete and disjoint."""

    @pytest.mark.parametrize("policy", ALL_POLICIES)
    def test_policy_sets_cover_all_kinds(self, policy: str):
        breaking, api_break, compatible, risk = policy_kind_sets(policy)
        all_covered = breaking | api_break | compatible | risk
        uncovered = set(ChangeKind) - all_covered
        assert not uncovered, f"Policy '{policy}' leaves kinds uncovered: {uncovered}"

    @pytest.mark.parametrize("policy", ALL_POLICIES)
    def test_policy_sets_disjoint(self, policy: str):
        breaking, api_break, compatible, risk = policy_kind_sets(policy)
        assert not (breaking & compatible), f"{policy}: BREAKING ∩ COMPATIBLE"
        assert not (breaking & api_break), f"{policy}: BREAKING ∩ API_BREAK"
        assert not (breaking & risk), f"{policy}: BREAKING ∩ RISK"
        assert not (compatible & api_break), f"{policy}: COMPATIBLE ∩ API_BREAK"
        assert not (compatible & risk), f"{policy}: COMPATIBLE ∩ RISK"
        assert not (api_break & risk), f"{policy}: API_BREAK ∩ RISK"


class TestVerdictComputationMatrix:
    """Verify that each kind produces the expected verdict under each policy."""

    @pytest.mark.parametrize("policy", ALL_POLICIES)
    def test_each_kind_produces_expected_verdict(self, policy: str):
        """For every ChangeKind, compute_verdict with a single change must
        produce a verdict consistent with policy_kind_sets classification."""
        breaking, api_break, compatible, risk = policy_kind_sets(policy)

        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _FakeChange:
            kind: ChangeKind

        for kind in ChangeKind:
            changes = [_FakeChange(kind=kind)]
            verdict = compute_verdict(changes, policy=policy)

            if kind in breaking:
                assert verdict == Verdict.BREAKING, (
                    f"{policy}/{kind}: expected BREAKING, got {verdict}"
                )
            elif kind in api_break:
                assert verdict == Verdict.API_BREAK, (
                    f"{policy}/{kind}: expected API_BREAK, got {verdict}"
                )
            elif kind in risk:
                assert verdict == Verdict.COMPATIBLE_WITH_RISK, (
                    f"{policy}/{kind}: expected COMPATIBLE_WITH_RISK, got {verdict}"
                )
            elif kind in compatible:
                assert verdict == Verdict.COMPATIBLE, (
                    f"{policy}/{kind}: expected COMPATIBLE, got {verdict}"
                )
            else:
                # Unclassified — should default to BREAKING (fail-safe)
                assert verdict == Verdict.BREAKING, (
                    f"{policy}/{kind}: unclassified kind should default to BREAKING, got {verdict}"
                )

    def test_empty_changes_no_change(self):
        """Empty change list must always produce NO_CHANGE."""
        for policy in ALL_POLICIES:
            assert compute_verdict([], policy=policy) == Verdict.NO_CHANGE

    def test_mixed_breaking_and_compatible(self):
        """If any BREAKING kind is present, verdict must be BREAKING."""
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _FakeChange:
            kind: ChangeKind

        changes = [
            _FakeChange(kind=ChangeKind.FUNC_ADDED),  # COMPATIBLE
            _FakeChange(kind=ChangeKind.FUNC_REMOVED),  # BREAKING
        ]
        for policy in ALL_POLICIES:
            assert compute_verdict(changes, policy=policy) == Verdict.BREAKING

    def test_unknown_policy_falls_back_to_strict(self):
        """Unknown policy names should fall back to strict_abi."""
        strict_sets = policy_kind_sets("strict_abi")
        unknown_sets = policy_kind_sets("nonexistent_policy")
        assert strict_sets == unknown_sets


class TestConfidenceComputation:
    """Test the new confidence/evidence tier computation."""

    def test_compare_returns_confidence_field(self):
        from abicheck.checker import compare
        from abicheck.model import AbiSnapshot

        snap = AbiSnapshot(
            library="libtest.so",
            version="1.0",
            functions=[],
            variables=[],
            types=[],
            enums=[],
            typedefs={},
        )
        result = compare(snap, snap)
        assert hasattr(result, "confidence")
        assert result.confidence in (Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW)
        assert isinstance(result.evidence_tiers, list)
        assert isinstance(result.coverage_warnings, list)

    def test_empty_snapshots_low_confidence(self):
        from abicheck.checker import compare
        from abicheck.model import AbiSnapshot

        snap = AbiSnapshot(
            library="libtest.so",
            version="1.0",
            functions=[],
            variables=[],
            types=[],
            enums=[],
            typedefs={},
        )
        result = compare(snap, snap)
        # No data at all → low confidence
        assert result.confidence == Confidence.LOW
