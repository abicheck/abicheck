"""Matrix tests for suppression / filter scenarios.

Tests the Cartesian product of:
  - Suppression selector: symbol (exact), symbol_pattern (regex), type_pattern
  - Change kind: func_removed, type_size_changed, enum_member_removed
  - Combinations: added, removed, negative (no match expected)

If additional suppression APIs are unavailable, individual tests are
scaffolded with TODO markers and skip directives.
"""

from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.suppression import SuppressionList

# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _snap(ver: str = "1.0", funcs=None, types=None, enums=None) -> AbiSnapshot:
    s = AbiSnapshot(library="libtest.so", version=ver)
    s.functions = funcs or []
    s.types = types or []
    s.enums = enums or []
    return s


def _suppression_from_yaml(tmp_path: Path, content: str) -> SuppressionList:
    p = tmp_path / "suppressions.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return SuppressionList.load(p)


def _make_removed_func_snaps(mangled: str = "_Z6helperi"):
    old = _snap("1.0", funcs=[_fn("compute", "_Z7computei"), _fn("helper", mangled)])
    new = _snap("2.0", funcs=[_fn("compute", "_Z7computei")])
    return old, new


def _make_struct_change_snaps():
    old = _snap(
        "1.0",
        types=[
            RecordType(
                name="Packet",
                kind="struct",
                size_bits=64,
                fields=[TypeField("x", "int", 0), TypeField("y", "int", 32)],
            )
        ],
    )
    new = _snap(
        "2.0",
        types=[
            RecordType(
                name="Packet",
                kind="struct",
                size_bits=96,
                fields=[
                    TypeField("x", "int", 0),
                    TypeField("y", "int", 32),
                    TypeField("z", "int", 64),
                ],
            )
        ],
    )
    return old, new


def _make_enum_change_snaps():
    old = _snap(
        "1.0",
        enums=[
            EnumType(
                name="Status",
                members=[
                    EnumMember("OK", 0),
                    EnumMember("FAIL", 1),
                    EnumMember("RETRY", 2),
                ],
            )
        ],
    )
    new = _snap(
        "2.0",
        enums=[
            EnumType(
                name="Status",
                members=[EnumMember("OK", 0), EnumMember("RETRY", 2)],  # FAIL removed
            )
        ],
    )
    return old, new


# ===========================================================================
# 1. symbol (exact match) suppression
# ===========================================================================


class TestSymbolExactSuppression:
    """Exact symbol name suppression."""

    def test_symbol_exact_suppresses_func_removed(self, tmp_path: Path) -> None:
        """Exact symbol match suppresses the targeted func_removed change."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_Z6helperi"
                change_kind: "func_removed"
                reason: "intentionally removed in v2"
        """,
        )
        old, new = _make_removed_func_snaps("_Z6helperi")
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 1
        assert result.verdict.value in ("NO_CHANGE", "COMPATIBLE"), (
            f"Expected suppression to eliminate break, got {result.verdict.value}"
        )

    def test_symbol_exact_no_match_different_symbol(self, tmp_path: Path) -> None:
        """Exact symbol mismatch → change is NOT suppressed."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_Z9otherFunci"
                change_kind: "func_removed"
                reason: "different symbol"
        """,
        )
        old, new = _make_removed_func_snaps("_Z6helperi")
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 0
        assert result.verdict.value == "BREAKING"

    def test_symbol_exact_without_change_kind_filter(self, tmp_path: Path) -> None:
        """Symbol exact without change_kind → matches any change for that symbol."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_Z6helperi"
                reason: "catch-all for helper"
        """,
        )
        old, new = _make_removed_func_snaps("_Z6helperi")
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 1

    def test_symbol_exact_wrong_change_kind_no_suppress(self, tmp_path: Path) -> None:
        """Symbol exact but wrong change_kind → NOT suppressed."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_Z6helperi"
                change_kind: "type_size_changed"
                reason: "wrong kind"
        """,
        )
        old, new = _make_removed_func_snaps("_Z6helperi")
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 0
        assert result.verdict.value == "BREAKING"


# ===========================================================================
# 2. symbol_pattern (regex) suppression
# ===========================================================================


class TestSymbolPatternSuppression:
    """Regex pattern suppression via symbol_pattern."""

    def test_pattern_suppresses_matching_symbol(self, tmp_path: Path) -> None:
        """Pattern matching the symbol mangled name suppresses the change."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol_pattern: ".*helper.*"
                reason: "suppress all helper symbols"
        """,
        )
        old, new = _make_removed_func_snaps("_Z6helperi")
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 1

    def test_pattern_does_not_match_unrelated_symbol(self, tmp_path: Path) -> None:
        """Pattern that doesn't match → no suppression."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol_pattern: ".*internal.*"
                reason: "only internal symbols"
        """,
        )
        old, new = _make_removed_func_snaps("_Z6helperi")
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 0
        assert result.verdict.value == "BREAKING"

    def test_pattern_fullmatch_semantics(self, tmp_path: Path) -> None:
        """symbol_pattern uses fullmatch — partial prefix does NOT match."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol_pattern: "_Z6"
                reason: "prefix only — should NOT match (fullmatch semantics)"
        """,
        )
        old, new = _make_removed_func_snaps("_Z6helperi")
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 0

    def test_pattern_with_change_kind_filter(self, tmp_path: Path) -> None:
        """Pattern + change_kind: both must match."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol_pattern: ".*helper.*"
                change_kind: "func_removed"
                reason: "helper func_removed only"
        """,
        )
        old, new = _make_removed_func_snaps("_Z6helperi")
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 1


# ===========================================================================
# 3. type_pattern suppression
# ===========================================================================


class TestTypePatternSuppression:
    """type_pattern only matches type-level change kinds."""

    def test_type_pattern_suppresses_struct_size_change(self, tmp_path: Path) -> None:
        """type_pattern matching struct name suppresses TYPE_SIZE_CHANGED."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - type_pattern: "Packet"
                reason: "Packet layout intentionally extended"
        """,
        )
        old, new = _make_struct_change_snaps()
        result = compare(old, new, suppression=sl)
        # At least one type change suppressed
        assert result.suppressed_count > 0

    def test_type_pattern_does_not_suppress_func_removed(self, tmp_path: Path) -> None:
        """type_pattern does NOT suppress func_removed (symbol-level change)."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - type_pattern: ".*"
                reason: "wildcard type pattern"
        """,
        )
        old, new = _make_removed_func_snaps()
        result = compare(old, new, suppression=sl)
        # func_removed is NOT a type change kind, should not be suppressed
        assert result.suppressed_count == 0
        assert result.verdict.value == "BREAKING"

    def test_type_pattern_with_change_kind_filter(self, tmp_path: Path) -> None:
        """type_pattern + change_kind: both must match."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - type_pattern: "Packet"
                change_kind: "type_size_changed"
                reason: "only size change suppressed"
        """,
        )
        old, new = _make_struct_change_snaps()
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count >= 1

    def test_type_pattern_enum_suppression(self, tmp_path: Path) -> None:
        """type_pattern matches enum changes (enum_member_removed)."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - type_pattern: "Status"
                reason: "Status enum member removed intentionally"
        """,
        )
        old, new = _make_enum_change_snaps()
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count >= 1


# ===========================================================================
# 4. change-kind filter in isolation
# ===========================================================================


class TestChangeKindFilter:
    """change_kind as primary filter axis."""

    def test_change_kind_func_removed_matches_only_func_removed(
        self, tmp_path: Path
    ) -> None:
        """change_kind: func_removed targets only func_removed changes."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol_pattern: ".*"
                change_kind: "func_removed"
                reason: "all func_removed changes"
        """,
        )
        old, new = _make_removed_func_snaps()
        result = compare(old, new, suppression=sl)
        # All func_removed changes suppressed by wildcard + change_kind filter
        assert all(c.kind != ChangeKind.FUNC_REMOVED for c in result.changes), (
            "func_removed changes should be suppressed"
        )

    def test_change_kind_does_not_suppress_other_kinds(self, tmp_path: Path) -> None:
        """Suppression with change_kind=func_removed doesn't affect struct changes."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol_pattern: ".*"
                change_kind: "func_removed"
                reason: "only func_removed"
        """,
        )
        old, new = _make_struct_change_snaps()
        result = compare(old, new, suppression=sl)
        # Struct size changes are NOT func_removed → should remain
        assert result.suppressed_count == 0
        assert result.verdict.value == "BREAKING"


# ===========================================================================
# 5. Multiple suppressions / added scenarios
# ===========================================================================


class TestMultipleSuppressionsAdded:
    """Multiple suppression rules and added-change scenarios."""

    def test_multiple_rules_each_match_once(self, tmp_path: Path) -> None:
        """Two rules each matching one symbol independently."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_Z6helperi"
                change_kind: "func_removed"
                reason: "helper removed"
              - symbol: "_Z7computei"
                change_kind: "func_removed"
                reason: "compute removed"
        """,
        )
        old = _snap(
            "1.0", funcs=[_fn("compute", "_Z7computei"), _fn("helper", "_Z6helperi")]
        )
        new = _snap("2.0")  # both removed
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 2

    def test_suppressed_changes_audit_trail(self, tmp_path: Path) -> None:
        """Suppressed changes are recorded in suppressed_changes audit trail."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_Z6helperi"
                reason: "audit test"
        """,
        )
        old, new = _make_removed_func_snaps()
        result = compare(old, new, suppression=sl)
        assert result.suppression_file_provided is True
        assert len(result.suppressed_changes) == result.suppressed_count


# ===========================================================================
# 6. Removed-change scenarios
# ===========================================================================


class TestRemovedChangeScenarios:
    """Suppression scenarios involving removed symbols / types."""

    def test_suppress_var_removed(self, tmp_path: Path) -> None:
        """Variable removal can be suppressed."""
        from abicheck.model import Variable

        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_ZN3foo6g_varE"
                change_kind: "var_removed"
                reason: "global var intentionally removed"
        """,
        )
        old = _snap("1.0")
        old.variables.append(
            Variable(
                name="foo::g_var",
                mangled="_ZN3foo6g_varE",
                type="int",
                visibility=Visibility.PUBLIC,
            )
        )
        new = _snap("2.0")
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 1

    def test_suppress_type_removed(self, tmp_path: Path) -> None:
        """Type removal can be suppressed with type_pattern."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - type_pattern: "OldConfig"
                reason: "OldConfig type removed intentionally"
        """,
        )
        old = _snap(
            "1.0",
            types=[
                RecordType(
                    name="OldConfig",
                    kind="struct",
                    size_bits=32,
                    fields=[TypeField("val", "int", 0)],
                )
            ],
        )
        new = _snap("2.0")
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count >= 1


# ===========================================================================
# 7. Negative scenarios — suppression should NOT match
# ===========================================================================


class TestNegativeSuppressionScenarios:
    """Ensure suppression rules do NOT over-suppress."""

    def test_no_suppression_different_change_kind(self, tmp_path: Path) -> None:
        """Suppression for wrong change_kind leaves change unsuppressed."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_Z6helperi"
                change_kind: "func_params_changed"
                reason: "only for param changes, not removal"
        """,
        )
        old, new = _make_removed_func_snaps()
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 0
        assert result.verdict.value == "BREAKING"

    def test_no_suppression_partial_pattern(self, tmp_path: Path) -> None:
        """Partial (non-fullmatch) pattern doesn't accidentally match."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol_pattern: "helper"
                reason: "short pattern without anchors - fullmatch fails"
        """,
        )
        old, new = _make_removed_func_snaps("_Z6helperi")
        result = compare(old, new, suppression=sl)
        # "helper" does not fullmatch "_Z6helperi"
        assert result.suppressed_count == 0

    def test_empty_suppression_list_no_effect(self, tmp_path: Path) -> None:
        """Empty suppression list → no changes suppressed."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions: []
        """,
        )
        old, new = _make_removed_func_snaps()
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 0
        assert result.verdict.value == "BREAKING"


# ===========================================================================
# 8. Scaffold / TODO — advanced suppression APIs (if not yet implemented)
# ===========================================================================


class TestAdvancedSuppressionScaffold:
    """Tests for advanced suppression features: label, source_location, expires."""

    def test_label_based_suppression(self, tmp_path: Path) -> None:
        """label field is stored and retrievable; label does not affect matching."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: _Z6helperi
                reason: tracked internally
                label: workaround
        """,
        )
        # label-based retrieval
        rules = sl.rules_by_label("workaround")
        assert len(rules) == 1
        assert rules[0].label == "workaround"

        # label doesn't change match behaviour — the rule still suppresses the change
        old, new = _make_removed_func_snaps()
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 1
        assert result.verdict.value in ("COMPATIBLE", "NO_CHANGE")

    def test_label_non_matching_does_not_suppress(self, tmp_path: Path) -> None:
        """A suppression with a label that targets a different symbol doesn't suppress."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: _Z9otherFuncv
                label: other_label
        """,
        )
        old, new = _make_removed_func_snaps()
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 0

    def test_file_scoped_suppression(self, tmp_path: Path) -> None:
        """source_location glob suppresses changes from matching headers."""
        from abicheck.checker import Change, ChangeKind
        from abicheck.suppression import Suppression, SuppressionList

        change_in_scope = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
            source_location="/project/internal/detail.h:42",
        )
        change_out_of_scope = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3barv",
            description="removed",
            source_location="/project/public/api.h:10",
        )

        sl = SuppressionList(
            suppressions=[
                Suppression(
                    source_location="*/internal/*",
                    reason="internal headers are not part of public ABI",
                )
            ]
        )

        assert sl.is_suppressed(change_in_scope), "internal change should be suppressed"
        assert not sl.is_suppressed(change_out_of_scope), (
            "public change should not be suppressed"
        )

    def test_file_scoped_suppression_no_source_location(self, tmp_path: Path) -> None:
        """source_location rule does not suppress changes with no source_location set."""
        from abicheck.checker import Change, ChangeKind
        from abicheck.suppression import Suppression, SuppressionList

        change_no_src = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
            source_location=None,
        )
        sl = SuppressionList(suppressions=[Suppression(source_location="*/internal/*")])
        assert not sl.is_suppressed(change_no_src)

    def test_suppression_with_expiry_date(self, tmp_path: Path) -> None:
        """Suppression with future expires date is active; past expires date is inactive."""
        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: _Z6helperi
                expires: "2099-12-31"
                reason: expires far in future
        """,
        )
        old, new = _make_removed_func_snaps()

        # Future expiry → still active
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 1, (
            "future-expiry suppression should be active"
        )

    def test_suppression_expired_does_not_suppress(self, tmp_path: Path) -> None:
        """Suppression past its expiry date does not suppress changes."""
        from abicheck.checker import Change, ChangeKind
        from abicheck.suppression import Suppression, SuppressionList

        past = date(2020, 1, 1)
        sup = Suppression(symbol="_Z6helperi", expires=past)
        sl = SuppressionList([sup])

        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z6helperi",
            description="removed",
        )
        assert not sl.is_suppressed(change), "expired suppression should not suppress"

    def test_suppression_expired_is_reported(self, tmp_path: Path) -> None:
        """expired_rules() returns rules past their expiry date."""
        from abicheck.suppression import Suppression, SuppressionList

        past = date(2020, 1, 1)
        future = date(2099, 1, 1)
        sl = SuppressionList(
            [
                Suppression(symbol="_Zfoo", expires=past, reason="old workaround"),
                Suppression(
                    symbol="_Zbar", expires=future, reason="current workaround"
                ),
                Suppression(symbol="_Zbaz"),
            ]
        )
        expired = sl.expired_rules()
        assert len(expired) == 1
        assert expired[0].symbol == "_Zfoo"

    def test_expires_invalid_date_raises(self, tmp_path: Path) -> None:
        """Invalid expires date in YAML raises ValueError."""
        with pytest.raises(ValueError, match="invalid 'expires' date"):
            _suppression_from_yaml(
                tmp_path,
                """
                version: 1
                suppressions:
                  - symbol: _Z3foov
                    expires: "not-a-date"
            """,
            )

    def test_source_location_combines_with_symbol_selector(
        self, tmp_path: Path
    ) -> None:
        """source_location must be conjunctive with symbol/symbol_pattern selectors."""
        from abicheck.checker import Change, ChangeKind
        from abicheck.suppression import Suppression, SuppressionList

        sl = SuppressionList(
            suppressions=[
                Suppression(
                    symbol="_Z3foov",
                    source_location="*/internal/*",
                    reason="only specific symbol in internal headers",
                )
            ]
        )

        # In-scope symbol and file => suppressed
        c1 = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
            source_location="/project/internal/a.h:12",
        )
        # In-scope file but different symbol => must NOT suppress
        c2 = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3barv",
            description="removed",
            source_location="/project/internal/a.h:13",
        )

        assert sl.is_suppressed(c1)
        assert not sl.is_suppressed(c2)

    def test_expires_unquoted_timestamp_normalized_to_date(
        self, tmp_path: Path
    ) -> None:
        """YAML datetime values for expires are normalized to date (no TypeError in compare)."""
        from datetime import datetime as _dt

        sl = _suppression_from_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: _Z6helperi
                expires: 2099-12-31T00:00:00
        """,
        )

        # Load path should normalize datetime -> date
        expires = sl._suppressions[0].expires  # noqa: SLF001 - intentional white-box test
        assert expires is not None
        assert not isinstance(expires, _dt)

        old, new = _make_removed_func_snaps()
        result = compare(old, new, suppression=sl)
        assert result.suppressed_count == 1

    def test_source_location_glob_matches_path_without_line_suffix(
        self, tmp_path: Path
    ) -> None:
        """source_location glob should match file path even when change carries :line suffix."""
        from abicheck.checker import Change, ChangeKind
        from abicheck.suppression import Suppression, SuppressionList

        c = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z3foov",
            description="removed",
            source_location="/project/internal/detail.h:42",
        )
        sl = SuppressionList([Suppression(source_location="*/internal/*.h")])
        assert sl.is_suppressed(c)
