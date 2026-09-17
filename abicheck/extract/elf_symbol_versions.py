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

"""Correlate ``.gnu.version`` entries onto already-parsed ELF symbols.

The second full walk of a symbol table a parse performs: the first builds
the ``ElfSymbol``/``ElfImport`` lists, this one attaches each entry's GNU
symbol version and default/hidden binding by walking the table again in
ordinal order.

Split out of ``elf_metadata.py`` rather than left beside its caller because
that module carries an ``architecture/debt.yaml`` no-growth baseline, and
the right way to stay under one is to move responsibility out (AGENTS.md:
"the way to shrink an entry is to move responsibility out to a properly-
owned module, never to trim the file to fit"). Reading a binary is
``extract``'s job, so this is where it belongs regardless.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from elftools.elf.sections import SymbolTableSection

    from ..model.elf_facts import ElfMetadata

from ..model.elf_facts import SymbolBinding
from .elf_symbol_tables import _BINDING_MAP, _HIDDEN_VISIBILITIES

__all__ = ["apply_versions_to_symbols"]


def _is_import_sym(sym: Any) -> bool:
    """Check if a dynsym entry is a counted import symbol."""
    if sym.entry.st_shndx != "SHN_UNDEF":
        return False
    return bool(
        sym.name
        and _BINDING_MAP.get(sym.entry.st_info.bind, SymbolBinding.OTHER)
        != SymbolBinding.LOCAL
    )


def _is_export_sym(sym: Any) -> bool:
    """Check if a dynsym entry is a counted export symbol."""
    if sym.entry.st_shndx in ("SHN_UNDEF", "SHN_ABS"):
        return False
    binding = _BINDING_MAP.get(sym.entry.st_info.bind, SymbolBinding.OTHER)
    vis_str = sym.entry.st_other.visibility
    return binding != SymbolBinding.LOCAL and vis_str not in _HIDDEN_VISIBILITIES


def apply_versions_to_symbols(
    dynsym: SymbolTableSection,
    ver_entries: list[tuple[int, bool]],
    ver_index_map: dict[int, tuple[str, str, bool]],
    meta: ElfMetadata,
) -> None:
    export_idx = 0
    import_idx = 0
    for sym_ordinal, sym in enumerate(dynsym.iter_symbols()):
        if sym_ordinal >= len(ver_entries):
            break
        ver_idx, is_hidden = ver_entries[sym_ordinal]
        export_idx, import_idx = _apply_version_to_symbol(
            sym,
            ver_idx,
            is_hidden,
            ver_index_map,
            meta,
            export_idx,
            import_idx,
        )


def _apply_version_to_symbol(
    sym: Any,
    ver_idx: int,
    is_hidden: bool,
    ver_index_map: dict[int, tuple[str, str, bool]],
    meta: ElfMetadata,
    export_idx: int,
    import_idx: int,
) -> tuple[int, int]:
    """Apply version info to a single symbol, returning updated indices."""
    if ver_idx < 2:
        if _is_import_sym(sym):
            import_idx += 1
        elif _is_export_sym(sym):
            export_idx += 1
        return export_idx, import_idx

    entry = ver_index_map.get(ver_idx)
    if entry is None:
        if _is_import_sym(sym):
            import_idx += 1
        elif _is_export_sym(sym):
            export_idx += 1
        return export_idx, import_idx

    _lib_name, ver_name, _is_defined = entry

    if _is_import_sym(sym):
        if import_idx < len(meta.imports):
            meta.imports[import_idx].version = ver_name
            meta.imports[import_idx].is_default = not is_hidden
            # Record the verneed provider soname so consumers can resolve which
            # DSO satisfies this import even when a version label collides.
            meta.imports[import_idx].version_soname = _lib_name
        import_idx += 1
    elif _is_export_sym(sym):
        if export_idx < len(meta.symbols):
            meta.symbols[export_idx].version = ver_name
            meta.symbols[export_idx].is_default = not is_hidden
        export_idx += 1

    return export_idx, import_idx
