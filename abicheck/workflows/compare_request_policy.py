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

"""A typed ``CompareRequest``'s policy inputs, resolved once.

``classify_compare_pair`` (``service_compare_pipeline.py``) used to assemble
these inline: the D7 evaluation config (read *before* the project-config
fold, which is the weakest tier), the project-config ``policy.overrides``
fold, and -- since ADR-067 D5/D6 reached the typed API -- the acknowledgment
records and the additions-review gate. Loading the files and folding a
forwarded pack stay with the caller, since ``pack_application`` is outside
what ``workflows`` may import. They are one
question ("what policy does this request run under?"), and the release
fan-out reaches them through the same request, so they have one owner here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .acknowledgment_inputs import (
    CompareAcknowledgments,
    resolve_compare_acknowledgments,
)

if TYPE_CHECKING:
    from ..policy_file import PolicyFile
    from ..suppression import SuppressionList
    from .contracts import CompareRequest

__all__ = ["RequestPolicyInputs", "resolve_request_policy_inputs"]


@dataclass(frozen=True)
class RequestPolicyInputs:
    suppression: SuppressionList | None
    policy_file: PolicyFile | None
    evaluation_config: Any
    acknowledgments: CompareAcknowledgments


def resolve_request_policy_inputs(
    request: CompareRequest,
    suppression: SuppressionList | None,
    policy_file: PolicyFile | None,
) -> RequestPolicyInputs:
    """Resolve *request*'s policy inputs in their precedence order.

    *suppression*/*policy_file* are the request's loaded files with any
    forwarded pack already folded in (the caller's step: ``pack_application``
    is not importable from ``workflows``). This reads the D7 evaluation
    config from them *before* the weakest-tier project-config fold.
    """
    from .compare_gate_receipt import resolve_request_evaluation_config

    pf = policy_file
    evaluation_config = resolve_request_evaluation_config(request, pf, suppression)
    # ADR-068 §3 #23 / ADR-049 D7: project-config overrides at the weakest tier.
    if request.project_policy_overrides:
        from ..policy.policy_file_project_overrides import (
            apply_lower_precedence_overrides,
        )

        pf = apply_lower_precedence_overrides(
            pf, dict(request.project_policy_overrides), base_policy=request.policy
        )
    acknowledgments = resolve_compare_acknowledgments(
        request.acknowledgments_path,
        policy_file=pf,
        configured_action=request.acknowledgment_unacknowledged_additions,
    )
    return RequestPolicyInputs(suppression, pf, evaluation_config, acknowledgments)
