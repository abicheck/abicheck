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
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = [
    "counts",
    "gc_object_count",
    "gc_census_is_safe",
    "mark",
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


def _page_size() -> int:
    """This kernel's page size, for converting ``statm``'s page counts.

    ``/proc/self/statm`` reports *pages*, not bytes, so a hard-coded 4096
    silently under-reports parent RSS by 4x on a 16 KiB-page host and 16x on
    a 64 KiB-page one (aarch64 and ppc64le both ship such kernels). Probed
    rather than assumed, with the common value as the fallback for a
    platform that does not expose the constant -- where this whole probe
    reads nothing anyway.
    """
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, ValueError, OSError):  # pragma: no cover - probe
        return 4096


_PAGE_SIZE = _page_size()

_lock = threading.Lock()
_state_lock = threading.Lock()
_resolved = False
_path: Path | None = None
_tracemalloc = False
#: Cumulative ``tracemalloc`` peak high-water, maintained by this module.
#:
#: :func:`phase` calls ``tracemalloc.reset_peak()`` on entry so each phase
#: reports *its own* peak rather than one inherited from an earlier phase --
#: without that, every phase after the biggest one reports the biggest one's
#: number and attribution is impossible. But ``reset_peak()`` destroys the
#: interpreter's cumulative peak, and ``tracemalloc_peak_bytes`` is an
#: already-published field that means the run's peak. So the cumulative value
#: is carried here instead of being read back from ``tracemalloc``, and both
#: are reported: the old field keeps its old meaning, and the per-phase number
#: is a new one beside it.
_peak_high_water = 0


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
    global _resolved, _path, _tracemalloc, _peak_high_water
    with _state_lock:
        _resolved = False
        _path = None
        _tracemalloc = False
        _peak_high_water = 0


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


def gc_census_is_safe() -> bool:
    """Whether enumerating the whole GC heap cannot corrupt another thread.

    ``gc.get_objects()`` (and ``gc.get_referrers()``) return a list holding a
    new reference to *every* GC-tracked object -- including a tuple another
    thread is still building inside ``PySequence_Tuple`` (``tuple(gen)``).
    The builder needs that tuple's refcount to be exactly 1 when it grows it
    with ``_PyTuple_Resize``; CPython checks the eval breaker after a
    ``CALL``, so the GIL can pass to the builder while the census list is
    alive, and the builder then fails with ``tupleobject.c:...: bad argument
    to internal function`` (a ``SystemError``). It can also hand the caller
    a half-built tuple whose unfilled slots are ``NULL``. This is how the
    release fan-out's members ended ``ERROR`` under the benchmark harness,
    whose attach hook took a census from inside a worker thread.

    So a census is safe only when this is the process's sole Python thread.
    Thread *count* rather than "no thread is building a tuple": nothing
    observable from Python can prove the latter.
    """
    return threading.active_count() == 1


def gc_object_count() -> int | None:
    """``len(gc.get_objects())``, or ``None`` when that would be unsafe.

    ``None`` whenever another Python thread exists (see
    :func:`gc_census_is_safe`) -- a missing number, per this module's rule,
    never a guessed one. Every census in this repository goes through here;
    ``tests/test_gc_census_thread_safety.py`` enforces that.
    """
    if not gc_census_is_safe():
        return None
    import gc

    return len(gc.get_objects())


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
    from ..process_resources import (
        _CGROUP_V1_ROOT,
        _CGROUP_V2_ROOT,
        _cgroup_chain,
        _cgroup_rel_paths,
    )

    v2_rel, v1_rel = _cgroup_rel_paths()
    for path in _cgroup_chain(_CGROUP_V2_ROOT, v2_rel):
        current = path / "memory.current"
        if current.exists():
            peak = path / "memory.peak"
            return (str(current), str(peak) if peak.exists() else None)
    # cgroup v1 spells the same two numbers differently. A v1-only host is
    # not exotic (plenty of CI images still are), and reporting "no cgroup
    # accounting" there would drop the one figure an OOM is decided on.
    for path in _cgroup_chain(_CGROUP_V1_ROOT, v1_rel):
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

        global _peak_high_water
        current, peak = tracemalloc.get_traced_memory()
        with _state_lock:
            if peak > _peak_high_water:
                _peak_high_water = peak
            cumulative = _peak_high_water
        record["tracemalloc_current_bytes"] = current
        # Unchanged meaning: the run's peak so far. See _peak_high_water.
        record["tracemalloc_peak_bytes"] = cumulative
        # New: the peak since the innermost enclosing phase began, which is
        # the number that attributes a peak to a stage.
        record["tracemalloc_phase_peak_bytes"] = peak
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


def mark(name: str, /, **attrs: Any) -> None:
    """Record a stage boundary in a linear pipeline.

    The sibling of :func:`phase` for code that runs stages in sequence
    rather than nesting them: each mark's sample reports the memory state at
    that boundary, and its ``tracemalloc_phase_peak_bytes`` is the peak since
    the *previous* mark -- so a stage is attributed by the mark that closes
    it. Use this where bracketing a stage in a ``with`` would mean
    re-indenting a large call expression for no gain in what is recorded.

    Off by default and one boolean test when off, like everything else here.
    """
    if not memory_trace_enabled():
        return
    sample(name, **attrs)
    _reset_tracemalloc_peak()


def _reset_tracemalloc_peak() -> None:
    """Start a fresh per-phase peak window.

    The cumulative peak is preserved in :data:`_peak_high_water` by
    :func:`sample`, which is called immediately before every reset, so the
    value being discarded here has already been folded in.
    """
    if not _tracemalloc:
        return
    import tracemalloc

    tracemalloc.reset_peak()


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
    _reset_tracemalloc_peak()
    try:
        yield
    finally:
        sample(f"{name}:exit", **attrs)
        # A nested phase's own reset means the enclosing phase's remaining
        # span is measured from here, not from its own entry. That is
        # deliberate: the run's peak is the max over phases either way (the
        # cumulative field above carries it), and the alternative -- no reset
        # at all -- makes every phase after the largest one report the
        # largest one's number, which is the failure this exists to avoid.
        _reset_tracemalloc_peak()


def phase_each(name: str, keys: Iterable[str], /) -> Iterator[str]:
    """Yield each of *keys* from inside its own :func:`phase`.

    For a sequential fan-out, so bracketing each item costs one expression
    at the call site instead of rewriting the loop around a ``with``.
    """
    for key in keys:
        with phase(name, key=key):
            yield key


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


def record_release_member(library: str, retention: Mapping[str, Any]) -> None:
    """Record what one finished release member left resident, and how much.

    Three records together, because none of them is attributable alone: the
    per-side retention decision (*retention*, from
    ``release_snapshot_retention.SnapshotRetention.as_counts``), a fully
    probed memory sample taken at that same moment, and the acquisition
    table's own counts -- both halves of it, the bounded id-keyed groups and
    the content-keyed raw entries.

    Lives here rather than at the release call site for two reasons: the
    fan-out is a *frontend*, and reaching into the acquisition table from
    there would be a `frontends -> storage` edge ADR-061 forbids; and this
    is instrumentation, which is this module's job and not the fan-out's.
    """
    if not memory_trace_enabled():
        return
    counts("release.member.retained", library=library, **dict(retention))
    sample("release.member.retained", library=library)
    from ..dumper_cache import ast_acquisition_stats

    stats = ast_acquisition_stats()
    if stats is not None:
        counts("release.ast_scope", library=library, **stats)
