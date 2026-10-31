"""Unit tests for the config-owned compare inputs (ADR-091 Phase 5).

``consumers.manifests``, ``consumers.required_symbols`` and ``output.view``
are read through :mod:`abicheck.frontends.cli.compare_config_scope`. These
tests pin each resolution rule at the function that owns it, so the CLI
integration tests do not have to carry the branch coverage alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import click
import pytest

from abicheck.frontends.cli.compare_config_scope import (
    _parse_config_view,
    _resolve_config_path,
    _resolve_config_required_symbol,
    config_scope_inputs,
    consumer_manifest_specs,
)


def _cfg(**fields: object) -> SimpleNamespace:
    """A stand-in for the resolved ``BuildConfig``: only the fields read here."""
    base = {"used_by_manifests": [], "required_symbols": [], "output_view": []}
    base.update(fields)
    return SimpleNamespace(**base)


class TestResolveConfigPath:
    def test_absolute_item_is_returned_unchanged(self, tmp_path: Path) -> None:
        absolute = tmp_path / "abs.json"
        assert (
            _resolve_config_path(str(absolute), tmp_path / "cfg" / ".abicheck.yml")
            == absolute
        )

    def test_relative_item_resolves_against_the_config_directory(
        self, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "project" / ".abicheck.yml"
        assert (
            _resolve_config_path("consumers/app.json", cfg)
            == tmp_path / "project" / "consumers/app.json"
        )

    def test_relative_item_without_a_config_file_stays_relative(self) -> None:
        assert _resolve_config_path("app.json", None) == Path("app.json")


class TestResolveConfigRequiredSymbol:
    def test_plain_symbol_is_passed_through(self, tmp_path: Path) -> None:
        assert (
            _resolve_config_required_symbol("foo_init", tmp_path / ".abicheck.yml")
            == "foo_init"
        )

    def test_at_file_resolves_relative_to_the_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "project" / ".abicheck.yml"
        resolved = _resolve_config_required_symbol("@symbols.txt", cfg)
        assert resolved == "@" + str(tmp_path / "project" / "symbols.txt")

    def test_at_file_without_a_config_keeps_the_given_path(self) -> None:
        assert _resolve_config_required_symbol("@symbols.txt", None) == "@symbols.txt"


class TestParseConfigView:
    def test_show_token_parses_to_report_mode_and_filter(self) -> None:
        parsed = _parse_config_view(["show=breaking"])
        assert parsed["show_only"] == "breaking"
        assert parsed["report_mode"] == "full"

    def test_unknown_token_is_a_usage_error_naming_the_key(self) -> None:
        with pytest.raises(click.UsageError, match=r"^output\.view: "):
            _parse_config_view(["no-such-token"])


class TestConsumerManifestSpecs:
    def test_no_manifests_yields_no_specs(self) -> None:
        assert consumer_manifest_specs(()) == ()

    def test_a_manifest_yields_its_consumers(self, tmp_path: Path) -> None:
        manifest = tmp_path / "consumers.json"
        manifest.write_text(
            json.dumps({"consumers": [{"path": "bin/app"}]}), encoding="utf-8"
        )
        specs = consumer_manifest_specs((manifest,))
        assert len(specs) == 1

    def test_a_malformed_manifest_is_a_usage_error_naming_the_key(
        self, tmp_path: Path
    ) -> None:
        manifest = tmp_path / "bad.json"
        manifest.write_text("not json", encoding="utf-8")
        with pytest.raises(click.UsageError, match=r"^consumers\.manifests: "):
            consumer_manifest_specs((manifest,))


class TestConfigScopeInputs:
    def test_no_project_config_passes_the_command_line_values_through(
        self, tmp_path: Path
    ) -> None:
        manifests = (tmp_path / "m.json",)
        symbols = ("sym",)
        assert config_scope_inputs(None, None, manifests, symbols) == (
            manifests,
            symbols,
            None,
        )

    def test_config_values_win_over_the_empty_command_line_default(
        self, tmp_path: Path
    ) -> None:
        cfg = tmp_path / ".abicheck.yml"
        project = _cfg(used_by_manifests=["consumers.json"], required_symbols=["foo"])
        manifests, symbols, view = config_scope_inputs(project, cfg, (), ())
        assert manifests == (tmp_path / "consumers.json",)
        assert symbols == ("foo",)
        assert view is None

    def test_command_line_values_are_kept_when_the_config_sets_none(
        self, tmp_path: Path
    ) -> None:
        cli_manifests = (tmp_path / "cli.json",)
        cli_symbols = ("cli_sym",)
        project = _cfg()
        assert config_scope_inputs(
            project, tmp_path / ".abicheck.yml", cli_manifests, cli_symbols
        ) == (
            cli_manifests,
            cli_symbols,
            None,
        )

    def test_output_view_is_parsed_to_report_mode_and_filter(
        self, tmp_path: Path
    ) -> None:
        project = _cfg(output_view=["show=breaking"])
        _, _, view = config_scope_inputs(project, tmp_path / ".abicheck.yml", (), ())
        assert view == ("full", "breaking")
