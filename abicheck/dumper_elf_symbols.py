# Copyright 2026 Nikolay Petrov
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

"""ELF visibility/symbol-classification helpers for :mod:`dumper`.

Relocated out of ``dumper.py`` (ADR-050 Phase 1 follow-up) to free line
budget for the new ``compute_extraction_contract`` wiring — ``dumper.py``
sits exactly at the AI-readiness file-size hard cap, so any net-positive
addition there needs an equal-or-greater reduction elsewhere first. This is
a pure relocation, not a rewrite; ``dumper.py`` re-exports both names so
existing bare-name calls and test patches (``patch.object(dumper,
"_elf_classify_symbols", ...)`` etc.) keep working unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .errors import SnapshotError
from .model import AbiSnapshot, ElfVisibility, Fact, is_cxx_runtime_library

if TYPE_CHECKING:
    from .elf_metadata import ElfMetadata

_ELF_VIS_MAP: dict[str, ElfVisibility] = {
    "default": ElfVisibility.DEFAULT,
    "protected": ElfVisibility.PROTECTED,
    "hidden": ElfVisibility.HIDDEN,
    "internal": ElfVisibility.INTERNAL,
}


def _populate_elf_visibility(snap: AbiSnapshot) -> None:
    """Populate elf_visibility/elf_binding on Function/Variable from ELF metadata symbols.

    ``elf_binding`` is ``elf_sym.binding`` unchanged — unlike
    ``elf_visibility`` it needs no ``_ELF_VIS_MAP``-style translation, since
    ``ElfSymbol.binding`` is already the same ``elf_metadata.SymbolBinding``
    enum ``model.py`` re-exports and stores it as.
    """
    if snap.elf is None:
        return
    sym_map = snap.elf.symbol_map
    for func in snap.functions:
        elf_sym = sym_map.get(func.mangled)
        if elf_sym is not None:
            func.elf_visibility = _ELF_VIS_MAP.get(elf_sym.visibility)
            func.elf_binding = elf_sym.binding
    for var in snap.variables:
        elf_sym = sym_map.get(var.mangled)
        if elf_sym is not None:
            var.elf_visibility = _ELF_VIS_MAP.get(elf_sym.visibility)
            var.elf_binding = elf_sym.binding
            # Plain attribute assignment never re-runs __post_init__, so the
            # elf_binding_fact sibling must be kept in sync explicitly here
            # (ADR-063 Phase 5) — see AGENTS.md's post-construction mutation
            # trap entries for the identical tu_merge.py/provenance.py fix.
            var.elf_binding_fact = Fact.present(elf_sym.binding)


def _elf_classify_symbols(
    elf_meta: ElfMetadata,
    exported_dynamic: set[str],
    *,
    library_name: str | None = None,
) -> tuple[set[str], set[str], set[str], set[str]]:
    """Split ELF metadata symbols into typed subsets for the no-header path.

    Returns ``(exported_dynamic, funcs, objects, tls)`` where *exported_dynamic*
    may be the original fallback set when *elf_meta* has no symbols.
    """
    from .elf_metadata import SymbolType

    exported_dynamic_funcs: set[str] = exported_dynamic  # fallback
    exported_dynamic_objects: set[str] = set()
    exported_dynamic_tls: set[str] = set()
    if elf_meta.symbols:
        runtime_name = elf_meta.soname or library_name
        filter_transitive_runtime_symbols = not is_cxx_runtime_library(runtime_name)
        # Apply the shared ABI-relevance filter here too: this no-header path
        # rebuilds the exported sets directly from ``elf_meta.symbols`` rather
        # than the already-filtered ``_pyelftools_exported_symbols`` result, so
        # lifecycle stubs (``_init``/``_fini``) and transitive runtime symbols
        # would otherwise re-enter the symbol-only ABI surface as ELF_ONLY
        # functions. Keeping it consistent with the DWARF-backed path.
        exported_dynamic_funcs = {
            sym.name
            for sym in elf_meta.symbols
            if sym.sym_type in (SymbolType.FUNC, SymbolType.IFUNC, SymbolType.NOTYPE)
            and is_abi_relevant_elf_symbol(
                sym.name,
                filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
            )
        }
        exported_dynamic_objects = {
            sym.name
            for sym in elf_meta.symbols
            if sym.sym_type == SymbolType.OBJECT
            and is_abi_relevant_elf_symbol(
                sym.name,
                filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
            )
        }
        exported_dynamic_tls = {
            sym.name
            for sym in elf_meta.symbols
            if sym.sym_type == SymbolType.TLS
            and is_abi_relevant_elf_symbol(
                sym.name,
                filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
            )
        }
        # Full set for CastxmlParser: determines PUBLIC vs ELF_ONLY visibility
        exported_dynamic = (
            exported_dynamic_funcs | exported_dynamic_objects | exported_dynamic_tls
        )
    return (
        exported_dynamic,
        exported_dynamic_funcs,
        exported_dynamic_objects,
        exported_dynamic_tls,
    )


_HIDDEN_VIS = frozenset({"STV_HIDDEN", "STV_INTERNAL"})


def _is_abi_relevant_symbol(name: str) -> bool:
    """Return False for symbols that are NOT part of the library's public ABI.

    Filters out (in ELF-only mode):
    1. GCC/compiler internal symbols (``ix86_*``, ``_ZGV*``, ``__svml_*`` …)
       that leak into ``.dynsym`` through a statically-linked runtime.
    2. Transitive C++ stdlib symbols (``_ZNSt*``, ``_ZTI*`` …) that appear
       in ``.dynsym`` via weak linkage from libstdc++ / libc++.
    3. Private C symbols that use ``__`` as a namespace separator
       (e.g. ``H5C__flush``, ``MPI__send``).  These follow an internal
       naming convention and are *not* part of the public API, even though
       they may have global ELF visibility.
    """
    return is_abi_relevant_elf_symbol(name)


def _pyelftools_exported_symbols(so_path: Path) -> tuple[set[str], set[str]]:
    """Return (exported_dynamic, exported_static) sets of mangled symbol names.

    Uses pyelftools (pure Python) instead of shelling out to readelf.
    - exported_dynamic: symbols from .dynsym, truly exported via ELF
    - exported_static: symbols from .symtab (all symbols including static)
    """
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile
    from elftools.elf.sections import SymbolTableSection

    def _extract_symbols(elf: Any, section_name: str) -> set[str]:
        syms: set[str] = set()
        section = elf.get_section_by_name(section_name)
        if section is None or not isinstance(section, SymbolTableSection):
            return syms
        for sym in section.iter_symbols():
            shndx = sym.entry.st_shndx
            if shndx in ("SHN_UNDEF", "SHN_ABS"):
                continue
            bind = sym.entry.st_info.bind
            vis = sym.entry.st_other.visibility
            if bind in ("STB_GLOBAL", "STB_WEAK") and vis not in _HIDDEN_VIS:
                name = sym.name
                if name and _is_abi_relevant_symbol(name):
                    syms.add(name)
        return syms

    try:
        with open(so_path, "rb") as f:
            elf: Any = ELFFile(f)  # type: ignore[no-untyped-call]
            exported_dynamic = _extract_symbols(elf, ".dynsym")
            try:
                exported_static = _extract_symbols(elf, ".symtab")
            except (ELFError, OSError):
                exported_static = set(exported_dynamic)
            return exported_dynamic, exported_static
    except (ELFError, OSError) as exc:
        raise SnapshotError(f"Failed to parse ELF file {so_path}: {exc}") from exc
