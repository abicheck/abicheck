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

"""Whole-binary resolution of inherited ``RecordType.vptr_offset_bits``.

Split out of ``dwarf_snapshot.py`` (ADR-061: a legacy root module migrating
its responsibilities into ``extract/``). The pass needs nothing from the DWARF
builder beyond the record list and the base-edge indexes it collected during
the CU walk, all passed in explicitly. Leaf module: depends only on
``model`` (allowed: ``extract -> model``).
"""

from __future__ import annotations

from ..model import RecordType, resolve_vptr_offset_bits

DieKey = tuple[int, int]


def finalize_vptr_offsets(
    types: list[RecordType],
    *,
    record_die_index: dict[DieKey, RecordType],
    die_key_to_qualified_name: dict[DieKey, str],
    record_by_qualified_name: dict[str, RecordType],
    base_edges_by_record: dict[int, list[tuple[str, int | None, DieKey | None]]],
    virtual_base_edges_by_record: dict[int, list[tuple[str, DieKey | None]]],
) -> None:
    """Resolve ``vptr_offset_bits`` for types whose vtable is inherited.

    ``_process_record_type_named`` already set ``vptr_offset_bits`` from
    each class's own artificial vptr member
    (``_vptr.<Class>``/``_vptr$<Class>``, read directly off DWARF instead
    of assumed to be 0 — G31 Phase C's "vptr placement is only a
    0-if-polymorphic heuristic" gap; verified against real GCC 13/Clang
    18 ``-g`` output for plain, multiple-inheritance, and virtual-base
    cases before writing this). Left ``None`` there is a class that only
    *extends or overrides* an already-inherited vtable (adds a new
    virtual method, or overrides one, without introducing a new vtable
    slot of its own) — it has no local vptr member at all, not even when
    every one of its own declared methods is virtual, or, in the most
    extreme case, when it declares no virtual methods of its own
    whatsoever and is polymorphic purely by inheritance (confirmed
    empirically: a class that neither adds nor overrides anything still
    carries no local ``vtable`` entries).

    This runs as a separate, whole-binary pass — *after* every CU has
    been walked and every record type is in ``types`` — rather than
    resolving eagerly per-class during the walk, because DWARF's
    per-child-DIE order does not correlate with declaration order (a
    compiler may emit a type's full definition at its first point of
    *use* in codegen, not at its point of *declaration* in the source —
    confirmed empirically: a subclass with no local vtable slot can be
    emitted by GCC before its own base class is). An eager, walk-order
    lookup would therefore silently miss a base processed later and
    leave a real inherited vptr resolved as unknown.

    Per Itanium C++ ABI (and, empirically, this is also what MSVC's
    non-virtual-inheritance layout does), the primary base — the one
    non-virtual base whose vtable pointer a derived class shares — is
    always placed at absolute offset 0 within the derived object, when
    one exists; a virtual base is never primary (its offset is resolved
    dynamically through the vbase-offset table, never a static
    ``DW_AT_data_member_location``) and is therefore never a candidate —
    mirrored by ``base_offsets`` only ever holding non-virtual bases
    (``_process_inheritance_child``'s own population of it). Since the
    primary base sits at offset 0, its own absolute vptr offset *is* the
    derived class's absolute vptr offset — no arithmetic needed, just a
    lookup once the base's own offset is known.

    A fixed-point loop (bounded by the actually-unresolved count, not a
    fixed depth) over the whole-binary type list handles a
    derived-of-derived chain (``M : N : A``) regardless of which of the
    three DWARF happened to emit first, converging once no further
    progress is made in a full pass over what remains unresolved.

    Resolving *which* base a bare name refers to is itself two-tiered,
    and deliberately does NOT walk ``RecordType.bases``/``base_offsets``
    (both pre-existing, bare-name-keyed fields, unrelated to this fix)
    directly. Two independent gaps were found and fixed here (both
    Codex review, both reproduced against real compiled binaries before
    fixing):

    1. **Namespace ambiguity.** ``types`` is keyed by *qualified*
       name the moment a namespace or enclosing class is involved
       (``qualified = f"{scope}::{name}"``), so a bare-name lookup alone
       silently fails to resolve `ns::N : ns::A`'s inherited vptr even
       though `ns::A`'s own offset is already known. Fixed by resolving
       each edge via the base DIE's own identity
       (``_base_edges_by_record``/``_record_die_index``, captured while
       each inheritance edge was still directly at hand — see
       ``_resolve_base_name_and_key``'s docstring), falling back to a
       bare-name lookup only when a base DIE didn't resolve to a key at
       all. A DIE for the SAME logical type discarded by ODR dedup (the
       same header parsed in a second CU) is separately aliased into
       ``_record_die_index`` at dedup time — see
       ``_process_record_type_named`` — so a base referenced from that
       second CU still resolves instead of finding an unregistered key.
    2. **Same-bare-name collision across two DIRECT bases of one
       class.** `D : one::A, two::A` — two distinct bases sharing a bare
       name in different namespaces — cannot be represented by a dict
       keyed on that bare name at all: the second edge's offset/identity
       silently overwrites the first's. This is why the walk below
       iterates ``_base_edges_by_record``'s ordered *list* of `(name,
       offset, key)` tuples, one per DW_TAG_inheritance child, rather
       than ``rec.bases`` (a plain name list) paired with
       ``rec.base_offsets``/a name-keyed dict (either pairing reopens
       the same collision `base_edges` exists specifically to avoid).

    A base DIE key can point at three different shapes, tried in order:
    the retained definition itself (``_record_die_index``, the common
    case); an ODR-duplicate or declaration-only stub DIE that names a
    *different* qualified type than its own bare name would suggest
    (``_die_key_to_qualified_name`` → ``_record_by_qualified_name`` —
    the exact shape GCC emits for a base referenced from a CU that
    never defines it itself, resolved here rather than eagerly, since
    the real definition is not guaranteed to already be known at the
    point the stub DIE was encountered); and, only once both DIE-identity
    tiers miss, the original bare-name ``by_name`` fallback.

    A LAST, final pass (after the fixed-point loop below) restores the
    original blanket "0 if polymorphic" heuristic for whatever remains
    unresolved — see that pass's own comment for the real case requiring
    it (a virtual-only base sharing a "nearly empty" primary base's
    vptr, which never enters this loop at all since virtual bases are
    excluded from ``bases``/``base_edges`` for the same static-offset
    reason ``base_offsets`` excludes them). This makes the whole
    function strictly additive over the pre-fix behavior: every type the
    old code resolved to 0 still resolves to (at worst) 0, and several
    classes it could only guess at now resolve to a real, DWARF-derived
    offset instead.
    """
    by_name = {t.name: t for t in types}

    def _resolve_base_record(
        base_name: str, base_key: tuple[int, int] | None
    ) -> RecordType | None:
        """Resolve a base-edge (name, DIE key) to its RecordType.

        Three tiers, tried in order: the retained definition itself
        (``_record_die_index``, the common case); an ODR-duplicate or
        declaration-only stub DIE that names a *different* qualified
        type than its own bare name would suggest
        (``_die_key_to_qualified_name`` → ``_record_by_qualified_name``);
        and, only once both DIE-identity tiers miss (or *base_key* is
        ``None``), the bare-name ``by_name`` fallback. Shared by both
        the primary-base walk and the virtual-primary-base fallback
        tier below, so a namespaced base resolves identically in both.
        """
        base_rec = record_die_index.get(base_key) if base_key is not None else None
        if base_rec is None and base_key is not None:
            aliased_name = die_key_to_qualified_name.get(base_key)
            if aliased_name is not None:
                base_rec = record_by_qualified_name.get(aliased_name)
        if base_rec is None:
            base_rec = by_name.get(base_name)
        return base_rec

    unresolved = [t for t in types if t.vptr_pending(t.resolved_bases())]
    progressed = True
    while progressed and unresolved:
        progressed = False
        still_unresolved = []
        for rec in unresolved:
            edges = base_edges_by_record.get(id(rec), [])
            resolved = None
            for base_name, offset_bits, base_key in edges:
                if offset_bits != 0:
                    continue
                base_rec = _resolve_base_record(base_name, base_key)
                if base_rec is not None and not base_rec.vptr_pending(True):
                    resolved = base_rec.resolved_vptr_offset_bits()
                    break
            if resolved is not None:
                resolve_vptr_offset_bits(rec, resolved)
                progressed = True
            else:
                still_unresolved.append(rec)
        unresolved = still_unresolved

    # Final fallback: restore the original "0 if polymorphic" heuristic
    # for whatever remains unresolved, rather than leaving it None.
    # Real gap (Codex review, reproduced against real GCC output):
    # `struct E : virtual A { virtual void e(); };` where A is "nearly
    # empty" (no data members beyond its own vtable pointer) can be laid
    # out under the Itanium ABI's virtual-primary-base rule so E and A
    # SHARE one vptr slot at offset 0 with no local `_vptr.E` member of
    # E's own — GCC emits no such member here, unlike every other
    # virtual-base case this fix already handles (`struct D : virtual A
    # { virtual void d(); int di; };`, which DOES get its own `_vptr.D`,
    # confirmed by DIE dump, since D has a real data member forcing a
    # non-degenerate layout). Because E's only base is virtual, it's
    # entirely absent from `bases`/`base_edges` (mirroring `base_offsets`'s
    # own long-standing exclusion of virtual bases — their offset is
    # dynamic, not a fixed one this resolution walks), so the loop above
    # never even considers it. Every class this applies to has
    # `own_vptr_offset_bits is None` (no local member) AND is unreachable
    # via the primary-base walk (no resolvable non-virtual base, or none
    # at all) — exactly the shape the original heuristic covered by
    # assuming 0 for any type with a non-empty `vtable`, so restoring it
    # here for the specific remaining unresolved set is a pure regression
    # fix, not a reintroduction of the original guess for cases this pass
    # already resolves more precisely (those are excluded here since
    # their `vptr_offset_bits` is already set).
    for rec in types:
        if rec.vptr_pending(rec.resolved_vtable()):
            resolve_vptr_offset_bits(rec, 0)

    # Second final-fallback tier: a class that is polymorphic ONLY
    # through a "nearly empty" virtual base, adding or overriding no
    # virtual method of its own at all, has neither a local vptr member
    # nor any entry in its own `vtable` list -- confirmed with
    # `struct A { virtual void a(); }; struct E : virtual A {};`: GCC
    # emits no local `_vptr.E` and `E.vtable` is empty, so the tier
    # above (gated on `rec.vtable`) never applies (Codex review, fresh
    # evidence). Unlike the first fallback tier, this one is a genuine
    # accuracy improvement, not a regression fix: the pre-fix heuristic
    # (`0 if vtable else None`) ALSO returned `None` for this exact
    # shape, since `vtable` was always empty here -- there was no prior
    # "0" answer for this case to preserve. Resolved by checking whether
    # any of this class's OWN virtual bases is itself already known to
    # be polymorphic. A polymorphic virtual base is positive evidence
    # *this* class shares that vtable pointer under the same
    # virtual-primary-base rule the first tier's own comment documents,
    # so 0 is used unconditionally here too -- not the base's own
    # (possibly different, if it isn't nearly-empty) offset.
    #
    # Resolution is DIE-identity-first, the same three-tier lookup the
    # non-virtual primary-base walk above uses, NOT a bare `by_name`
    # lookup on its own: a namespaced virtual base (`ns::E : virtual A`)
    # has its bare name `"A"` recorded in `rec.virtual_bases`, so a
    # pure-name lookup silently fails to find `ns::A`'s already-resolved
    # offset -- the identical namespace-ambiguity gap the non-virtual
    # walk's own docstring documents, reproduced here for the
    # virtual-only fallback tier specifically (Codex review, fresh
    # evidence). `_virtual_base_edges_by_record` carries only a name +
    # DIE key per edge, no offset field (a virtual base's own subobject
    # offset is inherently dynamic, unlike a non-virtual `base_edges`
    # entry) -- this tier only ever answers "0 or unknown," never a real
    # non-zero offset, so it has no arithmetic to do once a candidate
    # resolves.
    #
    # This must be a fixed-point loop, not a single pass, for the same
    # reason the primary-base walk above is one: a multi-level virtual
    # chain (`struct F : virtual E {}; struct E : virtual A {};`) needs
    # E resolved before F can resolve through it, and `types`
    # iteration order follows DWARF emission order, not dependency
    # order (Codex review, reproduced against real GCC AND Clang output
    # with exactly this A/E/F shape: `types` came out as
    # `[F, A, E]` under GCC, so a single pass checked F while E was
    # still `None`, set E to 0, and left F unresolved).
    virtual_unresolved = [
        t for t in types if t.vptr_pending(t.resolved_virtual_bases())
    ]
    progressed = True
    while progressed and virtual_unresolved:
        progressed = False
        still_unresolved = []
        for rec in virtual_unresolved:
            virtual_edges = virtual_base_edges_by_record.get(id(rec), [])
            if any(
                (vbase := _resolve_base_record(vbase_name, vbase_key)) is not None
                and not vbase.vptr_pending(True)
                for vbase_name, vbase_key in virtual_edges
            ):
                resolve_vptr_offset_bits(rec, 0)
                progressed = True
            else:
                still_unresolved.append(rec)
        virtual_unresolved = still_unresolved
