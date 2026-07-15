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

    def test_wrong_type_block_is_reported(self) -> None:
        """A block key given a scalar (e.g. `severity: strict` instead of
        `severity: {preset: strict}`) is silently coerced to `{}` by
        BuildConfig.from_dict — every key under it quietly dropped rather
        than raising. `validate` must catch this, not report OK (Codex
        review)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text("severity: strict\n", encoding="utf-8")
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 1
            assert "severity must be a mapping" in result.output

    def test_wrong_type_bool_subkey_is_reported(self) -> None:
        """A boolean subkey given a non-bool (e.g. the YAML string "false"
        instead of the boolean `false`) is silently treated as unset by
        `_opt_bool` — `validate` must catch this, not report OK (Codex
        review)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                'scope:\n  public: "false"\n', encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 1
            assert "scope.public must be a boolean" in result.output

    def test_wrong_type_string_subkey_is_reported(self) -> None:
        """A string subkey given a non-string (e.g. a bare number) is
        silently treated as unset by `_opt_str` — `validate` must catch this
        too, not just boolean subkeys (Codex review — fresh evidence after
        the initial bool-only pass)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "debug:\n  debuginfod_url: 456\n", encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 1
            assert "debug.debuginfod_url must be a string" in result.output

    def test_wrong_type_list_subkey_is_reported(self) -> None:
        """A list-of-strings subkey given a non-list/non-string (e.g. a bare
        number) is silently treated as empty by `_strs` — `validate` must
        catch this too (Codex review)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "sources:\n  public_headers: 123\n", encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 1
            assert (
                "sources.public_headers must be a string or list of strings"
                in result.output
            )

    def test_list_subkey_accepts_bare_string(self) -> None:
        """A bare string is a valid (1-element) value for a list subkey —
        `_strs` folds it, so this must NOT be reported as wrong-type."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "sources:\n  public_headers: include/foo.h\n", encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 0, result.output

    def test_wrong_type_top_level_string_is_reported(self) -> None:
        """A recognized top-level scalar (not a block key) has the same
        silent-coercion gap one level up: `exit_code_scheme: 123` is
        silently treated as "auto" by `_str(data, "exit_code_scheme",
        "auto")` — `validate` must catch this too, not just block subkeys
        (Codex review — fresh evidence after the block/subkey passes)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "exit_code_scheme: 123\n", encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 1
            assert "exit_code_scheme must be a string" in result.output

    def test_wrong_type_top_level_int_is_reported(self) -> None:
        """`version` must be an int (excluding bool) — a non-int is silently
        treated as 0 by BuildConfig.from_dict's isinstance guard."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text('version: "1"\n', encoding="utf-8")
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 1
            assert "version must be an integer" in result.output

    def test_version_accepts_plain_int(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text("version: 1\n", encoding="utf-8")
            result = runner.invoke(main, ["config", "validate"])
            assert result.exit_code == 0, result.output

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
    def test_invalid_config_value_is_usage_error_not_traceback(self) -> None:
        """A recognized key with an invalid value (e.g. severity.preset:
        bogus) previously raised an unhandled ValueError from the direct
        load_build_config() call — a raw Python traceback instead of the
        usage/config error `compare`/`config validate` produce for the same
        input, making the command unusable while debugging a bad config
        (Codex review)."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text(
                "severity:\n  preset: bogus\n", encoding="utf-8"
            )
            result = runner.invoke(main, ["config", "show-effective"])
            assert result.exit_code != 0
            assert result.exception is None or isinstance(result.exception, SystemExit)
            assert "severity.preset" in result.output

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
