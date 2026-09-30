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

"""Integrity of the defect-family layer of the bug-class registry.

See ``docs/contribute/plans/defect-family-harnesses.md``. Enforced here:

* every registered ``BugClass`` belongs to exactly one family, and no family
  assignment names a class that no longer exists;
* a family either has a harness module that exists or states why it is still
  planned;
* every harness carries seeded-mutant self-checks, so a harness that has gone
  vacuous (catches nothing) is visible as a missing mutant test rather than
  as a silently green suite;
* a *new* class in a harnessed family (F1-F5) lists its family harness among
  its ``seed_tests`` -- the rule that stops the registry from growing one
  isolated reproducer per fix;
* the ``OTHER`` bucket does not grow without review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.regressions.families import FAMILIES, FAMILY_OF, OTHER_REASONS, family_counts
from tests.regressions.families_legacy import LEGACY_UNATTACHED
from tests.regressions.manifest import BUG_CLASSES, all_ids

_REPO = Path(__file__).resolve().parent.parent

#: Raising this is a review decision: three or more classes sharing one
#: mechanism in OTHER means a new family (and harness) is due instead.
OTHER_BUDGET = 22

#: Families whose invariant a harness enforces; a new class there must attach.
_HARNESSED = ("F1", "F2", "F3", "F4", "F5")


def test_every_bug_class_has_exactly_one_family() -> None:
    registered = set(all_ids())
    unassigned = sorted(registered - set(FAMILY_OF))
    stale = sorted(set(FAMILY_OF) - registered)
    assert not unassigned, (
        f"assign these bug classes a family in tests/regressions/families.py: {unassigned}"
    )
    assert not stale, f"families.py names bug classes that no longer exist: {stale}"


def test_family_keys_are_known() -> None:
    assert set(FAMILY_OF.values()) <= set(FAMILIES)


@pytest.mark.parametrize("key", sorted(FAMILIES))
def test_family_has_harness_or_states_why_not(key: str) -> None:
    fam = FAMILIES[key]
    if fam.harness_tests:
        missing = [p for p in fam.harness_tests if not (_REPO / p).is_file()]
        assert not missing, f"{key} names harness modules that do not exist: {missing}"
        assert not fam.planned_reason, (
            f"{key} has a harness but still states planned_reason"
        )
    else:
        assert fam.planned_reason.strip(), f"{key} has no harness and no planned_reason"


def _test_function_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    ]


@pytest.mark.parametrize("key", _HARNESSED)
def test_harness_has_seeded_mutant_self_checks(key: str) -> None:
    """A harness proves it can fail: at least two tests reintroduce a
    historical bug of the family and assert the oracle reports it."""
    names: list[str] = []
    for rel in FAMILIES[key].harness_tests:
        path = _REPO / rel
        # Include sibling modules of a split harness (same family prefix).
        for sibling in sorted(path.parent.glob(f"test_family_{key.lower()}_*.py")):
            names.extend(_test_function_names(sibling))
    mutant_tests = [n for n in names if "mutant" in n]
    assert len(mutant_tests) >= 2, (
        f"{key} harness has {len(mutant_tests)} seeded-mutant tests; need >= 2"
    )


def test_new_harnessed_class_attaches_to_its_harness() -> None:
    offenders = []
    for bug in BUG_CLASSES:
        family = FAMILY_OF.get(bug.id)
        if family not in _HARNESSED or bug.id in LEGACY_UNATTACHED:
            continue
        if not set(FAMILIES[family].harness_tests) & set(bug.seed_tests):
            offenders.append((bug.id, family))
    assert not offenders, (
        "a new bug class in a harnessed family must list the family harness in "
        f"seed_tests (add a harness cell for it): {offenders}"
    )


def test_legacy_exemptions_only_shrink() -> None:
    registered = set(all_ids())
    stale = sorted(LEGACY_UNATTACHED - registered)
    assert not stale, f"remove deleted classes from families_legacy.py: {stale}"
    attached = sorted(
        bug.id
        for bug in BUG_CLASSES
        if bug.id in LEGACY_UNATTACHED
        and FAMILY_OF.get(bug.id) in _HARNESSED
        and set(FAMILIES[FAMILY_OF[bug.id]].harness_tests) & set(bug.seed_tests)
    )
    assert not attached, (
        f"these legacy classes are now attached; drop them from LEGACY_UNATTACHED: {attached}"
    )


def test_other_bucket_is_bounded_and_reasoned() -> None:
    assert all(reason.strip() for reason in OTHER_REASONS.values())
    assert family_counts()["OTHER"] <= OTHER_BUDGET, (
        "OTHER grew past its budget; group the new classes into a family with a harness instead"
    )


def test_family_counts_cover_the_registry() -> None:
    counts = family_counts()
    assert sum(counts.values()) == len(all_ids())
    # Vacuity guard: the families actually partition something.
    assert sum(1 for key in _HARNESSED if counts[key] > 0) == len(_HARNESSED)
