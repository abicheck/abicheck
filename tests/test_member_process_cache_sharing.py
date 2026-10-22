# SPDX-License-Identifier: Apache-2.0
"""Forked release members and sides share the work threads used to share.

Field run (28-member release, ``--depth headers``): with members in forked
processes a cold side looked the snapshot cache up twice (112 lookups for 56
sides), and children over one header set each ran clang and re-streamed the
same projection (``dump.binary`` 2.4x per dump against the thread fan-out).
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

from abicheck.model import AbiSnapshot
from abicheck.storage.cross_process_lock import (
    produce_ast_once_across_processes,
    single_flight,
)
from abicheck.workflows import side_isolation
from abicheck.workflows.cache_probe import (
    cache_only_probe,
    probed_miss_scope,
    refuse_dump_while_probing,
    take_probed_miss,
)
from abicheck.workflows.dump.cache import api as cache_api

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="flock and fork are POSIX"
)


# ---------------------------------------------------------------------------
# One snapshot-cache lookup per cold side
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc" / "a.h").write_text("int f(void);\n")
    for name in ("old.so", "new.so"):
        (tmp_path / name).write_bytes(b"\x7fELF " + name.encode())
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "warm_mask", [(False, False), (True, False), (False, True), (True, True)]
)
def test_each_side_looks_the_cache_up_exactly_once(project, monkeypatch, warm_mask):
    # Oracle: a side served warm reads its entry once (the probe); a cold
    # side's probe already missed, so its extraction must not read again.
    lookups: list[str] = []
    real_lookup = cache_api._lookup_key

    def counting_lookup(key, path):
        lookups.append(path.name)
        return real_lookup(key, path)

    monkeypatch.setattr(cache_api, "_lookup_key", counting_lookup)
    # In-process stand-in for the fork: the probe scope must reach the side
    # either way (fork copies it).
    monkeypatch.setattr(
        side_isolation, "run_isolated", lambda fns, **_: [f() for f in fns]
    )
    dumps: list[str] = []

    def run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
        dumps.append(path.name)
        return AbiSnapshot(library=path.name, version=version)

    def side(name: str):
        return lambda: cache_api.cached_run_dump(
            run_dump, Path(name), "elf", [Path("inc/a.h")], [], "1", "c"
        )

    names = ("old.so", "new.so")
    for name, warm in zip(names, warm_mask, strict=True):
        if warm:
            side(name)()
    dumps.clear()
    lookups.clear()

    got = side_isolation.run_cold_isolated(
        [side(n) for n in names], lambda fn: fn, concurrent=True
    )
    assert [s.library for s in got] == list(names)
    assert sorted(lookups) == sorted(names)
    assert dumps == [n for n, w in zip(names, warm_mask, strict=True) if not w]


def test_a_probed_miss_is_consumed_once_and_only_inside_its_scope():
    with cache_only_probe(), pytest.raises(BaseException):
        refuse_dump_while_probing("k")
    assert take_probed_miss("k") is False  # no scope: nothing recorded
    with probed_miss_scope():
        with cache_only_probe(), pytest.raises(BaseException):
            refuse_dump_while_probing("k")
        assert take_probed_miss("other") is False
        assert take_probed_miss("k") is True
        assert take_probed_miss("k") is False  # the next extraction reads
    with probed_miss_scope():
        assert take_probed_miss("k") is False  # a fresh scope starts empty


# ---------------------------------------------------------------------------
# One frontend run per AST key across processes
# ---------------------------------------------------------------------------


def _produce_in_child(entry: str, runs: str, backend: str, group: bool) -> None:
    def producer() -> str:
        if os.path.exists(entry):
            return "decoded"
        time.sleep(0.3)  # long enough that unlocked siblings overlap
        with open(runs, "a", encoding="utf-8") as fh:
            fh.write("ran\n")
        with open(entry, "w", encoding="utf-8") as fh:
            fh.write("{}")
        return "ran"

    produce_ast_once_across_processes(
        backend, "key", producer, object() if group else None
    )


def _run_siblings(tmp_path: Path, backend: str, *, group: bool, n: int = 4) -> int:
    entry, runs = tmp_path / "entry.json", tmp_path / "runs.log"
    ctx = multiprocessing.get_context("fork")
    procs = [
        ctx.Process(
            target=_produce_in_child, args=(str(entry), str(runs), backend, group)
        )
        for _ in range(n)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(30)
        assert proc.exitcode == 0
    return len(runs.read_text(encoding="utf-8").splitlines())


@posix_only
@pytest.mark.parametrize("backend", ["clang", "castxml"])
def test_concurrent_processes_run_a_disk_backed_frontend_once(
    tmp_path, monkeypatch, backend
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert _run_siblings(tmp_path, backend, group=False) == 1


@posix_only
@pytest.mark.parametrize(
    ("backend", "group"), [("clang-normalized", False), ("clang", True)]
)
def test_process_local_acquisitions_are_not_serialized(
    tmp_path, monkeypatch, backend, group
):
    # Negative control: an id-keyed or non-disk acquisition has nothing on
    # disk for a waiter to read, so it is not locked -- siblings overlap.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert _run_siblings(tmp_path, backend, group=group) > 1


@posix_only
def test_single_flight_excludes_other_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    log = tmp_path / "log"

    def child(tag: str) -> None:
        with single_flight("projection:x"):
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"{tag}+\n")
            time.sleep(0.2)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"{tag}-\n")

    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=child, args=(str(i),)) for i in range(3)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(30)
    lines = log.read_text(encoding="utf-8").split()
    # Every enter is immediately followed by its own exit: no interleaving.
    assert len(lines) == 6
    for enter, leave in zip(lines[::2], lines[1::2], strict=True):
        assert enter[:-1] == leave[:-1] and enter[-1] == "+" and leave[-1] == "-"


def test_single_flight_is_a_no_op_in_reference_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("ABICHECK_REFERENCE_MODE", "1")
    with single_flight("a"), single_flight("a"):  # would self-deadlock if locked
        pass
    assert not (tmp_path / "cache" / "abi_check" / "locks").exists()


# ---------------------------------------------------------------------------
# In-process branches (forked children above are not seen by coverage)
# ---------------------------------------------------------------------------


@posix_only
def test_single_flight_takes_and_releases_the_lock(tmp_path, monkeypatch):
    import fcntl

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    with single_flight("held"):
        (lock,) = (tmp_path / "cache" / "abi_check" / "locks").iterdir()
        fd = os.open(lock, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released on exit
    finally:
        os.close(fd)


@posix_only
def test_single_flight_waits_for_a_holder_then_proceeds(tmp_path, monkeypatch):
    import fcntl
    import threading

    from abicheck.storage import cross_process_lock

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = cross_process_lock._lock_path("busy")
    assert path is not None
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    order: list[str] = []
    released = threading.Timer(
        0.2, lambda: (order.append("released"), fcntl.flock(fd, fcntl.LOCK_UN))
    )
    released.start()
    with single_flight("busy"):
        order.append("entered")
    released.join()
    os.close(fd)
    assert order == ["released", "entered"]


@posix_only
@pytest.mark.parametrize("failure", ["lock_dir", "open", "flock"])
def test_single_flight_runs_unlocked_when_locking_is_unavailable(
    tmp_path, monkeypatch, failure
):
    import fcntl

    from abicheck.storage import cross_process_lock

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    if failure == "lock_dir":

        def no_dir(_backend):
            raise OSError("read-only")

        monkeypatch.setattr(
            "abicheck.storage.ast_cache_location.backend_cache_dir", no_dir
        )
    elif failure == "open":
        monkeypatch.setattr(
            cross_process_lock, "_lock_path", lambda _n: "/nonexistent/x/y"
        )
    else:

        def no_flock(_fd, _op):
            raise OSError("flock unsupported")

        monkeypatch.setattr(fcntl, "flock", no_flock)
    ran: list[int] = []
    with single_flight("x"), single_flight("x"):  # nested: would block if locked
        ran.append(1)
    assert ran == [1]


@posix_only
def test_single_flight_wait_honours_the_request_deadline(tmp_path, monkeypatch):
    import fcntl

    from abicheck import deadline
    from abicheck.storage import cross_process_lock

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = cross_process_lock._lock_path("stuck")
    assert path is not None
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        calls = iter([None, deadline.DeadlineExceeded(0.0)])

        def check():
            exc = next(calls)
            if exc is not None:
                raise exc

        monkeypatch.setattr(deadline, "check", check)
        with pytest.raises(deadline.DeadlineExceeded), single_flight("stuck"):
            pytest.fail("entered a lock another holder still has")
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    ("backend", "group", "reference", "locked"),
    [
        ("clang", None, False, True),
        ("castxml", None, False, True),
        ("clang-normalized", None, False, False),
        ("clang", "root", False, False),
        ("clang", None, True, False),
    ],
)
def test_ast_production_locks_only_disk_backed_content_keys(
    tmp_path, monkeypatch, backend, group, reference, locked
):
    from abicheck.storage import cross_process_lock

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    if reference:
        monkeypatch.setenv("ABICHECK_REFERENCE_MODE", "1")
    names: list[str] = []
    real = cross_process_lock.single_flight

    def recording(name):
        names.append(name)
        return real(name)

    monkeypatch.setattr(cross_process_lock, "single_flight", recording)
    got = produce_ast_once_across_processes(backend, "k", lambda: "ast", group)
    assert got == "ast"
    assert names == ([f"ast:{backend}:k"] if locked else [])


def test_probe_without_a_key_or_scope_records_nothing():
    with probed_miss_scope():
        with cache_only_probe(), pytest.raises(BaseException):
            refuse_dump_while_probing("")
        assert take_probed_miss("") is False
    refuse_dump_while_probing("k")  # outside a probe: no signal at all
    assert take_probed_miss("k") is False
