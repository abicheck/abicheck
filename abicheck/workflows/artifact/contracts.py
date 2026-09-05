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

"""``ResolvedArtifactPlan`` — Phase 1 of the duplication-and-convergence
plan's "Finish artifact-resolution convergence" work
(``docs/contribute/plans/duplication-and-convergence-assessment.md``).

**Milestone A (done).** The plan's Phase 1 item 1 asks for a
context-managed session that owns any resource an artifact-resolution
attempt allocates (an inferred-build temp directory, most concretely) from
resolution onward — spanning through whichever of execution or dry-run
inspection follows, not scoped to either alone. Every call site that
allocates such a resource shares this one reviewed session type instead of
its own hand-rolled ``list[Callable[[], None]]`` accumulator plus a manual
``finally`` drain — each still its own separate instance (no two call sites
are ever invoked concurrently against the same one, so nothing here makes
them share a session). There were three when this type was introduced
(``service_input_resolution._resolve_side_snapshot_impl`` plus
``cli_dump_helpers.perform_elf_dump``/``handle_non_elf_dump``); ADR-063
Track 1 deleted the latter two as dead code, leaving the typed pipeline's
own as the live one.

**Milestone B (this slice).** Item 1's "full shape" also asks
``ResolvedArtifactPlan`` to carry the resolved-fact fields the plan's
target-architecture section lists (normalized input type and binary
format, requested/effective depth, selected frontend, headers and
public-header scope, ...), not just own cleanup. Investigating that against
the real code found a real conflict with an already-documented, deliberate
design decision, not an oversight to fix: two of the listed fields —
*effective include search* and *effective compile context* — are only
known after the L3→L2 compile-context fold runs
(``buildsource.l2_seed.seed_includes_and_fold_compile_context``), and that
fold is the one step in this whole pipeline that can allocate the
inferred-build temp directory this session type exists to own. Moving the
fold from execution into resolution — which is what "own the resource from
resolution onward" would require for *this* resource — would also move it
ahead of ``dump --dry-run``'s own resolve-only preview
(``service_dump_pipeline.ResolvedDumpRequest``), whose own docstring
already states a considered, deliberate guarantee: dry-run must never raise
on anything but a usage error, and the fold can raise
``HeaderCompileContextAmbiguousError`` on genuinely ambiguous build
evidence. Folding the resource-lifetime scope into resolution would break
that guarantee, not merely widen it — so this slice does **not** move the
fold. The fields below are exactly the ones ``resolve_dump_request()``
already knows *without* running it: everything the target shape names other
than "effective include search"/"effective compile context". A plan built
from them therefore always starts with empty ``pending_cleanups`` today
(nothing is allocated during resolution yet) — that is an honest report of
today's architecture, not a placeholder; the two deferred fields, and
whatever resource lifetime moving them would introduce, are correctly
scoped to a future slice, once a design closes the dry-run-contract
conflict above (see the plan doc's own Phase 1 item 1 entry for this
finding in full).

Migrating everything downstream of a real resolve/execute split (Phase 1
items 2–10 — routing native ``dump``, ``scan``, PE/Mach-O, ``appcompat``,
`deps compare`, and the two ``symbols_only=True`` supplementary paths
through a typed artifact pipeline, and making dry-run render the resolved
plan) is deliberately out of scope for this slice too — see the plan doc's
own Phase 1 section for the full, still-open list.

Deliberately a leaf, engine-layer module: no ``click``/``cli_*`` import (the
``engine-cli-boundary`` AI-readiness gate would reject one), and no
dependency on ``service_input_resolution.py``, ``cli_dump_helpers.py``, or
``service_dump_pipeline.py`` — each of those depends on this module, not
the reverse, so a new resolved-fact field added here must stay plain data
(``str``/``Path``/tuples), never a type those modules themselves own.

ADR-061 Phase 3: this contract has zero first-party dependencies, so it
moved into ``abicheck.workflows.artifact`` (D2's target
``workflows/artifact/contracts.py``) as-is — the flat module's own leaf
status above is exactly what made the physical move risk-free. Its four
call sites at the time (``service_dump_pipeline.py``,
``service_input_resolution.py``, ``cli_dump_helpers.py``,
``cli_dump_non_elf.py``) stayed flat and imported it from its new location;
the last two of those have since gone -- ADR-063 Track 1 deleted
``cli_dump_non_elf.py`` outright and ``perform_elf_dump``, the only user of
this contract in ``cli_dump_helpers.py``, with it. The resolve/execute split
this module's own docstring already defers is unaffected by either.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
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
    to what the live call site
    (``service_input_resolution._resolve_side_snapshot_impl``, and
    ``perform_elf_dump`` before it was retired) actually needs: a
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

    **Resolved-fact fields (Milestone B).** The keyword-only fields below
    (all optional, all defaulting to ``None``/``()``) are the subset of the
    plan's own target-architecture "carries" list that is knowable *without*
    running the L3→L2 compile-context fold — see this module's own
    docstring for exactly which two fields that excludes, and why. A plan
    constructed with none of them (the three Milestone A call sites'
    existing usage, unchanged) still behaves exactly as before: a pure
    cleanup-owning session with no resolved facts attached. A caller that
    does know these facts at resolve time (``service_dump_pipeline.
    resolve_dump_request``, so far) can attach them here instead of
    threading them through a separate return value, so a single object
    carries both what execution needs to clean up *and* what a future
    dry-run render would show.
    """

    __slots__ = (
        "pending_cleanups",
        "_drained_count",
        "binary_format",
        "lang",
        "header_backend",
        "effective_header_backend",
        "requested_depth",
        "collect_mode",
        "public_headers",
        "public_header_dirs",
    )

    def __init__(
        self,
        *,
        binary_format: str | None = None,
        lang: str | None = None,
        header_backend: str | None = None,
        effective_header_backend: str | None = None,
        requested_depth: str | None = None,
        collect_mode: str | None = None,
        public_headers: tuple[Path, ...] = (),
        public_header_dirs: tuple[Path, ...] = (),
    ) -> None:
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
        #: The detected binary format of the artifact being resolved (e.g.
        #: ``"elf"``/``"pe"``/``"macho"``) -- mirrors ``ResolvedDumpRequest.fmt``.
        self.binary_format = binary_format
        #: The normalized source language (``service_dump_pipeline.
        #: resolve_dump_request``'s own case-lowered ``request.lang``).
        self.lang = lang
        #: The requested header-AST backend (``"auto"``/``"castxml"``/
        #: ``"clang"``/...), before resolution to a concrete choice.
        self.header_backend = header_backend
        #: A *reporting-only* projection of which concrete header-AST
        #: backend resolution currently favors -- see
        #: ``ResolvedDumpRequest.effective_header_backend``'s own docstring
        #: for why this can, in principle, disagree with what execution
        #: ends up doing (an unpinned "auto" re-resolving, or the
        #: environment changing between resolve and execute).
        self.effective_header_backend = effective_header_backend
        #: The caller's own requested evidence depth (``None`` when
        #: unspecified) -- an input, known up front, not an achieved value.
        self.requested_depth = requested_depth
        #: The effective build/source evidence level *requested_depth*
        #: resolves to (``SideEvidence.collect_mode``) -- still knowable
        #: without running the L3->L2 fold, unlike the fold's own output.
        self.collect_mode = collect_mode
        #: The resolved public-header file set (directories excluded; see
        #: *public_header_dirs*).
        self.public_headers = public_headers
        #: The resolved public-header directory set, unioned from both the
        #: caller's explicit directories and any directory entries split out
        #: of the raw header list.
        self.public_header_dirs = public_header_dirs

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

        Also covers registration *during* the drain itself: the cursor
        (:attr:`_drained_count`) advances one entry at a time, re-checking
        ``len(pending_cleanups)`` before each step, so a running cleanup that
        itself calls :meth:`add_cleanup` (discovering a further owned
        resource) has that new entry picked up in this same call rather than
        left for a drain that may never come again (Codex review: an earlier
        revision snapshotted the pending slice and advanced the cursor past
        it *before* running any of them, so a cleanup registered mid-drain
        was silently skipped even though it was registered strictly before
        the call finished).
        """
        while self._drained_count < len(self.pending_cleanups):
            cleanup = self.pending_cleanups[self._drained_count]
            self._drained_count += 1
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
