"""Tests for suppression lifecycle enforcement features.

Covers:
- suppression.strict (fail on expired rules)
- suppression.require_justification (require reason field)
- suggest-suppressions command
"""

from __future__ import annotations

import json
import textwrap
from datetime import date, timedelta
from pathlib import Path

import pytest

from abicheck.suppression import SuppressionList

# ─── helpers ──────────────────────────────────────────────────────────────────


def write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "suppressions.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ─── suppression.strict: check_expired_strict ────────────────────────────────


class TestStrictSuppressions:
    def test_no_expired_rules_returns_empty(self, tmp_path: Path) -> None:
        future = date.today() + timedelta(days=90)
        yaml_path = write_yaml(
            tmp_path,
            f"""
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "intentional"
                expires: "{future.isoformat()}"
        """,
        )
        sl = SuppressionList.load(yaml_path)
        assert sl.check_expired_strict() == []

    def test_expired_rules_returns_indexed_pairs(self, tmp_path: Path) -> None:
        past = date.today() - timedelta(days=30)
        future = date.today() + timedelta(days=90)
        yaml_path = write_yaml(
            tmp_path,
            f"""
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
        """,
        )
        sl = SuppressionList.load(yaml_path)
        expired = sl.check_expired_strict()
        assert len(expired) == 2
        # check_expired_strict returns 0-based indices; CLI adds 1 for display
        assert expired[0][0] == 1  # 0-based index of second rule
        assert expired[1][0] == 2  # 0-based index of third rule
        assert expired[0][1].symbol_pattern == "_ZN3foo.*Internal.*"
        assert expired[1][1].symbol == "_ZN3bar6legacyEv"

    def test_rules_without_expiry_not_flagged(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "no expiry"
        """,
        )
        sl = SuppressionList.load(yaml_path)
        assert sl.check_expired_strict() == []

    def test_custom_today_date(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "expires mid-year"
                expires: "2026-06-15"
        """,
        )
        sl = SuppressionList.load(yaml_path)
        # Before expiry
        assert sl.check_expired_strict(today=date(2026, 6, 14)) == []
        # On expiry day — not expired (check is >)
        assert sl.check_expired_strict(today=date(2026, 6, 15)) == []
        # After expiry
        expired = sl.check_expired_strict(today=date(2026, 6, 16))
        assert len(expired) == 1


# ─── suppression.require_justification ───────────────────────────────────────


class TestRequireJustification:
    def test_all_rules_have_reason_passes(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "intentional removal for v2"
              - symbol_pattern: ".*internal.*"
                reason: "internal namespace"
        """,
        )
        sl = SuppressionList.load(yaml_path, require_justification=True)
        assert len(sl) == 2

    def test_missing_reason_raises(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "has reason"
              - symbol: "_ZN3baz3quxEv"
              - symbol_pattern: ".*detail.*"
                reason: "internal"
        """,
        )
        with pytest.raises(ValueError, match=r"rule 1.*no 'reason' field"):
            SuppressionList.load(yaml_path, require_justification=True)

    def test_empty_reason_raises(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: ""
        """,
        )
        with pytest.raises(ValueError, match=r"rule 0.*no 'reason' field"):
            SuppressionList.load(yaml_path, require_justification=True)

    def test_without_flag_missing_reason_ok(self, tmp_path: Path) -> None:
        yaml_path = write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
        """,
        )
        sl = SuppressionList.load(yaml_path, require_justification=False)
        assert len(sl) == 1


# ─── suggest-suppressions ────────────────────────────────────────────────────


# ─── CLI integration tests ───────────────────────────────────────────────────


def _strict_config(tmp_path: Path, **keys: bool) -> Path:
    """A project config setting `suppression:` keys.

    The `--strict-suppressions`/`--require-justification` pair were hidden CLI
    duplicates of these keys and were removed, so a config file is how a run
    asks for them now.
    """
    cfg = tmp_path / ".abicheck.yml"
    body = "".join(f"  {k}: {str(v).lower()}\n" for k, v in keys.items())
    cfg.write_text(f"suppression:\n{body}", encoding="utf-8")
    return cfg


class TestStrictSuppressionsCliFlag:
    """Test `suppression.strict` end to end via Click's CliRunner."""

    def test_strict_suppressions_fails_on_expired(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        past = date.today() - timedelta(days=30)
        sup_path = write_yaml(
            tmp_path,
            f"""
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "expired"
                expires: "{past.isoformat()}"
        """,
        )

        # Create minimal old/new JSON snapshots
        old_snap = tmp_path / "old.json"
        new_snap = tmp_path / "new.json"
        snap = json.dumps(
            {
                "library": "libtest.so",
                "version": "1.0",
                "functions": [],
                "variables": [],
                "types": [],
            }
        )
        old_snap.write_text(snap, encoding="utf-8")
        new_snap.write_text(snap, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compare",
                str(old_snap),
                str(new_snap),
                "--suppress",
                str(sup_path),
                "--config",
                str(_strict_config(tmp_path, strict=True)),
            ],
        )
        assert result.exit_code != 0
        assert (
            "expired" in result.output.lower()
            or "expired" in (result.stderr or "").lower()
            or "expired" in str(result.exception or "").lower()
        )

    def test_strict_suppressions_passes_when_valid(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        future = date.today() + timedelta(days=90)
        sup_path = write_yaml(
            tmp_path,
            f"""
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
                reason: "valid"
                expires: "{future.isoformat()}"
        """,
        )

        old_snap = tmp_path / "old.json"
        new_snap = tmp_path / "new.json"
        snap = json.dumps(
            {
                "library": "libtest.so",
                "version": "1.0",
                "functions": [],
                "variables": [],
                "types": [],
            }
        )
        old_snap.write_text(snap, encoding="utf-8")
        new_snap.write_text(snap, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compare",
                str(old_snap),
                str(new_snap),
                "--suppress",
                str(sup_path),
                "--config",
                str(_strict_config(tmp_path, strict=True)),
            ],
        )
        assert result.exit_code == 0

    def test_strict_suppressions_renders_finding_id_not_bare_fallback(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review, PR #753, fresh evidence): a
        # finding_id-only rule with no symbol/type_pattern/source_location
        # rendered as the bare "?" fallback in this diagnostic's own
        # selector chain, distinct from (and missed by) the identical fix
        # already made to cli_compare_fold.py/post_processing.py.
        from click.testing import CliRunner

        from abicheck.cli import main

        past = date.today() - timedelta(days=30)
        digest = "abcd1234abcd1234"
        sup_path = write_yaml(
            tmp_path,
            f"""
            version: 1
            suppressions:
              - finding_id: "{digest}"
                reason: "expired"
                expires: "{past.isoformat()}"
        """,
        )

        old_snap = tmp_path / "old.json"
        new_snap = tmp_path / "new.json"
        snap = json.dumps(
            {
                "library": "libtest.so",
                "version": "1.0",
                "functions": [],
                "variables": [],
                "types": [],
            }
        )
        old_snap.write_text(snap, encoding="utf-8")
        new_snap.write_text(snap, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compare",
                str(old_snap),
                str(new_snap),
                "--suppress",
                str(sup_path),
                "--config",
                str(_strict_config(tmp_path, strict=True)),
            ],
        )
        assert result.exit_code != 0
        output = result.output + str(result.exception or "")
        assert digest in output
        assert 'Rule 1: "?"' not in output


class TestRequireJustificationCliFlag:
    def test_require_justification_fails_on_missing(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        sup_path = write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
        """,
        )

        old_snap = tmp_path / "old.json"
        new_snap = tmp_path / "new.json"
        snap = json.dumps(
            {
                "library": "libtest.so",
                "version": "1.0",
                "functions": [],
                "variables": [],
                "types": [],
            }
        )
        old_snap.write_text(snap, encoding="utf-8")
        new_snap.write_text(snap, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compare",
                str(old_snap),
                str(new_snap),
                "--suppress",
                str(sup_path),
                "--config",
                str(_strict_config(tmp_path, require_justification=True)),
            ],
        )
        assert result.exit_code != 0
        assert (
            "reason" in result.output.lower()
            or "reason" in str(result.exception or "").lower()
        )

    # The `suggest-suppressions` CLI command (cli_suggest.py) was deleted in the
    # pre-1.0 CLI reset; its JSON-file-reading/shape-validation wrapper (bad
    # JSON, non-object root, missing 'changes' key, non-dict change entries,
    # `--expiry-days` range check) was pure CLI plumbing and is gone with the
    # command. `suggest_suppressions()` itself (already-parsed `list[dict]` in,
    # YAML text out) is unchanged and already covered directly by
    # `TestSuggestSuppressions` above (including custom-expiry and empty-changes
    # cases) — no CLI-level replacement test is needed here.

class TestRequireJustificationErrorType:
    """suppression.require_justification failures are ClickException, not BadParameter."""

    def test_justification_error_is_not_usage_error(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from abicheck.cli import main

        sup_path = write_yaml(
            tmp_path,
            """
            version: 1
            suppressions:
              - symbol: "_ZN3foo3barEv"
        """,
        )

        old_snap = tmp_path / "old.json"
        new_snap = tmp_path / "new.json"
        snap = json.dumps(
            {
                "library": "libtest.so",
                "version": "1.0",
                "functions": [],
                "variables": [],
                "types": [],
            }
        )
        old_snap.write_text(snap, encoding="utf-8")
        new_snap.write_text(snap, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "compare",
                str(old_snap),
                str(new_snap),
                "--suppress",
                str(sup_path),
                "--config",
                str(_strict_config(tmp_path, require_justification=True)),
            ],
        )
        assert result.exit_code != 0
        # Should show "Error:" (ClickException) not "Usage:" (BadParameter)
        assert "Error:" in result.output
        assert "Usage:" not in result.output
