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


def _run_cmd(env_extra: dict[str, str], cwd: Path | None = None) -> list[str]:
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
            cwd=None if cwd is None else str(cwd),
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
            # Seventh Codex review round, P1 (fresh evidence): `--config
            # FILE` is accepted by both commands and reaches this same
            # escape hatch, but the dedicated `_config_sets_source_method`
            # gate condition never inspects an override reaching it
            # through `extra-args` -- Click's own last-flag-wins means it
            # always beats a `build-config` input too.
            "--config custom.yml",
            # Eleventh Codex review round, P1 (fresh evidence): an
            # attached-value short option (`-Hnew=api.h`, no space) is
            # Click-valid but this file's tokenizer deliberately never
            # parses a short option's concatenated value (documented limit,
            # `_extra_args_options`'s own docstring) -- reaches
            # `_extra_args_new_side_header_values`/`_extra_args_new_side_
            # include_values` as one opaque token, invisible to the
            # native-baseline header-reuse fallback. Forced to the legacy
            # CLI outright, the same safe-by-construction direction as the
            # pre-existing `-oPATH` case.
            "-Hnew=api.h",
            "-Iold=inc",
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

    def test_single_write_via_extra_args_does_not_force_legacy_cli(self) -> None:
        # A single, non-`text` `--write` is valid on both commands and must
        # not itself force the legacy CLI -- the negative control for the
        # repeated-`--write` case below.
        cmd = _run_cmd(_base_env(INPUT_EXTRA_ARGS="--write json=extra.json"))
        assert cmd[1] == "compare", cmd

    def test_repeated_write_via_extra_args_stays_on_scan(self) -> None:
        # Sixth Codex review round, P2 (fresh evidence): `scan --write` is
        # singular (a repeated occurrence just keeps the last value, per
        # Click's own default), but `compare --write` is repeatable and
        # rejects two occurrences naming the same PATH as a real usage
        # error (`reject_incoherent_secondary_writes`'s per-write
        # PATH-uniqueness check) -- a previously valid `--write
        # json=report.json --write json=report.json` scan step would
        # otherwise start hard-failing (exit 64) under a migrated
        # invocation.
        cmd = _run_cmd(
            _base_env(
                INPUT_EXTRA_ARGS="--write json=report.json --write json=report.json"
            )
        )
        assert cmd[1] == "scan", cmd

    def test_repeated_write_different_paths_via_extra_args_stays_on_scan(
        self,
    ) -> None:
        # The over-inclusive direction this tokenizer already takes
        # everywhere else it can't fully reproduce compare's own
        # validation: two *different* `--write` destinations is a real
        # `compare`-only multi-artifact capability `scan` never had, not a
        # shape this migration should try to guess is safe.
        cmd = _run_cmd(
            _base_env(
                INPUT_EXTRA_ARGS="--write json=one.json --write markdown=two.md"
            )
        )
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

    A sixth review round (two Codex passes plus a CodeRabbit pass) found two
    more gaps in the same check: it only ever looked at
    `$PWD/.abicheck.yml`/`.abicheck.yaml`, missing a `source.method` living
    in a `build-config`-named config file elsewhere (`--config FILE`
    selects a TRUSTED project config explicitly, per `scan --help-all`; cwd
    auto-discovery applies only when it's omitted) -- and it required
    `method` to start a line, missing YAML's flow-style spelling
    (`source: {method: auto}`, on one line).

    An eighth review round found one more spelling gap: valid YAML permits
    quoting any mapping key (`source: {"method": auto}`, or an indented
    `"method": auto`), which the project-config loader accepts and `scan`
    resolves identically to the unquoted spelling, but the check's own
    pattern required a bare `method` immediately after its prefix.
    """

    def _run_cmd_in(self, cwd: Path, env_extra: dict[str, str]) -> list[str]:
        # CodeRabbit review, sixth round: reuses `_run_cmd`'s own `cwd`
        # parameter instead of duplicating its script build/temp-file/
        # environment-filtering/failure-assertion logic a second time here.
        return _run_cmd(env_extra, cwd=cwd)

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

    @pytest.mark.parametrize("method", ["auto", "s1"])
    def test_flow_style_source_method_stays_on_scan(
        self, tmp_path: Path, method: str
    ) -> None:
        # CodeRabbit review, sixth round, fresh evidence: YAML permits a
        # flow-style mapping on one line (`source: {method: auto}`, this
        # function's own docstring example) in addition to the block style
        # already covered above -- the original pattern required `method`
        # to start a line, so this spelling fell through unmatched and
        # silently migrated to `compare`, hitting the exact usage error
        # this gate exists to prevent.
        (tmp_path / ".abicheck.yml").write_text(
            f"source: {{method: {method}}}\n", encoding="utf-8"
        )
        cmd = self._run_cmd_in(tmp_path, _base_env())
        assert cmd[1] == "scan", cmd

    @pytest.mark.parametrize(
        "config_text",
        [
            'source: {"method": auto}\n',
            "source: {'method': auto}\n",
            'source:\n  "method": auto\n',
            "source:\n  'method': s1\n",
        ],
    )
    def test_quoted_source_method_key_stays_on_scan(
        self, tmp_path: Path, config_text: str
    ) -> None:
        # Eighth Codex review round, P1, fresh evidence: valid YAML permits
        # quoting any mapping key -- the project-config loader accepts it
        # and `scan` resolves it identically to the unquoted spelling, but
        # the original pattern required a bare `method` immediately after
        # its prefix.
        (tmp_path / ".abicheck.yml").write_text(config_text, encoding="utf-8")
        cmd = self._run_cmd_in(tmp_path, _base_env())
        assert cmd[1] == "scan", cmd

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

    def test_build_config_source_method_stays_on_scan(self, tmp_path: Path) -> None:
        # Second Codex review round, P1 (fresh evidence): `build-config`/
        # `--config FILE` selects a TRUSTED project config explicitly
        # (`scan --help-all`) -- cwd auto-discovery applies only when it's
        # omitted. The earlier fix only ever looked at `$PWD/.abicheck.yml`,
        # so a `source.method` living in a `build-config`-named file
        # elsewhere (not at the checkout root, and not named
        # `.abicheck.yml`/`.abicheck.yaml` at all) went undetected.
        config_dir = tmp_path / "config_elsewhere"
        config_dir.mkdir()
        config_file = config_dir / "my-abicheck-config.yml"
        config_file.write_text("source:\n  method: auto\n", encoding="utf-8")
        cmd = self._run_cmd_in(
            tmp_path, _base_env(INPUT_BUILD_CONFIG=str(config_file))
        )
        assert cmd[1] == "scan", cmd

    def test_build_config_overrides_unrelated_cwd_config(
        self, tmp_path: Path
    ) -> None:
        # An explicit --config means abicheck never falls back to cwd
        # auto-discovery at all -- a `.abicheck.yml` that happens to also
        # sit at $PWD (with no source.method of its own) must not be
        # consulted once `build-config` names a different file.
        (tmp_path / ".abicheck.yml").write_text(
            "scope:\n  public: true\n", encoding="utf-8"
        )
        config_file = tmp_path / "explicit-config.yml"
        config_file.write_text("scope:\n  public: true\n", encoding="utf-8")
        cmd = self._run_cmd_in(
            tmp_path, _base_env(INPUT_BUILD_CONFIG=str(config_file))
        )
        assert cmd[1] == "compare", cmd

    def test_missing_build_config_file_does_not_crash(self, tmp_path: Path) -> None:
        # A build-config path that doesn't exist (a real usage error the
        # actual CLI invocation will itself surface) must not crash this
        # bash-side heuristic -- it simply finds no source.method to detect.
        cmd = self._run_cmd_in(
            tmp_path,
            _base_env(INPUT_BUILD_CONFIG=str(tmp_path / "does-not-exist.yml")),
        )
        assert cmd[1] == "compare", cmd

    def test_sources_tree_config_stays_on_scan(self, tmp_path: Path) -> None:
        # Ninth Codex review round, P1, fresh evidence: `scan` resolves its
        # own project config via `discover_build_config(sources)` when
        # `--sources` is given and no `--build-config` was -- checking the
        # `--sources` tree's OWN root, never `$PWD`. The previous version of
        # this check covered only an explicit `build-config` and `$PWD`
        # auto-discovery, entirely missing a `source.method` living in a
        # config discovered from `--sources`' own tree instead.
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        (sources_dir / ".abicheck.yml").write_text(
            "source:\n  method: auto\n", encoding="utf-8"
        )
        cmd = self._run_cmd_in(
            tmp_path, _base_env(INPUT_SOURCES=str(sources_dir))
        )
        assert cmd[1] == "scan", cmd

    def test_sources_tree_without_config_still_migrates(self, tmp_path: Path) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        cmd = self._run_cmd_in(
            tmp_path, _base_env(INPUT_SOURCES=str(sources_dir))
        )
        assert cmd[1] == "compare", cmd

    def test_build_config_wins_over_sources_tree_config(
        self, tmp_path: Path
    ) -> None:
        # An explicit build-config outranks --sources' own tree, exactly as
        # it outranks $PWD auto-discovery -- matching cli_scan.py's own
        # `_discover_scan_project_config` precedence (explicit config wins
        # outright over `discover_build_config(sources)`).
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        (sources_dir / ".abicheck.yml").write_text(
            "source:\n  method: auto\n", encoding="utf-8"
        )
        explicit_config = tmp_path / "explicit-config.yml"
        explicit_config.write_text("scope:\n  public: true\n", encoding="utf-8")
        cmd = self._run_cmd_in(
            tmp_path,
            _base_env(
                INPUT_SOURCES=str(sources_dir),
                INPUT_BUILD_CONFIG=str(explicit_config),
            ),
        )
        assert cmd[1] == "compare", cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestMigratedCompareForwardsSourcesTreeConfig:
    """Ninth Codex review round, P1, fresh evidence: the migrated ``compare``
    invocation now explicitly forwards ``--config`` when ``--sources`` has
    its own discovered project config -- otherwise ALL of its settings
    (severity, scope, suppression, gate) silently disappeared under a
    migrated run, not just ``source.method`` (covered separately above).
    Reproduced directly: an old library exporting ``existing``, a new
    library exporting ``existing``+``added``, ``severity.addition: error``
    in the sources tree's own config -- ``scan`` exits 1 for the addition,
    the un-fixed migrated ``compare`` request exited 0 since its own
    default config resolution never looked inside ``--sources`` at all.
    """

    def test_config_forwarded_when_sources_has_its_own(
        self, tmp_path: Path
    ) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        config_file = sources_dir / ".abicheck.yml"
        config_file.write_text("scope:\n  public: true\n", encoding="utf-8")
        cmd = _run_cmd(_base_env(INPUT_SOURCES=str(sources_dir)))
        assert cmd[1] == "compare", cmd
        assert "--config" in cmd, cmd
        assert str(config_file) in cmd, cmd

    def test_no_config_forwarded_when_sources_has_none(
        self, tmp_path: Path
    ) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        # CodeRabbit review, fresh evidence: without `cwd=tmp_path`,
        # `_resolve_scan_effective_config_path`'s own upward-walk fallback
        # (once `--sources`' own tree has nothing) runs from wherever the
        # test process's real cwd happens to be, which could spuriously
        # discover a real `.abicheck.yml` above it and flip this assertion --
        # isolate the same way the sibling sources-tree-config tests already
        # do.
        cmd = _run_cmd(_base_env(INPUT_SOURCES=str(sources_dir)), cwd=tmp_path)
        assert cmd[1] == "compare", cmd
        assert "--config" not in cmd, cmd

    def test_dedicated_build_config_input_still_wins(self, tmp_path: Path) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        (sources_dir / ".abicheck.yml").write_text(
            "scope:\n  public: true\n", encoding="utf-8"
        )
        explicit_config = tmp_path / "explicit-config.yml"
        explicit_config.write_text("scope:\n  public: true\n", encoding="utf-8")
        cmd = _run_cmd(
            _base_env(
                INPUT_SOURCES=str(sources_dir),
                INPUT_BUILD_CONFIG=str(explicit_config),
            )
        )
        assert cmd[1] == "compare", cmd
        assert "--config" in cmd, cmd
        assert str(explicit_config) in cmd, cmd
        assert str(sources_dir / ".abicheck.yml") not in cmd, cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForAbi3FloorConfig:
    """Ninth Codex review round, P1, fresh evidence: ``scan`` enables its
    stable-ABI audit ONLY from an explicit ``--abi3`` CLI value, never from
    project config, but ``compare`` ALSO enables it from a project config's
    ``python: {abi3_floor: ...}`` block -- a migrated invocation with no
    ``--abi3`` given at all could still run this audit under ``compare``
    and fail its own precondition (a non-CPython-extension pair) with exit
    7, where ``scan`` itself would simply never have looked at that key and
    exited 0. No ``python_stable_abi_violation`` finding is even produced
    in that failure, so the cross-source/pattern-verdict fallback's own
    after-the-fact detection can't catch it either -- this needs its own
    dedicated gate condition.
    """

    def test_cwd_config_with_abi3_floor_stays_on_scan(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            'python:\n  abi3_floor: "3.8"\n', encoding="utf-8"
        )
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "scan", cmd

    def test_sources_tree_config_with_abi3_floor_stays_on_scan(
        self, tmp_path: Path
    ) -> None:
        sources_dir = tmp_path / "vendored-src"
        sources_dir.mkdir()
        (sources_dir / ".abicheck.yml").write_text(
            'python:\n  abi3_floor: "3.8"\n', encoding="utf-8"
        )
        cmd = _run_cmd(
            _base_env(INPUT_SOURCES=str(sources_dir)), cwd=tmp_path
        )
        assert cmd[1] == "scan", cmd

    def test_config_without_abi3_floor_still_migrates(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "scope:\n  public: true\n", encoding="utf-8"
        )
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "compare", cmd

    def test_no_config_still_migrates(self, tmp_path: Path) -> None:
        cmd = _run_cmd(_base_env(), cwd=tmp_path)
        assert cmd[1] == "compare", cmd


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


def _native_lib(tmp_path: Path, name: str = "baseline.so") -> str:
    # `_baseline_is_native_library`'s content-first sniff recognizes real
    # ELF magic bytes -- matches the identical fixture the sibling
    # cross-source-fallback test module already uses for the same purpose.
    path = tmp_path / name
    path.write_bytes(b"\x7fELF")
    return str(path)


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestMigratedCompareReusesHeadersForNativeBaseline:
    """Seventh Codex review round, P1 (fresh evidence): ``cli_scan_baseline.
    _resolve_baseline_header_scope`` deliberately reuses the candidate's own
    header(s) for the old side too when ``--against`` is a native library
    (re-parsed from source, unlike a JSON/ABICC-dump snapshot which already
    carries its own headers) and no dedicated old-side header was given --
    "correct only when the headers did not change" (the function's own
    docstring), but strictly better than leaving the old side a headerless
    binary next to a header-evidenced new side. The migrated ``compare``
    invocation forwarded ``new-header``/``public-header-dir`` scoped to
    ``new=`` only, with nothing filling the old side: verified directly
    against an old library exporting ``existing`` and a new library
    exporting ``existing``+``added``, with only ``new-header`` given --
    ``scan`` reports no change (both sides read through the identical
    header), the un-fixed migrated ``compare`` reported ``func_added``
    (the old side had no header evidence to match the addition against).
    """

    def test_new_header_reused_for_old_side_on_native_baseline(
        self, tmp_path: Path
    ) -> None:
        header = str(tmp_path / "api.h")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_NEW_HEADER=header,
            )
        )
        assert cmd[1] == "compare", cmd
        assert f"old={header}" in cmd, cmd
        assert f"new={header}" in cmd, cmd

    def test_public_header_dir_reused_for_old_side_on_native_baseline(
        self, tmp_path: Path
    ) -> None:
        header_dir = str(tmp_path / "include")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_PUBLIC_HEADER_DIR=header_dir,
            )
        )
        assert cmd[1] == "compare", cmd
        assert f"old={header_dir}" in cmd, cmd
        assert f"new={header_dir}" in cmd, cmd

    def test_explicit_old_header_is_not_overridden(self, tmp_path: Path) -> None:
        # The reuse fallback only fires when old-header is absent -- an
        # explicitly-given old-header must win, exactly like scan's own
        # fallback only ever applies "without them" (no dedicated old-side
        # header).
        new_header = str(tmp_path / "new-api.h")
        old_header = str(tmp_path / "old-api.h")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_NEW_HEADER=new_header,
                INPUT_OLD_HEADER=old_header,
            )
        )
        assert cmd[1] == "compare", cmd
        assert f"old={old_header}" in cmd, cmd
        assert f"old={new_header}" not in cmd, cmd

    def test_no_reuse_for_json_baseline(self, tmp_path: Path) -> None:
        # A JSON/ABICC-dump snapshot baseline already carries its own
        # headers -- no reuse needed, and none must be added.
        baseline = tmp_path / "baseline.abicheck.json"
        baseline.write_text("{}", encoding="utf-8")
        header = str(tmp_path / "api.h")
        cmd = _run_cmd(
            _base_env(INPUT_AGAINST=str(baseline), INPUT_NEW_HEADER=header)
        )
        assert cmd[1] == "compare", cmd
        assert f"old={header}" not in cmd, cmd

    def test_no_reuse_when_new_side_has_no_header_either(
        self, tmp_path: Path
    ) -> None:
        # Nothing to reuse -- neither side gets a sided -H at all, and this
        # must not itself force the legacy CLI (the default, header-less
        # case this migration was built for in the first place).
        cmd = _run_cmd(_base_env(INPUT_AGAINST=_native_lib(tmp_path)))
        assert cmd[1] == "compare", cmd
        assert not any(tok.startswith("old=") for tok in cmd), cmd

    def test_new_include_reused_for_old_side_alongside_header(
        self, tmp_path: Path
    ) -> None:
        # Eighth Codex review round, P1, fresh evidence:
        # `_resolve_baseline_header_scope` reuses the candidate's own
        # includes for the old side in this exact branch too, not just its
        # headers -- reproduced directly (the un-fixed migrated command
        # failed parsing the old header with a "types.h not found" error
        # scan itself did not hit, since the reused header's own include
        # path never reached the old side).
        header = str(tmp_path / "api.h")
        include_dir = str(tmp_path / "include")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_NEW_HEADER=header,
                INPUT_NEW_INCLUDE=include_dir,
            )
        )
        assert cmd[1] == "compare", cmd
        assert f"old={include_dir}" in cmd, cmd
        assert f"new={include_dir}" in cmd, cmd

    def test_explicit_old_include_is_not_overridden(self, tmp_path: Path) -> None:
        # A deliberate refinement over scan's own literal behavior (which
        # ignores `old-include` entirely whenever `old-header` is absent):
        # an explicitly-given `old-include` must still win here.
        header = str(tmp_path / "api.h")
        new_include = str(tmp_path / "new-include")
        old_include = str(tmp_path / "old-include")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_NEW_HEADER=header,
                INPUT_NEW_INCLUDE=new_include,
                INPUT_OLD_INCLUDE=old_include,
            )
        )
        assert cmd[1] == "compare", cmd
        assert f"old={old_include}" in cmd, cmd
        assert f"old={new_include}" not in cmd, cmd

    def test_no_include_reuse_without_new_include(self, tmp_path: Path) -> None:
        # No `new-include` at all -- only the header gets reused for the
        # old side (covered above), no `-I old=...` is added.
        header = str(tmp_path / "api.h")
        cmd = _run_cmd(
            _base_env(INPUT_AGAINST=_native_lib(tmp_path), INPUT_NEW_HEADER=header)
        )
        assert cmd[1] == "compare", cmd
        old_tokens = [tok for tok in cmd if tok.startswith("old=")]
        assert old_tokens == [f"old={header}"], cmd

    def test_extra_args_sided_new_header_reused_for_old_side(
        self, tmp_path: Path
    ) -> None:
        # Ninth Codex review round, P1, fresh evidence: a sided `new=` header
        # value reaching the migrated invocation only through
        # `extra-args: -H new=PATH` is invisible to the dedicated `new-header`
        # input check above -- it is appended to the real command line only
        # at the very end of this script, well after this reuse fallback
        # already decided whether OLD needs a header. Without accounting for
        # it, OLD was left headerless in exactly the same shape the dedicated
        # `new-header` input already fixed for.
        header = str(tmp_path / "api.h")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_EXTRA_ARGS=f"-H new={header}",
            )
        )
        assert cmd[1] == "compare", cmd
        assert f"old={header}" in cmd, cmd

    def test_extra_args_bare_header_needs_no_reuse(self, tmp_path: Path) -> None:
        # A BARE (unsided) `-H`/`--header` value in extra-args needs no
        # reuse help -- `compare` already applies it to both sides on its
        # own (ADR-040's base/old/new fan-out), so the fallback must not add
        # a redundant/conflicting sided `old=` entry for it.
        header = str(tmp_path / "api.h")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_EXTRA_ARGS=f"-H {header}",
            )
        )
        assert cmd[1] == "compare", cmd
        assert not any(tok.startswith("old=") for tok in cmd), cmd

    def test_extra_args_sided_old_header_already_present_not_overridden(
        self, tmp_path: Path
    ) -> None:
        # An explicit dedicated `old-header` still wins over a sided `new=`
        # value arriving through extra-args -- the same non-overriding
        # direction the dedicated-input case already guarantees.
        new_header = str(tmp_path / "new-api.h")
        old_header = str(tmp_path / "old-api.h")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_OLD_HEADER=old_header,
                INPUT_EXTRA_ARGS=f"-H new={new_header}",
            )
        )
        assert cmd[1] == "compare", cmd
        assert f"old={old_header}" in cmd, cmd
        assert f"old={new_header}" not in cmd, cmd

    def test_extra_args_sided_new_include_reused_for_old_side(
        self, tmp_path: Path
    ) -> None:
        # Same class of gap as the header case above, for `-I`/`--include`:
        # a sided `new=` include value reaching this invocation only through
        # `extra-args: -I new=PATH` is reused for OLD too, mirroring the
        # dedicated `new-include` input's own reuse.
        header = str(tmp_path / "api.h")
        include_dir = str(tmp_path / "include")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_NEW_HEADER=header,
                INPUT_EXTRA_ARGS=f"-I new={include_dir}",
            )
        )
        assert cmd[1] == "compare", cmd
        assert f"old={include_dir}" in cmd, cmd

    def test_extra_args_old_side_header_not_duplicated_by_reuse(
        self, tmp_path: Path
    ) -> None:
        # Eleventh Codex review round, P1, fresh evidence: `-H`/`--header` is
        # repeatable, so `extra-args: -H old=old.h -H new=new.h` already
        # gives OLD its own real header. Without accounting for it, the
        # reuse fallback injected an ADDITIONAL `-H old=new.h` from the
        # candidate's own `new=` value alongside the user's real one --
        # Click keeps every `-H` occurrence, so OLD would parse through
        # BOTH headers at once. Only the user's own old header may appear
        # scoped to OLD; the candidate's must not be reused on top of it.
        old_header = str(tmp_path / "old-api.h")
        new_header = str(tmp_path / "new-api.h")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_EXTRA_ARGS=f"-H old={old_header} -H new={new_header}",
            )
        )
        assert cmd[1] == "compare", cmd
        assert f"old={new_header}" not in cmd, cmd

    def test_extra_args_old_side_include_not_duplicated_by_reuse(
        self, tmp_path: Path
    ) -> None:
        # Same class of gap as the header case above, for `-I`/`--include`:
        # an `extra-args: -I old=...` already scopes OLD's own include path,
        # so the reuse fallback must not also inject a reused `-I old=` from
        # `new-include`/extra-args' own `new=` value alongside it.
        header = str(tmp_path / "api.h")
        old_include = str(tmp_path / "old-include")
        new_include = str(tmp_path / "new-include")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_NEW_HEADER=header,
                INPUT_NEW_INCLUDE=new_include,
                INPUT_EXTRA_ARGS=f"-I old={old_include}",
            )
        )
        assert cmd[1] == "compare", cmd
        assert f"old={new_include}" not in cmd, cmd

    def test_extra_args_new_header_alone_does_not_force_legacy_cli(
        self, tmp_path: Path
    ) -> None:
        # The reuse fallback firing must not itself force the legacy CLI --
        # this stays on the migrated `compare` path, just with the reused
        # header added.
        header = str(tmp_path / "api.h")
        cmd = _run_cmd(
            _base_env(
                INPUT_AGAINST=_native_lib(tmp_path),
                INPUT_EXTRA_ARGS=f"--header new={header}",
            )
        )
        assert cmd[1] == "compare", cmd
        assert f"old={header}" in cmd, cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanStaysOnLegacyCliForDryRun:
    """Eighth Codex review round, P2 (fresh evidence): scan's own dry-run
    preview (``action.yml``'s documented "scan preview" contract) reports
    the PR preset's risk-resolved collect mode and scan-specific per-layer
    candidate cost, which ``compare --dry-run``'s own preview does not
    reproduce (a different collect-mode resolver, a different cost model,
    an old-plus-new comparison cost rather than a single-candidate one) --
    the same class of collect-mode/cost divergence the ``since``/
    ``changed-path`` and ``build-info``-without-``sources`` conditions
    already guard, just for the dry-run preview's own content."""

    def test_dry_run_true_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_DRY_RUN="true"))
        assert cmd[1] == "scan", cmd

    def test_estimate_alias_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_ESTIMATE="true"))
        assert cmd[1] == "scan", cmd

    def test_dry_run_via_extra_args_stays_on_scan(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_EXTRA_ARGS="--dry-run"))
        assert cmd[1] == "scan", cmd

    def test_dry_run_false_still_migrates(self) -> None:
        cmd = _run_cmd(_base_env(INPUT_DRY_RUN="false"))
        assert cmd[1] == "compare", cmd

    def test_no_dry_run_still_migrates(self) -> None:
        cmd = _run_cmd(_base_env())
        assert cmd[1] == "compare", cmd

    def test_dry_run_maps_to_dry_run_flag_no_output(self) -> None:
        # Relocated from `TestScanMigratesToCompare` once this round's fix
        # kept an effective dry run on the legacy CLI -- `scan`'s own
        # `--dry-run` handling maps identically (flag forwarded, `-o`
        # skipped entirely, mutually exclusive on both commands), so this
        # assertion still holds, just via the legacy builder now.
        cmd = _run_cmd(_base_env(INPUT_DRY_RUN="true", INPUT_OUTPUT_FILE="out.json"))
        assert cmd[1] == "scan", cmd
        assert "--dry-run" in cmd
        assert "-o" not in cmd
