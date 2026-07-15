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

"""Backfill header-parsed record layout from DWARF (clang L2 backend support).

The clang L2 header backend (:mod:`abicheck.dumper_clang`) is a syntactic AST
dump — it never computes ``size_bits``/``alignment_bits``/field
``offset_bits``/``vtable``. When the binary being dumped also carries DWARF
debug info (the common debug-headers case), :mod:`abicheck.dumper` calls
:func:`backfill_dwarf_layout` to fill in that missing layout from the
same compiled binary's DWARF, so layout-dependent detectors are not blind
under the clang backend. Split out of ``dumper.py`` to keep that module under
the AI-readiness file-size cap.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .model import RecordType

if TYPE_CHECKING:
    from pathlib import Path

    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .dwarf_unified import DwarfSession
    from .elf_metadata import ElfMetadata


def dwarf_layout_types_or_empty(
    so_path: Path,
    elf_meta: ElfMetadata,
    dwarf_meta: DwarfMetadata,
    dwarf_adv: AdvancedDwarfMetadata,
    is_clang_backend: bool,
    *,
    symbols_only: bool,
    debug_presence_only: bool,
    version: str,
    language_profile: str | None,
    session: DwarfSession | None,
) -> list[RecordType]:
    """DWARF-derived ``RecordType``\\ s of *so_path*, for ``backfill_dwarf_layout``.

    ``[]`` (no-op for the caller) unless the L2 header backend in play is
    layout-blind (clang) and DWARF is actually present — folding that check
    in here lets ``dumper._dump_elf`` call this unconditionally instead of
    guarding it with a separate branch just to decide whether to bother.
    *is_clang_backend* must reflect the backend the header parser actually
    used, not a static guess from the requested ``--ast-frontend``: on the
    "auto" frontend, an unrecoverable castxml failure makes the parser fall
    back to clang internally, which a pre-resolved guess would miss.
    """
    if symbols_only or debug_presence_only or not dwarf_meta.has_dwarf or not is_clang_backend:
        return []
    from .dwarf_snapshot import build_snapshot_from_dwarf
    return list(build_snapshot_from_dwarf(
        so_path, elf_meta, dwarf_meta, dwarf_adv,
        version=version, language_profile=language_profile, session=session,
    ).types)


def backfill_dwarf_layout(
    header_types: list[RecordType],
    dwarf_types: list[RecordType],
) -> list[RecordType]:
    """Fill in missing struct/class layout on header-parsed types from DWARF.

    Matched by name — both come from the same source, so a name match is
    unambiguous (no cross-version renaming ambiguity: this backfills a
    single snapshot from its own binary, never merges across old/new).
    castxml already computes real layout itself, so any type that already
    carries a ``size_bits`` is left untouched — purely additive for a
    layout-blind header backend, a no-op otherwise. An opaque (forward-
    declared-only) header type is also left alone: its blank layout is a
    meaningful "this header only forward-declares it" signal, not a gap to
    paper over with an unrelated full definition DWARF happens to carry.

    The clang header backend emits a bare record name with no namespace
    scope, while the DWARF builder qualifies it (``scope::name``) — an exact
    match therefore misses a genuinely namespaced type. Falling back to a
    match on the name's last ``::``-segment recovers that case, but *only*
    when it is unambiguous: if two DWARF types share that bare suffix (e.g.
    two different namespaces both declaring ``Foo``), matching either one
    could silently attach the wrong type's layout, so both are left
    unmatched rather than guessed (Codex review).
    """
    if not dwarf_types:
        return header_types
    dwarf_by_name = {t.name: t for t in dwarf_types}
    dwarf_by_suffix: dict[str, list[RecordType]] = {}
    for t in dwarf_types:
        dwarf_by_suffix.setdefault(t.name.rsplit("::", 1)[-1], []).append(t)

    def _dwarf_match(name: str) -> RecordType | None:
        exact = dwarf_by_name.get(name)
        if exact is not None:
            return exact
        candidates = dwarf_by_suffix.get(name, [])
        return candidates[0] if len(candidates) == 1 else None

    out: list[RecordType] = []
    for t in header_types:
        if t.size_bits is not None or t.is_opaque:
            out.append(t)
            continue
        dwarf_t = _dwarf_match(t.name)
        if dwarf_t is None:
            out.append(t)
            continue
        dwarf_fields_by_name = {f.name: f for f in dwarf_t.fields}
        new_fields = []
        for f in t.fields:
            df = dwarf_fields_by_name.get(f.name)
            if f.offset_bits is not None or df is None:
                new_fields.append(f)
                continue
            new_fields.append(replace(
                f,
                offset_bits=df.offset_bits,
                is_bitfield=df.is_bitfield,
                bitfield_bits=df.bitfield_bits,
            ))
        out.append(replace(
            t,
            size_bits=dwarf_t.size_bits,
            alignment_bits=dwarf_t.alignment_bits,
            fields=new_fields,
            vtable=t.vtable or dwarf_t.vtable,
            vptr_offset_bits=(
                t.vptr_offset_bits if t.vptr_offset_bits is not None else dwarf_t.vptr_offset_bits
            ),
            base_offsets=t.base_offsets or dwarf_t.base_offsets,
            data_size_bits=t.data_size_bits if t.data_size_bits is not None else dwarf_t.data_size_bits,
            is_standard_layout=(
                t.is_standard_layout if t.is_standard_layout is not None else dwarf_t.is_standard_layout
            ),
            is_trivially_copyable=(
                t.is_trivially_copyable if t.is_trivially_copyable is not None else dwarf_t.is_trivially_copyable
            ),
        ))
    return out
