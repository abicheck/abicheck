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

"""ADR-037 D4/D12 (G22 Phase 5): CLI ↔ `.abicheck.yml` config rebalance.

Per-category severity, scope/FP tuning, suppression hygiene, the precise S-axis,
and the exit-code scheme move to `.abicheck.yml`; the CLI keeps coarse overrides.
Precedence is **CLI > config > built-in default**, resolved once. The exit-code
scheme is explicit (D12): passing `--severity-*` no longer silently flips it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from abicheck.buildsource.inline import BuildConfig, load_build_config
from abicheck.cli import main
from abicheck.cli_helpers_compare import resolve_compare_config
from abicheck.cli_options import COMPARE_FLAG_BUDGET, count_visible_options
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.severity import SeverityLevel


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _api_break_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """Drop a default argument: an API_BREAK (recompile) but not a binary break."""
    old = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True, functions=[
        Function(name="foo", mangled="_Z3foov", return_type="int",
                 params=[Param(name="x", type="int", default="0")],
                 visibility=Visibility.PUBLIC),
    ])
    new = AbiSnapshot(library="libfoo.so", version="2.0", from_headers=True, functions=[
        Function(name="foo", mangled="_Z3foov", return_type="int",
                 params=[Param(name="x", type="int")],
                 visibility=Visibility.PUBLIC),
    ])
    return old, new


# ── precedence: CLI > config > default ─────────────────────────────────────────

class TestConfigPrecedence:
    def test_default_when_nothing_set(self) -> None:
        r = resolve_compare_config(
            None,
            cli_severity_preset=None, cli_severity_abi_breaking=None,
            cli_severity_potential_breaking=None, cli_severity_quality_issues=None,
            cli_severity_addition=None, cli_scope_public=None,
            cli_collapse_versioned_symbols=None,
        )
        assert r.severity.abi_breaking == SeverityLevel.ERROR  # preset default
        assert r.scope_public is True
        assert r.collapse_versioned_symbols is False
        assert r.strict_suppressions is False
        assert r.exit_code_scheme == "legacy"  # auto, no severity in effect
        assert r.severity_active is False

    def test_config_beats_default(self) -> None:
        cfg = BuildConfig(
            severity_abi_breaking="warning",
            scope_public=False,
            collapse_versioned_symbols=True,
            suppression_strict=True,
            suppression_require_justification=True,
        )
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None, cli_severity_abi_breaking=None,
            cli_severity_potential_breaking=None, cli_severity_quality_issues=None,
            cli_severity_addition=None, cli_scope_public=None,
            cli_collapse_versioned_symbols=None,
        )
        assert r.severity.abi_breaking == SeverityLevel.WARNING
        assert r.scope_public is False
        assert r.collapse_versioned_symbols is True
        assert r.strict_suppressions is True
        assert r.require_justification is True
        # A config severity value makes severity "active" → auto resolves severity.
        assert r.severity_active is True
        assert r.exit_code_scheme == "severity"

    def test_cli_beats_config(self) -> None:
        cfg = BuildConfig(
            severity_abi_breaking="warning",
            scope_public=False,
            suppression_strict=True,
        )
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None,
            cli_severity_abi_breaking="error",   # CLI override
            cli_severity_potential_breaking=None, cli_severity_quality_issues=None,
            cli_severity_addition=None,
            cli_scope_public=True,               # CLI override
            cli_collapse_versioned_symbols=None,
            cli_strict_suppressions=False,       # CLI override
        )
        assert r.severity.abi_breaking == SeverityLevel.ERROR
        assert r.scope_public is True
        assert r.strict_suppressions is False

    def test_public_symbols_merge_config_and_cli(self) -> None:
        cfg = BuildConfig(public_symbols=["_Z3foov"])
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None, cli_severity_abi_breaking=None,
            cli_severity_potential_breaking=None, cli_severity_quality_issues=None,
            cli_severity_addition=None, cli_scope_public=None,
            cli_collapse_versioned_symbols=None,
            cli_public_symbols=("_Z3barv",),
        )
        assert set(r.public_symbols) == {"_Z3foov", "_Z3barv"}

    def test_exit_scheme_cli_beats_config(self) -> None:
        cfg = BuildConfig(exit_code_scheme="legacy")
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None, cli_severity_abi_breaking=None,
            cli_severity_potential_breaking=None, cli_severity_quality_issues=None,
            cli_severity_addition=None, cli_scope_public=None,
            cli_collapse_versioned_symbols=None,
            cli_exit_code_scheme="severity",
        )
        assert r.exit_code_scheme == "severity"


# ── round-trip ─────────────────────────────────────────────────────────────────

class TestConfigRoundtrip:
    def test_dataclass_roundtrip(self) -> None:
        cfg = BuildConfig(
            system="cmake", query="cmake -S . -B build", compile_db="build/x.json",
            public_headers=["include"], exclude=["internal"], graph_detail="full",
            severity_preset="strict", severity_abi_breaking="error",
            severity_potential_breaking="warning", severity_quality_issues="info",
            severity_addition="info", scope_public=False,
            collapse_versioned_symbols=True, public_symbols=["_Z3foov"],
            suppression_strict=True, suppression_require_justification=False,
            source_method="s5", exit_code_scheme="severity", version=2,
        )
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg

    def test_yaml_file_roundtrip(self, tmp_path: Path) -> None:
        cfg = BuildConfig(
            severity_preset="strict", scope_public=False,
            suppression_strict=True, exit_code_scheme="legacy", version=1,
        )
        p = tmp_path / ".abicheck.yml"
        p.write_text(yaml.safe_dump(cfg.to_dict()), encoding="utf-8")
        assert load_build_config(p) == cfg

    def test_empty_roundtrip(self) -> None:
        cfg = BuildConfig()
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg

    def test_invalid_severity_level_rejected(self) -> None:
        with pytest.raises(ValueError, match="severity.abi_breaking"):
            BuildConfig.from_dict({"severity": {"abi_breaking": "nope"}})

    def test_invalid_exit_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="exit_code_scheme"):
            BuildConfig.from_dict({"exit_code_scheme": "loud"})


# ── flag budget (D10.5) ────────────────────────────────────────────────────────

class TestFlagBudget:
    def test_compare_under_budget(self) -> None:
        visible = count_visible_options(main.commands["compare"])
        assert visible <= COMPARE_FLAG_BUDGET, (
            f"compare exposes {visible} visible flags (> {COMPARE_FLAG_BUDGET}); "
            "demote stable project settings to .abicheck.yml (ADR-037 D4)."
        )

    def test_demoted_families_are_hidden(self) -> None:
        cmd = main.commands["compare"]
        hidden = {
            opt
            for p in cmd.params
            if getattr(p, "param_type_name", None) == "option" and getattr(p, "hidden", False)
            for opt in p.opts
        }
        for flag in (
            "--severity-abi-breaking", "--severity-quality-issues",
            "--strict-suppressions", "--require-justification",
            "--collapse-versioned-symbols", "--public-symbol",
        ):
            assert flag in hidden, f"{flag} should be hidden (demoted to config, D4)"

    def test_coarse_overrides_stay_visible(self) -> None:
        cmd = main.commands["compare"]
        visible = {
            opt
            for p in cmd.params
            if getattr(p, "param_type_name", None) == "option"
            and not getattr(p, "hidden", False)
            for opt in p.opts
        }
        for flag in ("--severity-preset", "--show-filtered", "--depth",
                     "--exit-code-scheme", "--scope-public-headers"):
            assert flag in visible, f"{flag} must remain a visible coarse override (D4)"


# ── exit-code scheme is explicit (D12) ─────────────────────────────────────────

class TestExitSchemeExplicit:
    def test_severity_flag_no_longer_flips_when_scheme_explicit(self, tmp_path: Path) -> None:
        old, new = _api_break_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)

        # auto (the default): a severity setting flips the scheme to severity, so
        # an API_BREAK (potential_breaking=warning) yields exit 0.
        auto = CliRunner().invoke(
            main, ["compare", str(old_f), str(new_f), "--severity-preset", "default"]
        )
        assert auto.exit_code == 0

        # Explicit legacy: the same severity setting does NOT flip the scheme;
        # the legacy verdict (API_BREAK → 2) stands.
        legacy = CliRunner().invoke(
            main,
            ["compare", str(old_f), str(new_f),
             "--severity-preset", "default", "--exit-code-scheme", "legacy"],
        )
        assert legacy.exit_code == 2

    def test_config_exit_scheme_respected(self, tmp_path: Path) -> None:
        old, new = _api_break_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(yaml.safe_dump({"exit_code_scheme": "legacy"}), encoding="utf-8")
        res = CliRunner().invoke(
            main,
            ["compare", str(old_f), str(new_f), "--config", str(cfg),
             "--severity-preset", "default"],
        )
        # config pins legacy → severity flag does not flip it → API_BREAK == 2.
        assert res.exit_code == 2

    def test_config_severity_drives_exit(self, tmp_path: Path) -> None:
        old, new = _api_break_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        cfg = tmp_path / ".abicheck.yml"
        # Make potential_breaking an error: the API_BREAK now exits 2 under the
        # severity scheme (config severity activates the severity scheme via auto).
        cfg.write_text(
            yaml.safe_dump({"severity": {"potential_breaking": "error"}}),
            encoding="utf-8",
        )
        res = CliRunner().invoke(
            main, ["compare", str(old_f), str(new_f), "--config", str(cfg)]
        )
        assert res.exit_code == 2
