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

"""The central cache wrapper's own contract (design-hardening plan, Phase 4).

Stated as invariants over generated inputs, against oracles that are not the
wrapper: a cache is transparent (every answer equals the uncached function's),
reference mode neither reads nor stores and counts every bypass, a key names
its inputs, and a witness makes a changed input recompute.
"""

from __future__ import annotations

import os
import random
import threading
from pathlib import Path

import pytest

from abicheck.model.execution_cache import (
    REFERENCE_MODE_ENV_VAR,
    MemoryCache,
    RequestKey,
    cache_stats,
    clear_all_caches,
    memoized,
    memoized_property,
    path_witness,
    reference_mode,
    request_key,
)
from abicheck.model.execution_cache_scoped import (
    DiskCache,
    InstanceMemo,
    ScopedCache,
    SharedScopedCache,
)


@pytest.fixture
def ref_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    yield
    monkeypatch.delenv(REFERENCE_MODE_ENV_VAR, raising=False)


@pytest.fixture(autouse=True)
def _default_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REFERENCE_MODE_ENV_VAR, raising=False)


# ── the switch ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "on"),
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        (" yes ", True),
        ("On", True),
        ("0", False),
        ("", False),
        ("false", False),
        ("no", False),
        ("2", False),
        ("enabled", False),
    ],
)
def test_reference_mode_parses_the_environment(
    monkeypatch: pytest.MonkeyPatch, raw: str, on: bool
) -> None:
    monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, raw)
    assert reference_mode() is on


def test_reference_mode_is_read_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not reference_mode()
    monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    assert reference_mode()


def test_reference_mode_forces_the_sequential_thread_budget(ref_mode: None) -> None:
    from abicheck import process_resources

    assert process_resources.max_threads() == 1
    ex = process_resources.BudgetedExecutor(8)
    try:
        assert ex.granted_threads == 0
        assert ex.submit(threading.get_ident).result() == threading.get_ident()
    finally:
        ex.shutdown()


# ── keys ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("seed", range(20))
def test_request_key_names_its_inputs_independent_of_order(seed: int) -> None:
    rng = random.Random(seed)
    fields = {f"f{i}": rng.randint(0, 3) for i in range(rng.randint(1, 6))}
    shuffled = dict(rng.sample(sorted(fields.items()), len(fields)))
    assert request_key(**fields) == request_key(**shuffled)
    assert request_key(**fields).fields() == fields
    renamed = {f"g{k[1:]}": v for k, v in fields.items()}
    assert request_key(**fields) != request_key(**renamed)
    assert isinstance(request_key(**fields), RequestKey)


def test_caches_refuse_an_ad_hoc_tuple_key() -> None:
    cache: MemoryCache[int] = MemoryCache("test.ec.adhoc")
    with pytest.raises(TypeError):
        cache.get_or_compute(("a", 1), lambda: 1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        cache.put(("a", 1), 1)  # type: ignore[arg-type]


# ── MemoryCache: transparency, bounds, witnesses ────────────────────────────


@pytest.mark.parametrize("seed", range(25))
@pytest.mark.parametrize("reference", [False, True])
def test_memory_cache_is_transparent_and_bounded(
    monkeypatch: pytest.MonkeyPatch, seed: int, reference: bool
) -> None:
    """Every answer equals the pure function's; occupancy never exceeds the
    bound; reference mode stores nothing and bypasses every call."""
    if reference:
        monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    rng = random.Random(seed)
    bound = rng.randint(1, 6)
    cache: MemoryCache[int] = MemoryCache("test.ec.transparent", max_entries=bound)
    calls = 0

    def oracle(x: int) -> int:
        return x * x + 7

    for _ in range(200):
        x = rng.randint(0, 12)

        def compute(x: int = x) -> int:
            nonlocal calls
            calls += 1
            return oracle(x)

        assert cache.get_or_compute(request_key(x=x), compute) == oracle(x)
        assert len(cache) <= bound
    stats = cache.stats
    if reference:
        assert len(cache) == 0 and calls == 200
        assert stats.bypasses == 200 and stats.hits == stats.misses == 0
    else:
        assert stats.bypasses == 0 and stats.hits + stats.misses == 200
        assert calls == stats.misses


def test_memory_cache_witness_recomputes_on_change() -> None:
    cache: MemoryCache[str] = MemoryCache("test.ec.witness")
    state = {"v": 1}
    calls: list[int] = []

    def get() -> str:
        return cache.get_or_compute(
            request_key(k="k"),
            lambda: calls.append(state["v"]) or f"value-{state['v']}",
            witness=lambda: state["v"],
        )

    assert get() == "value-1" and get() == "value-1"
    state["v"] = 2
    assert get() == "value-2"
    assert calls == [1, 2] and cache.stats.stale == 1


def test_memory_cache_never_stores_an_unverifiable_witness() -> None:
    cache: MemoryCache[int] = MemoryCache("test.ec.none_witness")
    calls: list[int] = []
    for _ in range(3):
        cache.get_or_compute(
            request_key(k=1), lambda: calls.append(1) or 1, witness=lambda: None
        )
    assert len(calls) == 3 and len(cache) == 0


def test_memory_cache_still_fresh_and_witness_of() -> None:
    cache: MemoryCache[tuple[int, ...]] = MemoryCache("test.ec.still_fresh")
    current = [1, 2]
    calls = 0

    def compute() -> tuple[int, ...]:
        nonlocal calls
        calls += 1
        return tuple(current)

    def get() -> tuple[int, ...]:
        return cache.get_or_compute(
            request_key(k=0),
            compute,
            witness_of=lambda v: v,
            still_fresh=lambda stored: stored == tuple(current),
        )

    assert get() == (1, 2) and get() == (1, 2) and calls == 1
    current.append(3)
    assert get() == (1, 2, 3) and calls == 2


def test_memory_cache_keep_and_copy_out() -> None:
    cache: MemoryCache[list[int]] = MemoryCache("test.ec.keep", copy=list)
    first = cache.get_or_compute(request_key(k=1), lambda: [1])
    first.append(99)  # a caller owns what it receives
    assert cache.get_or_compute(request_key(k=1), lambda: [2]) == [1]
    cache.get_or_compute(request_key(k=2), lambda: [0], keep=lambda v: False)
    assert request_key(k=2) not in cache


def test_memory_cache_peek_and_put_honour_reference_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache: MemoryCache[str] = MemoryCache("test.ec.peek")
    cache.put(request_key(s="a"), "A")
    assert cache.peek(request_key(s="a")) == "A"
    monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    assert cache.peek(request_key(s="a"), "miss") == "miss"
    cache.put(request_key(s="b"), "B")
    monkeypatch.delenv(REFERENCE_MODE_ENV_VAR)
    assert request_key(s="b") not in cache
    assert cache.stats.bypasses == 2


# ── memoized functions ──────────────────────────────────────────────────────


def test_memoized_keys_on_the_bound_arguments() -> None:
    calls: list[tuple[int, int]] = []

    @memoized
    def f(a: int, b: int = 2) -> int:
        calls.append((a, b))
        return a * 10 + b

    assert f(1) == f(1, 2) == f(a=1) == f(1, b=2) == f(b=2, a=1) == 12
    assert calls == [(1, 2)]
    assert f(1, 3) == 13 and calls[-1] == (1, 3)
    assert f.__wrapped__(4) == 42  # type: ignore[attr-defined]


def test_memoized_keyword_only_defaults_join_the_positional_call() -> None:
    """``demangle(sym)`` and ``demangle(sym, accept_macho_prefix=False)`` are
    one request; the fast path must build the same key binding would."""
    calls: list[tuple[str, bool]] = []

    @memoized
    def f(s: str, *, flag: bool = False) -> str:
        calls.append((s, flag))
        return f"{s}:{flag}"

    assert f("a") == f("a", flag=False) == f(s="a") == "a:False"
    assert f("a", flag=True) == "a:True"
    assert calls == [("a", False), ("a", True)]


def test_memory_cache_single_field_and_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    cache: MemoryCache[str] = MemoryCache("test.ec.field", field="name")
    cache.put("k", "v")
    read = cache.reader("miss")
    assert cache.peek("k") == "v" and read("k") == "v" and read("x") == "miss"
    assert cache.raw_get()("k")[0] == "v"
    monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    assert read("k") == "miss" and cache.peek("k", "miss") == "miss"


@pytest.mark.parametrize("seed", range(10))
def test_memoized_is_transparent_under_both_modes_and_lru_bound(
    monkeypatch: pytest.MonkeyPatch, seed: int
) -> None:
    rng = random.Random(seed)

    @memoized(maxsize=3)
    def f(x: int) -> int:
        return x * 3

    for i in range(150):
        if i == 75:
            monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
        x = rng.randint(0, 9)
        assert f(x) == x * 3
        assert f.cache_len() <= 3  # type: ignore[attr-defined]
    stats = f.stats  # type: ignore[attr-defined]
    assert stats.bypasses == 75 and stats.hits + stats.misses == 75


def test_memoized_witness_reprobes_a_replaced_executable(tmp_path: Path) -> None:
    tool = tmp_path / "tool"
    tool.write_text("v1")
    probes: list[str] = []

    @memoized(witness=path_witness)
    def version(path: str) -> str:
        probes.append(path)
        return Path(path).read_text()

    assert version(str(tool)) == "v1" and version(str(tool)) == "v1"
    tool.write_text("v2-longer")  # same path, different executable
    assert version(str(tool)) == "v2-longer"
    assert len(probes) == 2


def test_path_witness_resolves_bare_names_and_tolerates_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert path_witness(str(missing)) == ((str(missing), -1, -1, -1, -1),)
    missing.write_text("x")
    assert path_witness(str(missing))[0][1:] != (-1, -1, -1, -1)
    assert path_witness(None) == ()


def test_memoized_property_bypasses_in_reference_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class C:
        def __init__(self) -> None:
            self.n = 0

        @memoized_property
        def p(self) -> int:
            self.n += 1
            return self.n

    c = C()
    assert c.p == 1 and c.p == 1
    monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    d = C()
    assert d.p == 1 and d.p == 2 and "p" not in d.__dict__


# ── request-scoped, shared and instance memos ───────────────────────────────


def test_scoped_cache_is_inert_outside_a_scope_and_joins_nested_ones() -> None:
    cache = ScopedCache("test.ec.scoped")
    calls = 0

    def compute() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert cache.get_or_compute(request_key(k=1), compute) == 1
    assert cache.get_or_compute(request_key(k=1), compute) == 2
    with cache.scope():
        assert cache.get_or_compute(request_key(k=1), compute) == 3
        with cache.scope():
            assert cache.get_or_compute(request_key(k=1), compute) == 3
    assert not cache.active()


class _FailingOwners(dict):  # type: ignore[type-arg]
    """An in-flight table whose owner fails *n* times in a row, then is gone."""

    def __init__(self, n: int, exc: BaseException) -> None:
        super().__init__()
        self.n, self.exc = n, exc

    def get(self, key, default=None):  # type: ignore[no-untyped-def]
        if self.n <= 0:
            return default
        self.n -= 1
        from concurrent.futures import Future

        failed: Future[int] = Future()
        failed.set_exception(self.exc)
        return failed


@pytest.mark.parametrize("failures", [1, 3, 5000])
def test_waiter_retries_failed_owners_iteratively(failures: int) -> None:

    cache: MemoryCache[int] = MemoryCache("test.ec.retry_loop")
    cache._in_flight = _FailingOwners(failures, ValueError("owner failed"))
    assert cache.get_or_compute(request_key(k=1), lambda: 7) == 7
    assert cache._in_flight.n == 0


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)])
def test_waiter_does_not_retry_a_base_exception(exc: BaseException) -> None:
    cache: MemoryCache[int] = MemoryCache("test.ec.retry_base")
    cache._in_flight = _FailingOwners(1, exc)
    calls: list[int] = []
    with pytest.raises(type(exc)):
        cache.get_or_compute(request_key(k=1), lambda: calls.append(1) or 7)
    assert calls == []


def test_scoped_cache_pin_is_identity_not_equality() -> None:
    cache = ScopedCache("test.ec.pin")
    a, b = [1], [1]
    with cache.scope():
        assert cache.get_or_compute(request_key(k=0), lambda: "a", pin=a) == "a"
        assert cache.get_or_compute(request_key(k=0), lambda: "b", pin=b) == "b"
        assert cache.get_or_compute(request_key(k=0), lambda: "x", pin=b) == "b"


def test_scoped_cache_bypasses_in_reference_mode(ref_mode: None) -> None:
    cache = ScopedCache("test.ec.scoped_ref")
    with cache.scope():
        values = [cache.get_or_compute(request_key(k=1), object) for _ in range(3)]
    assert len({id(v) for v in values}) == 3 and cache.stats.bypasses == 3


def test_shared_scoped_cache_is_shared_across_threads_and_dropped() -> None:
    cache = SharedScopedCache("test.ec.shared")
    seen: list[object] = []
    with cache.scope():
        first = cache.get_or_compute(request_key(k=1), object)
        t = threading.Thread(
            target=lambda: seen.append(cache.get_or_compute(request_key(k=1), object))
        )
        t.start()
        t.join()
    assert seen == [first]
    assert not cache.active()


def test_instance_memo_lives_on_the_object_and_honours_the_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memo = InstanceMemo("test.ec.instance", "_test_memo")

    class Obj:
        pass

    o, pin = Obj(), object()
    assert memo.get_or_compute(o, request_key(k=1), lambda: 1, pin=pin) == 1
    assert memo.get_or_compute(o, request_key(k=1), lambda: 2, pin=pin) == 1
    assert memo.get_or_compute(o, request_key(k=1), lambda: 3, pin=object()) == 3
    memo.drop(o)
    assert "_test_memo" not in o.__dict__
    monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    assert memo.get_or_compute(o, request_key(k=1), lambda: 4, pin=pin) == 4
    assert "_test_memo" not in o.__dict__


# ── disk policy and registry ────────────────────────────────────────────────


def test_disk_cache_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    disk = DiskCache("test.ec.disk")
    writes: list[int] = []
    assert disk.lookup(lambda: "hit") == "hit" and disk.lookup(lambda: None) is None
    disk.store(lambda: writes.append(1))
    assert disk.permits()
    monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    assert disk.lookup(lambda: "hit") is None
    disk.store(lambda: writes.append(2))
    assert not disk.permits()
    disk.record(hit=True)
    assert writes == [1]
    assert (disk.stats.hits, disk.stats.misses, disk.stats.stores) == (1, 1, 1)
    assert disk.stats.bypasses == 4


def test_registry_reports_and_clears_every_cache() -> None:
    cache: MemoryCache[int] = MemoryCache("test.ec.registry")
    cache.get_or_compute(request_key(k=1), lambda: 1)
    cache.get_or_compute(request_key(k=1), lambda: 1)
    assert cache_stats()["test.ec.registry"]["hits"] == 1
    clear_all_caches()
    assert len(cache) == 0


def test_every_production_cache_is_registered() -> None:
    """Importing the package registers the inventoried caches by name."""
    import abicheck.cli  # noqa: F401 - imports the whole command surface

    names = set(cache_stats())
    for expected in (
        "abicheck.snapshot_cache.disk",
        "abicheck.storage.header_ast_cache.ast_disk",
        "abicheck.name_classification.canonicalize_type_name",
        "abicheck.model.comparison_memo",
    ):
        assert expected in names, expected
    assert os.environ.get(REFERENCE_MODE_ENV_VAR) is None


def test_memoized_property_value_is_reached_through_the_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored value must not shadow the descriptor: hits are counted, and a
    value stored before reference mode is switched on is not served after."""

    class C:
        def __init__(self) -> None:
            self.n = 0

        @memoized_property
        def p(self) -> int:
            self.n += 1
            return self.n

    c = C()
    assert c.p == 1 and c.p == 1
    assert C.p.stats.hits >= 1  # type: ignore[attr-defined]
    monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    assert c.p == 2  # recomputed despite the stored value
