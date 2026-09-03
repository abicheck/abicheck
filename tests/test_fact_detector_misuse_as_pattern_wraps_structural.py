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

"""`fact_equality_misuse_sites()`'s top-level whole-`case.pattern`
dispatch, now unified onto `_paired_sub_pattern_candidates()`
(``scripts/fact_detector_misuse.py``/``scripts/fact_detector_misuse_
scope.py``) -- ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

Covers the follow-up finding that `case (fact,) as whole:` -- a
structural pattern *wrapped* by a top-level `as`-pattern -- was handled
exclusively as a whole-subject capture (registering only `whole`) and
never recursed into its own wrapped `MatchSequence`, unlike
`_paired_sub_pattern_candidates()`'s own identical per-position handling
of the same shape (Codex review, fresh evidence). Fixed by having the
whole-`case.pattern` dispatch delegate to that same shared primitive
instead of reimplementing its own, narrower subset of the same rules.
"""

from __future__ import annotations

import ast

from scripts.fact_detector_misuse import fact_equality_misuse_sites


class TestAsPatternWrappingAStructuralSequenceRecursesIntoIt:
    """`case (fact,) as whole:` binds `whole` to the whole subject *and*
    `fact` to its own sequence sub-part -- both are real aliases."""

    def test_detects_a_comparison_through_the_inner_sub_part_name(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match (rec.bases_fact,):\n"
            "        case (fact,) as whole:\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 1

    def test_the_outer_whole_subject_alias_is_not_itself_fact_typed(self) -> None:
        """Negative control: `whole` is the whole *tuple*, not a
        Fact-typed value -- comparing it must stay unflagged."""
        src = (
            "def f(rec, other):\n"
            "    match (rec.bases_fact,):\n"
            "        case (fact,) as whole:\n"
            "            return whole == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_comparison_through_a_wrapped_mapping_position(self) -> None:
        src = (
            "def f(rec, other):\n"
            '    match {"fact": rec.bases_fact}:\n'
            '        case {"fact": fact} as whole:\n'
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 1


class TestUnifiedDispatchDoesNotRegressAnyExistingWholeSubjectShape:
    """The whole-`case.pattern` dispatch was refactored to delegate to
    `_paired_sub_pattern_candidates()` rather than reimplementing its own
    subset of the same rules -- confirms every pre-existing shape it
    already handled correctly still does."""

    def test_bare_capture_still_detected(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case fact:\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_class_pattern_as_capture_still_detected(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case object() as fact:\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_chained_matchas_still_detected(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case fact as alias:\n"
            "            return fact == other and alias == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 2

    def test_whole_subject_matchor_still_detected(self) -> None:
        src = (
            "class C: pass\n"
            "class D: pass\n"
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case (C() as fact) | (D() as fact):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_structural_sequence_sub_part_still_detected(self) -> None:
        src = (
            "def f(rec, tag, other):\n"
            "    match (rec.bases_fact, tag):\n"
            "        case (fact, _):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_structural_mapping_sub_part_still_detected(self) -> None:
        src = (
            "def f(rec, other):\n"
            '    match {"fact": rec.bases_fact}:\n'
            '        case {"fact": fact}:\n'
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_wildcard_still_contributes_no_candidate(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case _:\n"
            "            return other == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []
