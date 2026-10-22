# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The bodies the release member fan-out runs inside forked children, run
here in-process: a forked child's lines report no coverage, and its failure
paths (a dead pipe, an undecodable result, an ``on_error`` that raises) are
contracts of their own."""

from __future__ import annotations

import dataclasses
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.storage.ast_size_observer import observe_ast_sizes, report_ast_size
from abicheck.workflows import (
    keyed_process_pool as kpp,
    release_member_processes as rmp,
    side_isolation,
)
from abicheck.workflows.keyed_thread_pool import run_keyed_in_threads
from abicheck.workflows.release_admission import MemoryAdmission

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="forks; Linux only"
)


@pytest.mark.parametrize("sizes", [[], [7], [1, 2, 3]])
def test_child_body_returns_value_and_reported_ast_bytes(sizes):
    def fn(key: str) -> str:
        for n in sizes:
            report_ast_size(n)
        return key.upper()

    assert kpp._child_body(fn, "lib")() == ("LIB", sum(sizes))


@pytest.mark.parametrize("sizes", [[], [5, 6]])
def test_with_ast_sizes_returns_value_and_bytes(sizes):
    def fn() -> int:
        for n in sizes:
            report_ast_size(n)
        return 42

    outer: list[int] = []
    with observe_ast_sizes(outer.append):
        assert side_isolation._with_ast_sizes(fn)() == (42, sum(sizes))
    # The child-side sink captured them; the parent re-reports explicitly.
    assert outer == []


class _Pipe:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.closed = False

    def recv(self) -> object:
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def close(self) -> None:
        self.closed = True


class _Proc:
    exitcode = 9

    def __init__(self) -> None:
        self.joined = 0

    def join(self, timeout: float | None = None) -> None:
        self.joined += 1

    def is_alive(self) -> bool:
        return False


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (("ok", ("v", 3)), ("ok", ("v", 3))),
        (EOFError(), "status 9"),
        (ValueError("bad pickle"), "transfer failed"),
    ],
)
def test_receive_maps_every_pipe_outcome(outcome, expected):
    proc = _Proc()
    status, value = kpp._receive(_Pipe(outcome), proc)
    if isinstance(expected, tuple):
        assert (status, value) == expected
    else:
        assert status == "err" and isinstance(value, SnapshotError)
        assert expected in str(value)


@pytest.mark.parametrize(
    ("outcome", "result"),
    [
        (("ok", ("v", 4)), ("v", 4)),
        (("err", KeyError("k")), ("KeyError", 0)),
        (("err", "not an exception"), ("SnapshotError", 0)),
    ],
)
def test_settle_returns_value_or_on_error(monkeypatch, outcome, result):
    reaped: list[bool] = []
    monkeypatch.setattr(kpp, "reap_child", lambda _p, escalate: reaped.append(escalate))
    pipe = _Pipe(outcome)
    got = kpp._settle(pipe, _Proc(), "m", lambda exc, _k: type(exc).__name__)
    assert got == result
    assert pipe.closed and reaped == [outcome[0] != "ok"]


@linux_only
def test_raising_on_error_stops_running_children_and_returns_budget():
    admission = MemoryAdmission(100.0, default_cost_gib=1.0, floor_gib=0.5)

    def fn(key: str) -> str:
        if key == "boom":
            raise RuntimeError(key)
        time.sleep(30)
        return key

    def on_error(exc: Exception, key: str) -> str:
        raise KeyboardInterrupt(key)

    start = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        kpp.run_keyed_in_processes(
            ["boom", "slow1", "slow2"],
            fn,
            max_workers=3,
            on_error=on_error,
            admission=admission,
        )
    assert time.monotonic() - start < 25  # children were terminated
    assert not multiprocessing.active_children()
    # The still-running members' reservations came back (the raising
    # member's own was popped before its on_error raised).
    assert admission._in_flight <= 1


# --- release_member_processes, with the process fan-out run on threads ---


class _Inner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.texts: list[tuple[str, str]] = []

    def notice(self, text: str) -> None:
        self.texts.append(("notice", text))

    def warning(self, text: str) -> None:
        self.texts.append(("warning", text))

    def project(self, entry, member):
        self.calls.append(("project", (entry, member)))

    def reproject(self, entry, result, *, scope_changed):
        self.calls.append(("reproject", (entry, result, scope_changed)))

    def persist(self, entry, result):
        self.calls.append(("persist", (entry, result)))

    def persist_not_comparable(
        self, old_path, kind, reason, *, old_version, new_version
    ):
        self.calls.append(
            (
                "persist_not_comparable",
                (old_path, kind, reason, old_version, new_version),
            )
        )

    def settle(self, entry):
        self.calls.append(("settle", (entry,)))


def test_buffered_reporter_holds_text_and_forwards_everything_else():
    inner = _Inner()
    buf = rmp._BufferedTextReporter(inner)  # type: ignore[arg-type]
    entry: dict[str, object] = {"library": "a"}
    buf.notice("n")
    buf.warning("w")
    buf.project(entry, "member")  # type: ignore[arg-type]
    buf.reproject(entry, "result", scope_changed=True)  # type: ignore[arg-type]
    buf.persist(entry, "result")  # type: ignore[arg-type]
    buf.persist_not_comparable(
        Path("o.so"), "kind", "why", old_version="1", new_version="2"
    )
    buf.settle(entry)
    assert buf.lines == [("notice", "n"), ("warning", "w")]
    assert inner.texts == []
    assert [name for name, _ in inner.calls] == [
        "project",
        "reproject",
        "persist",
        "persist_not_comparable",
        "settle",
    ]
    assert inner.calls[1][1] == (entry, "result", True)
    assert inner.calls[3][1] == (Path("o.so"), "kind", "why", "1", "2")


@dataclasses.dataclass
class _Request:
    policy_file_path: Path | None = None


@dataclasses.dataclass
class _Ctx:
    """The fields of ``ReleaseMemberContext`` these paths read."""

    request: _Request = dataclasses.field(default_factory=_Request)
    reporter: _Inner = dataclasses.field(default_factory=_Inner)


def _on_threads(monkeypatch):
    """Run the "process" fan-out on threads, so member bodies execute here."""

    def pool(keys, fn, *, max_workers, on_error, admission=None):
        return run_keyed_in_threads(
            keys, fn, max_workers=max_workers, on_error=on_error
        )

    monkeypatch.setattr(kpp, "run_keyed_in_processes", pool)


@pytest.mark.parametrize("workers", [1, 3])
def test_members_text_is_replayed_in_order_and_failures_recorded(monkeypatch, workers):
    _on_threads(monkeypatch)
    ctx = _Ctx()

    def compare(key: str, member_ctx) -> dict[str, object]:
        member_ctx.reporter.notice(f"n{key}")
        member_ctx.reporter.warning(f"w{key}")
        if key == "b":
            raise ValueError("member broke")
        return {"library": key, "pid": os.getpid()}

    entries = rmp.compare_members_in_processes(
        ["a", "b", "c"],
        ctx,
        compare,
        max_workers=workers,
        admission=None,
        on_error=lambda exc, key: {"library": key, "error": str(exc)},
    )
    assert [e["library"] for e in entries] == ["a", "b", "c"]
    assert entries[1] == {"library": "b", "error": "member broke"}
    assert ctx.reporter.texts == [
        (kind, f"{prefix}{k}")
        for k in "abc"
        for kind, prefix in (("notice", "n"), ("warning", "w"))
    ]


def test_member_with_neither_entry_nor_failure_is_an_error(monkeypatch):
    _on_threads(monkeypatch)
    with pytest.raises(RuntimeError, match="neither an entry nor a failure"):
        rmp.compare_members_in_processes(
            ["x"],
            _Ctx(),
            lambda _k, _c: None,  # type: ignore[arg-type,return-value]
            max_workers=1,
            admission=None,
            on_error=lambda exc, key: {"library": key},
        )


def test_process_level_failure_reaches_on_error(monkeypatch):
    def fake_pool(keys, fn, *, max_workers, on_error, admission=None):
        return [on_error(SnapshotError("child died"), k) for k in keys]

    monkeypatch.setattr(kpp, "run_keyed_in_processes", fake_pool)
    seen: list[tuple[str, str]] = []
    entries = rmp.compare_members_in_processes(
        ["p", "q"],
        _Ctx(),
        lambda _k, _c: {"never": True},
        max_workers=2,
        admission=None,
        on_error=lambda exc, key: seen.append((key, str(exc))) or {"library": key},
    )
    assert entries == [{"library": "p"}, {"library": "q"}]
    assert seen == [("p", "child died"), ("q", "child died")]


def test_policy_load_failure_is_left_to_the_members(tmp_path, monkeypatch):
    from abicheck.policy_file import dedup_validate_overrides_warnings

    bad = tmp_path / "policy.yaml"
    bad.write_text("base_policy: [not, a, policy\n")
    with dedup_validate_overrides_warnings():
        # Must not raise: the members record the failure themselves.
        assert rmp._prime_policy_warning_dedup(_Ctx(_Request(bad))) is None
    # Without a dedup scope, nothing is loaded at all.
    called: list[object] = []
    monkeypatch.setattr(
        "abicheck.workflows.compare_policy.load_suppression_and_policy",
        lambda *a, **k: called.append(a),
    )
    rmp._prime_policy_warning_dedup(_Ctx(_Request(bad)))
    rmp._prime_policy_warning_dedup(_Ctx(_Request(None)))
    assert called == []
