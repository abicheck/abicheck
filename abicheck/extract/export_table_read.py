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

"""Finish a binary-format snapshot from what its export read actually saw
(evidence-entity-model Phase 4, gap A1).

The castxml, clang and DWARF producers -- and a multi-TU manifest dump and
the hybrid merge -- all look declarations up in the one export table the
format builder (``dumper._dump_elf``/``_dump_pe``/``_dump_macho``) read. So
whether that table was *read* is a fact about the dump, decided once, here,
from the read itself rather than from "a binary was supplied"; see
:class:`~abicheck.model.export_index.ExportTableState`.
"""

from __future__ import annotations

from ..model import AbiSnapshot, Function, Variable, Visibility
from ..model.export_index import ExportTableState, snapshot_export_table_state
from ..storage import closure_identity
from .surface_fact_producers import reconcile_export_absence, unread_export_fact

__all__ = ["finish_binary_snapshot", "reconcile_snapshot_export_absence"]


def reconcile_snapshot_export_absence(snapshot: AbiSnapshot) -> int:
    """Withdraw the export absences *snapshot*'s own export table cannot
    support; returns how many were withdrawn.

    The table is judged by
    :func:`~abicheck.model.export_index.snapshot_export_table_state`, the
    rule the ``exports`` coverage record (``compare.edge_query``) reads too:
    read when it holds an entry or its header fields show the binary was
    parsed (a parsed library exporting nothing is a real, empty table),
    failed when the block is a default or parse-failed one --
    ``parse_pe_metadata``/``parse_macho_metadata`` return empty metadata on
    error. The stored snapshot carries the same fields, so a live operand
    and its stored copy get the same answer.

    A snapshot with no platform block at all (header-only, synthetic) is
    left alone: its producers already recorded ``NOT_COLLECTED`` for (c),
    and there is no table here to judge a stored answer against.

    A pre-v46 snapshot carries no stored fact; its ``Visibility.HIDDEN`` is
    re-derived as ``PARTIAL(False)`` by ``model.surface_facts``. That reading
    is kept when the table was read -- ``HIDDEN`` was assigned from a lookup
    against exactly that table -- and replaced by an explicit ``FAILED``
    when it was not, the same bug in its pre-split spelling.
    """
    state = snapshot_export_table_state(snapshot)
    if state is ExportTableState.NO_BINARY:
        return 0
    withdrawn = reconcile_export_absence(snapshot, state)
    replacement = unread_export_fact(state, producer=None)
    if replacement is None:
        return withdrawn
    decls: list[Function | Variable] = [*snapshot.functions, *snapshot.variables]
    for decl in decls:
        if (
            getattr(decl, "binary_exported_fact", None) is None
            and decl.visibility is Visibility.HIDDEN
        ):
            decl.binary_exported_fact = replacement
            withdrawn += 1
    return withdrawn


def finish_binary_snapshot(snapshot: AbiSnapshot) -> AbiSnapshot:
    """The ELF/PE/Mach-O builders' shared tail: withdraw unsupported export
    absences (:func:`reconcile_snapshot_export_absence`), then renumber
    anonymous closure identities."""
    reconcile_snapshot_export_absence(snapshot)
    return closure_identity.renumber_anonymous_closure_identities(snapshot)
