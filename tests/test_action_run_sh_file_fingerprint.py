# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Nikolay Petrov
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

"""``action/run.sh``'s ``_file_fingerprint``: the (mtime, size) token that
decides whether a report destination was written by *this* run.

The contract, stated against ``os.stat`` as the oracle (not against the
helper's own output format): two states of a file fingerprint equal exactly
when their ``(st_mtime_ns, st_size)`` are equal, a missing path answers "",
and that holds whichever implementation answers -- the native ``stat`` fast
path or the ``os.stat`` fallback used when ``stat`` cannot report
nanoseconds.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from _action_run_sh_harness import annotation_helpers_source
from _workflow_exec import bash_executable, require_bash
from test_action_run_sh_py_safe_path import RUN_SH, _path_qualified_helper_source

pytestmark = pytest.mark.skipif(os.name == "nt", reason="needs a POSIX shell")


def _helper() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index("_file_fingerprint() {")
    return text[start : text.index("\n}\n", start) + 3]


def _fingerprints(
    tmp_path: Path, paths: list[str], *, fake_stat: str | None = None
) -> list[str]:
    """Fingerprint *paths* (relative to *tmp_path*) in one bash process.

    *fake_stat*, when given, is the body of a ``stat`` put first on PATH.
    """
    require_bash()
    env = dict(os.environ)
    if fake_stat is not None:
        bindir = tmp_path / "fakebin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "stat"
        stub.write_text("#!/bin/sh\n" + fake_stat + "\n", encoding="utf-8")
        stub.chmod(0o755)
        env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    calls = "".join(
        f'printf "[%s]\\n" "$(_file_fingerprint {shlex.quote(p)})"\n' for p in paths
    )
    script = (
        annotation_helpers_source()
        + _path_qualified_helper_source()
        + f"\n_PY_BIN={shlex.quote(sys.executable)}\n"
        + '_PY_SAFE_DIR="$(mktemp -d)"\n'
        + _helper()
        + f"\ncd {shlex.quote(str(tmp_path))}\n"
        + calls
    )
    script_path = tmp_path / "harness.sh"
    script_path.write_text(script, encoding="utf-8", newline="\n")
    result = subprocess.run(
        [bash_executable(), str(script_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    lines = [line[1:-1] for line in result.stdout.splitlines()]
    assert len(lines) == len(paths), result.stdout
    return lines


# Independently chosen (mtime_ns, size) states, including the one the
# fingerprint exists to catch: a rewrite inside the same second, same size.
_BASE_NS = 1_790_000_000_123_456_789
_STATES = [
    (_BASE_NS, 10),
    (_BASE_NS + 1, 10),  # 1ns later, same size
    (_BASE_NS, 11),  # same instant, one byte longer
    (_BASE_NS + 1_000_000_000, 10),  # next whole second
    (_BASE_NS, 10),  # back to the first state
]


@pytest.mark.parametrize(
    "fake_stat",
    [
        None,  # this host's native stat
        'echo "%.9Y:%s"',  # a stat that echoes the format literally
        'echo "1790000000:10"',  # a stat without sub-second precision
        "exit 1",  # no usable stat at all
    ],
    ids=["native", "literal-format", "whole-seconds", "failing"],
)
def test_fingerprints_are_equal_exactly_when_stat_says_so(
    tmp_path: Path, fake_stat: str | None
) -> None:
    oracle: list[tuple[int, int]] = []
    observed: list[str] = []
    for mtime_ns, size in _STATES:
        target = tmp_path / "report.json"
        target.write_bytes(b"x" * size)
        os.utime(target, ns=(mtime_ns, mtime_ns))
        st = target.stat()
        if st.st_mtime_ns != mtime_ns:
            pytest.skip("filesystem does not keep nanosecond mtimes")
        oracle.append((st.st_mtime_ns, st.st_size))
        observed += _fingerprints(tmp_path, ["report.json"], fake_stat=fake_stat)
    assert all(observed), observed
    for i in range(len(_STATES)):
        for j in range(len(_STATES)):
            assert (observed[i] == observed[j]) == (oracle[i] == oracle[j]), (
                i,
                j,
                observed,
            )


def test_a_missing_path_answers_empty(tmp_path: Path) -> None:
    # Empty is the "did not exist before" token: it can never equal a real
    # file's fingerprint, so a destination created by this run always reads
    # as changed.
    assert _fingerprints(tmp_path, ["absent.json"]) == [""]
    (tmp_path / "absent.json").write_text("{}", encoding="utf-8")
    assert _fingerprints(tmp_path, ["absent.json"]) != [""]
