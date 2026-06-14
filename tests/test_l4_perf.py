# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the L4 source-replay performance knobs.

Covers the worker-count clamp (oversubscription guard), the thread/process
executor selector, the picklable extract worker (process-pool requirement), and
the per-pass dependency-digest memo contract. All pure/fast — no clang.
"""

from __future__ import annotations

import logging
import pickle
from functools import partial
from pathlib import Path

import pytest

from abicheck.buildsource import source_replay as sr
from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.source_abi import SourceAbiTu
from abicheck.buildsource.source_extractors.base import SourceExtractionError


# ── worker-count clamp (#5: oversubscription guard) ───────────────────────────
def test_l4_jobs_clamps_oversubscription(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ABICHECK_L4_JOBS", "64")
    ceiling = sr._l4_jobs_ceiling()
    with caplog.at_level(logging.WARNING):
        jobs = sr._l4_jobs(100)
    assert jobs == ceiling
    assert jobs <= 64
    assert any("oversubscription" in r.message for r in caplog.records)


def test_l4_jobs_explicit_within_ceiling_is_honoured(monkeypatch) -> None:
    monkeypatch.setenv("ABICHECK_L4_JOBS", "1")
    assert sr._l4_jobs(100) == 1  # the determinism-forcing serial override


def test_l4_jobs_invalid_env_falls_back_serial(monkeypatch) -> None:
    monkeypatch.setenv("ABICHECK_L4_JOBS", "not-a-number")
    assert sr._l4_jobs(100) == 1


def test_l4_jobs_auto_capped_at_cpu_and_eight(monkeypatch) -> None:
    monkeypatch.delenv("ABICHECK_L4_JOBS", raising=False)
    monkeypatch.setattr(sr.os, "cpu_count", lambda: 4)
    assert sr._l4_jobs(1000) == 4  # min(units, cpu, 8)
    assert sr._l4_jobs(2) == 2


# ── executor selector (#1: GIL-bound AST work) ────────────────────────────────
def test_l4_executor_defaults_to_threads(monkeypatch) -> None:
    monkeypatch.delenv("ABICHECK_L4_EXECUTOR", raising=False)
    assert sr._l4_use_process_pool() is False


@pytest.mark.parametrize(
    "value,expected",
    [("process", True), ("thread", False), ("PROCESS", True), ("bogus", False)],
)
def test_l4_executor_env(monkeypatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("ABICHECK_L4_EXECUTOR", value)
    assert sr._l4_use_process_pool() is expected


# ── picklable extract worker (#1: process-pool requirement) ───────────────────
class _FakeExtractor:
    """Minimal SourceAbiExtractor-shaped stub; picklable (module-level class)."""

    name = "fake"
    version = "1"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def extract(self, cu, *, public_header_roots, target_id):  # noqa: ANN001
        if self.fail:
            raise SourceExtractionError("boom")
        return SourceAbiTu(
            tu_id=cu.id, extractor="fake", public_header_roots=[], declarations=[]
        )


def _cu(uid: str = "u1") -> CompileUnit:
    return CompileUnit(id=uid, source=f"{uid}.cpp")


def test_extract_one_returns_tu_or_diagnostic() -> None:
    tu, err = sr._extract_one(_FakeExtractor(), [], "", _cu())
    assert err is None
    assert isinstance(tu, SourceAbiTu)

    tu2, err2 = sr._extract_one(_FakeExtractor(fail=True), [], "", _cu("u2"))
    assert tu2 is None
    assert err2 is not None and "u2" in err2


def test_extract_worker_partial_is_picklable() -> None:
    # ProcessPoolExecutor pickles the worker + its bound args; this is the
    # invariant that lets the process executor exist at all.
    worker = partial(sr._extract_one, _FakeExtractor(), ["/inc"], "tgt")
    restored = pickle.loads(pickle.dumps(worker))
    tu, err = restored(_cu("u3"))
    assert err is None and isinstance(tu, SourceAbiTu)


# ── dependency-digest memo (#2: hash each shared header once per pass) ─────────
def test_dep_digest_memo_reuses_within_pass(tmp_path: Path) -> None:
    header = tmp_path / "common.h"
    header.write_text("#define A 1\n")
    memo: dict[str, str | None] = {}

    first = sr._dep_digest(str(header), memo)
    assert first is not None and memo[str(header)] == first

    # Mutate the file: a memoized lookup intentionally returns the cached digest
    # (the pass assumes files are stable for its duration) ...
    header.write_text("#define A 2\n")
    assert sr._dep_digest(str(header), memo) == first

    # ... while a memo-less lookup (direct cache callers) always re-reads, which
    # is what preserves the cache-invalidation contract across passes.
    assert sr._dep_digest(str(header)) != first


def test_dep_digest_memo_records_missing_as_none(tmp_path: Path) -> None:
    memo: dict[str, str | None] = {}
    missing = str(tmp_path / "gone.h")
    assert sr._dep_digest(missing, memo) is None
    assert missing in memo and memo[missing] is None
