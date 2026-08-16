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
    script = (
        _mode_branches_region()
        + '\nprintf \'%s\\x1f\' ${CMD[@]+"${CMD[@]}"}\n'
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    env = dict(os.environ)
    env.update(env_extra)
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True, text=True, encoding="utf-8", env=env,
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
    def test_public_header_dir_forwarded_as_both_flags(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_AGAINST": "baseline.json",
                "INPUT_PUBLIC_HEADER_DIR": "include",
            }
        )
        assert "scan" in cmd
        # --public-header-dir (scope) is still forwarded, unchanged.
        i = cmd.index("--public-header-dir")
        assert cmd[i + 1] == "include"
        # It must ALSO ride along as a bare -H root, so scan's own header
        # extraction expands the whole directory the same way dump's does.
        h_indices = [j for j, v in enumerate(cmd) if v == "-H"]
        assert any(cmd[j + 1] == "include" for j in h_indices), cmd

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

    def test_artifact_set_branch_also_forwards_both_flags(self) -> None:
        # The artifact-set branch composes -H separately from the sided
        # branch above (no old side to be sided about) -- confirm the
        # public-header-dir -> -H fold applies there too, since it sits
        # after both branches and runs unconditionally.
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY_SET": "a.so,b.so",
                "INPUT_PUBLIC_HEADER_DIR": "include",
            }
        )
        i = cmd.index("--public-header-dir")
        assert cmd[i + 1] == "include"
        h_indices = [j for j, v in enumerate(cmd) if v == "-H"]
        assert any(cmd[j + 1] == "include" for j in h_indices), cmd


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
