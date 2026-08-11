"""Suppression edge case tests.

Tests:
1. Conflicting rules (multiple rules matching same change)
2. Suppression + policy interaction (suppressed BREAKING → verdict changes)
3. Expiration edge cases (exact expiry date, past/future)
4. Pattern matching edge cases (regex, fnmatch)
5. Type pattern vs symbol pattern distinction
6. Empty/degenerate suppression lists
7. Audit trail integrity
"""
from __future__ import annotations

from datetime import date, timedelta

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_types import Change
from abicheck.model import (
    AbiSnapshot,
    Function,
    Visibility,
)
from abicheck.suppression import Suppression, SuppressionList


def _snap(version="1.0", functions=None, variables=None, types=None,
          enums=None, typedefs=None):
    return AbiSnapshot(
        library="libtest.so.1", version=version,
        functions=functions or [], variables=variables or [],
        types=types or [], enums=enums or [],
        typedefs=typedefs or {},
    )


def _pub_func(name, mangled, ret="void", **kwargs):
    return Function(name=name, mangled=mangled, return_type=ret,
                    visibility=Visibility.PUBLIC, **kwargs)


def _kinds(result):
    return {c.kind for c in result.changes}


# ═══════════════════════════════════════════════════════════════════════════
# Suppression + Verdict Interaction
# ═══════════════════════════════════════════════════════════════════════════

class TestSuppressionVerdictInteraction:
    """Suppressing changes affects the overall verdict."""

    def test_suppressing_only_breaking_change_clears_verdict(self):
        """If the only change is suppressed, verdict should be NO_CHANGE."""
        f = _pub_func("old", "_Z3oldv")
        sl = SuppressionList([
            Suppression(symbol="_Z3oldv", change_kind="func_removed",
                        reason="intentional removal"),
        ])
        r = compare(_snap(functions=[f]), _snap(), suppression=sl)
        assert r.verdict == Verdict.NO_CHANGE
        assert r.suppressed_count == 1

    def test_suppressing_one_of_two_changes(self):
        """Suppress one change, the other still drives verdict."""
        f1 = _pub_func("old1", "_Z4old1v")
        f2 = _pub_func("old2", "_Z4old2v")
        sl = SuppressionList([
            Suppression(symbol="_Z4old1v", change_kind="func_removed",
                        reason="intentional"),
        ])
        r = compare(_snap(functions=[f1, f2]), _snap(), suppression=sl)
        assert r.verdict == Verdict.BREAKING  # f2 removal still breaking
        assert r.suppressed_count == 1
        # Unsuppressed changes should still contain f2
        assert any(c.symbol == "_Z4old2v" for c in r.changes)

    def test_suppressed_change_in_audit_trail(self):
        """Suppressed changes should appear in suppressed_changes list."""
        f = _pub_func("gone", "_Z4gonev")
        sl = SuppressionList([
            Suppression(symbol="_Z4gonev", reason="expected removal"),
        ])
        r = compare(_snap(functions=[f]), _snap(), suppression=sl)
        assert len(r.suppressed_changes) > 0
        assert any(c.symbol == "_Z4gonev" for c in r.suppressed_changes)

    def test_suppression_flag_without_matches(self):
        """Providing a suppression list with 0 matches still sets the flag."""
        f = _pub_func("api", "_Z3apiv")
        sl = SuppressionList([
            Suppression(symbol="_Z999nomatchv", reason="doesn't match anything"),
        ])
        r = compare(_snap(functions=[f]), _snap(functions=[f]), suppression=sl)
        assert r.suppression_file_provided is True
        assert r.suppressed_count == 0


# ═══════════════════════════════════════════════════════════════════════════
# Expiration Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestSuppressionExpiration:
    """Suppression rule expiration behavior."""

    def test_expired_rule_does_not_match(self):
        """Past expiry date → rule is inactive."""
        yesterday = date.today() - timedelta(days=1)
        s = Suppression(
            symbol="_Z3foov", reason="temporary",
            expires=yesterday,
        )
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="func removed",
        )
        assert not s.matches(change)

    def test_future_expiry_still_active(self):
        """Future expiry date → rule is active."""
        tomorrow = date.today() + timedelta(days=1)
        s = Suppression(
            symbol="_Z3foov", reason="temporary",
            expires=tomorrow,
        )
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="func removed",
        )
        assert s.matches(change)

    def test_today_expiry_still_active(self):
        """Same-day expiry → rule is still active (expires at end of day)."""
        today = date.today()
        s = Suppression(
            symbol="_Z3foov", reason="temporary",
            expires=today,
        )
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="func removed",
        )
        assert s.matches(change, today=today)

    def test_expired_suppression_does_not_affect_verdict(self):
        """Expired suppression → change remains unsuppressed."""
        yesterday = date.today() - timedelta(days=1)
        f = _pub_func("old", "_Z3oldv")
        sl = SuppressionList([
            Suppression(symbol="_Z3oldv", reason="was temporary",
                        expires=yesterday),
        ])
        r = compare(_snap(functions=[f]), _snap(), suppression=sl)
        assert r.verdict == Verdict.BREAKING
        assert r.suppressed_count == 0


# ═══════════════════════════════════════════════════════════════════════════
# Pattern Matching Edge Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestSuppressionPatternMatching:
    """Pattern matching edge cases."""

    def test_symbol_pattern_regex_match(self):
        """Regex pattern matches symbol name."""
        s = Suppression(
            symbol_pattern=r"_Z\d+internal.*",
            reason="internal namespace",
        )
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z8internalFoov",
            description="removed",
        )
        assert s.matches(change)

    def test_symbol_pattern_no_partial_match(self):
        """Regex uses fullmatch, not search — partial match fails."""
        s = Suppression(
            symbol_pattern=r"internal",
            reason="internal",
        )
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z8internalFoov",
            description="removed",
        )
        assert not s.matches(change)

    def test_exact_symbol_match(self):
        """Exact symbol name match — no regex needed."""
        s = Suppression(symbol="_Z3foov", reason="exact")
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
        )
        assert s.matches(change)

    def test_exact_symbol_no_partial(self):
        """Exact match does not partial-match."""
        s = Suppression(symbol="_Z3foov", reason="exact")
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3fooBarv",
            description="removed",
        )
        assert not s.matches(change)

    def test_change_kind_filter(self):
        """Suppression with change_kind only matches that specific kind."""
        s = Suppression(
            symbol="_Z3foov",
            change_kind="func_removed",
            reason="only suppress removals",
        )
        removal = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
        )
        return_change = Change(
            kind=ChangeKind.FUNC_RETURN_CHANGED,
            symbol="_Z3foov",
            description="return changed",
        )
        assert s.matches(removal)
        assert not s.matches(return_change)


# ═══════════════════════════════════════════════════════════════════════════
# Namespace glob (``**``) semantics
# ═══════════════════════════════════════════════════════════════════════════

class TestNamespaceGlobstarSemantics:
    """Reported bug: a plain ``fnmatch.translate`` compiles ``**`` as an
    ordinary ``.*`` glued to whatever literal text follows it, so two
    starred regions are still separated by a *mandatory* literal ``::`` —
    ``oneapi::dal::**::detail::**`` could never match the zero-segment case
    ``oneapi::dal::detail::...`` even though "zero or more namespace
    segments" is exactly what ``**`` is documented to mean. Fixed with
    pathspec/gitignore-style globstar semantics in
    ``_translate_namespace_glob``.
    """

    def _change(self, symbol: str) -> Change:
        return Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=symbol,
            description="removed",
        )

    def test_middle_globstar_matches_zero_segments(self):
        s = Suppression(
            namespace="oneapi::dal::**::detail::**",
            reachability="any",
            reason="internal detail churn",
        )
        assert s.matches(self._change("oneapi::dal::detail::foo"))

    def test_middle_globstar_matches_one_segment(self):
        s = Suppression(
            namespace="oneapi::dal::**::detail::**",
            reachability="any",
            reason="internal detail churn",
        )
        assert s.matches(self._change("oneapi::dal::backend::detail::foo"))

    def test_middle_globstar_matches_nested_trailing_segments(self):
        s = Suppression(
            namespace="oneapi::dal::**::detail::**",
            reachability="any",
            reason="internal detail churn",
        )
        assert s.matches(self._change("oneapi::dal::detail::foo::bar"))

    def test_middle_globstar_rejects_unrelated_namespace(self):
        s = Suppression(
            namespace="oneapi::dal::**::detail::**",
            reachability="any",
            reason="internal detail churn",
        )
        assert not s.matches(self._change("oneapi::dal::public_ns::baz"))

    def test_middle_globstar_rejects_lookalike_segment(self):
        # "detailish" must not be treated as containing "detail".
        s = Suppression(
            namespace="oneapi::dal::**::detail::**",
            reachability="any",
            reason="internal detail churn",
        )
        assert not s.matches(self._change("oneapi::dal::detailish::baz"))

    def test_leading_and_trailing_globstar_matches_bare_segment(self):
        s = Suppression(
            namespace="**::backend::**",
            reachability="any",
            reason="backend churn",
        )
        assert s.matches(self._change("backend"))
        assert s.matches(self._change("oneapi::backend::foo"))

    def test_leading_and_trailing_globstar_rejects_lookalike(self):
        s = Suppression(
            namespace="**::backend::**",
            reachability="any",
            reason="backend churn",
        )
        assert not s.matches(self._change("backendish::x"))
        assert not s.matches(self._change("x::backendish"))

    def test_standalone_globstar_matches_any_namespace(self):
        # Codex review: the leading/trailing two-way branch above produced
        # "(?:.*::)?" for a standalone "**", which matches only "" or text
        # ending in "::" — so an ordinary name like "oneapi::dal::foo"
        # never matched at all, silently disabling an otherwise-valid
        # catch-all suppression rule.
        s = Suppression(namespace="**", reachability="any", reason="catch-all")
        assert s.matches(self._change("oneapi::dal::foo"))
        assert s.matches(self._change("plain"))

    def test_collapsed_standalone_globstar_matches_any_namespace(self):
        # "**::**" collapses to "**" before translation — must hit the same
        # standalone-catch-all fix.
        s = Suppression(namespace="**::**", reachability="any", reason="catch-all")
        assert s.matches(self._change("oneapi::dal::foo"))

    def test_collapse_does_not_span_into_a_non_globstar_segment(self):
        # Codex review: the original collapse ran on the raw string via
        # re.sub(r"(?:\*\*::)+\*\*", "**", pattern) — a substring match, so
        # "foo**::**" (segments ["foo**", "**"], nothing to collapse) got
        # corrupted to "foo**" because the literal substring "**::**" starts
        # mid-way through "foo**" and spans into the next segment, silently
        # dropping the "::" boundary the fnmatch-style prefix segment still
        # required. The collapse must only fire on segments that are
        # exactly "**", found by first splitting on "::".
        s = Suppression(namespace="foo**::**", reachability="any", reason="x")
        # Whatever "foo**" matches on its own (plain fnmatch semantics,
        # unaffected by this fix), the trailing "::**" must still be a
        # genuinely separate, non-corrupting segment — verified behaviorally
        # rather than against a specific regex substring, since a later fix
        # (Codex review, fresh evidence) changed *how* this exact adjacency
        # is translated (delegated to fnmatch.translate directly, which
        # already handles a wildcard segment bordering "**" correctly) while
        # keeping the same observable behavior this test checks: the "::"
        # boundary is still required (never collapsed away), so a bare
        # "foobar" with no "::" anywhere must NOT match even though "foo**"
        # alone would ordinarily match it.
        assert s.matches(self._change("foo::bar"))
        assert not s.matches(self._change("foobar"))

    def test_wildcard_segment_bordering_globstar_still_requires_separator(self):
        # Codex review, fresh evidence: the segment-collapse fix above did
        # not fully close this bug. "foo**::**" translated to
        # "foo.*(?:::.*)?" -- the whole trailing "::" absorption is
        # *optional*, and since "foo**"'s own regex ("foo.*") is itself an
        # unconstrained greedy wildcard, it can swallow an unrelated suffix
        # like "bar" directly, letting the optional group match nothing at
        # all. The result: Suppression(namespace="foo**::**") matched bare
        # "foobar" even though no "::" appears anywhere in it -- the
        # original (pre-globstar-rewrite) fnmatch-style pattern required
        # that literal "::" to be present (verified against real
        # fnmatch.translate("foo**::**"), which already handles this exact
        # adjacency correctly). A real-world catch-all rule like
        # "namespace: 'oneapi::detail**::**'" would then also over-suppress
        # an unrelated "oneapi::detail_other::..." finding.
        s = Suppression(namespace="foo**::**", reachability="any", reason="x")
        assert not s.matches(self._change("foobar"))
        assert s.matches(self._change("foobar::baz"))
        assert s.matches(self._change("foo::bar"))
        assert s.matches(self._change("foo::bar::baz"))

    def test_leading_globstar_beside_wildcard_segment_keeps_zero_segment_match(self):
        # Codex review, fresh evidence: an early attempt at the previous
        # fix's "delegate to fnmatch.translate" approach applied it to
        # *every* wildcard-adjacent globstar, including a leading one
        # ("**::detail*") -- but native fnmatch.translate applies the same
        # atomic-group optimization there too, which drops the documented
        # "zero or more segments" match entirely (bare "detail_private" no
        # longer matched "**::detail*" at all). Unlike the trailing case
        # this module does delegate, a leading globstar's own regex is
        # evaluated *before* the wildcarded segment and already carries its
        # own correct constraint, so the hand-rolled form needs no
        # delegation here -- verified it still rejects an unrelated name.
        s = Suppression(namespace="**::detail*", reachability="any", reason="x")
        assert s.matches(self._change("detail_private"))
        assert s.matches(self._change("a::detail_private"))
        assert s.matches(self._change("a::b::detail_private"))
        assert not s.matches(self._change("notdetail"))

    def test_middle_globstar_beside_wildcard_segment_keeps_zero_segment_match(self):
        # Same principle as the leading-globstar case above, for a globstar
        # in the middle of the pattern with a wildcarded neighbor: the
        # following segment's own mandatory "::" joiner already anchors the
        # match regardless of what precedes the globstar, so no delegation
        # is needed (or wanted -- native fnmatch's atomic-group translation
        # of this same shape incorrectly rejects the legitimate
        # zero-segment match here too).
        s = Suppression(namespace="a::**::detail*::x", reachability="any", reason="x")
        assert s.matches(self._change("a::detail_private::x"))
        assert s.matches(self._change("a::y::detail_private::x"))

    def test_python314_fnmatch_end_anchor_variant_is_accepted(self):
        # Codex review, verified against Python 3.14.4: fnmatch.translate()
        # ends its result with "\z" there instead of "\Z". The wrapper that
        # strips fnmatch's own "(?s: ... )\\Z" packaging around one segment
        # must accept either spelling, or a multi-segment pattern like
        # "ns::*" silently degrades to matching nothing at all (each
        # segment stays wrapped and self-anchored instead of contributing a
        # fragment). Exercised directly against the wrapper/segment helper
        # rather than depending on which anchor the *running* interpreter's
        # fnmatch happens to emit.
        from abicheck.suppression import _SEGMENT_RE_WRAPPER, _fnmatch_segment_regex

        assert _SEGMENT_RE_WRAPPER.match("(?s:ns)\\Z") is not None
        assert _SEGMENT_RE_WRAPPER.match("(?s:ns)\\z") is not None
        # Simulate the 3.14 anchor spelling directly, independent of which
        # one the local interpreter actually produces.
        m = _SEGMENT_RE_WRAPPER.match("(?s:ns)\\z")
        assert m is not None
        assert m.group(1) == "ns"
        # And end to end: a multi-segment pattern must still match a real
        # name regardless of which anchor spelling fnmatch used internally.
        s = Suppression(namespace="ns::*", reachability="any", reason="x")
        assert s.matches(self._change("ns::foo"))
        assert _fnmatch_segment_regex("ns") == "ns"


# ═══════════════════════════════════════════════════════════════════════════
# Type Pattern Matching
# ═══════════════════════════════════════════════════════════════════════════

class TestTypePatternSuppression:
    """Type pattern matching for type-level changes."""

    def test_type_pattern_matches_type_change(self):
        """type_pattern matches changes on a type name."""
        s = Suppression(
            type_pattern=r"Config",
            reason="config struct is internal",
        )
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Config",
            description="size changed",
        )
        assert s.matches(change)

    def test_type_pattern_does_not_match_function(self):
        """type_pattern should NOT match function-level changes."""
        s = Suppression(
            type_pattern=r"Config",
            reason="type only",
        )
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="Config",
            description="func removed",
        )
        assert not s.matches(change)

    def test_type_pattern_regex(self):
        """Regex type pattern."""
        s = Suppression(
            type_pattern=r"Internal.*",
            reason="internal types",
        )
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="InternalConfig",
            description="size changed",
        )
        assert s.matches(change)


# ═══════════════════════════════════════════════════════════════════════════
# Multiple Rules — Priority & Conflicts
# ═══════════════════════════════════════════════════════════════════════════

class TestMultipleRules:
    """When multiple rules could match, any match suppresses."""

    def test_multiple_rules_same_symbol(self):
        """Multiple rules matching the same symbol — any match suppresses."""
        sl = SuppressionList([
            Suppression(symbol="_Z3foov", reason="rule 1"),
            Suppression(symbol_pattern=r"_Z3.*", reason="rule 2"),
        ])
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
        )
        assert sl.is_suppressed(change)

    def test_overlapping_patterns(self):
        """Both exact and pattern rules cover the same change."""
        f = _pub_func("foo", "_Z3foov")
        sl = SuppressionList([
            Suppression(symbol="_Z3foov", reason="exact"),
            Suppression(symbol_pattern=r".*foov", reason="pattern"),
        ])
        r = compare(_snap(functions=[f]), _snap(), suppression=sl)
        assert r.suppressed_count == 1  # one change, suppressed once

    def test_rule_for_wrong_kind_does_not_suppress(self):
        """Rule with specific change_kind doesn't suppress other kinds."""
        f = _pub_func("foo", "_Z3foov")
        sl = SuppressionList([
            Suppression(symbol="_Z3foov", change_kind="func_return_changed",
                        reason="return only"),
        ])
        # This is a func_removed, not func_return_changed
        r = compare(_snap(functions=[f]), _snap(), suppression=sl)
        assert r.verdict == Verdict.BREAKING
        assert r.suppressed_count == 0


# ═══════════════════════════════════════════════════════════════════════════
# Empty / Degenerate Cases
# ═══════════════════════════════════════════════════════════════════════════

class TestEmptySuppression:
    """Edge cases with empty suppression lists."""

    def test_empty_suppression_list(self):
        """Empty suppression list → nothing suppressed."""
        f = _pub_func("foo", "_Z3foov")
        sl = SuppressionList([])
        r = compare(_snap(functions=[f]), _snap(), suppression=sl)
        assert r.verdict == Verdict.BREAKING
        assert r.suppressed_count == 0
        assert r.suppression_file_provided is True

    def test_none_suppression(self):
        """No suppression list → suppression_file_provided is False."""
        f = _pub_func("foo", "_Z3foov")
        r = compare(_snap(functions=[f]), _snap(), suppression=None)
        assert r.suppression_file_provided is False


# ═══════════════════════════════════════════════════════════════════════════
# Suppression Audit
# ═══════════════════════════════════════════════════════════════════════════

class TestSuppressionAudit:
    """Audit trail for suppression rules."""

    def test_stale_rules_detected(self):
        """Rules that match nothing should be flagged as stale."""
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3foov",
                   description="removed"),
        ]
        sl = SuppressionList([
            Suppression(symbol="_Z3foov", reason="matches"),
            Suppression(symbol="_Z999nomatchv", reason="stale — no match"),
        ])
        audit = sl.audit(changes)
        assert len(audit.stale_rules) == 1
        assert audit.stale_rules[0].symbol == "_Z999nomatchv"

    def test_high_risk_suppression_flagged(self):
        """Suppressing BREAKING changes should be flagged as high-risk."""
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3foov",
                   description="removed"),
        ]
        sl = SuppressionList([
            Suppression(symbol="_Z3foov", reason="intentional"),
        ])
        audit = sl.audit(changes)
        assert len(audit.high_risk_matches) > 0

    def test_expired_rules_in_audit(self):
        """Expired rules should appear in audit.expired_rules."""
        yesterday = date.today() - timedelta(days=1)
        sl = SuppressionList([
            Suppression(symbol="_Z3foov", reason="old",
                        expires=yesterday),
        ])
        audit = sl.audit([])
        assert len(audit.expired_rules) == 1

    def test_near_expiry_rules(self):
        """Rules expiring soon should appear in near_expiry_rules."""
        soon = date.today() + timedelta(days=5)
        sl = SuppressionList([
            Suppression(symbol="_Z3foov", reason="expiring",
                        expires=soon),
        ])
        audit = sl.audit([], near_expiry_days=30)
        assert len(audit.near_expiry_rules) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Suppression + Policy File Interaction
# ═══════════════════════════════════════════════════════════════════════════

class TestSuppressionWithPolicy:
    """Suppression and policy overrides applied together."""

    def test_suppression_applied_before_policy(self):
        """Suppression filters out changes; policy only sees unsuppressed."""
        from abicheck.policy_file import PolicyFile

        f1 = _pub_func("api", "_Z3apiv", ret="int")
        f2 = _pub_func("api", "_Z3apiv", ret="long")

        # Suppress the return type change
        sl = SuppressionList([
            Suppression(symbol="_Z3apiv", change_kind="func_return_changed",
                        reason="known return type change"),
        ])

        pf = PolicyFile(base_policy="strict_abi")

        r = compare(
            _snap(functions=[f1]),
            _snap(functions=[f2]),
            suppression=sl,
            policy_file=pf,
        )
        assert r.suppressed_count == 1
        suppressed_kinds = {c.kind for c in r.suppressed_changes}
        assert ChangeKind.FUNC_RETURN_CHANGED in suppressed_kinds
        # With the only breaking change suppressed, verdict should be NO_CHANGE
        assert r.verdict == Verdict.NO_CHANGE
