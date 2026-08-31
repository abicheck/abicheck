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

"""Back-compat facade: the shared graph node/edge-classification predicates
live in ``abicheck.model.source_graph_query`` now (ADR-061 Phase 5 item 2
closure) -- a physical move, not just a classification, because
``buildsource/poi.py`` (``extract``-classified) needs
:func:`~abicheck.model.source_graph_query.is_public_dependency_node` and
``extract`` may not import ``compare`` (this module's own prior
classification). See that module's own docstring for the full reasoning.

Every module-level name the original flat file defined is re-exported below
(``X as X``, for mypy's strict ``--no-implicit-reexport``), including the
private-by-convention ``_TYPE_ENTITY_KINDS`` (``source_graph_findings.py``
still imports it directly from here) and the two provenance constants, so
every existing ``from .source_graph_query import ...``/``from .source_graph
import ...`` (the outer facade) call site -- inside this package and out --
keeps resolving unchanged.
"""

from __future__ import annotations

from ..model.source_graph_query import (
    _CALL_GRAPH_FALLBACK_PROVENANCE as _CALL_GRAPH_FALLBACK_PROVENANCE,
    _NO_CONSUMER_COMPILED_SIGNAL_PROVENANCES as _NO_CONSUMER_COMPILED_SIGNAL_PROVENANCES,
    _SYSTEM_NAME_PREFIXES as _SYSTEM_NAME_PREFIXES,
    _SYSTEM_NAME_SUBSTRINGS as _SYSTEM_NAME_SUBSTRINGS,
    _TYPE_ENTITY_KINDS as _TYPE_ENTITY_KINDS,
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
