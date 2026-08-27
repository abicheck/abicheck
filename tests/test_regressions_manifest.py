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

"""Integrity checks for ``tests/regressions/manifest.py`` (Phase 1 of
``docs/contribute/plans/bug-class-regression-testing.md``).

Mirrors `test_canonical_finding_id_completeness.py`'s discipline for
`canonical_identity_contract.py`: a registry is only as trustworthy as the
mechanism that checks it stays honest. Every property here is something a
hand-added `BugClass` entry could get wrong silently (a typo'd path, a
duplicate id, an empty ``seed_tests``) without any of these checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.regressions.manifest import BUG_CLASSES, BugClass, all_ids, get

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestRegistryShape:
    def test_registry_is_non_empty(self) -> None:
        assert len(BUG_CLASSES) > 0

    def test_every_entry_is_a_bug_class(self) -> None:
        assert all(isinstance(bc, BugClass) for bc in BUG_CLASSES)

    def test_ids_are_unique(self) -> None:
        ids = [bc.id for bc in BUG_CLASSES]
        assert len(ids) == len(set(ids)), f"duplicate BugClass id(s) in {ids}"

    @pytest.mark.parametrize("bug_class", BUG_CLASSES, ids=lambda bc: bc.id)
    def test_id_is_dotted_and_non_empty(self, bug_class: BugClass) -> None:
        assert bug_class.id, "BugClass.id must not be empty"
        assert "." in bug_class.id, (
            f"{bug_class.id!r} should be a dotted id (category.name), "
            "matching every other entry's convention"
        )

    @pytest.mark.parametrize("bug_class", BUG_CLASSES, ids=lambda bc: bc.id)
    def test_invariant_is_stated(self, bug_class: BugClass) -> None:
        assert bug_class.invariant.strip(), (
            f"{bug_class.id}: invariant must be a real sentence, not empty"
        )

    @pytest.mark.parametrize("bug_class", BUG_CLASSES, ids=lambda bc: bc.id)
    def test_traces_to_at_least_one_fix(self, bug_class: BugClass) -> None:
        # A class with no fixed_by is a hypothesis, not a registered
        # escape-history entry — every class here traces back to a real
        # merged fix (see the class's own `fixed_by`).
        assert bug_class.fixed_by, f"{bug_class.id}: fixed_by must be non-empty"
        assert all(isinstance(n, int) and n > 0 for n in bug_class.fixed_by)

    @pytest.mark.parametrize("bug_class", BUG_CLASSES, ids=lambda bc: bc.id)
    def test_has_at_least_one_seed_test(self, bug_class: BugClass) -> None:
        # This is the one property that keeps this registry from
        # degrading into exactly what it exists to prevent: a class
        # description with no executable test backing it.
        assert bug_class.seed_tests, (
            f"{bug_class.id}: a BugClass with no seed_tests is prose, not "
            "a registry entry — either name a real test or leave this "
            "class as an AGENTS.md 'Known gaps' paragraph instead"
        )


class TestRegisteredTestPathsExist:
    """Every path this registry names must resolve to a real, collectible
    test file — a stale or typo'd path here is worse than no entry at all,
    since it reads as verified coverage that doesn't exist."""

    @pytest.mark.parametrize(
        "bug_class",
        BUG_CLASSES,
        ids=lambda bc: bc.id,
    )
    def test_seed_tests_exist_and_are_collectible(self, bug_class: BugClass) -> None:
        for rel_path in bug_class.seed_tests:
            path = REPO_ROOT / rel_path
            assert path.is_file(), f"{bug_class.id}: seed test not found: {rel_path}"
            assert path.name.startswith("test_") or path.parent.name != "tests", (
                f"{bug_class.id}: {rel_path} does not look like a "
                "pytest-collected test file (expected a tests/**/test_*.py "
                "path) — point at the test that exercises this class, not "
                "a support/data module"
            )

    @pytest.mark.parametrize(
        "bug_class",
        BUG_CLASSES,
        ids=lambda bc: bc.id,
    )
    def test_known_gap_canaries_exist(self, bug_class: BugClass) -> None:
        for gap in bug_class.known_gaps:
            path = REPO_ROOT / gap.canary_test
            assert path.is_file(), (
                f"{bug_class.id}: known_gaps canary not found: "
                f"{gap.canary_test} ({gap.description})"
            )
            assert gap.reference.strip(), (
                f"{bug_class.id}: known_gaps entry must name a reference "
                f"(issue/PR/plan section): {gap.description}"
            )


class TestLookupHelpers:
    def test_get_returns_the_registered_entry(self) -> None:
        for bug_class in BUG_CLASSES:
            assert get(bug_class.id) is bug_class

    def test_get_raises_key_error_on_unknown_id(self) -> None:
        with pytest.raises(KeyError):
            get("not.a.registered.class")

    def test_all_ids_matches_the_registry(self) -> None:
        assert all_ids() == tuple(bc.id for bc in BUG_CLASSES)
