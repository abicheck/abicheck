# Copyright 2026 Nikolay Petrov
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

"""ADR-084 disposition step: a binary-only run may not keep a source break it cannot back.

The step is a thin shell over
:func:`~abicheck.policy.binary_gated_contract.demote_unproven_binary_api_breaks`,
which owns the rule. It only ever sets a per-finding verdict override; it never
drops a finding, so the ADR-067 conservation invariant holds without a
recording call of its own. The rule and reason reach the ledger when
``finalize_ledger`` closes the kept findings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..binary_gated_contract import demote_unproven_binary_api_breaks

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...model.change import Change
    from .run_state import PipelineContext


class DemoteUnprovenBinaryApiBreaks:
    """Demote ``BINARY_GATED`` ``API_BREAK`` findings with no observed source evidence.

    Must run after every detector, including the late ones that build their
    findings after ``ApplySuppression`` (``DetectTemplatePatterns`` emits
    ``MANDATORY_TEMPLATE_PARAM_ADDED``), so it is the last step in
    :data:`~abicheck.policy.finding_disposition.default_pipeline.DEFAULT_PIPELINE`.
    """

    name = "demote_unproven_binary_api_breaks"

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        demote_unproven_binary_api_breaks(changes, ctx.old, ctx.new)
        return changes
