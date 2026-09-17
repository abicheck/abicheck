# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The release-level fold of every member's ``change_inventory`` block.

One aggregate, derived *only* from the per-member blocks
``release_member_summary.add_member_review_summary`` already stamped from
each member's finalized findings -- never recomputed from kind names, and
never from a display-filtered or display-capped view, so a release total
and the scalar report over one of its members state the same thing.

Four honesty rules, each one a way this aggregate could otherwise lie:

* **A member with no completed comparison is not zero findings.** A member
  whose verdict is one of the release's operational sentinels (``ERROR``,
  ``not_comparable``, ``unsupported``, ``failed``) contributes nothing to
  the sums and is counted separately, so a release that failed half its
  members cannot read as a release whose other half found everything.
  ``members_without_inventory`` is the same count for a member that
  completed but carries no block at all (a legacy entry, or a driver that
  built one by hand).
* **Member totals and release-global findings stay distinguishable.** The
  sums cover *members only*. Bundle-coherence and probe-matrix findings are
  a different unit over a different operand, so they are never folded in;
  a reader that wants them reads their own sections.
* **The counters keep their documented meaning.** Each is the plain sum of
  the identically-named member counter, so ``compatibility_changes`` plus
  the four ``hygiene_*`` counters still equals the members' combined
  ``total_changes``, and the four ``compatibility_*`` verdict counters
  still partition ``compatibility_changes`` over the scored subset only
  (ADR-049 D1) -- their sum can be lower, at the release level for exactly
  the reason it can be lower for one pair.
* **Absent means absent.** With no member carrying a block, the fold
  returns ``None`` and the caller emits no section, rather than a document
  full of zeros that reads as a clean, fully-inventoried release.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = [
    "RELEASE_INVENTORY_COUNTERS",
    "RELEASE_OPERATIONAL_SENTINELS",
    "fold_release_change_inventory",
    "release_inventory_counters",
    "release_change_inventory",
]

#: The release verdicts that are not real ``Verdict`` values but operational
#: states: a member carrying one completed no comparison. Owned here rather
#: than by the CLI helper module that historically spelled it, so the report
#: layer can answer "did this member actually produce findings?" without
#: importing a front end (and so the front end keeps one definition, which
#: it now imports back under its historical private name).
#:
#: They must never mask a *different*, already-completed compatibility
#: result on ``RunOutcome.compatibility``'s own independent axis (Codex
#: review, fresh evidence): the release fan-out's own
#: ``_RELEASE_VERDICT_ORDER`` rollup ranks both above every real verdict by
#: design -- an operational failure or refusal dominates the release's own
#: reported ``verdict``, which is exactly right for that field and exactly
#: wrong for ``run_outcome.compatibility``, a genuinely separate axis. The
#: same reasoning is why this set gates the inventory fold below: a member
#: that never completed a comparison has no findings to sum, and summing it
#: as zero would read as a clean, fully-inventoried member.
RELEASE_OPERATIONAL_SENTINELS = frozenset(
    {"ERROR", "not_comparable", "unsupported", "failed"}
)

#: The member counters summed, in the order the block renders them. Kept as
#: an explicit tuple rather than read off whichever member happened to come
#: first: a member missing a counter must read as ``0`` for that counter,
#: not silently drop it from the aggregate for every other member too.
RELEASE_INVENTORY_COUNTERS = (
    "compatibility_changes",
    "compatibility_breaking",
    "compatibility_source_breaks",
    "compatibility_risk",
    "compatibility_compatible",
    "hygiene_introduced",
    "hygiene_resolved",
    "hygiene_persistent",
    "hygiene_not_evaluated",
    "hygiene_total",
)

#: The subset actually summed from member blocks. ``hygiene_total`` is
#: **not** among them and must not be: ``render_change_inventory_json``
#: does not emit it (it is a *property* of ``ChangeInventorySplit`, not a
#: rendered field), so summing it produced ``hygiene_total: 0`` beside a
#: nonzero ``hygiene_persistent`` -- a release reporting no standing
#: inventory while listing it. It is derived below instead. Caught in
#: review; this PR's own fixture had hidden it by inventing the key.
_SUMMED_COUNTERS = tuple(c for c in RELEASE_INVENTORY_COUNTERS if c != "hygiene_total")

#: The four states ``hygiene_total`` is the sum of, named once so the
#: derivation cannot drift from the single-pair split's own definition.
_HYGIENE_STATE_COUNTERS = (
    "hygiene_introduced",
    "hygiene_resolved",
    "hygiene_persistent",
    "hygiene_not_evaluated",
)


def fold_release_change_inventory(
    library_results: Sequence[Mapping[str, object]],
    *,
    operational_sentinels: frozenset[str],
) -> dict[str, object] | None:
    """Sum every member's ``change_inventory`` into one release aggregate.

    Returns ``None`` when no member carries one, so the caller can omit the
    section entirely rather than publish an all-zero inventory for a
    release that never inventoried anything.
    """
    totals = dict.fromkeys(_SUMMED_COUNTERS, 0)
    contributing = 0
    no_comparison = 0
    without_inventory = 0
    for entry in library_results:
        verdict = str(entry.get("verdict", "NO_CHANGE"))
        if verdict in operational_sentinels:
            no_comparison += 1
            continue
        block = entry.get("change_inventory")
        if not isinstance(block, Mapping):
            without_inventory += 1
            continue
        contributing += 1
        for counter in _SUMMED_COUNTERS:
            value = block.get(counter)
            if isinstance(value, int) and not isinstance(value, bool):
                totals[counter] += value
    if contributing == 0:
        return None
    # Derived, never summed: see `_SUMMED_COUNTERS`.
    totals["hygiene_total"] = sum(totals[c] for c in _HYGIENE_STATE_COUNTERS)
    return {
        **totals,
        # The scope terms a reader needs to know what the sums cover. Named
        # explicitly because the alternative -- inferring "the rest must
        # have been fine" from a member count that does not add up -- is
        # exactly the scope dishonesty ADR-065 exists to prevent.
        "members_contributing": contributing,
        "members_no_comparison_completed": no_comparison,
        "members_without_inventory": without_inventory,
    }


def release_change_inventory(
    library_results: Sequence[Mapping[str, object]],
) -> dict[str, object] | None:
    """:func:`fold_release_change_inventory` with the release's own
    operational-sentinel vocabulary applied -- the choke point every render
    of one release goes through, so the JSON document, the one-line summary
    and the Markdown table cannot state different totals.
    """
    return fold_release_change_inventory(
        library_results,
        operational_sentinels=RELEASE_OPERATIONAL_SENTINELS,
    )


def release_inventory_counters(
    library_results: Sequence[Mapping[str, object]],
) -> dict[str, int] | None:
    """:func:`release_change_inventory` reduced to the integer counters a
    one-line or table renderer reads; the scope-accounting keys beside them
    are for a document reader, not for a one-line clause.
    """
    folded = release_change_inventory(library_results)
    if folded is None:
        return None
    return {
        key: value
        for key in RELEASE_INVENTORY_COUNTERS
        if isinstance(value := folded.get(key), int)
    }
