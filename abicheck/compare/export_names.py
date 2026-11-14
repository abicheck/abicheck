# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""A snapshot's exported symbol names as a comparison reads them.

The obligation set every "is this exported?" reader shares: the canonical
``model.export_index`` projection, gated on the export table having been
read (``compare.edge_query.export_table_covered``, invariant I4) and
without ELF linker-reserved symbols. Owned by ``compare/`` because the I4
coverage predicate it asks lives here (ADR-061).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..elf_symbol_filter import is_linker_reserved_symbol
from ..model.export_index import build_raw_export_index, default_versioned_names
from .edge_query import export_table_covered

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = ["default_export_names", "without_linker_reserved"]


def default_export_names(snapshot: AbiSnapshot) -> set[str] | None:
    """The binary's exported symbol names, or ``None`` if no export table exists.

    ADR-063 T7: thin wrapper over the canonical
    ``model.export_index.default_versioned_names`` projection (over
    ``model.export_index.build_raw_export_index``'s raw read) — only
    **default/unversioned** ELF exports count toward the obligation set, and
    Mach-O names are normalized the same way the dumper normalizes
    ``Function.mangled`` (its single leading underscore stripped). A table
    that was never read (a default or parse-failed platform block) is
    ``None`` too, not an empty set: I4 (``compare.edge_query.
    export_table_covered``) -- read as empty, it flagged every declaration
    ``PUBLIC_NOT_EXPORTED`` at HIGH confidence.
    """
    index = build_raw_export_index(snapshot)
    if index is None or not export_table_covered(snapshot, index.platform):
        return None
    return without_linker_reserved(set(default_versioned_names(index)), index.platform)


def without_linker_reserved(names: set[str], platform: str) -> set[str]:
    """Drop ELF linker-reserved symbols (``__bss_start``/``_edata``/``_end``/...).

    Every export-table-walking reader goes through :func:`default_export_names`
    or ``buildsource.cross_source_checks_base._linked_export_symbols``, so this
    is the one place the class is excluded: a gold- or Bazel-linked library
    exports these whatever its headers say, and ``exported_not_public`` used
    to report each one as an undocumented export (a RISK on every such build).
    The predicate is the shared ``elf_symbol_filter`` one, not a local list.
    """
    if platform != "elf":
        return names
    return {n for n in names if not is_linker_reserved_symbol(n)}
