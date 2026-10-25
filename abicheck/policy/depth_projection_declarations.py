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

"""The binary-level view of a header-derived function/variable declaration.

:mod:`abicheck.policy.depth_projection` projects a header-built snapshot to
``--depth binary``. A binary-only dump of the same library, when it has no
debug-info subprogram for a symbol, synthesizes that declaration from the
export table alone (``extract.export_symbol_identity.itanium_export_function``):
the symbol name as both ``name`` and ``mangled``, ``return_type="?"``,
``Visibility.ELF_ONLY``, and nothing else. Every other attribute — static,
const/volatile/ref qualifiers, virtuality and vtable slot, access, deprecation,
calling convention, ``inline``/``noexcept``/``explicit``, defaults — is header
evidence the binary view never saw.

The projection used to blank only ``return_type``/``params`` and keep the
rest, so a header-only fact still reached the detectors at ``--depth binary``
(``func_static_changed``, ``func_cv_changed``, ``func_ref_qual_changed``,
``func_deprecated_added``, ``method_access_changed``,
``calling_convention_changed``, …) — findings a binary-only dump of the same
library never produces (bug class
``evidence.depth_projection_diverges_from_shallow_dump``). Building the
projected declaration from an allowlist of binary-level facts, instead of
subtracting a denylist of header facts, keeps a header attribute added to
the model later from leaking the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..compare.debug_layout_view import debug_layout_view
from ..compare.public_surface import should_filter_transitive_runtime_symbols
from ..elf_symbol_filter import (
    FUNCTION_SYMBOL_TYPES,
    VARIABLE_SYMBOL_TYPES,
    exported_symbol_names,
)
from ..model import ElfVisibility, Function, Variable, Visibility
from ..model.fact import Fact
from ..model.owner_recovery import is_mangled_identity
from ..model.surface_facts import headers_discarded_surface_facts

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from ..model.elf_facts import ElfSymbol

__all__ = [
    "binary_level_function",
    "binary_level_variable",
    "export_only_declarations",
    "pair_layout_scope",
]

_DISCARDED_REASON = "header evidence discarded by evidence-depth projection"


def _discarded_surface_facts() -> dict[str, Any]:
    # `dict[str, Any]` like `export_table_surface_facts`, so it can be spread
    # into a constructor whose other keyword arguments have other types.
    return dict(headers_discarded_surface_facts(reason=_DISCARDED_REASON))


def _export_mangled_name(sym: str) -> str | None:
    """The mangled spelling a binary-only dump offers the identity builder:
    the symbol itself when it carries a C++ mangling, else ``None`` (the
    extern-"C" branch) -- the rule ``extract.export_symbol_identity``
    applies to an export-table entry."""
    return sym if is_mangled_identity(sym) else None


def binary_level_function(func: Function) -> Function:
    """*func* reduced to the facts a binary-only dump records for an export.

    Kept: the symbol (as both ``name`` and ``mangled``, the spelling a
    binary-only dump reports), the platform symbol-table facts (ELF
    visibility/binding) and the export fact. ``is_extern_c`` is re-derived
    from the symbol, as for an export-table entry. The header ``entity_id``
    is dropped, not carried: it is built from the declaration's scope and
    source spelling, which is header evidence, and an identity is built only
    by a producer that sees the declaration (``model/identity.py``), so the
    projection does not build another. The two header-derived surface facts
    read unknown with the projection named as the reason, never a confirmed
    negative.
    """
    return Function(
        name=func.mangled,
        mangled=func.mangled,
        return_type="?",
        visibility=Visibility.ELF_ONLY,
        is_extern_c=_export_mangled_name(func.mangled) is None,
        elf_visibility=func.elf_visibility,
        elf_binding=func.elf_binding,
        binary_exported_fact=func.binary_exported_fact,
        **_discarded_surface_facts(),
    )


def binary_level_variable(var: Variable) -> Variable:
    """The :func:`binary_level_function` counterpart for a variable."""
    return Variable(
        name=var.mangled,
        mangled=var.mangled,
        type="?",
        visibility=Visibility.ELF_ONLY,
        elf_visibility=var.elf_visibility,
        elf_binding=var.elf_binding,
        binary_exported_fact=var.binary_exported_fact,
        **_discarded_surface_facts(),
    )


def export_only_declarations(
    snap: AbiSnapshot, declared: set[str]
) -> tuple[list[Function], list[Variable]]:
    """An export-only record for every ABI-relevant ELF export not in *declared*.

    The projection switches a header-derived snapshot into ``elf_only_mode``,
    which tells every detector that the function/variable maps *are* the
    export table -- the ``undeclared_exports`` detector stands down on that
    promise, since a headerless dump already lists every export there. The
    header model declares only what the headers name, so without this the
    exports no header declares (a C++ class's vtable and typeinfo, implicit
    special members, an undeclared helper) dropped out of the projected
    comparison altogether: losing one was BREAKING in a binary-only dump and
    in the unprojected header comparison, and nothing at ``--depth binary``.

    The export set is the one the ``undeclared_exports`` detector reads
    (ABI-relevant dynamic exports, C++ runtime symbols filtered unless *snap*
    is the runtime). A ``notype`` export is listed as a function only, as a
    headerless dump lists it. Empty when the export table was never captured
    (no ``machine``): an uncaptured table is not an empty one.
    """
    elf = snap.elf
    if elf is None or not elf.machine:
        return [], []
    filter_transitive = should_filter_transitive_runtime_symbols(snap)
    func_names = exported_symbol_names(
        elf,
        FUNCTION_SYMBOL_TYPES,
        abi_relevant_only=True,
        filter_transitive_runtime_symbols=filter_transitive,
    )
    var_names = (
        exported_symbol_names(
            elf,
            VARIABLE_SYMBOL_TYPES,
            abi_relevant_only=True,
            filter_transitive_runtime_symbols=filter_transitive,
        )
        - func_names
    )
    functions: list[Function] = []
    for sym in sorted(func_names - declared):
        elf_sym = elf.unambiguous_symbol(sym)
        functions.append(
            Function(
                name=sym,
                mangled=sym,
                return_type="?",
                visibility=Visibility.ELF_ONLY,
                is_extern_c=_export_mangled_name(sym) is None,
                elf_visibility=_elf_visibility(elf_sym),
                elf_binding=elf_sym.binding if elf_sym is not None else None,
                binary_exported_fact=Fact.present(True),
                **_discarded_surface_facts(),
            )
        )
    variables: list[Variable] = []
    for sym in sorted(var_names - declared):
        elf_sym = elf.unambiguous_symbol(sym)
        variables.append(
            Variable(
                name=sym,
                mangled=sym,
                type="?",
                visibility=Visibility.ELF_ONLY,
                elf_visibility=_elf_visibility(elf_sym),
                elf_binding=elf_sym.binding if elf_sym is not None else None,
                binary_exported_fact=Fact.present(True),
                **_discarded_surface_facts(),
            )
        )
    return functions, variables


def _elf_visibility(elf_sym: ElfSymbol | None) -> ElfVisibility | None:
    """The symbol's ELF visibility in the model's vocabulary (the same
    spelling map ``extract.elf_symbol_classify`` applies)."""
    if elf_sym is None:
        return None
    value = getattr(elf_sym.visibility, "value", elf_sym.visibility)
    try:
        return ElfVisibility(str(value))
    except ValueError:
        return None


def pair_layout_scope(
    struct_scope: frozenset[str] | None,
    enum_scope: frozenset[str] | None,
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> tuple[frozenset[str], frozenset[str]]:
    """The debug record and enum layouts a projected pair compares: those
    present on both sides, within *struct_scope*/*enum_scope* (the names the
    headers name) narrowed to the types the header surface made public.

    The projection rebuilds a layout-only record for every name kept here
    (``compare.debug_layout_view.debug_layout_declarations``), so the set
    decides which types the type detectors judge at ``--depth binary``:

    * **Both sides only.** Debug info lists the types the compiled code
      uses, not the types a library declares, so a layout one side lacks is
      not evidence the type was removed or added (a header type the new
      code stopped using vanishes from its debug info). Rebuilding it read
      as ``type_removed`` -- a removal no header run reports.
    * **Public only.** The headers name every type they mention, toolchain
      internals included (``__mbstate_t``, ``std::shared_ptr<...>``); the
      header and binary-only comparisons scope those out by reachability
      from the public functions, which the projected export-only functions
      no longer carry. The public-type closure is computed from the
      *original* header snapshots. Skipped when either side's surface has
      no typed roots to walk from: an unknown never hides a type.

    Narrowing only, never widening: a scope of ``None`` (no header names a
    type of that kind) keeps every layout present on both sides.
    """
    old_view, new_view = debug_layout_view(old), debug_layout_view(new)
    records = frozenset(old_view.records.keys() & new_view.records.keys())
    enums = frozenset(old_view.enums.keys() & new_view.enums.keys())
    if struct_scope is not None:
        records &= struct_scope
    if enum_scope is not None:
        enums &= enum_scope
    if struct_scope is None and enum_scope is None:
        return records, enums
    from .public_surface_closure import resolve_public_surface

    surfaces = (resolve_public_surface(old), resolve_public_surface(new))
    if not all(s.resolvable and s.has_typed_roots for s in surfaces):
        return records, enums
    public = surfaces[0].public_types | surfaces[1].public_types
    return (
        records if struct_scope is None else records & public,
        enums if enum_scope is None else enums & public,
    )
