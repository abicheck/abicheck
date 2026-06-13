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

This sub-package implements the *BuildSourcePack* architecture: an optional,
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
from .call_graph import (
    CallEdge,
    ClangCallGraphExtractor,
    augment_graph_with_calls,
    parse_clang_ast_calls,
)
from .extractor import (
    DEFAULT_ALLOWED_ACTIONS,
    ActionNotPermittedError,
    CollectionAction,
    CollectionContext,
    CollectionMode,
    CollectionResult,
    DataExtractor,
    DiscoveryResult,
    ExtractorCapabilities,
    ExtractorError,
    NormalizationResult,
    RawArtifact,
    ValidationResult,
    parse_actions,
    require_action,
    resolve_allowed_actions,
)
from .extractor_manifest import (
    ExternalCliExtractor,
    ExtractorManifest,
    ManifestError,
    ManifestOutput,
    load_extractor_manifest,
    render_command,
    run_external_extractor,
)
from .graph_backends import ingest_codeql_call_results, ingest_kythe_entries
from .include_graph import (
    ClangIncludeExtractor,
    augment_graph_with_includes,
    parse_depfile,
)
from .model import (
    BUILD_SOURCE_PACK_VERSION,
    BuildSourceEntity,
    BuildSourceManifest,
    BuildSourceRef,
    DataLayer,
    ExtractorRecord,
    LayerConfidence,
    LayerCoverage,
)
from .pack import BuildSourcePack
from .source_abi import (
    SOURCE_ABI_VERSION,
    SourceAbiSurface,
    SourceAbiTu,
    SourceEntity,
    SourceLocation,
)
from .source_diff import diff_source_abi
from .source_extractors import (
    AndroidHeaderAbiAdapter,
    CastxmlSourceExtractor,
    ClangSourceExtractor,
    SourceAbiExtractor,
    SourceExtractionError,
)
from .source_graph import (
    SOURCE_GRAPH_VERSION,
    GraphEdge,
    GraphNode,
    GraphSummaryDiff,
    SourceGraphSummary,
    build_source_graph,
    diff_source_graph,
    diff_source_graph_findings,
)
from .source_link import link_source_abi
from .source_replay import (
    REPLAY_SCOPES,
    SourceAbiCache,
    run_source_replay,
    scope_for_ci_mode,
    select_compile_units,
)

__all__ = [
    "DEFAULT_ALLOWED_ACTIONS",
    "BUILD_SOURCE_PACK_VERSION",
    "REPLAY_SCOPES",
    "SOURCE_ABI_VERSION",
    "SOURCE_GRAPH_VERSION",
    "ActionNotPermittedError",
    "AndroidHeaderAbiAdapter",
    "BuildEvidence",
    "BuildOption",
    "CallEdge",
    "CastxmlSourceExtractor",
    "ClangCallGraphExtractor",
    "ClangIncludeExtractor",
    "ClangSourceExtractor",
    "CollectionAction",
    "CollectionContext",
    "CollectionMode",
    "CollectionResult",
    "CompileUnit",
    "DiscoveryResult",
    "LayerConfidence",
    "BuildSourceEntity",
    "DataExtractor",
    "DataLayer",
    "BuildSourcePack",
    "BuildSourceManifest",
    "BuildSourceRef",
    "ExternalCliExtractor",
    "ExtractorCapabilities",
    "ExtractorError",
    "ExtractorManifest",
    "ExtractorRecord",
    "Generator",
    "GraphEdge",
    "GraphNode",
    "GraphSummaryDiff",
    "LayerCoverage",
    "LinkUnit",
    "ManifestError",
    "ManifestOutput",
    "NormalizationResult",
    "RawArtifact",
    "SourceAbiCache",
    "SourceAbiExtractor",
    "SourceAbiSurface",
    "SourceAbiTu",
    "SourceEntity",
    "SourceExtractionError",
    "SourceGraphSummary",
    "SourceLocation",
    "Target",
    "Toolchain",
    "ValidationResult",
    "augment_graph_with_calls",
    "augment_graph_with_includes",
    "build_source_graph",
    "diff_source_abi",
    "diff_source_graph",
    "diff_source_graph_findings",
    "ingest_codeql_call_results",
    "ingest_kythe_entries",
    "link_source_abi",
    "load_extractor_manifest",
    "parse_actions",
    "parse_clang_ast_calls",
    "parse_depfile",
    "render_command",
    "require_action",
    "resolve_allowed_actions",
    "run_external_extractor",
    "run_source_replay",
    "scope_for_ci_mode",
    "select_compile_units",
]
