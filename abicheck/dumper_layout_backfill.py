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


def _topmost_scope_suffix(name: str) -> str:
    """*name* after its outermost ``::`` scope qualifier, template-args aware.

    A naive ``name.rsplit("::", 1)[-1]`` splits at the *last* ``::``
    anywhere in the string, including one nested inside a template
    argument — ``"api::Base<detail::Tag>".rsplit("::", 1)[-1]`` yields the
    nonsensical ``"Tag>"``, and an unrelated ``"other::Different<detail::
    Tag>"`` collides on that same ``"Tag>"`` (Codex review). This tracks
    ``<``/``>`` nesting depth and only splits on a ``::`` seen at depth 0,
    so ``"api::Base<detail::Tag>"`` correctly yields ``"Base<detail::
    Tag>"`` — stripping only the base's own scope, not descending into its
    template arguments.
    """
    depth = 0
    last = 0
    i = 0
    n = len(name)
    while i < n:
        ch = name[i]
        if ch == "<":
            depth += 1
            i += 1
        elif ch == ">":
            depth -= 1
            i += 1
        elif depth == 0 and name.startswith("::", i):
            last = i + 2
            i += 2
        else:
            i += 1
    return name[last:]


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
    ``"mismatch"`` (at least one record found a uniquely-named DWARF
    candidate but the two disagreed — the case worth surfacing). A run with
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


def _unique_dwarf_match(
    dwarf_candidates: dict[str, list[RecordType]], name: str
) -> RecordType | None:
    """The single DWARF candidate for *name*, or ``None`` when ambiguous/absent."""
    candidates = dwarf_candidates.get(name, [])
    return candidates[0] if len(candidates) == 1 else None


def _fields_corroborate(header: RecordType, dwarf: RecordType) -> bool:
    # Fact[T]-bridged reads (ADR-063 Phase 0): the legacy field and its
    # `_fact` sibling are kept in lockstep by `RecordType.__post_init__`
    # (`rec.bases == rec.bases_fact.value if rec.bases_fact.is_present else
    # []`, identically for `virtual_bases`/`vtable`), so resolving through
    # the `Fact[...]` sibling here is exactly value-preserving. Each
    # `*_fact` field is declared `Fact[list[str]] | None` (only
    # `__init__`-time callers may omit it); `__post_init__` always
    # backfills a real `Fact`, so the leading `is not None` check never
    # actually fails at runtime — it, and the trailing `.value is not
    # None` (mirroring `bridge_legacy_and_fact`'s own resolution), exist
    # to narrow the type for mypy.
    header_bases_fact = header.bases_fact
    header_bases = (
        header_bases_fact.value
        if header_bases_fact is not None
        and header_bases_fact.is_present
        and header_bases_fact.value is not None
        else []
    )
    header_virtual_bases_fact = header.virtual_bases_fact
    header_virtual_bases = (
        header_virtual_bases_fact.value
        if header_virtual_bases_fact is not None
        and header_virtual_bases_fact.is_present
        and header_virtual_bases_fact.value is not None
        else []
    )
    dwarf_bases_fact = dwarf.bases_fact
    dwarf_bases = (
        dwarf_bases_fact.value
        if dwarf_bases_fact is not None
        and dwarf_bases_fact.is_present
        and dwarf_bases_fact.value is not None
        else []
    )
    dwarf_virtual_bases_fact = dwarf.virtual_bases_fact
    dwarf_virtual_bases = (
        dwarf_virtual_bases_fact.value
        if dwarf_virtual_bases_fact is not None
        and dwarf_virtual_bases_fact.is_present
        and dwarf_virtual_bases_fact.value is not None
        else []
    )
    dwarf_vtable_fact = dwarf.vtable_fact
    dwarf_vtable = (
        dwarf_vtable_fact.value
        if dwarf_vtable_fact is not None
        and dwarf_vtable_fact.is_present
        and dwarf_vtable_fact.value is not None
        else []
    )
    if header.fields and dwarf.fields:
        return bool({f.name for f in header.fields} & {f.name for f in dwarf.fields})
    if not header.fields and dwarf.fields:
        # An empty header type (tag type) can't corroborate against a
        # DWARF candidate that DOES have fields — that's exactly the
        # unrelated-internal-type risk this check exists to catch, not
        # the anonymous-aggregate asymmetry handled below.
        return False
    # dwarf.fields is empty here — either both sides are genuinely
    # fieldless, or the header side has real fields that DWARF's
    # anonymous-aggregate asymmetry flattened away. Field names alone
    # can't tell those apart from a coincidentally-fieldless unrelated
    # type in either case, so fall back to base-class-name overlap as a
    # second corroborating signal before trusting the match. Virtual
    # bases are stored separately from ordinary bases on both the clang
    # header parser and the DWARF builder (RecordType.virtual_bases,
    # not .bases) — a virtual-inheritance-only class would otherwise
    # leave both .bases sets empty and fall through unchallenged. Base
    # names also need the same scope-suffix normalization record names
    # get: the clang header parser stores each base's full `qualType`
    # (e.g. "api::Base"), while the DWARF builder's base resolution
    # only ever reads DW_AT_name (always bare, e.g. "Base", never
    # scope-qualified) — comparing the raw strings would reject a
    # namespaced base's own correct match (Codex review).
    header_base_suffixes = {
        _topmost_scope_suffix(b) for b in header_bases + header_virtual_bases
    }
    dwarf_base_suffixes = {
        _topmost_scope_suffix(b) for b in dwarf_bases + dwarf_virtual_bases
    }
    if header_base_suffixes or dwarf_base_suffixes:
        return bool(header_base_suffixes & dwarf_base_suffixes)
    if header.name == dwarf.name:
        # Exact match still needs the header's own fields to be real
        # corroborating evidence, not just the match key itself (Codex
        # review, fresh evidence): the clang header parser never
        # namespace-qualifies RecordType.name at all (see above), so an
        # exact match only shows this DWARF candidate has no scope of
        # its own — not that it is the header's (possibly actually-
        # namespaced) type. A *populated* header record (real,
        # non-anonymous-aggregate fields) with an empty DWARF candidate
        # reached only by that coincidence is exactly the unrelated-
        # type risk the field-overlap check above exists to catch. A
        # trivial fieldless match still needs ``not dwarf.vtable`` too
        # (Codex review, fresh evidence): a genuinely empty header
        # record can exact-match a unique, unrelated, fieldless-but-
        # *polymorphic* DWARF candidate just as easily as the
        # anonymous-aggregate case below can — "nothing to disagree
        # with" isn't true once the DWARF side has a vtable the header
        # side structurally can't (the clang header parser never
        # populates ``RecordType.vtable`` itself).
        return not dwarf_vtable and (
            not header.fields or header.has_anonymous_aggregate_fields
        )
    # Suffix-only match with no field/base overlap left to corroborate.
    # Trusting this on "header merely has some fields" would reopen the
    # exact risk just closed above: an ordinary struct with real fields,
    # whose actual DWARF counterpart is simply absent, matched instead
    # to an unrelated, coincidentally-fieldless internal type via bare
    # suffix (CodeRabbit review). Only trust it when the header's
    # fields are *known* to come from an anonymous-aggregate flatten —
    # a structural signal the clang parser sets itself, not a guess —
    # since DWARF's own builder doesn't flatten the same way and a
    # *namespaced* anonymous-aggregate record (clang emits the bare
    # "Foo", DWARF emits "api::Foo") would otherwise be permanently
    # layout-blind, defeating the point of that exception for exactly
    # the common namespaced case it exists for (Codex review).
    #
    # That flag alone still doesn't vouch for *this particular* unique
    # candidate, though (Codex review, fresh evidence): an unrelated
    # ``impl::Foo`` that is fieldless and baseless but *polymorphic*
    # (virtual methods only, no data) would pass every check so far and
    # hand over its real vtable/size onto the public anonymous-aggregate
    # type. Unlike the header side, DWARF's own builder does populate
    # ``vtable`` for a genuinely polymorphic type, so requiring it to be
    # empty here closes that specific over-trust: the only match this
    # still can't rule out is a *fully* trivial unrelated type (no
    # fields, no bases, no vtable), whose own layout is necessarily
    # near-fixed and small regardless of identity — the same bounded,
    # low-consequence residual risk already accepted for the plain
    # fieldless-tag-type case above.
    #
    # A namespaced class with *only* virtual methods (no fields, no
    # bases, no anonymous-aggregate flatten) is a deliberately accepted
    # gap in this same vein, not an oversight (Codex review): it too is
    # fieldless/baseless with a real, non-empty ``dwarf.vtable``, so by
    # the reasoning just above it would need a structural signal on the
    # *header* side analogous to ``has_anonymous_aggregate_fields`` —
    # but "the class declares a virtual method" only demonstrates that
    # *some* class does, not that this unique suffix-matched candidate
    # is that same one; unlike anonymous-aggregate flattening (a fact
    # about field provenance) or field/base-name overlap (specific
    # identifiers), "has a vtable" is a coarse category shared by
    # every polymorphic class in the binary. Trusting it here would
    # reintroduce exactly the over-trust the ``not dwarf.vtable``
    # guard above exists to prevent, just from the opposite class
    # shape. Closing this safely would need to cross-reference actual
    # member-function names/mangled symbols between the header and
    # DWARF views — data this function doesn't have (only
    # ``RecordType``s, not the snapshot's ``functions`` list) — so it's
    # left unbackfilled (stays ``None``) rather than guessed.
    return header.has_anonymous_aggregate_fields and not dwarf_vtable


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

    A name (and field-name) match alone is not enough, either: a struct/
    class and a union can share a bare name and even a member name while
    having fundamentally different layouts (a union's members overlap in
    memory; a struct's/class's don't) — copying one's layout onto the other
    would be wrong regardless of how well the names line up (Codex review).
    ``is_union`` must agree before a match is used at all.

    The clang header backend never namespace-qualifies ``RecordType.name``
    at all (Codex review, fresh evidence) — so two distinct public records
    that happen to collide on the same bare name (``api::Foo`` and
    ``impl::Foo``, both stored as plain ``"Foo"``) would otherwise both
    match whatever single DWARF candidate that name resolves to, silently
    aliasing one type's real layout onto the other. That collision is a
    pre-existing limitation of the clang backend generally (the same bare
    name would already collide in ``AbiSnapshot``'s own ``_type_by_name``
    index used for diffing), not something this function can fix on its
    own, but it's cheap to guard against locally: any header type whose
    bare name isn't unique among this snapshot's *own* header-parsed types
    is left unmatched outright, before even attempting a DWARF lookup.

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
    but only when the *header* side is known to be a genuine anonymous-
    aggregate flatten, not merely "the header happens to have fields". A
    record whose members are all injected from an anonymous struct/union
    (``struct Foo { union { int i; float f; }; };``) is flattened onto the
    header side by ``dumper_clang.py`` (so ``header.fields`` lists ``i``/
    ``f`` directly, and ``RecordType.has_anonymous_aggregate_fields`` is set)
    but the DWARF builder does not flatten it the same way, leaving
    ``dwarf.fields`` empty even though DWARF *does* carry the record's real
    ``size_bits`` — rejecting that on "no overlap" would make every such
    struct permanently layout-blind under the clang backend (Codex review),
    which is a real, common C pattern, not a hypothetical. The exception is
    keyed off that dedicated flag rather than field non-emptiness alone, so
    an *ordinary* struct with real (non-anonymous) fields whose DWARF
    counterpart happens to be absent doesn't get the same free pass — that
    requires the flag to mean "*every* field came from the flatten", not
    "at least one did" (Codex review, fresh evidence): a mixed record like
    ``struct Foo { union { int i; }; int tag; };`` sets the same flag if it
    were computed from mere field-injection presence, letting an ordinary
    field (``tag``) ride along with no corroboration of its own —
    ``dumper_clang.py`` computes the flag as ``all(f.name in injected for f
    in fields)``, not ``any(...)``, specifically to close this. The flag
    only vouches for the *header* side, though — it says nothing about
    whether the specific unique suffix-matched DWARF candidate is really
    the same declaration, so a non-empty ``dwarf.vtable`` (an unrelated,
    fieldless-but-polymorphic type) still blocks the match even with the
    flag set (Codex review): DWARF, unlike the header parser, does
    populate ``vtable`` for a genuinely polymorphic type.

    The reverse — an empty *header* type matched against a DWARF candidate
    that DOES have fields — gets no such exception (Codex review): a header
    such as ``struct Foo {};`` with no DWARF emission of its own could
    otherwise silently match a unique but unrelated internal ``impl::Foo {
    int x; }`` via the bare-name suffix, backfilling the public empty type's
    layout from a type that isn't actually the same declaration.

    A C++ record's ABI surface is not only its data fields, though: an empty
    *derived* class, or one with only virtual methods, has no fields on
    either side yet still carries real layout via its base classes (Codex
    review — a fieldless ``impl::Foo`` with unrelated *bases* would
    otherwise pass an empty-vs-empty trust unchallenged). Whenever DWARF's
    field list is empty — both the "genuinely fieldless on both sides" case
    and the anonymous-aggregate case above, since field names alone can't
    tell a real same-declaration match from a coincidentally-fieldless
    unrelated type in either — base-class-name overlap is checked as a
    second corroborating signal, combining ``bases`` *and* ``virtual_bases``
    together (both the clang header parser and the DWARF builder file
    virtual inheritance under ``virtual_bases`` rather than ``bases`` —
    Codex review: a virtual-inheritance-only class, e.g. ``Foo : virtual
    PublicBase``, would otherwise leave both ``.bases`` sets empty and fall
    straight through unchallenged). Vtable entries can't play the same
    role: the clang header parser never populates ``RecordType.vtable``
    itself (only the DWARF side ever does, pre-backfill), so comparing
    vtable presence would reject every legitimate virtual-only match, not
    just the unrelated ones.

    Base names, like record names, need normalizing before comparison: the
    clang header parser stores each base's full ``qualType`` (e.g.
    ``"api::Base"``), while the DWARF builder's base resolution only ever
    reads ``DW_AT_name`` (always bare — ``"Base"``, never scope-qualified,
    unlike a DWARF *record's* own name). Comparing the raw strings would
    reject a namespaced base's legitimate match (Codex review), so both
    sides are reduced to their bare last-``::``-segment before the overlap
    check.

    That normalization is also an accepted, structural limitation, not a
    corroboration gap this module can close (Codex review, fresh evidence):
    since DWARF's base resolution is *always* bare, two entirely unrelated
    types declaring differently-namespaced bases that happen to share the
    same bare identifier (``api::Base`` vs. ``impl::Base``, both reduced to
    ``"Base"``) would appear to overlap. Recovering the DWARF base's real
    scope would need a general "resolve any type DIE to its fully qualified
    name" capability — this codebase has no such thing anywhere, not just
    here: ``_compute_type_name`` (used for every field/parameter/return
    type, not only bases) resolves a ``DW_TAG_structure_type`` reference to
    its bare ``DW_AT_name`` too, since qualified names are only known during
    the top-down namespace-scoped traversal that builds each record's own
    ``.name``, not when resolving an arbitrary reference to one. Adding that
    capability (an offset-to-qualified-name index built during the main
    walk) is a real feature, not a corroboration tweak, so it's left as a
    documented residual: reachable only when the header type's own DWARF
    counterpart is absent *and* an unrelated type coincidentally shares a
    bare base name — the same "counterpart missing" precondition every
    other residual risk in this function already requires.

    The one case this still can't distinguish (Codex review, fresh evidence
    after the base-corroboration fix above): a header type with *ordinary*
    (non-anonymous-aggregate) fields matched against a *totally unrelated*
    DWARF candidate that happens to have zero fields *and* zero bases —
    e.g. public ``struct Foo { int x; }`` next to an unrelated, genuinely
    empty ``impl::Foo {};`` reached only via the bare-suffix fallback. There
    is no remaining signal on the DWARF side to disagree with (no fields,
    no bases), so name equality is the last signal left — and neither an
    *exact* nor a *suffix* name match is trusted on that alone (Codex
    review, fresh evidence: an exact match was originally trusted
    unconditionally here, on the reasoning that ``dwarf.name ==
    header.name`` implies a genuinely unscoped type with no ambiguity — but
    since the clang header parser never namespace-qualifies
    ``RecordType.name`` at all regardless of the type's *real* scope, an
    exact match only shows the DWARF *candidate* has no scope of its own,
    not that it is the header's actual, possibly-namespaced counterpart;
    a public ``api::Foo { int x; }`` with no DWARF emission of its own can
    collide with an unrelated, genuinely global-scope, empty ``Foo`` in
    DWARF exactly as easily via an exact match as via a suffix one). Only a
    trivial fieldless header record, or one whose fields are known to come
    from an anonymous-aggregate flatten, still gets trusted here — and so
    does a suffix *or* exact match when ``has_anonymous_aggregate_fields``
    is set, since that flag is a structural fact about the header record,
    not a guess from field non-emptiness (Codex review, see above) — but
    both, regardless of which key resolved the match, require
    ``not dwarf.vtable`` too (Codex review, fresh evidence): a genuinely
    empty header record is not itself proof of "nothing left to disagree
    with" once the DWARF candidate has a vtable the header side
    structurally can't (the clang header parser never populates
    ``RecordType.vtable`` itself), so a fieldless-but-polymorphic unrelated
    type must still be rejected exactly like the anonymous-aggregate case.

    Returns ``(backfilled_types, coherence)`` (P0 evidence-coherence audit):
    *coherence* is purely observational bookkeeping over the same
    accept/reject decisions already made above — it changes nothing about
    which records get backfilled. A "mismatch" bucket entry means this
    function found a uniquely-named DWARF candidate and *declined* to trust
    it (the record already stays header-only/incomplete, exactly as
    before) — the caller uses this to make that refusal visible rather than
    silent, not to react differently to it here. *coherence* is ``None``
    when *dwarf_types* is empty (this function is a pure no-op then): this
    module has no way to tell "the clang backend ran but the binary carried
    no DWARF at all" (a real "unavailable" coherence state) apart from
    "the header backend was castxml, which never calls this function's
    caller with any DWARF types to begin with, because it doesn't need
    to" (not a coherence question at all) — only ``dumper.py`` knows which
    case it's in, so it decides the final ``AbiSnapshot.dwarf_layout_coherence``
    value for this case itself.
    """
    if not dwarf_types:
        return header_types, None
    dwarf_candidates: dict[str, list[RecordType]] = {}
    for t in dwarf_types:
        # A set, not a sequence: for an unscoped name the suffix *is* the name,
        # and appending `t` twice under the same key would make the
        # ambiguity check in `_dwarf_match` see two candidates for one type.
        for key in {t.name, _topmost_scope_suffix(t.name)}:  # pylint: disable=use-sequence-for-iteration
            dwarf_candidates.setdefault(key, []).append(t)

    header_name_counts: dict[str, int] = {}
    for t in header_types:
        header_name_counts[t.name] = header_name_counts.get(t.name, 0) + 1

    matched: list[str] = []
    mismatched: list[str] = []
    unavailable_types: list[str] = []
    ambiguous: list[str] = []

    out: list[RecordType] = []
    for t in header_types:
        if t.size_bits is not None or t.is_opaque or t.is_template_pattern:
            out.append(t)
            continue
        if header_name_counts[t.name] > 1:
            # The clang header parser never namespace-qualifies
            # RecordType.name (Codex review, fresh evidence): two distinct
            # public records that collide on the same bare name (e.g.
            # api::Foo and impl::Foo, both stored as "Foo") would otherwise
            # both match the *same* unique DWARF candidate here, silently
            # aliasing one type's real layout onto an unrelated one. This is
            # a symptom of a pre-existing, broader limitation — the same
            # bare-name collision already applies to AbiSnapshot's own
            # `_type_by_name` index used for diffing, not something specific
            # to this backfill step — so it's out of scope to fix generally
            # here, but cheap to guard locally: skip backfilling any header
            # type whose bare name isn't even unique within this snapshot's
            # own header-parsed types, since there's no way to tell which of
            # the colliding types a name-based match actually belongs to.
            out.append(t)
            ambiguous.append(t.name)
            continue
        dwarf_t = _unique_dwarf_match(dwarf_candidates, t.name)
        if dwarf_t is None:
            out.append(t)
            unavailable_types.append(t.name)
            continue
        if t.is_union != dwarf_t.is_union or not _fields_corroborate(t, dwarf_t):
            out.append(t)
            mismatched.append(t.name)
            continue
        matched.append(t.name)
        out.append(_backfilled_record(t, dwarf_t))
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
