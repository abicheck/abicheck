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
"""Bug classes for implicit state machines: lifecycle flags and scanner
counters whose states exist only as a flag or a running number
(``docs/contribute/plans/implicit-state-machines.md``)."""

from __future__ import annotations

from .bug_class_schema import BugClass

LIFECYCLE_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="lifecycle.done_flag_set_before_the_work_it_guards",
        invariant=(
            "A flag that makes later calls skip work is set only after that "
            "work has fully succeeded; a failure at any step leaves the "
            "object in its initial state, so a retry yields exactly what a "
            "fresh object yields. Instance: PDB TypeDatabase.parse_all "
            "marked itself parsed first and kept a half-filled type table "
            "after an unexpected error. Family F1: a failed input must not "
            "read as a complete one."
        ),
        fixed_by=(2038,),
        seed_tests=("tests/test_pdb_parse_all_lifecycle.py",),
        axes={
            "failing step": ("first record", "second record", "forward-ref link"),
        },
    ),
    BugClass(
        id="scanner.position_counter_skips_a_line_kind",
        invariant=(
            "A line scanner that numbers lines by a running counter advances "
            "it for every line kind that occupies a position on that side; "
            "the oracle is the line's index in the reconstructed file. "
            "Instance: the bug-fix contract gate's added_lines_by_path did "
            "not count diff context lines. Family F7 (harness integrity)."
        ),
        fixed_by=(2038,),
        seed_tests=("tests/test_bugfix_contract_line_numbers.py",),
        axes={"hunk line kinds": ("added", "context", "removed")},
    ),
    BugClass(
        id="lifecycle.repeated_narrowing_pass_intersects_instead_of_unions",
        invariant=(
            "A narrowing pass whose input must be the union of several "
            "consumers' sets runs once: repeating it with the same set is a "
            "no-op and a second, different set is rejected, because applying "
            "it again intersects the sets and silently counts a real finding "
            "out of the gate. A widening pass run after it is narrowed by the "
            "same recorded set, so the passes commute. Instance: "
            "DispositionLedger.apply_scope / refresh_promoted. Family F1: a "
            "pass may not make the result cleaner than the evidence."
        ),
        fixed_by=(2042,),
        seed_tests=("tests/test_disposition_transitions.py",),
        axes={
            "pass order": ("promote_then_scope", "scope_then_promote"),
            "repeat": ("same scope", "different scope"),
        },
    ),
    BugClass(
        id="scanner.delimiter_inside_a_literal_counted_as_structure",
        invariant=(
            "A text scanner that tracks nesting by delimiter characters "
            "ignores delimiters inside string and character literals; a "
            "literal's contents never open or close a scope. Instance: "
            "header_conditionals counted a '}' in a member initializer and "
            "closed the record early. Family F4 (structure over spelling)."
        ),
        fixed_by=(2048,),
        seed_tests=("tests/test_state_guards_phase5.py",),
        axes={"literal": ("string", "char", "escaped quote", "several braces", "none")},
    ),
    BugClass(
        id="lifecycle.counter_or_handle_used_past_its_release",
        invariant=(
            "A paired enter/exit counter never goes below zero and a released "
            "resource rejects further use, the same way on every backend: an "
            "unbalanced exit fails where it happens instead of leaving a state "
            "the counter can never return from. Instances: the deadline "
            "registration window (a deferred SIGTERM was lost) and "
            "BudgetedExecutor's inline path accepting work after shutdown. "
            "Family F10 (process lifecycle on failure paths)."
        ),
        fixed_by=(2048, 2054),
        seed_tests=(
            "tests/test_state_guards_phase5.py",
            "tests/test_bracket_depth_clamp.py",
        ),
        axes={
            "executor": ("thread pool", "inline"),
            "depth": ("1", "2", "3", "extra exit"),
            "scanner closer": (")", ">", ">>", "]", "}"),
        },
    ),
    BugClass(
        id="report.unrecognized_status_shown_as_a_known_one",
        invariant=(
            "A stored status outside the known vocabulary is reported as "
            "'unknown' with the value it carried, never verbatim as if it were "
            "a status, and it ranks worst. Instance: release member assurance "
            "status. Family F1."
        ),
        fixed_by=(2048,),
        seed_tests=("tests/test_state_guards_phase5.py",),
        axes={
            "status": (
                "each known",
                "unknown string",
                "case variant",
                "empty",
                "None",
                "int",
            )
        },
    ),
    BugClass(
        id="scanner.dead_preprocessor_branch_read_as_live",
        invariant=(
            "A text scan that skips provably dead preprocessor branches skips "
            "every branch whose condition is the literal 0, including an "
            "#elif 0 after #if 0; only a branch that can be live is read. "
            "Instance: the C++20 concept pre-scan (extractor generation 58). "
            "Family F4."
        ),
        fixed_by=(2054,),
        seed_tests=("tests/test_concept_scan_elif0.py",),
        axes={
            "branch": ("#elif 0", "#elif 0 chain", "#elif X", "#else", "nested #if X")
        },
    ),
)
