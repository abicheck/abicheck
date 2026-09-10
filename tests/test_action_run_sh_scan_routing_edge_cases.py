# SPDX-License-Identifier: Apache-2.0
"""Routing-predicate coverage for ``mode: scan``'s internal command assembly
(formerly gated by ``_SCAN_NEEDS_LEGACY_CLI``, then
``_SCAN_AUDIT_ONLY_NEEDS_LEGACY_CLI`` -- both retired).

ADR-068's second 2026-09-09 amendment collapsed the original predicate down
to one surviving condition: audit-only (no real baseline) stayed on the
legacy `scan` CLI, because `compare --no-baseline` did not yet reproduce
`scan`'s own audit-mode exit-code behavior for a gating cross-source
finding. ADR-068's 2026-09-10 amendment closed that last gap
(`policy/audit_gate_exit.py`'s orthogonal audit-gate exit axis), and this
Action now routes every `mode: scan` request -- baseline or audit-only
alike -- to `compare`/`compare --no-baseline` unconditionally; there is no
`_SCAN_..._NEEDS_LEGACY_CLI`-shaped predicate left in `run.sh` at all. Every
*other* request shape this file used to assert fell back to the legacy CLI
-- unset depth, `--depth build`/`--depth source`, a header/include
shared-root-plus-override combination, a compressed/neutral-named
JSON-snapshot baseline, a compile-context flag or `--abi3`/`-o`/an
unsupported `--format` value or a `compare`-only flag reaching `scan`
through `extra-args`, an effective `format: json`, and a default (or
explicit) `--pattern-verdicts` state -- had already closed before this
(see each class's own docstring for the specific evidence) and routes
unconditionally to `compare` whenever a real baseline is present,
regardless of `extra-args` content. This file inverts every one of those
old assertions, including the former "audit-only" survivor, to prove the
current behavior.

Mirrors ``test_action_run_sh_public_header_dir_parity.py``'s harness: sources
the verbatim mode-branch region of ``run.sh`` via a real ``bash`` subprocess
and asserts on the resulting ``CMD`` array (which CLI verb it starts with).
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


#: Printed immediately before the ``CMD`` array payload, so the payload can
#: be located precisely even when the sourced script emitted its own stdout
#: first -- e.g. `run.sh`'s documented `::warning::` when the `python3` this
#: harness resolves cannot import `abicheck` (a real condition in an
#: environment where pytest's own interpreter differs from `run.sh`'s
#: resolved one, Codex review, PR #1172, round 5). A naive
#: ``result.stdout.split(...)`` would fold that warning text into what
#: becomes ``cmd[0]``.
_CMD_MARKER = "__ABICHECK_TEST_CMD_START__"


def _run_cmd(env_extra: dict[str, str], *, region: str | None = None) -> list[str]:
    script = (
        (region if region is not None else _mode_branches_region())
        + f"\nprintf '%s' '{_CMD_MARKER}'"
        + "\nprintf '%s\\x1f' ${CMD[@]+\"${CMD[@]}\"}\n"
    )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    env = dict(os.environ)
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
    marker_index = result.stdout.rfind(_CMD_MARKER)
    if marker_index == -1:
        raise AssertionError(
            f"harness script never reached the CMD marker -- did the mode "
            f"branches region change shape?\n--- stdout ---\n{result.stdout}"
        )
    payload = result.stdout[marker_index + len(_CMD_MARKER) :]
    return [item for item in payload.split("\x1f") if item]


_BASE_INPUTS = {
    "INPUT_MODE": "scan",
    "INPUT_NEW_LIBRARY": "lib.so",
    "INPUT_AGAINST": "baseline.so",
}


#: One line past the extra-args-append block (Codex review, PR #1172,
#: round 18, fresh evidence): the default `_mode_branches_region()` cuts
#: off *before* this block (`_END_MARKER` sits right before it), so a
#: routing test alone cannot see what lands in `$CMD` from `extra-args`
#: itself -- only the routing *decision*.
#: CodeRabbit review, PR #1172, round 20: this used to match the *next*
#: block's own human-readable comment, which would silently break on an
#: unrelated reword of that prose. `action/run.sh` now carries a dedicated,
#: code-shaped sentinel immediately after the extra-args append block's own
#: closing `fi` for exactly this purpose -- match that instead.
_EXTRA_ARGS_APPEND_END_MARKER = "# --- END: extra-args append block ---"


def _region_through_extra_args_append() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_EXTRA_ARGS_APPEND_END_MARKER)]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAuditOnlyNoLongerNeedsLegacyCli:
    """The one condition ADR-068's second 2026-09-09 amendment left standing
    (a `mode: scan` request with no real baseline is audit-only, and
    `compare --no-baseline` did not yet reproduce `scan`'s audit-mode
    exit-code behavior for a gating cross-source finding) is closed by the
    2026-09-10 amendment's audit-gate exit axis: audit-only now routes to
    `compare --no-baseline` unconditionally too, exactly like a baseline
    scan routes to plain `compare AGAINST ARTIFACT`. There is no
    `MODE == "scan"` condition left in `run.sh`'s command assembly that
    exists to serve a legacy-CLI fallback -- every request shape routes to
    `compare`."""

    def test_no_against_at_all_routes_to_compare_no_baseline(self) -> None:
        cmd = _run_cmd({"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY": "lib.so"})
        assert cmd[0] == "abicheck"
        assert cmd[1] == "compare"
        assert "--no-baseline" in cmd, cmd

    def test_audit_flag_routes_to_compare_no_baseline_even_with_against_set(
        self,
    ) -> None:
        # The deprecated `audit: true` alias skips --against outright even
        # when against/abi-baseline resolved to a value elsewhere in the
        # workflow (FORCE_AUDIT_ONLY) -- still routes to `compare
        # --no-baseline`, never the two-sided `compare AGAINST ARTIFACT`.
        cmd = _run_cmd({**_BASE_INPUTS, "INPUT_AUDIT": "true"})
        assert cmd[1] == "compare"
        assert "--no-baseline" in cmd, cmd
        assert "baseline.so" not in cmd, cmd

    def test_audit_only_injects_default_severity_preset_when_unset(
        self,
    ) -> None:
        # ADR-068's 2026-09-10 amendment: the audit-gate exit axis is
        # opt-in via --severity-preset, so this Action injects `default`
        # on the translated invocation to preserve mode: scan's own
        # documented default-gating behavior when the caller stated no
        # preset of their own.
        cmd = _run_cmd({"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY": "lib.so"})
        assert cmd[1] == "compare"
        assert "--no-baseline" in cmd, cmd
        idx = cmd.index("--severity-preset")
        assert cmd[idx + 1] == "default", cmd

    def test_audit_only_keeps_an_explicit_severity_preset_input(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_SEVERITY_PRESET": "info-only",
            }
        )
        assert cmd[1] == "compare"
        idx = cmd.index("--severity-preset")
        assert cmd[idx + 1] == "info-only", cmd
        # The injected default must not ALSO be present -- exactly one
        # --severity-preset occurrence.
        assert cmd.count("--severity-preset") == 1, cmd

    def test_audit_only_keeps_an_explicit_severity_preset_via_extra_args(
        self,
    ) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_EXTRA_ARGS": "--severity-preset strict",
            },
            region=_region_through_extra_args_append(),
        )
        assert cmd[1] == "compare"
        # The dedicated-input injection must not fire when extra-args
        # already states one -- only the extra-args occurrence reaches CMD.
        assert cmd.count("--severity-preset") == 1, cmd
        idx = cmd.index("--severity-preset")
        assert cmd[idx + 1] == "strict", cmd

    def test_baseline_scan_severity_preset_forwarding_is_unaffected(
        self,
    ) -> None:
        # Negative control: the default-injection logic is scoped to the
        # audit-only shape alone -- a baseline scan with no severity-preset
        # input still forwards none at all (unchanged pre-existing
        # behavior), never an injected default.
        cmd = _run_cmd(_BASE_INPUTS)
        assert cmd[1] == "compare"
        assert "--no-baseline" not in cmd, cmd
        assert "--severity-preset" not in cmd, cmd

    def test_real_baseline_routes_to_compare(self) -> None:
        # Negative control: any real baseline, with no other input set at
        # all, is the one thing that now routes to compare unconditionally.
        cmd = _run_cmd(_BASE_INPUTS)
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestDepthNoLongerAffectsRouting:
    """The evidence-contract floor divergence that used to keep a pinned
    `--depth build`/`--depth source` (and an *unset* `--depth`, `scan`'s own
    risk-driven auto-depth case) on the legacy CLI is closed: `compare`
    enforces the identical hard evidence-contract floor itself now (exit 7,
    `EVIDENCE_CONTRACT_ERROR` -- live-verified against this repo: `compare
    --depth build old.so new.so` with no build evidence exits 7), and
    `--risk-rules`/auto-depth escalation are retired outright (ADR-068 (b)),
    so an omitted `--depth` deterministically defaults to `headers` on both
    CLIs now. Every depth shape -- set, unset, any case -- routes to
    `compare` whenever a real baseline is present."""

    @pytest.mark.parametrize(
        "value", [None, "headers", "build", "BUILD", "Build", "source", "SOURCE"]
    )
    def test_every_depth_value_routes_to_compare(self, value: str | None) -> None:
        extra = {"INPUT_DEPTH": value} if value is not None else {}
        cmd = _run_cmd({**_BASE_INPUTS, **extra})
        assert cmd[1] == "compare"

    def test_extra_args_depth_override_routes_to_compare(self) -> None:
        # A `--depth build` reaching the routing decision only through
        # extra-args (not the dedicated `depth` input) is no different now.
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--depth build",
            }
        )
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestHeaderIncludeSharedRootPlusOverrideRoutesToCompare:
    """`compare`'s per-side header resolution still genuinely OVERRIDES a
    shared root with a side-specific one instead of unioning them (ADR-040
    L1) -- that divergence is real and unchanged -- but it is no longer a
    reason to stay on the legacy CLI: the translated-`compare` branch now
    re-unions the bare root onto whichever side has an override before
    forwarding (`_add_unioned_sided_flag`, defined near `add_sided_flag` in
    `run.sh`), closing the gap at the Action level instead of avoiding the
    combination. See `test_action_run_sh_public_header_dir_parity.py` for
    dedicated coverage of the unioned `-H`/`-I` flags this produces."""

    def test_header_plus_public_header_dir_routes_to_compare(self) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_HEADER": "shared_inc",
                "INPUT_PUBLIC_HEADER_DIR": "pub_inc",
            }
        )
        assert cmd[1] == "compare"

    def test_header_plus_old_and_new_header_routes_to_compare(self) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_HEADER": "shared_inc",
                "INPUT_OLD_HEADER": "old_inc",
                "INPUT_NEW_HEADER": "new_inc",
            }
        )
        assert cmd[1] == "compare"

    def test_include_plus_old_and_new_include_routes_to_compare(self) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_INCLUDE": "shared_inc",
                "INPUT_OLD_INCLUDE": "old_inc",
                "INPUT_NEW_INCLUDE": "new_inc",
            }
        )
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestJsonSnapshotBaselineNoLongerAffectsRouting:
    """The stored-JSON-snapshot / `dependency_scope`-tag-matching divergence
    is closed: `service.run_dump`'s `include_dependencies` parameter already
    lets `compare`'s own live-binary dumping filter consistently with a
    `dependency_scope`-tagged baseline snapshot, so there is no remaining
    Action-level gap the extension/content sniff this predicate used to run
    (`_against_is_json_snapshot_by_content`, now deleted) needs to guard.
    Every baseline shape -- a canonical `.json`/`.json.gz`/`.json.zst`
    suffix, or a real JSON snapshot under a neutral filename -- routes to
    `compare` the same as a plain binary baseline now."""

    @pytest.mark.parametrize("suffix", [".json", ".json.gz", ".json.zst"])
    def test_canonical_json_suffix_routes_to_compare(self, suffix: str) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_AGAINST": f"baseline.abicheck{suffix}",
                "INPUT_DEPTH": "headers",
            }
        )
        assert cmd[1] == "compare"

    def test_json_snapshot_under_a_neutral_filename_routes_to_compare(
        self, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "baseline.snapshot"
        baseline.write_text('{"schema_version": 1}', encoding="utf-8")
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_AGAINST": str(baseline),
            }
        )
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestExtraArgsFlagsNoLongerAffectRouting:
    """Per ADR-068's re-scoping, the Action does not keep a compatible
    CLI-flag surface with `scan` for `extra-args` passthrough on the
    translated-`compare` route: routing to `compare` is now unconditional
    for every baseline scan regardless of `extra-args` content. A flag
    `compare` genuinely lacks (the whole L2 compile-context family, `--abi3`,
    `-o`/`--output`, an unsupported `--format` value, a `compare`-only flag
    like `--surface-metrics`) now reaches the translated `compare`
    invocation's `$CMD` unfiltered and would raise `compare`'s own real,
    correct Click usage error when actually run -- this harness only
    asserts the routing decision, not a live invocation, so it checks that
    the flag reaches `$CMD` verbatim rather than being silently dropped or
    forcing a fallback."""

    @pytest.mark.parametrize(
        "flag",
        [
            "--lang c++",
            "--ast-frontend clang",
            "--compiler clang++",
            "--compiler-prefix arm-linux-gnueabihf-",
            "--compiler-option -DFOO=1",
            "--sysroot /opt/sysroot",
            "--nostdinc",
            "--no-nostdinc",
            "--frontend-context strict",
            "--allow-ast-frontend-fallback",
            "--allow-unsupported-castxml",
            "--abi3 3.9",
            "--abi3=3.9",
            "-o report.json",
            "--output report.json",
            "--output=report.json",
            "-oreport.json",
            "--format markdown",
            "--format sarif",
            "--format html",
            "--format junit",
            "--format review",
            "--format oneline",
            "--surface-metrics",
            "--used-by consumer.so",
            "--required-symbol _Zfoo",
            "--no-baseline",
            "--dump-manifest",
            "--explain-patterns",
            "--output-dir out/",
        ],
    )
    def test_flag_via_extra_args_routes_to_compare(self, flag: str) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": flag,
            }
        )
        assert cmd[1] == "compare"

    @pytest.mark.parametrize(
        "flag",
        [
            # Genuinely shared by both `scan` and `compare` -- always routed
            # to compare here too, same as the flags above.
            "--severity-preset strict",
            "--require-complete-analysis",
        ],
    )
    def test_shared_flag_routes_to_compare(self, flag: str) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": flag,
            }
        )
        assert cmd[1] == "compare"

    def test_no_extra_args_at_all_routes_to_compare(self) -> None:
        cmd = _run_cmd({**_BASE_INPUTS, "INPUT_DEPTH": "headers"})
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestEffectiveJsonFormatNoLongerAffectsRouting:
    """`format: json` (the dedicated input, or an `extra-args --format json`
    override in either direction) no longer forces the legacy CLI: the
    scan-vs-compare JSON schema-shape difference
    (`scan_schema_version`/nested `diff.findings` vs.
    `report_schema_version`/root `changes`) is an accepted, documented
    breaking change of this migration (ADR-068's second amendment), not
    something to route around."""

    def test_dedicated_format_json_input_routes_to_compare(self) -> None:
        cmd = _run_cmd(
            {**_BASE_INPUTS, "INPUT_DEPTH": "headers", "INPUT_FORMAT": "json"}
        )
        assert cmd[1] == "compare"

    def test_extra_args_format_json_override_routes_to_compare(self) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_FORMAT": "text",
                "INPUT_EXTRA_ARGS": "--format json",
            }
        )
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestOutputFileAndWriteNoLongerAffectRouting:
    """The dedicated `output-file` input and a JSON `--write` via
    `extra-args` no longer force the legacy CLI either -- `compare` already
    has its own `-o`/`--output` and `--write`, so there is no report-shape
    gap left for this Action to route around."""

    def test_output_file_input_routes_to_compare(self) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_OUTPUT_FILE": "out.txt",
            }
        )
        assert cmd[1] == "compare"

    def test_extra_args_write_flag_routes_to_compare(self) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--write json=out.json",
            }
        )
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestPatternVerdictsNoLongerAffectsRouting:
    """`--pattern-verdicts` (present, absent, or explicitly turned off via
    `--no-pattern-verdicts`) no longer plays any role in the routing
    decision at all -- `compare`'s pattern-verdict modulation has been
    unconditional since ADR-068 D4 with no off switch on either CLI now, so
    the divergence this predicate used to guard is moot (ADR-068's second
    2026-09-09 amendment)."""

    def test_default_no_pattern_verdicts_flag_routes_to_compare(self) -> None:
        cmd = _run_cmd({**_BASE_INPUTS, "INPUT_DEPTH": "headers"})
        assert cmd[1] == "compare"

    def test_explicit_no_pattern_verdicts_routes_to_compare(self) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--no-pattern-verdicts",
            }
        )
        assert cmd[1] == "compare"

    def test_explicit_bare_pattern_verdicts_routes_to_compare(self) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--pattern-verdicts",
            }
        )
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestExtraArgsReachCompareUnfilteredNow:
    """The dedicated `mode: scan && _CLI_MODE == compare` special-case block
    that used to filter `--pattern-verdicts` out of `extra-args` before the
    translated `compare` invocation (Codex review, PR #1172, round 18) is
    dead now that routing to `compare` is unconditional for every baseline
    scan regardless of `extra-args` content (item 12 of the routing-
    predicate collapse): every mode, `scan` included, appends `extra-args`
    the same plain way. `--pattern-verdicts` now reaches `$CMD` unstripped,
    same as any other flag `extra-args` carries -- inverting the old
    stripped-flag assertion."""

    def test_pattern_verdicts_flag_is_not_stripped_when_routing_to_compare(
        self,
    ) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--pattern-verdicts",
            },
            region=_region_through_extra_args_append(),
        )
        assert cmd[1] == "compare"
        assert "--pattern-verdicts" in cmd, cmd

    def test_no_pattern_verdicts_flag_on_audit_only_scan_is_not_stripped(
        self,
    ) -> None:
        # Negative control, updated: audit-only (no baseline) no longer has
        # a legacy-CLI route at all -- it now reaches `compare
        # --no-baseline` unconditionally too, same as a baseline scan.
        # `--no-pattern-verdicts` is a real `scan` flag with no `compare`
        # equivalent, so it reaches $CMD unstripped either way (it would
        # raise a real Click usage error if actually run, the accepted
        # outcome for a flag compare genuinely lacks).
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--no-pattern-verdicts",
            },
            region=_region_through_extra_args_append(),
        )
        assert cmd[1] == "compare"
        assert "--no-baseline" in cmd, cmd
        assert "--no-pattern-verdicts" in cmd, cmd

    def test_other_extra_args_survive_alongside_pattern_verdicts(self) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--verbose --pattern-verdicts --severity-preset strict",
            },
            region=_region_through_extra_args_append(),
        )
        assert cmd[1] == "compare"
        assert "--pattern-verdicts" in cmd, cmd
        assert "--verbose" in cmd, cmd
        assert "--severity-preset" in cmd, cmd
