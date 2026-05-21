"""Tests for suppression file support."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from abicheck.checker import Change, ChangeKind, compare
from abicheck.suppression import Suppression, SuppressionList

# ─── helpers ──────────────────────────────────────────────────────────────────

def make_change(kind: ChangeKind, symbol: str, description: str = "desc") -> Change:
    return Change(kind=kind, symbol=symbol, description=description)


def write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "suppressions.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _make_snapshots_with_removed_func(mangled: str = "_ZN3foo3barEv"):
    """Return (old, new) where old has one public function that new doesn't."""
    from abicheck.model import AbiSnapshot, Function, Visibility
    old = AbiSnapshot(library="libfoo", version="1.0")
    new = AbiSnapshot(library="libfoo", version="2.0")
    old.functions.append(Function(
        name="foo::bar", mangled=mangled,
        return_type="void", visibility=Visibility.PUBLIC,
    ))
    return old, new


# ─── load valid suppression file ─────────────────────────────────────────────

def test_load_valid_file(tmp_path: Path) -> None:
    yaml_path = write_yaml(tmp_path, """
        version: 1
        suppressions:
          - symbol: "_ZN3foo3barEv"
            change_kind: "func_removed"
            reason: "intentional"
          - symbol_pattern: ".*detail.*"
            reason: "internal namespace"
    """)
    sl = SuppressionList.load(yaml_path)
    assert len(sl) == 2


def test_load_empty_suppressions(tmp_path: Path) -> None:
    yaml_path = write_yaml(tmp_path, "version: 1\nsuppressions: []\n")
    sl = SuppressionList.load(yaml_path)
    assert len(sl) == 0


def test_load_missing_suppressions_key(tmp_path: Path) -> None:
    yaml_path = write_yaml(tmp_path, "version: 1\n")
    sl = SuppressionList.load(yaml_path)
    assert len(sl) == 0


# ─── exact symbol match ───────────────────────────────────────────────────────

def test_exact_symbol_match() -> None:
    sup = Suppression(symbol="_ZN3foo3barEv")
    assert sup.matches(make_change(ChangeKind.FUNC_REMOVED, "_ZN3foo3barEv"))


def test_exact_symbol_no_match() -> None:
    sup = Suppression(symbol="_ZN3foo3barEv")
    assert not sup.matches(make_change(ChangeKind.FUNC_REMOVED, "_ZN3foo3bazEv"))


# ─── pattern match (fullmatch semantics) ─────────────────────────────────────

def test_pattern_fullmatch() -> None:
    # Pattern must cover the whole symbol — '.*detail.*' requires explicit wildcards
    sup = Suppression(symbol_pattern=".*detail.*")
    assert sup.matches(make_change(ChangeKind.FUNC_REMOVED, "_ZN3fooNdetailE3barEv"))


def test_pattern_no_match() -> None:
    sup = Suppression(symbol_pattern=".*detail.*")
    assert not sup.matches(make_change(ChangeKind.FUNC_REMOVED, "_ZN3foo3barEv"))


def test_pattern_short_does_not_over_match() -> None:
    """Short pattern without anchors should NOT match an unrelated symbol (fullmatch)."""
    sup = Suppression(symbol_pattern="foo")
    # fullmatch: 'foo' does not match '_ZN3foo3barEv'
    assert not sup.matches(make_change(ChangeKind.FUNC_REMOVED, "_ZN3foo3barEv"))


# ─── change_kind filtering ────────────────────────────────────────────────────

def test_change_kind_match() -> None:
    sup = Suppression(symbol="_ZN3foo3barEv", change_kind="func_removed")
    assert sup.matches(make_change(ChangeKind.FUNC_REMOVED, "_ZN3foo3barEv"))


def test_change_kind_mismatch() -> None:
    """Symbol matches but change_kind does not — should NOT be suppressed."""
    sup = Suppression(symbol="_ZN3foo3barEv", change_kind="func_removed")
    assert not sup.matches(make_change(ChangeKind.FUNC_RETURN_CHANGED, "_ZN3foo3barEv"))


# ─── suppressed_changes audit trail in DiffResult ────────────────────────────

def test_suppressed_changes_audit_trail(tmp_path: Path) -> None:
    """suppressed_changes must contain the full Change objects, not just count."""
    yaml_path = write_yaml(tmp_path, """
        version: 1
        suppressions:
          - symbol: "_ZN3foo3barEv"
            change_kind: "func_removed"
    """)
    sl = SuppressionList.load(yaml_path)
    old, new = _make_snapshots_with_removed_func("_ZN3foo3barEv")
    result = compare(old, new, suppression=sl)

    assert result.suppressed_count == 1
    assert len(result.suppressed_changes) == 1
    assert result.suppressed_changes[0].symbol == "_ZN3foo3barEv"
    assert result.suppressed_changes[0].kind == ChangeKind.FUNC_REMOVED
    # Suppressed change is absent from main changes list
    assert all(c.symbol != "_ZN3foo3barEv" for c in result.changes)


def test_suppression_file_provided_flag(tmp_path: Path) -> None:
    """suppression_file_provided=True even when 0 rules matched."""
    yaml_path = write_yaml(tmp_path, "version: 1\nsuppressions: []\n")
    sl = SuppressionList.load(yaml_path)
    old, new = _make_snapshots_with_removed_func()
    result = compare(old, new, suppression=sl)
    assert result.suppression_file_provided is True
    assert result.suppressed_count == 0
    assert result.suppressed_changes == []


def test_no_suppression_flag_when_not_provided() -> None:
    old, new = _make_snapshots_with_removed_func()
    result = compare(old, new)
    assert result.suppression_file_provided is False
    assert result.suppressed_count == 0
    assert result.suppressed_changes == []


# ─── reporter: markdown footer ───────────────────────────────────────────────

def test_markdown_footer_with_suppressions(tmp_path: Path) -> None:
    from abicheck.reporter import to_markdown
    yaml_path = write_yaml(tmp_path, """
        version: 1
        suppressions:
          - symbol: "_ZN3foo3barEv"
    """)
    sl = SuppressionList.load(yaml_path)
    old, new = _make_snapshots_with_removed_func("_ZN3foo3barEv")
    result = compare(old, new, suppression=sl)
    md = to_markdown(result)
    assert "1 change(s) suppressed" in md
    assert "_ZN3foo3barEv" in md


def test_markdown_footer_zero_suppressed(tmp_path: Path) -> None:
    from abicheck.reporter import to_markdown
    yaml_path = write_yaml(tmp_path, "version: 1\nsuppressions: []\n")
    sl = SuppressionList.load(yaml_path)
    old, new = _make_snapshots_with_removed_func()
    result = compare(old, new, suppression=sl)
    md = to_markdown(result)
    assert "0 changes matched" in md


# ─── reporter: JSON suppression section ──────────────────────────────────────

def test_json_includes_suppression_section(tmp_path: Path) -> None:
    import json

    from abicheck.reporter import to_json
    yaml_path = write_yaml(tmp_path, """
        version: 1
        suppressions:
          - symbol: "_ZN3foo3barEv"
    """)
    sl = SuppressionList.load(yaml_path)
    old, new = _make_snapshots_with_removed_func("_ZN3foo3barEv")
    result = compare(old, new, suppression=sl)
    d = json.loads(to_json(result))
    assert "suppression" in d
    assert d["suppression"]["file_provided"] is True
    assert d["suppression"]["suppressed_count"] == 1
    assert d["suppression"]["suppressed_changes"][0]["symbol"] == "_ZN3foo3barEv"


# ─── validation: bad inputs ──────────────────────────────────────────────────

def test_unsupported_version_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 2\nsuppressions: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        SuppressionList.load(bad)


def test_missing_symbol_raises() -> None:
    with pytest.raises(ValueError):
        Suppression(symbol=None, symbol_pattern=None)


def test_both_symbol_and_pattern_raises() -> None:
    with pytest.raises(ValueError):
        Suppression(symbol="foo", symbol_pattern="bar")


def test_invalid_yaml_syntax(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(": :\n  - bad: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError):
        SuppressionList.load(bad)


def test_invalid_change_kind_raises() -> None:
    with pytest.raises(ValueError, match="Unknown change_kind"):
        Suppression(symbol="foo", change_kind="totally_wrong_kind")


def test_invalid_regex_raises() -> None:
    with pytest.raises(ValueError, match="Invalid symbol_pattern"):
        Suppression(symbol_pattern="[unclosed")


def test_unknown_yaml_key_raises(tmp_path: Path) -> None:
    bad = write_yaml(tmp_path, """
        version: 1
        suppressions:
          - symbl: "_ZN3foo3barEv"
            reason: "typo in key"
    """)
    with pytest.raises(ValueError, match="unknown key"):
        SuppressionList.load(bad)


def test_type_pattern_non_type_change_not_matched() -> None:
    """type_pattern must not suppress symbol-level changes."""
    sup = Suppression(type_pattern="Foo")
    c = make_change(ChangeKind.FUNC_REMOVED, "Foo")
    assert not sup.matches(c)


def test_type_pattern_with_change_kind_mismatch_not_matched() -> None:
    sup = Suppression(type_pattern="Err", change_kind="enum_member_removed")
    c = make_change(ChangeKind.ENUM_MEMBER_ADDED, "Err")
    assert not sup.matches(c)


# ─── member_name selector ────────────────────────────────────────────────────

def test_member_name_matches_bare_typedef_symbol() -> None:
    """Typedef removals use the bare alias as the symbol — member_name should match it."""
    sup = Suppression(member_name="value_type", change_kind="typedef_removed")
    c = make_change(ChangeKind.TYPEDEF_REMOVED, "value_type")
    assert sup.matches(c)


def test_member_name_matches_last_qualified_segment() -> None:
    sup = Suppression(member_name="value_type", change_kind="typedef_removed")
    c = make_change(ChangeKind.TYPEDEF_REMOVED, "std::allocator::value_type")
    assert sup.matches(c)


def test_member_name_does_not_match_other_member() -> None:
    sup = Suppression(member_name="value_type", change_kind="typedef_removed")
    c = make_change(ChangeKind.TYPEDEF_REMOVED, "size_type")
    assert not sup.matches(c)


def test_member_name_uses_fullmatch_not_substring() -> None:
    sup = Suppression(member_name="value", change_kind="typedef_removed")
    c = make_change(ChangeKind.TYPEDEF_REMOVED, "value_type")
    assert not sup.matches(c)


def test_member_name_combines_with_type_pattern() -> None:
    """member_name + type_pattern is the canonical noise-control combo."""
    sup = Suppression(
        type_pattern="Allocator",
        member_name="value_type",
        change_kind="typedef_removed",
    )
    # Symbol shape from diff_types when typedef is qualified by its container.
    c = make_change(ChangeKind.TYPEDEF_REMOVED, "Allocator::value_type")
    assert sup.matches(c)
    # Different container — type_pattern blocks the match.
    c2 = make_change(ChangeKind.TYPEDEF_REMOVED, "OtherType::value_type")
    assert not sup.matches(c2)


def test_member_name_change_kind_mismatch_not_matched() -> None:
    sup = Suppression(member_name="value_type", change_kind="typedef_removed")
    c = make_change(ChangeKind.TYPEDEF_BASE_CHANGED, "value_type")
    assert not sup.matches(c)


def test_member_name_regex_pattern() -> None:
    sup = Suppression(member_name=r"(value|size)_type")
    assert sup.matches(make_change(ChangeKind.TYPEDEF_REMOVED, "value_type"))
    assert sup.matches(make_change(ChangeKind.TYPEDEF_REMOVED, "size_type"))
    assert not sup.matches(make_change(ChangeKind.TYPEDEF_REMOVED, "pointer"))


def test_member_name_invalid_regex_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid member_name"):
        Suppression(member_name="(unclosed")


def test_member_name_rejects_combination_with_symbol() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        Suppression(symbol="foo", member_name="bar")


def test_member_name_rejects_combination_with_symbol_pattern() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        Suppression(symbol_pattern="foo.*", member_name="bar")


def test_member_name_counts_as_selector() -> None:
    """member_name alone satisfies the 'at least one selector' requirement."""
    sup = Suppression(member_name="value_type")
    assert sup.matches(make_change(ChangeKind.TYPEDEF_REMOVED, "value_type"))


def test_member_name_loaded_from_yaml(tmp_path: Path) -> None:
    yaml_path = write_yaml(tmp_path, """
        version: 1
        suppressions:
          - member_name: "value_type"
            change_kind: "typedef_removed"
            reason: "stdlib boilerplate"
    """)
    sl = SuppressionList.load(yaml_path)
    assert len(sl) == 1
    assert sl.is_suppressed(make_change(ChangeKind.TYPEDEF_REMOVED, "value_type"))


def test_rules_by_label_and_repr() -> None:
    sl = SuppressionList([
        Suppression(symbol="a", label="x"),
        Suppression(symbol="b", label="x"),
        Suppression(symbol="c", label="y"),
    ])
    assert len(sl.rules_by_label("x")) == 2
    assert len(sl.rules_by_label("y")) == 1
    assert "SuppressionList(3 rules)" in repr(sl)


def test_merge_combines_lists() -> None:
    a = SuppressionList([Suppression(symbol="a")])
    b = SuppressionList([Suppression(symbol="b")])
    m = SuppressionList.merge(a, b)
    assert len(m) == 2


def test_suppressions_must_be_list(tmp_path: Path) -> None:
    bad = write_yaml(tmp_path, """
        version: 1
        suppressions: {}
    """)
    with pytest.raises(ValueError, match="must be a list"):
        SuppressionList.load(bad)


def test_entry_must_be_mapping(tmp_path: Path) -> None:
    bad = write_yaml(tmp_path, """
        version: 1
        suppressions:
          - not-a-mapping
    """)
    with pytest.raises(ValueError, match="must be a mapping"):
        SuppressionList.load(bad)


def test_invalid_expires_date_raises(tmp_path: Path) -> None:
    bad = write_yaml(tmp_path, """
        version: 1
        suppressions:
          - symbol: foo
            expires: not-a-date
    """)
    with pytest.raises(ValueError, match="invalid 'expires' date"):
        SuppressionList.load(bad)
