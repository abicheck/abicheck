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

"""Shared harness for the tests that execute the whole of ``action/run.sh``.

Extracted so the verdict-truthfulness tests and the report-destination tests
can share one faithful stub-and-runner pair instead of each keeping a copy.
That matters more here than usual: on this PR alone, four separate review
findings turned out to be *fixtures* that resembled no real abicheck output
(a stub ignoring ``--write``, an audit report stubbed as a bare
``{"findings": []}``), and a second copy of a harness is a second place for
that to happen unnoticed.

Not a ``test_`` module: it holds no tests, only the shared machinery, per this
directory's convention for ``_strict_process.py`` and ``_workflow_exec.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable

ACTION_DIR = Path(__file__).resolve().parents[1] / "action"
RUN_SH = ACTION_DIR / "run.sh"

# POSIX only, for the same reason `test_action_coverage_verdict.py` states: this
# harness works by putting an *extensionless, shebang-dispatched* `abicheck` on
# PATH, because `run.sh` resolves the binary by name. Windows has neither the
# executable bit nor kernel shebang handling, and Git bash's `chmod` is a no-op
# on NTFS, so the stub is not runnable there — every test in the module then
# fails identically with the WSL launcher stub's own UTF-16 "no installed
# distributions" text instead of anything from `run.sh`. The behaviour under
# test is plain shell with no platform-dependent branch, and the Linux lane
# exercises all of it.
#
# Omitting this marker is what turned the windows-latest unit lane red on this
# PR: the sibling module documented the pitfall and this one did not copy it.
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not RUN_SH.is_file() or shutil.which("bash") is None,
    reason="needs a POSIX shell that can exec a shebang script from PATH",
)


def _stub_abicheck(tmp_path: Path, *, exit_code: int, payload: bytes | None) -> Path:
    """An abicheck that exits *exit_code* and writes *payload* to its ``-o`` path.

    ``payload=None`` writes nothing at all -- the "died after the exit code,
    before the report" shape, which is the one a real truncated/killed run
    produces and which no valid-JSON fixture can stand in for.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    body = [
        "#!/usr/bin/env bash",
        "prev=''",
        'for arg in "$@"; do',
        '  if [[ "$prev" == "-o" ]]; then',
    ]
    if payload is None:
        body.append("    :")
    else:
        blob = tmp_path / "payload.bin"
        blob.write_bytes(payload)
        body.append(f'    cp "{blob}" "$arg"')
    body += ["  fi", '  prev="$arg"', "done", f"exit {exit_code}"]
    stub = bindir / "abicheck"
    stub.write_text("\n".join(body) + "\n", encoding="utf-8")
    stub.chmod(0o755)
    return bindir


def _lib(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"\x7fELF")
    return str(path)


def _run_action(tmp_path: Path, env_extra: dict[str, str], bindir: Path) -> dict:
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    summary = tmp_path / "step_summary"
    summary.write_text("", encoding="utf-8")
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "PATH": f"{bindir}{os.pathsep}{env.get('PATH', '')}",
            "ACTION_PATH": str(ACTION_DIR),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUNNER_TEMP": str(runner_temp),
            "INPUT_ADD_JOB_SUMMARY": "true",
            **env_extra,
        }
    )
    proc = subprocess.run(
        [bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    outputs: dict = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    outputs["_stdout"] = proc.stdout
    outputs["_exit"] = proc.returncode
    outputs["_summary"] = summary.read_text(encoding="utf-8")
    return outputs


def _compare_env(tmp_path: Path) -> dict[str, str]:
    return {
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
        "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
        "INPUT_FORMAT": "json",
        "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
    }


def _stub_stdout_only(tmp_path: Path, *, stdout: str) -> Path:
    """An abicheck that writes nothing to ``-o`` and prints *stdout* instead.

    The documented `format: json` stdout mode -- no `output-file` at all, the
    report goes to stdout. An empty *stdout* is the failure this shape can
    reach.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "abicheck"
    body = "#!/usr/bin/env bash\n"
    if stdout:
        blob = tmp_path / "stdout.txt"
        blob.write_text(stdout, encoding="utf-8")
        body += f'cat "{blob}"\n'
    body += "exit 0\n"
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    return bindir


#: Module-level skip every consumer applies as its own ``pytestmark``.
#:
#: POSIX only, for the same reason `test_action_coverage_verdict.py` states:
#: this harness works by putting an *extensionless, shebang-dispatched*
#: `abicheck` on PATH, because `run.sh` resolves the binary by name. Windows has
#: neither the executable bit nor kernel shebang handling, and Git bash's
#: `chmod` is a no-op on NTFS, so the stub is not runnable there -- every test
#: then fails identically with the WSL launcher stub's own UTF-16 "no installed
#: distributions" text instead of anything from `run.sh`. The behaviour under
#: test is plain shell with no platform-dependent branch, and the Linux lane
#: exercises all of it.
#:
#: Omitting this marker is what turned the windows-latest unit lane red on this
#: PR: a sibling module documented the pitfall and one did not copy it. Sharing
#: it here is what stops that recurring.
REQUIRES_POSIX_SHELL = pytest.mark.skipif(
    os.name == "nt" or not RUN_SH.is_file() or shutil.which("bash") is None,
    reason="needs a POSIX shell that can exec a shebang script from PATH",
)
