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


# ── memory-probe internals (real files; cgroup-aware, #458 Codex review) ──────
def test_meminfo_available_parses_memavailable(tmp_path: Path) -> None:
    mi = tmp_path / "meminfo"
    mi.write_text("MemTotal:       16000000 kB\nMemAvailable:    8388608 kB\n")
    # 8388608 kB == 8 GiB exactly.
    assert sr._meminfo_available_gib(str(mi)) == pytest.approx(8.0)


def test_meminfo_available_missing_file_is_none(tmp_path: Path) -> None:
    assert sr._meminfo_available_gib(str(tmp_path / "nope")) is None


def test_meminfo_available_no_memavailable_line_is_none(tmp_path: Path) -> None:
    mi = tmp_path / "meminfo"
    mi.write_text(
        "MemTotal:       16000000 kB\nMemFree:    1000 kB\n"
    )  # no MemAvailable
    assert sr._meminfo_available_gib(str(mi)) is None


def test_l4_job_mem_budget_invalid_env_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("ABICHECK_L4_JOB_MEM_GIB", "not-a-float")
    assert sr._l4_job_mem_budget_gib() == sr._L4_JOB_MEM_BUDGET_GIB


def test_read_int_file_reads_int_else_none(tmp_path: Path) -> None:
    good = tmp_path / "n"
    good.write_text("4294967296\n")
    assert sr._read_int_file(str(good)) == 4294967296
    bad = tmp_path / "max"
    bad.write_text("max\n")  # cgroup v2 unbounded keyword
    assert sr._read_int_file(str(bad)) is None
    assert sr._read_int_file(str(tmp_path / "absent")) is None


def test_cgroup_rel_paths_parses_v2_and_v1(monkeypatch, tmp_path: Path) -> None:
    proc = tmp_path / "cgroup"
    proc.write_text(
        "0::/pod123/container\n"  # v2 unified line
        "5:cpu,cpuacct:/pod123\n"  # non-memory v1 controller (ignored)
        "4:memory:/pod123/mem\n"  # v1 memory line
        "garbage-line-no-colons\n"  # malformed -> skipped
    )
    monkeypatch.setattr(sr, "_PROC_SELF_CGROUP", str(proc))
    assert sr._cgroup_rel_paths() == ("/pod123/container", "/pod123/mem")


def test_cgroup_rel_paths_missing_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sr, "_PROC_SELF_CGROUP", str(tmp_path / "absent"))
    assert sr._cgroup_rel_paths() == (None, None)


def test_cgroup_rel_paths_non_ascii_does_not_raise(monkeypatch, tmp_path: Path) -> None:
    # A non-ASCII systemd slice / container name must not raise UnicodeDecodeError
    # mid-iteration and abort the L4 run (best-effort probe). CodeRabbit #458.
    proc = tmp_path / "cgroup"
    proc.write_bytes("0::/slice-café/le-π\n".encode())  # non-ASCII bytes
    monkeypatch.setattr(sr, "_PROC_SELF_CGROUP", str(proc))
    v2, v1 = sr._cgroup_rel_paths()  # must not raise
    assert v1 is None and v2 is not None and v2.startswith("/slice-")


def test_cgroup_chain_walks_leaf_to_root(tmp_path: Path) -> None:
    chain = sr._cgroup_chain(str(tmp_path), "/a/b")
    assert chain == [tmp_path / "a" / "b", tmp_path / "a", tmp_path]
    assert sr._cgroup_chain(str(tmp_path), None) == [tmp_path]  # root-only
    assert sr._cgroup_chain(str(tmp_path), "/") == [tmp_path]


def _write_cg(d: Path, max_name: str, cur_name: str, limit: int, used: int) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / max_name).write_text(str(limit))
    (d / cur_name).write_text(str(used))


def test_cgroup_v2_uses_process_path_not_root(monkeypatch, tmp_path: Path) -> None:
    # The unified root is unbounded ("max") but the process's nested slice caps it
    # at 6 GiB w/ 2 used -> 4 GiB. The walk must find the leaf, not stop at root.
    root = tmp_path / "cg"
    root.mkdir()
    (root / "memory.max").write_text("max\n")
    (root / "memory.current").write_text("0\n")
    leaf = root / "slice" / "task"
    _write_cg(leaf, "memory.max", "memory.current", 6 * 1024**3, 2 * 1024**3)
    proc = tmp_path / "self_cgroup"
    proc.write_text("0::/slice/task\n")
    monkeypatch.setattr(sr, "_PROC_SELF_CGROUP", str(proc))
    monkeypatch.setattr(sr, "_CGROUP_V2_ROOT", str(root))
    assert sr._cgroup_available_mem_gib() == pytest.approx(4.0)


def test_cgroup_tightest_ancestor_wins(monkeypatch, tmp_path: Path) -> None:
    # Parent slice (3 GiB free) is tighter than the leaf (5 GiB free): the
    # effective headroom is the min across the chain.
    root = tmp_path / "cg"
    _write_cg(root / "slice", "memory.max", "memory.current", 4 * 1024**3, 1 * 1024**3)
    _write_cg(
        root / "slice" / "leaf",
        "memory.max",
        "memory.current",
        6 * 1024**3,
        1 * 1024**3,
    )
    proc = tmp_path / "self_cgroup"
    proc.write_text("0::/slice/leaf\n")
    monkeypatch.setattr(sr, "_PROC_SELF_CGROUP", str(proc))
    monkeypatch.setattr(sr, "_CGROUP_V2_ROOT", str(root))
    assert sr._cgroup_available_mem_gib() == pytest.approx(3.0)  # parent cap


def test_cgroup_v2_unbounded_falls_through_to_v1(monkeypatch, tmp_path: Path) -> None:
    v2root = tmp_path / "v2"
    v2root.mkdir()
    (v2root / "memory.max").write_text("max\n")  # whole v2 chain unbounded
    v1root = tmp_path / "v1mem"
    _write_cg(
        v1root / "pod",
        "memory.limit_in_bytes",
        "memory.usage_in_bytes",
        3 * 1024**3,
        1 * 1024**3,
    )
    proc = tmp_path / "self_cgroup"
    proc.write_text("0::/\n4:memory:/pod\n")
    monkeypatch.setattr(sr, "_PROC_SELF_CGROUP", str(proc))
    monkeypatch.setattr(sr, "_CGROUP_V2_ROOT", str(v2root))
    monkeypatch.setattr(sr, "_CGROUP_V1_ROOT", str(v1root))
    assert sr._cgroup_available_mem_gib() == pytest.approx(2.0)


def test_cgroup_v1_unlimited_sentinel_is_none(monkeypatch, tmp_path: Path) -> None:
    v2root = tmp_path / "v2"
    v2root.mkdir()  # no memory.max -> v2 unbounded
    v1root = tmp_path / "v1mem"
    (v1root).mkdir()
    (v1root / "memory.limit_in_bytes").write_text(str(sr._CGROUP_V1_UNLIMITED))
    proc = tmp_path / "self_cgroup"
    proc.write_text("0::/\n4:memory:/\n")
    monkeypatch.setattr(sr, "_PROC_SELF_CGROUP", str(proc))
    monkeypatch.setattr(sr, "_CGROUP_V2_ROOT", str(v2root))
    monkeypatch.setattr(sr, "_CGROUP_V1_ROOT", str(v1root))
    assert sr._cgroup_available_mem_gib() is None


def test_cgroup_none_when_nothing_bounded(monkeypatch, tmp_path: Path) -> None:
    proc = tmp_path / "self_cgroup"
    proc.write_text("0::/\n")
    monkeypatch.setattr(sr, "_PROC_SELF_CGROUP", str(proc))
    monkeypatch.setattr(sr, "_CGROUP_V2_ROOT", str(tmp_path / "absent-v2"))
    monkeypatch.setattr(sr, "_CGROUP_V1_ROOT", str(tmp_path / "absent-v1"))
    assert sr._cgroup_available_mem_gib() is None


def test_l4_available_mem_takes_min_of_host_and_cgroup(monkeypatch) -> None:
    # The cgroup limit (4 GiB) is smaller than host MemAvailable (64 GiB): a pod
    # on a big host must use the cgroup headroom, not the host RAM.
    monkeypatch.setattr(sr, "_meminfo_available_gib", lambda path="": 64.0)
    monkeypatch.setattr(sr, "_cgroup_available_mem_gib", lambda: 4.0)
    assert sr._l4_available_mem_gib() == pytest.approx(4.0)


def test_l4_available_mem_none_when_neither_readable(monkeypatch) -> None:
    monkeypatch.setattr(sr, "_meminfo_available_gib", lambda path="": None)
    monkeypatch.setattr(sr, "_cgroup_available_mem_gib", lambda: None)
    assert sr._l4_available_mem_gib() is None


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
    assert elapsed < 0.25
