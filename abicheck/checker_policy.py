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

"""Compatibility facade over :mod:`abicheck.policy.classification` and
:mod:`abicheck.policy.evidence_status` (ADR-061 gap B).

The real implementation moved to those two `policy`-owned modules — the
change-kind classification sets, the policy registry, and verdict
computation live in ``classification.py``; the per-finding epistemic status
and cross-comparison evolution enums live in ``evidence_status.py``. This
module now only re-exports their combined public surface, unchanged, plus
``ChangeKind``/``HasKind``/``Verdict``/``VALID_BASE_POLICIES`` (themselves
already re-exports from ``model``).

**Why this stays a flat, unclassified root module rather than a
`policy`-package `legacy_paths` entry.** ``abicheck/checker_types.py`` (the
`model`-owned, legacy ``DiffResult``) imports this facade directly —
``model``'s declared ADR-061 imports are the standard library only, so it
cannot statically depend on `policy`. Classifying this facade as `policy`
would turn that pre-existing import into a real, gate-checked direction
violation. Today it is not one: ``scripts/check_architecture.py``'s
dependency-direction check only flags a *classified* target outside a
source layer's declared imports, and this facade is deliberately kept off
every layer's `legacy_paths` so it never resolves to one. ``checker_types.
DiffResult`` keeps importing it exactly as before — the supported legacy
compatibility boundary the ADR's own gap-B fix describes preserving,
including its lazy per-method import style (so no policy-resolved value is
ever cached on the mutable ``DiffResult`` — see ``policy.classification.
apply_policy_file_overrides``'s own docstring for why that was tried and
reverted). Every other, physically-migrated internal caller
(``policy/*``, ``workflows/*``, ``report/*``, and ``compare/*``) imports
``policy.classification``/``policy.evidence_status`` directly instead of
routing through this facade.

``contract_gating.py`` and ``reclassify.py`` carry the identical treatment,
for the identical reason (see their own module docstrings); together with
this module they are ``modules.yaml``'s three "no single layer" leaves.
"""

from __future__ import annotations

from .model.change_catalog.kinds import ChangeKind as ChangeKind, HasKind as HasKind
from .model.change_catalog.registry import VALID_BASE_POLICIES as VALID_BASE_POLICIES
from .policy.classification import (
    ADDITION_KINDS as ADDITION_KINDS,
    API_BREAK_KINDS as API_BREAK_KINDS,
    BREAKING_KINDS as BREAKING_KINDS,
    COMPATIBLE_KINDS as COMPATIBLE_KINDS,
    IMPACT_TEXT as IMPACT_TEXT,
    PLUGIN_ABI_DOWNGRADED_KINDS as PLUGIN_ABI_DOWNGRADED_KINDS,
    POLICY_REGISTRY as POLICY_REGISTRY,
    QUALITY_KINDS as QUALITY_KINDS,
    RISK_KINDS as RISK_KINDS,
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
    policy_registry_markdown as policy_registry_markdown,
)
from .policy.evidence_status import (
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
    "policy_registry_markdown",
]
