# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A compare run is never unbounded: no stated budget means the 2-hour default.

Bug class ``concurrency.forked_child_unbounded_reap`` (known gap "A forked child
that is alive but never answers blocks the parent's pipe read", closed by
ADR-068's 2026-11 amendment). With no ``--budget`` the run had no deadline, so
the three waits on a child -- the side-isolation pipe read, the release member
pool's ``wait``/read, and the release memory-admission wait -- blocked for as
long as a deadlocked child stayed alive.

The oracle is the stated product rule, not the implementation's helpers: an
unstated budget expires at exactly 7200 s of the injected clock, a stated one
at its own value, on the CLI (scalar and release), the typed API and every
wait; expiry is exit 5 / ``DeadlineExceeded`` with a message naming how to set
a longer budget, never a clean result. No test sleeps: ``deadline._clock`` is
replaced by a fake clock.
"""

from __future__ import annotations

import json
import multiprocessing
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from abicheck import deadline
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.change_catalog.registry import Verdict
from abicheck.serialization import snapshot_to_json
from abicheck.workflows import keyed_process_pool, run_deadline, side_isolation
from abicheck.workflows.compare.budget import budget_deadline, parse_budget_seconds
from abicheck.workflows.compare.execute import run_compare_request
from abicheck.workflows.release_admission import MemoryAdmission
from abicheck.workflows.release_members import compare_release_member
from abicheck.workflows.request_inputs import InputSpec
from abicheck.workflows.service_requests import CompareRequest

REPO = Path(__file__).resolve().parent.parent
TWO_HOURS = 7200.0


class FakeClock:
    """``time.monotonic`` stand-in: ``start`` on the first read, ``later`` after.

    The first read is the scope's entry; every later read is "the run has been
    going for ``later`` seconds".
    """

    def __init__(self, later: float, start: float = 0.0) -> None:
        self.start, self.later, self.reads = start, later, 0

    def __call__(self) -> float:
        self.reads += 1
        return self.start if self.reads == 1 else self.later


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    def install(later: float) -> FakeClock:
        fake = FakeClock(later)
        monkeypatch.setattr(deadline, "_clock", fake)
        return fake

    return install


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deadline, "_clock", lambda: 0.0)


# -- the default, as a value ---------------------------------------------------


def test_registry_default_is_the_code_default() -> None:
    """ADR-091 D6: the registry's recorded default is the value the code applies."""
    registry = yaml.safe_load(
        (REPO / "architecture/option-vocabulary.yaml").read_text(encoding="utf-8")
    )
    (concept,) = [c for c in registry["concepts"] if c["concept"] == "budget"]
    assert parse_budget_seconds(concept["default"]) == TWO_HOURS
    assert run_deadline.DEFAULT_BUDGET_S == TWO_HOURS
    assert parse_budget_seconds(run_deadline.DEFAULT_BUDGET_TEXT) == TWO_HOURS


@pytest.mark.parametrize(
    ("budget_s", "expected_remaining"),
    [(None, TWO_HOURS), (60.0, 60.0), (0.0, 0.0), (5 * 3600.0, 5 * 3600.0)],
)
def test_budget_deadline_without_an_active_deadline(
    frozen: None, budget_s: float | None, expected_remaining: float
) -> None:
    assert deadline.remaining() is None
    with budget_deadline(budget_s):
        assert deadline.remaining() == expected_remaining
    assert deadline.remaining() is None


@pytest.mark.parametrize(
    ("budget_s", "expected_remaining"),
    # An unstated budget inherits the active deadline (a release member runs
    # under its release's budget); a stated one is its own.
    [(None, 30.0), (60.0, 60.0), (3 * 3600.0, 3 * 3600.0)],
)
def test_budget_deadline_inside_an_active_deadline(
    frozen: None, budget_s: float | None, expected_remaining: float
) -> None:
    with deadline.deadline_scope(30.0):
        with budget_deadline(budget_s):
            assert deadline.remaining() == expected_remaining
        assert deadline.remaining() == 30.0


@pytest.mark.parametrize("waiting_for", [None, "a side-resolution child process"])
def test_deadline_exceeded_survives_the_child_result_pipe(
    waiting_for: str | None,
) -> None:
    """A release member's failure crosses a forked child's result pipe."""
    exc = deadline.DeadlineExceeded(-2.5, waiting_for=waiting_for)
    recv, send = multiprocessing.Pipe(duplex=False)
    try:
        send.send(exc)
        back = recv.recv()
    finally:
        send.close()
        recv.close()
    assert type(back) is deadline.DeadlineExceeded
    assert (back.remaining_s, back.waiting_for, str(back)) == (
        -2.5,
        waiting_for,
        str(exc),
    )


# -- CLI and typed API: default applied, explicit overrides, expiry -------------


def _write_snapshots(tmp_path: Path) -> tuple[Path, Path]:
    paths = []
    for version in ("1.0", "2.0"):
        snap = AbiSnapshot(
            library="libtest.so",
            version=version,
            functions=[
                Function(
                    name="foo",
                    mangled="_Z3foov",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        path = tmp_path / f"{version}.abi.json"
        path.write_text(snapshot_to_json(snap), encoding="utf-8")
        paths.append(path)
    return paths[0], paths[1]


def _cli_compare(tmp_path: Path, budget_args: list[str]) -> tuple[Any, Path]:
    old_p, new_p = _write_snapshots(tmp_path)
    out_p = tmp_path / "out.json"
    result = CliRunner().invoke(
        main,
        ["compare", str(old_p), str(new_p), "-o", f"json={out_p}", *budget_args],
    )
    return result, out_p


@pytest.mark.parametrize(
    ("elapsed", "budget_args", "expected_message"),
    [
        # The default applies and expires.
        (TWO_HOURS + 1, [], "no budget was set, so the default 2h applied"),
        # A stated budget is enforced itself, longer or shorter than the default.
        (3 * 3600.0 + 1, ["--budget", "3h"], "--budget '3h' exceeded"),
        (61.0, ["--budget", "1m"], "--budget '1m' exceeded"),
    ],
)
def test_cli_compare_budget_overflow(
    tmp_path: Path,
    clock,
    elapsed: float,
    budget_args: list[str],
    expected_message: str,
) -> None:
    clock(elapsed)
    result, out_p = _cli_compare(tmp_path, budget_args)
    assert result.exit_code == 5, result.output
    doc = json.loads(out_p.read_text(encoding="utf-8"))
    assert doc["run_outcome"]["operational"] == "budget_overflow"
    assert "No verdict was produced" in result.output
    assert "--budget 4h" in result.output
    assert expected_message in result.output


@pytest.mark.parametrize(
    ("elapsed", "budget_args"),
    [
        (TWO_HOURS - 1, []),  # the default expires at 2 h, not before
        (TWO_HOURS + 1, ["--budget", "3h"]),  # a stated budget overrides it
    ],
)
def test_cli_compare_within_budget(
    tmp_path: Path, clock, elapsed: float, budget_args: list[str]
) -> None:
    clock(elapsed)
    result, out_p = _cli_compare(tmp_path, budget_args)
    assert result.exit_code != 5, result.output
    doc = json.loads(out_p.read_text(encoding="utf-8"))
    # A finished comparison: a verdict, and no budget-overflow abort.
    assert doc["verdict"] == "NO_CHANGE"
    assert doc["exit"]["budget_overflow_contribution"] == 0


@pytest.mark.parametrize(
    ("elapsed", "budget_s", "expect_overflow"),
    [
        (TWO_HOURS + 1, None, True),
        (TWO_HOURS - 1, None, False),
        (TWO_HOURS + 1, 3 * 3600.0, False),
        (3 * 3600.0 + 1, 3 * 3600.0, True),
    ],
)
def test_typed_api_budget(
    tmp_path: Path,
    clock,
    elapsed: float,
    budget_s: float | None,
    expect_overflow: bool,
) -> None:
    old_p, new_p = _write_snapshots(tmp_path)
    request = CompareRequest(
        old=InputSpec.of(old_p), new=InputSpec.of(new_p), budget_s=budget_s
    )
    clock(elapsed)
    if not expect_overflow:
        result = run_compare_request(request)
        assert result.diff.verdict == Verdict.NO_CHANGE
        return
    with pytest.raises(deadline.DeadlineExceeded) as info:
        run_compare_request(request)
    notes = "\n".join(getattr(info.value, "__notes__", []))
    assert "CompareRequest.budget_s" in notes
    assert ("default 2h" in notes) is (budget_s is None)


def test_cli_release_compare_accepts_and_enforces_a_budget(tmp_path: Path) -> None:
    """A directory compare takes --budget now (it was refused) and aborts
    the whole run on overflow -- exit 5, never a per-member ERROR row read as
    a finished release."""
    old_p, new_p = _write_snapshots(tmp_path)
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    for d, src in ((old_dir, old_p), (new_dir, new_p)):
        d.mkdir()
        (d / "libtest.abi.json").write_text(src.read_text(encoding="utf-8"))
    result = CliRunner().invoke(
        main, ["compare", str(old_dir), str(new_dir), "--budget", "0s"]
    )
    assert result.exit_code == 5, result.output
    assert "--budget '0s' exceeded" in result.output
    assert "not supported for directory" not in result.output


# -- release members: an expired budget aborts the run, not one member ---------


def _member_ctx(raises: BaseException) -> SimpleNamespace:
    def resolve_debug_info(*_a: Any) -> None:
        raise raises

    return SimpleNamespace(
        request=SimpleNamespace(contract_evaluation=False),
        reporter=SimpleNamespace(),
        severity_config=None,
        require_complete_analysis=False,
        old_map={"k": Path("old/libk.so")},
        new_map={"k": Path("new/libk.so")},
        resolve_debug_info=resolve_debug_info,
        old_debug_dir=Path("dbg"),
        new_debug_dir=None,
    )


def test_release_member_reraises_an_expired_budget() -> None:
    with pytest.raises(deadline.DeadlineExceeded):
        compare_release_member("k", _member_ctx(deadline.DeadlineExceeded(-1.0)))


def test_release_member_still_records_its_own_failure() -> None:
    """The contrast case: any other failure stays that member's entry."""
    entry = compare_release_member("k", _member_ctx(ValueError("bad member")))
    assert entry["library"] == "libk.so"
    assert entry["verdict"] == "ERROR"


# -- the three waits that used to be unbounded ----------------------------------


class _Proc:
    exitcode = None

    def __init__(self) -> None:
        self.terminated = False

    def is_alive(self) -> bool:
        return not self.terminated

    def terminate(self) -> None:
        self.terminated = True


class _SilentPipe:
    """A child's pipe that never answers; its reads go through ``recv_bounded``."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def silent_reads(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, float]]:
    """Every bounded read times out at once, recording ``(conn, bound)``.

    Uses the real :func:`run_deadline.wait_bound`/``wait_expired``, so the
    recorded bound is what the read would have waited. ``recv_bounded`` itself
    is tested against a real forked child below.
    """
    reads: list[tuple[Any, float]] = []

    def expire(conn: Any, waiting_for: str) -> Any:
        reads.append((conn, run_deadline.wait_bound(waiting_for)))
        raise run_deadline.wait_expired(waiting_for)

    monkeypatch.setattr(run_deadline, "recv_bounded", expire)
    monkeypatch.setattr(keyed_process_pool, "recv_bounded", expire)
    return reads


@pytest.mark.parametrize(("scope", "expected_bound"), [(None, TWO_HOURS), (90.0, 90.0)])
def test_side_isolation_read_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    frozen: None,
    silent_reads: list[tuple[Any, float]],
    scope: float | None,
    expected_bound: float,
) -> None:
    proc, pipe = _Proc(), _SilentPipe()
    monkeypatch.setattr(side_isolation, "fork_child", lambda _ctx, _fn: (proc, pipe))
    reaped: list[Any] = []
    monkeypatch.setattr(
        side_isolation, "reap_child", lambda p, escalate: reaped.append((p, escalate))
    )
    with (
        deadline.deadline_scope(scope),
        pytest.raises(deadline.DeadlineExceeded) as info,
    ):
        side_isolation._run_children(object(), [lambda: None])
    assert silent_reads == [(pipe, expected_bound)]
    assert info.value.waiting_for == "a side-resolution child process"
    # The silent child is stopped, its pipe closed and it is reaped escalated.
    assert proc.terminated and pipe.closed and reaped == [(proc, True)]


@pytest.mark.parametrize(("scope", "expected_bound"), [(None, TWO_HOURS), (45.0, 45.0)])
def test_release_member_pool_wait_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    frozen: None,
    scope: float | None,
    expected_bound: float,
) -> None:
    procs: list[_Proc] = []

    def fork(_ctx: Any, _fn: Any, *, daemon: bool) -> tuple[_Proc, _SilentPipe]:
        procs.append(_Proc())
        return procs[-1], _SilentPipe()

    waits: list[float | None] = []

    def wait_ready(conns: list[Any], timeout: float | None = None) -> list[Any]:
        waits.append(timeout)
        return []  # nobody answers before the timeout

    monkeypatch.setattr(keyed_process_pool, "fork_child", fork)
    monkeypatch.setattr(keyed_process_pool, "wait_ready", wait_ready)
    monkeypatch.setattr(keyed_process_pool, "reap_child", lambda p, escalate: None)
    with (
        deadline.deadline_scope(scope),
        pytest.raises(deadline.DeadlineExceeded) as info,
    ):
        keyed_process_pool.run_keyed_in_processes(
            ["a", "b"], lambda k: k, max_workers=2, on_error=lambda e, k: k
        )
    assert waits == [expected_bound]
    assert info.value.waiting_for == "a release member process"
    assert len(procs) == 2 and all(p.terminated for p in procs)


def test_release_member_pool_read_after_ready_is_bounded(
    frozen: None, silent_reads: list[tuple[Any, float]]
) -> None:
    """A pipe reported ready can hold a partial frame from a child that hung
    mid-send; the read is bounded too, and its expiry is the run's, not a
    member transfer failure."""
    pipe = _SilentPipe()
    with deadline.deadline_scope(30.0), pytest.raises(deadline.DeadlineExceeded):
        keyed_process_pool._receive(pipe, _Proc())
    assert silent_reads == [(pipe, 30.0)]


class _ExpiringCondition(threading.Condition):
    """A wait that times out, with the clock moved to the deadline meanwhile."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, now_after: float) -> None:
        super().__init__()
        self.timeouts: list[float | None] = []
        self._mp, self._after = monkeypatch, now_after

    def wait(self, timeout: float | None = None) -> bool:
        self.timeouts.append(timeout)
        self._mp.setattr(deadline, "_clock", lambda: self._after)
        return False


@pytest.mark.parametrize(
    ("scope", "expected_bound"), [(None, TWO_HOURS), (120.0, 120.0)]
)
def test_release_admission_wait_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    frozen: None,
    scope: float | None,
    expected_bound: float,
) -> None:
    admission = MemoryAdmission(1.0, default_cost_gib=0.8, floor_gib=0.1)
    assert admission.try_reserve() == 0.8  # a member that never settles
    cond = _ExpiringCondition(monkeypatch, now_after=expected_bound + 1)
    admission._cond = cond
    with (
        deadline.deadline_scope(scope),
        pytest.raises(deadline.DeadlineExceeded) as info,
    ):
        with admission.admit():
            pytest.fail("admitted past a full budget")
    assert cond.timeouts == [expected_bound]
    assert info.value.waiting_for.startswith("memory admission")


def test_an_already_expired_deadline_waits_on_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """recv_bounded starts no read once the deadline has passed."""
    monkeypatch.setattr(deadline, "_clock", FakeClock(later=11.0))

    class _NeverRead:
        def recv(self) -> Any:  # pragma: no cover - reaching it is the bug
            raise AssertionError("read started after the deadline passed")

    with deadline.deadline_scope(10.0), pytest.raises(deadline.DeadlineExceeded):
        run_deadline.recv_bounded(_NeverRead(), "a child")


def test_release_member_pool_stops_a_member_whose_read_expires(
    monkeypatch: pytest.MonkeyPatch,
    frozen: None,
    silent_reads: list[tuple[Any, float]],
) -> None:
    """A member already taken off the running set (its pipe was ready) is
    still stopped when its read expires."""
    proc, pipe = _Proc(), _SilentPipe()
    monkeypatch.setattr(
        keyed_process_pool, "fork_child", lambda _c, _f, *, daemon: (proc, pipe)
    )
    monkeypatch.setattr(
        keyed_process_pool, "wait_ready", lambda conns, timeout=None: list(conns)
    )
    reaped: list[Any] = []
    monkeypatch.setattr(
        keyed_process_pool, "reap_child", lambda p, escalate: reaped.append(escalate)
    )
    with deadline.deadline_scope(20.0), pytest.raises(deadline.DeadlineExceeded):
        keyed_process_pool.run_keyed_in_processes(
            ["a"], lambda k: k, max_workers=1, on_error=lambda e, k: k
        )
    assert silent_reads == [(pipe, 20.0)]
    assert proc.terminated and pipe.closed and reaped == [True]


@pytest.mark.parametrize("pool", ["threads", "processes"])
def test_parallel_release_aborts_on_an_expired_budget(
    monkeypatch: pytest.MonkeyPatch, pool: str
) -> None:
    """Both fan-outs hand a member's DeadlineExceeded to the same on_error,
    which re-raises it, while any other member failure stays that member's
    ERROR entry."""
    from abicheck.workflows import release_member_processes, release_members

    def member(key: str, _ctx: Any) -> dict[str, object]:
        if key == "late":
            raise deadline.DeadlineExceeded(-1.0)
        raise ValueError(f"{key} failed on its own")

    def fake_processes(keys, ctx, compare, *, max_workers, admission, on_error):
        # The process path's contract: each failure reaches on_error in order.
        entries = []
        for key in keys:
            try:
                entries.append(compare(key, ctx))
            except Exception as exc:  # noqa: BLE001 -- what the pool carries back
                entries.append(on_error(exc, key))
        return entries

    monkeypatch.setattr(release_members, "compare_release_member", member)
    monkeypatch.setattr(
        release_member_processes,
        "member_processes_apply",
        lambda _n: pool == "processes",
    )
    monkeypatch.setattr(
        release_member_processes, "compare_members_in_processes", fake_processes
    )
    notices: list[str] = []
    ctx = SimpleNamespace(
        old_map={"own": Path("old/libown.so"), "late": Path("old/liblate.so")},
        reporter=SimpleNamespace(notice=notices.append),
    )

    entries = release_members._compare_parallel(["own"], ctx, max_workers=2)
    assert [(e["library"], e["verdict"]) for e in entries] == [("libown.so", "ERROR")]
    assert notices == ["Error comparing libown.so: ValueError: own failed on its own"]

    with pytest.raises(deadline.DeadlineExceeded):
        release_members._compare_parallel(["own", "late"], ctx, max_workers=2)


def test_no_baseline_audit_exits_5_naming_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """compare --no-baseline runs under the 2h default and reports its expiry
    as exit 5 with an actionable message, never a clean audit."""
    from abicheck.frontends.cli.commands import compare_no_baseline

    def expired(*_a: Any, **_k: Any) -> None:
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(compare_no_baseline, "_run_no_baseline_compare_cmd", expired)
    candidate, _ = _write_snapshots(tmp_path)
    result = CliRunner().invoke(main, ["compare", "--no-baseline", str(candidate)])
    assert result.exit_code == 5, result.output
    assert "the default 2h budget was exceeded while auditing" in result.output
    assert "No verdict was produced" in result.output


def _stored_facts_and_new_dir(tmp_path: Path) -> tuple[Path, Path]:
    """A stored BundleFacts OLD side (captured by the CLI) and a live NEW dir."""
    from abicheck.elf_metadata import ElfMetadata

    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    for d, version in ((old_dir, "1"), (new_dir, "2")):
        d.mkdir()
        snap = AbiSnapshot(
            library="libm1.so",
            version=version,
            functions=[
                Function(
                    name="f",
                    mangled="f",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
            elf=ElfMetadata(soname="libm1.so"),
        )
        (d / "libm1.so").write_text(snapshot_to_json(snap), encoding="utf-8")
    facts = tmp_path / "old.bundle-facts.json"
    CliRunner().invoke(
        main,
        ["compare", str(old_dir), str(old_dir), "--bundle-facts-out", str(facts)],
    )
    assert facts.is_file()
    return facts, new_dir


@pytest.mark.parametrize(
    ("budget_args", "expected_remaining", "expected_message"),
    [
        ([], TWO_HOURS, "the default 2h budget exceeded"),
        (["--budget", "3h"], 3 * 3600.0, "--budget '3h' exceeded"),
    ],
)
def test_stored_bundle_facts_compare_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frozen: None,
    budget_args: list[str],
    expected_remaining: float,
    expected_message: str,
) -> None:
    """The stored-BundleFacts route never reaches run_compare; it enters the
    same budget scope itself and reports expiry as exit 5."""
    from abicheck.frontends.cli.commands import compare_bundle_facts

    facts, new_dir = _stored_facts_and_new_dir(tmp_path)
    seen: list[float | None] = []

    def expire(_request: Any) -> None:
        seen.append(deadline.remaining())
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(compare_bundle_facts, "execute_bundle_facts_compare", expire)
    result = CliRunner().invoke(
        main, ["compare", str(facts), str(new_dir), *budget_args]
    )
    assert seen == [expected_remaining]
    assert result.exit_code == 5, result.output
    assert expected_message in result.output
    assert "No verdict was produced" in result.output


# -- a real child that stops mid-frame -------------------------------------------

_READER = "abicheck-child-recv"


def _start_partial_frame_child() -> tuple[Any, Any]:
    """Fork a child that writes a length header and part of its frame, then
    stalls: poll() then reports the pipe ready, but the frame never completes."""
    ctx = multiprocessing.get_context("fork")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_write_partial_frame, args=(send.fileno(),), daemon=True)
    proc.start()
    send.close()
    return proc, recv


def _write_partial_frame(fd: int) -> None:
    import os
    import struct
    import time

    os.write(fd, struct.pack("!i", 1 << 20) + b"x" * 16)
    time.sleep(120)  # alive, holding its write end open, never finishing


def _join_readers() -> list[threading.Thread]:
    readers = [t for t in threading.enumerate() if t.name == _READER]
    for t in readers:
        t.join(10)
    return [t for t in readers if t.is_alive()]


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(), reason="needs fork"
)
def test_recv_bounded_stops_on_a_real_partial_frame() -> None:
    proc, recv = _start_partial_frame_child()
    try:
        assert recv.poll(10), "the header never arrived"  # poll alone says ready
        with (
            deadline.deadline_scope(0.5),
            pytest.raises(deadline.DeadlineExceeded) as info,
        ):
            run_deadline.recv_bounded(recv, "a stalled test child")
        assert info.value.waiting_for == "a stalled test child"
    finally:
        proc.terminate()
        proc.join(10)
    # The child's death closes its write end: the abandoned reader ends on EOF.
    assert not proc.is_alive()
    assert _join_readers() == []
    recv.close()


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(), reason="needs fork"
)
def test_side_isolation_reaps_a_real_child_stalled_mid_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[Any] = []

    def fork(_ctx: Any, _fn: Any) -> tuple[Any, Any]:
        proc, recv = _start_partial_frame_child()
        started.append(proc)
        return proc, recv

    monkeypatch.setattr(side_isolation, "fork_child", fork)
    with (
        deadline.deadline_scope(0.5),
        pytest.raises(deadline.DeadlineExceeded) as info,
    ):
        side_isolation._run_children(object(), [lambda: None])
    assert info.value.waiting_for == "a side-resolution child process"
    (proc,) = started
    assert not proc.is_alive() and proc.exitcode is not None  # terminated and reaped
    assert _join_readers() == []


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("value", ("ok", 42)), ("eof", EOFError)],
)
def test_recv_bounded_passes_answers_and_errors_through(
    outcome: str, expected: Any
) -> None:
    recv, send = multiprocessing.Pipe(duplex=False)
    if outcome == "value":
        send.send(("ok", 42))
    send.close()
    with deadline.deadline_scope(30.0):
        if expected is EOFError:
            with pytest.raises(EOFError):
                run_deadline.recv_bounded(recv, "a child")
        else:
            assert run_deadline.recv_bounded(recv, "a child") == expected
    recv.close()
