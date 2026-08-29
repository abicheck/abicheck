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

"""Back-compat facade: canonical entity identity lives in
``abicheck.model.entity_identity`` now, for the same reason
``graph_facts.py`` moved (see that module's own docstring) —
``entity_resolver.py`` (moved alongside it) imports this module at runtime,
and importing it through ``abicheck.buildsource`` would re-enter this
package's eager ``__init__.py`` cascade.
"""

from __future__ import annotations

from ..model.entity_identity import (
    IDENTITY_TIER_CANONICAL as IDENTITY_TIER_CANONICAL,
    IDENTITY_TIER_NORMALIZED as IDENTITY_TIER_NORMALIZED,
    IDENTITY_TIER_REDUCED as IDENTITY_TIER_REDUCED,
    CanonicalIdentity as CanonicalIdentity,
    candidate_lookup_keys as candidate_lookup_keys,
    is_real_mangled_name as is_real_mangled_name,
    normalize_mangled_name as normalize_mangled_name,
    normalized_signature as normalized_signature,
    resolve_canonical_identity as resolve_canonical_identity,
    resolve_identity_for_node as resolve_identity_for_node,
    source_relative_identity as source_relative_identity,
)
