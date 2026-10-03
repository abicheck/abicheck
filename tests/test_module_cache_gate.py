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

"""Phase 4 gate: caches go through one wrapper; no module-level mutable caches.

Design-hardening plan (``docs/contribute/plans/design-hardening-from-defect-
families.md``), Phase 4. Worker functions given to a pool may read only
immutable inputs and request-scoped caches. A module-level mutable container
that functions mutate is process-global state every worker shares, so it is
rejected outside :data:`ALLOWLIST`, and every ``functools`` memo is rejected
outright: :mod:`abicheck.model.execution_cache` is the one wrapper, and it is
what honours ``ABICHECK_REFERENCE_MODE=1``.

The allowlist is stale-checked: an entry the scan no longer finds fails, so
it can only shrink by being deleted.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest
from _module_cache_gate import Finding, scan_source, scan_tree

REPO = Path(__file__).resolve().parent.parent

_PURE_CLASS_FACTS = (
    "a pure function of a class object (introspected once per type); every "
    "worker computes the identical entry, so sharing it cannot change an answer"
)
_INTERNING = (
    "interning of immutable values (flyweight Fact instances); an entry is a "
    "pure function of its key"
)

#: ``<path>::<name>`` -> why this process-global state is safe to share.
ALLOWLIST: dict[str, str] = {
    "abicheck/model/name_heuristics.py::_REGISTRY": "the name-heuristic registry: one entry per heuristic, written at import by register_name_heuristic, never per request",
    "abicheck/model/execution_cache.py::_REGISTRY": "the cache wrapper's own registry: one record per cache definition, written at import, never per request",
    "abicheck/storage/ast_cache_location.py::_REFERENCE_SCRATCH": "reference mode's scratch-directory list, kept only to delete them at exit; never read as a cache",
    "abicheck/buildsource/include_graph_workers.py::_SHARED_POOL": "the process-wide probe pool (a pool, not a cache); reference mode never uses it (an inline executor instead)",
    "abicheck/deadline.py::_active_pgroups": "Phase 6 subprocess supervisor's live process-group registry for SIGTERM cleanup; not a cache (out of Phase 4's scope)",
    "abicheck/deadline.py::_deferred_sigterm": "Phase 6 signal-handling state; not a cache",
    "abicheck/deadline.py::_spawns_in_flight": "Phase 6 signal-handling state; not a cache",
    "abicheck/deadline.py::_terminating": "Phase 6 signal-handling state; not a cache",
    "abicheck/demangle.py::_cppfilt_binary_confirmed_missing": "negative-probe latch: once c++filt is confirmed absent the fallback path is taken; the answer does not depend on the latch",
    "abicheck/demangle.py::_cxxfilt_import_confirmed_broken": "negative-probe latch for the cxxfilt binding (see _cppfilt_binary_confirmed_missing)",
    "abicheck/demangle.py::_cxxfilt_import_confirmed_missing": "negative-probe latch for the cxxfilt binding (see _cppfilt_binary_confirmed_missing)",
    "abicheck/demangle.py::_warned_no_demangler": "warn-once flag for a log line; never affects a result",
    "abicheck/dwarf_metadata.py::_SEEN_UNKNOWN_DWARF_TAGS": "warn-once set for a log line; never affects a result",
    "abicheck/model/fact.py::_FLYWEIGHT": _INTERNING,
    "abicheck/model/fact.py::_PRESENT_CONSTANTS": _INTERNING,
    "abicheck/model/fact.py::_PRESENT_ENUM": _INTERNING,
    "abicheck/model/fact.py::_VALUELESS_CONSTANTS": _INTERNING,
    "abicheck/model/plain_copy.py::_CLASS_INFO": _PURE_CLASS_FACTS,
    "abicheck/model/semantic_ir.py::_FIELD_NAMES": _PURE_CLASS_FACTS,
    "abicheck/storage/snapshot_encode.py::_FIELD_NAMES": _PURE_CLASS_FACTS,
    "abicheck/storage/snapshot_encode.py::_SORTED_FIELD_NAMES": _PURE_CLASS_FACTS,
    "abicheck/qualified_name_segments_walk.py::_COLLECT_PLAN": _PURE_CLASS_FACTS,
    "abicheck/qualified_name_segments_walk.py::_FLAG_PLAN": _PURE_CLASS_FACTS,
    "abicheck/qualified_name_segments_walk.py::_NODE_KIND": _PURE_CLASS_FACTS,
    "abicheck/qualified_name_segments_walk.py::_REWRITE_PLAN": _PURE_CLASS_FACTS,
    "abicheck/qualified_name_segments_walk.py::_WALK_PLAN": _PURE_CLASS_FACTS,
    "abicheck/policy/selectors.py::_today_cache": "date.today() re-derived whenever the clock leaves the cached day or the zone changes; identical to an uncached call by construction",
    "abicheck/storage/ast_cache_budget.py::_last_checked": "throttle for the AST-cache size sweep (when to evict); never consulted for a cache entry's content",
    "abicheck/storage/acyclic_json.py::_active": "gc-pause nesting counter around a JSON decode; not a cache",
    "abicheck/storage/acyclic_json.py::_restore_enabled": "gc state to restore when the outermost pause ends; not a cache",
    "abicheck/storage/ast_size_observer.py::_INTAKE_HOOK": "optional instrumentation hook installed by memory_trace; never affects a result",
    "abicheck/workflows/memory_trace.py::_cgroup_cache": "memory instrumentation (ABICHECK_MEMORY_TRACE): the cgroup file paths; never affects a result",
    "abicheck/workflows/memory_trace.py::_path": "memory instrumentation output path; never affects a result",
    "abicheck/workflows/memory_trace.py::_peak_high_water": "memory instrumentation counter; never affects a result",
    "abicheck/workflows/memory_trace.py::_resolved": "memory instrumentation on/off latch; never affects a result",
    "abicheck/workflows/memory_trace.py::_tracemalloc": "memory instrumentation on/off latch; never affects a result",
}


@functools.cache
def _findings() -> list[Finding]:
    return scan_tree(REPO / "abicheck", REPO)


@pytest.mark.repo_scan
def test_no_functools_memo_outside_the_wrapper() -> None:
    bad = [f"{f.path}:{f.line} {f.name}" for f in _findings() if f.rule == "functools"]
    assert not bad, (
        "use abicheck.model.execution_cache (memoized / memoized_property / "
        "MemoryCache) instead of functools:\n" + "\n".join(bad)
    )


@pytest.mark.repo_scan
def test_no_unlisted_module_level_mutable_state() -> None:
    bad = sorted(
        f"{f.key} (line {f.line})"
        for f in _findings()
        if f.rule == "module_state" and f.key not in ALLOWLIST
    )
    assert not bad, (
        "module-level mutable state mutated by a function -- route a cache "
        "through abicheck.model.execution_cache (or a request-scoped "
        "ScopedCache), or add a reasoned ALLOWLIST entry:\n" + "\n".join(bad)
    )


@pytest.mark.repo_scan
def test_allowlist_has_no_stale_entry() -> None:
    found = {f.key for f in _findings() if f.rule == "module_state"}
    stale = sorted(set(ALLOWLIST) - found)
    assert not stale, "ALLOWLIST names state the scan no longer finds:\n" + "\n".join(
        stale
    )


def test_allowlist_states_a_reason_for_every_entry() -> None:
    assert all(len(reason) > 30 for reason in ALLOWLIST.values())


# ── the scanner's own contract (independent of today's tree) ────────────────


def _keys(source: str) -> set[tuple[str, str]]:
    return {(f.rule, f.name) for f in scan_source(source, "m.py")}


@pytest.mark.parametrize(
    "mutation",
    [
        "_C[key] = 1",
        "_C.setdefault(key, 1)",
        "_C.update({key: 1})",
        "_C.pop(key, None)",
        "_C.clear()",
        "_C.append(key)",
        "_C.add(key)",
        "del _C[key]",
        "_C[key] += 1",
        "_C.move_to_end(key)",
    ],
)
@pytest.mark.parametrize(
    "init",
    [
        "{}",
        "dict()",
        "[]",
        "set()",
        "OrderedDict()",
        "collections.OrderedDict()",
        "{k: 1 for k in ()}",
    ],
)
def test_scanner_flags_every_mutation_of_every_container(
    mutation: str, init: str
) -> None:
    src = f"_C = {init}\ndef f(key):\n    {mutation}\n"
    assert ("module_state", "_C") in _keys(src)


@pytest.mark.parametrize(
    "src",
    [
        # read only
        "_C = {}\ndef f(k):\n    return _C.get(k)\n",
        # an immutable module value
        "_C = frozenset()\ndef f(k):\n    return k in _C\n",
        # a local of the same name shadows the module container
        "_C = {}\ndef f(k):\n    _C = {}\n    _C[k] = 1\n    return _C\n",
        # a parameter of the same name shadows it too
        "_C = {}\ndef f(_C, k):\n    _C[k] = 1\n",
        # mutated only at import time, not in a function
        "_C = {}\n_C['a'] = 1\n",
    ],
)
def test_scanner_does_not_flag_immutable_or_shadowed_state(src: str) -> None:
    assert not {k for k in _keys(src) if k[0] == "module_state"}


def test_scanner_flags_global_rebinding_of_any_name() -> None:
    src = "_X = None\ndef f():\n    global _X\n    _X = object()\n"
    assert ("module_state", "_X") in _keys(src)


def test_scanner_still_sees_a_global_declared_mutation_inside_a_shadowing_def() -> None:
    src = "_C = {}\ndef f(k):\n    global _C\n    _C = {}\n    _C[k] = 1\n"
    assert ("module_state", "_C") in _keys(src)


@pytest.mark.parametrize(
    "src",
    [
        "import functools\n@functools.lru_cache(maxsize=4)\ndef f(x): return x\n",
        "import functools\n@functools.cache\ndef f(x): return x\n",
        "from functools import lru_cache\n@lru_cache\ndef f(x): return x\n",
        "from functools import cached_property\nclass C:\n    @cached_property\n    def p(self): return 1\n",
        "import functools as _functools\n@_functools.lru_cache(maxsize=None)\ndef f(x): return x\n",
    ],
)
def test_scanner_flags_every_functools_memo_spelling(src: str) -> None:
    assert any(rule == "functools" for rule, _ in _keys(src))


def test_scanner_ignores_other_functools_names() -> None:
    src = "from functools import partial, wraps\nimport functools\nfunctools.reduce(max, [1])\n"
    assert not _keys(src)
