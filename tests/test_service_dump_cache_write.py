# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``cached_run_dump``'s cache write switch and memory-trace phases.

Oracle: whether the passed-in ``run_dump`` was called (a miss) or not (a
hit), and which ``memory_trace.phase`` names were entered -- never the
cache's own internals.
"""

from __future__ import annotations

import pytest

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.workflows.dump.cache.api import cached_run_dump


def _sample_snap() -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
    )


@pytest.mark.parametrize("off", ["0", "false", "no", "off"])
def test_write_off_still_reads_but_never_populates(tmp_path, monkeypatch, off):
    """``ABICHECK_SNAPSHOT_CACHE_WRITE=0`` skips the store, not the lookup:
    an entry written by an earlier run is still served."""
    binary = tmp_path / "lib.so"
    binary.write_bytes(b"ELF fake content")
    calls = []

    def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
        calls.append(1)
        return _sample_snap()

    monkeypatch.setenv("ABICHECK_SNAPSHOT_CACHE_WRITE", off)
    cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")
    cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")
    assert len(calls) == 2  # nothing was stored, so both missed

    monkeypatch.delenv("ABICHECK_SNAPSHOT_CACHE_WRITE")
    cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")
    assert len(calls) == 3  # default: populates
    monkeypatch.setenv("ABICHECK_SNAPSHOT_CACHE_WRITE", off)
    cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")
    assert len(calls) == 3  # write-off still reads the populated entry


def test_memory_trace_phases_bracket_lookup_and_store(tmp_path, monkeypatch):
    """On a warm cache the lookup replaces the dump; it must be traced."""
    import contextlib

    from abicheck.workflows import memory_trace

    seen: list[str] = []

    @contextlib.contextmanager
    def record(name, /, **attrs):
        seen.append(name)
        yield

    monkeypatch.setattr(memory_trace, "phase", record)
    binary = tmp_path / "lib.so"
    binary.write_bytes(b"ELF fake content")

    def fake_run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
        return _sample_snap()

    cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")
    assert seen == ["dump.cache.lookup", "dump.cache.store"]
    seen.clear()
    cached_run_dump(fake_run_dump, binary, "elf", [], [], "1.0", "c++")
    assert seen == ["dump.cache.lookup"]
