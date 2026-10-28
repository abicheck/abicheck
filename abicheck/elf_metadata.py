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

"""ELF dynamic-section and symbol-table metadata.

Uses ``pyelftools`` (pure Python, actively maintained) for robust ELF/DWARF
parsing instead of text-scraping ``readelf`` output.

See docs/adr/001-technology-stack.md for rationale.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import re
import stat
from pathlib import Path
from typing import IO, Any

from elftools.common.exceptions import ELFError
from elftools.elf.dynamic import DynamicSection, DynamicTag
from elftools.elf.elffile import ELFFile
from elftools.elf.gnuversions import (
    GNUVerDefSection,
    GNUVerNeedSection,
    GNUVerSymSection,
)
from elftools.elf.sections import SymbolTableSection

from .extract.elf_abi_flags import _decode_abi_flags as _decode_abi_flags
from .extract.elf_code_hash import CodeHasher
from .extract.elf_loader_view import read_loader_view
from .extract.elf_notes import (
    NT_GNU_ABI_TAG_NAMES,
    decode_abi_tag_desc,
    decode_gnu_property_desc,
    iter_gnu_property_descs,
)
from .extract.elf_static_tls import has_static_tls_relocation

# Fact dataclasses live in the model package (ADR-061 Phase 5): this module
# parses into them and re-exports them so the historical
# ``from abicheck.elf_metadata import ElfImport`` spelling keeps resolving.
from .extract.elf_string_table import buffered_string_table, string_table_of
from .extract.elf_symbol_origin import guess_symbol_origin as _guess_symbol_origin
from .extract.elf_symbol_versions import (
    build_verdef_index,
    correlate_symbol_versions,
    symbols_of,
)
from .extract.parse_failures import (
    note_export_read_failure,
    recording_export_read_failures,
)
from .model.elf_facts import (
    ElfImport as ElfImport,
    ElfMetadata as ElfMetadata,
    ElfSymbol as ElfSymbol,
    SymbolBinding as SymbolBinding,
    SymbolType as SymbolType,
)
from .model.execution_cache import MemoryCache, request_key
from .model.fact import sync_present_facts

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

# Moved to `extract/elf_symbol_tables.py` so the version-correlation walk
# (`extract/elf_symbol_versions.py`) can share the *same* objects rather
# than keeping a second copy that could drift from these. Re-exported by
# value, so every existing reference in this module still resolves.
from .extract.elf_symbol_tables import (  # noqa: E402
    _BINDING_MAP as _BINDING_MAP,
    _HIDDEN_VISIBILITIES as _HIDDEN_VISIBILITIES,
    _TYPE_MAP as _TYPE_MAP,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


#: Process-level memo of parsed tables, keyed on the opened file's content
#: digest and path -- content, not a stat: an mtime has only kernel-tick
#: resolution, so a same-size rewrite within one tick would read as unchanged. One ``compare`` parses each binary
#: several times (primary dump, the symbols-only L0 re-resolve, release
#: members sharing a provider). Entries are stored *pickled*: every hit hands
#: its caller an independent object (no caller can mutate another's
#: metadata), and a pickle round trip is several times cheaper than the
#: ``copy.deepcopy`` it replaced on both halves -- measured on a 56k-symbol
#: library, 0.15s to store and 0.10s to load vs 0.6s for each deep copy.
#: That matters on the scalar path, where the two sides are two different
#: files and the memo never hits: a deep copy per store was pure overhead
#: (7.3s of an MKL ``libmkl_rt`` compare). A pickled entry is also one flat
#: ``bytes`` object rather than a second live object graph of the symbol
#: table, which is what inflated the live-at-exit residue. At most
#: ``_PARSE_MEMO_MAX`` entries -- enough for one compare's two sides plus their
#: L0 re-resolve, without growing with a release fan-out's member count.
_PARSE_MEMO_MAX = 4
_PARSE_MEMO: MemoryCache[bytes] = MemoryCache(
    "abicheck.elf_metadata.parse", max_entries=_PARSE_MEMO_MAX
)


def parse_elf_metadata(so_path: Path) -> ElfMetadata:
    """Extract ELF dynamic + symbol metadata from *so_path* using pyelftools.

    Returns an empty ``ElfMetadata`` on any parse error (logged as WARNING).
    Uses fstat() after open() to prevent TOCTOU symlink/FIFO attacks.
    """
    try:
        with open(so_path, "rb") as f:
            # Verify it's a regular file *after* open to avoid TOCTOU race.
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                log.warning("parse_elf_metadata: not a regular file: %s", so_path)
                note_export_read_failure(f"not a regular file: {so_path}")
                return ElfMetadata()
            key = request_key(
                content=hashlib.file_digest(f, "blake2b").hexdigest(),
                path=str(so_path),
            )
            f.seek(0)
            stored = _PARSE_MEMO.get_or_compute(
                key, lambda: _parse_and_pickle(f, so_path)
            )
            # nosec B301: bytes this function pickled itself, never external input
            loaded: tuple[ElfMetadata, list[str]] = pickle.loads(stored)  # nosec B301
            meta, failures = loaded
            # Replayed on every call, memo hit or not, so a recording caller
            # sees the failures of a parse an earlier caller paid for.
            for reason in failures:
                note_export_read_failure(reason)
            return meta
    except (ELFError, OSError, ValueError) as exc:
        log.warning("parse_elf_metadata: failed to open/parse %s: %s", so_path, exc)
        note_export_read_failure(f"ELF parse failed: {exc}")
        return ElfMetadata()


def _parse_and_pickle(f: IO[bytes], so_path: Path) -> bytes:
    """Parse *f* and pickle the metadata with the export-read failures seen."""
    with recording_export_read_failures() as failures:
        meta = _parse(f, so_path)
    return pickle.dumps((meta, failures), protocol=pickle.HIGHEST_PROTOCOL)


# ---------------------------------------------------------------------------
# Internal parsing
# ---------------------------------------------------------------------------


def _parse(f: IO[bytes], so_path: Path) -> ElfMetadata:
    meta = ElfMetadata()
    elf = ELFFile(f)

    meta.pointer_size = _read_pointer_size(elf, so_path)
    _read_identity(elf, meta, so_path)
    _parse_gnu_property(elf, meta, so_path)
    _parse_abi_tag(elf, meta, so_path)
    has_relro_segment, is_et_dyn = _parse_segments(elf, meta, so_path)

    ver_sym_section, dynsym_section, ver_index_map = _parse_all_sections(
        elf, meta, so_path
    )

    # Correlate per-symbol version entries using sections captured above.
    correlate_symbol_versions(
        ver_sym_section, dynsym_section, meta, ver_index_map, so_path
    )

    _postprocess_metadata(meta, ver_index_map, ver_sym_section, dynsym_section, so_path)
    # DF_STATIC_TLS is a linker summary that AArch64 GNU ld omits; the
    # relocation is the requirement itself (extract/elf_static_tls.py).
    meta.has_static_tls = meta.has_static_tls or has_static_tls_relocation(elf)

    # Finalize derived hardening properties now that segments, dynamic flags,
    # and the symbol table have all been parsed.
    _finalize_hardening(meta, has_relro_segment=has_relro_segment, is_et_dyn=is_et_dyn)

    return meta


def _read_pointer_size(elf: ELFFile, so_path: Path) -> int:
    """Return pointer width in bytes from ELF class (32-bit → 4, 64-bit → 8).

    Used to decode vtable/typeinfo object sizes in diff_elf_layout.py.
    Falls back to 8 (64-bit default) on parse error.
    """
    try:
        return 4 if elf.elfclass == 32 else 8
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "parse_elf_metadata: failed to read ELF class from %s: %s", so_path, exc
        )
        return 8


def _read_identity(elf: ELFFile, meta: ElfMetadata, so_path: Path) -> None:
    """Capture the ELF header fields that define the target binary contract."""
    try:
        meta.machine = elf["e_machine"]
        meta.elf_class = 32 if elf.elfclass == 32 else 64
        meta.osabi = elf["e_ident"]["EI_OSABI"]
        meta.e_flags = int(elf["e_flags"])
        meta.abi_flags = _decode_abi_flags(meta.machine, meta.e_flags)
        meta.ei_data = "LSB" if elf.little_endian else "MSB"
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "parse_elf_metadata: failed to read ELF identity from %s: %s", so_path, exc
        )


def _parse_abi_tag(elf: ELFFile, meta: ElfMetadata, so_path: Path) -> None:
    """Read the minimum-kernel floor from the .note.ABI-tag section."""
    try:
        section = elf.get_section_by_name(".note.ABI-tag")
        if section is None or not hasattr(section, "iter_notes"):
            return
        for note in section.iter_notes():
            if note.get("n_type") not in NT_GNU_ABI_TAG_NAMES:
                continue
            if note.get("n_name") not in (None, "GNU"):
                continue
            desc = note.get("n_descdata") or note.get("n_desc")
            if isinstance(desc, str):
                desc = desc.encode("latin-1", "replace")
            if isinstance(desc, (bytes, bytearray)):
                floor = decode_abi_tag_desc(bytes(desc), elf.little_endian)
                if floor:
                    meta.min_kernel_version = floor
                    return
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "parse_elf_metadata: failed to read .note.ABI-tag from %s: %s", so_path, exc
        )


def _parse_gnu_property(elf: ELFFile, meta: ElfMetadata, so_path: Path) -> None:
    """Read control-flow-protection features from GNU-property notes.

    Prefers the ``.note.gnu.property`` section, but falls back to the loadable
    ``PT_GNU_PROPERTY`` program segment when section headers have been stripped
    (common for production artifacts) — otherwise CET/BTI/PAC drift would be
    invisible on exactly those binaries.
    """
    try:
        # GNU property entries are padded to the ELF class word size: 8 bytes
        # for ELFCLASS64, 4 bytes for ELFCLASS32.
        align = 4 if elf.elfclass == 32 else 8
        features: set[str] = set()
        for desc in iter_gnu_property_descs(elf):
            features |= decode_gnu_property_desc(desc, elf.little_endian, align)
        meta.gnu_properties = frozenset(features)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "parse_elf_metadata: failed to read GNU-property notes from %s: %s",
            so_path,
            exc,
        )


_PF_X = 0x1
_PF_W = 0x2


def _parse_segments(
    elf: ELFFile, meta: ElfMetadata, so_path: Path
) -> tuple[bool, bool]:
    """Iterate program headers; populate interpreter and segment-level hardening fields.

    Returns ``(has_relro_segment, is_et_dyn)``.

    * ``has_relro_segment`` — a PT_GNU_RELRO segment was found.
    * ``is_et_dyn``         — the ELF type is ET_DYN (needed to gate PIE detection).
    """
    has_relro_segment = False
    try:
        for seg in elf.iter_segments():
            _process_segment(seg, meta, so_path)
            if seg.header.p_type == "PT_GNU_RELRO":
                has_relro_segment = True
            elif seg.header.p_type == "PT_TLS":
                # A PT_TLS segment means the object defines TLS storage of its
                # own — including hidden/local `__thread` variables that never
                # appear in .dynsym. Used (alongside dynamic STT_TLS symbols) to
                # gate the DF_STATIC_TLS finding so hidden static TLS is not
                # suppressed (G23-A1).
                meta.has_tls_symbols = True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "parse_elf_metadata: failed to read program headers from %s: %s",
            so_path,
            exc,
        )

    # PIE = position-independent *executable* (ET_DYN with the DF_1_PIE flag).
    # _parse_dynamic sets meta.is_pie tentatively from DF_1_PIE; gate it on
    # ET_DYN here so a non-PIE object never claims PIE.
    is_et_dyn = False
    try:
        is_et_dyn = elf.header.e_type == "ET_DYN"
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "parse_elf_metadata: failed to read e_type from %s: %s", so_path, exc
        )

    return has_relro_segment, is_et_dyn


def _process_segment(seg: object, meta: ElfMetadata, so_path: Path) -> None:
    """Apply a single ELF program-header segment to *meta*."""
    p_type = seg.header.p_type
    if p_type == "PT_INTERP":
        # PT_INTERP contains a null-terminated path string.
        meta.interpreter = seg.get_interp_name()
    elif p_type == "PT_GNU_STACK":
        # PF_X = executable. Executable stack is a security risk.
        if seg.header.p_flags & _PF_X:
            meta.has_executable_stack = True
    elif p_type == "PT_LOAD":
        # A loadable segment that is both writable and executable
        # violates W^X (memory should never be both at once).
        if (seg.header.p_flags & _PF_W) and (seg.header.p_flags & _PF_X):
            meta.has_writable_executable_segment = True


# Type alias for version index maps shared across section helpers.
_VerIndexMap = dict[int, tuple[str, str, bool]]


#: Sections whose loss changes the export table, SONAME or version facts.
_EXPORT_BEARING_SECTIONS = frozenset(
    {
        ".dynsym",
        ".symtab",
        ".dynamic",
        ".dynstr",
        ".gnu.version",
        ".gnu.version_d",
        ".gnu.version_r",
    }
)


def _parse_all_sections(
    elf: ELFFile, meta: ElfMetadata, so_path: Path
) -> tuple[GNUVerSymSection | None, SymbolTableSection | None, _VerIndexMap]:
    """Iterate all ELF sections; parse each into *meta* and return captured refs.

    Returns ``(ver_sym_section, dynsym_section, ver_index_map)`` for the
    subsequent version-correlation pass.

    * ``ver_sym_section`` — the ``.gnu.version`` section (one entry per .dynsym symbol).
    * ``dynsym_section``  — the ``.dynsym`` section (exported/imported symbols).
    * ``ver_index_map``   — merged verdef + verneed index maps; verdef takes priority.

    Relocatable objects (``.o``) carry no ``.dynsym`` — their symbol surface lives
    in ``.symtab``.  When ``.dynsym`` is absent and ``.symtab`` is present the
    fallback parsing fills ``meta.symbols``/``meta.imports`` via the same
    ``_parse_dynsym`` path used for shared libraries.
    """
    # Build separate version-index maps from .gnu.version_d and .gnu.version_r.
    # Verdef and verneed indices are normally non-overlapping, but separating
    # them prevents mis-attribution if a malformed ELF reuses an index.
    verdef_index_map: _VerIndexMap = {}  # idx → ("", ver, True)
    verneed_index_map: _VerIndexMap = {}  # idx → (lib, ver, False)

    ver_sym_section: GNUVerSymSection | None = None
    dynsym_section: SymbolTableSection | None = None
    symtab_section: SymbolTableSection | None = None

    for section in elf.iter_sections():
        try:
            dynsym_section, symtab_section, ver_sym_section = _process_section(
                section,
                meta,
                verdef_index_map,
                verneed_index_map,
                dynsym_section,
                symtab_section,
                ver_sym_section,
            )
        except Exception as exc:  # noqa: BLE001
            # Partial-success: log malformed section, keep results from other sections.
            log.warning(
                "parse_elf_metadata: skipping malformed section %r in %s: %s",
                section.name,
                so_path,
                exc,
            )
            if section.name in _EXPORT_BEARING_SECTIONS:
                note_export_read_failure(f"malformed {section.name} section: {exc}")

    # Relocatable objects (ET_REL `.o`, e.g. a probe-built object) carry no
    # `.dynsym` -- their symbol surface lives in `.symtab`. Fall back to it so the
    # defined GLOBAL/WEAK symbols of a `.o` are captured (the same classification
    # `_parse_dynsym` applies to a `.dynsym`). A shared object never takes this
    # path: its `.symtab` is link-time, not the export table.
    if (
        dynsym_section is None
        and symtab_section is not None
        and not _is_shared_object(elf)
    ):
        try:
            _parse_dynsym(symtab_section, meta)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "parse_elf_metadata: skipping malformed .symtab in %s: %s", so_path, exc
            )
            note_export_read_failure(f"malformed .symtab section: {exc}")

    # A shared object (ET_DYN, static-pie included) always carries a .dynsym,
    # but the section header table is a link-time index the loader never reads:
    # a zeroed, absent or stripped one (sstrip) still has its dynamic table.
    # Read that table through PT_DYNAMIC instead; the .symtab fallback is for
    # relocatable objects and would read link-time globals as exports.
    if dynsym_section is None and _is_shared_object(elf):
        ver_sym_section, dynsym_section = _parse_loader_view(
            elf, meta, so_path, ver_sym_section, verdef_index_map, verneed_index_map
        )

    # Merge: verdef entries take priority over verneed on index collision.
    ver_index_map: _VerIndexMap = {**verneed_index_map, **verdef_index_map}

    return ver_sym_section, dynsym_section, ver_index_map


def _parse_loader_view(
    elf: ELFFile,
    meta: ElfMetadata,
    so_path: Path,
    ver_sym_section: Any,
    verdef_index_map: _VerIndexMap,
    verneed_index_map: _VerIndexMap,
) -> tuple[Any, Any]:
    """Parse a shared object's dynamic symbol table from its ``PT_DYNAMIC`` view.

    Reached only when the section table gave no ``.dynsym``. Each piece is taken
    from its section when the section table has one, and from its dynamic tag
    otherwise, so an intact file is parsed exactly as before. Returns
    ``(ver_sym, dynsym)`` for the version correlation that follows; ``dynsym``
    is ``None`` (and a refusal is recorded) when the dynamic table is unreadable.
    """
    try:
        view = read_loader_view(elf)
    except Exception as exc:  # noqa: BLE001 - an unreadable table is the refusal
        log.warning(
            "parse_elf_metadata: dynamic table of %s unreadable: %s", so_path, exc
        )
        note_export_read_failure(
            "no readable .dynsym section in a shared object (dynamic symbol table "
            f"not read: PT_DYNAMIC unreadable: {exc})"
        )
        return ver_sym_section, None
    sections = list(elf.iter_sections())
    try:
        if not any(isinstance(s, DynamicSection) for s in sections):
            _parse_dynamic(view.dynamic, meta)
        if view.verdef is not None and not any(
            isinstance(s, GNUVerDefSection) for s in sections
        ):
            _parse_version_def(view.verdef, meta)
            build_verdef_index(view.verdef, verdef_index_map)
        if view.verneed is not None and not any(
            isinstance(s, GNUVerNeedSection) for s in sections
        ):
            _parse_version_need(view.verneed, meta)
            _build_verneed_index(view.verneed, verneed_index_map)
        meta.hash_styles = meta.hash_styles | view.hash_styles
        _parse_dynsym_entries(view.symbols, meta, CodeHasher(elf, by_segment=True))
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "parse_elf_metadata: dynamic table of %s not read: %s", so_path, exc
        )
        note_export_read_failure(f"malformed dynamic table (PT_DYNAMIC): {exc}")
        return ver_sym_section, None
    return (
        ver_sym_section if ver_sym_section is not None else view.versym
    ), view.symbols


def _is_shared_object(elf: ELFFile) -> bool:
    """True for an ELF whose header says ``ET_DYN`` (shared object or PIE)."""
    try:
        return str(elf.header["e_type"]) == "ET_DYN"
    except Exception:  # noqa: BLE001 — header already parsed; treat as not-DSO
        return False


def _process_section(
    section: object,
    meta: ElfMetadata,
    verdef_index_map: _VerIndexMap,
    verneed_index_map: _VerIndexMap,
    dynsym_section: SymbolTableSection | None,
    symtab_section: SymbolTableSection | None,
    ver_sym_section: GNUVerSymSection | None,
) -> tuple[
    SymbolTableSection | None, SymbolTableSection | None, GNUVerSymSection | None
]:
    """Dispatch one ELF section to the appropriate parser; return updated refs."""
    if isinstance(section, DynamicSection):
        _parse_dynamic(section, meta)
    elif isinstance(section, GNUVerDefSection):
        _parse_version_def(section, meta)
        build_verdef_index(section, verdef_index_map)
    elif isinstance(section, GNUVerNeedSection):
        _parse_version_need(section, meta)
        _build_verneed_index(section, verneed_index_map)
    elif isinstance(section, GNUVerSymSection):
        ver_sym_section = section
    elif isinstance(section, SymbolTableSection) and section.name == ".dynsym":
        _parse_dynsym(section, meta)
        dynsym_section = section
    elif isinstance(section, SymbolTableSection) and section.name == ".symtab":
        # Captured but not parsed yet — only used as a fallback for
        # relocatable objects (.o) that have no .dynsym (see below).
        symtab_section = section
    else:
        name = getattr(section, "name", "")
        if name == ".hash":
            meta.hash_styles = meta.hash_styles | {"sysv"}
        elif name == ".gnu.hash":
            meta.hash_styles = meta.hash_styles | {"gnu"}
        elif name == ".relr.dyn":
            # Section-level fallback for DT_RELR (the dynamic tag is the
            # primary signal; a stripped/static-pie image may only keep the
            # section).
            meta.has_dt_relr = True
    return dynsym_section, symtab_section, ver_sym_section


# Fortified libc wrappers are named ``__<func>_chk`` (e.g. __memcpy_chk,
# __printf_chk); their presence as undefined references means the object was
# built with -D_FORTIFY_SOURCE.
_CANARY_SYMS = frozenset({"__stack_chk_fail", "__stack_chk_guard"})
_FORTIFY_RE = re.compile(r"^__\w+_chk$")


def _finalize_hardening(
    meta: ElfMetadata, *, has_relro_segment: bool, is_et_dyn: bool
) -> None:
    """Derive RELRO level, PIE, stack-canary, and FORTIFY from parsed data."""
    # RELRO: a PT_GNU_RELRO segment gives partial RELRO; combined with eager
    # binding (BIND_NOW) the whole GOT is read-only → full RELRO.
    if has_relro_segment:
        meta.relro = "full" if meta.bind_now else "partial"
    else:
        meta.relro = "none"

    # PIE only applies to ET_DYN images flagged DF_1_PIE.
    meta.is_pie = meta.is_pie and is_et_dyn

    # Stack canary / FORTIFY are observed via referenced libc symbols. Check
    # both the imported (undefined) set and any defined symbols.
    names = [s.name for s in meta.imports] + [s.name for s in meta.symbols]
    for name in names:
        if name in _CANARY_SYMS:
            meta.has_stack_canary = True
        elif _FORTIFY_RE.match(name):
            meta.has_fortify_source = True


def _postprocess_metadata(
    meta: ElfMetadata,
    ver_index_map: dict[int, tuple[str, str, bool]],
    ver_sym_section: GNUVerSymSection | None,
    dynsym_section: SymbolTableSection | None,
    so_path: Path,
) -> None:
    """Post-loop processing: filter version-def aux symbols and fix origin hints."""
    # Filter out version-definition auxiliary symbols.
    # GNU ld emits these as OBJECT/size=0 in .dynsym; lld/gold may use NOTYPE.
    # Both are ELF artefacts of --version-script, not real exported functions.
    _ver_def_names: set[str] = set(meta.versions_defined)
    if _ver_def_names:
        meta.symbols = [
            sym
            for sym in meta.symbols
            if not (
                sym.name in _ver_def_names
                and sym.size == 0
                and sym.sym_type in (SymbolType.OBJECT, SymbolType.NOTYPE)
            )
        ]

    # Post-parse fixup: re-run origin detection now that meta.needed is fully
    # populated.  .dynsym is often parsed before .dynamic, so the initial
    # _guess_symbol_origin call in _parse_dynsym always sees an empty needed list.
    # The fixup also corrects symbols that were mis-attributed to the wrong
    # default library (e.g. libstdc++.so.6 vs libc++.so.1).
    _GENERIC_FALLBACKS = frozenset(
        {  # pylint: disable=invalid-name
            "libstdc++.so.6",
            "libgcc_s.so.1",
            "libc.so.6",
        }
    )
    for sym in meta.symbols:
        if sym.origin_lib is None or sym.origin_lib in _GENERIC_FALLBACKS:
            new_origin = _guess_symbol_origin(sym.name, meta.needed)
            if new_origin is not None:
                sym.origin_lib = new_origin


# Dynamic-flag bit constants (elf.h).
_DT_RELR = 36  # DT_RELR (packed relative relocations)
_DF_ORIGIN = 0x1  # DT_FLAGS
_DF_SYMBOLIC = 0x2  # DT_FLAGS
_DF_TEXTREL = 0x4  # DT_FLAGS
_DF_BIND_NOW = 0x8  # DT_FLAGS
_DF_STATIC_TLS = 0x10  # DT_FLAGS
_DF_1_NOW = 0x1  # DT_FLAGS_1
_DF_1_NODELETE = 0x8  # DT_FLAGS_1
_DF_1_NOOPEN = 0x10  # DT_FLAGS_1
_DF_1_ORIGIN = 0x80  # DT_FLAGS_1
_DF_1_PIE = 0x08000000  # DT_FLAGS_1

# Dynamic tags that mark load/unload-time code. The *SZ tags are checked too:
# lld emits DT_INIT_ARRAY even when empty, so a zero size must not count.
_INIT_TAGS = frozenset({"DT_INIT", "DT_PREINIT_ARRAY"})
_FINI_TAGS = frozenset({"DT_FINI"})

# All init/fini-related dynamic tags, deferred to _apply_init_fini_tags.
_INIT_FINI_TAGS = frozenset(
    _INIT_TAGS
    | _FINI_TAGS
    | {"DT_INIT_ARRAY", "DT_FINI_ARRAY", "DT_INIT_ARRAYSZ", "DT_FINI_ARRAYSZ"}
)


def _apply_simple_dynamic_tag(
    d_tag: object, tag: DynamicTag, meta: ElfMetadata
) -> bool:
    """Handle a self-contained dynamic tag; return True if the tag was consumed."""
    if d_tag == "DT_SONAME":
        meta.soname = tag.soname
    elif d_tag == "DT_NEEDED":
        meta.needed.append(tag.needed)
    elif d_tag == "DT_RPATH":
        meta.rpath = tag.rpath
    elif d_tag == "DT_RUNPATH":
        meta.runpath = tag.runpath
    elif d_tag == "DT_BIND_NOW":
        meta.bind_now = True
    elif d_tag == "DT_TEXTREL":
        # Legacy standalone tag; DF_TEXTREL (DT_FLAGS) is the modern spelling.
        meta.has_textrel = True
    elif d_tag in ("DT_RELR", _DT_RELR):
        # Packed relative relocations. pyelftools spells known tags as
        # strings; an older release may pass the raw numeric through.
        meta.has_dt_relr = True
    else:
        return False
    return True


def _apply_dt_flags(d_val: int, meta: ElfMetadata, dyn_flags: set[str]) -> None:
    """Apply DT_FLAGS bits (DF_*) to the metadata and the collected flag set."""
    if d_val & _DF_BIND_NOW:
        meta.bind_now = True
    if d_val & _DF_STATIC_TLS:
        meta.has_static_tls = True
    if d_val & _DF_ORIGIN:
        dyn_flags.add("ORIGIN")
    if d_val & _DF_SYMBOLIC:
        meta.is_symbolic = True
    if d_val & _DF_TEXTREL:
        meta.has_textrel = True


def _apply_dt_flags_1(d_val: int, meta: ElfMetadata, dyn_flags: set[str]) -> None:
    """Apply DT_FLAGS_1 bits (DF_1_*) to the metadata and the collected flag set."""
    if d_val & _DF_1_NOW:
        meta.bind_now = True
    if d_val & _DF_1_PIE:
        # Tentative; gated on ET_DYN by the caller.
        meta.is_pie = True
    if d_val & _DF_1_NODELETE:
        dyn_flags.add("NODELETE")
    if d_val & _DF_1_NOOPEN:
        dyn_flags.add("NOOPEN")
    if d_val & _DF_1_ORIGIN:
        dyn_flags.add("ORIGIN")


def _array_tag_is_populated(present: bool, size: int | None) -> bool:
    """True when a DT_*_ARRAY tag exists and its *SZ tag is absent or non-zero."""
    return present and (size is None or size > 0)


def _apply_init_fini_tags(tags: list[DynamicTag], meta: ElfMetadata) -> None:
    """Set meta.has_init/has_fini from the collected DT_INIT*/DT_FINI* tags."""
    init_array_sz: int | None = None  # None = no DT_INIT_ARRAYSZ tag seen
    fini_array_sz: int | None = None
    has_init_array = has_fini_array = False
    for tag in tags:
        d_tag = tag.entry.d_tag
        if d_tag in _INIT_TAGS:
            meta.has_init = True
        elif d_tag in _FINI_TAGS:
            meta.has_fini = True
        elif d_tag == "DT_INIT_ARRAY":
            has_init_array = True
        elif d_tag == "DT_FINI_ARRAY":
            has_fini_array = True
        elif d_tag == "DT_INIT_ARRAYSZ":
            init_array_sz = int(tag.entry.d_val)
        elif d_tag == "DT_FINI_ARRAYSZ":
            fini_array_sz = int(tag.entry.d_val)
    if _array_tag_is_populated(has_init_array, init_array_sz):
        meta.has_init = True
    if _array_tag_is_populated(has_fini_array, fini_array_sz):
        meta.has_fini = True


def _parse_dynamic(section: DynamicSection, meta: ElfMetadata) -> None:
    # A dynamic section exists — the loader-contract fields below are now
    # *captured* (tri-state None → concrete), so detectors may compare them.
    dyn_flags: set[str] = set(meta.dynamic_flags or ())
    meta.has_init = bool(meta.has_init)
    meta.has_fini = bool(meta.has_fini)
    init_fini_tags: list[DynamicTag] = []
    for tag in section.iter_tags():
        d_tag = tag.entry.d_tag
        if _apply_simple_dynamic_tag(d_tag, tag, meta):
            continue
        if d_tag in _INIT_FINI_TAGS:
            init_fini_tags.append(tag)
        elif d_tag == "DT_FLAGS":
            _apply_dt_flags(tag.entry.d_val, meta, dyn_flags)
        elif d_tag == "DT_FLAGS_1":
            _apply_dt_flags_1(tag.entry.d_val, meta, dyn_flags)
    _apply_init_fini_tags(init_fini_tags, meta)
    meta.dynamic_flags = frozenset(dyn_flags)
    sync_present_facts(meta, "dynamic_flags", "has_init", "has_fini")


def _parse_version_def(section: GNUVerDefSection, meta: ElfMetadata) -> None:
    # ELF version definition section (.gnu.version_d).
    # The first entry has VER_FLG_BASE (flags==1) and names the SONAME -- skip it.
    # Only real named version nodes (e.g. LIBFOO_1.0) should appear in versions_defined.
    VER_FLG_BASE = 0x1  # pylint: disable=invalid-name
    for verdef, verdaux_iter in section.iter_versions():
        is_base = bool(verdef.entry.vd_flags & VER_FLG_BASE)
        for verdaux in verdaux_iter:
            name = verdaux.name
            if name and not is_base and name not in meta.versions_defined:
                meta.versions_defined.append(name)


def _parse_version_need(section: GNUVerNeedSection, meta: ElfMetadata) -> None:
    for verneed, vernaux_iter in section.iter_versions():
        lib = verneed.name
        # pyelftools' .name fields are str | None; skip entries with no
        # library name rather than indexing the dict with None.
        if not lib:
            continue
        if lib not in meta.versions_required:
            meta.versions_required[lib] = []
        for vernaux in vernaux_iter:
            ver = vernaux.name
            if ver and ver not in meta.versions_required[lib]:
                meta.versions_required[lib].append(ver)


# Alignment derived from a symbol address is only meaningful up to the page
# size — PT_LOAD segments map page-aligned, so intra-page alignment survives
# relocation while anything larger is a load-address accident.
_PAGE_ALIGNMENT_CAP = 4096


def _value_alignment(st_value: int) -> int:
    """Power-of-two alignment of *st_value*, capped at the page size.

    0 means unknown (st_value of 0 carries no alignment information).
    """
    if st_value <= 0:
        return 0
    return min(st_value & -st_value, _PAGE_ALIGNMENT_CAP)


def _parse_dynsym(section: SymbolTableSection, meta: ElfMetadata) -> None:
    # Every `sym.name` below is a seek plus a chunked read of the linked
    # string table; buffering that table for the duration of the walk turns
    # ~N stream operations into one read (`extract/elf_string_table.py`,
    # which also explains why this is scoped rather than cached). Measured
    # on a real oneDAL build below in that module's docstring.
    with buffered_string_table(string_table_of(section)):
        _parse_dynsym_entries(
            section, meta, CodeHasher(getattr(section, "elffile", None))
        )


def _parse_dynsym_entries(
    section: SymbolTableSection, meta: ElfMetadata, hasher: CodeHasher
) -> None:
    """The symbol walk itself, with the string table already buffered.

    Split from :func:`_parse_dynsym` only so the buffering wraps the whole
    walk in one ``with`` block; the classification logic is unchanged.
    """
    for sym in symbols_of(section):
        binding_str = sym.entry.st_info.bind
        type_str = sym.entry.st_info.type
        vis_str = sym.entry.st_other.visibility
        binding = _BINDING_MAP.get(binding_str, SymbolBinding.OTHER)
        name = sym.name

        # Any STT_TLS entry (defined or undefined import) means the library
        # participates in TLS — used to gate the DF_STATIC_TLS finding so a
        # TLS-free library is never flagged (G23-A1).
        if type_str == "STT_TLS":
            meta.has_tls_symbols = True

        # Collect undefined symbols as imports.
        if sym.entry.st_shndx == "SHN_UNDEF":
            if not name or binding == SymbolBinding.LOCAL:
                continue
            sym_type = _TYPE_MAP.get(type_str, SymbolType.NOTYPE)
            meta.imports.append(
                ElfImport(
                    name=name,
                    binding=binding,
                    sym_type=sym_type,
                    version="",  # correlated later via .gnu.version
                    is_default=True,
                )
            )
            continue

        # Skip absolute (version-def markers).
        if sym.entry.st_shndx == "SHN_ABS":
            continue

        # Skip local symbols — not part of public ABI surface
        if binding == SymbolBinding.LOCAL:
            continue

        # Skip hidden/internal — not exported from DSO
        if vis_str in _HIDDEN_VISIBILITIES:
            continue

        sym_type = _TYPE_MAP.get(type_str, SymbolType.OTHER)
        if not name:
            # A defined, global, visible entry with no name is a corrupt
            # .dynsym entry (its st_name points outside .dynstr), not an export.
            note_export_read_failure(
                "malformed .dynsym entry: exported symbol has no name"
            )

        # NOTE: pyelftools does NOT embed version suffixes (@@/@ notation) in
        # sym.name — that's a readelf text-output artifact. Symbol version info
        # comes from the .gnu.version section correlated with .gnu.version_d/r,
        # which is parsed separately in _parse_version_def/_parse_version_need.
        # We leave version="" here; callers correlate via versions_defined/required.

        meta.symbols.append(
            ElfSymbol(
                name=name,
                binding=binding,
                sym_type=sym_type,
                size=sym.entry.st_size,
                version="",
                is_default=True,
                visibility=vis_str.replace("STV_", "").lower(),
                origin_lib=_guess_symbol_origin(name, meta.needed),
                value_alignment=_value_alignment(int(sym.entry.st_value)),
                code_hash=(
                    hasher.hash(
                        int(sym.entry.st_value), sym.entry.st_size, sym.entry.st_shndx
                    )
                    if type_str == "STT_FUNC"
                    else ""
                ),
            )
        )


def _build_verneed_index(
    section: GNUVerNeedSection,
    ver_index_map: dict[int, tuple[str, str, bool]],
) -> None:
    """Build version-index → (library, version_name, is_defined=False) from .gnu.version_r."""
    for verneed, vernaux_iter in section.iter_versions():
        lib = verneed.name
        if not lib:
            continue
        for vernaux in vernaux_iter:
            idx = vernaux.entry.vna_other
            name = vernaux.name
            if name:
                ver_index_map[idx] = (lib, name, False)
