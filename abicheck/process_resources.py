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
:mod:`abicheck.dumper_manifest`'s per-TU manifest-dump loop -- can size
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
from pathlib import Path

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
    cores (``eval/SCALING.md`` saw L4 ``jobs=8`` on 4 CPUs *regress*). An
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
    or ``None`` when RAM can't be read (the memory clamp is then skipped)."""
    avail = available_mem_gib()
    if avail is None:
        return None
    return max(1, int(avail / budget_gib))
