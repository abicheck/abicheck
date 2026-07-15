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
    paper over with an unrelated full definition DWARF happens to carry. A
    class-template pattern (``is_template_pattern``) is left alone for the
    same reason: it has no single fixed layout to backfill from — matching
    it by bare name against one particular DWARF instantiation, or worse an
    unrelated same-named type, would silently attach the wrong data (Codex
    review: template patterns and ordinary records share the same clang AST
    kind and bare name, with nothing else to tell them apart).

    The clang header backend emits a bare record name with no namespace
    scope, while the DWARF builder qualifies it (``scope::name``) — an exact
    match therefore misses a genuinely namespaced type. Falling back to a
    match on the name's last ``::``-segment recovers that case, but *only*
    when it is unambiguous. Ambiguity is checked across *both* keys a DWARF
    type can be found under (its full name and its bare suffix) together,
    not the full name first and the suffix only as a fallback: an unrelated
    top-level ``Foo`` matches "Foo" by full name just as validly as a
    namespaced ``api::Foo`` matches it by suffix, so if both exist, an
    exact-first lookup would silently pick the wrong one instead of ever
    reaching the ambiguity check (Codex review). Collecting every DWARF type
    under all of its lookup keys up front and requiring exactly one
    candidate — regardless of which key matched — closes that gap: two
    types sharing a bare name or suffix (e.g. two different namespaces both
    declaring ``Foo``, or a global ``Foo`` alongside a namespaced one) are
    both left unmatched rather than guessed.

    A *unique* bare-name candidate still is not necessarily the *right* one:
    if the header type's own DWARF counterpart is absent for any reason (e.g.
    declared in a broad public header but not actually instantiated by this
    particular binary), an unrelated internal helper that merely happens to
    share the bare name (``impl::Foo`` for a public ``Foo``) would be the
    only entry under that key and get accepted with no other type to
    disambiguate against (Codex review). Field-name overlap is the
    corroborating signal: two independent record definitions coincidentally
    sharing both a bare name *and* at least one member name is implausible,
    while the same source's header/DWARF views of one real type always
    share theirs. No overlap when *both* sides have fields means "unrelated
    type, not just unqualified" — left unmatched rather than trusted on name
    alone.

    An empty DWARF field list, though, is not itself a sign of "unrelated" —
    but only when the *header* side is the one with fields. A record whose
    members are all injected from an anonymous struct/union (``struct Foo {
    union { int i; float f; }; };``) is flattened onto the header side by
    ``dumper_clang.py`` (so ``header.fields`` lists ``i``/``f`` directly) but
    the DWARF builder does not flatten it the same way, leaving
    ``dwarf.fields`` empty even though DWARF *does* carry the record's real
    ``size_bits`` — rejecting that on "no overlap" would make every such
    struct permanently layout-blind under the clang backend (Codex review),
    which is a real, common C pattern, not a hypothetical.

    The reverse — an empty *header* type matched against a DWARF candidate
    that DOES have fields — gets no such exception (Codex review): a header
    such as ``struct Foo {};`` with no DWARF emission of its own could
    otherwise silently match a unique but unrelated internal ``impl::Foo {
    int x; }`` via the bare-name suffix, backfilling the public empty type's
    layout from a type that isn't actually the same declaration. Both sides
    empty (a genuine fieldless tag type) is still trusted; header-empty with
    dwarf-non-empty is not.
    """
    if not dwarf_types:
        return header_types
    dwarf_candidates: dict[str, list[RecordType]] = {}
    for t in dwarf_types:
        for key in {t.name, t.name.rsplit("::", 1)[-1]}:
            dwarf_candidates.setdefault(key, []).append(t)

    def _dwarf_match(name: str) -> RecordType | None:
        candidates = dwarf_candidates.get(name, [])
        return candidates[0] if len(candidates) == 1 else None

    def _fields_corroborate(header: RecordType, dwarf: RecordType) -> bool:
        if not header.fields:
            # An empty header type (tag type) can't corroborate against a
            # DWARF candidate that DOES have fields — that's exactly the
            # unrelated-internal-type risk this check exists to catch, not
            # the anonymous-aggregate asymmetry below. Only trust it when
            # DWARF is empty too (both sides genuinely fieldless).
            return not dwarf.fields
        if not dwarf.fields:
            return True  # anonymous-aggregate asymmetry: header flattens, DWARF doesn't
        return bool({f.name for f in header.fields} & {f.name for f in dwarf.fields})

    out: list[RecordType] = []
    for t in header_types:
        if t.size_bits is not None or t.is_opaque or t.is_template_pattern:
            out.append(t)
            continue
        dwarf_t = _dwarf_match(t.name)
        if dwarf_t is None or not _fields_corroborate(t, dwarf_t):
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
