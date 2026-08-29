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

"""Back-compat facade: the L5 node/edge schema and ADR-046 D1/D2 fact-merge
machinery live in ``abicheck.model.graph_facts``/``graph_vocabulary``/
``graph_identity`` now (ADR-061 Phase 5 item 2 follow-up) — a physical move,
not just a classification, because ``abicheck.model.source_graph`` needs it
and importing it through ``abicheck.buildsource`` would trigger that
package's eager ``__init__.py`` cascade (``call_graph.py`` -> the legacy
``source_graph.py`` facade -> back into whichever ``model`` module was
mid-import), a real circular-import failure a Codex review caught. None of
the three ``model`` modules depend on ``buildsource`` at all (only
``abicheck.name_classification``), so importing them never re-enters this
package.

Every module-level name the original flat file defined is re-exported below
(``X as X``, for mypy's strict ``--no-implicit-reexport``) — including
private-by-convention (``_``-prefixed) ones and ones with no known in-repo
caller, since this was a public leaf module any external consumer could
import from directly — so every existing ``from .graph_facts import
GraphNode``/``from abicheck.buildsource.graph_facts import ...`` call site,
inside this package and out, keeps resolving unchanged. A Codex review
already caught one real gap (``merge_graph_facts``, silently dropped from an
earlier version of this facade because nothing in this repo happened to call
it) — re-exporting the complete original surface instead of only what a
repo-local usage scan could prove closes that whole class of gap rather than
patching this one instance.
"""

from __future__ import annotations

from ..model.graph_facts import (
    OCCURRENCE_ATTR_KEYS as OCCURRENCE_ATTR_KEYS,
    FactConflict as FactConflict,
    GraphEdge as GraphEdge,
    GraphFact as GraphFact,
    GraphNode as GraphNode,
    _compute_occurrences as _compute_occurrences,
    _precedence_key as _precedence_key,
    edge_occurrence_id as edge_occurrence_id,
    edge_relation_key as edge_relation_key,
    ensure_facts_and_resolve as ensure_facts_and_resolve,
    merge_entity_facts as merge_entity_facts,
    merge_graph_facts as merge_graph_facts,
    register_fact as register_fact,
)
from ..model.graph_identity import (
    _BARE_ANON_TYPE_LOCATION_RE as _BARE_ANON_TYPE_LOCATION_RE,
    _DECL_OR_TYPE_ID_PREFIXES as _DECL_OR_TYPE_ID_PREFIXES,
    _IDENTITY_ATTR_KEYS as _IDENTITY_ATTR_KEYS,
    _decl_node_id as _decl_node_id,
    _is_decl_or_type_node_id as _is_decl_or_type_node_id,
    _normalize_graph_identity as _normalize_graph_identity,
    _normalize_identity_attrs as _normalize_identity_attrs,
    _normalize_if_decl_or_type as _normalize_if_decl_or_type,
    _strip_bare_anonymous_type_location as _strip_bare_anonymous_type_location,
    _type_node_id as _type_node_id,
)
from ..model.graph_vocabulary import (
    _CONFIDENCE_RANK as _CONFIDENCE_RANK,
    CALLBACK_EDGE_KINDS as CALLBACK_EDGE_KINDS,
    CONF_HIGH as CONF_HIGH,
    CONF_REDUCED as CONF_REDUCED,
    CONF_UNKNOWN as CONF_UNKNOWN,
    CONSUMER_EDGE_KINDS as CONSUMER_EDGE_KINDS,
    CONSUMER_NODE_KINDS as CONSUMER_NODE_KINDS,
    LINK_PROVENANCE_EDGE_KINDS as LINK_PROVENANCE_EDGE_KINDS,
    LINK_PROVENANCE_NODE_KINDS as LINK_PROVENANCE_NODE_KINDS,
    MACRO_DEP_EDGE_KINDS as MACRO_DEP_EDGE_KINDS,
    TEMPLATE_EDGE_KINDS as TEMPLATE_EDGE_KINDS,
    TEMPLATE_NODE_KINDS as TEMPLATE_NODE_KINDS,
    USE_CASE_EDGE_KINDS as USE_CASE_EDGE_KINDS,
    USE_CASE_NODE_KINDS as USE_CASE_NODE_KINDS,
    VIRTUAL_DISPATCH_EDGE_KINDS as VIRTUAL_DISPATCH_EDGE_KINDS,
    VIRTUAL_DISPATCH_NODE_KINDS as VIRTUAL_DISPATCH_NODE_KINDS,
)
