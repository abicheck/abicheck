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

"""The single call site that turns a ``DiffResult`` into a severity
``GateDecision`` for report-format document construction (ADR-061 Phase 2).

Before this module existed, ``reporter._build_severity_json``,
``sarif._severity_gate_properties``, and ``html_report``'s CI-gate card each
independently imported :func:`abicheck.policy.severity.compute_gate_decision`
and hand-assembled the same four arguments
(``result.changes``/``result.policy``/``result._effective_kind_sets()``/
``result.policy_file``) from the same ``DiffResult``. All three already
called the one canonical resolver, so the risk was never disagreement --
but ADR-061 D9 says a report format's document construction *consumes* a
decision, it does not *reconstruct* one from the result's own fields each
time. :func:`gate_decision_for_result` is that one reconstruction site: each
format's top-level entry point (``to_json_str``'s mode functions, ``to_sarif``,
``generate_html_report``) calls it exactly once and threads the resulting
``GateDecision`` down to its own document-building helpers, which read the
already-computed fields instead of importing ``compute_gate_decision``
themselves.

This module does not decide anything new -- it is a thin, single-purpose
wrapper around the existing canonical computation, scoped narrowly so every
format's severity gate can be proven equal by construction (see
``tests/test_gate_decision_shared.py``) rather than merely by inspection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .severity import GateDecision, SeverityConfig, compute_gate_decision

if TYPE_CHECKING:
    from ..checker_types import DiffResult


def gate_decision_for_result(
    result: DiffResult,
    severity_config: SeverityConfig | None,
) -> GateDecision | None:
    """Return the one severity gate decision for *result* under *severity_config*.

    Returns ``None`` when no severity gate is configured, matching every
    existing call site's ``severity_config is None`` branch (the legacy,
    verdict-only exit-code scheme). Always evaluated over ``result.changes``
    -- the full, unfiltered set -- so a display-only filter such as
    ``--show-only`` can never change the exit code a report's gate reflects.
    """
    if severity_config is None:
        return None
    return compute_gate_decision(
        result.changes,
        severity_config,
        policy=result.policy,
        kind_sets=result._effective_kind_sets(),
        policy_file=result.policy_file,
    )
