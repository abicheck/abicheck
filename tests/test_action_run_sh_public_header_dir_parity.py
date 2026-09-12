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

"""Behavioral tests for ``action/run.sh``'s ``public-header-dir`` forwarding.

``dump`` mode has no dedicated ``--public-header-dir`` flag at all -- it
folds the input into ``-H`` (dump derives BOTH declaration provenance AND
header-extraction scope from ``-H``'s own directory semantics, per ADR-015),
so a ``public-header-dir: include`` input makes ``dump`` recursively extract
every header under ``include/``.

Legacy ``mode: scan``'s own ``--public-header-dir`` CLI flag used to be
scope-only, forwarded alongside a bare ``-H`` root to keep a fresh ``dump``
baseline's ``include_sequence`` comparable (lab report, fresh evidence at
the time). ``mode: scan`` is retired outright now (ADR-068's Action-
input-lifecycle amendment); the identical need survives only in `compare`'s
audit-only shape (old-library and abi-baseline both omitted), which folds
`public-header-dir` into a bare `-H` root the same way, since a two-sided
`compare` has no equivalent flag at all and this input is never forwarded
there.

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
from _workflow_exec import bash_executable, require_bash

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_END_MARKER = 'if [[ "${INPUT_VERBOSE:-false}" == "true" ]]; then'


def _mode_branches_region() -> str:
    """Helper functions + the full compare/dump/.../else mode chain,
    extracted verbatim from run.sh (everything up to the shared -v/extra-args
    tail that follows every branch). Mirrors
    ``test_action_run_sh_artifact_set._mode_branches_region`` exactly."""
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_END_MARKER)]


def _run_cmd(env_extra: dict[str, str]) -> list[str]:
    """Source the real mode-branch region with *env_extra* set, return CMD."""
    require_bash()
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
            [bash_executable(), script_path],
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
class TestAuditOnlyComparePublicHeaderDirForwardedAsDashH:
    def test_forwarded_as_bare_dash_h(self) -> None:
        # Audit-only shape (old-library/abi-baseline both omitted): no
        # `--public-header-dir` flag exists on `compare --no-baseline`
        # either (same as a two-sided compare), so this folds into a bare
        # (unsided -- there is no baseline side to protect) `-H` root.
        cmd = _run_cmd(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_PUBLIC_HEADER_DIR": "include",
                "INPUT_DEPTH": "headers",
            }
        )
        assert "compare" in cmd
        assert "--no-baseline" in cmd
        assert "--public-header-dir" not in cmd
        h_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]
        assert "include" in h_pairs, cmd

    def test_absent_forwards_neither(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "lib.so",
            }
        )
        assert "--public-header-dir" not in cmd
        h_indices = [j for j, v in enumerate(cmd) if v == "-H"]
        assert not any(cmd[j + 1] == "" for j in h_indices)


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestTwoSidedComparePublicHeaderDirIsNeverForwarded:
    """A two-sided `compare` has no `--public-header-dir` equivalent, and
    unlike the audit-only shape, this Action does not fold it into `-H`
    there either -- there is no dedicated `dump`-style provenance-and-
    extraction role for a two-sided comparison's own `-H` to play the input
    into."""

    def test_public_header_dir_ignored_on_a_two_sided_compare(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_PUBLIC_HEADER_DIR": "include",
                "INPUT_DEPTH": "headers",
            }
        )
        assert "compare" in cmd
        assert "--public-header-dir" not in cmd
        h_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]
        assert "include" not in h_pairs, cmd
        assert "new=include" not in h_pairs, cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestHeaderIncludeOverrideOnTwoSidedCompare:
    """A two-sided `compare`'s own per-side resolution OVERRIDES a bare
    shared root with a side-specific one (unlike `dump`'s/the audit-only
    shape's own unsided union) -- this is `compare`'s native, documented
    behavior, not something this Action's own translation layer adjusts."""

    def test_bare_header_plus_new_header(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_DEPTH": "headers",
                "INPUT_HEADER": "shared_inc",
                "INPUT_NEW_HEADER": "new_only_inc",
            }
        )
        assert "compare" in cmd
        h_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "-H"]
        assert "shared_inc" in h_pairs, cmd
        header_pairs = [cmd[j + 1] for j, v in enumerate(cmd) if v == "--header"]
        assert "new=new_only_inc" in header_pairs, cmd

    def test_bare_header_with_no_override(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "lib.so",
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
    forwarding for public-header-dir keeps working."""

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
