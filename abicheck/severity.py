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

"""Back-compat shim: this module's real implementation moved to
:mod:`abicheck.policy.severity` (ADR-061 physical migration of the
``policy`` responsibility package's `legacy_paths` entries).

Every name this module used to define or re-export is re-exported here by
value (not a lazy ``__getattr__``, unlike ``cli_buildsource.py``'s shim at
the tail of that file) -- there is no import-cycle risk here, since nothing
under ``abicheck/policy/`` needs to import back through this flat path, so
a plain static import keeps ``abicheck.severity.X`` and
``from abicheck.severity import X`` both resolving to the exact same
object ``abicheck.policy.severity.X`` does. New code should import from
``abicheck.policy.severity`` directly.
"""

from __future__ import annotations

from .policy.severity import (
    ADDITION_KINDS as ADDITION_KINDS,
    PRESET_DEFAULT as PRESET_DEFAULT,
    PRESET_INFO_ONLY as PRESET_INFO_ONLY,
    PRESET_STRICT as PRESET_STRICT,
    SEVERITY_PRESETS as SEVERITY_PRESETS,
    CategorizedChanges as CategorizedChanges,
    ChangeKind as ChangeKind,
    CompatibilityDecision as CompatibilityDecision,
    GateDecision as GateDecision,
    HasKind as HasKind,
    IssueCategory as IssueCategory,
    KindSets as KindSets,
    PolicyError as PolicyError,
    SeverityConfig as SeverityConfig,
    SeverityLevel as SeverityLevel,
    Verdict as Verdict,
    categorize_changes as categorize_changes,
    classify_change as classify_change,
    classify_change_object as classify_change_object,
    classify_effective_change as classify_effective_change,
    compute_exit_code as compute_exit_code,
    compute_gate_decision as compute_gate_decision,
    effective_verdict_for_change as effective_verdict_for_change,
    first_matching_reclassify_verdict as first_matching_reclassify_verdict,
    gate_contribution_for_change as gate_contribution_for_change,
    gate_eligible_changes as gate_eligible_changes,
    is_evaluated as is_evaluated,
    legacy_exit_code as legacy_exit_code,
    missing_contract_exit_code as missing_contract_exit_code,
    reclassify_rule_for_change as reclassify_rule_for_change,
    resolve_severity_config as resolve_severity_config,
)

__all__ = [
    "ADDITION_KINDS",
    "PRESET_DEFAULT",
    "PRESET_INFO_ONLY",
    "PRESET_STRICT",
    "SEVERITY_PRESETS",
    "CategorizedChanges",
    "ChangeKind",
    "CompatibilityDecision",
    "GateDecision",
    "HasKind",
    "IssueCategory",
    "KindSets",
    "PolicyError",
    "SeverityConfig",
    "SeverityLevel",
    "Verdict",
    "categorize_changes",
    "classify_change",
    "classify_change_object",
    "classify_effective_change",
    "compute_exit_code",
    "compute_gate_decision",
    "effective_verdict_for_change",
    "first_matching_reclassify_verdict",
    "gate_contribution_for_change",
    "gate_eligible_changes",
    "is_evaluated",
    "legacy_exit_code",
    "missing_contract_exit_code",
    "reclassify_rule_for_change",
    "resolve_severity_config",
]
