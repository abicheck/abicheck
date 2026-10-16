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

"""The disposition pipeline: ordered steps over one change list, audited.

:class:`DispositionPipeline` runs :class:`PipelineStep` objects in order over
a change list and one shared
:class:`~abicheck.policy.finding_disposition.run_state.PipelineContext`. It owns
the ADR-067 D1 conservation invariant -- **every input finding ends the run
kept, in a context bucket, or recorded in the disposition ledger** -- rather
than leaving it to each step: a finding a step drops outright is recorded
under the disposition that step declares, and a run always has a ledger (one
is created when the caller supplies none), so no caller can opt a run out of
its own audit trail.

Steps that create findings late (after ``ApplySuppression``) merge them
through :func:`merge_late_findings`; steps that park a finding in
``ctx.suppressed`` with no user rule record it through
:func:`record_collapsed_findings`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Protocol

from ..disposition_ledger import Disposition, DispositionLedger
from .run_state import DispositionRules, PipelineContext
from .suppression import dispose_by_suppression

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...model import AbiSnapshot
    from ...model.change import Change


class PipelineStep(Protocol):
    """Protocol for a single disposition step."""

    #: ADR-067: what it *means* when this step's output no longer contains a
    #: finding its input did, for a finding that did not land in one of the
    #: context's own buckets. Three answers, and the step is the only thing
    #: that knows which applies:
    #:
    #: * ``Disposition.DEDUPLICATED`` (the default, declared by
    #:   :data:`DEFAULT_DROPPED_DISPOSITION`) -- the finding was folded into
    #:   another one that survived;
    #: * ``Disposition.NON_GATING`` -- it was excluded on its own merits as
    #:   compatible noise, not collapsed into anything;
    #: * ``None`` -- the step *substituted* a replacement object for it (a
    #:   reclassification of the same observation), so recording the original
    #:   as a drop would count one observed change twice: once as a
    #:   disappearance and once as the replacement.
    #:
    #: Declared as a class attribute rather than inferred, because none of
    #: the three is derivable from the before/after lists alone.

    #: A step that sets ``establishes_kept_alias = True`` (``FilterRedundant``)
    #: makes ``ctx.kept`` an *alias* of the list it returns; every later step
    #: must keep returning that same list object -- see
    #: :meth:`DispositionPipeline.run`.

    name: str

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        """Transform the change list, returning the updated list."""
        ...


#: The answer for a step that does not declare one: most steps that drop a
#: finding drop it because another finding now covers it.
DEFAULT_DROPPED_DISPOSITION = Disposition.DEDUPLICATED


def record_collapsed_findings(
    changes: Iterable[Change],
    ctx: PipelineContext,
    *,
    application_point: str,
) -> None:
    """Record findings this pipeline folded away as ``deduplicated``.

    **The rule this exists to enforce:** several steps park a change in
    ``ctx.suppressed`` with no user rule involved, because that is the only
    bucket ``compare()`` excludes from the verdict (see
    ``DetectCppPatterns._suppress_grouped_children``'s own (a)/(b) note for
    the mechanics). The bucket therefore does *not* identify the mechanism,
    and the ledger cannot infer it: labelling these ``suppressed`` makes
    ``semver.recommend_release`` read an ordinary grouping or version-rename
    collapse as a user-waived major break. So every such step names itself
    here instead, and ``DispositionLedger.record`` being first-write-wins is
    what makes that stick against the ledger's own closing fallback.

    Every current caller collapses a finding into another one (a grouped
    parent, a surviving duplicate, a version-rename pair), which is why they
    share one disposition and differ only in *application_point*. A future
    step that parks a change in ``ctx.suppressed`` for some other
    rule-less reason needs its own recording call, not this one.
    """
    if ctx.disposition_ledger is None:
        return
    for change in changes:
        ctx.disposition_ledger.record(
            change, Disposition.DEDUPLICATED, application_point=application_point
        )


def _bucket_total(ctx: PipelineContext) -> int:
    """How many findings the context's own side-output buckets hold."""
    return (
        len(ctx.suppressed)
        + len(ctx.redundant)
        + len(ctx.opaque_filtered)
        + len(ctx.out_of_surface)
    )


def _record_dropped_duplicates(
    before: Sequence[Change],
    after: list[Change],
    bucket_total_before: int,
    ctx: PipelineContext,
    step_name: str,
    disposition: Disposition | None = DEFAULT_DROPPED_DISPOSITION,
) -> None:
    """Record findings a step discarded outright as ``deduplicated``.

    ADR-067 D1/D3: the raw total must count every atomically detected change,
    including the ones an early collapse removed. ``DeduplicateAstDwarf`` and
    ``DeduplicateCrossDetector`` run *before* ``FilterRedundant`` and simply
    return a shorter list, so without this the detected total silently
    undercounted by exactly the duplicates they folded away.

    Deliberately generic rather than a patch to those two steps: any future
    step that drops a finding is covered the same way, which is the failure
    class -- a collapse with no audit record -- not the two instances of it.

    A finding a step *moved* into one of the context's own side-output lists
    is not a discard and is skipped here: it keeps the disposition its own
    owner assigns (suppression records at the point it fires; the surface,
    redundancy and opaque buckets are labelled when the ledger is closed over
    the result). Since :meth:`DispositionLedger.record` is identity-keyed and
    first-write-wins, mislabelling one here would be permanent.

    *disposition* is what the step declares its drops mean
    (:class:`PipelineStep`'s own ``dropped_finding_disposition``); ``None``
    means the step substitutes rather than drops, and nothing is recorded.
    """
    ledger = ctx.disposition_ledger
    if ledger is None or disposition is None:
        return
    # Counting first, identity second. This runs once per step on every
    # comparison, and a wide diff runs two dozen steps over thousands of
    # findings, so the overwhelmingly common "this step changed nothing"
    # case must cost O(1) rather than two identity sets per step.
    #
    # The count is a sound trigger because a step that removes a finding
    # either drops it (shortening `changes`) or moves it into one of the
    # buckets (growing them by the same amount). The one shape it cannot see
    # is a step that adds and drops in the same call, so such a step records
    # its own drops -- `merge_late_findings` is the only
    # one today and does exactly that. That is not an honour-system rule:
    # `tests/test_disposition_audit.py::
    # test_a_pipeline_step_can_never_drop_a_finding_unrecorded` runs the real
    # pipeline and asserts every input finding is accounted for by the kept
    # list, a bucket, or the ledger.
    if len(after) + (_bucket_total(ctx) - bucket_total_before) >= len(before):
        return
    survived = set(map(id, after))
    for bucket in (
        ctx.suppressed,
        ctx.redundant,
        ctx.opaque_filtered,
        ctx.out_of_surface,
    ):
        survived.update(map(id, bucket))
    missing = set(map(id, before)) - survived
    if not missing:
        return
    for change in before:
        if id(change) in missing:
            ledger.record(change, disposition, application_point=step_name)


def merge_late_findings(
    changes: list[Change],
    new_findings: list[Change],
    ctx: PipelineContext,
) -> None:
    """Append deduplicated ``new_findings`` to ``changes``, respecting suppression.

    Mutates ``changes`` in place. Shared by every post-``ApplySuppression``
    detector that builds fresh ``Change`` objects (``DetectCppPatterns``,
    ``DetectTemplatePatterns``, ``DetectNamespacePatterns``) — those findings
    never passed through ``ApplySuppression`` itself, so they must run their
    own suppression check here.

    Uses :meth:`SuppressionList.evaluate` (not the cheaper ``is_suppressed``)
    so a broad rule whose selectors matched but was withheld by the
    reachability/``allow_public_break`` gate still emits the same
    ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic ``ApplySuppression``
    produces for changes it sees directly (ADR-044 D4; Codex review, fresh
    evidence) — otherwise a late finding that a broad rule *would* have
    hidden, had it been reachable earlier, is silently kept with no
    explanation of why the matching rule didn't apply.
    """
    outcome = dispose_by_suppression(
        new_findings,
        ctx.suppression,
        ledger=ctx.disposition_ledger,
        application_point="merge_late_findings",
    )
    ctx.suppressed.extend(outcome.suppressed)
    seen_keys = {(c.kind, c.symbol) for c in changes}
    for c in outcome.kept:
        key = (c.kind, c.symbol)
        if key in seen_keys:
            # ADR-067 D1: a late finding a *second* detector produced for an
            # entity already reported is still an observed change, and it is
            # dropped here rather than moved to any bucket -- so it is
            # recorded, in both the suppressed and unsuppressed paths, or
            # adding a suppression rule would change the detected total
            # instead of moving the finding between dispositions. It never
            # reaches ``DispositionPipeline.run``'s own dropped-finding sweep either:
            # these objects are created *by* the step, so they are not in the
            # snapshot that sweep diffs against.
            record_collapsed_findings([c], ctx, application_point="merge_late_findings")
            continue
        changes.append(c)
        seen_keys.add(key)
    changes.extend(outcome.diagnostics)


class DispositionPipeline:
    """Execute a sequence of disposition steps on a change list.

    Each step receives the current change list and the shared context, and
    returns the (possibly modified) change list for the next step.
    """

    def __init__(self, steps: list[PipelineStep]) -> None:
        self.steps = list(steps)

    def run(
        self,
        changes: list[Change],
        old: AbiSnapshot | None,
        new: AbiSnapshot,
        rules: DispositionRules | None = None,
        *,
        ledger: DispositionLedger | None = None,
        absent_baseline: AbiSnapshot | None = None,
    ) -> PipelineContext:
        """Run every step over *changes* under *rules*.

        *ledger* is the comparison's disposition ledger, when the caller keeps
        recording into it afterwards (``compare()`` does, for its own late
        findings); otherwise the run creates one. Either way the returned
        context's ``disposition_ledger`` accounts for every input finding.
        *absent_baseline* is the OLD-side stand-in when *old* is ``None``
        (see ``PipelineContext.baseline_or_empty``).
        """
        ctx = PipelineContext.from_rules(
            old,
            new,
            rules or DispositionRules(),
            ledger=ledger if ledger is not None else DispositionLedger(),
            absent_baseline=absent_baseline,
        )
        # A step with ``establishes_kept_alias`` (``FilterRedundant``) sets
        # ``ctx.kept = kept`` — an *aliasing* contract, not a snapshot: every
        # step from that point on is required to either leave ``changes``
        # untouched, mutate it in place (``changes[:] = ...``), or explicitly
        # resync ``ctx.kept`` to whatever new list it returns (see
        # ``DetectVersionedSymbolScheme``). If a future step instead rebinds
        # ``changes = [c for c in changes if ...]`` without updating
        # ``ctx.kept``, ``ctx.kept`` silently keeps pointing at the stale
        # pre-filter list and any suppression/demotion recorded downstream is
        # lost from the verdict with no visible error (this happened once
        # already — see ``DetectCppPatterns``/``DemoteUnreachableInternalChurn``'s
        # in-place comments). Enforce the invariant here instead of trusting
        # every future step author to remember it.
        kept_tracking_active = False
        for step in self.steps:
            before = list(changes)
            bucket_total = _bucket_total(ctx)
            changes = step.run(changes, ctx)
            _record_dropped_duplicates(
                before,
                changes,
                bucket_total,
                ctx,
                step.name,
                getattr(
                    step, "dropped_finding_disposition", DEFAULT_DROPPED_DISPOSITION
                ),
            )
            if getattr(step, "establishes_kept_alias", False):
                kept_tracking_active = True
            elif kept_tracking_active and ctx.kept is not changes:
                raise RuntimeError(
                    f"disposition step {step.name!r} broke the ctx.kept "
                    "aliasing contract established by an earlier step: it "
                    "returned a `changes` list that is not the same object as "
                    "`ctx.kept`, which silently discards any suppression or "
                    "demotion tracked via ctx.kept from the verdict. Fix the "
                    "step to mutate `changes[:] = ...` in place, or to "
                    "explicitly resync `ctx.kept = changes` before returning."
                )
        # Ensure ctx.kept is set even if no aliasing step ran
        if not ctx.kept and changes:
            ctx.kept = changes
        return ctx
