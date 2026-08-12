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

    assert classify_effective_change(scoped, policy_file=pf) == IssueCategory.ADDITION
    # An unrelated func_added symbol still falls through to the (broader)
    # override -- BREAKING, not silently granted the same leniency.
    assert (
        classify_effective_change(unscoped, policy_file=pf)
        == IssueCategory.ABI_BREAKING
    )

    cfg = SeverityConfig(
        quality_issues=SeverityLevel.ERROR, addition=SeverityLevel.INFO
    )
    assert compute_exit_code([scoped], cfg, policy_file=pf) == 0
    assert compute_exit_code([scoped], PRESET_DEFAULT, policy_file=pf) == 0


# --- standard-report disclosure (reporter.py's policy_reclassify key) ------


def test_active_reclassify_rules_are_disclosed_in_the_standard_report(
    tmp_path: Path,
) -> None:
    """Codex review: an ordinary comparison reclassifying a finding had no
    trace of the active `reclassify:` rule anywhere in the standard JSON
    report. `policy_reclassify` (report_schema_version 2.29) now lists the
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
    every pre-existing report byte-identical (schema 2.29's own
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
