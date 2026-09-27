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

"""Single-flight contract of the two header-scan memos.

A release fan-out starts every member's header scan at once. Before these
memos were single-flight, each concurrent caller missed the still-empty memo
and repeated the identical scan (MKL release profile: the C++20 header scan
was ~16% of all samples with a warm castxml cache). The contract, stated for
any number of concurrent callers and keys:

* every caller gets the value its own ``compute`` would have produced;
* each key is computed exactly once while nothing fails;
* a failing computation raises only in the thread whose ``compute`` raised,
  and never leaves a waiter blocked.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from abicheck.extract.digest_memo import DigestMemo
from abicheck.extract.header_scan_memo import memoize_header_scan


def _run_concurrently(n: int, target) -> list[object]:
    barrier = threading.Barrier(n)
    results: list[object] = [None] * n

    def run(i: int) -> None:
        barrier.wait()
        try:
            results[i] = target(i)
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            results[i] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a caller was left blocked"
    return results


@pytest.mark.parametrize(("callers", "keys"), [(2, 1), (8, 1), (8, 3), (16, 5)])
def test_digest_memo_computes_each_key_once(callers: int, keys: int) -> None:
    memo: DigestMemo[str] = DigestMemo()
    calls: dict[int, int] = {}
    gate = threading.Event()
    lock = threading.Lock()

    def compute(k: int) -> str:
        with lock:
            calls[k] = calls.get(k, 0) + 1
        gate.wait(0.2)  # hold the computation open so the others really overlap
        return f"value-{k}"

    results = _run_concurrently(
        callers, lambda i: memo.get_or_compute(i % keys, lambda: compute(i % keys))
    )
    assert results == [f"value-{i % keys}" for i in range(callers)]
    assert calls == {k: 1 for k in range(keys)}


def test_digest_memo_failure_raises_only_in_its_own_thread() -> None:
    memo: DigestMemo[str] = DigestMemo()
    started = threading.Event()
    first = threading.Lock()
    first.acquire()

    def compute(i: int) -> str:
        if first.locked() and i == 0:
            started.set()
            threading.Event().wait(0.2)
            first.release()
            raise ValueError("boom")
        return "ok"

    def call(i: int) -> str:
        if i != 0:
            started.wait(5)
        return memo.get_or_compute("k", lambda: compute(i))

    results = _run_concurrently(6, call)
    assert isinstance(results[0], ValueError)
    assert results[1:] == ["ok"] * 5
    assert memo.get_or_compute("k", lambda: "unused") == "ok"


def test_digest_memo_oversized_value_still_reaches_waiters() -> None:
    memo: DigestMemo[bytes] = DigestMemo(max_bytes=4, weigh=len)
    calls = []

    def compute() -> bytes:
        calls.append(1)
        threading.Event().wait(0.1)
        return b"x" * 10

    results = _run_concurrently(4, lambda _i: memo.get_or_compute("k", compute))
    assert results == [b"x" * 10] * 4
    assert len(memo) == 0  # never stored, but never recomputed by a waiter either
    assert len(calls) == 1


def _expand(paths: list[Path], *, unresolved: list[Path], **_kw: object) -> list[Path]:
    return list(paths)


@pytest.mark.parametrize("callers", [2, 8])
def test_header_scan_memo_scans_once_per_key(tmp_path: Path, callers: int) -> None:
    header = tmp_path / "a.h"
    header.write_text("int f(void);\n")
    scans = []

    @memoize_header_scan(_expand)
    def scan(header_paths: list[Path], *, flag: bool = False) -> list[str]:
        scans.append(flag)
        threading.Event().wait(0.2)
        return [p.read_text() + str(flag) for p in header_paths]

    results = _run_concurrently(callers, lambda i: scan([header], flag=bool(i % 2)))
    for i, r in enumerate(results):
        assert r == ["int f(void);\n" + str(bool(i % 2))]
    assert sorted(scans) == sorted({bool(i % 2) for i in range(callers)})
    # Each caller got its own list: mutating one reaches no other.
    results[0].append("mutated")  # type: ignore[union-attr]
    assert scan([header], flag=False) == ["int f(void);\nFalse"]


def test_header_scan_memo_failure_does_not_strand_waiters(tmp_path: Path) -> None:
    header = tmp_path / "a.h"
    header.write_text("x\n")
    attempts = []

    @memoize_header_scan(_expand)
    def scan(header_paths: list[Path]) -> list[str]:
        attempts.append(1)
        if len(attempts) == 1:
            threading.Event().wait(0.2)
            raise OSError("first scan fails")
        return ["ok"]

    results = _run_concurrently(5, lambda _i: scan([header]))
    assert sum(isinstance(r, OSError) for r in results) == 1
    assert [r for r in results if not isinstance(r, OSError)] == [["ok"]] * 4
