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

"""Tests for the `doctor` command."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main


class TestDoctorEnvironmentOnly:
    def test_reports_expected_sections(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["doctor"])
            assert result.exit_code == 0, result.output
            assert "== AST frontend ==" in result.output
            assert "== Compiler toolchain ==" in result.output
            assert "== debuginfod ==" in result.output
            assert "== project config ==" in result.output

    def test_reports_discovered_config(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path(".abicheck.yml").write_text("version: 1\n", encoding="utf-8")
            result = runner.invoke(main, ["doctor"])
            assert result.exit_code == 0, result.output
            assert ".abicheck.yml" in result.output

    def test_reports_no_config_when_absent(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["doctor"])
            assert result.exit_code == 0, result.output
            assert "none found" in result.output

    def test_no_binary_section_when_no_binary_given(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["doctor"])
            assert result.exit_code == 0, result.output
            assert "== data sources" not in result.output

    def test_tolerates_invalid_ast_frontend_env(self, monkeypatch) -> None:
        """A misspelled ABICHECK_AST_FRONTEND must not crash `doctor` before
        it can print diagnostics — that's exactly the misconfiguration the
        command exists to help find (Codex P2)."""
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", "castxmll")
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["doctor"])
            assert result.exit_code == 0, result.output
            assert "== AST frontend ==" in result.output
            assert (
                "WARNING: ABICHECK_AST_FRONTEND='castxmll' is not recognized"
                in result.output
            )
            assert "selected: castxml" in result.output

    def test_valid_ast_frontend_env_no_warning(self, monkeypatch) -> None:
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["doctor"])
            assert result.exit_code == 0, result.output
            assert "WARNING: ABICHECK_AST_FRONTEND" not in result.output
            assert "selected: clang" in result.output


class TestDoctorWithBinary:
    def test_missing_binary_is_usage_error(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["doctor", "/does/not/exist.so"])
        assert result.exit_code != 0

    def test_binary_triggers_data_sources_section(self, tmp_path: Path) -> None:
        # A real (if minimal/garbage) file is enough for click's exists=True
        # check; print_data_sources itself is exercised by the existing
        # `dump --show-data-sources` tests, so this only proves the wiring.
        so_path = tmp_path / "libfoo.so"
        so_path.write_bytes(b"not a real elf file")
        runner = CliRunner()
        result = runner.invoke(main, ["doctor", str(so_path)])
        assert "== data sources:" in result.output
