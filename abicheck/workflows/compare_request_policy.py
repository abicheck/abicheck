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

``classify_compare_pair`` (``workflows.compare.resolve.py``) used to assemble
these inline: a forwarded pack's overrides, the D7 evaluation config (read
*before* the project-config fold, which is the weakest tier), the
project-config ``policy.overrides`` fold, and -- since ADR-067 D5/D6 reached
the typed API -- the acknowledgment records and the additions-review gate.
Selected ``pack_paths`` go through
:func:`~abicheck.workflows.compare_configuration.resolve_compare_configuration`,
the resolution the native ``compare`` CLI runs too. They are one question
("what policy does this request run under?"), and the release fan-out
reaches them through the same request, so they have one owner here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .acknowledgment_inputs import (
    CompareAcknowledgments,
    resolve_compare_acknowledgments,
)

if TYPE_CHECKING:
    from ..pack_application import PackApplication
    from ..policy.suppression.rules import SuppressionList
    from ..policy_file import PolicyFile
    from .service_requests import CompareRequest

__all__ = ["RequestPolicyInputs", "resolve_request_policy_inputs"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequestPolicyInputs:
    suppression: SuppressionList | None
    policy_file: PolicyFile | None
    evaluation_config: Any
    acknowledgments: CompareAcknowledgments
    #: The selected ``pack_paths``' contribution; ``None`` without a pack.
    pack_application: PackApplication | None = None


def resolve_request_policy_inputs(
    request: CompareRequest,
    suppression: SuppressionList | None,
    policy_file: PolicyFile | None,
) -> RequestPolicyInputs:
    """Resolve *request*'s policy inputs in their precedence order.

    *suppression*/*policy_file* are the request's loaded files. A forwarded
    pack contribution (``pack_policy_overrides``/``pack_internal_namespaces``)
    is folded first; selected ``pack_paths`` are resolved with the D7
    evaluation config, both *before* the weakest-tier project-config fold.

    Raises :class:`~abicheck.workflows.compare_configuration.CompareConfigurationError`
    for a pack conflict or an inapplicable pack.
    """
    from .compare_configuration import fold_project_policy_overrides
    from .compare_gate_receipt import resolve_request_evaluation_config

    pf = policy_file
    if request.pack_policy_overrides or request.pack_internal_namespaces is not None:
        from ..pack_application import PackApplication, policy_file_with_packs

        pf = policy_file_with_packs(
            pf,
            PackApplication(
                policy_overrides=dict(request.pack_policy_overrides or {}),
                internal_namespaces=request.pack_internal_namespaces,
            ),
            base_policy=request.policy,
        )
    application = None
    if request.pack_paths:
        from .compare_gate_receipt import resolve_request_pack_configuration

        configuration = resolve_request_pack_configuration(request, pf, suppression)
        evaluation_config = configuration.evaluation_config
        pf, application = configuration.policy_file, configuration.pack_application
    else:
        evaluation_config = resolve_request_evaluation_config(request, pf, suppression)
    # ADR-068 §3 #23 / ADR-049 D7: project-config overrides at the weakest tier.
    project_fold = fold_project_policy_overrides(
        pf, dict(request.project_policy_overrides or ()), base_policy=request.policy
    )
    pf = project_fold.policy_file
    for warning in project_fold.warnings:
        _logger.warning("%s", warning)
    acknowledgments = resolve_compare_acknowledgments(
        request.acknowledgments_path,
        policy_file=pf,
        configured_action=request.acknowledgment_unacknowledged_additions,
    )
    return RequestPolicyInputs(
        suppression, pf, evaluation_config, acknowledgments, application
    )
