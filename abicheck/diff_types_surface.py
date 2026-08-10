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

"""Type-level ABI-surface scoping shared across the ``diff_types`` detector
family: which records count as part of the inspected library's ABI surface
(std::/anonymous filtering, direct-reference un-filtering) and the
reserved-field name pattern ABICC-parity detectors match against.

A genuine leaf module (imports only ``model``/``name_classification``/
``type_reachability``, never ``diff_types`` itself or its siblings): moved
out of ``diff_types.py`` specifically so ``diff_types_field_facts.py`` and
``diff_types_abicc_parity.py`` — themselves split out of ``diff_types.py``
to stay under its line-count cap — can import these at module level instead
of the function-local ``from .diff_types import ...`` an earlier version of
this split used, which the AI-readiness import-cycle-growth check correctly
flagged as a real cycle in the static import graph (``diff_types ->
diff_types_field_facts -> diff_types``) even though it never deadlocks at
runtime. Promoting the shared helper to a true leaf module — the same fix
this repo's own AGENTS.md prescribes for exactly this shape — closes the
cycle outright rather than papering over it with a deferred import.
"""

from __future__ import annotations

import re

from .model import AbiSnapshot, RecordType, is_non_abi_surface_type
from .name_classification import STDLIB_TYPE_NAMESPACE_PREFIXES
from .type_reachability import directly_referenced_stdlib_types

#: ABICC ``Used_Reserved_Field``: matches ``reserved``/``_reserved``/
#: ``__reserved``/``pad``/``spare``/``unused``/``mbz``/``fill``/``filler``
#: style placeholder field names (case-insensitive, with an optional
#: numeric suffix — ``reserved1``, ``_pad3``). Shared with
#: ``_diff_field_renames`` (skips a reserved→real transition, handled
#: separately as ``USED_RESERVED_FIELD``) and the ABICC-parity
#: ``_diff_reserved_fields`` detector itself.
_RESERVED_FIELD_RE = re.compile(
    r"^_{0,2}(reserved|pad|padding|spare|unused|mbz|fill|filler)\d*$",
    re.IGNORECASE,
)


def _is_abi_surface_type(
    t: RecordType,
    *,
    exclude_stdlib: bool,
    directly_referenced: frozenset[str] = frozenset(),
) -> bool:
    """True if record *t* is part of the inspected library's ABI surface.

    Shared by every type-level detector (records, unions, field/kind/reserved
    diffs) so std::/anonymous filtering (FP-1/FP-2) is applied consistently and
    cannot be bypassed via an alternate map-construction path.

    *directly_referenced* (status-review item 3, ``type_reachability.py``)
    un-filters a std:: record a public, non-stdlib declaration names directly
    (vs. only reachable via template-instantiation internals) -- its layout
    changes are a real break (e.g. a libstdc++ dual-ABI flip). Keys on
    ``qualified_name or name``, not ``name`` alone (see ``type_reachability``'s
    identity fix for why); FP-2's anonymous-marker check keeps the bare name.
    """
    identity = t.qualified_name or t.name
    is_stdlib = identity.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)
    if exclude_stdlib and is_stdlib and identity not in directly_referenced:
        return False
    return not is_non_abi_surface_type(t.name, exclude_stdlib_namespaces=False)


def _directly_referenced(old: AbiSnapshot, new: AbiSnapshot) -> frozenset[str]:
    """Stdlib record identities directly referenced from either snapshot's
    public surface (status-review item 3) -- union of both sides so a type
    used directly in only one of old/new still lifts the filter for it."""
    return directly_referenced_stdlib_types(old) | directly_referenced_stdlib_types(new)
