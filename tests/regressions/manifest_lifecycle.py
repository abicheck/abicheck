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
)
