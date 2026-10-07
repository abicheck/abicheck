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

"""Public Python API re-export of the classification vocabulary.

``docs/use/python-api.md`` names this module as a public type surface, so it
re-exports, unchanged, the change-kind classification sets, policy registry
and verdict computation (:mod:`abicheck.policy.classification`), the
per-finding evidence and evolution enums
(:mod:`abicheck.model.evidence_status`), and ``ChangeKind``/``HasKind``/
``Verdict``/``VALID_BASE_POLICIES`` from ``model``. It holds no logic.

Internal code never imports it: every ``abicheck`` module imports the owner.
It used to exist for a second reason as well -- ``model``-owned
``checker_types.DiffResult`` reached ``policy`` through it unseen by the
direction gate. That edge is now a call-time import recorded in
``architecture/debt.yaml``'s ``dependency_direction_exceptions``, so this
module stays only until the public surface is retired.
"""

from __future__ import annotations

from .change_registry import (
    API_BREAK_KINDS as API_BREAK_KINDS,
    BREAKING_KINDS as BREAKING_KINDS,
    COMPATIBLE_KINDS as COMPATIBLE_KINDS,
    RISK_KINDS as RISK_KINDS,
)
from .model.change_catalog.kinds import ChangeKind as ChangeKind, HasKind as HasKind
from .model.change_catalog.registry import VALID_BASE_POLICIES as VALID_BASE_POLICIES
from .model.evidence_status import (
    BINARY_EVIDENCE_TIERS as BINARY_EVIDENCE_TIERS,
    Confidence as Confidence,
    CrossSourceEvolution as CrossSourceEvolution,
    EvidenceStatus as EvidenceStatus,
    EvidenceTier as EvidenceTier,
    FindingEvolution as FindingEvolution,
    ReachabilityState as ReachabilityState,
    has_binary_evidence as has_binary_evidence,
    is_cross_source_resolved as is_cross_source_resolved,
)
from .policy.classification import (
    ADDITION_KINDS as ADDITION_KINDS,
    IMPACT_TEXT as IMPACT_TEXT,
    PLUGIN_ABI_DOWNGRADED_KINDS as PLUGIN_ABI_DOWNGRADED_KINDS,
    POLICY_REGISTRY as POLICY_REGISTRY,
    QUALITY_KINDS as QUALITY_KINDS,
    SDK_VENDOR_COMPAT_KINDS as SDK_VENDOR_COMPAT_KINDS,
    SDK_VENDOR_DOWNGRADED_KINDS as SDK_VENDOR_DOWNGRADED_KINDS,
    SOURCE_BREAK as SOURCE_BREAK,
    SOURCE_BREAK_KINDS as SOURCE_BREAK_KINDS,
    PolicyEntry as PolicyEntry,
    Verdict as Verdict,
    apply_policy_file_overrides as apply_policy_file_overrides,
    compute_verdict as compute_verdict,
    effective_category as effective_category,
    evidence_status_for_change as evidence_status_for_change,
    evidence_status_for_result as evidence_status_for_result,
    impact_caveat_for as impact_caveat_for,
    impact_for as impact_for,
    policy_for as policy_for,
    policy_kind_sets as policy_kind_sets,
)

__all__ = [
    "ADDITION_KINDS",
    "API_BREAK_KINDS",
    "BINARY_EVIDENCE_TIERS",
    "BREAKING_KINDS",
    "COMPATIBLE_KINDS",
    "IMPACT_TEXT",
    "PLUGIN_ABI_DOWNGRADED_KINDS",
    "POLICY_REGISTRY",
    "QUALITY_KINDS",
    "RISK_KINDS",
    "SDK_VENDOR_COMPAT_KINDS",
    "SDK_VENDOR_DOWNGRADED_KINDS",
    "SOURCE_BREAK",
    "SOURCE_BREAK_KINDS",
    "VALID_BASE_POLICIES",
    "ChangeKind",
    "Confidence",
    "CrossSourceEvolution",
    "EvidenceStatus",
    "EvidenceTier",
    "FindingEvolution",
    "HasKind",
    "PolicyEntry",
    "ReachabilityState",
    "Verdict",
    "apply_policy_file_overrides",
    "compute_verdict",
    "effective_category",
    "evidence_status_for_change",
    "evidence_status_for_result",
    "has_binary_evidence",
    "impact_caveat_for",
    "impact_for",
    "is_cross_source_resolved",
    "policy_for",
    "policy_kind_sets",
]
