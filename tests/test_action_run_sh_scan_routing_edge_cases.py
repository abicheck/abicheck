# SPDX-License-Identifier: Apache-2.0
"""``_SCAN_NEEDS_LEGACY_CLI`` routing-predicate edge cases (Codex review,
PR #1172): three request shapes the predicate's reactivated narrower
conditions did not actually catch once the "unconditionally true" catch-all
(``9f2166e5c``) was removed -- each would have silently misrouted a baseline
scan onto ``compare``, losing a real, still-open capability gap's guard.

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


#: Markers bracketing `_PY_BIN_HAS_ABICHECK`'s own computation (same spelling
#: `test_action_release_topology_config.py`'s `_py_bin_has_abicheck_source`
#: uses) -- lets a test splice in a forced override right after real
#: detection ran, rather than fighting the resolution itself (e.g. by
#: manipulating `PATH`, which would also break coreutils this region needs).
_PY_BIN_HAS_ABICHECK_START = '_PY_BIN_HAS_ABICHECK="false"'
_PY_BIN_HAS_ABICHECK_END = "\nfi\n"


def _region_with_py_bin_forced_unavailable() -> str:
    region = _mode_branches_region()
    start = region.index(_PY_BIN_HAS_ABICHECK_START)
    end = region.index(_PY_BIN_HAS_ABICHECK_END, start) + len(_PY_BIN_HAS_ABICHECK_END)
    return (
        region[:end]
        + '_PY_BIN_HAS_ABICHECK="false"  # test override: force unavailable\n'
        + region[end:]
    )


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


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestDepthCaseSensitivity:
    """`DepthParam.convert()` lowercases and accepts any case; the routing
    predicate's own `--depth`/`extra-args` checks must match that, not just
    the lowercase spelling -- else `--depth BUILD` silently loses `scan`'s
    hard evidence-contract floor (exit 7) by routing onto `compare`, which
    has no equivalent."""

    @pytest.mark.parametrize("value", ["BUILD", "Build", "SOURCE", "Source"])
    def test_uppercase_deep_depth_stays_on_legacy_cli(self, value: str) -> None:
        cmd = _run_cmd({**_BASE_INPUTS, "INPUT_DEPTH": value})
        assert cmd[0] == "abicheck"
        assert cmd[1] == "scan"

    def test_lowercase_deep_depth_still_stays_on_legacy_cli(self) -> None:
        cmd = _run_cmd({**_BASE_INPUTS, "INPUT_DEPTH": "build"})
        assert cmd[1] == "scan"

    def test_shallow_depth_still_routes_to_compare(self) -> None:
        # Sanity control: this predicate change must not accidentally catch
        # every depth value, only build/source.
        cmd = _run_cmd({**_BASE_INPUTS, "INPUT_DEPTH": "headers"})
        assert cmd[1] == "compare"

    def test_extra_args_depth_override_stays_on_legacy_cli(self) -> None:
        # The dedicated `depth` input says `headers`, but a `--depth build`
        # in the general `extra-args` passthrough overrides it at the real
        # CLI -- the routing decision must see that override too.
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--depth build",
            }
        )
        assert cmd[1] == "scan"

    def test_extra_args_depth_headers_override_still_routes_to_compare(self) -> None:
        # Negative control: an extra-args --depth that is NOT build/source
        # must not itself force the legacy CLI.
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--depth headers",
            }
        )
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestPublicHeaderDirSharedHeaderConflict:
    """`compare`'s per-side header resolution OVERRIDES a shared root with a
    side-specific one instead of unioning them (ADR-040 L1) -- so a shared
    `header` combined with `public-header-dir` (forwarded as a `-H new=`
    side-specific root on the `compare` translation) can silently drop the
    shared header the same way `header` + `new-header` already does. Must
    stay on the legacy CLI exactly like that combination."""

    def test_header_plus_public_header_dir_stays_on_legacy_cli(self) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_HEADER": "shared_inc",
                "INPUT_PUBLIC_HEADER_DIR": "pub_inc",
            }
        )
        assert cmd[1] == "scan"

    def test_public_header_dir_alone_still_routes_to_compare(self) -> None:
        # Negative control: public-header-dir with no shared header set is
        # the already-fixed, already-tested compare-translation shape (see
        # test_action_run_sh_public_header_dir_parity.py) -- this predicate
        # change must not regress it back onto the legacy CLI.
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_PUBLIC_HEADER_DIR": "pub_inc",
            }
        )
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestCompressedBaselineSuffix:
    """A stored baseline snapshot's canonical suffixes are `.json`,
    `.json.gz`, and `.json.zst` (`abicheck/snapshot_io.py`'s
    `SNAPSHOT_SUFFIXES`) -- only checking `.json` let a compressed baseline
    slip onto `compare`, which has no equivalent for `scan`'s own
    `dependency_scope` tag-matching on the candidate side."""

    @pytest.mark.parametrize("suffix", [".json", ".json.gz", ".json.zst"])
    def test_compressed_baseline_stays_on_legacy_cli(self, suffix: str) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_AGAINST": f"baseline.abicheck{suffix}",
                "INPUT_DEPTH": "headers",
            }
        )
        assert cmd[1] == "scan"

    def test_native_library_baseline_still_routes_to_compare(self) -> None:
        # Negative control: a real .so baseline (this module's own
        # _BASE_INPUTS) is the already-tested compare-translation shape.
        cmd = _run_cmd({**_BASE_INPUTS, "INPUT_DEPTH": "headers"})
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestCompileContextFlagsViaExtraArgs:
    """`compile_context_options()` (`abicheck/cli_options.py`) is the whole
    L2 compile-context flag family (`--lang`, `--ast-frontend`, `--compiler`,
    `--compiler-prefix`, `--compiler-option`, `--sysroot`, `--nostdinc`/
    `--no-nostdinc`, `--frontend-context`, `--allow-ast-frontend-fallback`,
    `--allow-unsupported-castxml`) -- CLI cleanup phase two PR 7b (ADR-037
    D8.1) removed it from `compare`/`dump` as one unit, leaving it only on
    `scan`. `action.yml` has no dedicated input for any of them, so a user
    reaches them only through `extra-args` -- a `compare` translation would
    fail on the first one with an unknown-option usage error instead of
    running the scan it asked for."""

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
        ],
    )
    def test_compile_context_flag_stays_on_legacy_cli(self, flag: str) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": flag,
            }
        )
        assert cmd[1] == "scan"

    def test_no_compile_context_flag_still_routes_to_compare(self) -> None:
        # Negative control: extra-args with no scan-only flag at all is the
        # already-tested compare-translation shape.
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--verbose",
            }
        )
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAbi3FlagStaysOnLegacyCli:
    """``--abi3 FLOOR`` (Codex review, PR #1172, round 6): supported on both
    `scan` and `compare`, but with different gating for the stable-ABI-
    violation finding it produces. `scan`'s own audit
    (`scan_engine._run_abi3_audit`) only ever lands in the advisory
    crosscheck report, including on a baseline `scan --against` run, which
    never folds it into the real diff -- while `compare --abi3` (ADR-068
    Phase 2d) rides the same `extra_changes` channel every other finding
    uses, scored by policy/suppression/verdict like any other root finding.
    Translating a baseline `--abi3` scan onto `compare` would silently
    change an existing `mode: scan` workflow's own verdict/exit code."""

    @pytest.mark.parametrize("flag", ["--abi3 3.9", "--abi3=3.9", "--abi3 3.12"])
    def test_abi3_flag_stays_on_legacy_cli(self, flag: str) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": flag,
            }
        )
        assert cmd[1] == "scan"

    def test_no_abi3_flag_still_routes_to_compare(self) -> None:
        # Negative control: extra-args with no --abi3 at all is the
        # already-tested compare-translation shape.
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--verbose",
            }
        )
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestExtraArgsOutputFlagStaysOnLegacyCli:
    """``-o PATH``/``--output PATH`` via the general ``extra-args``
    passthrough (Codex review, PR #1172, round 7): both `scan` and
    `compare` accept it, but the file it writes carries a different JSON
    contract on each side (`scan_schema_version`/`diff.findings` vs.
    `report_schema_version`/`changes`) -- exactly the divergence the
    dedicated `INPUT_OUTPUT_FILE`/`--write` checks already guard against
    for their own inputs."""

    @pytest.mark.parametrize(
        "flag",
        [
            "-o report.json",
            "--output report.json",
            "--output=report.json",
            # Round 11: Click's attached short-option form (`-oPATH`, no
            # separating space) -- `_extra_args_options()` deliberately
            # leaves this opaque, so `_name` is the whole raw token, not
            # just `-o`.
            "-oreport.json",
        ],
    )
    def test_output_flag_stays_on_legacy_cli(self, flag: str) -> None:
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": flag,
            }
        )
        assert cmd[1] == "scan"

    def test_no_output_flag_still_routes_to_compare(self) -> None:
        # Negative control: extra-args with no -o/--output at all is the
        # already-tested compare-translation shape.
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_EXTRA_ARGS": "--verbose",
            }
        )
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestBaselineDetectedByContentStaysOnLegacyCli:
    """A stored JSON snapshot saved under a neutral filename (e.g.
    `baseline.snapshot`) carries none of the three canonical suffixes the
    existing suffix check matches, so it used to slip onto the `compare`
    translation -- which does not apply the same `dependency_scope`-aware
    candidate collection the legacy `scan` path's own
    `_scan_candidate_include_dependencies()` does (Codex review, PR #1172,
    round 8). `_against_is_json_snapshot_by_content()` content-sniffs via
    the canonical Python `sniff_text_format` instead of trusting the
    filename."""

    def test_json_snapshot_under_a_neutral_filename_stays_on_legacy_cli(
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
        assert cmd[1] == "scan"

    def test_a_native_library_baseline_under_the_same_directory_still_routes_to_compare(
        self, tmp_path: Path
    ) -> None:
        # Negative control: a real, non-JSON file at a path that could
        # plausibly be content-sniffed must not itself force the legacy
        # CLI -- only a real JSON snapshot does.
        baseline = tmp_path / "baseline.so"
        baseline.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 24)
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_AGAINST": str(baseline),
            }
        )
        assert cmd[1] == "compare"

    def test_a_nonexistent_baseline_path_still_routes_to_compare(self) -> None:
        # Negative control: the content-sniff helper must not itself force
        # the legacy CLI for a baseline path that doesn't exist on disk
        # (e.g. this harness's own fixture strings like "baseline.so" in
        # _BASE_INPUTS) -- only a real, readable JSON file does.
        cmd = _run_cmd({**_BASE_INPUTS, "INPUT_DEPTH": "headers"})
        assert cmd[1] == "compare"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestBaselineSniffFallsBackToLegacyWhenPythonUnavailable:
    """When `$_PY_BIN_HAS_ABICHECK` is false -- a self-hosted runner
    exposing an `abicheck`-importable executable while the separately
    resolved `$_PY_BIN` cannot import it, exactly the divergence the
    `$_PY_BIN_HAS_ABICHECK` warning elsewhere in this script already
    anticipates -- content sniffing cannot run at all (Codex review, PR
    #1172, round 9). Defaulting to "not JSON" there would silently reopen
    round 8's own regression for a neutral-name snapshot whenever the two
    interpreters differ. `_against_is_json_snapshot_by_content()` must
    instead conservatively force the legacy CLI for any *existing*
    `INPUT_AGAINST` file it cannot classify."""

    def test_an_existing_baseline_stays_on_legacy_cli_when_unclassifiable(
        self, tmp_path: Path
    ) -> None:
        # Even a plain, non-JSON file: with no way to classify it, the safe
        # answer is "assume it could be the neutral-name snapshot".
        baseline = tmp_path / "baseline.so"
        baseline.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 24)
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_AGAINST": str(baseline),
            },
            region=_region_with_py_bin_forced_unavailable(),
        )
        assert cmd[1] == "scan"

    def test_a_nonexistent_baseline_path_still_routes_to_compare(self) -> None:
        # Negative control: a definitively nonexistent path is a real "no"
        # -- not a classification failure -- so it must not itself force
        # the legacy CLI even with sniffing unavailable.
        cmd = _run_cmd(
            {**_BASE_INPUTS, "INPUT_DEPTH": "headers"},
            region=_region_with_py_bin_forced_unavailable(),
        )
        assert cmd[1] == "compare"

    def test_json_suffix_still_stays_on_legacy_cli_when_unclassifiable(self) -> None:
        # Negative control: the pre-existing suffix check must still catch
        # its own cases independent of content sniffing being available.
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_AGAINST": "baseline.abicheck.json",
            },
            region=_region_with_py_bin_forced_unavailable(),
        )
        assert cmd[1] == "scan"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestEffectiveJsonFormatStaysOnLegacyCli:
    """`format: json` with no output file at all (Codex review, PR #1172,
    round 10): `compare`'s report carries `report_schema_version`/root
    `changes`; `scan`'s carries `scan_schema_version`/nested
    `diff.findings` -- different, incompatible shapes. The prior fix only
    kept a *written file* (`-o`/`--output`/`output-file`) on the legacy
    CLI, but the run's raw JSON stdout is also echoed verbatim into
    `$GITHUB_STEP_SUMMARY` (a real, durable file a later job step can
    read) whenever no file is requested at all -- so the dedicated
    `format` input, and an `extra-args --format` override in either
    direction, must both stay on the legacy CLI too."""

    def test_dedicated_format_json_input_stays_on_legacy_cli(self) -> None:
        cmd = _run_cmd(
            {**_BASE_INPUTS, "INPUT_DEPTH": "headers", "INPUT_FORMAT": "json"}
        )
        assert cmd[1] == "scan"

    def test_extra_args_format_json_override_stays_on_legacy_cli(self) -> None:
        # The dedicated `format` input says `text`, but `extra-args
        # --format json` overrides it at the real CLI (Click keeps the
        # last occurrence) -- the routing decision must see that override.
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_FORMAT": "text",
                "INPUT_EXTRA_ARGS": "--format json",
            }
        )
        assert cmd[1] == "scan"

    def test_extra_args_format_text_override_still_routes_to_compare(self) -> None:
        # Negative control, the reverse override: the dedicated `format`
        # input says `json`, but `extra-args --format text` overrides it
        # back -- the *effective* format decides, not the nominal one.
        cmd = _run_cmd(
            {
                **_BASE_INPUTS,
                "INPUT_DEPTH": "headers",
                "INPUT_FORMAT": "json",
                "INPUT_EXTRA_ARGS": "--format markdown",
            }
        )
        assert cmd[1] == "compare"

    def test_no_format_input_at_all_still_routes_to_compare(self) -> None:
        # Negative control: the default format (text) must not itself
        # force the legacy CLI -- only an effective json format does.
        cmd = _run_cmd({**_BASE_INPUTS, "INPUT_DEPTH": "headers"})
        assert cmd[1] == "compare"
