"""Tests for suppression lifecycle enforcement features.

Covers:
- --strict-suppressions (fail on expired rules)
- --require-justification (require reason field)
- suggest-suppressions command
"""
from __future__ import annotations

import json
import textwrap
from datetime import date, timedelta
from pathlib import Path

import pytest

from abicheck.suppression import SuppressionList, suggest_suppressions

# ─── helpers ──────────────────────────────────────────────────────────────────

def write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "suppressions.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ─── --strict-suppressions: check_expired_strict ─────────────────────────────

class TestStrictSuppressions:

    def test_no_expired_rules_returns_empty(self, tmp_path: Path) -> None:
        future = date.today() + timedelta(days=90)
        yaml_path = write_yaml(tmp_path, f"""
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "intentional"
                expires: "{future.isoformat()}"
        """)
        sl = SuppressionList.load(yaml_path)
        assert sl.check_expired_strict() == []

    def test_expired_rules_returns_indexed_pairs(self, tmp_path: Path) -> None:
        past = date.today() - timedelta(days=30)
        future = date.today() + timedelta(days=90)
        yaml_path = write_yaml(tmp_path, f"""
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "still valid"
                expires: "{future.isoformat()}"
              - symbol_pattern: "_ZN3foo.*Internal.*"
                reason: "expired one"
                expires: "{past.isoformat()}"
              - symbol: "_ZN3bar6legacyEv"
                reason: "also expired"
                expires: "{past.isoformat()}"
        """)
        sl = SuppressionList.load(yaml_path)
        expired = sl.check_expired_strict()
        assert len(expired) == 2
        # check_expired_strict returns 0-based indices; CLI adds 1 for display
        assert expired[0][0] == 1  # 0-based index of second rule
        assert expired[1][0] == 2  # 0-based index of third rule
        assert expired[0][1].symbol_pattern == "_ZN3foo.*Internal.*"
        assert expired[1][1].symbol == "_ZN3bar6legacyEv"

    def test_rules_without_expiry_not_flagged(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(tmp_path, """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "no expiry"
        """)
        sl = SuppressionList.load(yaml_path)
        assert sl.check_expired_strict() == []

    def test_custom_today_date(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(tmp_path, """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "expires mid-year"
                expires: "2026-06-15"
        """)
        sl = SuppressionList.load(yaml_path)
        # Before expiry
        assert sl.check_expired_strict(today=date(2026, 6, 14)) == []
        # On expiry day — not expired (check is >)
        assert sl.check_expired_strict(today=date(2026, 6, 15)) == []
        # After expiry
        expired = sl.check_expired_strict(today=date(2026, 6, 16))
        assert len(expired) == 1


# ─── --require-justification ─────────────────────────────────────────────────

class TestRequireJustification:

    def test_all_rules_have_reason_passes(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(tmp_path, """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "intentional removal for v2"
              - symbol_pattern: ".*internal.*"
                reason: "internal namespace"
        """)
        sl = SuppressionList.load(yaml_path, require_justification=True)
        assert len(sl) == 2

    def test_missing_reason_raises(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(tmp_path, """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "has reason"
              - symbol: "_ZN3baz3quxEv"
              - symbol_pattern: ".*detail.*"
                reason: "internal"
        """)
        with pytest.raises(ValueError, match=r"rule 1.*no 'reason' field"):
            SuppressionList.load(yaml_path, require_justification=True)

    def test_empty_reason_raises(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(tmp_path, """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: ""
        """)
        with pytest.raises(ValueError, match=r"rule 0.*no 'reason' field"):
            SuppressionList.load(yaml_path, require_justification=True)

    def test_without_flag_missing_reason_ok(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(tmp_path, """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
        """)
        sl = SuppressionList.load(yaml_path, require_justification=False)
        assert len(sl) == 1


# ─── suggest-suppressions ────────────────────────────────────────────────────

class TestSuggestSuppressions:

    def test_basic_func_removed(self) -> None:
        changes = [
            {"kind": "func_removed", "symbol": "_ZN3foo6legacyEv"},
        ]
        yaml_text = suggest_suppressions(changes, today=date(2026, 3, 23))
        assert 'symbol: "_ZN3foo6legacyEv"' in yaml_text
        assert 'change_kind: "func_removed"' in yaml_text
        assert "reason:" in yaml_text
        assert "TODO" in yaml_text
        assert 'expires: "2026-09-19"' in yaml_text  # 180 days from 2026-03-23

    def test_type_level_change_uses_type_pattern(self) -> None:
        changes = [
            {"kind": "type_size_changed", "symbol": "MyStruct"},
        ]
        yaml_text = suggest_suppressions(changes, today=date(2026, 3, 23))
        assert 'type_pattern: "MyStruct"' in yaml_text
        assert "symbol:" not in yaml_text.split("type_pattern")[1].split("\n")[0]

    def test_type_pattern_strips_member_suffix(self) -> None:
        """Member-qualified symbols like Color::GREEN should emit type_pattern: Color."""
        changes = [
            {"kind": "enum_member_removed", "symbol": "Color::GREEN"},
        ]
        yaml_text = suggest_suppressions(changes, today=date(2026, 3, 23))
        assert 'type_pattern: "Color"' in yaml_text
        assert "GREEN" not in yaml_text

    def test_null_kind_or_symbol_skipped(self) -> None:
        """JSON null values must not produce literal 'None' strings."""
        changes = [
            {"kind": None, "symbol": "_ZN3foo3barEv"},
            {"kind": "func_removed", "symbol": None},
            {"kind": None, "symbol": None},
        ]
        yaml_text = suggest_suppressions(changes, today=date(2026, 3, 23))
        assert "None" not in yaml_text
        assert 'symbol: "' not in yaml_text

    def test_yaml_quote_escapes_special_chars(self) -> None:
        """Symbols with backslashes or quotes must produce valid YAML."""
        changes = [
            {"kind": "func_removed", "symbol": 'foo\\"bar'},
        ]
        yaml_text = suggest_suppressions(changes, today=date(2026, 3, 23))
        # Should be parseable as YAML without error
        import yaml
        data = yaml.safe_load(yaml_text)
        assert len(data["suppressions"]) == 1

    def test_custom_expiry_days(self) -> None:
        changes = [
            {"kind": "func_removed", "symbol": "_ZN3foo3barEv"},
        ]
        yaml_text = suggest_suppressions(
            changes, expiry_days=30, today=date(2026, 3, 23),
        )
        assert 'expires: "2026-04-22"' in yaml_text

    def test_multiple_changes(self) -> None:
        changes = [
            {"kind": "func_removed", "symbol": "_ZN3foo6legacyEv"},
            {"kind": "func_param_type_changed", "symbol": "_ZN3foo3bazEi"},
            {"kind": "enum_member_removed", "symbol": "Color"},
        ]
        yaml_text = suggest_suppressions(changes, today=date(2026, 3, 23))
        assert 'symbol: "_ZN3foo6legacyEv"' in yaml_text
        assert 'symbol: "_ZN3foo3bazEi"' in yaml_text
        assert 'type_pattern: "Color"' in yaml_text
        assert yaml_text.count("change_kind:") == 3

    def test_empty_changes(self) -> None:
        yaml_text = suggest_suppressions([], today=date(2026, 3, 23))
        assert "version: 1" in yaml_text
        assert "suppressions:" in yaml_text

    def test_skips_entries_without_kind_or_symbol(self) -> None:
        changes = [
            {"kind": "func_removed"},  # no symbol
            {"symbol": "_ZN3foo3barEv"},  # no kind
            {"kind": "", "symbol": ""},  # empty
        ]
        yaml_text = suggest_suppressions(changes, today=date(2026, 3, 23))
        # None of these should produce suppression entries
        assert 'symbol: "' not in yaml_text
        assert 'type_pattern: "' not in yaml_text

    def test_output_is_valid_yaml(self) -> None:
        import yaml

        changes = [
            {"kind": "func_removed", "symbol": "_ZN3foo6legacyEv"},
            {"kind": "type_size_changed", "symbol": "MyStruct"},
        ]
        yaml_text = suggest_suppressions(changes, today=date(2026, 3, 23))
        data = yaml.safe_load(yaml_text)
        assert data["version"] == 1
        assert len(data["suppressions"]) == 2

    def test_default_expiry_uses_today(self) -> None:
        changes = [
            {"kind": "func_removed", "symbol": "_ZN3foo3barEv"},
        ]
        yaml_text = suggest_suppressions(changes)
        expected_date = (date.today() + timedelta(days=180)).isoformat()
        assert f'expires: "{expected_date}"' in yaml_text


# ─── CLI integration tests ───────────────────────────────────────────────────

class TestStrictSuppressionsCliFlag:
    """Test --strict-suppressions via Click's CliRunner."""

    def test_strict_suppressions_fails_on_expired(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        past = date.today() - timedelta(days=30)
        sup_path = write_yaml(tmp_path, f"""
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "expired"
                expires: "{past.isoformat()}"
        """)

        # Create minimal old/new JSON snapshots
        old_snap = tmp_path / "old.json"
        new_snap = tmp_path / "new.json"
        snap = json.dumps({
            "library": "libtest.so", "version": "1.0",
            "functions": [], "variables": [], "types": [],
        })
        old_snap.write_text(snap, encoding="utf-8")
        new_snap.write_text(snap, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_snap), str(new_snap),
            "--suppress", str(sup_path),
            "--strict-suppressions",
        ])
        assert result.exit_code != 0
        assert "expired" in result.output.lower() or "expired" in (result.stderr or "").lower() or "expired" in str(result.exception or "").lower()

    def test_strict_suppressions_passes_when_valid(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        future = date.today() + timedelta(days=90)
        sup_path = write_yaml(tmp_path, f"""
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "valid"
                expires: "{future.isoformat()}"
        """)

        old_snap = tmp_path / "old.json"
        new_snap = tmp_path / "new.json"
        snap = json.dumps({
            "library": "libtest.so", "version": "1.0",
            "functions": [], "variables": [], "types": [],
        })
        old_snap.write_text(snap, encoding="utf-8")
        new_snap.write_text(snap, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_snap), str(new_snap),
            "--suppress", str(sup_path),
            "--strict-suppressions",
        ])
        assert result.exit_code == 0


class TestRequireJustificationCliFlag:

    def test_require_justification_fails_on_missing(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        sup_path = write_yaml(tmp_path, """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
        """)

        old_snap = tmp_path / "old.json"
        new_snap = tmp_path / "new.json"
        snap = json.dumps({
            "library": "libtest.so", "version": "1.0",
            "functions": [], "variables": [], "types": [],
        })
        old_snap.write_text(snap, encoding="utf-8")
        new_snap.write_text(snap, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_snap), str(new_snap),
            "--suppress", str(sup_path),
            "--require-justification",
        ])
        assert result.exit_code != 0
        assert "reason" in result.output.lower() or "reason" in str(result.exception or "").lower()


# The `suggest-suppressions` CLI command (cli_suggest.py) was deleted in the
# pre-1.0 CLI reset; its JSON-file-reading/shape-validation wrapper (bad
# JSON, non-object root, missing 'changes' key, non-dict change entries,
# `--expiry-days` range check) was pure CLI plumbing and is gone with the
# command. `suggest_suppressions()` itself (already-parsed `list[dict]` in,
# YAML text out) is unchanged and already covered directly by
# `TestSuggestSuppressions` above (including custom-expiry and empty-changes
# cases) — no CLI-level replacement test is needed here.

    def test_suggest_empty_changes(self) -> None:
        text = suggest_suppressions([])
        assert "suppressions:" in text


class TestRequireJustificationErrorType:
    """--require-justification failures should be ClickException, not BadParameter."""

    def test_justification_error_is_not_usage_error(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        sup_path = write_yaml(tmp_path, """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
        """)

        old_snap = tmp_path / "old.json"
        new_snap = tmp_path / "new.json"
        snap = json.dumps({
            "library": "libtest.so", "version": "1.0",
            "functions": [], "variables": [], "types": [],
        })
        old_snap.write_text(snap, encoding="utf-8")
        new_snap.write_text(snap, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_snap), str(new_snap),
            "--suppress", str(sup_path),
            "--require-justification",
        ])
        assert result.exit_code != 0
        # Should show "Error:" (ClickException) not "Usage:" (BadParameter)
        assert "Error:" in result.output
        assert "Usage:" not in result.output
