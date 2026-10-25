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

"""The default step order ``compare()`` disposes of findings in.

The order is the contract: each comment below records why a step sits where
it does. ``dispose_findings`` (this package's entry point) runs exactly this
pipeline; callers never assemble their own order.
"""

from __future__ import annotations

from ...post_processing import (
    AnnotateLayoutUnverifiableCoveredByVtableChanged,
    AttributeStdlibEmbedding,
    ClearOrphanedVtableGapCorrelation,
    DeduplicateAstDwarf,
    DeduplicateCrossDetector,
    DemoteOffPythonSurface,
    DemoteUnreachableInternalChurn,
    DetectCppPatterns,
    DetectInternalLeaks,
    DetectNamespacePatterns,
    DetectTemplatePatterns,
    DetectVersionedSymbolScheme,
    DisambiguateTypeSymbols,
    DowngradeOpaqueStructChanges,
    DowngradeOpaqueTypeChanges,
    EnrichAffectedSymbols,
    EnrichSourceLocations,
    EscalateFrozenNamespaceViolations,
    FilterNonPublicSurface,
    FilterOpaqueSizeChanges,
    FilterRedundant,
    FilterReservedFieldRenames,
    SuppressRenamedPairs,
)
from ...post_processing_reachability import MarkReachability
from ..export_existence_fold import FoldExportHygieneIntoExistence
from .binary_gate import DemoteUnprovenBinaryApiBreaks
from .pipeline import DispositionPipeline
from .suppression import ApplySuppression
from .target_isa import AttributeTargetIsaConsequences

DEFAULT_PIPELINE = DispositionPipeline(
    [
        FilterReservedFieldRenames(),
        FilterOpaqueSizeChanges(),
        DowngradeOpaqueStructChanges(),
        DeduplicateAstDwarf(),
        DeduplicateCrossDetector(),
        DowngradeOpaqueTypeChanges(),
        EnrichSourceLocations(),
        FilterNonPublicSurface(),
        # Runs immediately after FilterNonPublicSurface so it can read the
        # resolved C-header surface (ctx.surf_new) and defer to it; otherwise it
        # uses the recovered Python API as the extension's public-contract oracle.
        DemoteOffPythonSurface(),
        # Reads markers TYPE_VTABLE_CHANGED set at emission time (diff_types.py)
        # to cross-reference a LAYOUT_UNVERIFIABLE finding covering the identical
        # evidence gap via Change.correlated_change_kind. Never removes anything
        # from `changes` (see the step's own docstring for why an earlier,
        # removal-based design was unsafe), so its position relative to
        # scoping/suppression/FilterRedundant is not load-bearing the way a
        # filtering step's would be -- kept here simply to run once both
        # findings are guaranteed to exist in `changes` (i.e. after scoping
        # has settled which findings are even still present).
        #
        # Must run *before* MarkReachability, though (Codex review, fresh
        # evidence): that step caches each tagged change's whole
        # ImpactAssessment via impact.engine.assess_change(), and
        # assess_change() itself prefers a cached assessment's own
        # correlated_change_kind over the flat Change field once one exists
        # (impact/engine.py's own documented contract for reusing a cached
        # assessment). Running this step after MarkReachability would let it
        # set the flat field on a change whose impact_assessment was already
        # cached with correlated_change_kind=None -- silently stale for every
        # consumer of that unified object (JSON/SARIF's impact_assessment
        # block) even though the flat top-level field is correct.
        AnnotateLayoutUnverifiableCoveredByVtableChanged(),
        # ADR-044 D1: must run before ApplySuppression so a broad suppression
        # rule can see whether the change it is about to remove is part of the
        # effective public ABI.
        MarkReachability(),
        ApplySuppression(),
        SuppressRenamedPairs(),
        FoldExportHygieneIntoExistence(),  # one finding per export event
        # Runs immediately after suppression settles `changes` so it sees
        # the final post-suppression set -- see its own docstring for why a
        # suppression targeting only the covering TYPE_VTABLE_CHANGED would
        # otherwise leave the earlier annotation dangling.
        ClearOrphanedVtableGapCorrelation(),
        DisambiguateTypeSymbols(),
        FilterRedundant(),
        EnrichAffectedSymbols(),
        AttributeStdlibEmbedding(),
        AttributeTargetIsaConsequences(),
        DetectInternalLeaks(),
        # Must run immediately after DetectInternalLeaks: it consumes that step's
        # leak verdict to demote confirmed-unreachable internal-namespace churn.
        DemoteUnreachableInternalChurn(),
        DetectCppPatterns(),
        DetectNamespacePatterns(),
        DetectTemplatePatterns(),
        # Advisory overlay: explains a versioned-symbol-scheme churn (P08). Runs
        # after rename suppression so it only sees residual removed/added pairs.
        DetectVersionedSymbolScheme(),
        # Runs last so it can tag both raw findings and the synthetic
        # overlays added by DetectInternalLeaks / DetectCppPatterns.
        EscalateFrozenNamespaceViolations(),
        # ADR-084: the last word on source-level (API_BREAK) claims. It must
        # follow every detector, because DetectTemplatePatterns and the other
        # late detectors create findings (MANDATORY_TEMPLATE_PARAM_ADDED,
        # TAG_TYPE_RENAMED, ...) after ApplySuppression. It only overrides the
        # verdict category; it never drops a finding.
        DemoteUnprovenBinaryApiBreaks(),
    ]
)
