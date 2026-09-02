"""Tests for A: selector-scoped reclassification (abicheck/reclassify.py)
and its policy_file.py `reclassify:` wiring.

Covers the third policy-file primitive: same selector grammar as
`suppress:`'s Suppression rules, but `to:` a verdict instead of deletion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change
from abicheck.errors import PolicyError
from abicheck.policy_file import PolicyFile
from abicheck.reclassify import ReclassifyRule, first_matching_reclassify_verdict
from abicheck.severity import (
    PRESET_DEFAULT,
    IssueCategory,
    classify_effective_change,
    compute_exit_code,
    effective_verdict_for_change,
)


def _change(kind: ChangeKind, symbol: str, **kwargs) -> Change:
    return Change(kind=kind, symbol=symbol, description="x", **kwargs)


# --- ReclassifyRule / first_matching_reclassify_verdict -------------------


def test_reclassify_rule_matches_by_symbol_pattern() -> None:
    rule = ReclassifyRule(
        to_verdict=Verdict.COMPATIBLE_WITH_RISK,
        to="risk",
        change_kind="func_visibility_changed",
        symbol_pattern=r"_ZN6oneapi3dal.*",
    )
    matched = _change(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN6oneapi3dal3fooEv")
    unmatched_symbol = _change(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN3foo3barEv")
    unmatched_kind = _change(ChangeKind.FUNC_REMOVED, "_ZN6oneapi3dal3fooEv")

    assert rule.matches(matched)
    assert not rule.matches(unmatched_symbol)
    assert not rule.matches(unmatched_kind)


def test_reclassify_rule_matches_by_binding() -> None:
    # Codex review, fresh evidence: `binding` selector was implemented on
    # Suppression but never wired through ReclassifyRule/policy_file.py's
    # reclassify: loader, so the two selector grammars silently drifted.
    rule = ReclassifyRule(
        to_verdict=Verdict.COMPATIBLE_WITH_RISK,
        to="risk",
        change_kind="func_removed",
        symbol="_Z1fv",
        binding="weak",
    )
    matched = _change(ChangeKind.FUNC_REMOVED, "_Z1fv", symbol_binding="weak")
    unmatched_binding = _change(ChangeKind.FUNC_REMOVED, "_Z1fv", symbol_binding="global")
    unmatched_no_binding = _change(ChangeKind.FUNC_REMOVED, "_Z1fv")

    assert rule.matches(matched)
    assert not rule.matches(unmatched_binding)
    assert not rule.matches(unmatched_no_binding)


def test_reclassify_binding_is_conjunctive_only() -> None:
    # binding alone (no other selector) isn't a valid rule -- same
    # conjunctive-only rule Suppression.binding follows.
    with pytest.raises(ValueError):
        ReclassifyRule(to_verdict=Verdict.COMPATIBLE_WITH_RISK, to="risk", binding="weak")


def test_policy_file_loads_reclassify_binding_selector(tmp_path: Path) -> None:
    p = tmp_path / "policy.yml"
    p.write_text(
        "base_policy: strict_abi\n"
        "reclassify:\n"
        "  - kind: func_removed\n"
        "    symbol: _Z1fv\n"
        "    binding: weak\n"
        "    to: risk\n"
    )
    pf = PolicyFile.load(p)
    assert pf.reclassify[0].binding == "weak"


def test_reclassify_rule_requires_a_selector() -> None:
    with pytest.raises(ValueError):
        ReclassifyRule(to_verdict=Verdict.COMPATIBLE_WITH_RISK, to="risk")


def test_reclassify_rule_rejects_unknown_change_kind() -> None:
    with pytest.raises(ValueError):
        ReclassifyRule(
            to_verdict=Verdict.COMPATIBLE_WITH_RISK,
            to="risk",
            symbol="foo",
            change_kind="not_a_real_kind",
        )


def test_reclassify_rule_rejects_no_change_as_a_target_verdict() -> None:
    """Codex review: a directly-constructed ReclassifyRule(to_verdict=
    Verdict.NO_CHANGE, ...) would make a matching real change disappear from
    every one of DiffResult.breaking/source_breaks/risk/compatible -- a
    silently passing result that conceals a real change, not a lenient
    reclassification. The public `to:` YAML vocabulary (break/warn/risk/
    ignore) can never produce this, but the class itself is public API."""
    with pytest.raises(ValueError, match="Invalid to_verdict"):
        ReclassifyRule(to_verdict=Verdict.NO_CHANGE, to="none", symbol="foo")


def test_reclassify_rule_expires() -> None:
    from datetime import date

    rule = ReclassifyRule(
        to_verdict=Verdict.COMPATIBLE,
        to="ignore",
        symbol="foo",
        expires=date(2020, 1, 1),
    )
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    assert not rule.matches(change, today=date(2020, 6, 1))
    assert rule.matches(change, today=date(2019, 1, 1))


def test_first_matching_reclassify_verdict_is_first_match_wins() -> None:
    rules = [
        ReclassifyRule(to_verdict=Verdict.COMPATIBLE, to="ignore", symbol="foo"),
        ReclassifyRule(to_verdict=Verdict.BREAKING, to="break", symbol="foo"),
    ]
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    assert first_matching_reclassify_verdict(rules, change) == Verdict.COMPATIBLE


def test_first_matching_reclassify_verdict_none_when_no_rule_matches() -> None:
    rules = [ReclassifyRule(to_verdict=Verdict.COMPATIBLE, to="ignore", symbol="foo")]
    change = _change(ChangeKind.FUNC_REMOVED, "bar")
    assert first_matching_reclassify_verdict(rules, change) is None


# --- PolicyFile `reclassify:` loading and priority -------------------------


def test_policy_file_loads_reclassify_block(tmp_path: Path) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
base_policy: strict_abi
reclassify:
  - kind: func_visibility_changed
    symbol_pattern: "_ZN6oneapi3dal.*"
    to: risk
    reason: "COMDAT-inline demotions"
""".strip(),
        encoding="utf-8",
    )

    pf = PolicyFile.load(p)
    assert len(pf.reclassify) == 1
    assert pf.reclassify[0].to_verdict == Verdict.COMPATIBLE_WITH_RISK

    matched = _change(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN6oneapi3dal3fooEv")
    unmatched = _change(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN3foo3barEv")
    assert pf.compute_verdict([matched]) == Verdict.COMPATIBLE_WITH_RISK
    assert pf.compute_verdict([unmatched]) == Verdict.BREAKING


def test_reclassify_takes_priority_over_kind_global_override(tmp_path: Path) -> None:
    """A selector-scoped reclassify rule is more specific than a bare-kind
    `overrides:` entry for the same kind, so it wins for a matching symbol —
    but an unrelated symbol of the same kind still falls through to the
    (broader) override."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
base_policy: strict_abi
overrides:
  func_visibility_changed: warn
reclassify:
  - kind: func_visibility_changed
    symbol_pattern: "_ZN6oneapi3dal.*"
    to: risk
""".strip(),
        encoding="utf-8",
    )

    pf = PolicyFile.load(p)
    scoped = _change(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN6oneapi3dal3fooEv")
    other = _change(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN3foo3barEv")
    assert pf.compute_verdict([scoped]) == Verdict.COMPATIBLE_WITH_RISK
    assert pf.compute_verdict([other]) == Verdict.API_BREAK


def test_reclassify_is_honored_by_the_shared_severity_resolver(tmp_path: Path) -> None:
    """`severity.effective_verdict_for_change` (and everything built on it --
    IssueCategory buckets, JSON/HTML/SARIF severity labels, severity-based
    exit codes) must agree with `PolicyFile.compute_verdict` for a
    reclassified finding, not silently re-derive the pre-reclassify kind
    category on its own independent code path."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_visibility_changed
    symbol_pattern: "_ZN6oneapi3dal.*"
    to: risk
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    matched = _change(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN6oneapi3dal3fooEv")
    unmatched = _change(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN3foo3barEv")

    assert pf.compute_verdict([matched]) == Verdict.COMPATIBLE_WITH_RISK
    assert effective_verdict_for_change(matched, policy_file=pf) == Verdict.COMPATIBLE_WITH_RISK
    assert classify_effective_change(matched, policy_file=pf) == IssueCategory.POTENTIAL_BREAKING
    assert compute_exit_code([matched], PRESET_DEFAULT, policy_file=pf) == 0

    assert pf.compute_verdict([unmatched]) == Verdict.BREAKING
    assert effective_verdict_for_change(unmatched, policy_file=pf) == Verdict.BREAKING
    assert classify_effective_change(unmatched, policy_file=pf) == IssueCategory.ABI_BREAKING
    assert compute_exit_code([unmatched], PRESET_DEFAULT, policy_file=pf) == 4


def test_reclassify_via_severity_resolver_respects_frozen_namespace_floor(
    tmp_path: Path,
) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: "frozen_symbol"
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(
        ChangeKind.FUNC_REMOVED, "frozen_symbol", frozen_namespace_violation="ns::**"
    )
    assert effective_verdict_for_change(change, policy_file=pf) == Verdict.BREAKING


def test_policy_file_keeps_existing_positional_field_order() -> None:
    """PolicyFile is public API constructed positionally by external callers
    -- `reclassify` must not shift `source_path` (or anything after it) out
    of its pre-existing positional slot."""
    from abicheck.policy_file import PolicyFile as PF

    pf = PF("strict_abi", {}, Path("/tmp/x.yaml"))
    assert pf.base_policy == "strict_abi"
    assert pf.overrides == {}
    assert pf.source_path == Path("/tmp/x.yaml")
    assert pf.reclassify == []


def test_reclassify_expires_normalizes_a_yaml_datetime(tmp_path: Path) -> None:
    """An unquoted YAML timestamp (`2020-08-12T00:00:00`) decodes as a
    `datetime`, not a `date` -- must be normalized to `.date()` so
    `Suppression.is_expired()`'s `date.today() > self.expires` comparison
    doesn't crash with `TypeError: can't compare datetime.date to
    datetime.datetime`."""
    from datetime import date

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: ignore
    expires: 2020-08-12T00:00:00
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    assert pf.reclassify[0].expires == date(2020, 8, 12)
    assert pf.reclassify[0].matches(
        _change(ChangeKind.FUNC_REMOVED, "foo"), today=date(2020, 1, 1)
    ), "before expiry, the rule should still match"
    assert not pf.reclassify[0].matches(
        _change(ChangeKind.FUNC_REMOVED, "foo"), today=date(2026, 1, 1)
    ), "past-expiry rule should stop matching (not crash)"


def test_reclassify_rule_direct_construction_normalizes_a_datetime_expires() -> None:
    """Codex review: only the policy-file loader (`_parse_reclassify_expires`)
    normalized a `datetime` expires to `.date()` -- a Python caller
    constructing `ReclassifyRule` directly with `expires=datetime(...)` (a
    `datetime` is itself a `date` subclass, so the type annotation accepts
    it) hit the identical `TypeError` crash on `is_expired()`/`matches()`.
    Verified by deliberately reverting the __post_init__ normalization and
    confirming this test fails with a raised TypeError."""
    from datetime import date, datetime

    rule = ReclassifyRule(
        to_verdict=Verdict.COMPATIBLE,
        to="ignore",
        symbol="foo",
        expires=datetime(2020, 8, 12, 0, 0, 0),
    )
    assert rule.expires == date(2020, 8, 12)
    assert not rule.matches(
        _change(ChangeKind.FUNC_REMOVED, "foo"), today=date(2026, 1, 1)
    ), "past-expiry rule should stop matching (not crash)"


def test_reclassify_rule_canonicalizes_to_from_to_verdict() -> None:
    """Codex review (second round on the same audit-trail concern): a
    directly-constructed rule's `to` is canonicalized from `to_verdict` in
    `__post_init__`, not trusted verbatim -- so `describe()` (used by the
    Markdown/HTML renderers) can never disagree with the verdict the rule
    actually applies, even when a caller supplies an inconsistent or omitted
    `to`. Verified by deliberately reverting the canonicalization and
    confirming this test fails."""
    # Inconsistent `to` -- the rule still promotes to BREAKING (to_verdict is
    # authoritative for matching/severity), but a naive `self.to` echo would
    # have described it as "ignore".
    rule = ReclassifyRule(to_verdict=Verdict.BREAKING, to="ignore", symbol="foo")
    assert rule.to == "break"
    assert "to=break" in rule.describe()
    assert "to=ignore" not in rule.describe()

    # Omitted `to` (the dataclass default "") is likewise canonicalized.
    rule2 = ReclassifyRule(to_verdict=Verdict.COMPATIBLE_WITH_RISK, symbol="foo")
    assert rule2.to == "risk"
    assert "to=risk" in rule2.describe()


def test_reclassify_respects_frozen_namespace_floor(tmp_path: Path) -> None:
    """A reclassify rule downgrading a change on a frozen namespace is
    silently rejected, exactly like a kind-global override already is."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
base_policy: strict_abi
reclassify:
  - kind: func_removed
    symbol: "frozen_symbol"
    to: ignore
""".strip(),
        encoding="utf-8",
    )

    pf = PolicyFile.load(p)
    change = _change(
        ChangeKind.FUNC_REMOVED, "frozen_symbol", frozen_namespace_violation="ns::**"
    )
    assert pf.compute_verdict([change]) == Verdict.BREAKING


def test_reclassify_does_not_apply_when_effective_verdict_already_set(
    tmp_path: Path,
) -> None:
    """A pipeline-set `effective_verdict` (ADR-025 modulation) wins over a
    reclassify rule, matching how it already wins over `overrides:`."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
base_policy: strict_abi
reclassify:
  - kind: func_removed
    symbol: "foo"
    to: ignore
""".strip(),
        encoding="utf-8",
    )

    pf = PolicyFile.load(p)
    change = _change(
        ChangeKind.FUNC_REMOVED, "foo", effective_verdict=Verdict.API_BREAK
    )
    assert pf.compute_verdict([change]) == Verdict.API_BREAK


# --- Validation errors ------------------------------------------------------


def test_reclassify_missing_to_field_is_a_hard_error(tmp_path: Path) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: "foo"
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="missing required 'to'"):
        PolicyFile.load(p)


def test_reclassify_invalid_to_value_is_a_hard_error(tmp_path: Path) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: "foo"
    to: not_a_real_severity
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="invalid 'to' value"):
        PolicyFile.load(p)


def test_reclassify_unknown_key_is_a_hard_error(tmp_path: Path) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: "foo"
    to: ignore
    bogus_key: 1
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="unknown key"):
        PolicyFile.load(p)


def test_reclassify_not_a_list_is_a_hard_error(tmp_path: Path) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text("reclassify: not-a-list", encoding="utf-8")
    with pytest.raises(PolicyError, match="must be a YAML list"):
        PolicyFile.load(p)


def test_reclassify_entry_not_a_mapping_is_a_hard_error(tmp_path: Path) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - "not a mapping"
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="must be a YAML mapping"):
        PolicyFile.load(p)


@pytest.mark.parametrize(
    "field",
    [
        "symbol",
        "symbol_pattern",
        "type_pattern",
        "member_name",
        "namespace",
        "entity_namespace",
        "cause_namespace",
        "source_location",
        "reason",
        "label",
    ],
)
def test_reclassify_non_string_selector_field_is_a_hard_error(
    field: str, tmp_path: Path
) -> None:
    """A non-string selector value must fail loading up front -- not crash
    with an uncaught TypeError deep in re.compile/fnmatch (symbol_pattern,
    type_pattern, every namespace variant) and not load silently as a rule
    that can never match (symbol, compared via `==`)."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        f"""
reclassify:
  - kind: func_removed
    {field}: 42
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match=f"{field}.*must be a string"):
        PolicyFile.load(p)


def test_reclassify_invalid_selector_is_a_hard_error(tmp_path: Path) -> None:
    """No symbol/pattern/namespace selector at all -- Suppression's own
    "at least one selector" validation propagates through PolicyFile.load."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(PolicyError):
        PolicyFile.load(p)


def test_policy_file_describe_includes_reclassify_rules(tmp_path: Path) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_visibility_changed
    symbol_pattern: "_ZN6oneapi3dal.*"
    to: risk
    reason: "COMDAT-inline demotions"
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    text = pf.describe()
    assert "reclassify:" in text
    assert "to=risk" in text
    assert "COMDAT-inline demotions" in text


# --- cli_scan_baseline._blocking_compatible_changes / classify_change_object ----


def test_reclassified_finding_is_identified_as_the_scan_blocker(tmp_path: Path) -> None:
    """A `reclassify:`-demoted BREAKING finding that lands in `diff.compatible`
    (as QUALITY_ISSUES, since func_removed isn't an ADDITION_KINDS member)
    must still be nameable as the scan's own blocking finding -- not just
    correctly gated (that already worked; `_build_severity_json` passes
    `policy_file`) but correctly *reported*, via
    `cli_scan_baseline._blocking_compatible_changes` /
    `severity.classify_change_object` (Codex review)."""
    from abicheck.checker_types import DiffResult
    from abicheck.cli_scan_baseline import _blocking_compatible_changes
    from abicheck.severity import IssueCategory, classify_change_object

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: "_ZN6oneapi3dal3fooEv"
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    reclassified = _change(ChangeKind.FUNC_REMOVED, "_ZN6oneapi3dal3fooEv")
    diff = DiffResult(
        changes=[reclassified], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )

    # The reclassify rule already correctly moves it into `.compatible`
    # (DiffResult._effective_verdict_for_change already passes policy_file).
    assert diff.compatible == [reclassified]

    # Without policy_file, classify_change_object can't see the
    # selector-scoped rule and falls back to the raw kind category.
    assert (
        classify_change_object(reclassified, kind_sets=diff._effective_kind_sets())
        == IssueCategory.ABI_BREAKING
    )
    # With it, the reclassification is honored.
    assert (
        classify_change_object(
            reclassified, kind_sets=diff._effective_kind_sets(), policy_file=pf
        )
        == IssueCategory.QUALITY_ISSUES
    )

    blocked = _blocking_compatible_changes(diff, {"quality_issues"})
    assert blocked == [reclassified]


def _override_adjusted_kind_sets(pf: PolicyFile, *changes):
    """The real, override-adjusted kind sets a production caller
    (sarif.py's ``_severity()``, cli_scan_baseline.py's
    ``_blocking_compatible_changes``) actually passes as *kind_sets* --
    ``DiffResult._effective_kind_sets()``. A bare
    ``classify_effective_change(change, policy_file=pf)`` call with no
    explicit *kind_sets* falls back to the canonical, override-*unaware*
    default set instead, which never actually exercises "a kind-global
    override moved this kind out of ``sets[2]``" at all -- exactly the
    scenario these tests need to be realistic about (Codex review: an
    earlier version of this test passed regardless of whether the
    addition/quality-split fix below was even present, for exactly this
    reason)."""
    from abicheck.checker_types import DiffResult

    diff = DiffResult(
        changes=list(changes), old_version="1", new_version="2", library="l",
        policy_file=pf,
    )
    return diff._effective_kind_sets()


def test_reclassify_wins_addition_bucket_over_a_kind_global_override(
    tmp_path: Path,
) -> None:
    """A `reclassify:` rule downgrading one ADDITION_KINDS finding to
    `ignore` must land in the `addition` category, not `quality_issues` --
    even when a kind-global `overrides:` entry for the same kind would
    otherwise move it out of the compatible kind set entirely (Codex
    review: `func_added` overridden to `break` globally, with a scoped
    `reclassify:` rule bringing one specific symbol back to `ignore` --
    `quality_issues=error`/`addition=info` must exit 0, not 1)."""
    from abicheck.severity import (
        PRESET_DEFAULT,
        IssueCategory,
        SeverityConfig,
        SeverityLevel,
        classify_effective_change,
        compute_exit_code,
    )

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
overrides:
  func_added: break
reclassify:
  - kind: func_added
    symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    scoped = _change(ChangeKind.FUNC_ADDED, "foo")
    unscoped = _change(ChangeKind.FUNC_ADDED, "bar")
    kind_sets = _override_adjusted_kind_sets(pf, scoped, unscoped)

    assert (
        classify_effective_change(scoped, kind_sets=kind_sets, policy_file=pf)
        == IssueCategory.ADDITION
    )
    # An unrelated func_added symbol still falls through to the (broader)
    # override -- BREAKING, not silently granted the same leniency.
    assert (
        classify_effective_change(unscoped, kind_sets=kind_sets, policy_file=pf)
        == IssueCategory.ABI_BREAKING
    )

    cfg = SeverityConfig(
        quality_issues=SeverityLevel.ERROR, addition=SeverityLevel.INFO
    )
    assert compute_exit_code([scoped], cfg, kind_sets=kind_sets, policy_file=pf) == 0
    assert (
        compute_exit_code([scoped], PRESET_DEFAULT, kind_sets=kind_sets, policy_file=pf)
        == 0
    )


def test_reclassify_addition_bucket_ignores_a_shadowed_rule(tmp_path: Path) -> None:
    """Codex review, second round: `_reclassify_resolved_to_compatible` must
    not widen the ADDITION bucket for a rule that merely *matches* -- only
    for one that actually *decided* the verdict. When
    `change.effective_verdict` is already set (a pipeline modulation, which
    outranks `reclassify:` entirely -- see `effective_verdict_for_change`'s
    own topmost branch), a same-symbol `reclassify:` rule sits shadowed and
    must not still grant ADDITION leniency: an addition kind globally
    overridden to `break`, with effective_verdict=COMPATIBLE set by an
    unrelated mechanism, stays QUALITY_ISSUES."""
    from abicheck.severity import IssueCategory, classify_effective_change

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
overrides:
  func_added: break
reclassify:
  - kind: func_added
    symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(
        ChangeKind.FUNC_ADDED, "foo", effective_verdict=Verdict.COMPATIBLE
    )
    kind_sets = _override_adjusted_kind_sets(pf, change)

    assert (
        classify_effective_change(change, kind_sets=kind_sets, policy_file=pf)
        == IssueCategory.QUALITY_ISSUES
    )


# --- standard-report disclosure (reporter.py's policy_reclassify key) ------


def test_active_reclassify_rules_are_disclosed_in_the_standard_report(
    tmp_path: Path,
) -> None:
    """Codex review: an ordinary comparison reclassifying a finding had no
    trace of the active `reclassify:` rule anywhere in the standard JSON
    report. `policy_reclassify` (report_schema_version 2.30) now lists the
    active rule set, mirroring the existing `policy_overrides` disclosure."""
    import json

    from abicheck.checker_types import DiffResult
    from abicheck.reporter import to_json
    from abicheck.schemas import REPORT_SCHEMA_VERSION

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_visibility_changed
    symbol_pattern: "_ZN6oneapi3dal.*"
    to: risk
    reason: "COMDAT-inline demotions"
    expires: 2027-01-01
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN6oneapi3dal3fooEv")
    diff = DiffResult(
        changes=[change], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )

    d = json.loads(to_json(diff))
    assert d["report_schema_version"] == REPORT_SCHEMA_VERSION
    assert d["policy_file"] == str(p)
    assert d["policy_reclassify"] == [
        {
            "to": "COMPATIBLE_WITH_RISK",
            "kind": "func_visibility_changed",
            "symbol_pattern": "_ZN6oneapi3dal.*",
            "reason": "COMDAT-inline demotions",
            "expires": "2027-01-01",
        }
    ]


def test_policy_reclassify_absent_without_any_configured_rule(tmp_path: Path) -> None:
    """No `reclassify:` rule => no `policy_reclassify` key at all, keeping
    every pre-existing report byte-identical (schema 2.30's own
    additive-change guarantee)."""
    import json

    from abicheck.checker_types import DiffResult
    from abicheck.reporter import to_json

    p = tmp_path / "policy.yaml"
    p.write_text("overrides:\n  enum_member_renamed: ignore\n", encoding="utf-8")
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.ENUM_MEMBER_RENAMED, "foo")
    diff = DiffResult(
        changes=[change], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )

    d = json.loads(to_json(diff))
    assert "policy_reclassify" not in d
    assert d["policy_overrides"] == {"enum_member_renamed": "COMPATIBLE"}


def _reclassify_diff_and_policy_path(tmp_path: Path):
    """Shared fixture for the markdown/HTML/SARIF disclosure tests below."""
    from abicheck.checker_types import DiffResult

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_visibility_changed
    symbol_pattern: "_ZN6oneapi3dal.*"
    to: risk
    reason: "COMDAT-inline demotions"
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN6oneapi3dal3fooEv")
    return DiffResult(
        changes=[change], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )


def test_reclassify_disclosed_in_markdown_report(tmp_path: Path) -> None:
    """Codex review: Markdown must disclose active reclassify: rules the
    same way it already discloses overrides:."""
    from abicheck.reporter_markdown import to_markdown

    diff = _reclassify_diff_and_policy_path(tmp_path)
    md = to_markdown(diff)
    assert "Policy reclassify" in md
    assert "func_visibility_changed" in md
    assert "COMDAT-inline demotions" in md


def test_reclassify_markdown_disclosure_is_code_span_wrapped(
    tmp_path: Path,
) -> None:
    """CodeRabbit review: describe() returns raw selector text (e.g.
    `symbol_pattern='_ZN6oneapi3dal.*'`) -- unescaped, Markdown reads `_`/`*`
    as emphasis markers, which can mangle a mangled-name selector's
    rendering. Must be code-span-wrapped, same as the `Policy overrides`
    line already wraps its own values."""
    from abicheck.reporter_markdown import to_markdown

    diff = _reclassify_diff_and_policy_path(tmp_path)
    md = to_markdown(diff)
    line = next(ln for ln in md.splitlines() if "Policy reclassify" in ln)
    assert "`reclassify(to=risk" in line
    assert line.rstrip().endswith("`")


def test_reclassify_disclosed_in_html_report(tmp_path: Path) -> None:
    from abicheck.html_report import generate_html_report

    diff = _reclassify_diff_and_policy_path(tmp_path)
    html_out = generate_html_report(diff)
    assert "Policy reclassify" in html_out
    assert "func_visibility_changed" in html_out


def test_reclassify_rule_describe_includes_expiry_and_label() -> None:
    """Codex review: describe() (what the Markdown/HTML renderers actually
    call) previously stopped after `reason`, silently omitting `expires`
    and `label` -- a reader of those two formats had no way to tell when a
    temporary waiver stops applying. to_report_dict() already included both;
    describe() must match."""
    from datetime import date

    rule = ReclassifyRule(
        to_verdict=Verdict.COMPATIBLE_WITH_RISK,
        to="risk",
        symbol="foo",
        label="workaround",
        expires=date(2027, 1, 1),
    )
    text = rule.describe()
    assert "label='workaround'" in text
    assert "expires='2027-01-01'" in text


def test_reclassify_disclosed_in_sarif_report(tmp_path: Path) -> None:
    from abicheck.sarif import to_sarif

    diff = _reclassify_diff_and_policy_path(tmp_path)
    sarif_out = to_sarif(diff)
    props = sarif_out["runs"][0]["properties"]
    assert props["policyReclassify"] == [
        {
            "to": "COMPATIBLE_WITH_RISK",
            "kind": "func_visibility_changed",
            "symbol_pattern": "_ZN6oneapi3dal.*",
            "reason": "COMDAT-inline demotions",
        }
    ]


def test_reclassify_absent_from_sarif_without_any_configured_rule() -> None:
    """Mirrors the existing policyOverrides absence test -- no reclassify:
    rule => no policyReclassify key, report byte-identical."""
    from abicheck.checker_types import DiffResult
    from abicheck.sarif import to_sarif

    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    diff = DiffResult(changes=[change], old_version="1", new_version="2", library="l")
    props = to_sarif(diff)["runs"][0]["properties"]
    assert "policyReclassify" not in props


def _expired_reclassify_diff(tmp_path: Path):
    from abicheck.checker_types import DiffResult

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: ignore
    expires: 2020-01-01
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    return DiffResult(
        changes=[change], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )


def test_expired_reclassify_rule_absent_from_json_report(tmp_path: Path) -> None:
    """Codex review: an expired rule -- which ReclassifyRule.matches() would
    already refuse to apply -- must not be disclosed as though it were
    still active."""
    import json

    from abicheck.reporter import to_json

    diff = _expired_reclassify_diff(tmp_path)
    d = json.loads(to_json(diff))
    assert "policy_reclassify" not in d


def test_expired_reclassify_rule_absent_from_markdown_report(tmp_path: Path) -> None:
    from abicheck.reporter_markdown import to_markdown

    diff = _expired_reclassify_diff(tmp_path)
    assert "Policy reclassify" not in to_markdown(diff)


def test_expired_reclassify_rule_absent_from_html_report(tmp_path: Path) -> None:
    from abicheck.html_report import generate_html_report

    diff = _expired_reclassify_diff(tmp_path)
    assert "Policy reclassify" not in generate_html_report(diff)


def test_expired_reclassify_rule_absent_from_sarif_report(tmp_path: Path) -> None:
    from abicheck.sarif import to_sarif

    diff = _expired_reclassify_diff(tmp_path)
    props = to_sarif(diff)["runs"][0]["properties"]
    assert "policyReclassify" not in props


def test_active_reclassify_rules_filters_expired() -> None:
    from datetime import date

    from abicheck.reclassify import active_reclassify_rules

    active = ReclassifyRule(to_verdict=Verdict.COMPATIBLE, to="ignore", symbol="a")
    expired = ReclassifyRule(
        to_verdict=Verdict.COMPATIBLE,
        to="ignore",
        symbol="b",
        expires=date(2020, 1, 1),
    )
    result = active_reclassify_rules([active, expired], today=date(2026, 1, 1))
    assert result == [active]


# --- --audit-suppressions (SuppressionList.audit's policy_file param) ------


def test_audit_flags_a_reclassify_promoted_finding_as_high_risk(
    tmp_path: Path,
) -> None:
    """Codex review: a `reclassify:` rule promoting one normally-compatible
    finding to `break` isn't expressible in `effective_breaking_kinds` (a
    kind-wide set) -- SuppressionList.audit() must classify a matching
    change by the rule's own resolution instead of falling back to (silently
    absent) kind-set membership."""
    from abicheck.suppression import Suppression, SuppressionList

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_added
    symbol: foo
    to: break
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    sup = Suppression(symbol="foo")
    supl = SuppressionList([sup])
    change = _change(ChangeKind.FUNC_ADDED, "foo")

    # Without policy_file: func_added isn't in the (empty, for this test)
    # breaking set, so the match isn't flagged high-risk.
    audit_without = supl.audit([change], breaking_kinds=frozenset())
    assert audit_without.high_risk_matches == []

    # With policy_file: the reclassify rule's own `to: break` resolution
    # wins, even though func_added is still absent from breaking_kinds.
    audit_with = supl.audit([change], breaking_kinds=frozenset(), policy_file=pf)
    assert len(audit_with.high_risk_matches) == 1
    assert audit_with.high_risk_matches[0] == (sup, change)


def test_audit_reclassify_demotion_is_not_high_risk(tmp_path: Path) -> None:
    """The inverse: a `reclassify:` rule demoting a normally-breaking kind
    to `ignore` for one symbol must NOT be reported as high-risk for that
    symbol, even though the kind is still in breaking_kinds."""
    from abicheck.suppression import Suppression, SuppressionList

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    sup = Suppression(symbol="foo")
    supl = SuppressionList([sup])
    change = _change(ChangeKind.FUNC_REMOVED, "foo")

    audit = supl.audit(
        [change], breaking_kinds=frozenset({ChangeKind.FUNC_REMOVED}), policy_file=pf
    )
    assert audit.high_risk_matches == []


def test_audit_without_policy_file_is_unchanged() -> None:
    """No policy_file passed at all => identical to the pre-existing
    behavior (kind-set membership only)."""
    from abicheck.suppression import Suppression, SuppressionList

    sup = Suppression(symbol="foo")
    supl = SuppressionList([sup])
    change = _change(ChangeKind.FUNC_REMOVED, "foo")

    audit = supl.audit(
        [change], breaking_kinds=frozenset({ChangeKind.FUNC_REMOVED})
    )
    assert len(audit.high_risk_matches) == 1


def test_audit_reclassify_respects_frozen_namespace_floor(tmp_path: Path) -> None:
    """Codex review, second round: a naive reclassify-only check in the
    audit bypassed the frozen-namespace verdict floor. A `to: ignore` rule
    matching a frozen-namespace change must NOT demote it -- the audit must
    still flag the suppression as high-risk, via the same shared resolver
    (severity.effective_verdict_for_change) the comparison's own verdict
    already went through."""
    from abicheck.suppression import Suppression, SuppressionList

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    sup = Suppression(symbol="foo")
    supl = SuppressionList([sup])
    change = _change(
        ChangeKind.FUNC_REMOVED, "foo", frozen_namespace_violation="ns::**"
    )

    audit = supl.audit([change], policy_file=pf)
    assert len(audit.high_risk_matches) == 1


def test_audit_reclassify_yields_to_effective_verdict_precedence(
    tmp_path: Path,
) -> None:
    """Codex review, second round: a pipeline-set `effective_verdict`
    (ADR-025 modulation) must win over a matching `reclassify:` rule in the
    audit too, exactly like it already does in
    `severity.effective_verdict_for_change` itself -- not get silently
    bypassed by a reclassify-only shortcut."""
    from abicheck.suppression import Suppression, SuppressionList

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    sup = Suppression(symbol="foo")
    supl = SuppressionList([sup])
    change = _change(
        ChangeKind.FUNC_REMOVED, "foo", effective_verdict=Verdict.COMPATIBLE
    )

    audit = supl.audit([change], policy_file=pf)
    assert audit.high_risk_matches == []


# --- PolicyFile.validate_overrides() covers reclassify: too ---------------


def test_validate_overrides_flags_a_critical_kind_reclassified_to_ignore(
    tmp_path: Path,
) -> None:
    """Codex review: a `reclassify:` rule downgrading a critical/dangerous
    kind is exactly the mistake this check exists to catch -- it must not
    bypass the diagnostic purely by being selector-scoped instead of a
    kind-global `overrides:` entry."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    warnings = pf.validate_overrides()
    assert len(warnings) == 1
    assert "HIGH RISK" in warnings[0]
    assert "func_removed" in warnings[0]


def test_validate_overrides_flags_a_critical_kind_reclassified_to_risk(
    tmp_path: Path,
) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: type_size_changed
    symbol: foo
    to: risk
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    warnings = pf.validate_overrides()
    assert len(warnings) == 1
    assert "RISK" in warnings[0]


def test_validate_overrides_flags_a_breaking_kind_reclassified_to_ignore(
    tmp_path: Path,
) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_virtual_added
    symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    warnings = pf.validate_overrides()
    assert len(warnings) == 1
    assert "BREAKING" in warnings[0]


def test_validate_overrides_safe_reclassify_no_warning(tmp_path: Path) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: enum_member_renamed
    symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    assert pf.validate_overrides() == []


def test_validate_overrides_flags_a_kindless_reclassify_rule_downgraded_to_ignore(
    tmp_path: Path,
) -> None:
    """Codex review (second round): a rule scoped purely by symbol/namespace,
    with no `kind:` filter, has no single base_verdict to compare against the
    way a kind-scoped rule does -- but silently skipping it entirely left the
    widest-blast-radius shape of reclassify rule (matches every kind a
    symbol/pattern could ever produce, including func_removed) with no
    safety diagnostic at all. It's now warned unconditionally rather than
    silently skipped."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    warnings = pf.validate_overrides()
    assert len(warnings) == 1
    assert "HIGH RISK" in warnings[0]
    assert "no 'kind:' filter" in warnings[0]


def test_validate_overrides_flags_a_kindless_reclassify_rule_downgraded_to_risk(
    tmp_path: Path,
) -> None:
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - symbol: foo
    to: risk
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    warnings = pf.validate_overrides()
    assert len(warnings) == 1
    assert "RISK" in warnings[0]
    assert "no 'kind:' filter" in warnings[0]


def test_validate_overrides_kindless_reclassify_rule_to_warn_is_not_flagged(
    tmp_path: Path,
) -> None:
    """A kindless rule reclassifying to 'break'/'warn' isn't a downgrade at
    all -- only 'ignore'/'risk' targets warrant the conservative warning."""
    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - symbol: foo
    to: warn
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    assert pf.validate_overrides() == []


# --- effective_verdict_for_change's final fallback honors base_policy -----


def test_effective_verdict_for_change_honors_custom_base_policy_with_no_match(
    tmp_path: Path,
) -> None:
    """Codex review: for a policy_file whose base_policy isn't strict_abi
    (e.g. plugin_abi), a finding with no effective_verdict/reclassify:/
    overrides: match must fall back to *that* base policy's own kind sets,
    not silently re-derive strict_abi's. Concrete repro: plugin_abi
    downgrades calling_convention_changed from BREAKING (strict_abi) to
    COMPATIBLE -- a bug here reads as BREAKING regardless of the policy
    file's own base_policy. Pre-existing in effective_verdict_for_change's
    final fallback line, surfaced by SuppressionList.audit()'s new
    policy_file=-only call path (this PR) since no earlier caller passed
    policy_file without also passing a matching kind_sets/policy."""
    from abicheck.severity import effective_verdict_for_change

    p = tmp_path / "policy.yaml"
    p.write_text("base_policy: plugin_abi\n", encoding="utf-8")
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.CALLING_CONVENTION_CHANGED, "foo")

    assert effective_verdict_for_change(change, policy_file=pf) == Verdict.COMPATIBLE


def test_audit_honors_custom_base_policy_for_a_non_matching_finding(
    tmp_path: Path,
) -> None:
    """Same bug, exercised through the actual --audit-suppressions call
    path this PR added (SuppressionList.audit(..., policy_file=pf))."""
    from abicheck.suppression import Suppression, SuppressionList

    p = tmp_path / "policy.yaml"
    p.write_text("base_policy: plugin_abi\n", encoding="utf-8")
    pf = PolicyFile.load(p)
    sup = Suppression(symbol="foo")
    supl = SuppressionList([sup])
    change = _change(ChangeKind.CALLING_CONVENTION_CHANGED, "foo")

    # plugin_abi downgrades this kind to COMPATIBLE -- never high-risk.
    audit = supl.audit([change], policy_file=pf)
    assert audit.high_risk_matches == []


# --- severity.reclassify_rule_for_change / reporter's reclassified_by -----
#
# Codex review: cli_pr_comment's `pr_comment._reclassified_count()` only
# recognized the kind-global `policy_overrides` map, so a finding downgraded
# by a selector-scoped `reclassify:` rule bypassed the PR comment's "🔀 N
# findings reclassified" disclosure entirely -- it read as an unremarked
# safe change. Fixed with a per-change `reclassified_by` field
# (report_schema_version 2.31), stamped by `severity.
# reclassify_rule_for_change` -- the deciding rule, honoring the same
# precedence `effective_verdict_for_change` already uses (not shadowed by a
# pipeline `effective_verdict`, not blocked by the frozen-namespace floor).


def test_reclassify_rule_for_change_returns_the_deciding_rule(tmp_path: Path) -> None:
    from abicheck.severity import reclassify_rule_for_change

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: ignore
    label: comdat-inline
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    rule = reclassify_rule_for_change(change, pf)
    assert rule is not None
    assert rule.label == "comdat-inline"


def test_reclassify_rule_for_change_none_for_a_no_op_rule(tmp_path: Path) -> None:
    """Codex review: a matching rule whose `to:` merely restates the verdict
    the finding already has (func_removed, already BREAKING under
    strict_abi, reclassified `to: break`) isn't a real reclassification --
    stamping `reclassified_by` for it would make the PR comment falsely
    report a downgrade that never happened. Verified by deliberately
    reverting the no-op check and confirming this test fails."""
    from abicheck.severity import reclassify_rule_for_change

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: break
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    assert reclassify_rule_for_change(change, pf) is None


def test_reclassify_rule_for_change_none_for_a_no_op_rule_restating_an_override(
    tmp_path: Path,
) -> None:
    """The no-op comparison is against whichever verdict would apply next in
    precedence -- a same-kind `overrides:` entry when one exists, not always
    the base policy's own verdict."""
    from abicheck.severity import reclassify_rule_for_change

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
overrides:
  func_removed: risk
reclassify:
  - kind: func_removed
    symbol: foo
    to: risk
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    assert reclassify_rule_for_change(change, pf) is None


def test_reclassify_rule_for_change_not_none_when_it_actually_changes_the_verdict(
    tmp_path: Path,
) -> None:
    """Sanity check alongside the two no-op tests above: a rule that *does*
    change the verdict from what the override/base-policy path would
    produce is still correctly recognized as deciding."""
    from abicheck.severity import reclassify_rule_for_change

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
overrides:
  func_removed: risk
reclassify:
  - kind: func_removed
    symbol: foo
    to: warn
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    rule = reclassify_rule_for_change(change, pf)
    assert rule is not None
    assert rule.to_verdict == Verdict.API_BREAK


def test_reclassify_rule_for_change_no_op_against_a_floor_clamped_override(
    tmp_path: Path,
) -> None:
    """Codex review (second round): the no-op comparison verdict must apply
    the identical frozen-namespace floor the `overrides:` branch itself
    applies -- not the override's raw value. `overrides: func_removed:
    ignore` (COMPATIBLE) on a frozen-namespace finding already clamps back
    to BREAKING (the base verdict) via the floor with no reclassify: rule
    involved at all -- so a `reclassify: ... to: break` rule here restates
    an already-BREAKING outcome and is a no-op, even though the *raw*
    override value (COMPATIBLE) differs from `to: break`. Verified by
    deliberately comparing against the raw override instead of the
    floor-clamped one and confirming this test fails."""
    from abicheck.severity import reclassify_rule_for_change

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
overrides:
  func_removed: ignore
reclassify:
  - kind: func_removed
    symbol: foo
    to: break
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(
        ChangeKind.FUNC_REMOVED, "foo", frozen_namespace_violation="detail.*"
    )
    assert reclassify_rule_for_change(change, pf) is None


def test_reclassify_rule_for_change_attributes_a_blocked_rule_that_still_changed_the_outcome(
    tmp_path: Path,
) -> None:
    """Codex review (third round on the same frozen-floor interaction): a
    matching reclassify rule blocked by the frozen-namespace floor still
    changed the effective outcome if the floor-clamped result (base_v)
    differs from what overrides: alone would have produced (next_priority_v).
    `func_noexcept_removed` is a RISK kind (base verdict
    COMPATIBLE_WITH_RISK under strict_abi); `overrides: ... to: break`
    (BREAKING) makes next_priority_v BREAKING, and `reclassify: ... to:
    ignore` (COMPATIBLE) on a frozen-namespace finding is blocked back to
    base_v (COMPATIBLE_WITH_RISK) -- BREAKING != COMPATIBLE_WITH_RISK, so
    the rule genuinely changed the outcome (from BREAKING to
    COMPATIBLE_WITH_RISK) even though it didn't get the verdict it asked
    for, and must still be attributed. Verified by deliberately reverting
    to the previous "blocked == always no-op" logic and confirming this
    test fails."""
    from abicheck.severity import reclassify_rule_for_change

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
overrides:
  func_noexcept_removed: break
reclassify:
  - kind: func_noexcept_removed
    symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(
        ChangeKind.FUNC_NOEXCEPT_REMOVED,
        "foo",
        frozen_namespace_violation="detail.*",
    )
    rule = reclassify_rule_for_change(change, pf)
    assert rule is not None
    assert rule.to_verdict == Verdict.COMPATIBLE


def test_reclassify_rule_for_change_none_when_no_rule_matches(tmp_path: Path) -> None:
    from abicheck.severity import reclassify_rule_for_change

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: bar
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    assert reclassify_rule_for_change(change, pf) is None


def test_reclassify_rule_for_change_none_without_a_policy_file() -> None:
    from abicheck.severity import reclassify_rule_for_change

    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    assert reclassify_rule_for_change(change, None) is None


def test_reclassify_rule_for_change_shadowed_by_effective_verdict(
    tmp_path: Path,
) -> None:
    """A matching rule that's shadowed by a higher-priority pipeline
    effective_verdict didn't actually decide the change's verdict, so it
    isn't "the reclassifying rule" for disclosure purposes."""
    from abicheck.severity import reclassify_rule_for_change

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(
        ChangeKind.FUNC_REMOVED, "foo", effective_verdict=Verdict.BREAKING
    )
    assert reclassify_rule_for_change(change, pf) is None


def test_reclassify_rule_for_change_blocked_by_frozen_namespace_floor(
    tmp_path: Path,
) -> None:
    """A matching rule blocked by the frozen-namespace verdict floor didn't
    actually decide the change's verdict either -- verified by deliberately
    reverting the frozen-namespace guard and confirming this test fails."""
    from abicheck.severity import reclassify_rule_for_change

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(
        ChangeKind.FUNC_REMOVED,
        "foo",
        frozen_namespace_violation="detail.*",
    )
    assert reclassify_rule_for_change(change, pf) is None


def test_reclassified_by_stamped_in_the_standard_report(tmp_path: Path) -> None:
    """The JSON report's `changes[].reclassified_by` field names the
    deciding rule, mirroring reporter.py's `policy_reclassify` (the active
    rule set) with per-finding attribution."""
    import json

    from abicheck.checker_types import DiffResult
    from abicheck.reporter import to_json
    from abicheck.schemas import REPORT_SCHEMA_VERSION

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: ignore
    reason: "COMDAT-inline demotions"
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    diff = DiffResult(
        changes=[change], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )

    d = json.loads(to_json(diff))
    assert d["report_schema_version"] == REPORT_SCHEMA_VERSION
    assert d["changes"][0]["reclassified_by"] == "COMDAT-inline demotions"


def test_reclassified_by_absent_when_no_rule_matches(tmp_path: Path) -> None:
    import json

    from abicheck.checker_types import DiffResult
    from abicheck.reporter import to_json

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: bar
    to: ignore
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    diff = DiffResult(
        changes=[change], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )

    d = json.loads(to_json(diff))
    assert "reclassified_by" not in d["changes"][0]


def test_reclassified_by_absent_for_a_kind_global_override(tmp_path: Path) -> None:
    """A change decided by a kind-global `overrides:` entry (not a
    `reclassify:` rule) doesn't carry `reclassified_by` -- that field
    attributes only to a selector-scoped rule; `overrides:`'s own audit
    trail is `policy_overrides`, already keyed by kind."""
    import json

    from abicheck.checker_types import DiffResult
    from abicheck.reporter import to_json

    p = tmp_path / "policy.yaml"
    p.write_text("overrides:\n  func_removed: ignore\n", encoding="utf-8")
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    diff = DiffResult(
        changes=[change], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )

    d = json.loads(to_json(diff))
    assert "reclassified_by" not in d["changes"][0]
    assert d["policy_overrides"] == {"func_removed": "COMPATIBLE"}


def test_reclassified_by_absent_for_a_no_op_reclassify_rule(tmp_path: Path) -> None:
    """Codex review: a matching `reclassify:` rule whose `to:` merely
    restates the finding's already-BREAKING verdict (`func_removed`, `to:
    break`) is not a real reclassification -- stamping `reclassified_by` for
    it would make the JSON report (and the PR comment reading it) falsely
    claim a downgrade that never happened."""
    import json

    from abicheck.checker_types import DiffResult
    from abicheck.reporter import to_json

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_removed
    symbol: foo
    to: break
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    diff = DiffResult(
        changes=[change], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )

    d = json.loads(to_json(diff))
    assert "reclassified_by" not in d["changes"][0]


def test_reclassified_by_falls_back_to_to_verdict_for_a_directly_constructed_rule() -> (
    None
):
    """Codex review: a Python caller constructing `ReclassifyRule` directly
    with only `to_verdict` set (`to` defaults to `""`) previously stamped no
    `reclassified_by` at all -- `rule.label or rule.reason or rule.to`
    fell through to the empty string. Worse, a `to` that doesn't actually
    match `to_verdict` would mislabel the audit trail. Falling back to
    `rule.to_verdict.value` instead of the raw, caller-supplied `to`
    guarantees the disclosure is always present and always reflects the
    verdict that was actually applied."""
    import json

    from abicheck.checker_types import DiffResult
    from abicheck.reporter import to_json

    # `to=""` (the dataclass default) -- as a direct Python construction
    # would leave it if the caller only set to_verdict.
    rule = ReclassifyRule(
        to_verdict=Verdict.COMPATIBLE, symbol="foo", change_kind="func_removed"
    )
    pf = PolicyFile(base_policy="strict_abi", overrides={}, reclassify=[rule])
    change = _change(ChangeKind.FUNC_REMOVED, "foo")
    diff = DiffResult(
        changes=[change], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )

    d = json.loads(to_json(diff))
    assert d["changes"][0]["reclassified_by"] == "COMPATIBLE"


def test_reclassified_by_and_policy_reclassify_present_in_leaf_mode(
    tmp_path: Path,
) -> None:
    """Codex review: `--report-mode leaf` builds its own leaf-entry dict
    (`_leaf_entry`, for root TYPE_* kinds) rather than routing through
    `_change_to_dict`, and never called `_add_policy_overrides` at all --
    both `changes[].reclassified_by` and the run-level `policy_reclassify`
    key were silently absent from leaf-mode output even though the
    identical comparison's full-mode report carried both. `type_size_changed`
    is a root TYPE_* kind (`_ROOT_TYPE_CHANGE_KINDS`), so it's built by
    `_leaf_entry`, not `_change_to_dict`, exercising the leaf-only path."""
    import json

    from abicheck.checker_types import DiffResult
    from abicheck.reporter import to_json

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: type_size_changed
    symbol: foo
    to: risk
    reason: "COMDAT-inline demotions"
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.TYPE_SIZE_CHANGED, "foo")
    diff = DiffResult(
        changes=[change], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )

    d = json.loads(to_json(diff, report_mode="leaf"))
    assert d["leaf_changes"][0]["reclassified_by"] == "COMDAT-inline demotions"
    assert d["changes"][0]["reclassified_by"] == "COMDAT-inline demotions"
    assert d["policy_reclassify"] == [
        {
            "to": "COMPATIBLE_WITH_RISK",
            "kind": "type_size_changed",
            "symbol": "foo",
            "reason": "COMDAT-inline demotions",
        }
    ]


# --- policy_reclassify[].to schema enum (CodeRabbit review) ----------------


def test_policy_reclassify_to_field_validates_against_the_published_schema(
    tmp_path: Path,
) -> None:
    """CodeRabbit review: `ReclassifyRule.to_report_dict()`'s `to` key is
    `to_verdict.value` (a resolved Verdict spelling like
    `COMPATIBLE_WITH_RISK`), not the raw `break`/`warn`/`risk`/`ignore` YAML
    spelling -- the schema's `to` enum must match what's actually emitted,
    not the YAML vocabulary. Skips cleanly if `jsonschema` isn't installed,
    matching this repo's existing schema-validation test convention."""
    import json

    pytest.importorskip("jsonschema")
    import jsonschema

    from abicheck.checker_types import DiffResult
    from abicheck.reporter import to_json

    p = tmp_path / "policy.yaml"
    p.write_text(
        """
reclassify:
  - kind: func_visibility_changed
    symbol: foo
    to: risk
""".strip(),
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = _change(ChangeKind.FUNC_VISIBILITY_CHANGED, "foo")
    diff = DiffResult(
        changes=[change], old_version="1", new_version="2", library="l",
        policy_file=pf,
    )
    report = json.loads(to_json(diff))
    assert report["policy_reclassify"][0]["to"] == "COMPATIBLE_WITH_RISK"

    schema = json.loads(
        Path("abicheck/schemas/compare_report.schema.json").read_text()
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    to_schema = schema["properties"]["policy_reclassify"]["items"]["properties"]["to"]
    validator = jsonschema.Draft202012Validator(to_schema)
    validator.validate(report["policy_reclassify"][0]["to"])
    with pytest.raises(jsonschema.exceptions.ValidationError):
        validator.validate("risk")  # the raw YAML spelling, not the schema's


class TestNoImportlibWorkaround:
    """ADR-063 D10 (implementation plan Phase 9): the cycle
    ``policy_file -> reclassify -> suppression -> checker_types ->
    policy_file`` that motivated ``_suppression_cls()``'s runtime
    ``importlib.import_module`` resolution no longer exists — ``reclassify.py``
    imports ``abicheck.policy.selectors`` (a leaf module with zero dependency
    on ``suppression.py``/``checker_types.py``/``policy_file.py``/
    ``finding_identity.py``) statically instead. Confirmed to fail against
    the pre-Phase-9 code, which had exactly one such call
    (``_suppression_cls()``, per that module's own docstring)."""

    def test_reclassify_module_contains_no_importlib_import_module_call(self) -> None:
        import ast
        import inspect

        import abicheck.reclassify as reclassify_module

        source = inspect.getsource(reclassify_module)
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ]
        assert not calls, (
            "reclassify.py must not resolve Suppression (or anything else) "
            "via importlib.import_module -- the import cycle that workaround "
            "existed for no longer exists (ADR-063 D10)"
        )

    def test_reclassify_module_does_not_import_importlib(self) -> None:
        import ast
        import inspect

        import abicheck.reclassify as reclassify_module

        source = inspect.getsource(reclassify_module)
        tree = ast.parse(source)
        imports_importlib = any(
            isinstance(node, ast.Import) and any(a.name == "importlib" for a in node.names)
            for node in ast.walk(tree)
        )
        assert not imports_importlib
