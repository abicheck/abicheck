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

"""Progress lines for the long phases of a run (header AST, DWARF, L4 replay).

A ``dump`` of a large library can spend many minutes inside one phase -- a
pool of clang source-replay jobs, a per-TU header parse -- with nothing
written anywhere, so an operator cannot tell a slow run from a hung one.
This module is the one place those phases report from.

It is deliberately only a *logger* (``abicheck.progress``), never a writer of
its own: an engine or typed-API caller that configures no logging hears
nothing, and stdout -- where ``dump``/``compare`` write their JSON -- is never
touched. The CLI turns it on in ``frontends.cli.runtime._setup_verbosity``
(by logger name -- ``frontends`` may not import ``extract``),
which routes it to stderr; ``ABICHECK_PROGRESS=0`` turns it off.

Two shapes, both cheap when disabled (one ``isEnabledFor`` check):

* :func:`phase` / :func:`timed` -- a start line and a finish line with the
  elapsed time, for a single long step.
* :func:`track` -- wraps an iterable of *total* work items and reports
  ``label: i/total``, throttled to one line per :data:`TICK_INTERVAL_S`
  (plus the last item), so a 10,000-unit pool writes a few dozen lines, not
  ten thousand.

Stdlib only. Lives in ``extract`` because every emitter does (the header
AST, DWARF and build/source collection stages); ``workflows`` may import it
too.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import TypeVar

__all__ = [
    "LOGGER_NAME",
    "PROGRESS_ENV",
    "TICK_INTERVAL_S",
    "enabled_by_env",
    "phase",
    "timed",
    "track",
]

LOGGER_NAME = "abicheck.progress"
PROGRESS_ENV = "ABICHECK_PROGRESS"
#: Minimum seconds between two ``track`` lines for the same loop.
TICK_INTERVAL_S = 5.0

_log = logging.getLogger(LOGGER_NAME)
_T = TypeVar("_T")
_F = TypeVar("_F", bound=Callable[..., object])


def enabled_by_env() -> bool:
    """``False`` only when ``ABICHECK_PROGRESS`` is set to an off value."""
    from .env_flags import env_flag

    return env_flag(PROGRESS_ENV)


def _enabled() -> bool:
    """Emit when the front end asked for it: its logger at DEBUG (``-v``)
    always, at INFO (the CLI default) unless ``ABICHECK_PROGRESS`` is off.
    A library caller that configured no logging stays at WARNING: silent."""
    if _log.isEnabledFor(logging.DEBUG):
        return True
    return _log.isEnabledFor(logging.INFO) and enabled_by_env()


@contextmanager
def phase(label: str) -> Iterator[None]:
    """Report *label* starting, then finishing (or failing) with its duration."""
    if not _enabled():
        yield
        return
    start = time.monotonic()
    _log.info("%s ...", label)
    try:
        yield
    except BaseException:
        _log.info("%s failed after %.1fs", label, time.monotonic() - start)
        raise
    _log.info("%s done (%.1fs)", label, time.monotonic() - start)


def timed(label: str) -> Callable[[_F], _F]:
    """Decorator form of :func:`phase` for a function that *is* the phase."""

    def wrap(fn: _F) -> _F:
        @functools.wraps(fn)
        def inner(*args: object, **kwargs: object) -> object:
            with phase(label):
                return fn(*args, **kwargs)

        return inner  # type: ignore[return-value]

    return wrap


def track(items: Iterable[_T], label: str, total: int) -> Iterator[_T]:
    """Yield *items* unchanged, reporting ``label: i/total`` as they arrive.

    Counts items as the caller consumes them, so wrapping a pool's
    ``map``/``as_completed`` reports completed work, not submitted work.
    """
    if total <= 1 or not _enabled():
        yield from items
        return
    start = last = time.monotonic()
    _log.info("%s: 0/%d", label, total)
    done = 0
    for item in items:
        yield item
        done += 1
        now = time.monotonic()
        if done == total or now - last >= TICK_INTERVAL_S:
            last = now
            _log.info("%s: %d/%d (%.0fs)", label, done, total, now - start)
