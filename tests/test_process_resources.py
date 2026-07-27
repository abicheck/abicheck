# SPDX-License-Identifier: Apache-2.0
"""Unit tests for abicheck.process_resources — the shared RAM-probing/
pool-sizing leaf module (G32 Phase E, ADR-050 D6).

Migrated from tests/test_l4_perf.py's original RAM-probing tests (same
behavior, new import path — a refactor test, not new coverage): these used
to test buildsource.source_replay's own inline meminfo/cgroup parsing
before it was factored out here so dumper_manifest.py's per-TU pool could
reuse the identical implementation. test_l4_perf.py keeps its L4-policy
tests (_l4_jobs/_l4_jobs_ceiling/_l4_job_mem_budget_gib/_l4_use_process_pool),
which still exercise source_replay.py's own ABICHECK_L4_*-named wrappers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck import process_resources as pr


# ── memory-probe internals (real files; cgroup-aware) ──────────────────────
def test_meminfo_available_parses_memavailable(tmp_path: Path) -> None:
    mi = tmp_path / "meminfo"
    mi.write_text("MemTotal:       16000000 kB\nMemAvailable:    8388608 kB\n")
    # 8388608 kB == 8 GiB exactly.
    assert pr.meminfo_available_gib(str(mi)) == pytest.approx(8.0)


def test_meminfo_available_missing_file_is_none(tmp_path: Path) -> None:
    assert pr.meminfo_available_gib(str(tmp_path / "nope")) is None


def test_meminfo_available_no_memavailable_line_is_none(tmp_path: Path) -> None:
    mi = tmp_path / "meminfo"
    mi.write_text(
        "MemTotal:       16000000 kB\nMemFree:    1000 kB\n"
    )  # no MemAvailable
    assert pr.meminfo_available_gib(str(mi)) is None


def test_read_int_file_reads_int_else_none(tmp_path: Path) -> None:
    good = tmp_path / "n"
    good.write_text("4294967296\n")
    assert pr._read_int_file(str(good)) == 4294967296
    bad = tmp_path / "max"
    bad.write_text("max\n")  # cgroup v2 unbounded keyword
    assert pr._read_int_file(str(bad)) is None
    assert pr._read_int_file(str(tmp_path / "absent")) is None


def test_cgroup_rel_paths_parses_v2_and_v1(monkeypatch, tmp_path: Path) -> None:
    proc = tmp_path / "cgroup"
    proc.write_text(
        "0::/pod123/container\n"  # v2 unified line
        "5:cpu,cpuacct:/pod123\n"  # non-memory v1 controller (ignored)
        "4:memory:/pod123/mem\n"  # v1 memory line
        "garbage-line-no-colons\n"  # malformed -> skipped
    )
    monkeypatch.setattr(pr, "_PROC_SELF_CGROUP", str(proc))
    assert pr._cgroup_rel_paths() == ("/pod123/container", "/pod123/mem")


def test_cgroup_rel_paths_missing_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pr, "_PROC_SELF_CGROUP", str(tmp_path / "absent"))
    assert pr._cgroup_rel_paths() == (None, None)


def test_cgroup_rel_paths_non_ascii_does_not_raise(monkeypatch, tmp_path: Path) -> None:
    # A non-ASCII systemd slice / container name must not raise UnicodeDecodeError
    # mid-iteration and abort the caller (best-effort probe).
    proc = tmp_path / "cgroup"
    proc.write_bytes("0::/slice-café/le-π\n".encode())  # non-ASCII bytes
    monkeypatch.setattr(pr, "_PROC_SELF_CGROUP", str(proc))
    v2, v1 = pr._cgroup_rel_paths()  # must not raise
    assert v1 is None and v2 is not None and v2.startswith("/slice-")


def test_cgroup_chain_walks_leaf_to_root(tmp_path: Path) -> None:
    chain = pr._cgroup_chain(str(tmp_path), "/a/b")
    assert chain == [tmp_path / "a" / "b", tmp_path / "a", tmp_path]
    assert pr._cgroup_chain(str(tmp_path), None) == [tmp_path]  # root-only
    assert pr._cgroup_chain(str(tmp_path), "/") == [tmp_path]


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
    monkeypatch.setattr(pr, "_PROC_SELF_CGROUP", str(proc))
    monkeypatch.setattr(pr, "_CGROUP_V2_ROOT", str(root))
    assert pr.cgroup_available_mem_gib() == pytest.approx(4.0)


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
    monkeypatch.setattr(pr, "_PROC_SELF_CGROUP", str(proc))
    monkeypatch.setattr(pr, "_CGROUP_V2_ROOT", str(root))
    assert pr.cgroup_available_mem_gib() == pytest.approx(3.0)  # parent cap


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
    monkeypatch.setattr(pr, "_PROC_SELF_CGROUP", str(proc))
    monkeypatch.setattr(pr, "_CGROUP_V2_ROOT", str(v2root))
    monkeypatch.setattr(pr, "_CGROUP_V1_ROOT", str(v1root))
    assert pr.cgroup_available_mem_gib() == pytest.approx(2.0)


def test_cgroup_v1_unlimited_sentinel_is_none(monkeypatch, tmp_path: Path) -> None:
    v2root = tmp_path / "v2"
    v2root.mkdir()  # no memory.max -> v2 unbounded
    v1root = tmp_path / "v1mem"
    (v1root).mkdir()
    (v1root / "memory.limit_in_bytes").write_text(str(pr._CGROUP_V1_UNLIMITED))
    proc = tmp_path / "self_cgroup"
    proc.write_text("0::/\n4:memory:/\n")
    monkeypatch.setattr(pr, "_PROC_SELF_CGROUP", str(proc))
    monkeypatch.setattr(pr, "_CGROUP_V2_ROOT", str(v2root))
    monkeypatch.setattr(pr, "_CGROUP_V1_ROOT", str(v1root))
    assert pr.cgroup_available_mem_gib() is None


def test_cgroup_none_when_nothing_bounded(monkeypatch, tmp_path: Path) -> None:
    proc = tmp_path / "self_cgroup"
    proc.write_text("0::/\n")
    monkeypatch.setattr(pr, "_PROC_SELF_CGROUP", str(proc))
    monkeypatch.setattr(pr, "_CGROUP_V2_ROOT", str(tmp_path / "absent-v2"))
    monkeypatch.setattr(pr, "_CGROUP_V1_ROOT", str(tmp_path / "absent-v1"))
    assert pr.cgroup_available_mem_gib() is None


def test_available_mem_takes_min_of_host_and_cgroup(monkeypatch) -> None:
    # The cgroup limit (4 GiB) is smaller than host MemAvailable (64 GiB): a pod
    # on a big host must use the cgroup headroom, not the host RAM.
    monkeypatch.setattr(pr, "meminfo_available_gib", lambda path="": 64.0)
    monkeypatch.setattr(pr, "cgroup_available_mem_gib", lambda: 4.0)
    assert pr.available_mem_gib() == pytest.approx(4.0)


def test_available_mem_none_when_neither_readable(monkeypatch) -> None:
    monkeypatch.setattr(pr, "meminfo_available_gib", lambda path="": None)
    monkeypatch.setattr(pr, "cgroup_available_mem_gib", lambda: None)
    assert pr.available_mem_gib() is None


# ── generic sizing primitives (jobs_ceiling / job_mem_budget_gib / mem_cap) ──
def test_jobs_ceiling_defaults_match_l4_original(monkeypatch) -> None:
    monkeypatch.setattr(pr.os, "cpu_count", lambda: 4)
    assert pr.jobs_ceiling() == 8  # max(8, 2*4)
    monkeypatch.setattr(pr.os, "cpu_count", lambda: 10)
    assert pr.jobs_ceiling() == 20  # max(8, 2*10)


def test_job_mem_budget_gib_env_override(monkeypatch) -> None:
    monkeypatch.setenv("SOME_JOB_MEM_GIB", "1.5")
    assert pr.job_mem_budget_gib("SOME_JOB_MEM_GIB", 3.0) == pytest.approx(1.5)


def test_job_mem_budget_gib_invalid_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("SOME_JOB_MEM_GIB", "not-a-float")
    assert pr.job_mem_budget_gib("SOME_JOB_MEM_GIB", 3.0) == 3.0


def test_job_mem_budget_gib_floored(monkeypatch) -> None:
    monkeypatch.setenv("SOME_JOB_MEM_GIB", "0.01")
    assert pr.job_mem_budget_gib("SOME_JOB_MEM_GIB", 3.0) == pytest.approx(0.25)


def test_mem_cap_none_when_ram_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(pr, "available_mem_gib", lambda: None)
    assert pr.mem_cap(3.0) is None


def test_mem_cap_divides_available_by_budget(monkeypatch) -> None:
    monkeypatch.setattr(pr, "available_mem_gib", lambda: 6.0)
    assert pr.mem_cap(3.0) == 2
    assert pr.mem_cap(1.0) == 6
