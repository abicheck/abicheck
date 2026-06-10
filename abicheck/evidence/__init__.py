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

"""Optional source/build/graph evidence layers (ADR-028).

This sub-package implements the *EvidencePack* architecture: an optional,
content-addressed, independently-versioned artifact that augments an
``AbiSnapshot`` with build-context (L3), source ABI replay (L4), and
source/implementation graph (L5) evidence.

Authority rule (ADR-028 D3): artifact-backed L0/L1/L2 evidence remains the
shipped-ABI source of truth. Evidence from L3/L4/L5 may *explain, localize,
add confidence, scope, or correlate* an artifact-proven break, but must never
silently delete it. Findings produced only by L3/L4/L5 are ordinary
``ChangeKind`` entries that default to ``API_BREAK_KINDS`` (source breaks) or
``RISK_KINDS`` (deployment/context risk), never ``BREAKING_KINDS`` unless an
artifact diff also proves the break.

Phase 0 (this module) ships the manifest, coverage model, on-disk pack layout,
and snapshot reference. ADR-029 adds the build-evidence model and adapters.
"""
from __future__ import annotations

from .build_evidence import (
    BuildEvidence,
    BuildOption,
    CompileUnit,
    Generator,
    LinkUnit,
    Target,
    Toolchain,
)
from .model import (
    EVIDENCE_PACK_VERSION,
    EvidenceConfidence,
    EvidenceEntity,
    EvidenceLayer,
    EvidencePackManifest,
    EvidencePackRef,
    ExtractorRecord,
    LayerCoverage,
)
from .pack import EvidencePack

__all__ = [
    "EVIDENCE_PACK_VERSION",
    "BuildEvidence",
    "BuildOption",
    "CompileUnit",
    "EvidenceConfidence",
    "EvidenceEntity",
    "EvidenceLayer",
    "EvidencePack",
    "EvidencePackManifest",
    "EvidencePackRef",
    "ExtractorRecord",
    "Generator",
    "LayerCoverage",
    "LinkUnit",
    "Target",
    "Toolchain",
]
