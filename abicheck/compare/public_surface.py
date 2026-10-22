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

"""The compared public surface: which functions and variables each side of a
comparison actually exposes, evidence-gap-reconciled across the two sides.

Every per-pair function or variable detector asks this module for its
old/new maps rather than deciding for itself what counts as public. The
answer depends on three things that must agree everywhere: the ABI-visibility
filter, the ELF export table (narrowed by transitive C++ runtime filtering
when the snapshot is not the runtime itself), and the evidence-gap join that
keeps a declaration which left one side's surface only because that run
established less contract evidence from reading as a removal.

Owning the join here, rather than in ``diff_symbols``, is what lets the
sibling diff modules (``diff_types``, ``diff_templates``, and, once its split
lands, ``diff_platform``) use the same surface without importing another
detector's private helpers. ``diff_platform`` still relies on aliases in
``diff_symbols`` until then.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..elf_symbol_filter import (
    FUNCTION_SYMBOL_TYPES,
    VARIABLE_SYMBOL_TYPES,
    exported_symbol_names,
    is_abi_relevant_elf_symbol,
)
from ..model import is_cxx_runtime_library
from ..model.surface_facts import (
    is_abi_visible,
    is_export_table_only_record,
)
from ..name_classification import is_local_rtti_symbol
from . import export_transition as _export_transition
from .edge_query import ObservedExportTable, observed_export_table
from .finding_identity import SymbolIdentityIndex
from .implicit_member_surface import (
    drop_orphan_implicit_members,
    is_unexported_implicit_member,
)
from .surface_reconcile import (
    RECONCILED_FUNCTIONS,
    RECONCILED_VARIABLES,
    cached_reconciliation,
    reconcile_surfaces,
    store_reconciliation,
)

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function, Variable


def should_filter_transitive_runtime_symbols(snap: AbiSnapshot) -> bool:
    """Return True when transitive C++ runtime symbols should be filtered.

    Returns False when ``snap.library`` or the ELF SONAME identifies *snap* as
    the C++ runtime itself, where runtime-owned symbols are the inspected ABI.
    """
    elf = getattr(snap, "elf", None)
    return not (
        is_cxx_runtime_library(snap.library)
        or is_cxx_runtime_library(getattr(elf, "soname", ""))
    )


def is_extern_c_function(f: Function) -> bool:
    """Eligibility predicate for the name-alias fallback below."""
    return f.is_extern_c


def observed_exports(snap: AbiSnapshot, types: frozenset[str]) -> ObservedExportTable:
    """The names *snap*'s own export table carries (empty when it has none),
    with the table's ``exports`` coverage, so a reader can ask whether a
    missing name is proven absent (``compare.edge_query``, I4).

    The removal paths cross-check against this rather than trusting a
    declaration's own, possibly legacy-bridged, ``binary_exported`` fact --
    see ``export_transition.surface_exit_is_evidence_gap``.
    """
    return observed_export_table(
        snap,
        exported_symbol_names(
            getattr(snap, "elf", None),
            types,
            abi_relevant_only=True,
            filter_transitive_runtime_symbols=(
                should_filter_transitive_runtime_symbols(snap)
            ),
        ),
    )


def public_functions(snap: AbiSnapshot) -> dict[str, Function]:
    """Return public/ELF-only functions from *snap*.

    When ELF dynamic-symbol evidence is available, narrow the DWARF-derived
    public set to names that are actually exported (or explicitly ``= delete``,
    so an API becoming deleted stays observable). This keeps transitive
    runtime/stdlib subprograms that slipped into the DWARF DIEs out of the diff.

    The narrowing only happens when exports are present: a snapshot with no ELF
    symbol table (``elf`` absent/empty) keeps the full DWARF set untouched.

    Caveat: this trusts the ELF symbol table to be reasonably complete. A
    *partially* captured table (e.g. only a stripped ``.symtab`` subset) could in
    theory hide a genuine removal — but DWARF-primary snapshots carry the full
    ``.dynsym``, so in practice the export set is authoritative here.
    """
    filter_transitive_runtime_symbols = should_filter_transitive_runtime_symbols(snap)
    funcs = {
        k: v
        for k, v in snap.function_map.items()
        if (
            is_abi_visible(v)
            and (
                not is_export_table_only_record(v)
                or is_abi_relevant_elf_symbol(
                    k,
                    filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
                )
            )
        )
    }
    elf = getattr(snap, "elf", None)
    if elf is None or not getattr(elf, "symbols", None):
        return funcs
    exported = exported_symbol_names(
        elf,
        FUNCTION_SYMBOL_TYPES,
        abi_relevant_only=True,
        filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
    )
    name_counts: dict[str, int] = {}
    for f in funcs.values():
        name_counts[f.name] = name_counts.get(f.name, 0) + 1
    return {
        k: v
        for k, v in funcs.items()
        if _export_transition.survives_export_narrowing(k, v, exported, name_counts)
    }


def impact_candidate_functions(snap: AbiSnapshot) -> dict[str, Function]:
    """:func:`public_functions` minus unexported compiler-generated members.

    The population a type change's ``affected_symbols`` is drawn from: an
    implicit special member nothing exports is synthesized inside each
    consumer, so it is not a library symbol a consumer is affected through
    -- and for a castxml dump its key is an internal placeholder
    (``__abicheck_ctor__T()``) that must never reach a report (#1713).
    """
    return {
        k: f
        for k, f in public_functions(snap).items()
        if not is_unexported_implicit_member(f)
    }


def _full_map_alias_resolver(
    full_map: dict[str, Function],
) -> Callable[[str, Function], Function | None]:
    """Resolve a key to *full_map*'s declaration through the same
    ambiguity-safe ``extern "C"`` alias tier ``_match_old_function`` uses.

    An ``extern "C"`` declaration is spelled by its bare name on one side and
    by a C++ mangling on the other, so an exact-key lookup misses the peer
    and the surviving pair reads as a removal plus an addition instead of the
    linkage change it is (Codex review, P2). Eligibility is unchanged from
    the symbol join: an extern-C declaration may match any single same-named
    peer, a C++ one only an extern-C peer -- and "no candidate" and "several
    candidates" both answer ``None``, so an overload set is never guessed at.
    """
    # Built on first use, not at construction: the index resolves a canonical
    # identity per declaration in the full map, and a pair with no
    # evidence-gap candidate at all must not pay for it.
    index: list[SymbolIdentityIndex[Function]] = []

    def _resolve(key: str, decl: Function) -> Function | None:
        if not index:
            index.append(SymbolIdentityIndex.for_functions(full_map))
        match = index[0].unique_alias_match(
            f"name:{decl.name}",
            where=None if decl.is_extern_c else is_extern_c_function,
        )
        return None if match is None else match.declaration

    return _resolve


def reconciled_function_surfaces(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[dict[str, Function], dict[str, Function]]:
    """Both sides' public function surfaces, evidence-gap-reconciled.

    The one join every per-pair function detector uses in place of a bare
    ``public_functions`` pair -- in this module *and* in the sibling diff
    modules that build their own old/new surfaces (``diff_types``,
    ``diff_platform_templates``, ``diff_platform``, and ``diff_templates``'s
    lambda-closure demotion pass), so a declaration that left one side's
    surface only because that run established less contract evidence is
    visible to all of them rather than to whichever call site happened to
    guard it. A caller whose surface is a *list* rather than a mangled-keyed
    map -- ``diff_templates``'s internal-template-leak pass -- joins through
    ``surface_reconcile.reconcile_declaration_lists`` instead, which is the
    same rule over the same primitive. Limiting this to one module
    left `diff_types`'s own overload/qualifier detectors on the unreconciled
    maps -- a false ``OVERLOAD_ADDED``, and an ``is_static`` change on a
    surviving declaration reported by nothing (Codex review, P1). See
    :mod:`abicheck.compare.surface_reconcile`.
    """
    cached = cached_reconciliation(old, new, RECONCILED_FUNCTIONS)
    if cached is not None:
        return cached
    result = reconcile_surfaces(
        public_functions(old),
        public_functions(new),
        old_all=old.function_map,
        new_all=new.function_map,
        old_exported=observed_exports(old, FUNCTION_SYMBOL_TYPES),
        new_exported=observed_exports(new, FUNCTION_SYMBOL_TYPES),
        resolve_in_old=_full_map_alias_resolver(old.function_map),
        resolve_in_new=_full_map_alias_resolver(new.function_map),
    )
    # Implicit members of a record only one side declares are entailed by
    # that record's TYPE_ADDED/TYPE_REMOVED, not API findings (#1713).
    result = drop_orphan_implicit_members(*result, old, new)
    return store_reconciliation(old, new, RECONCILED_FUNCTIONS, result)


def public_variables(snap: AbiSnapshot) -> dict[str, Variable]:
    """Return public/ELF-only variables from *snap*.

    Excludes RTTI/vtable symbols of function-local types (lambda closures and
    other in-function types): they are not nameable public ABI and only churn
    across builds (RD2-4).
    """
    filter_transitive_runtime_symbols = should_filter_transitive_runtime_symbols(snap)
    return {
        k: v
        for k, v in snap.variable_map.items()
        if (
            is_abi_visible(v)
            and (
                not is_export_table_only_record(v)
                or is_abi_relevant_elf_symbol(
                    k,
                    filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
                )
            )
            and not is_local_rtti_symbol(k)
        )
    }


def reconciled_variable_surfaces(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[dict[str, Variable], dict[str, Variable]]:
    """:func:`reconciled_function_surfaces` for data symbols, and for the
    same reason: the variable value/access detectors join their own filtered
    maps too, so reconciling only inside the removal path would leave them
    blind to the pair (Codex review, P1)."""
    cached = cached_reconciliation(old, new, RECONCILED_VARIABLES)
    if cached is not None:
        return cached
    return store_reconciliation(
        old,
        new,
        RECONCILED_VARIABLES,
        reconcile_surfaces(
            public_variables(old),
            public_variables(new),
            old_all=old.variable_map,
            new_all=new.variable_map,
            old_exported=observed_exports(old, VARIABLE_SYMBOL_TYPES),
            new_exported=observed_exports(new, VARIABLE_SYMBOL_TYPES),
        ),
    )
