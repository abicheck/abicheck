"""Run one ``scripts/verify.py`` step's command, echoing its output live.

``verify.py`` used to run a step with ``subprocess.run(capture_output=True)``
and print the captured text once the child exited. A step that never exits
-- a hung test, or one the CI job limit cancels -- then printed nothing at
all: the Windows integration lane ran 37 minutes with an empty log, and the
pytest-timeout traceback that would have named the stuck test was sitting in
the never-printed buffer. Here each line is written to this process's own
stdout/stderr (flushed) the moment the child produces it, and is also kept,
so the caller still gets a ``CompletedProcess`` with the full text. Split
out of ``verify.py`` to keep that orchestrator under its file-size record; a
leaf module with no import back into it.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO


def _pump(src: IO[str], sink: IO[str], kept: list[str]) -> None:
    for line in iter(src.readline, ""):
        kept.append(line)
        sink.write(line)
        sink.flush()
    src.close()


def run_streaming(
    cmd: Sequence[str], *, cwd: Path, env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run *cmd*, echoing each stdout/stderr line as it arrives.

    The return value matches ``subprocess.run(cmd, capture_output=True,
    text=True)``. Decoding replaces undecodable bytes rather than raising:
    a step's diagnostic text must never be what fails the step.
    """
    proc = subprocess.Popen(
        list(cmd),
        cwd=cwd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None and proc.stderr is not None
    out: list[str] = []
    err: list[str] = []
    pumps = [
        threading.Thread(
            target=_pump, args=(proc.stdout, sys.stdout, out), daemon=True
        ),
        threading.Thread(
            target=_pump, args=(proc.stderr, sys.stderr, err), daemon=True
        ),
    ]
    for pump in pumps:
        pump.start()
    returncode = proc.wait()
    # The pipes close when the child (and any grandchild holding them) exits;
    # the pumps then reach EOF on their own.
    for pump in pumps:
        pump.join()
    return subprocess.CompletedProcess(
        list(cmd), returncode, "".join(out), "".join(err)
    )
