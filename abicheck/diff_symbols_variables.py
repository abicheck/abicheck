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

"""Public-variable comparison helpers: alignment changes and top-level
const/reference-aware type-spelling normalization.

Leaf module (must not import from ``diff_symbols`` to avoid an import
cycle). ``diff_symbols._check_variable`` is the sole caller.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .checker_types import Change
from .compare import export_transition as _export_transition
from .compare.declaration_facts import alignment_changes
from .compare.edge_query import ObservedExportTable, observed_export_table
from .compare.elf_only_demangle import (
    elf_only_demangled_name as _elf_only_demangled_name,
)
from .compare.variables import (
    VariableTypeIndex,
    variable_type_changes,
    variable_type_index,
)
from .diff_helpers import make_change
from .diff_symbols_renames import _should_filter_transitive_runtime_symbols
from .elf_symbol_filter import (
    exported_symbol_names,
    is_abi_relevant_elf_symbol,
)
from .model import AbiSnapshot, AccessLevel, Function, Variable
from .model.change_catalog.kinds import ChangeKind
from .model.semantic_ir_variable_payload import variable_canonical_entity
from .model.surface_facts import (
    is_abi_visible,
    is_export_confirmed_absent,
    is_export_table_only_record,
    surface_fact_summary,
)
from .name_classification import (
    is_local_rtti_symbol,
)


def _is_access_narrowing(old_access: Any, new_access: Any) -> bool:
    """Return True if the access level transition is narrowing (breaking).

    Narrowing = less accessible: public→protected, public→private, protected→private.
    Widening (e.g., private→public) is backward-compatible and should NOT be flagged.

    Shared by ``diff_symbols.py``'s method/field access-narrowing checks and
    :func:`var_access_changes` below — moved here (rather than staying in
    ``diff_symbols.py``) purely to make room under that file's 2000-line hard
    cap; not otherwise Variable-specific.
    """
    _RANK = {AccessLevel.PUBLIC: 0, AccessLevel.PROTECTED: 1, AccessLevel.PRIVATE: 2}  # pylint: disable=invalid-name
    return _RANK.get(new_access, 0) > _RANK.get(old_access, 0)


def _var_removed(mangled: str, v_old: Variable) -> list[Change]:
    """A public variable with no peer in the NEW side's public surface.

    Both surfaces are evidence-gap-reconciled before any detector runs
    (:mod:`abicheck.compare.surface_reconcile`), so a key that is still
    missing here is genuinely absent rather than merely unestablished --
    which is why this function needs no evidence guard of its own.
    """
    return [
        make_change(
            ChangeKind.VAR_REMOVED,
            symbol=mangled,
            name=v_old.name,
            # See Change.symbol_binding's docstring - None when not captured.
            symbol_binding=v_old.elf_binding.value if v_old.elf_binding else None,
            entity_id=v_old.entity_id,
            demangled_symbol=_elf_only_demangled_name(mangled, v_old),
            # See _check_removed_function's identical stamp.
            surface_facts=surface_fact_summary(v_old),
        )
    ]


def _unexported_shape(decl: Function | Variable) -> str | None:
    """Why a declaration legitimately has no exported symbol, or ``None``.

    The answer comes from the declaration itself, never from the export
    table's silence alone: an inline function (explicit, ``constexpr``, or
    defined in its class) and an internal-linkage constant are emitted in
    each consumer's own object file; a pure virtual or deleted function has
    no definition to export at all. Anything else that is declared but not
    exported is *not* header-only -- it is declared without a definition the
    library ships.
    """
    if isinstance(decl, Function):
        if decl.is_pure_virtual:
            return "pure virtual"
        if decl.is_deleted:
            return "deleted"
        if decl.is_inline:
            return "header-only"
        return None
    from .buildsource.export_obligation_linkage import (
        has_internal_linkage,
        is_static_member_symbol,
    )

    mangled = decl.mangled or ""
    # Internal linkage is read from the mangling's own marker. A bare
    # `is_static` says it only when the name is not a class member: a static
    # data member owes an out-of-line definition and is not header-only.
    if has_internal_linkage(mangled) or (
        decl.is_static and not is_static_member_symbol(mangled)
    ):
        return "header-only"
    return None


def addition_evidence(decl: Function | Variable, noun: str) -> dict[str, Any]:
    """``description``/``surface_facts`` for a ``FUNC_ADDED``/``VAR_ADDED``.

    The mirror of what the removal paths already stamp. A public header can
    add a declaration that no binary ever exports -- an inline or
    ``constexpr`` function, a pure virtual, a ``constexpr`` constant -- and
    when the NEW side's export table *confirms* the symbol is absent the
    addition is a source-level API addition, not a new export. The kind stays
    the same (it is still a compatible addition consumers can use), but the
    finding must not read as an added exported symbol: the description says
    so, and ``surface_facts`` records ``binary_exported: false``.

    The description states only what the declaration shows. It used to say
    "public header-only" for every confirmed-absent export, which was false
    twice over for a ``private:`` member declared without an inline body
    (oneCCL): the member is not consumer-callable, and nothing about it is
    header-only -- its definition simply is not exported. Access other than
    ``public`` is named, and "header-only" is said only for a shape that is
    (:func:`_unexported_shape`).
    """
    access = getattr(decl, "access", AccessLevel.PUBLIC)
    where = (
        "public"
        if access == AccessLevel.PUBLIC
        else f"{getattr(access, 'value', access)} member"
    )
    if not is_export_confirmed_absent(decl):
        description = f"New {where} {noun}: {decl.name}"
    else:
        shape = _unexported_shape(decl)
        if shape is None:
            description = (
                f"New {where} {noun} declared without an exported symbol "
                f"(not inline, so its definition is not shipped): {decl.name}"
            )
        else:
            description = (
                f"New {where} {shape} {noun} (no exported symbol): {decl.name}"
            )
    return {"description": description, "surface_facts": surface_fact_summary(decl)}


def _var_added(mangled: str, v_new: Variable) -> list[Change]:
    """The mirror of :func:`_var_removed`, and reconciled the same way."""
    return [
        make_change(
            ChangeKind.VAR_ADDED,
            symbol=mangled,
            name=v_new.name,
            entity_id=v_new.entity_id,
            **addition_evidence(v_new, "variable"),
        )
    ]


def _observed_exports(snap: AbiSnapshot, types: frozenset[str]) -> ObservedExportTable:
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
                _should_filter_transitive_runtime_symbols(snap)
            ),
        ),
    )


def _public_variables(snap: AbiSnapshot) -> dict[str, Variable]:
    """Return public/ELF-only variables from *snap*.

    Excludes RTTI/vtable symbols of function-local types (lambda closures and
    other in-function types): they are not nameable public ABI and only churn
    across builds (RD2-4).
    """
    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(snap)
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


def _check_variable(
    mangled: str,
    v_old: Variable,
    v_new: Variable,
    *,
    old_index: VariableTypeIndex,
    new_index: VariableTypeIndex,
    cv_facts_reliable: bool = True,
) -> list[Change]:
    """Compare a matched pair of public variables.

    The type/const half reads each side's ``SemanticIR`` through
    *old_index*/*new_index* (``compare/variables.py``, ADR-063 6B variable
    cohort). *cv_facts_reliable* is ``False`` for a pre-v9 CastXML document,
    which silently dropped ``volatile`` from a variable's type spelling, so a
    cv-only difference there would misreport a breaking ``VAR_TYPE_CHANGED``
    (Codex review, PR #582).
    """
    old_entity, new_entity = old_index.entity_for(v_old), new_index.entity_for(v_new)
    entity_id = v_old.entity_id or v_new.entity_id
    changes = alignment_changes(
        mangled, v_old.name, old_entity, new_entity, entity_id=entity_id
    )
    # The export axis is independent of every type/qualifier comparison and
    # must survive their early returns -- an unknown type on a stripped side
    # says nothing about whether the symbol is still exported
    # (compare/export_transition.py).
    changes += _export_transition.check_variable(mangled, v_old, v_new)
    return changes + variable_type_changes(
        mangled,
        v_old.name,
        (v_old.type, v_new.type),
        old_entity,
        new_entity,
        entity_id=entity_id,
        cv_facts_reliable=cv_facts_reliable,
    )


def variable_type_index_for(
    snap: AbiSnapshot, variables: Iterable[Variable]
) -> VariableTypeIndex:
    """*snap*'s :class:`~abicheck.compare.variables.VariableTypeIndex` over
    the *variables* the caller pairs, projecting what the IR cannot name with
    the normalizer's own formula under *snap*'s producer."""
    producer = snap.ast_producer or ""
    return variable_type_index(
        snap.canonical_ir,
        variables,
        lambda var: variable_canonical_entity(var, producer),
        projection_key=producer,
    )
