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
set (:class:`~abicheck.checker_policy.FindingEvolution`).

**Scope of this slice**: exactly one check,
``unversioned_exported_symbol`` (``buildsource.crosscheck.
CHECK_UNVERSIONED_EXPORTED_SYMBOL``) -- chosen because it needs no
public/internal boundary evidence (ADR-068 plan P4, still open), only the
ELF export table + version-definition section already present in every ELF
``AbiSnapshot``. The other checks §3 lists (rows 3-5, 15) migrate in later
slices, reusing this same evolution-folding shape -- see
:func:`compute_cross_source_evolution`'s own docstring for exactly how to
extend it.

Authority is unchanged (ADR-028 D3 / ADR-035 D1): every ``Change`` this
module returns keeps whatever ``ChangeKind`` default verdict already
governs it (``RISK``, for ``unversioned_exported_symbol``) -- this module
never sets ``effective_verdict`` and never invents a new verdict for the
``not_evaluated``/``introduced``/``resolved``/``persistent`` axis. That axis
is purely descriptive.
"""

from __future__ import annotations

from ..buildsource.crosscheck import (
    CHECK_UNVERSIONED_EXPORTED_SYMBOL,
    CrosscheckConfig,
    run_crosschecks,
)
from ..checker_policy import FindingEvolution
from ..checker_types import Change
from ..model import AbiSnapshot

#: Checks this module knows how to fold into an evolution-stated finding set.
#: Extending this set to migrate another §3 check (rows 3-5, 15) means:
#: nothing here changes -- the folding logic below is check-agnostic, keyed
#: only by ``Change.symbol`` -- so long as the check's own findings carry a
#: stable per-side identity in ``symbol`` the way every existing crosscheck
#: producer already does.
CROSS_SOURCE_EVOLUTION_CHECKS: frozenset[str] = frozenset(
    {CHECK_UNVERSIONED_EXPORTED_SYMBOL}
)


def _run_one_side(snapshot: AbiSnapshot) -> tuple[bool, dict[str, Change]]:
    """Run the migrated check(s) on one snapshot.

    Returns ``(evaluated, findings_by_symbol)``. ``evaluated`` is True iff
    the check actually ran against this snapshot's evidence *and* saw every
    finding, not just some of them: ``run_crosschecks`` records a
    ``providers`` entry once a check's own status is ``"present"`` even when
    its own ``coverage`` row is later downgraded to ``"partial"`` (its
    ``max_per_check`` cap truncated the finding list) -- a check that
    returned ``"skipped"`` outright (e.g. no ELF symbol table at all) leaves
    no ``providers`` entry either way. Trusting ``providers`` alone would
    read a capped side as fully evaluated, letting a symbol beyond the cap
    misclassify as ``INTRODUCED``/``RESOLVED`` instead of the correct
    ``NOT_EVALUATED`` (Codex review). ``max_per_check=0`` disables the cap
    outright for this workflow instead of reading the coverage row, since a
    single migrated check's own finding count is not the unbounded, whole-
    snapshot volume the cap exists to bound. ``findings_by_symbol`` is empty
    when the check ran and found nothing to flag -- that is a real,
    evaluated "clean" result, not the same as not having run at all.
    """
    cfg = CrosscheckConfig(enabled=CROSS_SOURCE_EVOLUTION_CHECKS, max_per_check=0)
    result = run_crosschecks(snapshot, cfg)
    evaluated = CHECK_UNVERSIONED_EXPORTED_SYMBOL in result.providers
    by_symbol = {c.symbol: c for c in result.findings}
    return evaluated, by_symbol


def compute_cross_source_evolution(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Run the migrated cross-source check(s) on *old* and *new* independently
    and return one evolution-stated :class:`Change` per distinct symbol
    either side flagged.

    Per-symbol resolution (ADR-068 D3's four states):

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

    A symbol flagged on neither side never appears here at all -- there is
    nothing to report and no evolution to state.
    """
    old_evaluated, old_findings = _run_one_side(old)
    new_evaluated, new_findings = _run_one_side(new)

    results: list[Change] = []
    for symbol in sorted(set(old_findings) | set(new_findings)):
        old_hit = symbol in old_findings
        new_hit = symbol in new_findings
        if old_evaluated and new_evaluated:
            if old_hit and new_hit:
                evolution = FindingEvolution.PERSISTENT
                change = new_findings[symbol]
            elif new_hit:
                evolution = FindingEvolution.INTRODUCED
                change = new_findings[symbol]
            else:
                evolution = FindingEvolution.RESOLVED
                change = old_findings[symbol]
        elif new_hit and not old_evaluated:
            # NEW flags it; OLD's evidence could not confirm or deny it was
            # already present -- never claim INTRODUCED on that basis.
            evolution = FindingEvolution.NOT_EVALUATED
            change = new_findings[symbol]
        elif old_hit and not new_evaluated:
            # Symmetric case: OLD flagged it; NEW's evidence could not
            # confirm or deny whether it persists or was resolved.
            evolution = FindingEvolution.NOT_EVALUATED
            change = old_findings[symbol]
        else:
            # Neither evaluated side flagged this symbol -- unreachable in
            # practice (a symbol only enters the union when some evaluated
            # side flagged it), kept as a defensive no-op rather than an
            # assertion so a future check reuse can't turn this into a hard
            # crash on an evidence shape this module hasn't seen yet.
            continue
        change.finding_evolution = evolution
        results.append(change)
    return results
