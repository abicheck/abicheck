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

"""Behavioral tests for ``action/run.sh``'s ``public-header-dir`` forwarding
parity between ``dump`` and ``scan`` mode (lab report, fresh evidence).

``dump`` mode has no dedicated ``--public-header-dir`` flag at all -- it
folds the input into ``-H`` (dump derives BOTH declaration provenance AND
header-extraction scope from ``-H``'s own directory semantics, per ADR-015),
so a ``public-header-dir: include`` input makes ``dump`` recursively extract
every header under ``include/``. ``scan`` mode's own ``--public-header-dir``
CLI flag is scope-only (see its own ``--help`` text: extraction only ever
comes from ``-H``) -- so the identical Action input left ``scan``'s own
header extraction narrowed to whatever explicit ``-H``/``--header`` was also
given, never expanding to the whole directory the way ``dump``'s did. Two
genuinely different header candidate sets (and therefore ``include_sequence``)
for the "same" logical Action inputs, so ``scan --against`` a fresh ``dump``
baseline of the same project spuriously read ``NOT_COMPARABLE`` (a
``profile_fingerprint`` mismatch on ``include_sequence``) with no real recipe
difference. Fixed by also forwarding ``public-header-dir`` as a bare ``-H``
root for ``scan`` -- ``scan``'s own ``-H <dir>`` expansion
(``service_scan.expand_header_inputs``) recursively extracts every header
under a directory identically to ``dump``'s (``header_utils.
iter_directory_headers``), so this closes the gap with no CLI change needed.

Extracts the full mode-branch region of ``run.sh`` verbatim -- the same
"parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_artifact_set.py`` -- and runs it with a harness that
sets the relevant ``INPUT_*`` env vars, capturing the resulting ``CMD``
array.
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
    """Helper functions + the full compare/dump/scan/.../else mode chain,
    extracted verbatim from run.sh (everything up to the shared -v/extra-args
    tail that follows every branch). Mirrors
    ``test_action_run_sh_artifact_set._mode_branches_region`` exactly."""
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_END_MARKER)]


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    See ``test_action_run_sh_helpers._bash_executable`` for the full
    rationale.
    """
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
    """Source the real mode-branch region with *env_extra* set, return CMD."""
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
    return [item for item in result.stdout.split("\x1f") if item]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanPublicHeaderDirAlsoForwardedAsDashH:
    def test_public_header_dir_forwarded_as_dash_h_via_compare_translation(
        self,
    ) -> None:
        # A baseline (against), an explicit --depth headers, and no other
        # input: this routes through the `compare` translation
        # unconditionally now (there is no legacy-CLI route left at all,
        # per ADR-068's second 2026-09-09 amendment and its 2026-09-10
        # amendment). The `compare`-translation branch has no
        # `--public-header-dir` flag at all (`compare` derives provenance
        # and extraction scope from `-H` alone), so this only forwards it
        # as a sided `-H new=` root (via `_add_unioned_sided_flag`); see
        # `test_audit_only_routes_to_compare_no_baseline_and_forwards_bare_dash_h`
        # below for the audit-only (no baseline) shape of this same input.
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_AGAINST": "baseline.so",
                "INPUT_PUBLIC_HEADER_DIR": "include",
                "INPUT_DEPTH": "headers",
            }
        )
        assert "compare" in cmd
        assert "scan" not in cmd
        assert "--public-header-dir" not in cmd
        h_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]
        assert "new=include" in h_pairs, cmd

    def test_audit_only_routes_to_compare_no_baseline_and_forwards_bare_dash_h(
        self,
    ) -> None:
        # Audit-only (no baseline) now routes to `compare --no-baseline`
        # unconditionally too (ADR-068's 2026-09-10 amendment closed the
        # last gap that kept it on the legacy `scan` CLI) -- there is no
        # legacy-CLI route left at all. `compare --no-baseline` has no
        # `--public-header-dir` flag either (same as the baseline shape
        # above); with no baseline side to contaminate, the candidate-only
        # `-H`/`-I` inputs are forwarded bare/unsided rather than through
        # `_add_unioned_sided_flag`'s `new=` union.
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_PUBLIC_HEADER_DIR": "include",
                "INPUT_DEPTH": "headers",
            }
        )
        assert "compare" in cmd
        assert "--no-baseline" in cmd
        assert "scan" not in cmd
        assert "--public-header-dir" not in cmd
        h_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]
        assert "include" in h_pairs, cmd

    def test_public_header_dir_absent_forwards_neither(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_AGAINST": "baseline.json",
            }
        )
        assert "--public-header-dir" not in cmd
        h_indices = [j for j, v in enumerate(cmd) if v == "-H"]
        assert not any(cmd[j + 1] == "" for j in h_indices)


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestHeaderIncludeUnionOnCompareTranslation:
    """`_add_unioned_sided_flag` (defined near `add_sided_flag` in
    `run.sh`): now that routing to `compare` is unconditional for every
    baseline scan (ADR-068's second 2026-09-09 amendment), a shared
    `header`/`include` root combined with a side-specific override reaches
    the translated `compare AGAINST ARTIFACT` command too -- but `compare`'s
    own per-side resolution OVERRIDES a bare shared root with a
    side-specific one instead of unioning them the way `scan`'s own
    `action.yml`-documented handling does. This re-adds the bare root as an
    explicit same-sided entry for whichever side has an override, closing
    that gap at the Action level."""

    def test_bare_header_plus_new_header_unions_onto_both_sides(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_AGAINST": "baseline.so",
                "INPUT_DEPTH": "headers",
                "INPUT_HEADER": "shared_inc",
                "INPUT_NEW_HEADER": "new_only_inc",
            }
        )
        assert "compare" in cmd
        h_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]
        # The untouched (old) side still effectively carries the bare root,
        # via its own sided entry -- not a bare, unprefixed one.
        assert "old=shared_inc" in h_pairs, cmd
        # The overridden (new) side carries BOTH the bare root and its own
        # override, never just the override alone (that would silently drop
        # the shared root for this side, the bug this helper exists to fix).
        assert "new=shared_inc" in h_pairs, cmd
        assert "new=new_only_inc" in h_pairs, cmd
        # No bare, unprefixed -H token at all once any override exists.
        h_indices = [j for j, v in enumerate(cmd) if v == "-H"]
        assert not any(cmd[j + 1] == "shared_inc" for j in h_indices), cmd

    def test_bare_header_plus_old_header_unions_onto_both_sides(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_AGAINST": "baseline.so",
                "INPUT_DEPTH": "headers",
                "INPUT_HEADER": "shared_inc",
                "INPUT_OLD_HEADER": "old_only_inc",
            }
        )
        assert "compare" in cmd
        h_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]
        assert "old=shared_inc" in h_pairs, cmd
        assert "old=old_only_inc" in h_pairs, cmd
        assert "new=shared_inc" in h_pairs, cmd

    def test_bare_include_plus_new_include_unions_onto_both_sides(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_AGAINST": "baseline.so",
                "INPUT_DEPTH": "headers",
                "INPUT_INCLUDE": "shared_inc",
                "INPUT_NEW_INCLUDE": "new_only_inc",
            }
        )
        assert "compare" in cmd
        i_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-I"]
        assert "old=shared_inc" in i_pairs, cmd
        assert "new=shared_inc" in i_pairs, cmd
        assert "new=new_only_inc" in i_pairs, cmd
        i_indices = [j for j, v in enumerate(cmd) if v == "-I"]
        assert not any(cmd[j + 1] == "shared_inc" for j in i_indices), cmd

    def test_bare_header_with_no_override_is_byte_identical_to_before(self) -> None:
        # No old-header/new-header/public-header-dir override at all: this
        # must stay a single bare, unprefixed -H entry -- no side prefix
        # introduced -- exactly matching pre-fix behavior for this shape.
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_AGAINST": "baseline.so",
                "INPUT_DEPTH": "headers",
                "INPUT_HEADER": "shared_inc",
            }
        )
        assert "compare" in cmd
        h_indices = [j for j, v in enumerate(cmd) if v == "-H"]
        h_values = [cmd[j + 1] for j in h_indices]
        assert h_values == ["shared_inc"], cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestDumpPublicHeaderDirUnaffected:
    """Sanity check: dump mode's own (pre-existing, unchanged) -H-only
    forwarding for public-header-dir keeps working -- this fix only adds a
    second forward on the scan side, it doesn't touch dump's."""

    def test_dump_forwards_public_header_dir_as_dash_h_only(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "dump",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_PUBLIC_HEADER_DIR": "include",
            }
        )
        assert "dump" in cmd
        assert "--public-header-dir" not in cmd
        h_indices = [j for j, v in enumerate(cmd) if v == "-H"]
        assert any(cmd[j + 1] == "include" for j in h_indices), cmd
