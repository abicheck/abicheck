# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the `init` command and the `config` group (validate, show-effective)."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main


class TestInit:
    def test_writes_starter_config(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["init"])
            assert result.exit_code == 0, result.output
            assert Path(".abicheck.yml").is_file()
            text = Path(".abicheck.yml").read_text(encoding="utf-8")
            assert "severity:" in text
            assert "version: 1" in text

    def test_refuses_to_overwrite_without_force(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(main, ["init"])
            result = runner.invoke(main, ["init"])
            assert result.exit_code != 0
            assert "already exists" in result.output

    def test_force_overwrites(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text("stale content", encoding="utf-8")
            result = runner.invoke(main, ["init", "--force"])
            assert result.exit_code == 0, result.output
            assert "stale content" not in Path(".abicheck.yml").read_text(
                encoding="utf-8"
            )

    def test_custom_path(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["init", "--path", "custom.yml"])
            assert result.exit_code == 0, result.output
            assert Path("custom.yml").is_file()
            assert not Path(".abicheck.yml").exists()


class TestConfigValidate:
    def test_clean_config_is_ok(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "severity:\n  preset: strict\n", encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 0, result.output
            assert "OK" in result.output

    def test_unknown_top_level_key_is_reported(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "totally_unknown: true\n", encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 1
            assert "unknown top-level key" in result.output
            assert "totally_unknown" in result.output

    def test_unknown_block_key_is_reported(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "severity:\n  preset: strict\n  bogus: 1\n", encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 1
            assert "severity.'bogus'" in result.output

    def test_invalid_value_on_recognized_key_is_reported(self) -> None:
        # Codex review #556: a recognized key with an invalid value (e.g.
        # severity.preset: bogus) must not report OK — compare/show-effective
        # would fail on the same file via BuildConfig.from_dict's real
        # validation, which `validate` now runs too.
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "severity:\n  preset: bogus\n", encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 1
            assert "invalid value" in result.output
            assert "severity.preset" in result.output

    def test_explicit_path(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("custom.yml").write_text("version: 1\n", encoding="utf-8")
            result = runner.invoke(main, ["config", "validate", "custom.yml"])
            assert result.exit_code == 0, result.output

    def test_no_config_found_is_usage_error(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 64, result.output
            assert "no .abicheck.yml found" in result.output

    def test_malformed_yaml_is_usage_error(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "severity: [unterminated\n", encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 64, result.output

    def test_non_mapping_top_level_is_usage_error(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text("- just\n- a\n- list\n", encoding="utf-8")
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 64, result.output
            assert "must be a mapping" in result.output


class TestConfigShowEffective:
    def test_defaults_when_no_config_and_no_flags(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["config", "show-effective"])
            assert result.exit_code == 0, result.output
            assert "(none found)" in result.output
            assert "scope.public" in result.output
            assert "True" in result.output
            assert "(default)" in result.output

    def test_cli_severity_preset_overrides_default_and_is_labelled_cli(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                main, ["config", "show-effective", "--severity-preset", "strict"]
            )
            assert result.exit_code == 0, result.output
            assert "severity.preset" in result.output
            assert "strict" in result.output
            assert "(cli)" in result.output
            # strict preset promotes every category to error.
            assert "abi_breaking: error" in result.output
            assert "potential_breaking: error" in result.output

    def test_config_file_value_is_labelled_config(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "severity:\n  preset: strict\n", encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "show-effective"])
            assert result.exit_code == 0, result.output
            assert "(config)" in result.output

    def test_cli_flag_beats_config_file(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "severity:\n  preset: strict\n", encoding="utf-8"
            )
            result = runner.invoke(
                main, ["config", "show-effective", "--severity-preset", "info-only"]
            )
            assert result.exit_code == 0, result.output
            assert "severity.preset" in result.output
            assert "info-only" in result.output
            assert "(cli)" in result.output

    def test_strict_suppressions_flag_reflected(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                main, ["config", "show-effective", "--strict-suppressions"]
            )
            assert result.exit_code == 0, result.output
            assert "suppression.strict" in result.output
