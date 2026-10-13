# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Direct tests of the shared report view (``abicheck.report.view``).

The renderers exercise these primitives end to end; these tests pin each
branch on its own so a regression shows up here first, not as a diff in one
format's golden output.
"""

from __future__ import annotations

import hashlib

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.report.view import (
    SHOW_ONLY_GROUP_SEP,
    ShowOnlyFilter,
    apply_show_only,
    display_changes,
    finding_id,
    group_changes_by_root_cause,
    parse_show_only_groups,
    render_show_only_cli_hint,
    resolve_scoped_gate_findings,
    root_cause_id_for_key,
    root_cause_key_and_display,
    show_only_matches,
    show_only_matches_severity_label,
)


def _result(changes: list[Change] | None = None) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=changes or [],
        verdict=Verdict.BREAKING,
        policy="strict_abi",
    )


# ---------------------------------------------------------------------------
# Identity and root-cause ids
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["", "a", "caused::by", "unicode-ü", "x" * 300])
def test_root_cause_id_is_sha256_prefix(key: str) -> None:
    # Oracle is hashlib directly, not the helper under test.
    expected = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    assert root_cause_id_for_key(key) == expected
    assert len(root_cause_id_for_key(key)) == 16


def test_finding_id_is_stable_across_calls() -> None:
    c = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
    assert finding_id(c) == finding_id(
        Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
    )


def test_finding_id_differs_for_different_kinds() -> None:
    a = Change(ChangeKind.FUNC_REMOVED, "foo", "x")
    b = Change(ChangeKind.FUNC_ADDED, "foo", "x")
    assert finding_id(a) != finding_id(b)


# ---------------------------------------------------------------------------
# Root-cause keying
# ---------------------------------------------------------------------------


def test_root_cause_key_prefers_caused_by_type() -> None:
    key, display = root_cause_key_and_display("Widget", "foo", "func_removed", "fid")
    assert key == display == "Widget"


def test_root_cause_key_groups_symbol_only_when_referenced() -> None:
    key, display = root_cause_key_and_display(
        None, "foo", "func_removed", "fid", referenced_causes=frozenset({"foo"})
    )
    assert key == display == "foo"


def test_root_cause_key_stays_singleton_for_unreferenced_symbol() -> None:
    key, display = root_cause_key_and_display(
        None, "foo", "func_removed", "fid", referenced_causes=frozenset()
    )
    assert key == "finding:fid"
    assert display == "foo"


def test_root_cause_key_falls_back_to_kind_without_symbol() -> None:
    key, display = root_cause_key_and_display(None, None, "enum_changed", "fid")
    assert key == "finding:fid"
    assert display == "enum_changed"


def test_group_changes_shares_a_root_cause_through_caused_by_type() -> None:
    a = Change(ChangeKind.FUNC_REMOVED, "f1", "a", caused_by_type="Widget")
    b = Change(ChangeKind.FUNC_REMOVED, "f2", "b", caused_by_type="Widget")
    c = Change(ChangeKind.FUNC_ADDED, "f3", "c")
    groups = group_changes_by_root_cause([a, b, c])
    assert [(key, display) for key, display, _ in groups] == [
        ("Widget", "Widget"),
        (f"finding:{finding_id(c)}", "f3"),
    ]
    assert groups[0][2] == [a, b]


def test_group_changes_keeps_independent_same_symbol_findings_apart() -> None:
    # Two independent findings that merely share a symbol must not merge:
    # only a producer-set caused_by_type correlates findings.
    a = Change(ChangeKind.FUNC_RETURN_CHANGED, "foo", "a")
    b = Change(ChangeKind.FUNC_PARAMS_CHANGED, "foo", "b")
    groups = group_changes_by_root_cause([a, b])
    assert len(groups) == 2


def test_group_changes_extra_causes_join_an_existing_group() -> None:
    # A symbol named only by a later-appended finding's caused_by_type must
    # still join the group it belongs to.
    a = Change(ChangeKind.FUNC_REMOVED, "foo", "a")
    groups = group_changes_by_root_cause([a], extra_causes=frozenset({"foo"}))
    assert [key for key, _, _ in groups] == ["foo"]


# ---------------------------------------------------------------------------
# --show-only grammar
# ---------------------------------------------------------------------------


def test_show_only_filter_parses_each_dimension() -> None:
    f = ShowOnlyFilter.parse("breaking,functions,removed")
    assert f.severities == frozenset({"breaking"})
    assert f.elements == frozenset({"functions"})
    assert f.actions == frozenset({"removed"})


def test_show_only_filter_ignores_empty_tokens_and_case() -> None:
    f = ShowOnlyFilter.parse(" Breaking , , Functions ")
    assert f.severities == frozenset({"breaking"})
    assert f.elements == frozenset({"functions"})


def test_show_only_filter_rejects_unknown_token() -> None:
    with pytest.raises(ValueError, match="Unknown --view show= token"):
        ShowOnlyFilter.parse("nonsense")


def test_parse_show_only_groups_splits_on_separator() -> None:
    groups = parse_show_only_groups(f"breaking{SHOW_ONLY_GROUP_SEP}functions")
    assert [g.severities for g in groups] == [frozenset({"breaking"}), frozenset()]
    assert [g.elements for g in groups] == [frozenset(), frozenset({"functions"})]


def test_render_show_only_cli_hint_one_view_per_group() -> None:
    assert render_show_only_cli_hint("breaking") == "--view show=breaking"
    assert (
        render_show_only_cli_hint(f"breaking{SHOW_ONLY_GROUP_SEP}removed")
        == "--view show=breaking --view show=removed"
    )


def test_show_only_matches_is_or_across_groups() -> None:
    removed = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
    added = Change(ChangeKind.FUNC_ADDED, "bar", "added")
    show = f"removed{SHOW_ONLY_GROUP_SEP}added"
    assert show_only_matches(show, removed)
    assert show_only_matches(show, added)
    assert not show_only_matches("removed", added)


def test_apply_show_only_keeps_matching_changes_in_order() -> None:
    removed = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
    added = Change(ChangeKind.FUNC_ADDED, "bar", "added")
    kept = apply_show_only([removed, added], "removed")
    assert kept == [removed]


def test_display_changes_without_filter_returns_all() -> None:
    a = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
    b = Change(ChangeKind.FUNC_ADDED, "bar", "added")
    out = display_changes(
        [a, b], None, policy="strict_abi", kind_sets=None, policy_file=None, today=None
    )
    assert out == [a, b]


def test_display_changes_filters_and_clears_dangling_reference() -> None:
    target = Change(ChangeKind.FUNC_ADDED, "bar", "added")
    dangling = Change(
        ChangeKind.FUNC_REMOVED,
        "foo",
        "removed",
        correlated_change_kind=ChangeKind.FUNC_ADDED,
    )
    # "removed" keeps only the dangling one; its target ``FUNC_ADDED`` is
    # hidden, so the correlation must be cleared on the displayed copy.
    out = display_changes(
        [target, dangling],
        "removed",
        policy="strict_abi",
        kind_sets=None,
        policy_file=None,
        today=None,
    )
    assert [(c.kind, c.symbol) for c in out] == [(ChangeKind.FUNC_REMOVED, "foo")]
    assert out[0].correlated_change_kind is None
    # The caller's own Change is not mutated.
    assert dangling.correlated_change_kind == ChangeKind.FUNC_ADDED


@pytest.mark.parametrize(
    ("show", "label", "expected"),
    [
        (None, "breaking", True),
        ("", "breaking", True),
        ("breaking", "breaking", True),
        ("breaking", "compatible", False),
        # A group with no severity token matches every label.
        ("functions", "compatible", True),
    ],
)
def test_show_only_matches_severity_label(show, label, expected) -> None:
    assert show_only_matches_severity_label(show, label) is expected


# ---------------------------------------------------------------------------
# Scoped gate resolution
# ---------------------------------------------------------------------------


def test_resolve_scoped_gate_findings_without_scope_is_empty() -> None:
    scoped, labels, blocks, _ = resolve_scoped_gate_findings(_result(), None, None)
    assert scoped == []
    assert labels == []
    assert blocks is True  # no severity config: a missing contract blocks


def test_resolve_scoped_gate_findings_drops_scoped_changes_already_present() -> None:
    present = Change(ChangeKind.FUNC_REMOVED, "foo", "removed")
    fresh = Change(ChangeKind.FUNC_ADDED, "bar", "added")
    result = _result([present])
    result.scoped_only_changes = [
        Change(ChangeKind.FUNC_REMOVED, "foo", "removed"),
        fresh,
    ]
    scoped, _, _, _ = resolve_scoped_gate_findings(result, None, None)
    assert scoped == [fresh]
