# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Per-side process isolation returns what in-process resolution returns,
and leaves the child's memory behind."""

from __future__ import annotations

import multiprocessing
import os
import sys
import threading

import pytest

from abicheck.errors import SnapshotError
from abicheck.workflows.side_isolation import isolation_enabled, run_isolated

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="isolation forks; Linux only"
)


class _SideFailed(Exception):
    pass


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("ABICHECK_EXTRACTION_ISOLATION", "process")


def test_disabled_runs_in_process(monkeypatch):
    monkeypatch.delenv("ABICHECK_EXTRACTION_ISOLATION", raising=False)
    assert not isolation_enabled()
    pid = os.getpid()
    assert run_isolated([os.getpid, lambda: 7], concurrent=True) == [pid, 7]


@linux_only
@pytest.mark.parametrize("concurrent", [True, False])
def test_results_come_back_in_order_from_children(isolated, concurrent):
    parent = os.getpid()
    fns = [lambda i=i: (i, os.getpid(), {"k": list(range(i))}) for i in range(4)]
    out = run_isolated(fns, concurrent=concurrent)
    assert [r[0] for r in out] == [0, 1, 2, 3]
    assert all(r[1] != parent for r in out)
    assert out[3][2] == {"k": [0, 1, 2]}


@linux_only
def test_child_exception_is_reraised_and_siblings_are_reaped(isolated):
    def boom():
        raise _SideFailed("old side failed")

    with pytest.raises(_SideFailed, match="old side failed"):
        run_isolated([boom, lambda: 1], concurrent=True)
    assert multiprocessing.active_children() == []


@linux_only
def test_child_that_dies_without_answering_is_a_snapshot_error(isolated):
    with pytest.raises(SnapshotError, match="exited with status 3"):
        run_isolated([lambda: os._exit(3)], concurrent=False)


@linux_only
def test_unpicklable_result_is_reported_not_lost(isolated):
    with pytest.raises(SnapshotError, match="could not be returned"):
        run_isolated([lambda: threading.Lock()], concurrent=False)


@linux_only
def test_child_allocation_does_not_stay_in_the_parent(isolated):
    """The point of isolation: a child's transient peak never becomes the
    parent's. ru_maxrss is the process high-water mark, so the parent's own
    must not move by anything near what the child allocated."""
    import resource  # POSIX-only: imported here so Windows can collect this module

    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    def heavy():
        blob = [bytearray(1 << 20) for _ in range(300)]  # ~300 MiB
        return len(blob)

    assert run_isolated([heavy], concurrent=False) == [300]
    grown_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
    assert grown_kib < 100 * 1024


@linux_only
@pytest.mark.integration
def test_compare_report_is_identical_with_isolation(tmp_path, monkeypatch):
    import json
    import shutil
    import subprocess

    from click.testing import CliRunner

    from abicheck.cli import main

    cc = shutil.which("gcc") or shutil.which("cc")
    if cc is None or shutil.which("castxml") is None:
        pytest.skip("needs a C compiler and castxml")
    monkeypatch.chdir(tmp_path)
    for side, field in (("old", "int a;"), ("new", "int a; long b;")):
        (tmp_path / side).mkdir()
        (tmp_path / side / "api.h").write_text(
            f"struct S {{ {field} }};\nint use(struct S *s);\n"
        )
        (tmp_path / side / "api.c").write_text(
            '#include "api.h"\nint use(struct S *s) { return s->a; }\n'
        )
        subprocess.run(
            [cc, "-shared", "-fPIC", "-g", "api.c", "-o", "libapi.so"],
            cwd=tmp_path / side,
            check=True,
        )

    def run(mode: str | None) -> tuple[int, list]:
        if mode is None:
            monkeypatch.delenv("ABICHECK_EXTRACTION_ISOLATION", raising=False)
        else:
            monkeypatch.setenv("ABICHECK_EXTRACTION_ISOLATION", mode)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                "old/libapi.so",
                "new/libapi.so",
                "--header",
                "old=old/api.h",
                "--header",
                "new=new/api.h",
                "-o",
                "json=r.json",
            ],
            catch_exceptions=False,
        )
        report = json.loads((tmp_path / "r.json").read_text())
        return result.exit_code, sorted(
            (c["kind"], c.get("symbol") or "") for c in report["changes"]
        )

    import abicheck.workflows.side_isolation as iso

    forked: list[int] = []
    real = iso._run_children

    def spy(ctx, fns):
        forked.append(len(fns))
        return real(ctx, fns)

    monkeypatch.setattr(iso, "_run_children", spy)
    baseline = run(None)
    assert baseline[1], "fixture must produce findings or the check is vacuous"
    assert forked == []
    assert run("process") == baseline
    # Both sides really went through children (sequential CLI: one each).
    assert forked == [1, 1]


def _child_payload(fn):
    """Run the child body in-process (coverage does not follow a fork)."""
    from abicheck.workflows.side_isolation import _child

    recv, send = multiprocessing.Pipe(duplex=False)
    _child(send, fn)
    return recv.recv()


def test_child_body_sends_result_error_and_unpicklable_report():
    assert _child_payload(lambda: {"a": 1}) == ("ok", {"a": 1})

    def boom():
        raise _SideFailed("x")

    status, err = _child_payload(boom)
    assert status == "err" and isinstance(err, _SideFailed)
    status, err = _child_payload(lambda: threading.Lock())
    assert status == "err" and isinstance(err, SnapshotError)
    assert "could not be returned" in str(err)


def test_isolation_is_linux_only(monkeypatch):
    import abicheck.workflows.side_isolation as iso

    monkeypatch.setenv("ABICHECK_EXTRACTION_ISOLATION", "process")
    monkeypatch.setattr(iso.sys, "platform", "darwin")
    assert not isolation_enabled()
    monkeypatch.setattr(iso.sys, "platform", "linux")
    assert isolation_enabled()
    monkeypatch.setenv("ABICHECK_EXTRACTION_ISOLATION", "thread")
    assert not isolation_enabled()


@linux_only
def test_every_child_is_reaped_when_an_early_side_fails(isolated):
    import time

    def boom():
        raise _SideFailed("first")

    def slow():
        time.sleep(0.3)
        return list(range(100_000))  # a payload big enough to block on send

    with pytest.raises(_SideFailed):
        run_isolated([boom, slow, slow], concurrent=True)
    assert multiprocessing.active_children() == []
