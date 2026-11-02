"""ADR-062 D11 / plan A2.3: byte-quota LRU eviction.

The oracle is an independent ``OrderedDict`` cache simulation (move-to-end on
use, pop-from-front while over quota), not ``select_victims`` itself. Random
access sequences drive both; after every operation the set of surviving
entries must agree.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.storage.byte_quota_lru import (
    CacheEntry,
    evict_directory_to_quota,
    select_victims,
)

_NAMES = st.sampled_from([f"k{i}" for i in range(8)])
_OPS = st.lists(
    st.tuples(st.sampled_from(["put", "get"]), _NAMES, st.integers(0, 50)),
    max_size=60,
)


class _ReferenceLru:
    """Textbook LRU over bytes; the just-put key is exempt for that put."""

    def __init__(self, quota: int) -> None:
        self.quota = quota
        self.items: OrderedDict[str, int] = OrderedDict()

    def get(self, key: str) -> None:
        if key in self.items:
            self.items.move_to_end(key)

    def put(self, key: str, size: int) -> None:
        self.items[key] = size
        self.items.move_to_end(key)
        others = [k for k in self.items if k != key]
        total = sum(self.items[k] for k in others)
        for k in others:  # oldest first
            if total <= self.quota:
                break
            total -= self.items.pop(k)


@settings(max_examples=300, deadline=None)
@given(ops=_OPS, quota=st.integers(0, 150))
def test_matches_reference_lru_over_random_access_sequences(ops, quota) -> None:
    ref = _ReferenceLru(quota)
    live: dict[str, CacheEntry] = {}
    clock = 0.0
    for op, key, size in ops:
        clock += 1.0
        if op == "get":
            ref.get(key)
            if key in live:
                live[key] = CacheEntry(key, live[key].size, clock)
            continue
        ref.put(key, size)
        live[key] = CacheEntry(key, size, clock)
        for victim in select_victims(live.values(), quota, protected={key}):
            del live[victim]
        assert set(live) == set(ref.items)
        unprotected = sum(e.size for k, e in live.items() if k != key)
        assert unprotected <= quota


@given(
    entries=st.lists(
        st.builds(
            CacheEntry, _NAMES, st.integers(0, 100), st.integers(0, 5).map(float)
        ),
        unique_by=lambda e: e.name,
    ),
    quota=st.integers(0, 300),
)
def test_victims_are_minimal_lru_prefix(entries, quota) -> None:
    victims = select_victims(entries, quota)
    order = sorted(entries, key=lambda e: (e.last_used, e.name))
    assert victims == [e.name for e in order[: len(victims)]]
    remaining = sum(e.size for e in order[len(victims) :])
    assert remaining <= quota
    if victims:  # one fewer victim would not have fit
        assert remaining + order[len(victims) - 1].size > quota
    # Deterministic regardless of input order.
    assert select_victims(list(reversed(entries)), quota) == victims


def test_negative_quota_rejected() -> None:
    with pytest.raises(ValueError):
        select_victims([], -1)


def _file(d: Path, name: str, size: int, age: float) -> Path:
    p = d / name
    p.write_bytes(b"x" * size)
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def test_directory_eviction_by_bytes_not_count(tmp_path: Path) -> None:
    _file(tmp_path, "old.json.zst", 100, 30)
    _file(tmp_path, "mid.json", 10, 20)
    _file(tmp_path, "new.json.zst", 10, 10)
    _file(tmp_path, "unrelated.txt", 999, 40)
    removed = evict_directory_to_quota(
        tmp_path, suffixes=(".json.zst", ".json"), quota_bytes=25
    )
    assert removed == ["old.json.zst"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "mid.json",
        "new.json.zst",
        "unrelated.txt",
    ]


def test_protected_entry_survives_even_above_quota(tmp_path: Path) -> None:
    _file(tmp_path, "big.json.zst", 500, 0)
    _file(tmp_path, "small.json.zst", 1, 100)
    evict_directory_to_quota(
        tmp_path, suffixes=(".json.zst",), quota_bytes=0, protected={"big.json.zst"}
    )
    assert [p.name for p in tmp_path.iterdir()] == ["big.json.zst"]


@pytest.mark.parametrize("age,survives", [(10.0, True), (7200.0, False)])
def test_crash_residue_swept_only_when_stale(tmp_path: Path, age, survives) -> None:
    """A write interrupted before ``os.replace`` leaves ``.<name>.<rand>.tmp``.
    A young one may be a live write in another process; an old one is debris.
    Neither is ever counted as an entry."""
    tmp = _file(tmp_path, ".k.json.zst.abc123.tmp", 10_000, age)
    _file(tmp_path, "k2.json.zst", 5, 0)
    removed = evict_directory_to_quota(
        tmp_path, suffixes=(".json.zst",), quota_bytes=100
    )
    assert removed == []
    assert tmp.exists() is survives
    assert (tmp_path / "k2.json.zst").exists()


@pytest.mark.parametrize("failing", ["a.json", "b.json", "c.json"])
def test_unlink_failure_at_every_victim_never_raises(
    tmp_path: Path, monkeypatch, failing
) -> None:
    """Enumerate an unlink failure (concurrent removal, EPERM) at each victim:
    eviction carries on with the rest and reports only what it removed."""
    for i, n in enumerate(["a.json", "b.json", "c.json"]):
        _file(tmp_path, n, 10, 30 - i)
    real_unlink = Path.unlink

    def flaky(self, *a, **k):
        if self.name == failing:
            raise OSError("injected")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", flaky)
    removed = evict_directory_to_quota(tmp_path, suffixes=(".json",), quota_bytes=0)
    assert failing not in removed
    assert set(removed) == {"a.json", "b.json", "c.json"} - {failing}


def test_missing_directory_is_noop(tmp_path: Path) -> None:
    assert (
        evict_directory_to_quota(tmp_path / "nope", suffixes=(".json",), quota_bytes=0)
        == []
    )
