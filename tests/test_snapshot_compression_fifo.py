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

"""FIFO write-through tests, split out of ``tests/test_snapshot_compression.py``
purely to stay under that file's ADR-061 no-growth line baseline rather
than growing it further -- see that file's own module docstring for the
sibling coverage of everything else in ``abicheck/snapshot_io.py``.
"""

from __future__ import annotations

import os
import threading

import pytest

from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.serialization import write_snapshot


def _sample_snapshot() -> AbiSnapshot:
    """Mirrors ``test_snapshot_compression.py``'s own ``_sample_snapshot`` --
    a small, standalone fixture, duplicated deliberately (not imported)
    so this leaf test module stays self-contained the same way its
    ``test_snapshot_compression_skippable_frames.py`` sibling does."""
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[
            Function(
                name="foo_init",
                mangled="_Z8foo_initv",
                return_type="int",
                params=[Param(name="x", type="int")],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[
            RecordType(
                name="Widget",
                kind="struct",
                size_bits=64,
                alignment_bits=32,
                fields=[TypeField(name="a", type="int", offset_bits=0)],
            ),
        ],
    )


def _write_through_fifo(fifo_path, write_fn) -> bytes:
    """Run *write_fn* (a zero-arg callable that writes through *fifo_path*)
    while a background thread concurrently drains the FIFO's read end, and
    return everything the reader collected.

    **Replaces a real, observed macOS CI hang** (Codex review, fresh
    evidence): the previous version of this helper's two callers opened
    the read end non-blocking *before* the write, on the theory that a
    FIFO's write-side ``open()`` only ever blocks waiting for a reader to
    connect -- true, but the two callers' write was of already-serialized
    snapshot bytes that were never actually drained afterward. That
    assumption -- "the write always fits in the pipe's kernel buffer, so
    nothing needs to read concurrently" -- is not portable: a platform's
    FIFO buffer capacity is not part of any portability guarantee this
    project can rely on (observed materially smaller on macOS runners than
    on the Linux runners this suite was originally written against), and a
    write that exceeds it blocks in the kernel until *something* reads,
    which nothing here ever did. Both callers hung for the full
    pytest-timeout budget on every macOS CI run once this was reached,
    consistently and reproducibly -- not a flake.

    A concurrently-draining reader thread is the standard, capacity-
    independent way to exercise a pipe/FIFO write and is correct
    regardless of the platform's actual buffer size, so it replaces the
    non-blocking-open trick entirely rather than patching around one
    observed size: the reader thread's own blocking ``open()`` is what
    satisfies "a reader is present" for the writer's blocking open to
    proceed, and its blocking ``read()`` loop drains the pipe as fast as
    the writer fills it, so the writer's ``write()`` calls can never block
    on a full buffer either. The reader thread's own ``open()``/``read()``
    call chain returns once the writer closes its end (EOF) -- ``.join()``
    is still given a bound so a genuine future regression fails this test
    promptly with a clear stack instead of silently eating the whole
    600s pytest-timeout budget again.
    """
    collected = bytearray()

    def _drain() -> None:
        # This blocking open() -- exactly like the writer's own -- blocks
        # until the *other* side connects; there is deliberately no
        # cross-thread handshake before it. FIFO open() is itself the
        # rendezvous (POSIX: a blocking read-side open() waits for a
        # writer and vice versa), so starting this thread and then
        # immediately calling write_fn() below is sufficient for both
        # sides to meet -- an explicit "reader is ready" event would
        # instead *deadlock*, since this open() call does not return
        # until the writer side (called only after such an event) shows
        # up.
        with open(fifo_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                collected.extend(chunk)

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    try:
        write_fn()
    finally:
        reader.join(timeout=30)
    assert not reader.is_alive(), "FIFO reader thread never observed EOF"
    return bytes(collected)


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFOs only")
def test_write_through_fifo_does_not_replace_it(tmp_path):
    """Codex review, PR #699: an existing non-regular destination has no
    meaningful "atomic replace" -- os.replace() would swap it out for a
    brand-new regular file, destroying the special file. Verify
    write_snapshot writes directly through a named pipe instead (no
    error, and the FIFO is still a FIFO afterward, not a regular file).
    See :func:`_write_through_fifo` for why the write runs alongside a
    concurrently-draining reader thread rather than assuming the write
    fits in the pipe's kernel buffer unread."""
    import stat as stat_mod

    fifo_path = tmp_path / "pipe.abicheck.json"
    os.mkfifo(fifo_path)
    snap = _sample_snapshot()
    _write_through_fifo(
        fifo_path,
        lambda: write_snapshot(snap, fifo_path, compression="none"),
    )  # must not raise
    assert stat_mod.S_ISFIFO(fifo_path.stat().st_mode)  # still a FIFO


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFOs only")
def test_write_through_a_symlink_to_a_fifo_does_not_need_realpath(
    tmp_path, monkeypatch
):
    """Codex review, PR #699 (second finding): the non-regular check used
    to resolve *path* via os.path.realpath() before stat()-ing it -- for a
    symlink whose target is a pipe-backed file descriptor (e.g. /dev/stdout
    connected to a pipe on a CI runner), realpath() can return a synthetic,
    unstat-able pseudo-path like /proc/<pid>/fd/pipe:[12345], which then
    made the follow-up stat() fail and the non-regular destination was
    never recognized -- the write fell through to the atomic-rename path
    and tried to create a temp file under that bogus pseudo-directory.

    Reproduce the failure mode directly: monkeypatch os.path.realpath to
    return a path that cannot be stat()-ed at all, and confirm
    write_snapshot through a symlink-to-FIFO still succeeds -- proving the
    non-regular detection no longer depends on realpath() succeeding. See
    :func:`_write_through_fifo` for why the write runs alongside a
    concurrently-draining reader thread rather than assuming the write
    fits in the pipe's kernel buffer unread."""
    import stat as stat_mod

    fifo_path = tmp_path / "real_pipe"
    os.mkfifo(fifo_path)
    link_path = tmp_path / "link_to_pipe.abicheck.json"
    link_path.symlink_to(fifo_path)

    def _broken_realpath(path, *args, **kwargs):
        return "/proc/nonexistent-pid/fd/pipe:[999999]"

    monkeypatch.setattr(os.path, "realpath", _broken_realpath)

    snap = _sample_snapshot()
    _write_through_fifo(
        fifo_path,
        lambda: write_snapshot(snap, link_path, compression="none"),
    )  # must not raise
    assert stat_mod.S_ISFIFO(fifo_path.stat().st_mode)  # still a FIFO
    assert stat_mod.S_ISLNK(link_path.lstat().st_mode)  # symlink intact
