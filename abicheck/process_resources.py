# Copyright 2026 Nikolay Petrov
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

"""Cross-process RAM-probing and pool-sizing primitives (ADR-050 D6, G32
Phase E).

Factored out of :mod:`abicheck.buildsource.source_replay`'s L4 worker-sizing
logic so a *second* concurrent per-process pool --
:mod:`abicheck.extract.headers.manifest`'s per-TU manifest-dump loop -- can size
itself off the same host/cgroup memory-headroom probe instead of a second,
independently-maintained copy: "move shared logic to a leaf module both
sides can depend on" (root ``AGENTS.md``'s import-cycle guidance).

A leaf module: no imports from anywhere else in this package, so both
``buildsource/source_replay.py`` and the top-level ``dumper_manifest.py``
can depend on it without risking a cycle either way.

This is a pure relocation of already-proven policy (G32 Phase E's own
"no new scheduling policy" note) -- the actual sizing decisions
(``jobs_ceiling``/``job_mem_budget_gib``/``mem_cap``) are unchanged from
``source_replay.py``'s original ``_l4_*`` implementation, just parameterized
so a caller supplies its own env-var name/default budget instead of the
hard-coded ``ABICHECK_L4_*`` ones. ``source_replay.py`` keeps its own
``_l4_jobs``/``_l4_jobs_ceiling``/``_l4_job_mem_budget_gib``/``_l4_mem_cap``/
``_l4_available_mem_gib`` wrapper functions (unchanged names, unchanged log
messages) delegating into this module, so every existing ``ABICHECK_L4_*``
caller and test keeps working unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from .model.execution_cache import reference_mode

_T = TypeVar("_T")

_KIB = 1024.0
_GIB = 1024.0 * 1024.0 * 1024.0

#: cgroup memory accounting -- see ``source_replay.py``'s original docstring
#: for the full "why" (a process confined to a cgroup limit below host RAM
#: must size off the tighter of the two, or a container on a big host still
#: gets OOM-killed). The *effective* limit lives at the process's own cgroup
#: path (from ``/proc/self/cgroup``), not the controller root -- under a
#: nested cgroup (k8s pod / systemd slice / CI runner) the root is often
#: unbounded while a parent slice imposes the real cap -- so the walk goes
#: leaf->root and takes the tightest bounded limit. Module constants (not
#: inlined) so tests can repoint them.
_PROC_SELF_CGROUP = "/proc/self/cgroup"
_CGROUP_V2_ROOT = "/sys/fs/cgroup"  # unified-hierarchy mount
_CGROUP_V1_ROOT = "/sys/fs/cgroup/memory"  # v1 memory-controller mount
#: cgroup v1 reports "unlimited" as a near-INT64_MAX sentinel rather than a
#: keyword; anything at/above this is treated as no limit.
_CGROUP_V1_UNLIMITED = 1 << 62


def _read_int_file(path: str) -> int | None:
    """Read a single integer from ``path`` (cgroup files), or ``None``.

    Returns ``None`` for a missing/unreadable file or a non-integer body such
    as cgroup v2's literal ``max`` (= unbounded), which callers treat as "no
    cgroup limit".
    """
    try:
        with open(path, encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _cgroup_rel_paths() -> tuple[str | None, str | None]:
    """``(v2_rel, v1_memory_rel)`` cgroup paths from ``/proc/self/cgroup``.

    Each is ``None`` when that hierarchy isn't listed (e.g. a pure-v2 host
    has no v1 memory line). The v2 line is ``0::/rel``; the v1 memory line is
    ``N:...,memory,...:/rel``.
    """
    v2 = v1 = None
    try:
        # ``errors="replace"`` (and the ValueError guard) keeps a non-ASCII
        # systemd slice / container name in the cgroup path from raising
        # UnicodeDecodeError mid-iteration and aborting the caller -- this
        # probe is best-effort and must degrade to ``None``.
        with open(_PROC_SELF_CGROUP, encoding="ascii", errors="replace") as fh:
            for line in fh:
                parts = line.rstrip("\n").split(":", 2)
                if len(parts) != 3:
                    continue
                hid, controllers, path = parts
                if hid == "0":
                    v2 = path
                elif "memory" in controllers.split(","):
                    v1 = path
    except (OSError, ValueError):
        pass
    return v2, v1


def _cgroup_chain(root: str, rel: str | None) -> list[Path]:
    """Cgroup dirs from the leaf (``root``/``rel``) up to ``root``, leaf first."""
    base = Path(root)
    chain = [base]
    cur = base
    for part in (rel or "").strip("/").split("/"):
        if part:
            cur = cur / part
            chain.append(cur)
    chain.reverse()
    return chain


def _cgroup_headroom_gib(
    root: str, rel: str | None, max_name: str, cur_name: str, unlimited: int | None
) -> float | None:
    """Tightest memory headroom (GiB) along the leaf->root cgroup chain, or
    ``None``.

    A bounded ancestor can cap a process more tightly than its own leaf
    cgroup, so the effective headroom is the *minimum* across the chain.
    ``None`` when no level is bounded.
    """
    best: float | None = None
    for d in _cgroup_chain(root, rel):
        limit = _read_int_file(str(d / max_name))
        if limit is None or (unlimited is not None and limit >= unlimited):
            continue
        used = _read_int_file(str(d / cur_name)) or 0
        headroom = max(0.0, (limit - used) / _GIB)
        best = headroom if best is None else min(best, headroom)
    return best


def cgroup_available_mem_gib() -> float | None:
    """Container memory headroom in GiB from cgroup limits, or ``None``.

    Resolves the process's own cgroup (``/proc/self/cgroup``) and walks
    leaf->root for the tightest bounded limit -- cgroup v2
    (``memory.max`` - ``memory.current``) then v1
    (``memory.limit_in_bytes`` - ``memory.usage_in_bytes``). ``None`` when
    nothing is bounded (the common bare-metal/host case).
    """
    v2_rel, v1_rel = _cgroup_rel_paths()
    headroom = _cgroup_headroom_gib(
        _CGROUP_V2_ROOT, v2_rel, "memory.max", "memory.current", None
    )
    if headroom is not None:
        return headroom
    return _cgroup_headroom_gib(
        _CGROUP_V1_ROOT,
        v1_rel,
        "memory.limit_in_bytes",
        "memory.usage_in_bytes",
        _CGROUP_V1_UNLIMITED,
    )


def meminfo_available_gib(path: str = "/proc/meminfo") -> float | None:
    """Host ``MemAvailable`` in GiB (Linux ``/proc/meminfo``), or ``None``."""
    try:
        with open(path, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * _KIB / _GIB  # kB -> GiB
    except (OSError, ValueError, IndexError):
        pass
    return None


def available_mem_gib() -> float | None:
    """Best-effort available RAM in GiB, honouring cgroup limits in containers.

    Returns the *smaller* of host ``MemAvailable`` and the cgroup memory
    headroom so a process confined to a small cgroup on a large host still
    sizes its worker count to what it is actually allowed to use. ``None``
    when neither source is readable (non-Linux / sandbox), which skips the
    memory clamp entirely.
    """
    candidates = [
        v
        for v in (meminfo_available_gib(), cgroup_available_mem_gib())
        if v is not None
    ]
    return min(candidates) if candidates else None


def jobs_ceiling(*, floor: int = 8, cpu_multiplier: int = 2) -> int:
    """Hard oversubscription ceiling for a worker-count clamp.

    Each worker drives a heavyweight clang/castxml process (one TU,
    single-threaded); past ~2x the CPU count the processes only contend for
    cores (``skills-src/evaluation/field/SCALING.md`` saw L4 ``jobs=8`` on 4 CPUs *regress*). An
    explicit env-var override is still clamped to this so a stray large
    value can't thrash the host.
    """
    return max(floor, cpu_multiplier * (os.cpu_count() or 1))


def job_mem_budget_gib(env_var: str, default_gib: float) -> float:
    """Per-worker RAM budget (GiB), floored at 0.25 GiB.

    ``env_var`` overrides *default_gib* when set to a parseable float; an
    unparsable value falls back to the default.
    """
    try:
        return max(0.25, float(os.environ.get(env_var) or default_gib))
    except ValueError:
        return default_gib


def mem_cap(budget_gib: float) -> int | None:
    """Max concurrent workers that fit in available RAM at *budget_gib* each,
    or ``None`` when RAM can't be read (the memory clamp is then skipped).

    *budget_gib* is expected positive (:func:`job_mem_budget_gib` floors it at
    0.25) -- a non-positive value would otherwise raise ``ZeroDivisionError``
    or invert the clamp's meaning, so it is treated the same as "can't be
    read" here rather than trusted unconditionally (CodeRabbit review).
    """
    if budget_gib <= 0:
        return None
    avail = available_mem_gib()
    if avail is None:
        return None
    return max(1, int(avail / budget_gib))


# ---------------------------------------------------------------------------
# Concurrency policy: how much *Python* work can actually run at once.
#
# Every pool above sizes itself off CPUs and RAM, which is right for a worker
# whose cost is a child process (clang, castxml): those run in parallel
# whatever the interpreter does. It is wrong for a worker whose cost is
# Python itself -- parsing an AST document, building the model, comparing,
# rendering. Under the GIL such workers serialize, so a release fan-out of 28
# members measured ~100% CPU while holding 28 members' working sets resident
# at once. On a free-threaded build (PEP 703, ``python3.xt``) the same
# workers really do scale, and memory becomes the only limit.
#
# So the number of concurrently *running* Python-heavy workers is a property
# of the interpreter, decided here, in one place, rather than guessed at each
# pool. Two levels, deliberately separate:
#
# * level 1 -- Python-heavy units (release members): :func:`python_parallelism`;
# * level 2 -- child-process units (``clang -M`` probes and friends): the
#   CPU/RAM sizing above, bounded process-wide by the caller's own gate.
# ---------------------------------------------------------------------------

#: Override for level 1: how many Python-heavy units (release members) may run
#: at once. ``0``/unset takes the interpreter-derived default; an unparsable
#: value is ignored. The one knob for this level -- add to it, not beside it.
MEMBER_JOBS_ENV_VAR = "ABICHECK_MEMBER_JOBS"

#: Level-1 default while the GIL is enabled. Not 1: while one member's Python
#: holds the GIL, another member's clang child (``-M`` probe, AST dump) still
#: runs, so a second member overlaps the child-process phases for the cost of
#: one extra working set. Beyond two, measured wall time was flat and resident
#: memory grew linearly (28 concurrent members vs. batches: ~10x peak RSS for
#: no speedup).
GIL_MEMBER_PARALLELISM = 2

#: Level-1 default on a free-threaded interpreter, as a ceiling on the CPU
#: count. Not the CPU count itself: members share caches, the include memo
#: and the AST acquisition table, and contend on them. Measured on a
#: 28-member release on a 224-core host (python3.14t), wall time was flat
#: from 4 to 96 members (17:12 / 17:58 / 18:14) while CPU seconds grew 6.5x
#: (5 191 -> 33 869) and peak RSS 4-7x -- past ~4 the extra members only
#: contend. Still 2.9x faster than the GIL default on that release.
FREE_THREADED_MEMBER_PARALLELISM = 4


def gil_enabled() -> bool:
    """Whether this interpreter serializes Python bytecode on a GIL.

    ``sys._is_gil_enabled`` exists from 3.13; on a free-threaded build it
    answers ``False`` unless the GIL was re-enabled at runtime (``PYTHON_GIL=1``
    or an extension module that does not declare free-threading support), in
    which case the conservative answer is the right one. Older interpreters
    always have a GIL.
    """
    import sys

    probe = getattr(sys, "_is_gil_enabled", None)
    return True if probe is None else bool(probe())


def python_parallelism() -> int:
    """How many Python-heavy units may run at once (>= 1).

    ``ABICHECK_MEMBER_JOBS`` when set to a positive integer (clamped to
    :func:`jobs_ceiling`, like every other override); otherwise
    :data:`GIL_MEMBER_PARALLELISM` under the GIL and, on a free-threaded
    interpreter, the CPU count capped at
    :data:`FREE_THREADED_MEMBER_PARALLELISM`. Memory is *not* considered here -- the caller's
    memory admission is what bounds a level-1 pool by RAM; this bounds it by
    what can actually execute. An unparsable override falls back to that
    default silently, as every other sizing variable here does.
    """
    raw = os.environ.get(MEMBER_JOBS_ENV_VAR, "").strip()
    if raw:
        try:
            requested = int(raw)
        except ValueError:
            requested = 0
        if requested > 0:
            return max(1, min(requested, jobs_ceiling()))
    if gil_enabled():
        return GIL_MEMBER_PARALLELISM
    return max(1, min(os.cpu_count() or 1, FREE_THREADED_MEMBER_PARALLELISM))


# ---------------------------------------------------------------------------
# One process-wide thread budget.
#
# Every pool above is sized on its own -- members, the two sides of a member,
# include probes, per-TU header parses, L4/L5 build-source passes -- and they
# nest, so their product, not any one of them, is what the process runs.
# ``ABICHECK_MAX_THREADS`` bounds that product: each pool borrows its worker
# threads from one budget when it is created and returns them when it shuts
# down. The budget is the *only* total cap; the per-pool knobs still decide how
# much of it a pool asks for.
#
# Borrowing never blocks. A pool created while the budget is spent is granted
# fewer threads than it asked for, down to none, and a pool granted none runs
# each task inline in the thread that submits it. Blocking instead would
# deadlock: pools nest, so an outer worker holding its thread would wait for
# threads only its own inner pool could release. Inline is always safe here
# because no abicheck pool submits a task that waits on a later task of the
# same pool -- every pool maps independent units.
# ---------------------------------------------------------------------------

#: Process-wide cap on worker threads held by abicheck pools at once. Unset,
#: ``0`` or unparsable means unlimited (each pool's own sizing alone).
MAX_THREADS_ENV_VAR = "ABICHECK_MAX_THREADS"


def max_threads() -> int | None:
    """The configured thread budget, or ``None`` when unlimited.

    ``ABICHECK_REFERENCE_MODE=1`` forces ``1`` (and :class:`BudgetedExecutor`
    then grants no worker thread at all), so every pool runs its tasks
    inline and the release fan-out takes its sequential member path.
    """
    if reference_mode():
        return 1
    raw = os.environ.get(MAX_THREADS_ENV_VAR, "").strip()
    try:
        value = int(raw) if raw else 0
    except ValueError:
        return None
    return value if value > 0 else None


class _ThreadBudget:
    """Counts worker threads currently lent to pools (see the section note)."""

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self.in_use = 0
        self.peak = 0

    def grant(self, requested: int) -> int:
        if reference_mode():
            return 0  # inline in the submitting thread: the sequential path
        cap = max_threads()
        with self._lock:
            granted = (
                requested if cap is None else max(0, min(requested, cap - self.in_use))
            )
            self.in_use += granted
            self.peak = max(self.peak, self.in_use)
            return granted

    def release(self, granted: int) -> None:
        with self._lock:
            self.in_use -= granted


#: The one budget for the process.
THREAD_BUDGET = _ThreadBudget()


class InlineExecutor(Executor):
    """Runs each task at submission, in the submitting thread."""

    def submit(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> Future[_T]:
        future: Future[_T] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - delivered via the future
            future.set_exception(exc)
        return future


class BudgetedExecutor(Executor):
    """A thread pool of up to *requested* workers, borrowed from the budget.

    Behaves like ``ThreadPoolExecutor(max_workers=requested)`` -- including as
    a context manager -- except that it holds only the threads
    :data:`THREAD_BUDGET` grants it (:attr:`granted_threads`), running tasks
    inline when granted none. The grant is returned by the first ``shutdown``;
    a caller that shuts down with ``wait=False`` therefore returns threads that
    may still be finishing, a brief overshoot accepted on that (failure) path
    only.
    """

    def __init__(self, requested: int, *, thread_name_prefix: str = "") -> None:
        import threading

        self.granted_threads = THREAD_BUDGET.grant(max(1, requested))
        self._inner: Executor = (
            ThreadPoolExecutor(
                max_workers=self.granted_threads,
                thread_name_prefix=thread_name_prefix,
            )
            if self.granted_threads
            else InlineExecutor()
        )
        self._lock = threading.Lock()
        self._released = False

    def submit(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> Future[_T]:
        return self._inner.submit(fn, *args, **kwargs)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self._inner.shutdown(wait=wait, cancel_futures=cancel_futures)
        with self._lock:
            if self._released:
                return
            self._released = True
        THREAD_BUDGET.release(self.granted_threads)
