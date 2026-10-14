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


"""Unqualified-lookup evidence for the leaf widening in
:func:`~abicheck.compare.opaque_types.find_by_value_types`.

A bare ``Handle`` written inside ``other::f`` names ``other::Handle`` when
the snapshot declares one, not ``ns::Handle``: C++ unqualified lookup stops
at the innermost enclosing scope that declares the name. These helpers say
when the snapshot proves that, and otherwise leave the conservative
"exposed by value" reading in place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.qualified_name_split import split_top_level_scopes

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["leaf_hidden", "leaf_hiders", "scope_of"]


def scope_of(name: str) -> tuple[str, ...]:
    """Enclosing scopes of a qualified declaration name (parameter list cut).

    Only a ``(`` outside template arguments starts the parameter list, so an
    owner such as ``ns::Box<void (*)()>`` keeps its full spelling."""
    depth = 0
    for i, ch in enumerate(name):
        if ch == "<":
            depth += 1
        elif ch == ">" and depth:
            depth -= 1
        elif ch == "(" and not depth:
            name = name[:i]
            break
    return tuple(split_top_level_scopes(name)[:-1])


def leaf_hiders(
    snap: AbiSnapshot, opaque: set[str]
) -> dict[str, list[tuple[str, ...]]]:
    """For each qualified candidate, the scopes of *other* declarations in
    *snap* that share its leaf spelling. Only a declaration whose scope is
    known (a top-level ``::`` in ``qualified_name`` or ``name``) counts."""
    by_leaf: dict[str, list[tuple[str, ...]]] = {}
    for t in snap.declarations.types:
        spelled = t.qualified_name or t.name
        segs = split_top_level_scopes(spelled)
        if len(segs) > 1:
            by_leaf.setdefault(segs[-1], []).append(tuple(segs[:-1]))
    hiders: dict[str, list[tuple[str, ...]]] = {}
    for tname in opaque:
        segs = split_top_level_scopes(tname)
        if len(segs) > 1:
            own = tuple(segs[:-1])
            found = [p for p in by_leaf.get(segs[-1], ()) if p != own]
            if found:
                hiders[tname] = found
    return hiders


def leaf_hidden(
    tname: str, hiders: list[tuple[str, ...]], scope: tuple[str, ...]
) -> bool:
    """Whether a bare leaf reference written in *scope* provably names a
    different declaration than *tname*: unqualified lookup finds a same-leaf
    declaration in a scope enclosing *scope* that is deeper than *tname*'s
    own scope (or *tname*'s scope does not enclose *scope* at all). An
    unknown (global/empty) *scope* proves nothing."""
    if not scope:
        return False
    own = scope_of(tname)
    own_encloses = scope[: len(own)] == own
    return any(
        scope[: len(p)] == p and (not own_encloses or len(p) > len(own)) for p in hiders
    )
