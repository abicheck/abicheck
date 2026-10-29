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

"""``scripts/verify_stream.run_streaming``: a step's output reaches the log
while the step is still running.

Bug class ``test_harness.step_output_released_only_on_exit``: diagnostic
output held until a child exits is lost whenever the
child never exits on its own (a hung test, a CI job limit). The Windows
integration lane ran 37 minutes with an empty log because ``verify.py``
captured pytest's output and only printed it after the exit that the job
cancellation pre-empted. The invariant here is stated over several child
shapes (stdout only, stderr only, both interleaved, a fragment with no
newline, CRLF endings, undecodable and non-ASCII bytes, a nonzero exit):
everything the child flushed is in the parent's stream *before the child is
released*, and the text equals what ``subprocess.run(capture_output=True,
text=True)`` makes of the same child -- the standard library is the oracle
for the host's pipe encoding and newline translation, not this module.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from verify_stream import run_streaming  # noqa: E402

pytestmark = pytest.mark.timeout(150)


class _Recorder(io.StringIO):
    """A thread-safe text sink standing in for the CI log."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

    def write(self, s: str) -> int:
        with self._lock:
            return super().write(s)

    def text(self) -> str:
        with self._lock:
            return self.getvalue()


#: (out lines, err lines, exit code, line ending). Each piece is written and
#: flushed, then the child blocks until the test releases it.
_SHAPES = {
    "stdout_only": (["first line", "second line"], [], 0, "\n"),
    "stderr_only": ([], ["a warning", "a traceback line"], 0, "\n"),
    "interleaved": (["out 1", "out 2"], ["err 1", "err 2"], 1, "\n"),
    # pytest -v without xdist prints a node id and then, on the same line,
    # its verdict: the id alone is what a hung test leaves.
    "no_trailing_newline": (["tests/test_x.py::test_y "], [], 0, ""),
    "crlf_line_endings": (["windows line 1", "windows line 2"], [], 0, "\r\n"),
    "undecodable_bytes": (["bad \udcff byte"], [], 3, "\n"),
    "non_ascii": (["caf\u00e9 \u2211"], [], 0, "\n"),
    "timeout_traceback": (
        ["[gw0] [ 98%] PASSED tests/test_a.py::test_b"],
        ["+++++ Timeout +++++", '  File "x.py", line 1, in wait'],
        1,
        "\n",
    ),
}

_CHILD = r"""
import os, sys, time
out, err, code, sentinel, end = eval(sys.argv[1])
for line in out:
    sys.stdout.buffer.write(line.encode("utf-8", "surrogateescape") + end.encode())
    sys.stdout.buffer.flush()
for line in err:
    sys.stderr.buffer.write(line.encode("utf-8") + b"\n")
    sys.stderr.buffer.flush()
deadline = time.monotonic() + 60
while not os.path.exists(sentinel) and time.monotonic() < deadline:
    time.sleep(0.02)
sys.exit(code)
"""

_ENV = {"PATH": "", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}


def _child(shape: str, sentinel: Path) -> list[str]:
    out, err, code, end = _SHAPES[shape]
    return [sys.executable, "-c", _CHILD, repr((out, err, code, str(sentinel), end))]


def _oracle(shape: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """What ``subprocess.run(capture_output=True, text=True)`` -- the shape
    ``run_streaming`` replaces -- makes of the same child: the host's pipe
    encoding and newline translation, applied by the standard library."""
    released = tmp_path / "already-released"
    released.write_text("go", encoding="utf-8")
    return subprocess.run(
        _child(shape, released),
        cwd=tmp_path,
        env=_ENV,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_output_reaches_the_log_before_the_child_exits(
    shape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    want = _oracle(shape, tmp_path)
    sentinel = tmp_path / "release"
    out_log, err_log = _Recorder(), _Recorder()
    monkeypatch.setattr(sys, "stdout", out_log)
    monkeypatch.setattr(sys, "stderr", err_log)

    result: list[subprocess.CompletedProcess[str]] = []
    runner = threading.Thread(
        target=lambda: result.append(
            run_streaming(_child(shape, sentinel), cwd=tmp_path, env=_ENV)
        ),
        daemon=True,
    )
    runner.start()
    # The child is blocked on the sentinel: everything it flushed -- a
    # fragment with no newline included -- must already be visible.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if (out_log.text(), err_log.text()) == (want.stdout, want.stderr):
            break
        time.sleep(0.02)
    seen = (out_log.text(), err_log.text())
    still_running = runner.is_alive()
    sentinel.write_text("go", encoding="utf-8")
    runner.join(60)

    assert still_running, "the child exited before it was released"
    assert seen == (want.stdout, want.stderr), "output held back while the child ran"
    assert not runner.is_alive()
    (proc,) = result
    assert (proc.returncode, proc.stdout, proc.stderr) == (
        want.returncode,
        want.stdout,
        want.stderr,
    )
    assert (out_log.text(), err_log.text()) == (want.stdout, want.stderr)


def test_verify_run_step_streams_through_run_streaming() -> None:
    """``verify.run_step`` is the caller the invariant protects: it must not
    regain a capture-then-print path of its own."""
    source = (
        Path(__file__).resolve().parent.parent / "scripts" / "verify.py"
    ).read_text(encoding="utf-8")
    body = source[source.index("def run_step(") : source.index("def main(")]
    assert "run_streaming(" in body
    assert "capture_output" not in body
    assert "subprocess.run(" not in body
