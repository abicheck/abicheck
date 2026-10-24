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

"""Pipeline step: annotate the findings a compiled-target-ISA change may cause.

Annotation only: the step rewrites descriptions and never removes, moves or
re-classifies a finding (record-before-dispose). The logic lives in
:mod:`abicheck.compare.target_isa`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...compare.target_isa import annotate_target_isa_consequences

if TYPE_CHECKING:
    from ...model.change import Change
    from .run_state import PipelineContext

__all__ = ["AttributeTargetIsaConsequences"]


class AttributeTargetIsaConsequences:
    """Name the compiled-target root on layout and symbol findings it plausibly causes."""

    name = "attribute_target_isa_consequences"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        annotate_target_isa_consequences(changes)
        return changes
