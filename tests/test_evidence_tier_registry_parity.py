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

"""A kind's evidence tier must not change which registry it belongs to.

A ``ChangeKind`` name carries two independent things: **what was observed**
(added / removed) and **which evidence saw it** (`_elf_only`,
`_elf_fallback`, `_dwarf`). Only the first decides membership of an
operation registry. Conflating them has now produced two separate defects on
one branch:

* ``operation_for_kind`` had ``_elf_only`` in its *removed* suffix list --
  correct only while ``func_removed_elf_only`` was the family's sole member
  -- so the new ``func_added_elf_only`` was reported as a removal.
* Four addition consumers listed ``FUNC_ADDED`` and not
  ``FUNC_ADDED_ELF_ONLY``, while their removal counterparts *did* list
  ``FUNC_REMOVED_ELF_ONLY``, so ``-warn-newsym`` ignored it, ``-strict``
  mis-promoted it, HTML/compat XML counted zero added symbols, and its
  lifecycle event was dropped (Codex review).

Both were found one consumer at a time. This states the rule instead, over
every ``*_elf_only`` pair that exists or is added later, so the next one
cannot be missed the same way.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind

#: Every (added, removed) pair whose members differ only by the operation
#: word, derived from the enum rather than hand-listed so a new pair joins
#: automatically.
_OPERATION_PAIRS: list[tuple[ChangeKind, ChangeKind]] = sorted(
    (
        (added, removed)
        for added in ChangeKind
        for removed in ChangeKind
        if "_added" in added.value
        and added.value.replace("_added", "_removed") == removed.value
    ),
    key=lambda pair: pair[0].value,
)

#: The pairs that also carry an evidence-tier suffix -- the ones the two
#: defects above were about.
_EVIDENCE_TIER_PAIRS = [
    (a, r)
    for a, r in _OPERATION_PAIRS
    if a.value.endswith(("_elf_only", "_elf_fallback"))
]


def test_the_pair_sets_are_not_empty() -> None:
    """Vacuity guard. Both sweeps below pass trivially against an empty list,
    which is what a renamed kind or a changed suffix would produce."""
    assert _OPERATION_PAIRS
    assert _EVIDENCE_TIER_PAIRS
    assert (
        ChangeKind.FUNC_ADDED_ELF_ONLY,
        ChangeKind.FUNC_REMOVED_ELF_ONLY,
    ) in _EVIDENCE_TIER_PAIRS


@pytest.mark.parametrize(("added", "removed"), _EVIDENCE_TIER_PAIRS)
def test_operation_classification_ignores_the_evidence_tier(
    added: ChangeKind, removed: ChangeKind
) -> None:
    from abicheck.report.change_operation import operation_for_kind

    assert operation_for_kind(added.value) == "added"
    assert operation_for_kind(removed.value) == "removed"


def _registry_pairs() -> list[tuple[str, set[object], set[object]]]:
    """Each consumer that keeps *separate* addition and removal registries,
    as ``(name, added_members, removed_members)``.

    Only consumers holding both halves are listed: the invariant is that the
    two stay symmetric, and a set with no removal counterpart has nothing to
    be symmetric with.
    """
    from abicheck.report_classifications import ADDED_KINDS, REMOVED_KINDS
    from abicheck.workflows.history import _ADDED_KINDS, _REMOVED_KINDS

    return [
        ("report_classifications", set(ADDED_KINDS), set(REMOVED_KINDS)),
        ("workflows.history", set(_ADDED_KINDS), set(_REMOVED_KINDS)),
    ]


def test_an_addition_registry_matches_its_removal_counterpart() -> None:
    """If a consumer registers the ``_elf_only`` *removal*, it must register
    the matching addition -- and vice versa.

    Deliberately keyed on the consumer's own removal set rather than on a
    fixed list of kinds: that is what makes this a rule about symmetry
    instead of one more hand-maintained membership list, which is the thing
    that was wrong in the first place. Batched so one run names every
    asymmetric consumer at once.
    """
    offenders: set[str] = set()
    for name, added_members, removed_members in _registry_pairs():
        for added, removed in _EVIDENCE_TIER_PAIRS:
            # Members are `ChangeKind` in one consumer and `str` in another,
            # so each pair is looked up in both spellings.
            present_add = added in added_members or added.value in added_members
            present_rm = removed in removed_members or removed.value in removed_members
            if present_rm and not present_add:
                offenders.add(f"{name}: has {removed.value}, missing {added.value}")
            if present_add and not present_rm:
                offenders.add(f"{name}: has {added.value}, missing {removed.value}")
    assert not offenders, sorted(offenders)


def test_a_pure_elf_only_addition_is_an_addition_everywhere_it_is_known() -> None:
    """The consumers with no removal counterpart to compare against, checked
    directly: ``-warn-newsym``'s new-symbol set and ``-strict``'s
    addition-only set both treat an ELF-only function addition as what it
    is."""
    from abicheck.compat import _helpers

    assert ChangeKind.FUNC_ADDED_ELF_ONLY in _helpers._NEW_SYMBOL_KINDS
    assert ChangeKind.FUNC_ADDED in _helpers._NEW_SYMBOL_KINDS

    # `-strict`'s own set is a local inside `_apply_strict`, so it is checked
    # through behaviour rather than by reaching into the function: a result
    # whose only change is an ELF-only addition must not be promoted.
    from abicheck.checker_policy import Verdict
    from abicheck.checker_types import Change, DiffResult

    addition_only = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so",
        verdict=Verdict.COMPATIBLE,
        changes=[Change(ChangeKind.FUNC_ADDED_ELF_ONLY, "gained", "new export")],
    )
    assert _helpers._apply_strict(addition_only).verdict == Verdict.COMPATIBLE

    # The control: a non-addition under `-strict` *is* promoted, so the
    # assertion above is about the addition set and not about `-strict`
    # being inert.
    with_a_real_change = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so",
        verdict=Verdict.COMPATIBLE,
        changes=[Change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "signature")],
    )
    assert _helpers._apply_strict(with_a_real_change).verdict != Verdict.COMPATIBLE
