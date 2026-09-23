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

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

from ..elf_symbol_filter import (
    FUNCTION_SYMBOL_TYPES,
    VARIABLE_SYMBOL_TYPES,
    exported_symbol_names,
)
from ..model import Function
from ..model.surface_facts import in_public_surface, is_abi_visible
from .surface_reconcile import (
    RECONCILED_ABI_VISIBLE,
    RECONCILED_FUNCTION_MAPS,
    reconcile_declaration_lists,
)

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Variable

_ABI_TAG_RE = re.compile(r"\[abi:[^\]]*\]")

__all__ = [
    "abi_visible_functions",
    "alias_identity",
    "public_functions",
    "reconciled_abi_visible_functions",
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
    return _reconcile(
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
        alias_key=alias_identity,
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
    cached: tuple[dict[str, Function], dict[str, Function]] | None = _cached(
        old, new, RECONCILED_FUNCTION_MAPS
    )
    if cached is not None:
        return cached
    reconciled_old, reconciled_new = _reconcile(
        public_functions(old),
        public_functions(new),
        old_all=old.functions,
        new_all=new.functions,
        key=key,
        # The mangled key is exactly what a realistic change here moves, so
        # the declared name is the second tier -- the same "single peer or
        # nothing" rule the detectors' own demangled-name fallback applies to
        # their leftovers.
        alias_key=alias_identity,
        old_exported=exported_symbol_names(
            getattr(old, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
        new_exported=exported_symbol_names(
            getattr(new, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
    )
    return _store(
        old,
        new,
        RECONCILED_FUNCTION_MAPS,
        (
            {key(f): f for f in reconciled_old},
            {key(f): f for f in reconciled_new},
        ),
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
    return _reconcile(
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
    cross_old_funcs, cross_new_vars = _reconcile(
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
    cross_old_vars, cross_new_funcs = _reconcile(
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


def _needs_demangle(name: str, mangled: str) -> bool:
    """Whether :func:`qualified_declaration_name` must demangle *mangled*."""
    return (
        "::" not in name
        and not ("<" in name and not name.startswith("operator"))
        and mangled.startswith("_Z")
    )


_Old = TypeVar("_Old", Function, "Variable")
_New = TypeVar("_New", Function, "Variable")


def _reconcile(
    old_list: Sequence[_Old],
    new_list: Sequence[_New],
    *,
    old_all: Sequence[_Old],
    new_all: Sequence[_New],
    **kwargs: Any,
) -> tuple[list[_Old], list[_New]]:
    """`reconcile_declaration_lists`, after demangling every declaration its
    identity functions may ask about in **one** batch.

    Those identities (:func:`alias_identity`, :func:`cpo_identity`) resolve
    names one declaration at a time through :func:`qualified_declaration_name`,
    and each first-time name used to cost its own ``c++filt`` process --
    ~950 of them, 4.3 s of an 18.7 s synthetic compare. Batching up front
    turns every later lookup into a `demangle_batch` cache hit; the names and
    their demangled forms are unchanged.
    """
    from ..demangle import demangle_batch

    pending = [
        d.mangled
        for group in (old_all, new_all)
        for d in group
        if d.mangled and _needs_demangle(d.name, d.mangled)
    ]
    if pending:
        demangle_batch(pending)
    return reconcile_declaration_lists(
        old_list, new_list, old_all=old_all, new_all=new_all, **kwargs
    )


def qualified_declaration_name(name: str, mangled: str) -> str:
    """A declaration's namespace-qualified name.

        Moved here from ``diff_templates`` because it is an *identity* rule and
        this module is what keys surfaces on it. castxml does not
        namespace-qualify a variable's name, so the qualified spelling has to be
        recovered from the mangling; two ``foo``s in different namespaces are
        otherwise one key, and a first-seen merge then resolves the collision by
        list order rather than by identity (Codex review, P2).

    ``<`` still reads as "this is a template spelling, take it as given" --
        dropping that entirely changes what several template detectors see, and
        two of their tests say so -- but not when the name *starts* with
        ``operator``: ``operator<``, ``operator<=`` and ``operator<<`` are
        unqualified names that merely contain the character, so ``A::operator<``
        and ``B::operator<`` both reduced to one alias and reconciliation paired
        two unrelated declarations (Codex review, P1). A qualified operator
        carries ``::`` and is caught by the first test.

        Every call site's declarations are demangled in one batch first
        (:func:`_reconcile`), so the lookup here is a `demangle_batch` cache
        hit; the result is a pure function of the two spellings.
    """
    if _needs_demangle(name, mangled):
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


def abi_visible_functions(snap: AbiSnapshot) -> list[Function]:
    """The functions in *snap* that participate in the binary contract."""
    return [f for f in snap.functions if is_abi_visible(f)]


_T = TypeVar("_T")


def _cached(old: AbiSnapshot, new: AbiSnapshot, slot: str) -> Any:
    """The result already computed for exactly this pair, if any.

    The same per-pair memo `surface_reconcile` applies to the two
    mangled-keyed surfaces, for the surfaces this module owns: several
    detectors ask for each of them, and building one resolves an identity
    for every declaration in both full maps. Without this the PR-vs-base
    performance gate measured `add_remove` 33-37% slower -- the second time
    in this change that recomputing a shared join per detector showed up
    there rather than in any test.
    """
    entry = old.__dict__.get(slot)
    return entry[1] if entry is not None and entry[0] is new else None


def _store(old: AbiSnapshot, new: AbiSnapshot, slot: str, result: _T) -> _T:
    old.__dict__[slot] = (new, result)
    return result


def reconciled_abi_visible_functions(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[list[Function], list[Function]]:
    """:func:`reconciled_public_functions` for the detectors that select on
    :func:`~abicheck.model.surface_facts.is_abi_visible`.

    That predicate is the union "exported *or* promised", so it inherits the
    same gap: a promised-but-unexported declaration is ABI-visible only on
    the side whose run established the promise. The const-overload and SYCL
    overload-set detectors compare *sets* of such declarations, so the
    asymmetry read as a removed overload -- a false `REMOVED_CONST_OVERLOAD`
    and a false `SYCL_OVERLOAD_SET_REMOVED` on identical declarations (Codex
    review, P1).
    """
    cached: tuple[list[Function], list[Function]] | None = _cached(
        old, new, RECONCILED_ABI_VISIBLE
    )
    if cached is not None:
        return cached
    return _store(
        old,
        new,
        RECONCILED_ABI_VISIBLE,
        _reconcile(
            abi_visible_functions(old),
            abi_visible_functions(new),
            old_all=old.functions,
            new_all=new.functions,
            key=lambda f: f.mangled or f.name,
            alias_key=alias_identity,
            old_exported=exported_symbol_names(
                getattr(old, "elf", None), FUNCTION_SYMBOL_TYPES
            ),
            new_exported=exported_symbol_names(
                getattr(new, "elf", None), FUNCTION_SYMBOL_TYPES
            ),
        ),
    )


def alias_identity(decl: Function | Variable) -> str:
    """The ambiguity-safe second tier's identity: a declaration's
    *namespace-qualified* stem.

    The bare declared name is not an identity. Header-AST backends commonly
    store only the leaf, so `A::foo` and `B::foo` are one key -- and since
    each is unique under it, the tier's "single peer or nothing" rule pairs
    two unrelated declarations rather than declining. Measured: an
    evidence-poor `A::foo(char *)` beside an evidence-backed
    `B::foo(char8_t *)` reconciled as one declaration and reported a false
    `CHAR8T_MIGRATION` with a `BREAKING` verdict, where the honest answer is
    a removal and an addition (Codex review, P1).

    The signature is dropped but the namespace kept, because those are the
    two things this tier needs at once: it exists to pair a declaration
    whose *mangling* changed (`A::foo(char *)` -> `A::foo(char8_t *)`, one
    entity), and it must not pair declarations that differ by more than
    that.

    Itanium ABI tags are stripped for the same reason the signature is:
    they live in the *mangling* (`_Z3fooB3barv` demangles to
    `foo[abi:bar]()`), so keeping them would make a tag gain or loss look
    like a different entity -- and pairing exactly that pair is what lets
    the ABI-tag detector see a tag change at all rather than a removal plus
    an addition.

    The truncation is deliberately cruder than the display stems
    `diff_templates` computes: it cuts at the first parenthesis, so
    `A::operator()` and `A::operator()(int)` share an identity. That is
    correct for this purpose -- they are the same entity name in the same
    namespace -- and it keeps the rule a leaf that needs no part of the
    template machinery.
    """
    qualified = qualified_declaration_name(decl.name, decl.mangled) or decl.name
    head, sep, _ = qualified.partition("(")
    stem = head.rstrip() if sep else qualified
    return _ABI_TAG_RE.sub("", stem)
