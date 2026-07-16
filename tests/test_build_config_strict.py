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

"""ADR-043 (pre-1.0 CLI reset): strict ``.abicheck.yml`` loading.

``abicheck doctor``, ``abicheck init``, and the ``abicheck config`` group
(``validate``/``show-effective``) are removed entirely — no aliases, no
deprecation warnings. The structural strictness ``config validate`` used to
provide as a separate, easy-to-skip step now lives in
``BuildConfig.from_dict`` itself (``abicheck/buildsource/inline.py``), so it
fires on every real ``dump``/``compare``/``scan`` config load. This module
proves:

* ``BuildConfig.from_dict`` raises ``ValueError`` for every structural
  problem (unknown top-level key, unknown block subkey, non-mapping block,
  wrong scalar/list type, bad enum value) with no opt-in step required.
* A real CLI command (``compare``) exits 64 (the project's usage-error code)
  on a bad ``.abicheck.yml``, never an uncaught traceback.
* ``init``/``config``/``doctor`` are gone: invoking them produces Click's
  ordinary "no such command" usage error, exactly like any other unknown
  command name.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.inline import BuildConfig
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _identical_pair(tmp_path: Path) -> tuple[Path, Path]:
    snap = AbiSnapshot(
        library="libtest.so",
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    old_p = _write_snap(tmp_path / "old.json", snap)
    new_p = _write_snap(tmp_path / "new.json", snap)
    return old_p, new_p


# ── BuildConfig.from_dict: structural strictness ─────────────────────────────


class TestBuildConfigFromDictRejects:
    def test_unknown_top_level_key(self) -> None:
        with pytest.raises(ValueError, match="unknown .abicheck.yml key 'bogus_top'"):
            BuildConfig.from_dict({"bogus_top": 1})

    def test_unknown_block_subkey(self) -> None:
        with pytest.raises(ValueError, match=r"severity\.'bogus'"):
            BuildConfig.from_dict({"severity": {"preset": "strict", "bogus": 1}})

    def test_non_mapping_block_value(self) -> None:
        with pytest.raises(ValueError, match="severity must be a mapping"):
            BuildConfig.from_dict({"severity": "strict"})

    def test_wrong_scalar_type_bool_subkey(self) -> None:
        with pytest.raises(ValueError, match="scope.public must be a boolean"):
            BuildConfig.from_dict({"scope": {"public": "false"}})

    def test_wrong_scalar_type_string_subkey(self) -> None:
        with pytest.raises(ValueError, match="debug.debuginfod_url must be a string"):
            BuildConfig.from_dict({"debug": {"debuginfod_url": 456}})

    def test_wrong_list_type_container(self) -> None:
        with pytest.raises(
            ValueError, match="sources.public_headers must be a string or list"
        ):
            BuildConfig.from_dict({"sources": {"public_headers": 123}})

    def test_wrong_list_type_element(self) -> None:
        with pytest.raises(
            ValueError, match=r"sources\.public_headers must be a list of strings"
        ):
            BuildConfig.from_dict(
                {"sources": {"public_headers": ["include/foo.h", 123]}}
            )

    def test_wrong_top_level_scalar_string(self) -> None:
        with pytest.raises(ValueError, match="exit_code_scheme must be a string"):
            BuildConfig.from_dict({"exit_code_scheme": 123})

    def test_wrong_top_level_scalar_int(self) -> None:
        with pytest.raises(ValueError, match="version must be an integer"):
            BuildConfig.from_dict({"version": "1"})

    def test_bad_enum_value(self) -> None:
        with pytest.raises(ValueError, match="severity.abi_breaking"):
            BuildConfig.from_dict({"severity": {"abi_breaking": "nope"}})

    def test_multiple_findings_all_reported(self) -> None:
        """A single bad file reports every problem at once, not just the first."""
        with pytest.raises(ValueError) as exc_info:
            BuildConfig.from_dict({"bogus_top": 1, "version": "not-an-int"})
        message = str(exc_info.value)
        assert "bogus_top" in message
        assert "version must be an integer" in message

    def test_known_good_config_does_not_raise(self) -> None:
        """A config using only known keys/blocks/types must still load cleanly
        (guards against the hardening becoming stricter than the real schema)."""
        cfg = BuildConfig.from_dict(
            {
                "version": 1,
                "build": {
                    "system": "cmake",
                    "query": "cmake --version",
                    "compile_db": "x.json",
                },
                "sources": {
                    "public_headers": ["a.h"],
                    "exclude": "internal/**",
                    "graph": "full",
                },
                "severity": {
                    "preset": "strict",
                    "abi_breaking": "error",
                    "potential_breaking": "warning",
                    "quality_issues": "info",
                    "addition": "info",
                },
                "scope": {
                    "public": True,
                    "collapse_versioned_symbols": False,
                    "public_symbols": ["_Z3foov"],
                    "show_redundant": False,
                },
                "suppression": {"strict": True, "require_justification": False},
                "source": {"method": "s4"},
                "compile": {
                    "frontend": "clang",
                    "std": "c++20",
                    "include_dirs": ["include"],
                    "defines": ["FOO=1"],
                    "sysroot": "/opt/sysroot",
                    "nostdinc": True,
                },
                "debug": {
                    "format": "dwarf",
                    "dwarf_only": True,
                    "debuginfod": True,
                    "debuginfod_url": "https://example.invalid",
                },
                "exit_code_scheme": "severity",
                # Keys parsed by sibling modules, not from_dict itself.
                "risk_rules": {},
                "crosschecks": {},
            }
        )
        assert cfg.version == 1
        assert cfg.exit_code_scheme == "severity"
        assert cfg.compile_frontend == "clang"


# ── end-to-end: a bad .abicheck.yml exits 64 through a real command ─────────


class TestBadConfigExitsUsageError:
    def test_compare_with_bad_config_exits_64(self, tmp_path: Path) -> None:
        old_p, new_p = _identical_pair(tmp_path)
        bad_cfg = tmp_path / "bad.yml"
        bad_cfg.write_text(
            "severity:\n  preset: strict\n  bogus: 1\n", encoding="utf-8"
        )
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--config", str(bad_cfg)]
        )
        assert result.exit_code == 64, result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)
        combined = result.output + (result.stderr or "")
        assert "bogus" in combined

    def test_compare_with_unknown_top_level_key_exits_64(self, tmp_path: Path) -> None:
        old_p, new_p = _identical_pair(tmp_path)
        bad_cfg = tmp_path / "bad.yml"
        bad_cfg.write_text("not_a_real_key: 1\n", encoding="utf-8")
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--config", str(bad_cfg)]
        )
        assert result.exit_code == 64, result.output
        combined = result.output + (result.stderr or "")
        assert "not_a_real_key" in combined

    def test_compare_with_wrong_type_value_exits_64(self, tmp_path: Path) -> None:
        old_p, new_p = _identical_pair(tmp_path)
        bad_cfg = tmp_path / "bad.yml"
        bad_cfg.write_text('version: "1"\n', encoding="utf-8")
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--config", str(bad_cfg)]
        )
        assert result.exit_code == 64, result.output
        combined = result.output + (result.stderr or "")
        assert "version" in combined

    def test_compare_with_good_config_still_works(self, tmp_path: Path) -> None:
        old_p, new_p = _identical_pair(tmp_path)
        good_cfg = tmp_path / "good.yml"
        good_cfg.write_text("severity:\n  preset: strict\n", encoding="utf-8")
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--config", str(good_cfg)]
        )
        assert result.exit_code == 0, result.output


# ── removed commands: no aliases, no deprecation warnings ───────────────────


class TestRemovedCommandsAreGone:
    @pytest.mark.parametrize("cmd", ["init", "config", "doctor"])
    def test_removed_command_is_no_such_command(self, cmd: str) -> None:
        """Same convention as any other unknown command name (e.g. a typo) —
        Click's ordinary "No such command" usage error, remapped to exit 64
        by the root group (see abicheck/cli.py's _AbicheckGroup)."""
        baseline = CliRunner().invoke(main, ["definitely-not-a-real-command"])
        result = CliRunner().invoke(main, [cmd])
        assert result.exit_code == baseline.exit_code == 64
        assert "No such command" in result.output
        assert cmd in result.output

    def test_no_config_group_no_init_no_doctor_in_command_list(self) -> None:
        assert "init" not in main.commands
        assert "config" not in main.commands
        assert "doctor" not in main.commands
