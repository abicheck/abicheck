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

"""Back-compat facade: source-graph values/construction/comparison split.

ADR-061 Phase 5 item 2's construction/comparison split (following the same
"values" move this module's own docstring already tracked): graph values
(``SourceGraphSummary``/``GraphSummaryDiff``, node-id constructors, schema
vocabulary) live in ``abicheck.model.source_graph``; ``GraphNode``/
``GraphEdge`` + the ADR-046 fact-merge machinery live in
``abicheck.model.graph_facts``. Construction (:func:`build_source_graph` and
its private helpers, folding an ADR-029 ``BuildEvidence`` + optional ADR-030
``SourceAbiSurface`` into a graph) is split across ``source_graph_build.py``
(Phase 2) and ``source_graph_build_source_abi.py`` (Phases 3-4 + the ADR-038
C.9 ``source_edges`` fold) — two flat modules rather than one, purely to stay
under the new-file line-count cap. Comparison (:func:`diff_source_graph`,
:func:`localize_symbol`) lives in ``source_graph_compare.py``. The shared
node/edge-classification predicates neither half owns exclusively (used by
``crosscheck.py``, ``graph_reconcile.py``, ``internal_leak.py``,
``impact/*``, ``surface.py``, ``post_processing_reachability.py``, and
others) live in ``source_graph_query.py``.

This module re-exports every moved name (``X as X``, the same convention its
own pre-existing ``graph_facts``/model re-export blocks already used) so
every existing ``from .source_graph import ...``/``from
abicheck.buildsource.source_graph import ...`` call site — inside this
package and out — keeps resolving unchanged.
"""

from __future__ import annotations

from typing import Any

# GraphNode/GraphEdge live in graph_facts.py now (ADR-046 D1/D2/D3 schema
# additions pushed this module to its AI-readiness line-count cap) and are
# re-exported for backward compatibility (many modules do ``from
# .source_graph import GraphNode``/``CONF_HIGH`` etc.) — the ``as``-aliases
# make the re-export explicit for mypy's strict ``--no-implicit-reexport``.
from ..model.graph_facts import (
    CONF_HIGH as CONF_HIGH,
    CONF_REDUCED as CONF_REDUCED,
    CONF_UNKNOWN as CONF_UNKNOWN,
    FactConflict as FactConflict,
    GraphEdge as GraphEdge,
    GraphFact as GraphFact,
    GraphNode as GraphNode,
    _decl_node_id as _decl_node_id,
    _normalize_graph_identity as _normalize_graph_identity,
    _type_node_id as _type_node_id,
    register_fact as register_fact,
)

# SourceGraphSummary/GraphSummaryDiff, the node-id constructors, and the
# schema vocabulary (NODE_KINDS/EDGE_KINDS/...) live in
# abicheck.model.source_graph now (ADR-061 Phase 5 item 2's "values" slice)
# and are re-exported here for backward compatibility (the same "as"-alias
# convention the graph_facts re-export block below already established).
from ..model.source_graph import (
    _FULL_WALK_SOURCE_EDGES_PRODUCER as _FULL_WALK_SOURCE_EDGES_PRODUCER,
    DEPENDENCY_EDGE_KINDS as DEPENDENCY_EDGE_KINDS,
    EDGE_KINDS as EDGE_KINDS,
    EVIDENCE_TIER_L5 as EVIDENCE_TIER_L5,
    NODE_KINDS as NODE_KINDS,
    SOURCE_GRAPH_VERSION as SOURCE_GRAPH_VERSION,
    GraphSummaryDiff as GraphSummaryDiff,
    SourceGraphSummary as SourceGraphSummary,
    _debug_type_node_id as _debug_type_node_id,
    _header_node_id as _header_node_id,
    _macro_node_id as _macro_node_id,
    _object_node_id as _object_node_id,
    _option_node_id as _option_node_id,
    _source_node_id as _source_node_id,
    _static_library_node_id as _static_library_node_id,
    _symbol_node_id as _symbol_node_id,
    _type_node_kind as _type_node_kind,
    _version_script_node_id as _version_script_node_id,
    _vtable_node_id as _vtable_node_id,
    function_decl_identity as function_decl_identity,
)

# Construction (ADR-061 Phase 5 item 2's construction slice).
from .source_graph_build import (
    build_source_graph as build_source_graph,
    project_source_files as project_source_files,
)
from .source_graph_build_source_abi import (
    _file_in_project as _file_in_project,
    fold_source_edges as fold_source_edges,
    mark_source_edges_extractor_coverage as mark_source_edges_extractor_coverage,
)

# Comparison (ADR-061 Phase 5 item 2's comparison slice).
from .source_graph_compare import (
    diff_source_graph as diff_source_graph,
    localize_symbol as localize_symbol,
)

# Shared node/edge-classification predicates neither half owns exclusively.
from .source_graph_query import (
    DECL_NODE_KINDS as DECL_NODE_KINDS,
    INTERNAL_VISIBILITIES as INTERNAL_VISIBILITIES,
    PUBLIC_VISIBILITIES as PUBLIC_VISIBILITIES,
    UNANNOTATED_VISIBILITIES as UNANNOTATED_VISIBILITIES,
    decl_declaring_files as decl_declaring_files,
    is_consumer_compiled_node as is_consumer_compiled_node,
    is_consumer_compiled_public_entry as is_consumer_compiled_public_entry,
    is_internal_dependency_node as is_internal_dependency_node,
    is_public_dependency_node as is_public_dependency_node,
    looks_like_system_name as looks_like_system_name,
)


# ── Back-compat re-export shim (lazy, to avoid an import cycle) ───────────────
# `diff_source_graph_findings` moved to `source_graph_findings.py` (split out
# to keep this module under its line-count cap; that module imports schema
# names back from here). A *static* `from .source_graph_findings import ...`
# would form a `source_graph -> source_graph_findings -> source_graph` import
# cycle (the AI-readiness gate rejects it), so this module-level
# `__getattr__` (PEP 562) resolves it lazily via `importlib.import_module` —
# a runtime call, not a static import edge — preserving
# `from .source_graph import diff_source_graph_findings` for existing callers.
def __getattr__(name: str) -> Any:
    if name == "diff_source_graph_findings":
        import importlib

        return importlib.import_module(
            ".source_graph_findings", __package__
        ).diff_source_graph_findings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
