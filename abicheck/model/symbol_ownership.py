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

"""Which scope *owns* a mangled symbol (and is that scope an
internal-implementation namespace)?

Split out of ``name_classification.py``, which answers a different question:
what *shape* is this name (RTTI artifact, local name, stdlib-owned, ...).
Ownership is not a name shape -- answering it needs the real Itanium
nested-name parser (``model.mangled_name``), and the reason this module
exists at all is that re-deriving a weaker copy of that parsing is exactly
what went wrong before: the predicate here used to be a substring scan over
the **whole** mangled string, which attributes a *parameter type's*
namespace to the function itself.

``name_classification`` stays deliberately dependency-free (it sits at the
bottom of the graph so anything may import it) and does **not** re-export
these names: doing so would make the one-way dependency here a cycle.
Importers name this module directly.
"""

from __future__ import annotations

from ..name_classification import INTERNAL_NAMESPACE_COMPONENTS, is_rtti_symbol

__all__ = [
    "INTERNAL_NAMESPACE_NAMES",
    "has_internal_namespace_component",
    "owning_scope_components",
    "symbol_origin",
]


# The same conventional internal namespaces as bare identifiers, for callers
# that already hold *parsed* scope components rather than raw mangled text.
# Kept in lockstep with INTERNAL_NAMESPACE_COMPONENTS above by
# ``tests/test_name_classification.py``.
INTERNAL_NAMESPACE_NAMES: frozenset[str] = frozenset(
    {"internal", "detail", "impl", "__detail", "_impl"}
)


def _internal_component_in_region(region: str) -> bool:
    """Return True if *region* -- a slice of a mangled name that contains only
    scope encoding -- carries a conventional internal-namespace component."""
    return any(comp in region for comp in INTERNAL_NAMESPACE_COMPONENTS)


def owning_scope_components(symbol: str) -> list[str] | None:
    """Return the namespace/class components that *own* *symbol*, or None.

    "Own" means strictly enclosing: the entity's own leaf name is dropped, so
    a function *named* ``detail`` is not mistaken for one *declared in* a
    ``detail`` namespace. Handles both ordinary entities (``_ZN...``) and the
    vtable/typeinfo/VTT special names, whose owner is the type they describe.

    Returns None for the forms the structural parser does not model
    (constructors, operators, unmangled C names, ...) so callers can fall
    back to :func:`_nested_name_region`.
    """
    from .mangled_name import (
        itanium_scope_components,
        itanium_special_name_owner_scope_components,
    )

    special = itanium_special_name_owner_scope_components(symbol)
    comps = special[0] if special is not None else itanium_scope_components(symbol)
    if comps is None:
        return None
    # Drop the entity's (or, for a special name, the owning type's) own leaf.
    return comps[:-1]


def _nested_name_region(symbol: str) -> str:
    """Return the leading nested-name region of an Itanium mangled *symbol*.

    A deliberately conservative textual fallback for the shapes
    :func:`owning_scope_components` returns None for. The nested-name
    encoding of the entity itself is everything up to the first ``E``; the
    parameter types that follow it -- the source of the misattribution this
    helper exists to avoid -- are excluded. Template arguments also contain
    ``E``, so this can truncate early: that under-detects rather than
    over-detects, which is the safe direction (see :func:`symbol_origin`).
    """
    if not symbol.startswith("_Z"):
        return symbol
    end = symbol.find("E")
    return symbol if end == -1 else symbol[:end]


def has_internal_namespace_component(name: str) -> bool:
    """Return True if the scope that *owns* *name* is a conventional
    internal-implementation namespace (``detail``, ``impl``, ``internal``, ...).

    Only the *owning* scope is consulted, never the whole mangled string. A
    mangled name embeds its parameter types, so a whole-string scan reports a
    public function as internal whenever any argument type happens to live in
    such a namespace -- e.g. ``svs::consume(const svs::detail::Token&)``
    mangles to ``_ZN3svs7consumeERKNS_6detail5TokenE``, whose ``6detail``
    belongs to the parameter, not to ``consume``'s own namespace. That
    misattribution fed the report's public/internal surface breakdown, so a
    genuinely public break was presented to users as internal churn.

    Used by :func:`symbol_origin`; also exposed as a building block for the
    planned report view-model (C2) and the ``model.py`` split (C10).
    """
    comps = owning_scope_components(name)
    if comps is not None:
        return any(c in INTERNAL_NAMESPACE_NAMES for c in comps)
    return _internal_component_in_region(_nested_name_region(name))


def symbol_origin(symbol: str) -> str:
    """Best-effort origin of a (usually mangled) symbol.

    Returns ``"rtti"``, ``"internal"`` or ``"public"``. RTTI is checked first:
    an RTTI symbol for an internal type (e.g. ``_ZTIN4daal8internal3FooE``)
    classifies as ``"rtti"``, mirroring the historical behaviour.

    **This answers symbol representation and naming convention, not
    public-contract membership** -- a vtable for a public, user-derivable
    class is public surface, and ``"internal"`` rests on a convention the
    library may not follow. Contract membership is ADR-049's question
    (``--contract``); a caller must not present these buckets as evidence
    that a finding is not a public break.

    Used to explain why a large C++ ``breaking`` count is dominated by churn in
    RTTI artifacts or internal-namespace symbols rather than genuine public-API
    breaks (a common pattern in libraries built without ``-fvisibility=hidden``).
    """
    if is_rtti_symbol(symbol):
        return "rtti"
    if has_internal_namespace_component(symbol):
        return "internal"
    return "public"
