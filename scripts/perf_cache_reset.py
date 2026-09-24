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

"""Cold-cache reset for ``benchmark_scaling.py``'s memory passes.

Split out to keep that file under the file-size gate's hard cap.
"""

from __future__ import annotations

import functools
import gc
import sys


def _lru_cache_candidates(*, heap_census: bool) -> list[object]:
    """Objects that may be ``lru_cache`` wrappers.

    The whole heap when that is safe. A heap census beside another live
    thread breaks that thread's ``tuple(...)`` construction
    (``memory_trace.gc_census_is_safe``), so with other threads alive this
    falls back to every ``abicheck`` module's globals and their classes'
    ``__dict__`` values. Every decorated function and method lives there;
    only a cache created inside a closure is missed.
    """
    if heap_census:
        return gc.get_objects()
    found: list[object] = []
    for name, module in list(sys.modules.items()):
        if name != "abicheck" and not name.startswith("abicheck."):
            continue
        for value in list(getattr(module, "__dict__", {}).values()):
            found.append(value)
            if isinstance(value, type):
                found.extend(list(value.__dict__.values()))
    return found


def clear_process_caches() -> None:
    """Clear process-wide caches so the memory pass measures a *cold* run.

    Some scenarios fill input-scaled process-wide caches during the timing loop
    (e.g. the ``functools.lru_cache`` demanglers in ``abicheck/demangle.py``).
    Those allocations happen *before* ``tracemalloc`` starts, so a warm memory
    pass would not see them and cache-driven space growth could slip past the
    ``--max-memory-mb`` gate (Codex review, #336). Clearing every live
    ``lru_cache`` (plus the demangle batch cache) before the traced run forces
    it to repopulate them, so their allocation is counted and each size is
    measured from the same cold baseline.

    Caches are found via an ``isinstance`` check against the ``lru_cache``
    wrapper type — obtained without naming the private ``functools`` symbol by
    taking ``type()`` of a throwaway cache. ``isinstance`` is used rather than
    duck-typing ``getattr(obj, "cache_clear")`` because a bare ``getattr`` scan
    over every live object would trigger side effects on objects with a dynamic
    ``__getattr__`` (e.g. pytest's mark objects synthesise attributes on access).
    """
    from abicheck.workflows.memory_trace import gc_census_is_safe

    lru_type = type(functools.lru_cache(maxsize=1)(lambda: None))
    for obj in _lru_cache_candidates(heap_census=gc_census_is_safe()):
        if isinstance(obj, lru_type):
            try:
                obj.cache_clear()
            except Exception:  # noqa: BLE001 — best-effort cache reset  # nosec B110
                pass
    try:
        from abicheck.demangle import _reset_demangle_batch_cache

        _reset_demangle_batch_cache()
    except Exception:  # noqa: BLE001 — optional internal helper  # nosec B110
        pass
