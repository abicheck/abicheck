"""Invariants for the header-AST cache byte budget."""

from __future__ import annotations

import os
import random

import pytest

from abicheck.storage import ast_cache_budget as acb, cache_integrity

NOW = 10_000_000.0


def _entry(d, name, size, age, sidecar=0):
    p = d / name
    p.write_bytes(b"x" * size)
    os.utime(p, (NOW - age, NOW - age))
    if sidecar:
        cache_integrity.sidecar_path(p).write_bytes(b"y" * sidecar)
    return p


def _total(d):
    return sum(p.stat().st_size for p in d.iterdir())


@pytest.mark.parametrize("seed", range(25))
def test_random_caches_match_an_independent_lru_oracle(tmp_path, seed):
    rng = random.Random(seed)
    rows = []  # (age, bytes incl. sidecar, path)
    for i in range(rng.randint(1, 12)):
        size, side = rng.randint(1, 500), rng.choice([0, 64])
        age = float(rng.randint(0, 3 * int(acb.MIN_AGE_SECONDS)))
        rows.append(
            (
                age,
                size + side,
                _entry(
                    tmp_path, f"k{i}{rng.choice(['.xml', '.json'])}", size, age, side
                ),
            )
        )
    budget = rng.randint(0, 3000)

    # Oracle: walk oldest-first, dropping eligible entries until under budget.
    total = sum(r[1] for r in rows)
    expected = []
    for age, size, path in sorted(rows, key=lambda r: (-r[0], str(r[2]))):
        if total <= budget:
            break
        if age >= acb.MIN_AGE_SECONDS:
            expected.append(path)
            total -= size

    evicted = acb.enforce_ast_cache_budget(tmp_path, max_bytes=budget, now=NOW)
    assert evicted == expected
    for p in evicted:
        assert not p.exists() and not cache_integrity.sidecar_path(p).exists()
    assert _total(tmp_path) == total


def test_lru_order_and_young_entries_are_protected(tmp_path):
    old = _entry(tmp_path, "a.xml", 100, age=5 * 3600)
    mid = _entry(tmp_path, "b.json", 100, age=3 * 3600)
    young = _entry(tmp_path, "c.xml", 100, age=10)
    assert acb.enforce_ast_cache_budget(tmp_path, max_bytes=150, now=NOW) == [old, mid]
    assert young.exists()


def test_under_budget_disabled_and_foreign_files_are_untouched(tmp_path):
    a = _entry(tmp_path, "a.xml", 100, age=9 * 3600)
    other = _entry(tmp_path, "notes.txt", 10_000, age=9 * 3600)
    assert acb.enforce_ast_cache_budget(tmp_path, max_bytes=1_000, now=NOW) == []
    assert acb.enforce_ast_cache_budget(tmp_path, max_bytes=0, now=NOW) == []
    assert a.exists() and other.exists()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", acb.DEFAULT_MAX_BYTES),
        ("123", 123),
        ("0", 0),
        ("-5", 0),
        ("junk", acb.DEFAULT_MAX_BYTES),
    ],
)
def test_env_budget(monkeypatch, raw, expected):
    monkeypatch.setenv("ABICHECK_AST_CACHE_MAX_BYTES", raw)
    assert acb.configured_max_bytes() == expected
