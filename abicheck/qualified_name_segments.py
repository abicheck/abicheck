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

"""Compatibility facade over :mod:`abicheck.compare.qualified_name_normalization`
and :mod:`abicheck.storage.closure_identity` (ADR-061 gap B).

This module used to hold both the qualified-name segmentation/versioned-
namespace *diffing decisions* (now owned by `compare`, since that is what
they are) and the anonymous/lambda-closure ordinal-identity *snapshot
normalization* logic (now owned by `storage`, since that is what it is) --
two genuinely different responsibilities that happened to share one file
because they both started from the same ``"::"``-qualified-name primitive.
Splitting them let each get a real, single-layer owner: `storage`'s own
real caller (``storage.snapshot_load_normalization``) cannot statically
depend on a `compare`-owned module, and vice versa `compare`'s own real
callers (``diff_namespaces.py``/``diff_helpers.py``) have no reason to
depend on snapshot-normalization plumbing. See each new module's own
docstring for the full reasoning.

This flat module now only re-exports both modules' combined public
surface, unchanged, so every existing import path
(``from abicheck.qualified_name_segments import ...``) keeps working.
Canonical internal callers import the owning module directly.
"""

from __future__ import annotations

from .compare.qualified_name_normalization import (
    is_inline_abi_namespace_segment as is_inline_abi_namespace_segment,
    raw_segments as raw_segments,
    segments as segments,
    strip_inline_abi_namespaces as strip_inline_abi_namespaces,
    version_strip_segments as version_strip_segments,
    version_suffix as version_suffix,
)
from .qualified_name_segments_walk import (
    _PAYLOAD_FIELD_EXCLUSIONS as _PAYLOAD_FIELD_EXCLUSIONS,
    _collect_strings as _collect_strings,
    _legacy_sibling_is_payload_excluded as _legacy_sibling_is_payload_excluded,
    _walk_rewrite_strings as _walk_rewrite_strings,
)
from .storage.closure_identity import (
    _LAMBDA_IDENTITY_FIELDS as _LAMBDA_IDENTITY_FIELDS,
    apply_anonymous_type_ordinals as apply_anonymous_type_ordinals,
    collect_anonymous_type_ordinals as collect_anonymous_type_ordinals,
    defer_closure_identity_renumbering as defer_closure_identity_renumbering,
    renumber_anonymous_closure_identities as renumber_anonymous_closure_identities,
)

__all__ = [
    "apply_anonymous_type_ordinals",
    "collect_anonymous_type_ordinals",
    "defer_closure_identity_renumbering",
    "is_inline_abi_namespace_segment",
    "raw_segments",
    "renumber_anonymous_closure_identities",
    "segments",
    "strip_inline_abi_namespaces",
    "version_strip_segments",
    "version_suffix",
]
