"""Policy override matrix — systematic ChangeKind × policy combination tests.

Verifies that:
1. Every ChangeKind is classified correctly under each of the 3 policies
2. sdk_vendor downgrades API_BREAK source-level kinds to COMPATIBLE
3. plugin_abi downgrades calling-convention BREAKING kinds to COMPATIBLE
4. PolicyFile overrides move kinds between verdict buckets correctly
5. Unclassified ChangeKinds fail-safe to BREAKING
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    PLUGIN_ABI_DOWNGRADED_KINDS,
    RISK_KINDS,
    SDK_VENDOR_COMPAT_KINDS,
    compute_verdict,
    policy_kind_sets,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    Visibility,
)
from abicheck.policy_file import PolicyFile


@dataclass(frozen=True)
class _FakeChange:
    """Minimal stub satisfying the HasKind protocol for compute_verdict."""

    kind: ChangeKind


# ═══════════════════════════════════════════════════════════════════════════
# Classification Completeness
# ═══════════════════════════════════════════════════════════════════════════


class TestClassificationCompleteness:
    """Every ChangeKind must be classified under every policy."""

    @pytest.mark.parametrize("policy", ["strict_abi", "sdk_vendor", "plugin_abi"])
    def test_all_kinds_classified(self, policy):
        """Every ChangeKind should produce a non-BREAKING verdict for
        compatible kinds and a BREAKING verdict for breaking kinds."""
        breaking, api_break, compatible, risk = policy_kind_sets(policy)
        all_classified = breaking | api_break | compatible | risk
        all_kinds = set(ChangeKind)
        unclassified = all_kinds - all_classified
        assert not unclassified, (
            f"Unclassified ChangeKinds under {policy}: {unclassified}"
        )

    @pytest.mark.parametrize("policy", ["strict_abi", "sdk_vendor", "plugin_abi"])
    def test_no_kind_in_multiple_sets(self, policy):
        """No ChangeKind should be in more than one verdict bucket."""
        breaking, api_break, compatible, risk = policy_kind_sets(policy)
        overlap_ba = breaking & api_break
        overlap_bc = breaking & compatible
        overlap_br = breaking & risk
        overlap_ac = api_break & compatible
        overlap_ar = api_break & risk
        overlap_cr = compatible & risk
        assert not overlap_ba, f"BREAKING ∩ API_BREAK = {overlap_ba}"
        assert not overlap_bc, f"BREAKING ∩ COMPATIBLE = {overlap_bc}"
        assert not overlap_br, f"BREAKING ∩ RISK = {overlap_br}"
        assert not overlap_ac, f"API_BREAK ∩ COMPATIBLE = {overlap_ac}"
        assert not overlap_ar, f"API_BREAK ∩ RISK = {overlap_ar}"
        assert not overlap_cr, f"COMPATIBLE ∩ RISK = {overlap_cr}"


# ═══════════════════════════════════════════════════════════════════════════
# Verdict Computation per Policy
# ═══════════════════════════════════════════════════════════════════════════


class TestVerdictComputation:
    """Verify compute_verdict produces correct verdicts."""

    @pytest.mark.parametrize("policy", ["strict_abi", "sdk_vendor", "plugin_abi"])
    def test_empty_is_no_change(self, policy):
        assert compute_verdict([], policy=policy) == Verdict.NO_CHANGE

    @pytest.mark.parametrize("policy", ["strict_abi", "sdk_vendor", "plugin_abi"])
    def test_single_breaking_kind(self, policy):
        """Any breaking kind (under current policy) should produce BREAKING verdict."""
        breaking, _, _, _ = policy_kind_sets(policy)
        if breaking:
            kind = next(iter(breaking))
            result = compute_verdict([_FakeChange(kind)], policy=policy)
            assert result == Verdict.BREAKING

    @pytest.mark.parametrize("policy", ["strict_abi", "sdk_vendor", "plugin_abi"])
    def test_single_compatible_kind(self, policy):
        compatible_set = policy_kind_sets(policy)[2]
        if compatible_set:
            kind = next(iter(compatible_set))
            result = compute_verdict([_FakeChange(kind)], policy=policy)
            assert result == Verdict.COMPATIBLE

    @pytest.mark.parametrize("policy", ["strict_abi", "sdk_vendor", "plugin_abi"])
    def test_single_risk_kind(self, policy):
        risk_set = policy_kind_sets(policy)[3]
        if risk_set:
            kind = next(iter(risk_set))
            result = compute_verdict([_FakeChange(kind)], policy=policy)
            assert result == Verdict.COMPATIBLE_WITH_RISK

    @pytest.mark.parametrize("policy", ["strict_abi", "sdk_vendor", "plugin_abi"])
    def test_breaking_overrides_compatible(self, policy):
        """BREAKING + COMPATIBLE → BREAKING."""
        breaking, _, compatible, _ = policy_kind_sets(policy)
        if breaking and compatible:
            changes = [
                _FakeChange(next(iter(breaking))),
                _FakeChange(next(iter(compatible))),
            ]
            assert compute_verdict(changes, policy=policy) == Verdict.BREAKING


# ═══════════════════════════════════════════════════════════════════════════
# sdk_vendor Policy Downgrades
# ═══════════════════════════════════════════════════════════════════════════

# Dynamically discover which kinds sdk_vendor downgrades from API_BREAK → COMPATIBLE
_strict_api_break = policy_kind_sets("strict_abi")[1]
_vendor_compatible = policy_kind_sets("sdk_vendor")[2]
SDK_VENDOR_DOWNGRADED = _strict_api_break & _vendor_compatible
# Note: there are deliberately no tests here asserting that every member of
# this set is API_BREAK in strict and COMPATIBLE in sdk_vendor. It is defined
# as the intersection of exactly those two sets, so such a test asserts
# `A & B <= A` -- true for any two sets, including two wrong ones. What is
# worth checking is that the *verdict function* agrees with that membership,
# which `test_sdk_vendor_verdict_for_downgraded_kind` below does, and that the
# set is non-empty, which `test_some_kinds_are_downgraded` does.


class TestSdkVendorPolicy:
    """sdk_vendor downgrades source-level-only API_BREAK kinds."""

    def test_some_kinds_are_downgraded(self):
        """sdk_vendor should downgrade at least some kinds."""
        assert len(SDK_VENDOR_DOWNGRADED) > 0

    @pytest.mark.parametrize(
        "kind", sorted(SDK_VENDOR_DOWNGRADED, key=lambda k: k.value)
    )
    def test_sdk_vendor_verdict_for_downgraded_kind(self, kind):
        """Each downgraded kind produces API_BREAK in strict, COMPATIBLE in sdk_vendor."""
        strict_v = compute_verdict([_FakeChange(kind)], policy="strict_abi")
        vendor_v = compute_verdict([_FakeChange(kind)], policy="sdk_vendor")
        assert strict_v == Verdict.API_BREAK
        assert vendor_v == Verdict.COMPATIBLE


# ═══════════════════════════════════════════════════════════════════════════
# plugin_abi Policy Downgrades
# ═══════════════════════════════════════════════════════════════════════════

# Dynamically discover which kinds plugin_abi downgrades from BREAKING → COMPATIBLE
_strict_breaking = policy_kind_sets("strict_abi")[0]
_plugin_compatible = policy_kind_sets("plugin_abi")[2]
PLUGIN_ABI_DOWNGRADED = _strict_breaking & _plugin_compatible
# As with SDK_VENDOR_DOWNGRADED above: no "members of this intersection are in
# its own operands" tests, which are set-algebra tautologies rather than
# statements about policy.


class TestPluginAbiPolicy:
    """plugin_abi downgrades calling-convention BREAKING kinds."""

    def test_some_kinds_are_downgraded(self):
        """plugin_abi should downgrade at least some kinds."""
        assert len(PLUGIN_ABI_DOWNGRADED) > 0

    @pytest.mark.parametrize(
        "kind", sorted(PLUGIN_ABI_DOWNGRADED, key=lambda k: k.value)
    )
    def test_plugin_abi_verdict_for_downgraded_kind(self, kind):
        strict_v = compute_verdict([_FakeChange(kind)], policy="strict_abi")
        plugin_v = compute_verdict([_FakeChange(kind)], policy="plugin_abi")
        assert strict_v == Verdict.BREAKING
        assert plugin_v == Verdict.COMPATIBLE


# ═══════════════════════════════════════════════════════════════════════════
# Exhaustive ChangeKind × Policy Matrix
# ═══════════════════════════════════════════════════════════════════════════

_ALL_POLICIES = ["strict_abi", "sdk_vendor", "plugin_abi"]


_SEVERITY_ORDER = {
    Verdict.COMPATIBLE: 0,
    Verdict.COMPATIBLE_WITH_RISK: 1,
    Verdict.API_BREAK: 2,
    Verdict.BREAKING: 3,
}


def _expected_verdict(kind: ChangeKind, policy: str) -> Verdict:
    """The verdict each policy is *documented* to produce for a lone change.

    This is the oracle the matrix below is checked against, and it is written
    to be derivable from the documented policy semantics rather than from the
    code under test:

    * ``strict_abi`` is the intrinsic registration -- the bucket the kind's
      own ``ChangeKindMeta.default_verdict`` placed it in.
    * ``sdk_vendor`` differs from strict in exactly one way: the
      source-level-only kinds named by ``SDK_VENDOR_COMPAT_KINDS`` read
      COMPATIBLE.
    * ``plugin_abi`` differs in exactly two: the calling-convention kinds
      named by ``PLUGIN_ABI_DOWNGRADED_KINDS`` read COMPATIBLE, and there is
      no risk band at all -- a deployment-floor increase can stop a plugin
      loading, so it is BREAKING rather than COMPATIBLE_WITH_RISK.

    Deliberately built from the four intrinsic ``*_KINDS`` partitions and the
    two named downgrade sets, *not* from ``policy_kind_sets`` -- which is the
    composition ``compute_verdict`` itself folds over. An oracle that called
    that function would restate the implementation and pass no matter what it
    returned; this one is an independent second derivation, so the two
    disagreeing is a real signal.
    """
    if kind in BREAKING_KINDS:
        strict = Verdict.BREAKING
    elif kind in API_BREAK_KINDS:
        strict = Verdict.API_BREAK
    elif kind in RISK_KINDS:
        strict = Verdict.COMPATIBLE_WITH_RISK
    else:
        strict = Verdict.COMPATIBLE

    if policy == "sdk_vendor":
        if kind in SDK_VENDOR_COMPAT_KINDS:
            return Verdict.COMPATIBLE
        return strict
    if policy == "plugin_abi":
        if kind in PLUGIN_ABI_DOWNGRADED_KINDS:
            return Verdict.COMPATIBLE
        if strict is Verdict.COMPATIBLE_WITH_RISK:
            return Verdict.BREAKING
        return strict
    return strict


class TestExhaustiveMatrix:
    """Every (ChangeKind, policy) pair produces the verdict its policy promises.

    This class previously emitted 1,608 parametrized cases whose only
    assertions were that the result was *one of the four* Verdict members and
    that strict was at least as severe as vendor. Neither pins behaviour: an
    implementation that returned ``COMPATIBLE`` for every input, and one that
    returned ``BREAKING`` for every input, both satisfy all 1,608 (verified by
    substituting each in turn). They also cost far more in fixture setup than
    in assertion.

    They are replaced by batched checks against ``_expected_verdict``, an
    independently-derived oracle, with every disagreeing kind named in the
    failure message rather than one kind per test id.
    """

    @pytest.mark.parametrize("policy", _ALL_POLICIES)
    def test_every_kind_gets_the_verdict_its_policy_promises(self, policy):
        """The whole ChangeKind x policy matrix, against the documented oracle."""
        mismatches = {
            kind.value: (actual, expected)
            for kind in ChangeKind
            for actual, expected in [
                (
                    compute_verdict([_FakeChange(kind)], policy=policy),
                    _expected_verdict(kind, policy),
                )
            ]
            if actual is not expected
        }
        assert not mismatches, (
            f"{len(mismatches)} kind(s) disagree with the documented {policy} "
            f"semantics (kind: got != expected): {mismatches}"
        )

    @pytest.mark.parametrize("policy", _ALL_POLICIES)
    def test_every_kind_produces_a_recognized_verdict(self, policy):
        """A lone change never yields NO_CHANGE or a non-Verdict value.

        Kept as its own (now batched) assertion because it is a different
        claim from the oracle above: it holds even for a kind the oracle has
        no opinion about, e.g. one added to the enum but not yet registered.
        """
        unrecognized = {
            kind.value: compute_verdict([_FakeChange(kind)], policy=policy)
            for kind in ChangeKind
            if compute_verdict([_FakeChange(kind)], policy=policy)
            not in _SEVERITY_ORDER
        }
        assert not unrecognized, f"unrecognized verdicts under {policy}: {unrecognized}"

    def test_strict_verdict_at_least_as_severe_as_vendor(self):
        """sdk_vendor only ever *relaxes* strict_abi -- it may never call a
        kind worse than strict does. The named-policy counterpart of the
        monotonicity rule the tier-accuracy gate states for evidence levels."""
        regressions = {
            kind.value: (strict, vendor)
            for kind in ChangeKind
            for strict, vendor in [
                (
                    compute_verdict([_FakeChange(kind)], policy="strict_abi"),
                    compute_verdict([_FakeChange(kind)], policy="sdk_vendor"),
                )
            ]
            if _SEVERITY_ORDER[strict] < _SEVERITY_ORDER[vendor]
        }
        assert not regressions, (
            "sdk_vendor is more severe than strict_abi for "
            f"{len(regressions)} kind(s) (kind: strict, vendor): {regressions}"
        )

    def test_the_oracle_is_not_vacuous(self):
        """Guard on the oracle itself: it must actually distinguish policies
        and span more than one verdict, or the matrix above would pass while
        asserting nothing. Without this, an oracle accidentally reduced to a
        constant would make every check above vacuously true -- the exact
        failure mode that motivated replacing the old matrix."""
        for policy in _ALL_POLICIES:
            spanned = {_expected_verdict(k, policy) for k in ChangeKind}
            assert len(spanned) > 1, f"oracle is constant under {policy}"
        assert any(
            _expected_verdict(k, "strict_abi") is not _expected_verdict(k, "sdk_vendor")
            for k in ChangeKind
        )
        assert any(
            _expected_verdict(k, "strict_abi") is not _expected_verdict(k, "plugin_abi")
            for k in ChangeKind
        )


# ═══════════════════════════════════════════════════════════════════════════
# PolicyFile Overrides
# ═══════════════════════════════════════════════════════════════════════════


class TestPolicyFileOverrides:
    """PolicyFile can override individual ChangeKind verdicts."""

    def test_downgrade_breaking_to_compatible(self):
        """Override func_removed from BREAKING → COMPATIBLE."""
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        changes = [_FakeChange(ChangeKind.FUNC_REMOVED)]
        assert pf.compute_verdict(changes) == Verdict.COMPATIBLE

    def test_upgrade_compatible_to_breaking(self):
        """Override func_added from COMPATIBLE → BREAKING."""
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_ADDED: Verdict.BREAKING},
        )
        changes = [_FakeChange(ChangeKind.FUNC_ADDED)]
        assert pf.compute_verdict(changes) == Verdict.BREAKING

    def test_mixed_overrides(self):
        """One downgraded + one upgraded, verdict = worst."""
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={
                ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE,
                ChangeKind.FUNC_ADDED: Verdict.BREAKING,
            },
        )
        changes = [
            _FakeChange(ChangeKind.FUNC_REMOVED),
            _FakeChange(ChangeKind.FUNC_ADDED),
        ]
        assert pf.compute_verdict(changes) == Verdict.BREAKING

    def test_override_to_risk(self):
        """Override from BREAKING → COMPATIBLE_WITH_RISK."""
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.TYPE_SIZE_CHANGED: Verdict.COMPATIBLE_WITH_RISK},
        )
        changes = [_FakeChange(ChangeKind.TYPE_SIZE_CHANGED)]
        assert pf.compute_verdict(changes) == Verdict.COMPATIBLE_WITH_RISK

    def test_override_to_api_break(self):
        """Override from COMPATIBLE → API_BREAK."""
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.SONAME_CHANGED: Verdict.API_BREAK},
        )
        changes = [_FakeChange(ChangeKind.SONAME_CHANGED)]
        assert pf.compute_verdict(changes) == Verdict.API_BREAK


# ═══════════════════════════════════════════════════════════════════════════
# PolicyFile with compare()
# ═══════════════════════════════════════════════════════════════════════════


class TestPolicyFileWithCompare:
    """PolicyFile integration with the compare() function."""

    def _pub_func(self, name, mangled, ret="void", params=None, **kwargs):
        return Function(
            name=name,
            mangled=mangled,
            return_type=ret,
            params=params or [],
            visibility=Visibility.PUBLIC,
            **kwargs,
        )

    def _snap(self, functions=None, variables=None, types=None, enums=None):
        return AbiSnapshot(
            library="libtest.so.1",
            version="1.0",
            functions=functions or [],
            variables=variables or [],
            types=types or [],
            enums=enums or [],
        )

    def test_policy_file_downgrades_func_removed(self):
        """func_removed downgraded from BREAKING → COMPATIBLE via policy_file."""
        f = self._pub_func("old", "_Z3oldv")
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        r = compare(self._snap(functions=[f]), self._snap(), policy_file=pf)
        assert r.verdict == Verdict.COMPATIBLE

    def test_policy_file_upgrades_func_added(self):
        """func_added upgraded from COMPATIBLE → BREAKING via policy_file."""
        f = self._pub_func("new", "_Z3newv")
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_ADDED: Verdict.BREAKING},
        )
        r = compare(self._snap(), self._snap(functions=[f]), policy_file=pf)
        assert r.verdict == Verdict.BREAKING

    def test_policy_file_with_sdk_vendor_base(self):
        """PolicyFile with sdk_vendor base: func_removed is still BREAKING,
        and downgraded kinds become COMPATIBLE (proving base_policy is applied)."""
        f_old = self._pub_func("helper", "_ZN3Cls6helperEv")
        pf = PolicyFile(base_policy="sdk_vendor")
        r = compare(self._snap(functions=[f_old]), self._snap(), policy_file=pf)
        assert r.verdict == Verdict.BREAKING
        # Verify base_policy is actually applied: a downgraded kind should
        # produce COMPATIBLE under sdk_vendor (but API_BREAK under strict_abi)
        downgraded_kind = next(iter(SDK_VENDOR_DOWNGRADED))
        assert pf.compute_verdict([_FakeChange(downgraded_kind)]) == Verdict.COMPATIBLE


# ═══════════════════════════════════════════════════════════════════════════
# Unknown / Invalid Policies
# ═══════════════════════════════════════════════════════════════════════════


class TestUnknownPolicy:
    """Unknown policies should fall back to strict_abi."""

    def test_unknown_policy_falls_back(self):
        """Unknown policy name should default to strict_abi behavior."""
        strict_v = compute_verdict(
            [_FakeChange(ChangeKind.FUNC_REMOVED)], policy="strict_abi"
        )
        unknown_v = compute_verdict(
            [_FakeChange(ChangeKind.FUNC_REMOVED)], policy="nonexistent_policy"
        )
        assert strict_v == unknown_v == Verdict.BREAKING
