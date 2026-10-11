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

"""Ask whether the snapshot cache can serve a side whole, without extracting.

The compare pipeline resolves a warm side in its own process and forks a
child only for a side that really extracts: a forked child that merely loads
a cache entry pays fork, pickle-back and a second resident copy for nothing.
Inside :func:`cache_only_probe`, ``cached_run_dump`` serves hits as usual and
raises :class:`CacheProbeMiss` at the point it would otherwise start a dump.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

__all__ = ["CacheProbeMiss", "cache_only_probe", "refuse_dump_while_probing"]


class CacheProbeMiss(BaseException):  # noqa: N818 -- a control signal, not an error
    """A dump would have to run inside :func:`cache_only_probe`.

    A ``BaseException`` so the broad ``except Exception`` fallbacks on the
    side-resolution path (export-table fallback, header-backend retries) let
    it through instead of degrading the side."""


_CACHE_ONLY: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "abicheck_dump_cache_only", default=False
)


@contextlib.contextmanager
def cache_only_probe() -> Iterator[None]:
    token = _CACHE_ONLY.set(True)
    try:
        yield
    finally:
        _CACHE_ONLY.reset(token)


def refuse_dump_while_probing() -> None:
    """Raise :class:`CacheProbeMiss` when called inside a cache-only probe."""
    if _CACHE_ONLY.get():
        raise CacheProbeMiss
