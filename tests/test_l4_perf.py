# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the L4 source-replay performance knobs.

Covers the worker-count clamp (oversubscription guard), the thread/process
executor selector, the picklable extract worker (process-pool requirement), and
the per-pass dependency-digest memo contract. All pure/fast — no clang.
"""

from __future__ import annotations

import logging
import pickle
import time
from functools import partial
from pathlib import Path

import pytest

from abicheck.buildsource import source_replay as sr
from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.source_abi import SourceAbiTu
from abicheck.buildsource.source_extractors.base import SourceExtractionError


# ── worker-count clamp (#5: oversubscription guard) ───────────────────────────
def test_l4_jobs_clamps_oversubscription(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ABICHECK_L4_JOBS", "64")
    monkeypatch.setattr(sr, "_l4_available_mem_gib", lambda: None)  # isolate CPU clamp
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
    monkeypatch.setattr(sr, "_l4_available_mem_gib", lambda: None)  # isolate CPU clamp
    assert sr._l4_jobs(1000) == 4  # min(units, cpu, 8)
    assert sr._l4_jobs(2) == 2


# ── memory clamp (#3: OOM guard for template-heavy L4) ────────────────────────
def test_l4_jobs_auto_capped_by_available_memory(monkeypatch) -> None:
    # On a low-memory host the auto default is reduced below the CPU cap so N
    # concurrent multi-GiB clang ASTs can't OOM-kill the replay (the UXL s5/s6 OOM).
    monkeypatch.delenv("ABICHECK_L4_JOBS", raising=False)
    monkeypatch.delenv("ABICHECK_L4_JOB_MEM_GIB", raising=False)
    monkeypatch.setattr(sr.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(sr, "_l4_available_mem_gib", lambda: 6.0)  # 6 GiB / 3.0 = 2
    assert sr._l4_jobs(1000) == 2  # CPU cap 8 reduced to the memory cap 2


def test_l4_jobs_explicit_clamped_by_memory(monkeypatch, caplog) -> None:
    # An explicit override that won't fit in RAM is clamped (loudly), like the
    # oversubscription ceiling — correctness (no OOM) over literal obedience.
    monkeypatch.setenv("ABICHECK_L4_JOBS", "8")
    monkeypatch.delenv("ABICHECK_L4_JOB_MEM_GIB", raising=False)
    monkeypatch.setattr(sr, "_l4_available_mem_gib", lambda: 6.0)  # cap = 2
    with caplog.at_level(logging.WARNING):
        jobs = sr._l4_jobs(100)
    assert jobs == 2
    assert any("OOM" in r.message for r in caplog.records)


def test_l4_job_mem_budget_env_tunes_the_cap(monkeypatch) -> None:
    # A smaller per-worker budget raises the cap (escape hatch for big-RAM/swap hosts).
    monkeypatch.delenv("ABICHECK_L4_JOBS", raising=False)
    monkeypatch.setattr(sr.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(sr, "_l4_available_mem_gib", lambda: 6.0)
    monkeypatch.setenv("ABICHECK_L4_JOB_MEM_GIB", "1.0")  # 6 / 1.0 = 6
    assert sr._l4_jobs(1000) == 6


def test_l4_jobs_no_meminfo_falls_back_to_cpu_cap(monkeypatch) -> None:
    # When RAM can't be read (non-Linux / sandbox), the memory clamp is skipped.
    monkeypatch.delenv("ABICHECK_L4_JOBS", raising=False)
    monkeypatch.setattr(sr.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(sr, "_l4_available_mem_gib", lambda: None)
    assert sr._l4_jobs(1000) == 4


# ── memory-probe internals ──────────────────────────────────────────────────
# Moved to tests/test_process_resources.py (G32 Phase E, ADR-050 D6): the
# meminfo/cgroup probing itself now lives in abicheck.process_resources, a
# leaf module shared with dumper_manifest.py's per-TU pool. This file keeps
# only the ABICHECK_L4_*-policy tests below, which still exercise
# source_replay.py's own thin wrapper functions.


def test_l4_job_mem_budget_invalid_env_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("ABICHECK_L4_JOB_MEM_GIB", "not-a-float")
    assert sr._l4_job_mem_budget_gib() == sr._L4_JOB_MEM_BUDGET_GIB


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
    # nosec B301 - round-tripping our own in-process object IS the test: it
    # proves ProcessPoolExecutor can ship the worker to a child process.
    restored = pickle.loads(pickle.dumps(worker))  # noqa: S301  # nosec B301
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


def test_headers_only_public_roots_perf_guard_avoids_full_fanout() -> None:
    """Track the pvxs-style regression: public roots must not replay every TU."""
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id=f"cu://{idx}", source=f"src/unit{idx}.cpp")
            for idx in range(120)
        ]
    )
    include_map = {
        f"cu://{idx}": [f"src/private{idx}.h"]
        for idx in range(120)
    }
    include_map["cu://17"] = ["../pvxs/log.h", "src/private17.h"]
    include_map["cu://89"] = ["../pvxs/client.h", "src/private89.h"]

    started = time.perf_counter()
    selected = sr.select_compile_units(
        build,
        scope="headers-only",
        include_map=include_map,
        public_header_roots=[
            "/work/pvxs/include/pvxs/log.h",
            "/work/pvxs/include/pvxs/client.h",
        ],
    )
    elapsed = time.perf_counter() - started

    assert {unit.id for unit in selected} == {"cu://17", "cu://89"}
    assert len(selected) <= 2
    # Coarse full-fanout guard: public-root selection is pure set/path logic over
    # the 120 units, so it is sub-millisecond in principle; a regression to
    # per-unit replay/IO would be orders of magnitude slower (seconds). The
    # threshold has generous headroom over the pure-logic cost so shared-runner
    # scheduling jitter (observed spikes to ~0.29s for sub-ms work) can't flake it
    # while a real fan-out regression still trips it.
    assert elapsed < 1.5
