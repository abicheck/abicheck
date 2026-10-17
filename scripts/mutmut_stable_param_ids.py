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

"""pytest plugin for the mutation lane: parametrize ids that stay the same across
in-process sessions, and cheapest-tests-first order during a mutant run.

mutmut 3 runs several pytest sessions in one process: the stats pass records
every test's node id, then the clean run and the per-mutant runs (forked from
that process) select tests by those ids. pytest caches the ids it generated
for a ``parametrize`` mark on the mark itself (``_param_ids_generated``) and,
in the next session, feeds them back through id resolution as if they were
user-given explicit ids -- so any explicit id containing a character pytest
escapes (a backslash, a newline, non-ASCII) is escaped once more per
session: ``x[a\\b]`` is recorded, ``x[a\\\\b]`` is collected. pytest then
reports the recorded id as not found, exits 4, and mutmut aborts the lane
(``BadTestExecutionCommandsException`` in "Running clean tests"; CI run
37077168345, ``tests/test_project_package_archive.py``'s
``refs\\artifacts\\x.json`` id). Auto-generated ids are not affected.

Escaping is applied by ``_resolve_parameter_set_ids``, so the cached value V
is exactly ``escape(raw ids)``, and escaping is invertible. The first time a
later session meets a mark, the plugin hands pytest ``unescape(V)`` (each
entry verified by re-escaping it with pytest's own function) and restores V
once that function is parametrized. Every later session therefore follows
the first session's path step for step -- including pytest's own spelling
for the second function a shared mark reaches, which the first session
already escapes twice -- for every way ``ids`` can be given, a one-shot
generator included. Nothing is changed in the first session. Loaded only by
``[tool.mutmut].pytest_add_cli_args`` (``-p scripts.mutmut_stable_param_ids``;
mutmut puts ``mutants/`` on ``sys.path`` and ``scripts/`` is in
``also_copy``). It changes no test's selection or outcome, only which spelling
a later session gives its id.

Second job: mutmut runs each mutant's tests with ``pytest -x``, so a killed
mutant stops at its first failing test. During a mutant run (``MUTANT_UNDER_TEST``
names a mutant, not the clean/stats/fail sentinels) the plugin stably sorts the
collected items by the ``duration_by_test`` recorded in ``mutmut-stats.json``
(cwd ``mutants/``), unknown durations last. The order of the verdicts is
unchanged; only the cost of killed mutants drops. A missing or unreadable
stats file leaves the order alone.

Third job: the same mutant run also deselects every test whose recorded
duration is at least ``SLOW_TEST_CUTOFF_SECONDS`` (1.0 s; override with the
``MUTMUT_SLOW_TEST_CUTOFF`` environment variable, a value <= 0 disables it).
Measured on 102 killed mutants (14 functions, seed 7, all 429 killed keys
sampled under a per-function cap): running only tests under 1.0 s kept all
102 kills (0 lost, 95% Wilson CI 0-3.6%), and the summed ``-x`` runtime fell
from 7402 s to 3392 s. Tests with an unknown duration are never deselected.
If the cutoff would leave no test, the full sorted set runs instead, so a
mutant never runs with zero tests. Deselected items are reported through
``pytest_deselected``, so pytest's own accounting still sees them. Clean,
stats and listing runs, and a missing or unreadable stats file, are untouched.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Generator, Sequence
from pathlib import Path

import pytest

_escape: Callable[[str, pytest.Config | None], str] | None
try:  # pytest's own escaping, used to verify each inverse exactly
    from _pytest.python import _ascii_escaped_by_config as _escape
except ImportError:  # pragma: no cover - only on an unsupported pytest
    _escape = None

_SEEN = pytest.StashKey[set[int]]()

# mutmut's sentinels for the non-mutant runs (see mutmut's __main__.py and
# mutation/trampoline.py); "" is the clean run.
_NOT_A_MUTANT = frozenset({"", "fail", "stats", "list_all_tests", "mutant_generation"})
_STATS_FILE = "mutmut-stats.json"
_DURATIONS: dict[Path, dict[str, float]] = {}
# Measured cutoff (see the module docstring); overridden by _CUTOFF_ENV.
SLOW_TEST_CUTOFF_SECONDS = 1.0
_CUTOFF_ENV = "MUTMUT_SLOW_TEST_CUTOFF"


def load_durations(path: Path) -> dict[str, float]:
    """``duration_by_test`` from mutmut's stats file, cached per path for the
    process. A missing or unreadable file yields no durations."""
    key = path.absolute()
    if key not in _DURATIONS:
        try:
            raw = json.loads(key.read_text(encoding="utf-8"))["duration_by_test"]
            _DURATIONS[key] = {
                str(k): float(v)
                for k, v in raw.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            _DURATIONS[key] = {}
    return _DURATIONS[key]


def slow_test_cutoff() -> float:
    """Seconds from ``MUTMUT_SLOW_TEST_CUTOFF``; the default when it is unset or
    not a number. A value <= 0 disables the cutoff."""
    raw = os.environ.get(_CUTOFF_ENV, "").strip()
    if not raw:
        return SLOW_TEST_CUTOFF_SECONDS
    try:
        return float(raw)
    except ValueError:
        return SLOW_TEST_CUTOFF_SECONDS


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Cheapest tests first, so a killed mutant's ``-x`` run stops early; then
    drop the tests recorded as slower than the cutoff (never an unknown one)."""
    if os.environ.get("MUTANT_UNDER_TEST", "") in _NOT_A_MUTANT:
        return
    durations = load_durations(Path(_STATS_FILE))
    if not durations:
        return
    # list.sort is stable; unknown durations (inf) keep their order at the end.
    items.sort(key=lambda item: durations.get(item.nodeid, math.inf))
    cutoff = slow_test_cutoff()
    if cutoff <= 0:
        return
    fast: list[pytest.Item] = []
    slow: list[pytest.Item] = []
    for item in items:
        duration = durations.get(item.nodeid)
        if duration is not None and duration >= cutoff:
            slow.append(item)
        else:
            fast.append(item)
    # Never leave a mutant with zero tests: keep the full sorted set instead.
    if not slow or not fast:
        return
    items[:] = fast
    config.hook.pytest_deselected(items=slow)


def unescape_ids(
    ids: Sequence[object],
    escape: Callable[[str, pytest.Config | None], str] | None,
    config: pytest.Config | None,
) -> list[str] | None:
    """The raw ids pytest escaped into *ids*, or None if any entry is not
    exactly invertible (then pytest's own behaviour is left untouched)."""
    if escape is None:
        return None
    raw = []
    for value in ids:
        if not isinstance(value, str) or not value.isascii():
            return None
        try:
            candidate = value.encode("ascii").decode("unicode_escape")
        except UnicodeDecodeError:  # e.g. a lone trailing backslash
            return None
        if escape(candidate, config) != value:
            return None
        raw.append(candidate)
    return raw


@pytest.hookimpl(hookwrapper=True)
def pytest_generate_tests(metafunc: pytest.Metafunc) -> Generator[None]:
    seen = metafunc.config.stash.setdefault(_SEEN, set())
    restore = []
    for mark in metafunc.definition.iter_markers(name="parametrize"):
        origin = getattr(mark, "_param_ids_from", None)
        if origin is None or id(origin) in seen:
            continue
        seen.add(id(origin))
        cached = getattr(origin, "_param_ids_generated", None)
        if cached is None:  # first session: nothing cached, nothing to undo
            continue
        raw = unescape_ids(cached, _escape, metafunc.config)
        if raw is None:
            continue
        # Mark is a frozen dataclass; pytest writes this field the same way
        # (_pytest/python.py, Metafunc.parametrize).
        object.__setattr__(origin, "_param_ids_generated", raw)
        restore.append((origin, cached))
    try:
        yield
    finally:
        for origin, cached in restore:
            object.__setattr__(origin, "_param_ids_generated", cached)
