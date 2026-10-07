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

"""Say what an appearing or disappearing undeclared export *is*.

``compare.undeclared_exports`` emits ``*_added_elf_only``/``*_removed_elf_only``
from the two export tables and the parsed declarations, which is all a
``compare``-layer detector may read. Whether the public headers speak for the
export some other way -- a ``template <...>`` declaration it instantiates, an
``#ifdef`` region the run did not analyse, an excluded header -- needs the
header *text*, and whether it is a C++ ABI artifact or a dependency leak needs
the linked-library list. ``exported_not_public`` already answered all of that
for the same symbol, through ``buildsource.export_account_decision``; this
step words the existence finding from that same decision, so the two findings
about one export cannot contradict each other (oneCCL: six
``create_communicators`` instantiations were "instantiation of a public
template" in one and "not declared in any public header" in the other).

Only the description changes. The kind, and therefore the verdict and the
addition count, is the detector's.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..buildsource.cross_source_checks_base import _exported_symbol_names
from ..buildsource.export_account_decision import (
    ExportAccount,
    account_export,
    accounting_context,
)
from ..buildsource.export_accounting import (
    ACCOUNT_ALLOCATOR_INTERPOSER,
    ACCOUNT_CXX_ARTIFACT,
    ACCOUNT_EXTERNAL_DEP,
    ACCOUNT_INTERNAL_NS,
    ACCOUNT_OWN_TYPE_INSTANTIATION,
    ACCOUNT_PUBLIC,
    ACCOUNT_PUBLIC_TEMPLATE,
    ACCOUNT_TEMPLATE_INST,
)
from ..model import AbiSnapshot
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind

__all__ = ["EXISTENCE_KINDS", "describe_export_existence", "existence_description"]

_ADDED = frozenset({ChangeKind.FUNC_ADDED_ELF_ONLY, ChangeKind.VAR_ADDED_ELF_ONLY})
_REMOVED = frozenset(
    {ChangeKind.FUNC_REMOVED_ELF_ONLY, ChangeKind.VAR_REMOVED_ELF_ONLY}
)
#: The export-table-only existence kinds this step words.
EXISTENCE_KINDS = _ADDED | _REMOVED

#: What a removal of an export no public header promised can break: exactly
#: the consumers that bound it outside the public API. Said on the finding so
#: "not declared in any public header" and a BREAKING verdict read as one
#: claim, not as two that contradict each other.
_REMOVAL_REACH = (
    "; only a consumer that bound it outside the public headers (dlsym(), a "
    "private or hand-written prototype) is affected"
)


def existence_description(change: Change, decision: ExportAccount) -> str | None:
    """The description *decision* gives an existence finding, or ``None`` to
    keep the detector's own wording."""
    sym = change.symbol
    removed = change.kind in _REMOVED
    lead = "Removed exported" if removed else "New exported"
    category = decision.category
    if category == ACCOUNT_PUBLIC_TEMPLATE:
        return (
            f"{lead} instantiation of public template {decision.public_template} "
            f"(no concrete declaration in the parsed headers): {sym}"
        )
    if decision.hint is not None:
        hint = decision.hint
        where = (
            "an excluded header (--exclude-header)"
            if hint.reason == "excluded_header"
            else f"a conditionally compiled region ({hint.guard})"
        )
        return (
            f"{lead} symbol with no *parsed* public declaration: {hint.header} "
            f"declares it in {where}, which this run did not analyse: {sym}"
        )
    if category == ACCOUNT_PUBLIC:
        return f"{lead} symbol of a public declaration: {sym}"
    if category == ACCOUNT_CXX_ARTIFACT:
        return (
            f"{lead} C++ ABI artifact (vtable/typeinfo/VTT/thunk) of a class, "
            f"declared by no header by construction: {sym}"
        )
    if category == ACCOUNT_ALLOCATOR_INTERPOSER:
        return f"{lead} allocator replacement of a malloc-interposer library: {sym}"
    reach = _REMOVAL_REACH if removed else ""
    if category == ACCOUNT_EXTERNAL_DEP:
        return (
            f"{lead} symbol leaked from external dependency {decision.origin_lib}, "
            f"not part of this library's API{reach}: {sym}"
        )
    reason = {
        ACCOUNT_INTERNAL_NS: "it belongs to an internal namespace",
        ACCOUNT_TEMPLATE_INST: "a template instantiation with no public declaration",
        ACCOUNT_OWN_TYPE_INSTANTIATION: (
            "this library's own instantiation of an external template over its own types"
        ),
    }.get(category)
    detail = f" ({reason})" if reason else ""
    return f"{lead} symbol not declared in any public header{detail}{reach}: {sym}"


def describe_export_existence(
    changes: Iterable[Change], old: AbiSnapshot, new: AbiSnapshot
) -> None:
    """Re-word every existence finding in *changes* from the shared decision.

    A removal is accounted against OLD (the side that had the export), an
    addition against NEW. A side whose export table was not captured keeps
    the detector's wording -- the detector would not have fired without one.
    """
    exports: dict[int, frozenset[str] | None] = {}
    for change in changes:
        if change.kind not in EXISTENCE_KINDS or not change.symbol:
            continue
        side = old if change.kind in _REMOVED else new
        if id(side) not in exports:
            names = _exported_symbol_names(side)
            exports[id(side)] = None if names is None else frozenset(names)
        exported = exports[id(side)]
        if exported is None:
            continue
        decision = account_export(change.symbol, accounting_context(side, exported))
        description = existence_description(change, decision)
        if description is not None:
            change.description = description
