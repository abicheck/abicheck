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
import pickle
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
    """A release member's failure crosses a forked child's pipe pickled."""
    exc = deadline.DeadlineExceeded(-2.5, waiting_for=waiting_for)
    back = pickle.loads(pickle.dumps(exc))
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


@pytest.mark.parametrize(
    ("elapsed", "budget_args", "expect_overflow"),
    [
        (TWO_HOURS + 1, [], True),  # the default applies and expires
        (TWO_HOURS - 1, [], False),  # ... at 2 h, not before
        (TWO_HOURS + 1, ["--budget", "3h"], False),  # a stated budget overrides it
        (3 * 3600.0 + 1, ["--budget", "3h"], True),  # ... and is enforced itself
        (61.0, ["--budget", "1m"], True),  # a shorter stated budget too
    ],
)
def test_cli_compare_budget(
    tmp_path: Path,
    clock,
    elapsed: float,
    budget_args: list[str],
    expect_overflow: bool,
) -> None:
    old_p, new_p = _write_snapshots(tmp_path)
    out_p = tmp_path / "out.json"
    clock(elapsed)
    result = CliRunner().invoke(
        main,
        ["compare", str(old_p), str(new_p), "-o", f"json={out_p}", *budget_args],
    )
    if not expect_overflow:
        assert result.exit_code != 5, result.output
        return
    assert result.exit_code == 5, result.output
    doc = json.loads(out_p.read_text(encoding="utf-8"))
    assert doc["run_outcome"]["operational"] == "budget_overflow"
    assert "No verdict was produced" in result.output
    assert "--budget 4h" in result.output
    if budget_args:
        assert f"--budget {budget_args[1]!r} exceeded" in result.output
    else:
        assert "the default 2h budget exceeded" in result.output
        assert "no budget was set, so the default 2h applied" in result.output


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
        assert result.diff.verdict is not None
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
    """A child that is alive and never answers: ``poll`` always times out."""

    def __init__(self) -> None:
        self.poll_timeouts: list[float | None] = []
        self.closed = False

    def poll(self, timeout: float | None = None) -> bool:
        self.poll_timeouts.append(timeout)
        return False

    def recv(self) -> Any:  # pragma: no cover - reaching it is the bug
        raise AssertionError("read a pipe poll() said was empty")

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(("scope", "expected_bound"), [(None, TWO_HOURS), (90.0, 90.0)])
def test_side_isolation_read_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    frozen: None,
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
    assert pipe.poll_timeouts == [expected_bound]
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
    monkeypatch: pytest.MonkeyPatch, frozen: None
) -> None:
    """A pipe reported ready can hold a partial frame from a child that hung
    mid-send; the read is bounded too."""
    pipe = _SilentPipe()
    with deadline.deadline_scope(30.0), pytest.raises(deadline.DeadlineExceeded):
        keyed_process_pool._receive(pipe, _Proc())
    assert pipe.poll_timeouts == [30.0]


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
    monkeypatch.setattr(deadline, "_clock", FakeClock(later=11.0))
    pipe = _SilentPipe()
    with deadline.deadline_scope(10.0), pytest.raises(deadline.DeadlineExceeded):
        keyed_process_pool._receive(pipe, _Proc())
    assert pipe.poll_timeouts == []


def test_release_member_pool_stops_a_member_whose_read_expires(
    monkeypatch: pytest.MonkeyPatch, frozen: None
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
    assert proc.terminated and pipe.closed and reaped == [True]
