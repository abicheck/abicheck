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

    A name (and field-name) match alone is not enough, either: a struct/
    class and a union can share a bare name and even a member name while
    having fundamentally different layouts (a union's members overlap in
    memory; a struct's/class's don't) — copying one's layout onto the other
    would be wrong regardless of how well the names line up (Codex review).
    ``is_union`` must agree before a match is used at all.

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
    counterpart happens to be absent doesn't get the same free pass. The
    flag only vouches for the *header* side, though — it says nothing about
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

    The one case this still can't distinguish (Codex review, fresh evidence
    after the base-corroboration fix above): a header type with *ordinary*
    (non-anonymous-aggregate) fields matched against a *totally unrelated*
    DWARF candidate that happens to have zero fields *and* zero bases —
    e.g. public ``struct Foo { int x; }`` next to an unrelated, genuinely
    empty ``impl::Foo {};`` reached only via the bare-suffix fallback. There
    is no remaining signal on the DWARF side to disagree with (no fields,
    no bases), so name equality is the last signal left — and a *suffix*
    match (``impl::Foo`` recovered only because it ends in "Foo") is
    rejected here rather than trusted (CodeRabbit review): it already means
    the header's bare name lacks the scope DWARF's qualified name carries,
    so stacking that on top of zero field/base evidence would trust two
    independently weak signals at once. An *exact* name match
    (``dwarf.name == header.name``, e.g. a genuinely unscoped ``struct Foo
    {};`` gaining a field later) carries no such scope ambiguity and is
    still trusted even with nothing else to go on — and so does a suffix
    match when ``has_anonymous_aggregate_fields`` is set, since that flag
    is a structural fact about the header record, not a guess from field
    non-emptiness (Codex review, see above).
    """
    if not dwarf_types:
        return header_types
    dwarf_candidates: dict[str, list[RecordType]] = {}
    for t in dwarf_types:
        for key in {t.name, _topmost_scope_suffix(t.name)}:
            dwarf_candidates.setdefault(key, []).append(t)

    def _dwarf_match(name: str) -> RecordType | None:
        candidates = dwarf_candidates.get(name, [])
        return candidates[0] if len(candidates) == 1 else None

    def _fields_corroborate(header: RecordType, dwarf: RecordType) -> bool:
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
        header_bases = {_topmost_scope_suffix(b) for b in header.bases + header.virtual_bases}
        dwarf_bases = {_topmost_scope_suffix(b) for b in dwarf.bases + dwarf.virtual_bases}
        if header_bases or dwarf_bases:
            return bool(header_bases & dwarf_bases)
        if header.name == dwarf.name:
            # Exact match: no scope ambiguity even with nothing else to
            # disagree on — covers both a truly trivial tag type and an
            # unnamespaced anonymous-aggregate record.
            return True
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
        return header.has_anonymous_aggregate_fields and not dwarf.vtable

    out: list[RecordType] = []
    for t in header_types:
        if t.size_bits is not None or t.is_opaque or t.is_template_pattern:
            out.append(t)
            continue
        dwarf_t = _dwarf_match(t.name)
        if (
            dwarf_t is None
            or t.is_union != dwarf_t.is_union
            or not _fields_corroborate(t, dwarf_t)
        ):
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
