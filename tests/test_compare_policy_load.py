"""Unit tests for abicheck.workflows.compare_policy.load_suppression_and_policy."""

from __future__ import annotations

import pytest

from abicheck.errors import ValidationError
from abicheck.workflows.compare_policy import (
    dedup_policy_override_warnings,
    load_suppression_and_policy,
)

# ── load_suppression_and_policy() ───────────────────────────────────────────


class TestLoadSuppressionAndPolicy:
    def test_no_suppress_no_policy(self):
        s, p = load_suppression_and_policy(None)
        assert s is None
        assert p is None

    def test_invalid_suppression_file(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("not: [valid: suppression")
        with pytest.raises(ValidationError, match="Invalid suppression"):
            load_suppression_and_policy(f)

    def test_valid_suppression_file(self, tmp_path):
        f = tmp_path / "suppress.yaml"
        f.write_text(
            "version: 1\nsuppressions:\n  - symbol: 'foo'\n    change_kind: func_removed\n"
        )
        s, p = load_suppression_and_policy(f)
        assert s is not None
        assert p is None

    def test_policy_file_with_non_default_policy_warns(self, tmp_path, caplog):
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("overrides: {}\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            _, p = load_suppression_and_policy(
                None, policy="permissive", policy_file_path=pf
            )
        assert p is not None
        assert "ignored" in caplog.text.lower()

    def test_invalid_policy_file(self, tmp_path):
        pf = tmp_path / "bad_policy.yaml"
        pf.write_text("- this is a list not a mapping\n")
        with pytest.raises(ValidationError):
            load_suppression_and_policy(None, policy_file_path=pf)

    def test_risky_override_warns(self, tmp_path, caplog):
        """A risky override must warn even through this Tier-2 chokepoint --
        `compare-release`'s real per-library fan-out loads its policy here,
        not through the CLI's own `_load_suppression_and_policy` (Codex
        review: the latter's warning never reached that path)."""
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            _, p = load_suppression_and_policy(None, policy_file_path=pf)
        assert p is not None
        assert "HIGH RISK" in caplog.text
        assert "func_removed" in caplog.text

    def test_risky_override_warns_every_call_outside_dedup_scope(
        self, tmp_path, caplog
    ):
        """Without `dedup_policy_override_warnings()`, every call still warns
        -- e.g. a plain `compare`/`scan --against` invocation, which only
        ever loads a given policy file once, must see no behaviour change."""
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            load_suppression_and_policy(None, policy_file_path=pf)
            load_suppression_and_policy(None, policy_file_path=pf)
        assert caplog.text.count("HIGH RISK") == 2

    def test_dedup_policy_override_warnings_warns_once_per_scope(
        self, tmp_path, caplog
    ):
        """Inside `dedup_policy_override_warnings()`, reloading the identical
        policy file warns once -- the fix for `compare-release`'s per-library
        fan-out flooding stderr with the same warning (Codex review)."""
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            with dedup_policy_override_warnings():
                load_suppression_and_policy(None, policy_file_path=pf)
                load_suppression_and_policy(None, policy_file_path=pf)
                load_suppression_and_policy(None, policy_file_path=pf)
        assert caplog.text.count("HIGH RISK") == 1

    def test_dedup_scope_does_not_leak_across_calls(self, tmp_path, caplog):
        """A fresh `dedup_policy_override_warnings()` scope must not remember
        warnings already emitted by a previous, already-exited scope."""
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            with dedup_policy_override_warnings():
                load_suppression_and_policy(None, policy_file_path=pf)
            with dedup_policy_override_warnings():
                load_suppression_and_policy(None, policy_file_path=pf)
        assert caplog.text.count("HIGH RISK") == 2

    def test_dedup_scope_shared_with_cli_params_loader(self, tmp_path, capsys, caplog):
        """`dedup_policy_override_warnings()` must dedupe across *both*
        loaders, not just repeated `service.load_suppression_and_policy()`
        calls -- `compare-release` also loads through
        `frontends.cli.options.params._load_suppression_and_policy` (its
        early strict-suppression validation and probe-matrix paths), and a
        scope covering only one loader would still let the same warning
        through twice (Codex review: fresh evidence on the follow-up
        commit)."""
        import logging

        from abicheck.frontends.cli.options.params import (
            _load_suppression_and_policy,
        )

        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            with dedup_policy_override_warnings():
                _load_suppression_and_policy(None, "strict_abi", pf)  # click.echo
                load_suppression_and_policy(None, policy_file_path=pf)  # logger
                load_suppression_and_policy(None, policy_file_path=pf)  # logger
        # The CLI-level loader's warning is the first one seen in the shared
        # scope, so it "wins" -- both later service-level loads are deduped
        # against it and log nothing further.
        assert "HIGH RISK" in capsys.readouterr().err
        assert "HIGH RISK" not in caplog.text
