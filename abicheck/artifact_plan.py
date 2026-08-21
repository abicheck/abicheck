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

"""``ResolvedArtifactPlan`` — Phase 1 (Milestone A) of the
duplication-and-convergence plan's "Finish artifact-resolution convergence"
work (``docs/contribute/plans/duplication-and-convergence-assessment.md``).

**Scope of this module.** The plan's Phase 1 item 1 asks for a
context-managed session that owns any resource an artifact-resolution
attempt allocates (an inferred-build temp directory, most concretely) from
resolution onward — spanning through whichever of execution or dry-run
inspection follows, not scoped to either alone. Today, every call site that
allocates such a resource (``service_input_resolution._resolve_side_snapshot_impl``,
``cli_dump_helpers.perform_elf_dump``/``handle_non_elf_dump``) threads a
plain ``list[Callable[[], None]]`` accumulator by hand and drains it in its
own ``finally``, strictly before returning anything to its caller — there is
no reusable, tested primitive. This module is that primitive, generalized
just enough to replace one such hand-rolled accumulator
(``cli_dump_helpers.perform_elf_dump``'s own ``_l2_pending_cleanups``) with a
single, reviewed implementation; migrating every other call site named in
the plan is deliberately out of scope for this first slice — see the plan
doc's own Phase 1 section for the full, still-open list.

Deliberately a leaf, engine-layer module: no ``click``/``cli_*`` import (the
``engine-cli-boundary`` AI-readiness gate would reject one), no dependency on
``service_dump_pipeline.py``/``service_input_resolution.py`` — those keep
their own existing dataclasses unchanged in this pass.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from types import TracebackType

logger = logging.getLogger(__name__)


class ResolvedArtifactPlan:
    """A context-managed session owning the cleanup thunks an
    artifact-resolution attempt accumulates.

    Mirrors the existing ``pending_cleanups``/``defer_cleanup`` accumulator
    pattern used throughout ``buildsource/`` and ``service_input_resolution.py``
    (a plain ``list[Callable[[], None]]``, appended to as resolution
    allocates resources) — but as a reusable, independently-tested type
    instead of a hand-rolled local list plus a manual ``try``/``finally`` at
    every call site.

    Usage — the exact shape the plan's own "Lifetime problem" section
    specifies (``with resolve...() as plan: ... if dry_run: return
    render(plan) ... with execute...(plan) as result: ...``), collapsed here
    to what one call site (``perform_elf_dump``) actually needs today: a
    single ``with`` spanning resolution through execution::

        with ResolvedArtifactPlan() as plan:
            includes, ctx = seed_includes_and_fold_compile_context(
                ..., pending_cleanups=plan.pending_cleanups,
            )
            snap = dump(...)  # the real extraction
        # plan.__exit__ has now run every collected cleanup exactly once.

    A cleanup registered via :attr:`pending_cleanups` (the list itself,
    handed to a callee the same way an existing bare list is today) or via
    :meth:`add_cleanup` (for a cleanup only known after construction, e.g.
    one discovered during execution rather than resolution) is run on
    ``__exit__``, in registration order, exactly once — a second ``__exit__``
    (or an explicit :meth:`run_cleanups` call after nothing new was
    registered) is a safe no-op, not a double-run. Registering a cleanup
    *after* a drain already ran — a plan re-entered via a second ``with``
    block, or ``add_cleanup()`` called on an already-closed plan — is not a
    leak either (Codex review: an earlier revision used a single ``_closed``
    flag that made every drain after the first a no-op unconditionally, so a
    cleanup registered post-close was silently never run): draining tracks
    how much of :attr:`pending_cleanups` has actually been run, not merely
    whether a drain has ever happened, so the next :meth:`run_cleanups` call
    (explicit, or via a later ``with`` exit) still picks up and runs exactly
    the cleanups appended since the previous drain.

    **Partial-resolution failure.** The plan's own text calls out a failure
    mode this type must not reproduce: if the code building a
    ``ResolvedArtifactPlan``-like object fails *after* allocating a resource
    but *before* returning an object a ``with`` block can call
    ``__exit__`` on, that resource leaks — the caller never receives
    anything to close. This type is agnostic to *where* it is constructed
    (a caller doing all its own allocation before constructing one is still
    exposed to that failure mode) — the safe pattern is to construct the
    plan *first*, and register each cleanup via :attr:`pending_cleanups` or
    :meth:`add_cleanup` as soon as it is known, inside the same ``with``
    block whose ``__exit__`` will run it regardless of what happens later in
    that block (see the module-level usage example above): any exception
    raised later in that same ``with`` body still reaches ``__exit__`` and
    still drains everything collected up to that point, because
    ``pending_cleanups`` is mutated in place rather than being handed over
    only at the end.
    """

    __slots__ = ("pending_cleanups", "_drained_count")

    def __init__(self) -> None:
        #: Handed directly to a callee that expects the existing bare
        #: ``pending_cleanups: list[Callable[[], None]]`` parameter shape
        #: (``seed_includes_and_fold_compile_context``, ``collect_inline_pack``'s
        #: ``defer_cleanup``) — appended to in place, the same contract those
        #: functions already document.
        self.pending_cleanups: list[Callable[[], None]] = []
        #: How many leading entries of ``pending_cleanups`` have already been
        #: run. A count, not a bool -- so a cleanup appended after a drain
        #: (post-close ``add_cleanup()``, or a second ``with`` re-entry) is
        #: still reachable by the *next* drain instead of being silently
        #: skipped by an unconditional "already closed" flag.
        self._drained_count = 0

    def add_cleanup(self, cleanup: Callable[[], None]) -> None:
        """Register a cleanup discovered after construction (e.g. during
        execution rather than resolution, or after an earlier drain already
        ran). Equivalent to ``self.pending_cleanups.append(cleanup)`` —
        provided as a named method so a caller need not reach into the list
        directly."""
        self.pending_cleanups.append(cleanup)

    def run_cleanups(self) -> None:
        """Run every cleanup registered since the last drain exactly once,
        in registration order, never letting one failure skip the rest —
        the identical contract ``buildsource.inline._run_cleanups`` already
        establishes for the same class of thunk (a failure is logged, not
        raised, since a temp tree already gone or a read-only mount must not
        abort an otherwise-successful extraction). Idempotent when nothing
        new was registered: a second call with no new cleanups is a no-op,
        so an explicit call here followed by context-manager ``__exit__``
        (or two nested ``with`` exits on the same instance) never double-runs
        a cleanup — but a cleanup registered *after* the previous drain
        (post-close ``add_cleanup()``, or a plan re-entered via a second
        ``with`` block) still runs on this call rather than being dropped.
        """
        pending = self.pending_cleanups[self._drained_count :]
        self._drained_count = len(self.pending_cleanups)
        for cleanup in pending:
            try:
                cleanup()
            except Exception:  # noqa: BLE001 - see _run_cleanups' own rationale
                logger.debug(
                    "ResolvedArtifactPlan cleanup failed: %r", cleanup, exc_info=True
                )

    def __enter__(self) -> ResolvedArtifactPlan:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.run_cleanups()
