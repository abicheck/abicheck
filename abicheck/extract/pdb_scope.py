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

"""Typed ``ScopePath``/``EntityId`` construction for PDB (ADR-063 Phase 6,
PDB EntityId slice, types only).

**Structurally different from DWARF's/the header-AST backends' own scope
construction (``extract/dwarf_scope.py``, ``extract/headers/*/scope.py``),
and harder for one specific reason.** Those three producers each walk a
real tree (a DIE tree, a Clang/castxml AST) with an explicit parent-scope
stack, so a declaration's ``ScopePath`` is built incrementally, for free,
as the walk descends. CodeView's own TPI type records carry no such
tree — ``TypeDatabase``/``pdb_parser.py`` name each struct/class/union/enum
with its own FULLY QUALIFIED spelling as one flat string, ``"::"`` already
embedded (e.g. ``"NS::Outer::Inner"``), with no separate parent-scope
reference to walk. This module's job is therefore the reverse of the other
three: PARSING a flat qualified name back into typed scope segments, not
building one while walking a tree.

**Namespace vs. record disambiguation is a heuristic, not a certainty --
verify against real MSVC output before trusting it further (this
environment has no MSVC/``cl.exe`` toolchain to do that with; see
``tests/test_msvc_pdb_e2e.py``'s own ``msvc``-marker gate).** A qualified
name's own text alone cannot say whether ``NS`` in ``"NS::Widget"`` is a
namespace or an enclosing class — CodeView's flat spelling loses that
distinction the same way it loses the tree DWARF/AST walkers get for free.
The one signal available post-hoc: check whether the ACCUMULATED prefix up
to that segment (e.g. ``"NS"``, then ``"NS::Middle"``, ...) is ITSELF a
name PDB separately recorded as a struct/class/union
(*known_record_names*, the caller's own ``DwarfMetadata.structs`` key set)
— if so, that segment is a :class:`~abicheck.model.identity.Record`;
otherwise it defaults to
:class:`~abicheck.model.identity.Namespace`. **Known, accepted limitation**:
a purely forward-declared enclosing class that is never itself given a
full definition anywhere in the same PDB would not appear in
*known_record_names* (``pdb_metadata._is_user_visible`` filters out a
forward-ref-only record entirely) and would therefore misclassify as a
namespace instead — an edge case this module cannot resolve from the
qualified-name text alone, matching the DWARF/header-AST backends' own
practice of documenting an evidence gap rather than guessing past it.

**No anonymous-type handling.** Unlike DWARF and the header-AST backends,
this module builds no :class:`~abicheck.model.identity.Anonymous` segment
at all — CodeView type records for an unnamed struct/union/enum are
synthesized by MSVC with an internal, compiler-generated name (not left
genuinely anonymous the way a DWARF DIE or a Clang/castxml AST node can
be), and ``pdb_metadata._is_user_visible`` already filters those
compiler-internal (``"<...>"``/``"__..."``-prefixed) names out entirely
before they ever reach this module. A real anonymous nested aggregate
that MSVC spells some other, unfiltered way is an unverified gap (see the
module docstring above on the lack of a real MSVC toolchain here), not a
case this module claims to handle.

Leaf module: depends on ``model`` (allowed: ``extract -> model``, ADR-061),
``model.qualified_name_split`` (the shared, dependency-free ``"::"``-
splitting primitive; see that module's own docstring for why it lives in
``model/`` rather than in ``qualified_name_segments.py``, which belongs to
the ``compare`` layer ``extract`` may not import), and
``extract.headers.scope_segments`` for the same shared segment
constructors DWARF and the two header-AST backends already use — nothing
above.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet

from ..model.identity import (
    EntityId,
    ScopePath,
    ScopeSegment,
    entity_id_for_enum,
    entity_id_for_type,
)
from ..model.qualified_name_split import split_top_level_scopes
from .headers.scope_segments import (
    namespace_segment as _namespace_segment,
    record_segment as _record_segment,
)

__all__ = ["enum_entity_id", "record_entity_id", "scope_path_for_qualified_name"]


def scope_path_for_qualified_name(
    qualified: str, known_record_names: AbstractSet[str]
) -> tuple[ScopePath, str]:
    """Split *qualified* into ``(scope_path, leaf_name)``.

    *known_record_names* is the caller's own set of qualified names PDB
    separately recorded as a struct/class/union (typically
    ``DwarfMetadata.structs.keys()``) — see this module's own docstring for
    why that lookup, not the segment text alone, is what decides
    :class:`~abicheck.model.identity.Record` vs.
    :class:`~abicheck.model.identity.Namespace` for each enclosing segment.

    >>> scope_path_for_qualified_name("Widget", frozenset())
    ((), 'Widget')
    >>> scope_path_for_qualified_name("NS::Widget", frozenset())
    ((Namespace(name='NS'),), 'Widget')
    >>> scope_path_for_qualified_name("Outer::Inner", frozenset({"Outer"}))
    ((Record(name='Outer', access='public'),), 'Inner')
    """
    segments = split_top_level_scopes(qualified)
    if not segments:
        return (), qualified
    leaf = segments[-1]
    scope: list[ScopeSegment] = []
    prefix_parts: list[str] = []
    for segment in segments[:-1]:
        prefix_parts.append(segment)
        prefix = "::".join(prefix_parts)
        if prefix in known_record_names:
            scope.append(_record_segment(segment))
        else:
            scope.append(_namespace_segment(segment))
    return tuple(scope), leaf


def record_entity_id(qualified: str, known_record_names: AbstractSet[str]) -> EntityId:
    """``EntityId`` for a PDB struct/class/union named *qualified*."""
    scope, leaf = scope_path_for_qualified_name(qualified, known_record_names)
    return entity_id_for_type(scope, leaf)


def enum_entity_id(qualified: str, known_record_names: AbstractSet[str]) -> EntityId:
    """``EntityId`` for a PDB enum named *qualified*. See
    :func:`record_entity_id`."""
    scope, leaf = scope_path_for_qualified_name(qualified, known_record_names)
    return entity_id_for_enum(scope, leaf)
