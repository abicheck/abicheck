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

"""Admit release fan-out members against memory, costed by their AST size.

``release_jobs.resolve_release_worker_count`` fixes the pool size up front
from a per-depth guess -- 4.0 GiB per header-depth worker. Measured on Intel
SVS, one dump's peak is 1.6-2.5x its ``clang -ast-dump=json`` document, and
that document ran from 1 GiB (one header root) to 3.5 GiB (76 roots): the
fixed figure is several times too generous for a small library, which
serializes a fan-out for nothing, and too small for a large one, which is
the overcommit the cap exists to prevent.

:class:`MemoryAdmission` replaces the guess with a measurement once one
exists. Each member runs inside :meth:`MemoryAdmission.admit`, which
observes the AST sizes that member's dumps report
(``storage.ast_size_observer``) and, when it finishes, turns them into a
cost: ``floor + PEAK_PER_AST_BYTE * sum(sizes)``. The sum, not the maximum,
because a member resolves its old and new sides concurrently by default,
so both documents can be resident at once. Every later admission is charged
the largest cost any finished member was observed to need, and waits while
the members in flight plus that cost would exceed the committable budget.

Two properties are kept deliberately:

* **Never zero in flight.** A member is always admitted when nothing else
  runs, however large its estimate -- the same "run one and let it fail on
  its own terms" floor ``release_jobs_mem_cap`` keeps.
* **Before any measurement, the old behaviour.** Until a member finishes,
  every admission is charged the per-depth default the pool was sized with,
  so the first wave is exactly what it was before this gate existed.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from ..storage.ast_size_observer import observe_ast_sizes

__all__ = ["PEAK_PER_AST_BYTE", "MemoryAdmission"]

#: Peak resident bytes per byte of AST document. Measured peak/AST was
#: 1.6-2.5x across 1-76 SVS header roots, the high end at one root, where
#: the interpreter's own baseline is a larger share -- which the per-member
#: ``floor`` already pays for, so the proportional part takes 2.0.
PEAK_PER_AST_BYTE = 2.0

_GIB = float(1 << 30)


class MemoryAdmission:
    """A counting gate over committable memory; see the module docstring.

    *committable_gib* ``None`` means memory could not be probed: every
    member is admitted at once, exactly as with no gate.
    """

    def __init__(
        self,
        committable_gib: float | None,
        *,
        default_cost_gib: float,
        floor_gib: float,
    ) -> None:
        self._committable = committable_gib
        self._default = default_cost_gib
        self._floor = floor_gib
        self._learned: float | None = None
        self._committed = 0.0
        self._in_flight = 0
        self._cond = threading.Condition()

    def estimate_gib(self) -> float:
        """What the next admission will be charged."""
        with self._cond:
            return self._estimate()

    def _estimate(self) -> float:
        return self._default if self._learned is None else self._learned

    def cost_of(self, ast_bytes: int) -> float:
        """The cost of a member whose dumps reported *ast_bytes* in total."""
        return self._floor + PEAK_PER_AST_BYTE * ast_bytes / _GIB

    @contextmanager
    def admit(self) -> Iterator[None]:
        """Block until this member fits, run it, then learn from it."""
        with self._cond:
            cost = self._estimate()
            while (
                self._committable is not None
                and self._in_flight
                and self._committed + cost > self._committable
            ):
                self._cond.wait()
                cost = self._estimate()
            self._committed += cost
            self._in_flight += 1
        observed = [0]
        observed_lock = threading.Lock()

        def _sink(nbytes: int) -> None:
            with observed_lock:
                observed[0] += nbytes

        try:
            with observe_ast_sizes(_sink):
                yield
        finally:
            with self._cond:
                self._committed -= cost
                self._in_flight -= 1
                if observed[0]:
                    measured = self.cost_of(observed[0])
                    self._learned = max(self._learned or 0.0, measured)
                self._cond.notify_all()
