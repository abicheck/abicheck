"""Run one ``scripts/verify.py`` step's command, echoing its output live.

``verify.py`` used to run a step with ``subprocess.run(capture_output=True)``
and print the captured text once the child exited. A step that never exits
-- a hung test, or one the CI job limit cancels -- then printed nothing at
all: the Windows integration lane ran 37 minutes with an empty log, and the
pytest-timeout traceback that would have named the stuck test was sitting in
the never-printed buffer. Here whatever the child flushes -- a whole line or
a fragment -- is written to this process's own stdout/stderr at once, and kept,
so the caller still gets a ``CompletedProcess`` with the full text. Split
out of ``verify.py`` to keep that orchestrator under its file-size record; a
leaf module with no import back into it.
"""

from __future__ import annotations

import codecs
import io
import locale
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO

#: The encoding ``text=True`` decodes a pipe with; a child writing to a pipe
#: uses the same one, so non-ASCII output survives on every host.
_PIPE_ENCODING = locale.getpreferredencoding(False)


def _pump(src: IO[bytes], sink: IO[str], kept: list[str]) -> None:
    """Forward *src* to *sink* as it arrives, newline or not: an unbuffered
    pipe's ``read`` returns whatever the child has flushed, so a fragment
    such as pytest's ``test_x.py::test_y `` (its verdict still pending) is in
    the log while the test runs."""
    decoder = io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder(_PIPE_ENCODING)(errors="replace"),
        translate=True,
    )
    while chunk := src.read(65536):
        if text := decoder.decode(chunk):
            kept.append(text)
            sink.write(text)
            sink.flush()
    if text := decoder.decode(b"", final=True):
        kept.append(text)
        sink.write(text)
        sink.flush()
    src.close()


def run_streaming(
    cmd: Sequence[str], *, cwd: Path, env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run *cmd*, echoing its stdout/stderr as the child writes them.

    The return value matches ``subprocess.run(cmd, capture_output=True,
    text=True)``: the same encoding and newline translation. Decoding
    replaces undecodable bytes rather than raising: a step's diagnostic text
    must never be what fails the step.
    """
    proc = subprocess.Popen(
        list(cmd),
        cwd=cwd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
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
