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

"""Which release member a header-derived *type* finding belongs to.

A directory/package ``compare`` hands every member the release's whole
``-H`` header set, so a type declared in one library's header is diffed --
and reported -- for every member (``docs/contribute/known-gaps.md``, "A
directory ``compare``'s ``-H``/``--header`` set is applied to every
member", step 3). The release report already folds an identical finding
into one product-level entry; this module answers the question that entry
could not: *which* members' export surfaces actually reach the type.

Three answers, never two. :data:`REACHES` and :data:`PROVEN_UNREACHABLE`
are evidence; :data:`UNESTABLISHED` is the honest answer whenever
:attr:`~abicheck.export_surface.ExportSurface.exclusion_is_provable` is
false on a side that knows the type (an unexported or untyped root, an
unresolved type edge, no export table at all) or the name is ambiguous.
:func:`attribute_type` never *removes* a finding -- it only narrows which
members a product-level finding is said to affect, and falls back to "not
established" rather than to silence (step 3's own rule). The verdict half,
:func:`unreached_types_owned_elsewhere`, relieves a member of a type only
when its own evidence proves the type unreached *and* a sibling reaches it;
the finding stays recorded on the member as ``out_of_contract``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..export_surface import ExportSurface

__all__ = [
    "PROVEN_UNREACHABLE",
    "REACHES",
    "UNESTABLISHED",
    "attribute_type",
    "unreached_types_owned_elsewhere",
]

#: The member's export surface reaches the type on at least one side.
REACHES = "reaches"
#: Every side that knows the type proves it is not reachable from an export.
PROVEN_UNREACHABLE = "proven_unreachable"
#: Neither of the above could be established from this member's evidence.
UNESTABLISHED = "unestablished"


def attribute_type(name: str, surfaces: Iterable[ExportSurface]) -> str | None:
    """Attribute type *name* to one member from its per-side *surfaces*.

    Returns ``None`` when no side knows a type of that name at all -- the
    finding is not about a type this member's snapshots carry, so it has no
    reachability question to answer (a symbol finding, say).
    """
    knowing = [s for s in surfaces if name in s.all_types]
    if not knowing:
        return None
    if any(name in s.ambiguous_type_names for s in knowing):
        return UNESTABLISHED
    if any(s.resolvable and name in s.export_types for s in knowing):
        return REACHES
    if all(s.exclusion_is_provable for s in knowing):
        return PROVEN_UNREACHABLE
    return UNESTABLISHED


def unreached_types_owned_elsewhere(
    attributions: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, tuple[str, ...]]]:
    """``{member: {type: reaching members}}`` -- the type findings a member
    may keep out of its *own* verdict.

    The release-level policy half of step 3: a member whose evidence proves
    it does not reach a type is relieved of that type's findings **only**
    when another member's surface does reach it, so the finding still gates
    the release through that member. A type no member reaches stays counted
    wherever it was found, and an ``unestablished`` member keeps it --
    exclusion is never the fallback for missing evidence.
    """
    reaching: dict[str, list[str]] = {}
    for member, by_type in attributions.items():
        for name, verdict in by_type.items():
            if verdict == REACHES:
                reaching.setdefault(name, []).append(member)
    out: dict[str, dict[str, tuple[str, ...]]] = {}
    for member, by_type in attributions.items():
        for name, verdict in by_type.items():
            owners = tuple(sorted(m for m in reaching.get(name, ()) if m != member))
            if verdict == PROVEN_UNREACHABLE and owners:
                out.setdefault(member, {})[name] = owners
    return out
