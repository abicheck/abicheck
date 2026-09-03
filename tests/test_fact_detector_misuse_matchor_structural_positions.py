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

"""`_paired_sub_pattern_candidates()`'s `MatchOr` handling
(``scripts/fact_detector_misuse_scope.py``) -- ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

Split into its own file rather than appended to
``test_fact_detector_misuse_alias_edge_cases.py`` (whose own whole-
subject `TestMatchOrPropagatesWholeSubjectCaptures` class covers the
same OR-pattern trust rule at the top-level `case.pattern` position):
that file has only ~60 lines of headroom left under the architecture
gate's 1200-line test-file cap, too little to safely add a new matrix
to.

Covers the follow-up finding that a `MatchOr` nested inside a structural
sequence/mapping *position* (`case ((C() as fact) | (D() as fact),):`)
fell through `_paired_sub_pattern_candidates()`'s every branch untouched
-- the identical OR-pattern shape `_trusted_matchor_chain_names()`
already recognizes at the whole-`case.pattern` level, just unreached at
the per-position level (Codex review, fresh evidence).
"""

from __future__ import annotations

import ast

from scripts.fact_detector_misuse import fact_equality_misuse_sites


class TestMatchOrAtAStructuralSequencePosition:
    """`case ((C() as fact) | (D() as fact),):` -- an OR pattern nested
    at a sequence position, every alternative a top-level `MatchAs`
    capturing the identical position's element under the identical
    name."""

    def test_detects_a_comparison_through_the_shared_capture_name(self) -> None:
        src = (
            "class C: pass\n"
            "class D: pass\n"
            "def f(rec, other):\n"
            "    match (rec.bases_fact,):\n"
            "        case ((C() as fact) | (D() as fact),):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 1

    def test_mismatched_alternative_names_is_not_trusted(self) -> None:
        """Negative control: Python requires every alternative to bind
        the same *set* of names, but not the same *value* -- only
        `fact`'s own value differs across alternatives here, so it is
        not safe to trust as the raw subject element."""
        src = (
            "class C: pass\n"
            "class D: pass\n"
            "def f(rec, other):\n"
            "    match (rec.bases_fact,):\n"
            "        case ((C() as fact) | (D() as other_name),):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_non_whole_value_binding_is_not_trusted(self) -> None:
        """Negative control: `C(x=fact)` binds `fact` to a *field* of
        the matched element, not the whole element -- even though the
        other alternative does bind the whole value, only one alternative
        being whole-value is not enough to trust the shared name."""
        src = (
            "class C:\n"
            "    x: int\n"
            "class D: pass\n"
            "def f(rec, other):\n"
            "    match (rec.bases_fact,):\n"
            "        case ((C(x=fact)) | (D() as fact),):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_chained_matchas_with_mismatched_inner_names_is_not_trusted(
        self,
    ) -> None:
        """Negative control: only the *outer* name (`alias`) is
        guaranteed identical across alternatives by Python's own grammar
        -- the inner chained names differ, so neither is safe to trust,
        mirroring the identical whole-subject negative control."""
        src = (
            "def f(rec, other):\n"
            "    match (rec.bases_fact,):\n"
            "        case ((fact as alias) | (other_fact as alias),):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestMatchOrAtAStructuralMappingPosition:
    """The identical OR-pattern shape, reproduced at a mapping position
    (`_paired_match_mapping_candidates()`'s own per-position delegation
    to the same shared helper)."""

    def test_detects_a_comparison_through_the_shared_capture_name(self) -> None:
        src = (
            "class C: pass\n"
            "class D: pass\n"
            "def f(rec, other):\n"
            '    match {"fact": rec.bases_fact}:\n'
            '        case {"fact": (C() as fact) | (D() as fact)}:\n'
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 1


class TestWholeSubjectMatchOrStillWorksAfterTheSharedHelperRefactor:
    """Regression guard: the whole-`case.pattern` OR-pattern handling
    (`fact_equality_misuse_sites()`'s own `MatchOr` branch) was
    refactored to delegate to the same new `_trusted_matchor_chain_names()`
    helper this fix introduced -- confirms that refactor changed nothing
    for the case it already handled correctly."""

    def test_detects_a_comparison_through_the_shared_capture_name(self) -> None:
        src = (
            "class C: pass\n"
            "class D: pass\n"
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case (C() as fact) | (D() as fact):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 1

    def test_mismatched_alternative_names_is_not_trusted(self) -> None:
        src = (
            "class C: pass\n"
            "class D: pass\n"
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case (C() as fact) | (D() as other_name):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []
