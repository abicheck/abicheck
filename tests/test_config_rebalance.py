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
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from abicheck.buildsource.inline import BuildConfig, load_build_config
from abicheck.cli import main
from abicheck.cli_helpers_compare import resolve_compare_config
from abicheck.cli_options import (
    COMPARE_FLAG_BUDGET,
    DUMP_FLAG_BUDGET,
    RULINGS_BY_COMMAND,
    count_visible_options,
)
from abicheck.frontends.cli.options.inventory import _HELP_META_OPTION_NAMES
from abicheck.frontends.cli.options.rulings import OptionRuling
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.severity import SeverityLevel


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _api_break_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """Drop a default argument: an API_BREAK (recompile) but not a binary break."""
    old = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                params=[Param(name="x", type="int", default="0")],
                visibility=Visibility.PUBLIC,
            ),
        ],
    )
    new = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        from_headers=True,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                params=[Param(name="x", type="int")],
                visibility=Visibility.PUBLIC,
            ),
        ],
    )
    return old, new


# ── precedence: CLI > config > default ─────────────────────────────────────────


class TestConfigPrecedence:
    def test_default_when_nothing_set(self) -> None:
        r = resolve_compare_config(
            None,
            cli_severity_preset=None,
            cli_scope_public=None,
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
            cli_severity_preset=None,
            cli_scope_public=None,
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
        # Only the keys that still HAVE a CLI flag can be overridden from the
        # command line. --severity-abi-breaking and --strict-suppressions were
        # hidden duplicates of a config key and are gone, so the config value
        # is now the whole answer for those two -- an override the resolver
        # cannot express is the point, not an omission.
        cfg = BuildConfig(
            severity_abi_breaking="warning",
            scope_public=False,
            suppression_strict=True,
        )
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None,
            cli_scope_public=True,  # CLI override
        )
        assert r.scope_public is True
        assert r.severity.abi_breaking == SeverityLevel.WARNING
        assert r.strict_suppressions is True

    def test_public_symbols_come_from_config_only(self) -> None:
        # The --public-symbol/--public-symbols-list overlay was removed with
        # the rest of the hidden config duplicates; scope.public_symbols is
        # the only source, so there is no CLI half left to merge.
        cfg = BuildConfig(public_symbols=["_Z3foov"])
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert set(r.public_symbols) == {"_Z3foov"}

    def test_exit_scheme_has_no_manual_override_any_more(self) -> None:
        # CLI cleanup phase two PR G2: there is no `cli_exit_code_scheme`
        # parameter, and no `BuildConfig.exit_code_scheme` field, to compete
        # over any more -- the scheme is purely derived from whether a
        # severity setting is in effect, exactly like `test_config_beats_
        # default`/`test_default_when_nothing_set` above already show. A
        # CLI `severity_preset` still "wins" in the only sense left: it is
        # itself the thing that activates severity, same as a config one.
        cfg = BuildConfig()  # no exit_code_scheme field exists to set
        r = resolve_compare_config(
            cfg,
            cli_severity_preset="strict",
            cli_scope_public=None,
        )
        assert r.exit_code_scheme == "severity"

    def test_debug_and_show_redundant_default(self) -> None:
        r = resolve_compare_config(
            None,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert r.debug_format is None
        assert r.dwarf_only is False
        assert r.debuginfod is False
        assert r.debuginfod_url is None
        assert r.show_redundant is False

    def test_debug_and_show_redundant_config_beats_default(self) -> None:
        # ADR-040 Lever 2: the demoted knobs come from the debug:/scope: blocks.
        cfg = BuildConfig(
            debug_format="dwarf",
            debug_dwarf_only=True,
            debug_debuginfod=True,
            debug_debuginfod_url="https://dbginfo.example",
            scope_show_redundant=True,
        )
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert r.debug_format == "dwarf"
        assert r.dwarf_only is True
        assert r.debuginfod is True
        assert r.debuginfod_url == "https://dbginfo.example"
        assert r.show_redundant is True

    def test_resolve_dump_debug_format_precedence(self) -> None:
        # ADR-040 L2 (Codex P2): --debug-format beats config; H1 hidden-shim
        # deletion removed the legacy --btf/--ctf/--dwarf spellings this used
        # to also reconcile, so only the selector itself is left to resolve.
        from abicheck.cli_dump_helpers import resolve_dump_debug_format

        assert resolve_dump_debug_format("btf") == "btf"
        # An explicit "auto" returns to auto-detection.
        assert resolve_dump_debug_format("auto") is None
        # Nothing on the command line → None → config wins downstream.
        assert resolve_dump_debug_format(None) is None

    def test_debug_resolution_has_no_cli_override_left(self) -> None:
        """Phase 7 (one-comparison-product.md §4.1, "H" rows): the four
        hidden debug-resolution flags are deleted outright, not merely
        hidden -- `resolve_compare_config` no longer accepts a `cli_*`
        argument for any of them, so config is the only remaining source
        (ADR-068 D5 guard #2, "no escape hatch")."""
        import inspect

        params = inspect.signature(resolve_compare_config).parameters
        for removed in (
            "cli_debug_format",
            "cli_dwarf_only",
            "cli_debuginfod",
            "cli_debuginfod_url",
        ):
            assert removed not in params, (
                f"resolve_compare_config still accepts {removed!r} -- the "
                "Phase 7 override was supposed to be deleted, not just hidden"
            )
        cfg = BuildConfig(
            debug_format="dwarf",
            debug_dwarf_only=True,
            scope_show_redundant=True,
        )
        r = resolve_compare_config(cfg, cli_severity_preset=None, cli_scope_public=None)
        assert r.debug_format == "dwarf"
        assert r.dwarf_only is True
        assert r.show_redundant is True

    def test_release_topology_default(self) -> None:
        """Phase 7d (one-comparison-product.md §4.1): the release/bundle
        topology knobs default to the removed flags' own defaults when
        unset."""
        r = resolve_compare_config(
            None,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert r.on_incomplete_scope == "warn"
        assert r.fail_on_removed_library is False
        assert r.release_dso_only is False
        assert r.release_include_private_dso is False

    def test_release_topology_config_beats_default(self) -> None:
        """Phase 7d: scope.on_incomplete/gate.fail_on_removed_library/
        release.dso_only/release.include_private_dso are the only source --
        no CLI override at all, same shape as bundle_system_providers/
        cohorts above."""
        cfg = BuildConfig(
            scope_on_incomplete="block",
            gate_fail_on_removed_library=True,
            release_dso_only=True,
            release_include_private_dso=True,
        )
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert r.on_incomplete_scope == "block"
        assert r.fail_on_removed_library is True
        assert r.release_dso_only is True
        assert r.release_include_private_dso is True

    def test_release_topology_has_no_cli_override(self) -> None:
        """Phase 7d: `resolve_compare_config` accepts no `cli_*` argument
        for any of the four release/bundle topology knobs (ADR-068 D5 guard
        #2, "no escape hatch") -- matching the debug-resolution precedent
        above."""
        import inspect

        params = inspect.signature(resolve_compare_config).parameters
        for removed in (
            "cli_dso_only",
            "cli_fail_on_removed_library",
            "cli_on_incomplete_scope",
            "cli_include_private_dso",
        ):
            assert removed not in params, (
                f"resolve_compare_config still accepts {removed!r} -- the "
                "Phase 7d override was supposed to be deleted, not just hidden"
            )

    def test_assurance_require_complete_default(self) -> None:
        """rulings.py deferred-option followup: unset resolves to `False`,
        the former `--require-complete-analysis` flag's own default."""
        r = resolve_compare_config(
            None,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert r.require_complete_analysis is False

    def test_assurance_require_complete_config_beats_default(self) -> None:
        """assurance.require_complete is the only source -- no CLI override
        at all, same shape as the Phase 7d release/bundle topology knobs
        above."""
        cfg = BuildConfig(assurance_require_complete=True)
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert r.require_complete_analysis is True

    def test_assurance_require_complete_has_no_cli_override(self) -> None:
        """`resolve_compare_config` accepts no `cli_*` argument for the
        assurance knob (ADR-068 D5 guard #2, "no escape hatch") -- matching
        the release/bundle topology precedent above."""
        import inspect

        params = inspect.signature(resolve_compare_config).parameters
        assert "cli_require_complete_analysis" not in params

    def test_resource_limits_default(self) -> None:
        """Phase 7g (one-comparison-product.md §4.1/§3 #21): unset resolves
        to `None`, so the caller applies
        `bundle_facts.DEFAULT_MAX_JSON_OBJECT_NODES`."""
        r = resolve_compare_config(
            None,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert r.resource_limits_max_bundle_facts_decode_nodes is None

    def test_resource_limits_config_beats_default(self) -> None:
        """Phase 7g: resource_limits.max_bundle_facts_decode_nodes is the
        only source -- no CLI override at all, same shape as the Phase 7d
        release/bundle topology knobs above."""
        cfg = BuildConfig(resource_limits_max_bundle_facts_decode_nodes=5_000_000)
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert r.resource_limits_max_bundle_facts_decode_nodes == 5_000_000

    def test_resource_limits_has_no_cli_override(self) -> None:
        """Phase 7g: `resolve_compare_config` accepts no `cli_*` argument
        for the resource-limits knob (ADR-068 D5 guard #2, "no escape
        hatch")."""
        import inspect

        params = inspect.signature(resolve_compare_config).parameters
        assert "cli_max_json_object_nodes" not in params

    def test_deployment_default(self) -> None:
        """ADR-020b / ADR-068 D5: the former `compare --env-matrix FILE`,
        now `.abicheck.yml`'s `deployment:` config key -- unset resolves to
        `None` (no declared matrix), same shape as the release-topology
        knobs above."""
        r = resolve_compare_config(
            None,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert r.deployment is None

    def test_deployment_config_beats_default(self) -> None:
        """`deployment:` is the only source -- no CLI override at all."""
        from abicheck.environment_matrix import EnvironmentMatrix

        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        cfg = BuildConfig(deployment=matrix)
        r = resolve_compare_config(
            cfg,
            cli_severity_preset=None,
            cli_scope_public=None,
        )
        assert r.deployment is matrix
        assert r.deployment is not None
        assert r.deployment.runtime_floors == {"GLIBC": "2.28"}

    def test_deployment_has_no_cli_override(self) -> None:
        """`resolve_compare_config` accepts no `cli_*` argument for
        `deployment` (ADR-068 D5 guard #2, "no escape hatch")."""
        import inspect

        params = inspect.signature(resolve_compare_config).parameters
        for removed in ("cli_env_matrix", "cli_env_matrix_path", "cli_deployment"):
            assert removed not in params


# ── round-trip ─────────────────────────────────────────────────────────────────


class TestConfigRoundtrip:
    def test_dataclass_roundtrip(self) -> None:
        cfg = BuildConfig(
            system="cmake",
            query="cmake -S . -B build",
            compile_db="build/x.json",
            public_headers=["include"],
            exclude=["internal"],
            graph_detail="full",
            severity_preset="strict",
            severity_abi_breaking="error",
            severity_potential_breaking="warning",
            severity_quality_issues="info",
            severity_addition="info",
            scope_public=False,
            collapse_versioned_symbols=True,
            public_symbols=["_Z3foov"],
            scope_show_redundant=True,
            suppression_strict=True,
            suppression_require_justification=False,
            source_method="s5",
            debug_format="dwarf",
            debug_dwarf_only=True,
            debug_debuginfod=True,
            debug_debuginfod_url="https://dbginfo.example",
            version=2,
        )
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg

    def test_debug_block_invalid_format_rejected(self) -> None:
        with pytest.raises(ValueError, match="debug.format"):
            BuildConfig.from_dict({"debug": {"format": "elf"}})

    def test_debug_block_parses_and_roundtrips(self) -> None:
        cfg = BuildConfig.from_dict(
            {
                "debug": {
                    "format": "btf",
                    "dwarf_only": True,
                    "debuginfod": True,
                    "debuginfod_url": "https://x.example",
                },
                "scope": {"show_redundant": True},
            }
        )
        assert cfg.debug_format == "btf"
        assert cfg.debug_dwarf_only is True
        assert cfg.debug_debuginfod is True
        assert cfg.debug_debuginfod_url == "https://x.example"
        assert cfg.scope_show_redundant is True
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg

    def test_resource_limits_block_invalid_type_rejected(self) -> None:
        with pytest.raises(
            ValueError, match="resource_limits.max_bundle_facts_decode_nodes"
        ):
            BuildConfig.from_dict(
                {"resource_limits": {"max_bundle_facts_decode_nodes": "lots"}}
            )

    def test_resource_limits_block_parses_and_roundtrips(self) -> None:
        cfg = BuildConfig.from_dict(
            {"resource_limits": {"max_bundle_facts_decode_nodes": 5_000_000}}
        )
        assert cfg.resource_limits_max_bundle_facts_decode_nodes == 5_000_000
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg

    def test_deployment_block_invalid_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="deployment"):
            BuildConfig.from_dict({"deployment": "not-a-mapping"})

    def test_deployment_block_invalid_runtime_floor_rejected(self) -> None:
        # Delegated straight to EnvironmentMatrix.from_dict's own validation
        # (an unquoted YAML float loses trailing zeros) -- proves the
        # delegation actually runs, not just a shape check.
        with pytest.raises(ValueError, match="runtime_floors"):
            BuildConfig.from_dict({"deployment": {"runtime_floors": {"GLIBC": 2.4}}})

    def test_deployment_block_parses_and_roundtrips(self) -> None:
        cfg = BuildConfig.from_dict(
            {
                "deployment": {
                    "target_os": "linux",
                    "runtime_floors": {"GLIBC": "2.28", "GLIBCXX": "3.4.28"},
                    "sycl": {
                        "implementation": "dpcpp",
                        "backends": ["level_zero", "opencl"],
                    },
                }
            }
        )
        assert cfg.deployment is not None
        assert cfg.deployment.target_os == "linux"
        assert cfg.deployment.runtime_floors == {
            "GLIBC": "2.28",
            "GLIBCXX": "3.4.28",
        }
        assert cfg.deployment.sycl.implementation == "dpcpp"
        # `backends` is frozen into a `tuple` at construction (Codex review,
        # P2 follow-up, PR #1221's hash-invariant fix).
        assert cfg.deployment.sycl.backends == ("level_zero", "opencl")
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg

    def test_deployment_block_absent_is_none(self) -> None:
        cfg = BuildConfig.from_dict({})
        assert cfg.deployment is None
        assert "deployment" not in cfg.to_dict()

    def test_deployment_explicitly_empty_roundtrips_non_none(self) -> None:
        """Codex review finding 1: `deployment: {}` (explicitly selected, but
        empty) must not collapse into `deployment` absent (`None`) on a
        `to_dict()`/`from_dict()` round-trip -- the two are different
        states (`env_matrix_source_sha256` identity depends on telling them
        apart), unlike every other block, whose emptiness genuinely does
        mean "unset"."""
        from abicheck.environment_matrix import EnvironmentMatrix

        cfg = BuildConfig(deployment=EnvironmentMatrix())
        assert cfg.deployment is not None
        d = cfg.to_dict()
        assert "deployment" in d
        assert d["deployment"] == {}

        reloaded = BuildConfig.from_dict(d)
        assert reloaded.deployment is not None
        assert reloaded.deployment == EnvironmentMatrix()
        assert reloaded == cfg

        # Contrast: no `deployment:` key at all still round-trips to `None`,
        # not to the same non-`None` empty matrix.
        absent = BuildConfig.from_dict({})
        assert absent.deployment is None
        assert "deployment" not in absent.to_dict()

    def test_deployment_block_rejects_unknown_top_level_key(self) -> None:
        """Codex review finding 2: a typo'd top-level `deployment:` key
        (`runtime_floor` for `runtime_floors`) is a hard config error, not a
        silently-ignored, log-only warning -- unlike `EnvironmentMatrix.
        from_dict`'s own lenient default for a direct typed-API/`--env-
        matrix`-era caller, `.abicheck.yml`'s strict-schema contract applies
        to the embedded `deployment:` block too."""
        with pytest.raises(ValueError, match="unknown key"):
            BuildConfig.from_dict({"deployment": {"runtime_floor": {"GLIBC": "2.28"}}})

    def test_deployment_block_rejects_unknown_nested_sycl_key(self) -> None:
        """Same strict contract for a nested `sycl:`/`cuda:` subkey typo."""
        with pytest.raises(ValueError, match="unknown key"):
            BuildConfig.from_dict({"deployment": {"sycl": {"backend": ["level_zero"]}}})

    def test_deployment_block_rejects_unknown_nested_cuda_key(self) -> None:
        with pytest.raises(ValueError, match="unknown key"):
            BuildConfig.from_dict(
                {"deployment": {"cuda": {"gpu_architecture": ["sm_80"]}}}
            )

    def test_yaml_file_roundtrip(self, tmp_path: Path) -> None:
        cfg = BuildConfig(
            severity_preset="strict",
            scope_public=False,
            suppression_strict=True,
            version=1,
        )
        p = tmp_path / ".abicheck.yml"
        p.write_text(yaml.safe_dump(cfg.to_dict()), encoding="utf-8")
        assert load_build_config(p) == cfg

    def test_empty_roundtrip(self) -> None:
        cfg = BuildConfig()
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg

    def test_top_level_exit_code_scheme_key_no_longer_exists(self) -> None:
        # CLI cleanup phase two PR G2 deleted the top-level `exit_code_
        # scheme:` key entirely (`BuildConfig` no longer has a matching
        # field at all) -- it now falls through to the standard unknown-
        # top-level-key path, which is a hard `ValueError`
        # (`_validate_structure`), same as any other unrecognized key.
        with pytest.raises(ValueError, match="exit_code_scheme"):
            BuildConfig.from_dict({"exit_code_scheme": "auto"})

    def test_invalid_severity_level_rejected(self) -> None:
        with pytest.raises(ValueError, match="severity.abi_breaking"):
            BuildConfig.from_dict({"severity": {"abi_breaking": "nope"}})


# ── flag budget (D10.5) ────────────────────────────────────────────────────────


class TestFlagBudget:
    """ADR-068 D5 / plan Phase 7k: an *exact bijection* between each
    command's visible options and its written rulings.

    This replaces the superseded ``BASE + len(RAISES)`` budget, whose own
    docstring claimed "a new visible flag cannot be slipped in by silently
    consuming slack" while the assertion was ``visible <= budget``. Because
    ``BASE`` was never lowered for every removed flag, the two diverged:
    measured at replacement time, ``visible=48`` against a budget of ``57``
    -- nine flags of slack, one of which (``--budget``) had already landed
    with no ledger entry at all. ``test_the_superseded_budget_shape_would_
    have_missed_an_unruled_flag`` below is the executable statement of that
    bug class rather than a prose note: it constructs the old comparison
    over a surface with an unruled flag and shows it passing.
    """

    @pytest.mark.parametrize("command", sorted(RULINGS_BY_COMMAND))
    def test_every_visible_option_carries_a_ruling(self, command: str) -> None:
        rulings = RULINGS_BY_COMMAND[command]
        missing = sorted(_visible_canonical_flags(command) - rulings.keys())
        assert not missing, (
            f"{command} exposes {missing} with no ADR-068 D5 ruling. Add an "
            "entry to frontends/cli/options/rulings.py saying which of D5's "
            "three guards lets the option in -- a per-run operand, not a "
            "duplicate spelling, not an analysis-disabling hatch -- or "
            "demote the setting to .abicheck.yml."
        )

    @pytest.mark.parametrize("command", sorted(RULINGS_BY_COMMAND))
    def test_no_ruling_outlives_its_option(self, command: str) -> None:
        rulings = RULINGS_BY_COMMAND[command]
        stale = sorted(rulings.keys() - _visible_canonical_flags(command))
        assert not stale, (
            f"{command} rulings name {stale}, which are no longer visible "
            "options -- drop the entries so the table cannot accumulate "
            "justifications for surface that no longer exists."
        )

    @pytest.mark.parametrize("command", sorted(RULINGS_BY_COMMAND))
    def test_the_budget_is_exactly_the_ruled_set(self, command: str) -> None:
        """No slack, by construction: the ceiling *is* the ruled set's size."""
        budget = {"compare": COMPARE_FLAG_BUDGET, "dump": DUMP_FLAG_BUDGET}[command]
        assert budget == len(RULINGS_BY_COMMAND[command])
        assert count_visible_options(main.commands[command]) == budget

    @pytest.mark.parametrize("command", sorted(RULINGS_BY_COMMAND))
    def test_every_ruling_is_substantive(self, command: str) -> None:
        for flag, ruling in RULINGS_BY_COMMAND[command].items():
            assert ruling.rationale.strip(), f"{flag} has an empty rationale"
            assert len(ruling.rationale) >= 60, (
                f"{flag}'s rationale is too short to be a ruling -- state "
                "which guard it clears and why, the way 7d/7i did."
            )

    @pytest.mark.parametrize("command", sorted(RULINGS_BY_COMMAND))
    def test_a_deferral_names_its_blocker(self, command: str) -> None:
        """A deferral without an owner silently becomes a permanent keep."""
        for flag, ruling in RULINGS_BY_COMMAND[command].items():
            if ruling.disposition == "deferred":
                assert ruling.blocker, f"{flag} is deferred with no blocker"
            else:
                assert ruling.blocker is None, (
                    f"{flag} is a keep but names a blocker -- a keep pending "
                    "someone else's work is a deferral, say so."
                )

    def test_a_deferral_and_a_keep_are_structurally_distinguishable(self) -> None:
        """The dataclass rejects the two ways this table could lie: a
        deferral with nobody on the hook, and a keep dressed as one."""
        with pytest.raises(ValueError):
            OptionRuling("deferred", "x" * 80)
        with pytest.raises(ValueError):
            OptionRuling("per_run_operand", "x" * 80, blocker="someday")

    def test_the_superseded_budget_shape_would_have_missed_an_unruled_flag(
        self,
    ) -> None:
        """The bug class, executed rather than described.

        Reconstructs the retired ``visible <= BASE + len(RAISES)`` check over
        a surface carrying one flag that appears in neither, and shows it
        passing -- then shows the bijection check failing on the same input.
        A regression test pinned to ``--budget`` alone would only foreclose
        that one flag; what actually failed was the *shape* of the check, so
        that is what is asserted here.
        """
        base, raises = 41, {f"--ruled-{i}": "why" for i in range(16)}
        visible_with_an_unruled_flag = 48

        assert visible_with_an_unruled_flag <= base + len(raises)

        rulings = {flag: _keep_stub() for flag in raises}
        live = set(raises) | {"--slipped-in-unruled"}
        assert sorted(live - rulings.keys()) == ["--slipped-in-unruled"]


def _keep_stub() -> OptionRuling:
    return OptionRuling("per_run_operand", "stub rationale, long enough to pass")


def _visible_canonical_flags(command: str) -> set[str]:
    """Each visible option's canonical (longest) spelling, help meta aside."""
    return {
        max(p.opts, key=len)
        for p in main.commands[command].params
        if getattr(p, "param_type_name", None) == "option"
        and not getattr(p, "hidden", False)
        and getattr(p, "name", None) not in _HELP_META_OPTION_NAMES
    }


class TestRemovedConfigDuplicates:
    """Split out of ``TestFlagBudget`` when Phase 7k replaced that class's
    budget assertions: these pin *absence* of the removed hidden/config
    duplicates, which is a separate contract from the ruling bijection."""

    #: The hidden-flag families that became *removed* families. A hidden flag
    #: is still a flag: it parses, it takes precedence over the config key it
    #: duplicates, and every one of these duplicated a key `.abicheck.yml`
    #: already owned. This PR deletes them outright rather than keeping a
    #: second, invisible spelling of one setting, so the contract these tests
    #: pin is absence, not concealment -- a stronger claim than the one they
    #: made before, and the reason they are not simply deleted alongside the
    #: flags: "hidden" is exactly the state a re-introduction would land in.
    REMOVED_CONFIG_DUPLICATES = (
        "--severity-abi-breaking",
        "--severity-potential-breaking",
        "--severity-quality-issues",
        "--severity-addition",
        "--strict-suppressions",
        "--require-justification",
        "--collapse-versioned-symbols",
        "--public-symbol",
        "--public-symbols-list",
        "--show-redundant",
        "--no-show-redundant",
        # ADR-068 D5 / Phase 7a (one-comparison-product.md §6 Phase 7 item
        # 7a): the debug-resolution knobs joined this list too -- previously
        # the one family D5 exempted (hidden-but-kept, see the now-removed
        # test_debug_resolution_family_stays_hidden), but "hidden but
        # accepted still counts as public surface" (ADR-068 D5) applies to
        # them exactly the same as every other entry above.
        "--debug-format",
        "--debuginfod",
        "--debuginfod-url",
        "--dwarf-only",
        "--no-debuginfod",
        "--no-dwarf-only",
        # Phase 7d (one-comparison-product.md §4.1): the release/bundle
        # topology knobs joined this list too -- gate.fail_on_removed_library/
        # release.dso_only/release.include_private_dso/scope.on_incomplete
        # in .abicheck.yml are their only source now, no CLI escape hatch.
        "--dso-only",
        "--fail-on-removed-library",
        "--no-fail-on-removed-library",
        "--include-private-dso",
        "--on-incomplete-scope",
        # rulings.py deferred-option followup: assurance.require_complete
        # in .abicheck.yml is the only source now, no CLI escape hatch --
        # same shape as the release/bundle topology knobs above.
        "--require-complete-analysis",
        # ADR-020b / ADR-068 D5: the declared-deployment-constraints flag
        # joined this list too -- `.abicheck.yml`'s `deployment:` config key
        # (`BuildConfig.deployment`, embedding `EnvironmentMatrix`'s own
        # YAML shape via `EnvironmentMatrix.from_dict`) is its only source
        # now, no CLI escape hatch.
        "--env-matrix",
        # `dump --build-target` (CLI cleanup, the build-target retirement):
        # `.abicheck.yml`'s `build.targets` is its only source now. Unlike
        # every entry above, this one was never a `compare` flag at all --
        # it's here anyway, over the shared list rather than a `dump`-only
        # one, per the generalization below: this class's own contract is
        # "no *command's* param set carries this dead spelling", and a
        # command that never had it trivially satisfies that already.
        "--build-target",
    )

    @staticmethod
    def _option_spellings(cmd: Any, *, hidden_only: bool = False) -> set[str]:
        return {
            opt
            for p in cmd.params
            if getattr(p, "param_type_name", None) == "option"
            and (not hidden_only or getattr(p, "hidden", False))
            for opt in (*p.opts, *p.secondary_opts)
        }

    # `scan` was removed outright (ADR-068 Phase 6) -- `main.commands` no
    # longer has an entry for it at all, which made this parametrize's own
    # `[scan]` case a `KeyError` rather than a real "flag absent" assertion
    # (found while generalizing this class to also cover `dump`, the first
    # `dump`-side removal it needs to track: a hardcoded `compare`/`scan`-only
    # check here was itself a latent gap for exactly the same reason a
    # missing bucket in `canonical_identity_contract.py` is one -- an
    # omission that fails nothing, anywhere, until the exact case it misses
    # shows up). `["compare", "dump"]` is every command this class's
    # `REMOVED_CONFIG_DUPLICATES` entries can actually appear on today.
    @pytest.mark.parametrize("command", ["compare", "dump"])
    def test_demoted_families_are_gone(self, command: str) -> None:
        # `scan` was itself retired outright (ADR-068) and is no longer a
        # registered command at all -- `main.commands["scan"]` would raise
        # `KeyError` rather than name a command with the flags re-added.
        spellings = self._option_spellings(main.commands[command])
        for flag in self.REMOVED_CONFIG_DUPLICATES:
            assert flag not in spellings, (
                f"{flag} duplicates an .abicheck.yml key and was removed from "
                f"{command}; re-adding it as a hidden flag is the drift this "
                "pins against."
            )

    def test_debug_resolution_flags_deleted_outright(self) -> None:
        """Phase 7 (one-comparison-product.md §4.1, "H" rows): the four
        hidden debug-resolution flags are gone from `compare` entirely --
        neither hidden nor visible -- since this repo runs no deprecation
        window. `.abicheck.yml`'s `debug:` block is their only surviving
        spelling. `--debug-root` (a per-run evidence input, ADR-068 D5
        guard #3) is unaffected and stays visible."""
        cmd = main.commands["compare"]
        hidden = self._option_spellings(cmd, hidden_only=True)
        visible = self._option_spellings(cmd, hidden_only=False)
        for flag in (
            "--debug-format",
            "--debuginfod",
            "--debuginfod-url",
            "--dwarf-only",
            "--no-debuginfod",
            "--no-dwarf-only",
        ):
            assert flag not in hidden, f"{flag} should be deleted outright, not hidden"
            assert flag not in visible, f"{flag} should be deleted outright"
        assert "--debug-root" in visible

    @pytest.mark.parametrize(
        "flag",
        [
            "--dwarf-only",
            "--no-dwarf-only",
            "--debuginfod",
            "--no-debuginfod",
            "--debuginfod-url",
            "--debug-format",
        ],
    )
    def test_removed_debug_flags_exit_usage_error_on_compare(
        self, tmp_path: Path, flag: str
    ) -> None:
        """ADR-068 D5 / Phase 7a: each removed hidden flag exits 64 with
        Click's standard 'No such option' on `compare` -- the old spelling
        must not silently resolve to anything, hidden or otherwise."""
        old = tmp_path / "old.so"
        new = tmp_path / "new.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 100)
        new.write_bytes(b"\x7fELF" + b"\x00" * 100)
        # A value-taking flag (--debuginfod-url/--debug-format) needs an
        # operand or Click's own "no such option" would be pre-empted by
        # missing-argument handling for the *next* token; the boolean flags
        # take none.
        extra = ["x"] if flag in ("--debuginfod-url", "--debug-format") else []
        result = CliRunner().invoke(
            main,
            ["compare", str(old), str(new), flag, *extra],
        )
        assert result.exit_code == 64, result.output
        assert "No such option" in result.output
        assert flag in result.output

    @pytest.mark.parametrize(
        "flag",
        [
            "--dso-only",
            "--fail-on-removed-library",
            "--no-fail-on-removed-library",
            "--include-private-dso",
            "--on-incomplete-scope",
        ],
    )
    def test_removed_release_topology_flags_exit_usage_error_on_compare(
        self, tmp_path: Path, flag: str
    ) -> None:
        """Phase 7d (one-comparison-product.md §4.1): each removed
        release/bundle topology flag exits 64 with Click's standard
        'No such option' on `compare` -- the old spelling must not
        silently resolve to anything, hidden or otherwise."""
        old = tmp_path / "old.so"
        new = tmp_path / "new.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 100)
        new.write_bytes(b"\x7fELF" + b"\x00" * 100)
        # --on-incomplete-scope needs an operand or Click's own "no such
        # option" would be pre-empted by missing-argument handling for the
        # *next* token; the boolean flags take none.
        extra = ["warn"] if flag == "--on-incomplete-scope" else []
        result = CliRunner().invoke(
            main,
            ["compare", str(old), str(new), flag, *extra],
        )
        assert result.exit_code == 64, result.output
        assert "No such option" in result.output
        assert flag in result.output

    def test_coarse_overrides_stay_visible(self) -> None:
        cmd = main.commands["compare"]
        visible = {
            opt
            for p in cmd.params
            if getattr(p, "param_type_name", None) == "option"
            and not getattr(p, "hidden", False)
            for opt in p.opts
        }
        # one-comparison-product.md Phase 5 removed --show-filtered (the
        # ledger it echoed is unconditional; `--view filtered` renders it),
        # so `--view` is the coarse rendering override that stays visible.
        for flag in (
            "--severity-preset",
            "--view",
            "--depth",
            "--scope-public-headers",
            # ADR-040 Lever 2 carve-out: the coarse debug-root
            # override stays visible.
            "--debug-root",
        ):
            assert flag in visible, f"{flag} must remain a visible coarse override (D4)"
        # Phase 7 (one-comparison-product.md §4.1): the toolchain family
        # (--compiler/--compiler-prefix/--compiler-option/--sysroot/
        # --nostdinc/--ast-frontend), unlike --debug-root, is now CONFIG-only
        # with no CLI spelling at all -- neither hidden nor visible.
        hidden = self._option_spellings(cmd, hidden_only=True)
        for flag in ("--compiler", "--sysroot", "--ast-frontend", "--nostdinc"):
            assert flag not in visible and flag not in hidden, (
                f"{flag} should be deleted outright (compile.* config only)"
            )


# ── exit-code scheme is fully automatic (CLI cleanup phase two PR G2) ──────────


class TestExitSchemeExplicit:
    """Before PR G2 (ADR-037 D12), an explicit ``--exit-code-scheme``/
    ``.abicheck.yml`` ``exit_code_scheme:`` key could force ``legacy``
    regardless of a severity setting also being in effect. PR G2 deleted
    that manual override -- both the CLI flag and the config key -- so a
    severity setting now *always* flips the scheme, with nothing left able
    to hold it at ``legacy`` instead."""

    def test_severity_flag_always_flips_the_scheme_now(self, tmp_path: Path) -> None:
        old, new = _api_break_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)

        # A severity setting flips the scheme to severity, so an API_BREAK
        # (potential_breaking=warning) yields exit 0.
        auto = CliRunner().invoke(
            main, ["compare", str(old_f), str(new_f), "--severity-preset", "default"]
        )
        assert auto.exit_code == 0

        # No `--exit-code-scheme legacy` exists any more to hold it at the
        # legacy verdict (API_BREAK -> 2) instead -- the flag itself is gone.
        removed_flag = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_f),
                str(new_f),
                "--severity-preset",
                "default",
                "--exit-code-scheme",
                "legacy",
            ],
        )
        assert removed_flag.exit_code == 64
        assert "No such option" in removed_flag.output

    def test_config_exit_scheme_key_no_longer_exists(self, tmp_path: Path) -> None:
        old, new = _api_break_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(yaml.safe_dump({"exit_code_scheme": "legacy"}), encoding="utf-8")
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_f),
                str(new_f),
                "--config",
                str(cfg),
                "--severity-preset",
                "default",
            ],
        )
        # The unrecognized `exit_code_scheme:` key is a hard error, same as
        # any other unknown `.abicheck.yml` top-level key
        # (`BuildConfig._validate_structure`) -- a config written before PR
        # G2's removal no longer loads at all, rather than silently no
        # longer pinning anything.
        assert res.exit_code == 64, res.output
        assert "exit_code_scheme" in res.output

    def test_config_applies_on_directory_dispatch(self, tmp_path: Path) -> None:
        # ADR-037 D4: a directory (set-input) compare honours .abicheck.yml too —
        # config severity flows through to the per-library fan-out. A config that
        # downgrades abi_breaking to a warning turns a BREAKING removal into a
        # non-error exit under the severity scheme.
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            functions=[
                Function(
                    name="foo",
                    mangled="_Z3foov",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                ),
                Function(
                    name="bar",
                    mangled="_Z3barv",
                    return_type="void",
                    visibility=Visibility.PUBLIC,
                ),
            ],
        )
        new = AbiSnapshot(
            library="libfoo.so",
            version="2.0",
            from_headers=True,
            functions=[
                Function(
                    name="foo",
                    mangled="_Z3foov",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                ),
            ],
        )
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
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--config",
                str(empty_cfg),
                "-o",
                "markdown=json=-",
            ],
        )
        assert baseline.exit_code == 4
        # With config downgrading abi_breaking, the fan-out no longer errors.
        res = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--config",
                str(cfg),
                "-o",
                "markdown=json=-",
            ],
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


# ── ADR-043 CLI reset: config strictness (version + unknown-key rejection) ────


class TestConfigStrictness:
    """ADR-043 (pre-1.0 CLI reset): `.abicheck.yml` carries `version:`, and an
    unknown key is now a hard ``ValueError`` (never a warning) — there is no
    separate ``abicheck config validate`` command any more, so this strictness
    has to live in ``BuildConfig.from_dict`` itself to ever be seen."""

    def test_version_round_trips(self) -> None:
        cfg = BuildConfig.from_dict({"version": 1})
        assert cfg.version == 1
        assert cfg.to_dict()["version"] == 1
        # Round-trip is stable and raises nothing.
        assert BuildConfig.from_dict(cfg.to_dict()).version == 1

    def test_unknown_top_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="future_feature"):
            BuildConfig.from_dict({"version": 2, "future_feature": {"enabled": True}})

    def test_unknown_block_key_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"sources\.'?nonsense'?"):
            BuildConfig.from_dict(
                {"sources": {"public_headers": ["api.h"], "nonsense": 1}}
            )

    def test_known_config_does_not_raise(
        self, recwarn: pytest.WarningsRecorder
    ) -> None:
        BuildConfig.from_dict(
            {
                "version": 1,
                "build": {"system": "cmake"},
                "sources": {"public_headers": ["a.h"], "graph": "full"},
                "severity": {"preset": "strict"},
                "scope": {"public": True},
                "suppression": {"strict": True},
                "source": {"method": "s4"},
                # Keys parsed by sibling modules must not trip the check.
                "risk_rules": {},
                "crosschecks": {},
            }
        )
        assert [w for w in recwarn.list if issubclass(w.category, UserWarning)] == []

    def test_load_build_config_unknown_key_raises(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / ".abicheck.yml"
        cfg_path.write_text("version: 3\nbrand_new_block:\n  x: 1\n")
        with pytest.raises(ValueError, match="brand_new_block"):
            load_build_config(cfg_path)
