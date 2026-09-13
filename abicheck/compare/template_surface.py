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

"""The compared public function *list* the template detectors select, and
its evidence-gap reconciliation.

``diff_templates`` selects a list straight off ``AbiSnapshot.functions``
rather than the mangled-keyed map ``diff_symbols`` builds, so it needs the
list-shaped side of :mod:`~abicheck.compare.surface_reconcile`. Owning both
the selector and the join here keeps that pairing in one place -- and in
``compare``, whose job matching two sides' entities is (ADR-061) -- instead
of in a detector module already at its debt baseline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from ..elf_symbol_filter import (
    FUNCTION_SYMBOL_TYPES,
    VARIABLE_SYMBOL_TYPES,
    exported_symbol_names,
)
from ..model import Function
from ..model.surface_facts import in_public_surface
from .surface_reconcile import reconcile_declaration_lists

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Variable

__all__ = [
    "public_functions",
    "public_variables",
    "reconciled_cpo_surfaces",
    "reconciled_public_variables",
    "reconciled_public_function_maps",
    "reconciled_public_functions",
]


def public_functions(snap: AbiSnapshot) -> list[Function]:
    """Return the subset of public functions in *snap*."""
    return [f for f in snap.functions if in_public_surface(f)]


def reconciled_public_functions(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[list[Function], list[Function]]:
    """Both sides' public function lists, evidence-gap-reconciled.

    :func:`public_functions` builds each side's list from that side's own
    facts, and one of them -- ``in_public_contract`` -- is only *established*
    when that run's producer was given a public-header set. So two captures
    of an unchanged library, one with that set and one without, disagree
    about every promised-but-unexported instantiation, and
    ``diff_templates.detect_internal_template_leaks`` reads the disagreement
    as an OLD instantiation NEW no longer emits: a false breaking
    ``INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API`` (Codex review, P1).

    Routed through the shared list-shaped join rather than a local guard, so
    no second copy of the rule exists -- see
    :mod:`abicheck.compare.surface_reconcile` for why the repair belongs to
    the surface and not to each disposition site.
    """
    return reconcile_declaration_lists(
        public_functions(old),
        public_functions(new),
        old_all=old.functions,
        new_all=new.functions,
        key=lambda f: f.mangled or f.name,
        # Same second tier as the keyed variant below, and needed for the
        # same reason: a change can move the mangled key itself (an ABI-tag
        # gain or loss *is* a mangling change, `_Z3fooB3barv` ->
        # `_Z3foov`), leaving the exact-key lookup with no peer to admit
        # (Codex review, P2).
        alias_key=lambda f: f.name,
        old_exported=exported_symbol_names(
            getattr(old, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
        new_exported=exported_symbol_names(
            getattr(new, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
    )


def reconciled_public_function_maps(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    key: Callable[[Function], str] = lambda f: f.mangled,
) -> tuple[dict[str, Function], dict[str, Function]]:
    """:func:`reconciled_public_functions`, as the mangled-keyed maps the
    type-spelling and integer-model detectors join on.

    They select with the same ``in_public_surface`` predicate as the template
    detectors, then key the result themselves -- so the evidence asymmetry
    costs them the same pair, and with it a real finding: a
    ``char *`` -> ``char8_t *`` return change on a promised-but-unexported
    function reported ``CHAR8T_MIGRATION`` only when both sides happened to
    carry contract evidence, and an ``int`` -> ``long`` group likewise lost
    ``INTEGER_MODEL_CHANGED`` (Codex review, P2).

    *key* is used for the reconciliation join as well as for the returned
    maps, so the two cannot disagree about what counts as the same
    declaration. Later wins on a duplicate key, matching the dict
    comprehensions this replaces.
    """
    reconciled_old, reconciled_new = reconcile_declaration_lists(
        public_functions(old),
        public_functions(new),
        old_all=old.functions,
        new_all=new.functions,
        key=key,
        # The mangled key is exactly what a realistic change here moves, so
        # the declared name is the second tier -- the same "single peer or
        # nothing" rule the detectors' own demangled-name fallback applies to
        # their leftovers.
        alias_key=lambda f: f.name,
        old_exported=exported_symbol_names(
            getattr(old, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
        new_exported=exported_symbol_names(
            getattr(new, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
    )
    return (
        {key(f): f for f in reconciled_old},
        {key(f): f for f in reconciled_new},
    )


def public_variables(snap: AbiSnapshot) -> list[Variable]:
    """Return the subset of public variables in *snap*."""
    return [v for v in snap.variables if in_public_surface(v)]


def reconciled_public_variables(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[list[Variable], list[Variable]]:
    """:func:`reconciled_public_functions` for data symbols.

    The CPO detector compares a function population against a *variable*
    one, so reconciling only the functions would leave the same asymmetry
    reachable through the other half of its own comparison.

    No alias tier: two differing mangled names are two different exports,
    and a display-name join would pair declarations that are not the same
    entity -- the same reasoning ``SymbolIdentityIndex`` records for
    declining a variable alias tier.
    """
    return reconcile_declaration_lists(
        public_variables(old),
        public_variables(new),
        old_all=old.variables,
        new_all=new.variables,
        key=lambda v: v.mangled or v.name,
        old_exported=exported_symbol_names(
            getattr(old, "elf", None), VARIABLE_SYMBOL_TYPES
        ),
        new_exported=exported_symbol_names(
            getattr(new, "elf", None), VARIABLE_SYMBOL_TYPES
        ),
    )


def reconciled_cpo_surfaces(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    identity: Callable[[Any], str],
) -> tuple[list[Function], list[Variable], list[Function], list[Variable]]:
    """Both sides' function and variable populations for the CPO detector,
    reconciled *across* kinds as well as within them.

    That detector's whole subject is a declaration changing kind -- a
    customization point that was a function becoming an object, or the
    reverse -- so its two populations are joined by qualified name, not by
    kind. Same-kind reconciliation cannot restore the evidence-poor side's
    declaration there: the function pass never looks in ``new.variables``
    and the variable pass never looks in ``old.functions``, so under either
    asymmetry the transition read as a bare removal plus addition and
    ``CPO_KIND_CHANGED`` was lost (Codex review, P2).

    The cross-kind pass reuses the same admission rule, parameterized over
    the two declaration types rather than copied, and resolves on the same
    ambiguity-safe "single peer or nothing" tier used everywhere else here.

    *identity* is supplied by the caller rather than chosen here, and it must
    be the detector's own: a bare ``Function.name``/``Variable.name`` is not
    an identity across namespaces -- castxml does not namespace-qualify a
    variable's name at all, so two declarations of ``foo`` in different
    namespaces collide on it, and the first-seen rule then resolves the
    collision by *list order* rather than by identity (Codex review, P2).
    An order-dependent merge is a defect in its own right here, whatever a
    given caller currently exercises: ``AGENTS.md`` records six review rounds
    spent on exactly that failure in the last shared merge primitive this
    repository grew.
    """
    old_funcs, new_funcs = reconciled_public_functions(old, new)
    old_vars, new_vars = reconciled_public_variables(old, new)
    cross_old_funcs, cross_new_vars = reconcile_declaration_lists(
        old_funcs,
        new_vars,
        old_all=old.functions,
        new_all=new.variables,
        key=identity,
        alias_key=identity,
        old_exported=exported_symbol_names(
            getattr(old, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
        new_exported=exported_symbol_names(
            getattr(new, "elf", None), VARIABLE_SYMBOL_TYPES
        ),
    )
    cross_old_vars, cross_new_funcs = reconcile_declaration_lists(
        old_vars,
        new_funcs,
        old_all=old.variables,
        new_all=new.functions,
        key=identity,
        alias_key=identity,
        old_exported=exported_symbol_names(
            getattr(old, "elf", None), VARIABLE_SYMBOL_TYPES
        ),
        new_exported=exported_symbol_names(
            getattr(new, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
    )
    return (cross_old_funcs, cross_old_vars, cross_new_funcs, cross_new_vars)


def qualified_declaration_name(name: str, mangled: str) -> str:
    """A declaration's namespace-qualified name.

    Moved here from ``diff_templates`` because it is an *identity* rule and
    this module is what keys surfaces on it. castxml does not
    namespace-qualify a variable's name, so the qualified spelling has to be
    recovered from the mangling; two ``foo``s in different namespaces are
    otherwise one key, and a first-seen merge then resolves the collision by
    list order rather than by identity (Codex review, P2).
    """
    if "::" in name or "<" in name:
        return name
    if mangled.startswith("_Z"):
        from ..demangle import demangle_batch

        return demangle_batch([mangled]).get(mangled, name)
    return name


def cpo_identity(
    decl: Function | Variable, *, function_stem: Callable[[str], str]
) -> str:
    """The CPO detector's identity: the qualified name, reduced to the stem
    that identifies the *customization point* rather than one spelling of it.

    It has to agree across kinds, which the raw qualified name does not: a
    function demangles to ``ns1::foo()`` and the variable it became to
    ``ns1::foo``, so keying on the qualified name alone pairs neither. The
    detector already reduces a function to that stem for its own comparison;
    *function_stem* is that same reduction, passed in rather than
    reimplemented so the join and the comparison cannot drift apart.

    Never the bare declared name: castxml does not namespace-qualify a
    variable's name, so two ``foo``s in different namespaces would collide
    on one key and a first-seen merge would then resolve the collision by
    list order (Codex review, P2).
    """
    qname = qualified_declaration_name(decl.name, decl.mangled) or decl.name
    return function_stem(qname) if isinstance(decl, Function) else qname
