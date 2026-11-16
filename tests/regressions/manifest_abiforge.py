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
"""Bug classes of the abiforge expectation layer (ADR-084 at reconcile)."""

from __future__ import annotations

from .bug_class_schema import BugClass

ABIFORGE_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="abiforge.expectation.source_break_disclosed_at_binary_depth",
        invariant=(
            "With no header or source evidence, a source-only break is judged "
            "correct only as a disclosed COMPATIBLE_WITH_RISK (ADR-084). An "
            "undisclosed or clean verdict for it is a false clean, and a "
            "disclosed downgrade is not a clean verdict. The rule applies to "
            "every binary-gated kind, not one cell."
        ),
        fixed_by=(2091,),
        seed_tests=("tests/test_abiforge_binary_gate.py",),
        public_surfaces=(),
        axes={
            "kind": (
                "FIELD_BECAME_CONST",
                "FIELD_ACCESS_CHANGED",
                "TYPE_REMOVED",
                "METHOD_ACCESS_CHANGED",
            ),
            "verdict": ("COMPATIBLE", "NO_CHANGE", "COMPATIBLE_WITH_RISK", "API_BREAK"),
            "disclosed": ("yes", "no"),
            "evidence": ("binary and DWARF", "with headers"),
        },
    ),
)
