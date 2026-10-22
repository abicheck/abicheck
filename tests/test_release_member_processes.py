# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The release member fan-out over forked processes returns what the thread
fan-out returns: results by key, failures recorded per member, terminal text
and warning dedup preserved -- and it is only chosen where it is safe."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.workflows import release_member_processes as rmp, side_isolation
from abicheck.workflows.keyed_process_pool import run_keyed_in_processes
from abicheck.workflows.keyed_thread_pool import run_keyed_in_threads
from abicheck.workflows.release_admission import MemoryAdmission
from abicheck.workflows.release_members import (
    ReleaseMemberContext,
    _compare_parallel,
    release_parent_request,
)

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="member processes fork; Linux only"
)


class _MemberFailed(Exception):
    pass


def _fn(key: str) -> dict[str, object]:
    """A member body with every outcome a real one can have, by key."""
    if key == "raises":
        raise _MemberFailed(key)
    if key == "dies":
        os._exit(7)
    if key == "unpicklable":
        return {"lock": threading.Lock()}
    if key.startswith("slow"):
        time.sleep(0.05 * int(key[-1]))
    return {"key": key, "payload": list(range(len(key)))}


def _on_error(exc: Exception, key: str) -> dict[str, object]:
    return {"key": key, "error": type(exc).__name__, "text": str(exc)}


@linux_only
@pytest.mark.parametrize("workers", [1, 2, 4])
def test_processes_return_what_threads_return_in_key_order(workers):
    # Completion order is the reverse of key order (slow3 finishes last).
    keys = ["slow3", "a", "raises", "slow1", "bb", "slow2"]
    via_threads = run_keyed_in_threads(
        keys, _fn, max_workers=workers, on_error=_on_error
    )
    via_processes = run_keyed_in_processes(
        keys, _fn, max_workers=workers, on_error=_on_error
    )
    assert via_processes == via_threads
    assert [r["key"] for r in via_processes] == keys
    assert via_processes[2] == {
        "key": "raises",
        "error": "_MemberFailed",
        "text": "raises",
    }


@linux_only
@pytest.mark.parametrize("bad", ["raises", "dies", "unpicklable"])
def test_one_failing_member_is_recorded_and_siblings_complete(bad):
    keys = ["a", bad, "ccc"]
    out = run_keyed_in_processes(keys, _fn, max_workers=3, on_error=_on_error)
    assert out[0] == {"key": "a", "payload": [0]}
    assert out[2] == {"key": "ccc", "payload": [0, 1, 2]}
    expected = "_MemberFailed" if bad == "raises" else SnapshotError.__name__
    assert out[1]["key"] == bad and out[1]["error"] == expected
    if bad == "dies":
        assert "status 7" in str(out[1]["text"])


@linux_only
def test_members_run_in_children_never_in_the_parent():
    out = run_keyed_in_processes(
        ["x", "y"], lambda _k: os.getpid(), max_workers=2, on_error=_on_error
    )
    assert os.getpid() not in out and len(set(out)) == 2


@linux_only
def test_admission_serializes_members_and_learns_from_child_ast_sizes():
    from abicheck.storage.ast_size_observer import report_ast_size

    # Budget for exactly one member at a time.
    admission = MemoryAdmission(1.0, default_cost_gib=1.0, floor_gib=0.5)

    def body(key: str) -> tuple[float, float]:
        start = time.monotonic()
        report_ast_size(3 << 30)  # 3 GiB of AST -> cost 0.5 + 2*3 = 6.5 GiB
        time.sleep(0.05)
        return start, time.monotonic()

    spans = run_keyed_in_processes(
        ["a", "b", "c"], body, max_workers=3, on_error=_on_error, admission=admission
    )
    ordered = sorted(spans)
    assert all(ordered[i][1] <= ordered[i + 1][0] for i in range(len(ordered) - 1))
    assert admission.estimate_gib() == pytest.approx(6.5)
    # Every reservation was returned: nothing in flight blocks the next one.
    assert admission.try_reserve() == pytest.approx(6.5)


def test_try_reserve_never_blocks_and_always_admits_the_first():
    admission = MemoryAdmission(1.0, default_cost_gib=2.0, floor_gib=0.5)
    first = admission.try_reserve()
    assert first == 2.0  # over budget, but nothing is in flight
    assert admission.try_reserve() is None
    admission.release(first, 0)
    assert admission.try_reserve() == 2.0


@pytest.mark.parametrize(
    ("pool", "env", "gil", "safe", "expected"),
    [
        (2, None, True, True, True),
        (1, None, True, True, False),  # -j1: sequential in-process
        (2, "0", True, True, False),  # operator opt-out
        (2, None, False, True, False),  # free-threaded: threads scale
        (2, None, True, False, False),  # other threads / no fork
    ],
)
def test_process_fan_out_is_chosen_only_where_it_is_safe(
    monkeypatch, pool, env, gil, safe, expected
):
    import abicheck.process_resources as pr

    if env is None:
        monkeypatch.delenv("ABICHECK_MEMBER_PROCESSES", raising=False)
    else:
        monkeypatch.setenv("ABICHECK_MEMBER_PROCESSES", env)
    monkeypatch.setattr(pr, "gil_enabled", lambda: gil)
    monkeypatch.setattr(side_isolation, "fork_is_safe", lambda: safe)
    assert rmp.member_processes_apply(pool) is expected


def test_fork_is_unsafe_while_another_thread_runs(monkeypatch):
    monkeypatch.setattr(side_isolation.sys, "platform", "linux")
    stop = threading.Event()
    worker = threading.Thread(target=stop.wait)
    worker.start()
    try:
        assert side_isolation.fork_is_safe() is False
    finally:
        stop.set()
        worker.join()
    monkeypatch.setattr(side_isolation.sys, "platform", "darwin")
    assert side_isolation.fork_is_safe() is False


class _Recorder:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def notice(self, text: str) -> None:
        self.lines.append(("notice", text))

    def warning(self, text: str) -> None:
        self.lines.append(("warning", text))


def _ctx(tmp_path: Path, keys: tuple[str, ...], **fields) -> ReleaseMemberContext:
    from abicheck.workflows.release_compare_reporter import SilentReleaseReporter

    class _Reporter(_Recorder, SilentReleaseReporter):
        def __init__(self) -> None:
            _Recorder.__init__(self)

    return ReleaseMemberContext(
        request=release_parent_request(old_version="1", new_version="2", **fields),
        old_map={k: tmp_path / f"{k}.so" for k in keys},
        new_map={k: tmp_path / f"{k}_new.so" for k in keys},
        reporter=_Reporter(),
    )


def _force(monkeypatch, processes: bool) -> list[int]:
    """Pin the dispatch and record which path ran (a differential test must
    prove both of its configurations actually ran)."""
    ran: list[int] = []
    monkeypatch.setattr(rmp, "member_processes_apply", lambda _n: processes)
    real = rmp.compare_members_in_processes

    def spy(*args, **kwargs):
        ran.append(os.getpid())
        return real(*args, **kwargs)

    monkeypatch.setattr(rmp, "compare_members_in_processes", spy)
    return ran


@linux_only
def test_child_terminal_text_is_replayed_through_the_parent_reporter(
    tmp_path, monkeypatch
):
    def fake_member(key, ctx):
        ctx.reporter.notice(f"note {key}")
        ctx.reporter.warning(f"warn {key}")
        if key == "b":
            raise RuntimeError("escaped the worker")
        return {"library": f"{key}.so", "verdict": "NO_CHANGE", "pid": os.getpid()}

    monkeypatch.setattr(
        "abicheck.workflows.release_members.compare_release_member", fake_member
    )
    outcomes = {}
    for processes in (False, True):
        ran = _force(monkeypatch, processes)
        ctx = _ctx(tmp_path, ("a", "b", "c"))
        entries = _compare_parallel(["a", "b", "c"], ctx, 3)
        assert bool(ran) is processes
        pids = {e.pop("pid", None) for e in entries} - {None}
        assert (os.getpid() in pids) is not processes
        lines = ctx.reporter.lines  # type: ignore[attr-defined]
        outcomes[processes] = (entries, sorted(lines))
    assert outcomes[True] == outcomes[False]
    entries, lines = outcomes[True]
    assert [e["library"] for e in entries] == ["a.so", "b.so", "c.so"]
    assert entries[1]["verdict"] == "ERROR"
    assert ("notice", "note c") in lines and ("warning", "warn a") in lines
    # The member whose worker raised still had its text shown.
    assert ("notice", "note b") in lines


@linux_only
def test_policy_warning_dedup_reaches_forked_members(tmp_path, monkeypatch, caplog):
    import logging

    from abicheck.policy_file import (
        PolicyFile,
        dedup_validate_overrides_warnings,
        pending_validate_overrides_warnings,
    )

    pf = tmp_path / "policy.yaml"
    pf.write_text("base_policy: strict_abi\noverrides:\n  func_removed: ignore\n")

    def fake_member(key, _ctx):
        # What a child would still have to warn about after the fork.
        fresh = pending_validate_overrides_warnings(PolicyFile.load(pf))
        return {"library": f"{key}.so", "verdict": "NO_CHANGE", "fresh": fresh}

    monkeypatch.setattr(
        "abicheck.workflows.release_members.compare_release_member", fake_member
    )
    ran = _force(monkeypatch, True)
    ctx = _ctx(tmp_path, ("a", "b", "c"), policy_file_path=pf)
    with caplog.at_level(logging.WARNING):
        with dedup_validate_overrides_warnings():
            entries = _compare_parallel(["a", "b", "c"], ctx, 3)
    assert ran
    assert [e["fresh"] for e in entries] == [[], [], []]
    assert caplog.text.count("HIGH RISK") == 1  # shown once, by the parent


@pytest.mark.parametrize("n", [1, 3, 8])
def test_disposition_ledger_identity_survives_a_pickle_round_trip(n):
    """The ledger keys records on ``id(change)``; after a pickle (the trip a
    forked member's result takes) every change in the unpickled result must
    still find its own record and rule -- including a dedupe-key alias."""
    import pickle

    import abicheck.policy.disposition_ledger_pickle  # noqa: F401
    from abicheck.checker_policy import ChangeKind
    from abicheck.model.change import Change
    from abicheck.policy.disposition_ledger import Disposition, DispositionLedger
    from abicheck.policy.rule_provenance import RuleProvenance

    changes = [
        Change(kind=ChangeKind.FUNC_REMOVED, symbol=f"f{i}", description="x")
        for i in range(n)
    ]
    alias = Change(kind=ChangeKind.FUNC_REMOVED, symbol="f0", description="x")
    ledger = DispositionLedger()
    for i, change in enumerate(changes):
        ledger.record(
            change,
            Disposition.SUPPRESSED,
            application_point="test",
            rule=RuleProvenance(rule_id=f"r{i}"),
            dedupe_key=f"k{i}",
        )
    ledger.record(alias, Disposition.SUPPRESSED, application_point="t", dedupe_key="k0")
    copy_changes, copy_alias, copy_ledger = pickle.loads(
        pickle.dumps((changes, alias, ledger))
    )
    for i, change in enumerate(copy_changes):
        assert copy_ledger.rule_for(change) == RuleProvenance(rule_id=f"r{i}")
        assert copy_ledger.index_for(change) == ledger.index_for(changes[i])
    assert copy_ledger.rule_for(copy_alias) == RuleProvenance(rule_id="r0")
    # An unrelated, equal-valued object is still not a hit.
    stranger = Change(kind=ChangeKind.FUNC_REMOVED, symbol="f0", description="x")
    assert copy_ledger.rule_for(stranger) is None
