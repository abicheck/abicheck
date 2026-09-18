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

"""Attributable memory instrumentation for a run (memory work, step 1).

The point of this module is that the four numbers people reach for when
they say "abicheck used N GiB" are *four different numbers*, and a memory
investigation that mixes them reaches a wrong owner:

* **parent RSS** -- this interpreter's resident set. What a naive
  ``/usr/bin/time -v`` on the CLI reports, and what a pure-Python retention
  fix moves.
* **process-tree RSS / PSS** -- the parent plus every live child
  (``castxml``, ``clang``, a compiler driver). Summed RSS double-counts
  shared pages; PSS does not, which is why both are recorded rather than
  one. A *concurrency* change moves this without moving parent RSS.
* **cgroup ``memory.current`` / ``memory.peak``** -- what the kernel bills
  the job, page cache included. This is what an OOM kill is decided on and
  the only one comparable against "a nominal 16 GB runner".
* **Python allocations** (``tracemalloc``) -- allocation totals, not
  resident bytes: they exclude the allocator's unreturned arenas and every
  byte a child process holds. Recorded only in an explicitly opted-in
  *profiling* run, because tracemalloc itself perturbs both time and RSS.

So this module records all four separately, per phase, alongside the
structural counts that say *what* is resident (retained snapshots, raw AST
groups/entries, completed-member evidence sizes) -- the attribution a CPU
profile cannot give and a single peak-RSS figure cannot either.

**Disabled by default and cheap when disabled.** Everything is gated on
``ABICHECK_MEMORY_TRACE`` naming an output path. With it unset, :func:`phase`
is a bare generator yielding once and :func:`sample`/:func:`counts` return
after a single module-global boolean test -- no probing, no imports, no
``/proc`` reads. Nothing in this module changes analysis behaviour: it is
observation only, and a probe failure is swallowed rather than raised.

Output is JSON Lines, one object per sample, appended: a long run stays
readable while it is still going, and a crash keeps every sample up to the
crash. ``scripts/bench_release_memory.py`` is the consumer.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = [
    "counts",
    "memory_trace_enabled",
    "memory_trace_path",
    "phase",
    "sample",
    "read_samples",
]

#: Names the output path *and* switches the whole module on.
ENV_TRACE_PATH = "ABICHECK_MEMORY_TRACE"
#: Opt into ``tracemalloc`` sampling. Separate from the switch above on
#: purpose: Python-allocation attribution is a *different run* from a
#: wall-clock/RSS measurement, never the same one (see the module docstring).
ENV_TRACEMALLOC = "ABICHECK_MEMORY_TRACE_TRACEMALLOC"

_PAGE_SIZE = 4096

_lock = threading.Lock()
_state_lock = threading.Lock()
_resolved = False
_path: Path | None = None
_tracemalloc = False


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() not in ("", "0", "false", "no", "off")


def _resolve() -> None:
    """Resolve the env gate once per process.

    Deliberately latched: a trace that switched itself on halfway through a
    run would report phase boundaries against an unknown earlier baseline,
    which is worse than not tracing.
    """
    global _resolved, _path, _tracemalloc
    with _state_lock:
        if _resolved:
            return
        raw = os.environ.get(ENV_TRACE_PATH, "").strip()
        _path = Path(raw) if raw else None
        _tracemalloc = _path is not None and _truthy(os.environ.get(ENV_TRACEMALLOC))
        if _tracemalloc:
            import tracemalloc

            if not tracemalloc.is_tracing():
                tracemalloc.start()
        _resolved = True


def reset_for_testing() -> None:
    """Re-read the environment. Tests only -- never called from a run."""
    global _resolved, _path, _tracemalloc
    with _state_lock:
        _resolved = False
        _path = None
        _tracemalloc = False


def memory_trace_enabled() -> bool:
    """Whether this process is recording a memory trace."""
    if not _resolved:
        _resolve()
    return _path is not None


def memory_trace_path() -> Path | None:
    """Where samples are being written, or ``None`` when disabled."""
    if not _resolved:
        _resolve()
    return _path


# --------------------------------------------------------------------------
# Probes. Each returns ``None`` rather than raising when its source is
# unavailable (a non-Linux host, a hidden ``/proc``, a v1-only cgroup): a
# missing number must be visibly missing in the trace, never silently zero.
# --------------------------------------------------------------------------


def _self_rss_bytes() -> int | None:
    """This process's resident set, from ``/proc/self/statm`` (field 2)."""
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            return int(fh.read().split()[1]) * _PAGE_SIZE
    except (OSError, ValueError, IndexError):
        return None


def _child_pids(pid: int) -> list[int]:
    """Direct children of *pid*, from ``/proc/<pid>/task/*/children``."""
    out: list[int] = []
    try:
        task_dir = Path(f"/proc/{pid}/task")
        for task in task_dir.iterdir():
            try:
                raw = (task / "children").read_text(encoding="ascii")
            except OSError:
                continue
            out.extend(int(tok) for tok in raw.split())
    except (OSError, ValueError):
        return out
    return out


def _proc_tree_pids(root: int | None = None) -> list[int]:
    """*root* and every descendant alive at this instant.

    Best effort by construction: a child can exit mid-walk, and one that
    starts mid-walk may be missed. That is acceptable for a sample -- it is
    a point-in-time reading, not an accounting record -- but it is why the
    benchmark harness samples repeatedly rather than once.
    """
    root = os.getpid() if root is None else root
    seen: list[int] = []
    pending = [root]
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.append(pid)
        pending.extend(_child_pids(pid))
    return seen


def _smaps_rollup(pid: int) -> tuple[int | None, int | None]:
    """``(rss_bytes, pss_bytes)`` for *pid* from ``smaps_rollup``.

    PSS divides each shared page by the number of mappers, so summing PSS
    over a process tree is the only one of the two that does not
    double-count the pages a fork shares with its parent.
    """
    rss = pss = None
    try:
        with open(f"/proc/{pid}/smaps_rollup", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("Rss:"):
                    rss = int(line.split()[1]) * 1024
                elif line.startswith("Pss:"):
                    pss = int(line.split()[1]) * 1024
                if rss is not None and pss is not None:
                    break
    except (OSError, ValueError, IndexError):
        return (None, None)
    return (rss, pss)


def _tree_memory() -> tuple[int | None, int | None, int]:
    """``(tree_rss, tree_pss, process_count)`` over this process tree."""
    pids = _proc_tree_pids()
    rss_total = 0
    pss_total = 0
    any_rss = any_pss = False
    for pid in pids:
        rss, pss = _smaps_rollup(pid)
        if rss is not None:
            rss_total += rss
            any_rss = True
        if pss is not None:
            pss_total += pss
            any_pss = True
    return (
        rss_total if any_rss else None,
        pss_total if any_pss else None,
        len(pids),
    )


def _cgroup_files() -> tuple[str | None, str | None]:
    """Paths to this process's cgroup ``memory.current``/``memory.peak``.

    Reuses :mod:`abicheck.process_resources`'s own leaf->root cgroup walk
    rather than re-deriving the path, so the bytes billed here and the
    headroom the worker sizing reads come from the same cgroup.
    """
    from . import process_resources as pr

    v2_rel, v1_rel = pr._cgroup_rel_paths()
    for path in pr._cgroup_chain(pr._CGROUP_V2_ROOT, v2_rel):
        current = path / "memory.current"
        if current.exists():
            peak = path / "memory.peak"
            return (str(current), str(peak) if peak.exists() else None)
    # cgroup v1 spells the same two numbers differently. A v1-only host is
    # not exotic (plenty of CI images still are), and reporting "no cgroup
    # accounting" there would drop the one figure an OOM is decided on.
    for path in pr._cgroup_chain(pr._CGROUP_V1_ROOT, v1_rel):
        current = path / "memory.usage_in_bytes"
        if current.exists():
            peak = path / "memory.max_usage_in_bytes"
            return (str(current), str(peak) if peak.exists() else None)
    return (None, None)


_cgroup_cache: tuple[str | None, str | None] | None = None


def _cgroup_memory() -> tuple[int | None, int | None]:
    global _cgroup_cache
    if _cgroup_cache is None:
        try:
            _cgroup_cache = _cgroup_files()
        except Exception:  # pragma: no cover - probe must never raise
            _cgroup_cache = (None, None)
    current_path, peak_path = _cgroup_cache

    def _read(path: str | None) -> int | None:
        if path is None:
            return None
        try:
            with open(path, encoding="ascii") as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return None

    return (_read(current_path), _read(peak_path))


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------

_start = time.monotonic()


def _write(record: dict[str, Any]) -> None:
    path = memory_trace_path()
    if path is None:
        return
    try:
        line = json.dumps(record, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError:  # pragma: no cover - a trace must never fail a run
        return


def sample(event: str, /, **attrs: Any) -> None:
    """Record one fully-probed sample labelled *event*.

    Returns immediately -- one boolean test, no probing -- when tracing is
    off, which is the default.
    """
    if not memory_trace_enabled():
        return
    tree_rss, tree_pss, procs = _tree_memory()
    cg_current, cg_peak = _cgroup_memory()
    record: dict[str, Any] = {
        "event": event,
        "t": round(time.monotonic() - _start, 6),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "parent_rss_bytes": _self_rss_bytes(),
        "tree_rss_bytes": tree_rss,
        "tree_pss_bytes": tree_pss,
        "tree_processes": procs,
        "cgroup_current_bytes": cg_current,
        "cgroup_peak_bytes": cg_peak,
        "threads": threading.active_count(),
    }
    if _tracemalloc:
        import tracemalloc

        current, peak = tracemalloc.get_traced_memory()
        record["tracemalloc_current_bytes"] = current
        record["tracemalloc_peak_bytes"] = peak
    if attrs:
        record["attrs"] = attrs
    _write(record)


def counts(event: str, /, **values: Any) -> None:
    """Record structural counts without the (more costly) memory probes.

    For "what is resident" rather than "how much is resident": retained
    full snapshots, raw AST groups/entries and their byte sizes, completed
    member evidence. Pairing these with :func:`sample` is what makes a peak
    attributable instead of merely observed.
    """
    if not memory_trace_enabled():
        return
    _write(
        {
            "event": event,
            "kind": "counts",
            "t": round(time.monotonic() - _start, 6),
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
            "counts": dict(values),
        }
    )


@contextmanager
def phase(name: str, /, **attrs: Any) -> Iterator[None]:
    """Bracket a phase with an enter and an exit sample.

    The exit sample is emitted on the exception path too, so a run that
    dies mid-phase still shows where it was.
    """
    if not memory_trace_enabled():
        yield
        return
    sample(f"{name}:enter", **attrs)
    try:
        yield
    finally:
        sample(f"{name}:exit", **attrs)


def read_samples(path: str | os.PathLike[str]) -> list[Mapping[str, Any]]:
    """Parse a trace file. Malformed trailing lines are skipped.

    A run killed mid-write can leave a partial final line; refusing to
    parse the whole trace because of it would lose exactly the samples an
    OOM investigation needs most.
    """
    out: list[Mapping[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
