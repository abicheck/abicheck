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

"""When a name missing from a source-surface index is really *removed*.

The membership predicate an index is built with
(``model/declaration_surface.in_source_surface``) already keeps a
still-declared, no-longer-exported declaration in the index -- that was
the reproducing defect. This module closes the residual half: a
declaration can also fall out of a *public* source index without being
removed from anywhere, because its own ``ScopeOrigin`` moved outside the
public-header set. "Its defining header is now classified private" is a
scope change, not a removal, and the two must not produce the same
finding.

Kept out of ``diff_namespaces.py`` (which sits at its
``architecture/debt.yaml`` no-growth baseline) and expressed over plain
qualified-name strings, so any future source-axis detector can apply the
identical rule instead of re-deriving it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING

from ..model import source_removal_supported

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function

__all__ = ["declarations_by_qualified_name", "removal_is_supported"]


def declarations_by_qualified_name(
    snap: AbiSnapshot,
    qualified_name: Callable[[str, str, dict[str, str] | None], str],
    demangled: dict[str, str] | None,
) -> dict[str, Function]:
    """Every function in *snap* keyed by its qualified declared name.

    Deliberately unfiltered -- no visibility, origin or export predicate --
    because its one consumer asks "does the new side still declare this at
    all", and answering that from an already-filtered view is how the
    conflation this fix undoes got in. *qualified_name* is the caller's own
    name-resolution function, injected rather than imported, so this module
    stays free of the flat detector it serves.

    Last write wins on a duplicate qualified name (two overloads sharing an
    undemangled declared name). Safe for the one question asked of the
    result: overloads of one declared name are all declared or all absent
    together as far as ``declared_fact`` is concerned, since that fact
    records which producer parsed the declaration, not anything that varies
    per overload.
    """
    out: dict[str, Function] = {}
    for f in snap.functions:
        qname = qualified_name(f.name, f.mangled, demangled)
        if qname:
            out[qname] = f
    return out


def removal_is_supported(
    new_declarations: Mapping[str, Function] | None, qnames: Iterable[str]
) -> bool:
    """Whether the disappearance of *qnames* may be reported as a removal.

    ``True`` when *new_declarations* is ``None`` (the caller has no
    new-side view, so nothing can be established and the pre-fix behaviour
    stands) or when at least one of *qnames* is genuinely gone.

    "At least one", not "all", for the same reason the caller's own
    ``still_linked`` check is an ``all``: several overloads can share one
    undemangled declared name, and a single surviving sibling must never
    suppress a genuinely removed one.
    """
    if new_declarations is None:
        return True
    names = list(qnames)
    if not names:
        return True
    return any(source_removal_supported(new_declarations.get(q)) for q in names)
