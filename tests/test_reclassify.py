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
