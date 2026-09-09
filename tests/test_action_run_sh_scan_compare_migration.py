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

"""``action/run.sh``'s ``mode: scan`` reimplementation as ``abicheck
compare`` (ADR-068 Phase 4 item 1,
``docs/contribute/plans/one-comparison-product.md``).

The Action's own ``mode: scan`` input keeps accepting exactly the same
documented values (ADR-068 D8) -- only the CLI subcommand ``run.sh``
assembles under the hood changes, and only for invocations that use none of
the still-scan-only capabilities (``new-library-set``/``budget``/
``crosscheck``/``risk-rules``/``build-target``, an audit-only run with no
``against`` resolved, a directory/package ``against``, or any ``format``
other than ``json``, all named in ``run.sh``'s own gate comment). Those
cases keep invoking ``abicheck scan`` directly and are already covered by
the existing scan-mode test modules (``test_action_run_sh_build_target.py``,
``test_action_run_sh_artifact_set.py``,
``test_action_run_sh_public_header_dir_scan_scope.py``, ...) -- none of
which combine ``against`` with ``format: json`` and no other caveat, so the
migrated path itself was previously uncovered. This module closes that gap.

Extracts the full mode-branch region of ``run.sh`` verbatim -- same
"parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_build_target.py``/``test_action_run_sh_artifact_set.py``
-- and runs it with a harness that sets the relevant ``INPUT_*`` env vars,
capturing the resulting ``CMD`` array.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_END_MARKER = 'if [[ "${INPUT_VERBOSE:-false}" == "true" ]]; then'


def _mode_branches_region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_END_MARKER)]


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "bash"


def _run_cmd(env_extra: dict[str, str]) -> list[str]:
    script = _mode_branches_region() + "\nprintf '%s\\x1f' ${CMD[@]+\"${CMD[@]}\"}\n"
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    # Ambient INPUT_* variables are dropped, not just overlaid: every
    # assertion in this module reads the gate decision from `cmd[1]`
    # ("compare" vs "scan"), and a stray ambient INPUT_BUDGET/INPUT_DEPTH/
    # INPUT_FORMAT (e.g. leaked from the calling process's own environment)
    # would silently flip that decision. The sibling module
    # (test_action_run_sh_scan_cross_source_fallback.py) already filters
    # these out for the identical reason (CodeRabbit review).
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(env_extra)
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return [item for item in result.stdout.split("\x1f") if item]


def _base_env(**extra: str) -> dict[str, str]:
    return {
        "INPUT_MODE": "scan",
        "INPUT_NEW_LIBRARY": "lib.so",
        "INPUT_AGAINST": "baseline.json",
        "INPUT_FORMAT": "json",
        **extra,
    }


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanMigratesToCompare:
    """A single-artifact ``scan --against`` run in ``format: json``, with
    none of the still-scan-only capabilities set, now assembles a real
    ``abicheck compare OLD NEW`` invocation instead of ``abicheck scan``."""

    def test_invokes_compare_not_scan(self) -> None:
        cmd = _run_cmd(_base_env())
        assert cmd[1] == "compare", cmd
        assert "scan" not in cmd, cmd

    def test_positional_operands_are_old_then_new(self) -> None:
        cmd = _run_cmd(_base_env())
        assert cmd[1] == "compare"
        assert cmd[2] == "baseline.json"
        assert cmd[3] == "lib.so"

    def test_format_json_forwarded(self) -> None:
        cmd = _run_cmd(_base_env())
        idx = cmd.index("--format")
        assert cmd[idx + 1] == "json"

    def test_no_against_flag_no_scan_subcommand_token(self) -> None:
        # `--against` is scan's own flag; `compare` has no such option --
        # the resolved baseline becomes the OLD positional instead.
        cmd = _run_cmd(_base_env())
        assert "--against" not in cmd, cmd

    def test_header_include_forwarded_sided(self) -> None:
        cmd = _run_cmd(
            _base_env(
                INPUT_HEADER="both.h",
                INPUT_OLD_HEADER="old.h",
                INPUT_NEW_HEADER="new.h",
                INPUT_INCLUDE="both_inc",
                INPUT_OLD_INCLUDE="old_inc",
                INPUT_NEW_INCLUDE="new_inc",
            )
        )
        h_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]
        i_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-I"]
        assert "both.h" in h_pairs
        assert "old=old.h" in h_pairs
        assert "new=new.h" in h_pairs
        assert "both_inc" in i_pairs
        assert "old=old_inc" in i_pairs
        assert "new=new_inc" in i_pairs

    def test_public_header_dir_forwarded_as_sided_new_h(self) -> None:
        # `compare` has no dedicated --public-header-dir flag at all --
        # forwarded as a sided `-H new=...` root instead (matching real
        # `mode: compare`'s own identical treatment of this input).
        cmd = _run_cmd(_base_env(INPUT_PUBLIC_HEADER_DIR="pub"))
        h_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]
        assert "new=pub" in h_pairs
        assert "pub" not in h_pairs
        assert "--public-header-dir" not in cmd

    def test_sources_and_build_info_scoped_to_new(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_SOURCES="src", INPUT_BUILD_INFO="build"))
        sources_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "--sources"]
        build_info_pairs = [
            cmd[j + 1] for j, v in enumerate(cmd) if v == "--build-info"
        ]
        assert sources_pairs == ["new=src"]
        assert build_info_pairs == ["new=build"]

    def test_policy_and_suppress_forwarded_unconditionally(self) -> None:
        # Unlike scan's own legacy branch (which only forwards these with a
        # resolved baseline), a real baseline is always present on this
        # path, so no gating condition is needed -- matches real
        # `mode: compare`'s own unconditional forwarding.
        cmd = _run_cmd(_base_env(INPUT_POLICY="security", INPUT_SUPPRESS="supp.yml"))
        idx = cmd.index("--policy")
        assert cmd[idx + 1] == "security"
        idx = cmd.index("--suppress")
        assert cmd[idx + 1] == "supp.yml"

    def test_require_complete_analysis_forwarded(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_REQUIRE_COMPLETE_ANALYSIS="true"))
        assert "--require-complete-analysis" in cmd

    def test_depth_since_changed_path_forwarded(self) -> None:
        cmd = _run_cmd(
            _base_env(
                INPUT_DEPTH="source",
                INPUT_SINCE="origin/main",
                INPUT_CHANGED_PATH="src/foo.c",
            )
        )
        idx = cmd.index("--depth")
        assert cmd[idx + 1] == "source"
        idx = cmd.index("--since")
        assert cmd[idx + 1] == "origin/main"
        idx = cmd.index("--changed-path")
        assert cmd[idx + 1] == "src/foo.c"

    def test_no_write_sidecar_injected_for_json_primary(self) -> None:
        # The primary format is already json, so there is nothing to
        # inject -- matches real `mode: compare`'s own identical guard.
        cmd = _run_cmd(_base_env(INPUT_PR_COMMENT="true"))
        assert "--write" not in cmd, cmd

    def test_dry_run_maps_to_dry_run_flag_no_output(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_DRY_RUN="true", INPUT_OUTPUT_FILE="out.json"))
        assert "--dry-run" in cmd
        assert "-o" not in cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForUnmigratedCapabilities:
    """Every capability `compare` cannot reach yet keeps `mode: scan`
    invoking `abicheck scan` directly, unchanged."""

    def test_new_library_set_stays_on_scan(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY_SET": "libs/",
                "INPUT_FORMAT": "json",
            }
        )
        assert cmd[1] == "scan", cmd

    def test_budget_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_BUDGET="15m"))
        assert cmd[1] == "scan", cmd

    def test_crosscheck_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_CROSSCHECK="private_header_leak=error"))
        assert cmd[1] == "scan", cmd

    def test_risk_rules_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_RISK_RULES="rules.yml"))
        assert cmd[1] == "scan", cmd

    def test_build_target_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_BUILD_TARGET="//:math"))
        assert cmd[1] == "scan", cmd

    def test_audit_only_no_against_stays_on_scan(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_FORMAT": "json",
            }
        )
        assert cmd[1] == "scan", cmd

    def test_audit_alias_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_AUDIT="true"))
        assert cmd[1] == "scan", cmd

    def test_non_json_format_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_FORMAT="text"))
        assert cmd[1] == "scan", cmd

    def test_default_format_stays_on_scan(self) -> None:
        env = _base_env()
        del env["INPUT_FORMAT"]
        cmd = _run_cmd(env)
        assert cmd[1] == "scan", cmd

    def test_directory_style_against_stays_on_scan(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "baseline_pkg"
        pkg_dir.mkdir()
        cmd = _run_cmd(_base_env(INPUT_AGAINST=str(pkg_dir)))
        assert cmd[1] == "scan", cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForPinnedDepth:
    """Codex review, P1 (fresh evidence): `scan`'s pinned-depth contract is
    "auto-strict" -- `cli_scan.py`'s `_scan_explicit_flags`/
    `pinned_explicit` makes an explicit `--depth source`/`--depth build`
    that cannot collect its evidence unconditionally abort with exit 7
    (`EVIDENCE_CONTRACT_ERROR`). `compare` has no such unconditional
    contract (it only enforces evidence completeness when
    `--require-complete-analysis` is separately passed, which this migrated
    branch does not force on for a pinned depth) -- so a pinned `depth`
    must keep `mode: scan` on the legacy CLI, the same as
    `budget`/`crosscheck`/`risk-rules`/`build-target` already do, to avoid
    a workflow silently losing scan's own loud exit-7 safety contract.

    The bug class here is "any explicitly pinned, non-`auto` depth value",
    not just one reported value -- parametrized across every real `--depth`
    choice `dump`/`scan`/`compare` share (`binary`/`headers`/`build`/
    `source`), plus the literal string `auto` proving the *omitted*/`auto`
    case is deliberately exempted (it is scan's own non-strict default, and
    `compare` reaches an identical, non-strict result for it).
    """

    @pytest.mark.parametrize("depth", ["binary", "headers", "build", "source"])
    def test_pinned_depth_stays_on_scan(self, depth: str) -> None:
        cmd = _run_cmd(_base_env(INPUT_DEPTH=depth))
        assert cmd[1] == "scan", cmd

    def test_auto_depth_still_migrates_to_compare(self) -> None:
        # The literal word "auto" is scan's own non-strict default spelled
        # out explicitly -- it must NOT force the legacy CLI, the same as
        # omitting `depth` entirely doesn't (see
        # `TestScanMigratesToCompare.test_depth_since_changed_path_forwarded`
        # for the omitted case already covered as a migrated-path input).
        cmd = _run_cmd(_base_env(INPUT_DEPTH="auto"))
        assert cmd[1] == "compare", cmd

    def test_omitted_depth_still_migrates_to_compare(self) -> None:
        cmd = _run_cmd(_base_env())
        assert cmd[1] == "compare", cmd
        assert "--depth" not in cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForUnpinnedDepthWithChangeSeed:
    """Second Codex review round, P1 (fresh evidence): when `depth` is
    omitted/`auto` but a real diff seed (`since`/`changed-path`) is given,
    `scan` and `compare` resolve the *effective* depth differently.

    `cli_scan.py`'s `_resolve_auto_source_method` risk-scores the changed
    paths and can auto-select a real depth (e.g. `source`) from that risk
    signal, while `cli_compare_helpers.py`'s `_resolve_compare_collect_mode`
    only infers depth from whether `--sources`/`--build-info` were given at
    all -- treating the changed-path seed as pure localization, never as a
    depth signal. `--sources ... --changed-path README.md` verifiably
    resolves to depth `off` under scan's own auto-resolution but `source`
    under compare's, for the identical Action inputs -- different cost,
    different collected evidence, potentially different findings.

    The bug class is "an unpinned depth with a change seed reachable through
    either dedicated input this gate already forwards", not just one of the
    two -- parametrized across both `since` and `changed-path` triggering
    the fallback independently, plus each combined with an explicit `auto`
    (spelled out) depth, mirroring `TestScanStaysOnLegacyCliForPinnedDepth`'s
    own `auto`-is-not-pinned distinction.
    """

    @pytest.mark.parametrize(
        "extra",
        [
            {"INPUT_SINCE": "origin/main"},
            {"INPUT_CHANGED_PATH": "src/foo.c"},
            {"INPUT_SINCE": "origin/main", "INPUT_DEPTH": "auto"},
            {"INPUT_CHANGED_PATH": "src/foo.c", "INPUT_DEPTH": "auto"},
        ],
    )
    def test_unpinned_depth_with_change_seed_stays_on_scan(
        self, extra: dict[str, str]
    ) -> None:
        cmd = _run_cmd(_base_env(**extra))
        assert cmd[1] == "scan", cmd

    def test_pinned_depth_with_change_seed_still_migrates_to_compare(self) -> None:
        # A genuinely pinned (non-auto) depth already forces the legacy CLI
        # for the unrelated pinned-depth reason above -- this proves the new
        # condition doesn't spuriously widen what "pinned" means; the two
        # conditions overlap here but neither depends on the other.
        cmd = _run_cmd(_base_env(INPUT_SINCE="origin/main", INPUT_DEPTH="source"))
        assert cmd[1] == "scan", cmd

    def test_no_change_seed_with_unpinned_depth_still_migrates_to_compare(
        self,
    ) -> None:
        # No `since`/`changed-path` at all -- the new condition must not
        # fire just because depth is unpinned.
        cmd = _run_cmd(_base_env())
        assert cmd[1] == "compare", cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForScanOnlyExtraArgs:
    """Codex review, P2 (fresh evidence): the `_SCAN_USES_LEGACY_CLI` gate
    only inspected dedicated `INPUT_*` fields, but `extra-args` is
    documented as forwarded verbatim to whichever command the gate selects
    -- so a scan-only flag passed this way (e.g. `--crosscheck
    private_header_leak=error`) silently routed to `compare` instead of
    forcing the legacy CLI, either erroring outright (`compare` has no such
    option) or, for `--format text`, producing a format `compare` doesn't
    even have.

    The bug class is "any scan-only or format-changing token reachable via
    extra-args", not just the one `--crosscheck`/`--format text` combination
    the review comment named -- parametrized across several independently-
    chosen scan-only flags (mirroring the dedicated-input conditions this
    gate already had) plus the `--format` override case, each via
    `extra-args` alone with no matching dedicated input set.
    """

    @pytest.mark.parametrize(
        "extra_args",
        [
            "--crosscheck private_header_leak=error",
            "--budget 15m",
            "--risk-rules rules.yml",
            "--build-target //:math",
            "--against other-baseline.json",
            "--manifest manifest.json",
            "--public-header-dir pub",
            "--depth source",
            # Second Codex review round: the remaining scan-minus-compare
            # flags a prior revision deliberately left out of this helper,
            # both value-taking and boolean/flag-only (see the helper's own
            # docstring for why a loud CLI usage error isn't an exemption).
            "--max-findings 5",
            "--pattern-verdicts",
            "--no-pattern-verdicts",
            "--show-suppressed",
        ],
    )
    def test_scan_only_extra_arg_stays_on_scan(self, extra_args: str) -> None:
        cmd = _run_cmd(_base_env(INPUT_EXTRA_ARGS=extra_args))
        assert cmd[1] == "scan", cmd

    def test_format_text_override_via_extra_args_stays_on_scan(self) -> None:
        # `format: json` (the dedicated input) with `extra-args: --format
        # text` really does run `text` (Click's last-flag-wins) -- `compare`
        # has no `text` format at all.
        cmd = _run_cmd(_base_env(INPUT_EXTRA_ARGS="--format text"))
        assert cmd[1] == "scan", cmd

    def test_format_json_via_extra_args_does_not_force_legacy_cli(self) -> None:
        # The inverse of the `--format text` case above: an explicit
        # `--format json` in extra-args (redundant with the already-json
        # dedicated `INPUT_FORMAT`) must NOT itself force the legacy CLI --
        # proves `_extra_args_forces_legacy_scan_cli`'s own `--format` arm
        # reads the token's *value*, not merely whether `--format` is
        # present in extra-args at all.
        cmd = _run_cmd(_base_env(INPUT_EXTRA_ARGS="--format json"))
        assert cmd[1] == "compare", cmd

    def test_unrelated_extra_arg_does_not_force_legacy_cli(self) -> None:
        # A flag shared by both commands, with no scan-only meaning, must
        # not trip the gate -- otherwise every migrated invocation with any
        # extra-args at all would be forced back to legacy CLI.
        cmd = _run_cmd(_base_env(INPUT_EXTRA_ARGS="--severity-preset strict"))
        assert cmd[1] == "compare", cmd

    # `--pattern-verdicts`/`--max-findings`/`--show-suppressed` used to be
    # exempted here on the reasoning that `compare` would reject them
    # outright as an unknown option anyway (a loud CLI usage error, not a
    # silent misbehavior) -- a second Codex review round found that
    # reasoning incomplete (see `_extra_args_forces_legacy_scan_cli`'s own
    # docstring): forcing a guaranteed usage error when a working legacy
    # `scan` invocation is one flag away is itself the wrong outcome, so
    # these are now covered by the parametrized case above instead of
    # exempted here.

    @pytest.mark.parametrize("flag", ["--sources", "--build-info", "--compile-db"])
    def test_bare_scoped_evidence_flag_via_extra_args_stays_on_scan(
        self, flag: str
    ) -> None:
        # Fourth Codex review round, P1 (fresh evidence): a BARE (unscoped)
        # occurrence of any of these three shares a name with a `compare`
        # option that means something different -- unscoped on `scan`
        # applies only to the single candidate; unscoped on `compare`
        # applies to BOTH operands. Forwarded verbatim through `extra-args`,
        # this would silently apply the candidate's own evidence to the
        # baseline side too, with no usage error to catch it.
        cmd = _run_cmd(_base_env(INPUT_EXTRA_ARGS=f"{flag} ./evidence"))
        assert cmd[1] == "scan", cmd

    def test_sided_spelling_also_stays_on_scan(self) -> None:
        # `scan --build-info` has no `old=`/`new=`-scoped form at all
        # (confirmed against the real CLI: it takes a plain PATH, unlike
        # `compare`'s sided `-H`/`--header`) -- there is no "safe" spelling
        # of this option through `extra-args` for a migrated scan, so a
        # `new=`-prefixed value must force the legacy CLI exactly like the
        # bare form does, not be mistaken for a `compare`-side scoping
        # convention `scan` doesn't share.
        cmd = _run_cmd(_base_env(INPUT_EXTRA_ARGS="--build-info new=./build"))
        assert cmd[1] == "scan", cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForSourceMethodConfig:
    """Fourth Codex review round, P1 (fresh evidence): an auto-discovered
    ``.abicheck.yml``/``.abicheck.yaml`` stating an explicit ``source:
    {method: auto}`` hits the identical auto-depth-resolution mismatch class
    as ``since``/``changed-path`` and ``build-info``-without-``sources``,
    but via project config rather than any Action input -- `compare`'s own
    auto-resolution has no equivalent for this value and raises a usage
    error outright (`scan --dry-run` resolves it to the PR preset's
    `source-target`).

    A fifth review round found the original ``method: auto``-only check too
    narrow: a NON-``auto`` value (e.g. ``s1``) diverges too, just
    differently -- ``scan``'s own risk-scored preset still resolves
    `source-target` regardless of the pinned method, while `compare`
    genuinely honors the pinned value and resolves `build` instead, for the
    identical inputs. Widened to match ANY `source.method` setting, not
    just the one value known to hard-fail; parametrized across both to
    prove the widened check covers the class, not just the originally
    reported value.
    """

    def _run_cmd_in(self, cwd: Path, env_extra: dict[str, str]) -> list[str]:
        script = _mode_branches_region() + "\nprintf '%s\\x1f' ${CMD[@]+\"${CMD[@]}\"}\n"
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
        ) as f:
            f.write(script)
            script_path = f.name
        env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
        env.update(env_extra)
        try:
            result = subprocess.run(
                [_bash_executable(), script_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=str(cwd),
            )
        finally:
            os.unlink(script_path)
        if result.returncode != 0:
            raise AssertionError(
                f"harness script failed (exit {result.returncode})\n"
                f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
            )
        return [item for item in result.stdout.split("\x1f") if item]

    @pytest.mark.parametrize("method", ["auto", "s1"])
    def test_config_with_source_method_stays_on_scan(
        self, tmp_path: Path, method: str
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            f"source:\n  method: {method}\n", encoding="utf-8"
        )
        cmd = self._run_cmd_in(tmp_path, _base_env())
        assert cmd[1] == "scan", cmd

    def test_config_without_source_method_auto_still_migrates(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "scope:\n  public: true\n", encoding="utf-8"
        )
        cmd = self._run_cmd_in(tmp_path, _base_env())
        assert cmd[1] == "compare", cmd

    def test_no_config_still_migrates(self, tmp_path: Path) -> None:
        cmd = self._run_cmd_in(tmp_path, _base_env())
        assert cmd[1] == "compare", cmd

    def test_pinned_depth_with_auto_method_config_still_stays_on_scan(
        self, tmp_path: Path
    ) -> None:
        # Overlaps with the pinned-depth condition already covered
        # elsewhere -- proves the new config check doesn't need a pinned
        # depth to fire, and a pinned depth alone (unrelated to config)
        # already keeps this on scan regardless.
        (tmp_path / ".abicheck.yml").write_text(
            "source:\n  method: auto\n", encoding="utf-8"
        )
        cmd = self._run_cmd_in(tmp_path, _base_env(INPUT_DEPTH="source"))
        assert cmd[1] == "scan", cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForFullDependencyScopeBaseline:
    """Sixth Codex review round, P1 (fresh evidence): `scan` peeks a JSON
    ``--against``/``abi-baseline`` snapshot for an explicit
    ``dependency_scope: "full"`` tag (a baseline `dump`ped with
    ``--include-system-declarations``) and extracts the live candidate
    unfiltered to match (`scan_engine._scan_candidate_include_dependencies`)
    -- otherwise the comparability gate's own dependency-scope check rejects
    the pair outright as `NOT_COMPARABLE`. `compare` has no equivalent
    automatic detection (its own ``--include-system-declarations`` must be
    requested explicitly), so a migrated invocation used to silently drop
    this legitimate workflow to `NOT_COMPARABLE` where `scan` succeeds
    (verified directly: identical full-dependency-scope JSON baseline + live
    native candidate, `scan` exits 0, migrated `compare` exits 16).

    `_migrated_compare_against_declares_full_dependency_scope` shells out to
    the real `scan_engine._scan_candidate_include_dependencies` rather than
    reimplementing its detection a second time in bash, so these tests
    exercise the genuine Python helper via a real (if minimal) JSON baseline
    file, not a bash-side approximation of it.
    """

    def test_full_dependency_scope_baseline_stays_on_scan(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.abicheck.json"
        baseline.write_text('{"dependency_scope": "full"}', encoding="utf-8")
        cmd = _run_cmd(_base_env(INPUT_AGAINST=str(baseline)))
        assert cmd[1] == "scan", cmd

    def test_filtered_dependency_scope_baseline_still_migrates(
        self, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "baseline.abicheck.json"
        baseline.write_text('{"dependency_scope": "filtered"}', encoding="utf-8")
        cmd = _run_cmd(_base_env(INPUT_AGAINST=str(baseline)))
        assert cmd[1] == "compare", cmd

    def test_untagged_baseline_still_migrates(self, tmp_path: Path) -> None:
        # No `dependency_scope` key at all -- the common case (an ordinary
        # filtered-by-default baseline, or one dumped by a version predating
        # this field). Must not spuriously stay on the legacy CLI.
        baseline = tmp_path / "baseline.abicheck.json"
        baseline.write_text("{}", encoding="utf-8")
        cmd = _run_cmd(_base_env(INPUT_AGAINST=str(baseline)))
        assert cmd[1] == "compare", cmd
