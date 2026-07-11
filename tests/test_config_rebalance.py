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
from abicheck.cli_options import (
    COMPARE_FLAG_BUDGET,
    COMPARE_FLAG_BUDGET_BASE,
    COMPARE_FLAG_BUDGET_RAISES,
    count_visible_options,
)
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

    def test_debug_and_show_redundant_default(self) -> None:
        r = resolve_compare_config(
            None,
            cli_severity_preset=None, cli_severity_abi_breaking=None,
            cli_severity_potential_breaking=None, cli_severity_quality_issues=None,
            cli_severity_addition=None, cli_scope_public=None,
            cli_collapse_versioned_symbols=None,
        )
        assert r.debug_format is None
        assert r.dwarf_only is False
        assert r.debuginfod is False
        assert r.debuginfod_url is None
        assert r.show_redundant is False

    def test_debug_and_show_redundant_config_beats_default(self) -> None:
        # ADR-040 Lever 2: the demoted knobs come from the debug:/scope: blocks.
        cfg = BuildConfig(
            debug_format="dwarf", debug_dwarf_only=True, debug_debuginfod=True,
            debug_debuginfod_url="https://dbginfo.example", scope_show_redundant=True,
        )
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None, cli_severity_abi_breaking=None,
            cli_severity_potential_breaking=None, cli_severity_quality_issues=None,
            cli_severity_addition=None, cli_scope_public=None,
            cli_collapse_versioned_symbols=None,
        )
        assert r.debug_format == "dwarf"
        assert r.dwarf_only is True
        assert r.debuginfod is True
        assert r.debuginfod_url == "https://dbginfo.example"
        assert r.show_redundant is True

    def test_debug_and_show_redundant_cli_beats_config(self) -> None:
        cfg = BuildConfig(
            debug_format="dwarf", debug_dwarf_only=True, scope_show_redundant=True,
        )
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None, cli_severity_abi_breaking=None,
            cli_severity_potential_breaking=None, cli_severity_quality_issues=None,
            cli_severity_addition=None, cli_scope_public=None,
            cli_collapse_versioned_symbols=None,
            cli_debug_format="btf",       # CLI override
            cli_dwarf_only=False,         # CLI override (flag not passed → False here)
            cli_show_redundant=False,     # CLI override
        )
        assert r.debug_format == "btf"
        assert r.dwarf_only is False
        assert r.show_redundant is False


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
            scope_show_redundant=True,
            suppression_strict=True, suppression_require_justification=False,
            source_method="s5",
            debug_format="dwarf", debug_dwarf_only=True, debug_debuginfod=True,
            debug_debuginfod_url="https://dbginfo.example",
            exit_code_scheme="severity", version=2,
        )
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg

    def test_debug_block_invalid_format_rejected(self) -> None:
        with pytest.raises(ValueError, match="debug.format"):
            BuildConfig.from_dict({"debug": {"format": "elf"}})

    def test_debug_block_parses_and_roundtrips(self) -> None:
        cfg = BuildConfig.from_dict({
            "debug": {
                "format": "btf", "dwarf_only": True,
                "debuginfod": True, "debuginfod_url": "https://x.example",
            },
            "scope": {"show_redundant": True},
        })
        assert cfg.debug_format == "btf"
        assert cfg.debug_dwarf_only is True
        assert cfg.debug_debuginfod is True
        assert cfg.debug_debuginfod_url == "https://x.example"
        assert cfg.scope_show_redundant is True
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
            "demote stable project settings to .abicheck.yml (ADR-037 D4), or — if "
            "the flag is a genuine per-run analysis input — add a documented entry "
            "to COMPARE_FLAG_BUDGET_RAISES in cli_options.py."
        )

    def test_budget_is_derived_from_ledger(self) -> None:
        """The ceiling must equal BASE + the documented raises, never a bare number.

        This is the guard that closes the ``--post-manifest`` gap: because the
        only way to raise the budget is to add a rationale-carrying ledger entry,
        a new visible flag can no longer be slipped in by silently consuming
        slack between a hand-set number and the real count.
        """
        assert (
            COMPARE_FLAG_BUDGET
            == COMPARE_FLAG_BUDGET_BASE + len(COMPARE_FLAG_BUDGET_RAISES)
        )

    def test_every_ledger_flag_is_visible_and_documented(self) -> None:
        """Each ledger key must be a currently-visible compare flag with a reason.

        Keeps the ledger honest: a flag later demoted to hidden/config (or removed)
        must have its entry dropped, so the ledger cannot accumulate stale
        justifications for flags the surface no longer exposes.
        """
        cmd = main.commands["compare"]
        visible = {
            opt
            for p in cmd.params
            if getattr(p, "param_type_name", None) == "option"
            and not getattr(p, "hidden", False)
            for opt in p.opts
        }
        for flag, rationale in COMPARE_FLAG_BUDGET_RAISES.items():
            assert flag in visible, (
                f"{flag} is in COMPARE_FLAG_BUDGET_RAISES but is not a visible "
                "compare flag — drop its ledger entry (and adjust BASE if needed)."
            )
            assert rationale.strip(), f"{flag} ledger entry has an empty rationale"

    def test_no_undocumented_visible_flag_beyond_base(self) -> None:
        """Visible count above BASE must be fully covered by ledger entries.

        Equivalent to ``visible <= budget`` today, but stated in ledger terms so
        the failure message points a future author straight at the fix: any flag
        pushing the count past BASE needs a COMPARE_FLAG_BUDGET_RAISES rationale.
        """
        visible = count_visible_options(main.commands["compare"])
        assert visible - COMPARE_FLAG_BUDGET_BASE <= len(COMPARE_FLAG_BUDGET_RAISES), (
            f"compare has {visible} visible flags; BASE is "
            f"{COMPARE_FLAG_BUDGET_BASE} and only {len(COMPARE_FLAG_BUDGET_RAISES)} "
            "raises are documented — add a ledger entry for the new flag."
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
            # ADR-040 Lever 2 (Phase D): debug-resolution + show-redundant demotion
            "--debug-format", "--debuginfod", "--debuginfod-url", "--dwarf-only",
            "--show-redundant",
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
                     "--exit-code-scheme", "--scope-public-headers",
                     # ADR-040 Lever 2 carve-outs: the coarse debug-root override and
                     # the toolchain family (shared with dump/scan) stay visible.
                     "--debug-root", "--gcc-path", "--sysroot"):
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

    def test_config_applies_on_directory_dispatch(self, tmp_path: Path) -> None:
        # ADR-037 D4: a directory (set-input) compare honours .abicheck.yml too —
        # config severity flows through to the per-library fan-out. A config that
        # downgrades abi_breaking to a warning turns a BREAKING removal into a
        # non-error exit under the severity scheme.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True, functions=[
            Function(name="foo", mangled="_Z3foov", return_type="int", visibility=Visibility.PUBLIC),
            Function(name="bar", mangled="_Z3barv", return_type="void", visibility=Visibility.PUBLIC),
        ])
        new = AbiSnapshot(library="libfoo.so", version="2.0", from_headers=True, functions=[
            Function(name="foo", mangled="_Z3foov", return_type="int", visibility=Visibility.PUBLIC),
        ])
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            yaml.safe_dump({"severity": {"abi_breaking": "warning"}}), encoding="utf-8"
        )
        # Without config the removal is BREAKING → exit 4. Pin an empty config so
        # the baseline doesn't pick up an ambient .abicheck.yml from the CWD.
        empty_cfg = tmp_path / "empty.yml"
        empty_cfg.write_text(yaml.safe_dump({}), encoding="utf-8")
        baseline = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--config", str(empty_cfg),
             "--format", "json"],
        )
        assert baseline.exit_code == 4
        # With config downgrading abi_breaking, the fan-out no longer errors.
        res = CliRunner().invoke(
            main,
            ["compare", str(old_dir), str(new_dir), "--config", str(cfg), "--format", "json"],
        )
        assert res.exit_code == 0

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


# ── G22 Phase 7: config forward-compat (version + unknown-key warning) ────────


class TestConfigForwardCompat:
    """ADR-037 §Backward compatibility: `.abicheck.yml` carries `version:`, and an
    unknown key **warns** (never errors) so an older abicheck still loads a config
    written for a newer schema."""

    def test_version_round_trips(self) -> None:
        cfg = BuildConfig.from_dict({"version": 1})
        assert cfg.version == 1
        assert cfg.to_dict()["version"] == 1
        # Round-trip is stable and emits no unknown-key warning.
        assert BuildConfig.from_dict(cfg.to_dict()).version == 1

    def test_unknown_top_key_warns_but_loads(self) -> None:
        with pytest.warns(UserWarning, match="future_feature"):
            cfg = BuildConfig.from_dict(
                {"version": 2, "future_feature": {"enabled": True}}
            )
        # Load still succeeds: known keys parse, the unknown one is ignored.
        assert cfg.version == 2

    def test_unknown_block_key_warns_but_loads(self) -> None:
        with pytest.warns(UserWarning, match=r"sources\.'?nonsense'?"):
            cfg = BuildConfig.from_dict(
                {"sources": {"public_headers": ["api.h"], "nonsense": 1}}
            )
        # The recognized sibling key still parsed.
        assert cfg.public_headers == ["api.h"]

    def test_known_config_does_not_warn(self, recwarn: pytest.WarningsRecorder) -> None:
        BuildConfig.from_dict(
            {
                "version": 1,
                "build": {"system": "cmake"},
                "sources": {"public_headers": ["a.h"], "graph": "full"},
                "severity": {"preset": "strict"},
                "scope": {"public": True},
                "suppression": {"strict": True},
                "source": {"method": "s4"},
                "exit_code_scheme": "severity",
                # Keys parsed by sibling modules must not trip the warning.
                "risk_rules": {},
                "crosschecks": {},
            }
        )
        assert [w for w in recwarn.list if issubclass(w.category, UserWarning)] == []

    def test_load_build_config_unknown_key_warns(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / ".abicheck.yml"
        cfg_path.write_text("version: 3\nbrand_new_block:\n  x: 1\n")
        with pytest.warns(UserWarning, match="brand_new_block"):
            cfg = load_build_config(cfg_path)
        assert cfg.version == 3


def test_collect_help_panels_cover_all_options() -> None:
    """Every `collect` option (bar --help) is grouped into a rich-help panel, so
    the messiest command never regresses to a flat option wall (G22 collect tidy)."""
    import click

    import abicheck.cli_buildsource  # noqa: F401  — registers `collect`
    from abicheck.cli import main
    from abicheck.cli_help import OPTION_GROUPS

    grouped: set[str] = set()
    for panel in OPTION_GROUPS["* collect"]:
        grouped.update(panel["options"])  # type: ignore[arg-type]

    cmd = main.commands["collect"]
    for p in cmd.params:
        if not isinstance(p, click.Option):
            continue
        if "--help" in p.opts:
            continue
        assert grouped & set(p.opts), f"collect option {p.opts} is not in any help panel"
