# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Cross-source hygiene checks, stated as OLD -> NEW evolution.

ADR-068 D3 / ``docs/contribute/plans/one-comparison-product.md`` P2 and §3
row 3: :mod:`abicheck.buildsource.crosscheck` diffs one snapshot's evidence
sources against each other -- it carries no baseline of its own, so today it
only runs from ``scan_engine.py`` against the candidate binary alone. This
module is the first, minimal slice of moving that class of check onto
``compare()``'s own pipeline: it runs a check independently on OLD and NEW
and folds the two one-sided results into a single, evolution-stated finding
set (:class:`~abicheck.checker_policy.CrossSourceEvolution`).

**Scope so far**: two checks, ``unversioned_exported_symbol`` (``buildsource.
crosscheck.CHECK_UNVERSIONED_EXPORTED_SYMBOL`` -- chosen first because it
needs no public/internal boundary evidence, ADR-068 plan P4, still open;
only the ELF export table + version-definition section already present in
every ELF ``AbiSnapshot``) and ``private_header_leak``
(``CHECK_PRIVATE_HEADER_LEAK``). The other checks §3 lists (row 5, 15)
migrate in later slices, reusing this same evolution-folding shape -- see
:func:`compute_cross_source_evolution`'s own docstring for exactly how to
extend it.

**Per-check identity, not a bare ``Change.symbol`` key.** A first version of
this module keyed every check's OLD/NEW pairing on ``symbol`` alone, on the
premise that "the folding logic is check-agnostic, keyed only by symbol, so
long as the check's own findings carry a stable per-side identity in
``symbol``". That premise does not hold for every check:
``private_header_leak`` can legitimately emit *more than one* finding for
the same ``symbol`` -- one public function referencing two distinct
private-header types produces two ``Change`` objects sharing one ``symbol``
but differing ``new_value`` (the leaked type name) -- so a bare
symbol-keyed dict silently collapsed one of the two onto the other. Identity
is therefore a **per-check** function (:data:`_IDENTITY_FUNCS`, keyed by
check name), defaulting to plain ``symbol`` for a check that genuinely never
emits more than one finding per symbol (``unversioned_exported_symbol``: a
given exported symbol either has a version or it doesn't -- one finding,
one symbol, always). A check whose own identity needs more than ``symbol``
registers its own identity function here rather than inventing a second
folding algorithm.

Authority is unchanged (ADR-028 D3 / ADR-035 D1): every ``Change`` this
module returns keeps whatever ``ChangeKind`` default verdict already
governs it (``RISK``, for both migrated checks) -- this module never sets
``effective_verdict`` and never invents a new verdict for the
``not_evaluated``/``introduced``/``resolved``/``persistent`` axis. That axis
is purely descriptive.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable

from ..buildsource.crosscheck import (
    CHECK_PRIVATE_HEADER_LEAK,
    CHECK_UNVERSIONED_EXPORTED_SYMBOL,
    CrosscheckConfig,
    run_crosschecks,
)
from ..checker_policy import CrossSourceEvolution
from ..checker_types import Change
from ..model import AbiSnapshot


def _default_identity(change: Change) -> Hashable:
    """The default per-finding identity: ``Change.symbol`` alone.

    Correct for a check whose own contract guarantees at most one finding
    per symbol (``unversioned_exported_symbol``: a given exported symbol
    either lacks a version or it doesn't). A check without that guarantee
    must register its own identity function in :data:`_IDENTITY_FUNCS`
    instead of relying on this default.
    """
    return change.symbol


#: Per-check identity override for a check whose findings are not uniquely
#: keyed by ``symbol`` alone. ``private_header_leak`` can emit more than one
#: finding for the same symbol (one function leaking two distinct private
#: types), distinguished only by ``new_value`` (the leaked type name) -- see
#: the module docstring's "Per-check identity" note.
_IDENTITY_FUNCS: dict[str, Callable[[Change], Hashable]] = {
    CHECK_PRIVATE_HEADER_LEAK: lambda c: (c.symbol, c.new_value),
}

#: Checks this module knows how to fold into an evolution-stated finding
#: set. Extending this set to migrate another §3 check (row 5, 15) means:
#: register the check here, and register its own identity function in
#: :data:`_IDENTITY_FUNCS` above *unless* it shares
#: ``unversioned_exported_symbol``'s "at most one finding per symbol"
#: guarantee (see the module docstring's "Per-check identity" note) --
#: nothing else in this module's folding logic changes.
CROSS_SOURCE_EVOLUTION_CHECKS: frozenset[str] = frozenset(
    {CHECK_UNVERSIONED_EXPORTED_SYMBOL, CHECK_PRIVATE_HEADER_LEAK}
)


def _run_one_side(
    snapshot: AbiSnapshot, check: str
) -> tuple[bool, dict[Hashable, Change]]:
    """Run *check* alone against one snapshot.

    Returns ``(evaluated, findings_by_identity)``. ``evaluated`` is True iff
    the check actually ran against this snapshot's evidence *and* saw every
    finding, not just some of them: ``run_crosschecks`` records a
    ``providers`` entry once a check's own status is ``"present"`` even when
    its own ``coverage`` row is later downgraded to ``"partial"`` (its
    ``max_per_check`` cap truncated the finding list) -- a check that
    returned ``"skipped"`` outright (e.g. no ELF symbol table at all, or no
    header/origin provenance) leaves no ``providers`` entry either way.
    Trusting ``providers`` alone would read a capped side as fully
    evaluated, letting a finding beyond the cap misclassify as
    ``INTRODUCED``/``RESOLVED`` instead of the correct ``NOT_EVALUATED``
    (Codex review). ``max_per_check=0`` disables the cap outright for this
    workflow instead of reading the coverage row, since a single migrated
    check's own finding count is not the unbounded, whole-snapshot volume
    the cap exists to bound. ``enabled=frozenset({check})`` restricts this
    call to just *check* -- every other check is recorded as a disabled
    coverage row and never runs, keeping a per-check call cheap.
    ``findings_by_identity`` is empty when the check ran and found nothing
    to flag -- that is a real, evaluated "clean" result, not the same as
    not having run at all.
    """
    cfg = CrosscheckConfig(enabled=frozenset({check}), max_per_check=0)
    result = run_crosschecks(snapshot, cfg)
    evaluated = check in result.providers
    identity = _IDENTITY_FUNCS.get(check, _default_identity)
    by_identity = {identity(c): c for c in result.findings}
    return evaluated, by_identity


def compute_cross_source_evolution(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Run every migrated cross-source check on *old* and *new* independently
    and return one evolution-stated :class:`Change` per distinct identity
    either side flagged, for each check in :data:`CROSS_SOURCE_EVOLUTION_CHECKS`.

    Each check is run, and its OLD/NEW findings paired, entirely on its own
    (own :func:`_run_one_side` calls, own identity function) -- a finding
    from one check can never be paired against, or share an identity
    bucket with, a finding from a different check, even if their identity
    tuples happen to collide (e.g. the same ``symbol``).

    Per-identity resolution (ADR-068 D3's four states):

    - Evaluated on both sides, flagged on both -> ``PERSISTENT`` (NEW's
      ``Change``, since that is the currently-live finding).
    - Evaluated on both sides, flagged only on NEW -> ``INTRODUCED``.
    - Evaluated on both sides, flagged only on OLD -> ``RESOLVED`` (OLD's
      ``Change`` -- NEW carries no finding to report, but the fact that OLD
      *had* one and NEW does not is itself worth surfacing, per plan
      acceptance criterion F-9: "visible on a passing run").
    - Flagged on a side whose sibling side was **not** evaluated ->
      ``NOT_EVALUATED``, regardless of which side is missing evidence. This
      is the correctness crux (plan §5 P2): a hygiene problem present on
      both real releases must never read as ``INTRODUCED`` merely because
      one side (commonly a stripped/ELF-only baseline) lacked the evidence
      to confirm or deny it.

    An identity flagged on neither side never appears here at all -- there
    is nothing to report and no evolution to state.
    """
    results: list[Change] = []
    for check in sorted(CROSS_SOURCE_EVOLUTION_CHECKS):
        old_evaluated, old_findings = _run_one_side(old, check)
        new_evaluated, new_findings = _run_one_side(new, check)

        for key in sorted(set(old_findings) | set(new_findings), key=repr):
            old_hit = key in old_findings
            new_hit = key in new_findings
            if old_evaluated and new_evaluated:
                if old_hit and new_hit:
                    evolution = CrossSourceEvolution.PERSISTENT
                    change = new_findings[key]
                elif new_hit:
                    evolution = CrossSourceEvolution.INTRODUCED
                    change = new_findings[key]
                else:
                    evolution = CrossSourceEvolution.RESOLVED
                    change = old_findings[key]
            elif new_hit and not old_evaluated:
                # NEW flags it; OLD's evidence could not confirm or deny it
                # was already present -- never claim INTRODUCED on that basis.
                evolution = CrossSourceEvolution.NOT_EVALUATED
                change = new_findings[key]
            elif old_hit and not new_evaluated:
                # Symmetric case: OLD flagged it; NEW's evidence could not
                # confirm or deny whether it persists or was resolved.
                evolution = CrossSourceEvolution.NOT_EVALUATED
                change = old_findings[key]
            else:
                # Neither evaluated side flagged this identity -- unreachable
                # in practice (an identity only enters the union when some
                # evaluated side flagged it), kept as a defensive no-op
                # rather than an assertion so a future check reuse can't
                # turn this into a hard crash on an evidence shape this
                # module hasn't seen yet.
                continue
            change.cross_source_evolution = evolution
            results.append(change)
    return results
