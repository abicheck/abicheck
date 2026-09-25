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

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from .model import RecordType, TypeField, replace_with_fact_sync
from .model.debug_type_match import DebugRecordFacts, match_header_records
from .model.graph_join import JoinState

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
    debug_format: str | None,
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

    *debug_format* must also be checked directly, not inferred from
    ``dwarf_meta.has_dwarf`` alone (Codex review): when the caller forces
    ``debug_format="btf"``/``"ctf"``, ``_resolve_debug_metadata`` builds
    ``dwarf_meta`` via ``BtfMetadata.to_dwarf_metadata()``/
    ``CtfMetadata.to_dwarf_metadata()``, which sets ``has_dwarf`` to the
    BTF/CTF presence flag (for checker compatibility) rather than leaving it
    ``False`` — and no real ``DwarfSession`` is opened for that path either
    (*session* is ``None``). Passing ``session=None`` through to
    ``build_snapshot_from_dwarf`` would make it open *so_path* itself and
    walk whatever real ``.debug_info`` the binary happens to also carry,
    silently backfilling from the DWARF the user explicitly asked to bypass
    by forcing BTF/CTF — on a binary with both sections present, and the
    DWARF stale or otherwise not meant to be trusted, that's a real
    correctness gap, not just missed coverage.
    """
    if (
        symbols_only
        or debug_presence_only
        or debug_format in ("btf", "ctf")
        or not dwarf_meta.has_dwarf
        or not is_clang_backend
    ):
        return []
    from .dwarf_snapshot import build_snapshot_from_dwarf

    return list(
        build_snapshot_from_dwarf(
            so_path,
            elf_meta,
            dwarf_meta,
            dwarf_adv,
            version=version,
            language_profile=language_profile,
            session=session,
        ).types
    )


@dataclass(frozen=True)
class DwarfLayoutCoherence:
    """Observability for one :func:`backfill_dwarf_layout` run (P0
    evidence-coherence audit) — *never* changes which records get
    backfilled, only records what happened, so the already-reviewed
    accept/reject decisions in :func:`backfill_dwarf_layout` stay untouched.

    ``status`` uses the same four-state vocabulary as the AST-vs-build-
    context coherence check (``compile_context_conflict``): ``"matched"``
    (every eligible record corroborated), ``"partial"`` (some corroborated,
    some had no DWARF candidate to check against at all — benign), or
    ``"mismatch"`` (at least one record's same-spelled DWARF record
    contradicted its layout — the case worth surfacing). A run with
    no DWARF types at all is not constructed by this module; the caller
    (``dumper.py``) is responsible for stamping ``AbiSnapshot
    .dwarf_layout_coherence = "unavailable"`` directly in that case, since
    :func:`backfill_dwarf_layout` returns *early* (a no-op) rather than
    running any of this bucketing logic then.
    """

    status: str
    matched: tuple[str, ...] = field(default_factory=tuple)
    mismatched: tuple[str, ...] = field(default_factory=tuple)
    unavailable_types: tuple[str, ...] = field(default_factory=tuple)
    ambiguous: tuple[str, ...] = field(default_factory=tuple)


def _coherence_status(
    *, mismatched: list[str], unavailable_types: list[str], ambiguous: list[str]
) -> str:
    if mismatched:
        return "mismatch"
    if unavailable_types or ambiguous:
        return "partial"
    return "matched"


def _merged_fields(header: RecordType, dwarf: RecordType) -> list[TypeField]:
    """*header*'s fields with offset/bitfield data filled in from *dwarf*.

    A field that already carries an ``offset_bits``, or has no DWARF
    counterpart by name, is kept exactly as the header parser produced it.
    """
    dwarf_fields_by_name = {f.name: f for f in dwarf.fields}
    merged: list[TypeField] = []
    for f in header.fields:
        df = dwarf_fields_by_name.get(f.name)
        if f.offset_bits is not None or df is None:
            merged.append(f)
            continue
        merged.append(
            replace(
                f,
                offset_bits=df.offset_bits,
                is_bitfield=df.is_bitfield,
                bitfield_bits=df.bitfield_bits,
            )
        )
    return merged


def _backfilled_record(header: RecordType, dwarf: RecordType) -> RecordType:
    """*header* with every layout attribute it lacks taken from *dwarf*.

    Purely additive: an attribute the header backend already computed always
    wins, so this is a no-op for a layout-aware backend (castxml) and a fill-in
    for a layout-blind one (clang).

    ADR-063 Phase 0: `replace()` re-invokes `RecordType.__post_init__` with
    EVERY field of `header`, not just the ones this function overrides —
    including `header`'s own (pre-backfill) `vtable_fact`/
    `vptr_offset_bits_fact` when `vtable`/`vptr_offset_bits` themselves ARE
    being replaced with dwarf's value below. `__post_init__`'s "explicit
    Fact wins" rule would then silently revert the just-backfilled scalar
    back to `header`'s own (pre-backfill) value (Codex review, confirmed
    against a real repro) — `replace_with_fact_sync` derives and passes the
    matching `Fact.present(...)` sibling alongside each, so the two cannot
    disagree.
    """
    return replace_with_fact_sync(
        header,
        size_bits=dwarf.size_bits,
        alignment_bits=dwarf.alignment_bits,
        fields=_merged_fields(header, dwarf),
        vtable=header.vtable or dwarf.vtable,
        # Whichever side's *value* wins above must also supply its own Fact
        # status -- the same rule the vptr_offset_bits_fact/data_size_bits_
        # fact/is_standard_layout_fact/is_trivially_copyable_fact kwargs
        # below already apply, extended to vtable now that a producer can
        # emit something other than Fact.present(...) for it (ADR-063 Phase
        # 5B / T9 DWARF per-TU completeness slice: dwarf.vtable_fact can be
        # Fact.partial(...)). Without this, replace_with_fact_sync's default
        # derivation would stamp Fact.present(final_value) unconditionally,
        # silently promoting a known-incomplete dwarf.vtable_fact to
        # confirmed-complete the instant it survives this backfill -- Codex
        # review, PR #1213, reproducing the exact fabrication that slice
        # exists to close, for any ELF dump combining the clang header
        # frontend with DWARF layout backfill.
        vtable_fact=(header.vtable_fact if header.vtable else dwarf.vtable_fact),
        vptr_offset_bits=(
            header.vptr_offset_bits
            if header.vptr_offset_bits is not None
            else dwarf.vptr_offset_bits
        ),
        # Whichever side's *value* wins above must also supply its own Fact
        # status -- otherwise replace_with_fact_sync's default derivation
        # would stamp Fact.present(...) even when the surviving value is
        # still header's own PARTIAL heuristic, silently promoting it to a
        # confirmed determination it never became (Codex review, PR #909).
        vptr_offset_bits_fact=(
            header.vptr_offset_bits_fact
            if header.vptr_offset_bits is not None
            else dwarf.vptr_offset_bits_fact
        ),
        base_offsets=header.base_offsets or dwarf.base_offsets,
        # ADR-063 Phase 5: same "surviving value's own Fact status" rule as
        # vptr_offset_bits_fact above, now that these three also carry a
        # Fact[...] sibling -- without an explicit *_fact kwarg here,
        # replace_with_fact_sync would derive Fact.present(final_value)
        # unconditionally, which is wrong whenever the surviving value is
        # header's own not-yet-determined None (header.data_size_bits_fact
        # already correctly reads not_collected() in that case; stamping
        # present(None) over it would fabricate a confirmed determination
        # that was never made). dwarf never populates these three fields
        # (dwarf_snapshot.py's own comment), so dwarf.*_fact is always
        # not_collected() too -- passing it through on that branch is
        # exactly as inert as the plain-value ternary already is.
        data_size_bits=(
            header.data_size_bits
            if header.data_size_bits is not None
            else dwarf.data_size_bits
        ),
        data_size_bits_fact=(
            header.data_size_bits_fact
            if header.data_size_bits is not None
            else dwarf.data_size_bits_fact
        ),
        is_standard_layout=(
            header.is_standard_layout
            if header.is_standard_layout is not None
            else dwarf.is_standard_layout
        ),
        is_standard_layout_fact=(
            header.is_standard_layout_fact
            if header.is_standard_layout is not None
            else dwarf.is_standard_layout_fact
        ),
        is_trivially_copyable=(
            header.is_trivially_copyable
            if header.is_trivially_copyable is not None
            else dwarf.is_trivially_copyable
        ),
        is_trivially_copyable_fact=(
            header.is_trivially_copyable_fact
            if header.is_trivially_copyable is not None
            else dwarf.is_trivially_copyable_fact
        ),
    )


def backfill_dwarf_layout(
    header_types: list[RecordType],
    dwarf_types: list[RecordType],
) -> tuple[list[RecordType], DwarfLayoutCoherence | None]:
    """Fill in missing struct/class layout on header-parsed types from DWARF.

    A header record is paired with a DWARF record under the one matching
    rule the snapshot-level debug-type join also uses
    (:func:`abicheck.model.debug_type_match.match_header_records`): the
    DWARF name must *equal* the header record's qualified spelling
    (``qualified_name or name``), no layout fact both sides carry may
    contradict it (union-ness, size, per-field offset), and the pair must be
    mutually unique -- two header records sharing a spelling, or two
    surviving DWARF records, leave the record unfilled. There is no
    bare-name or last-``::``-segment fallback: a same-leaf record in another
    scope is a different entity, however plausible its field names look.

    Purely additive: a record that already carries ``size_bits`` (castxml,
    which computes layout itself), an opaque forward declaration (its blank
    layout *is* the header's answer), and a class-template pattern (no single
    layout exists) are left untouched.

    Returns ``(backfilled_types, coherence)``. *coherence* is observational
    bookkeeping over the same decisions: ``mismatched`` names an eligible
    record whose same-spelled DWARF record(s) all contradicted its layout,
    ``ambiguous`` one the rule could not resolve to a single pair,
    ``unavailable_types`` one with no same-spelled DWARF record at all. It is
    ``None`` when *dwarf_types* is empty (a no-op): only ``dumper.py`` knows
    whether that means "castxml, not a coherence question" or "clang, but
    no DWARF" -- see :func:`resolve_snapshot_layout_coherence`.
    """
    if not dwarf_types:
        return header_types, None
    matches = match_header_records(
        header_types, [DebugRecordFacts.from_record_type(t) for t in dwarf_types]
    )
    matched: list[str] = []
    mismatched: list[str] = []
    unavailable_types: list[str] = []
    ambiguous: list[str] = []

    out: list[RecordType] = []
    for t, m in zip(header_types, matches):
        if t.size_bits is not None or t.is_opaque or t.is_template_pattern:
            out.append(t)
            continue
        if m.state is JoinState.MATCHED and m.debug_index is not None:
            matched.append(t.name)
            out.append(_backfilled_record(t, dwarf_types[m.debug_index]))
            continue
        out.append(t)
        if m.state is JoinState.AMBIGUOUS:
            ambiguous.append(t.name)
        elif m.layout_conflict:
            mismatched.append(t.name)
        else:
            unavailable_types.append(t.name)
    coherence = DwarfLayoutCoherence(
        status=_coherence_status(
            mismatched=mismatched,
            unavailable_types=unavailable_types,
            ambiguous=ambiguous,
        ),
        matched=tuple(matched),
        mismatched=tuple(mismatched),
        unavailable_types=tuple(unavailable_types),
        ambiguous=tuple(ambiguous),
    )
    return out, coherence


def resolve_snapshot_layout_coherence(
    *, is_clang_backend: bool, coherence: DwarfLayoutCoherence | None
) -> tuple[str | None, tuple[str, ...]]:
    """Turn a :func:`backfill_dwarf_layout` call's result into the two
    ``AbiSnapshot`` fields ``dwarf_layout_coherence``/
    ``dwarf_layout_coherence_mismatches`` (P0 evidence-coherence audit).

    *coherence* is ``None`` exactly when this dump's ``dwarf_types`` was
    empty, which happens for two semantically different reasons only the
    caller (``dumper.py``) can tell apart -- ``dwarf_layout_types_or_empty()``
    itself folds "castxml backend (layout already computed directly, not a
    coherence question)" and "clang backend but no usable DWARF at all (a
    real 'unavailable' state)" into the same empty-list result. Split out of
    ``dumper.py`` to keep that module under the AI-readiness file-size cap.
    """
    if not is_clang_backend:
        return None, ()
    if coherence is None:
        return "unavailable", ()
    return coherence.status, coherence.mismatched
