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

"""Record-entity parsing for the castxml backend (ADR-061 D9).

Third entity module split out of ``_CastxmlParser`` proper, after
``enums.py`` and ``functions.py``. Reads ``ctx.record_els``/``ctx.typedef_els``
(both populated once by :meth:`~.context.CastxmlParserContext.build_id_map`)
and produces ``RecordType`` model objects, including the vtable/RTTI layout
walk (:func:`build_vtable` and its helpers) that reconstructs each
polymorphic type's virtual-method slot order from castxml's per-method
``vtable_index``/``overrides`` attributes.

This is the fullest test of the shared-context design so far: unlike
``enums.py``/``functions.py``, record parsing reads and *writes* memoized
cross-record state (``ctx.vtable_slot_root``/``ctx.vtable_slot_extra_roots``)
while walking a class's base hierarchy, not just read-only lookups. Both
caches already lived on :class:`~.context.CastxmlParserContext` before this
module existed (Phase 5's ``functions.py`` slice put them there, since
``RecordVtableIndex``'s clang counterpart needed the analogous state too),
so no context-shape change was needed to move the code that populates them
— only the code itself. Every function here takes the context object
explicitly rather than reading ``self``, per D9's "entity modules parse one
class of node using shared context"; the vtable-slot-key resolver
(:func:`vtable_slot_key`) and the recursive slot collector
(:func:`collect_virtual_methods`) are the two functions that actually mutate
``ctx.vtable_slot_root``/``ctx.vtable_slot_extra_roots`` as a side effect of
resolving a class's own slots, exactly as the pre-split methods did on
``self``.

``access_level``/``is_builtin_element``/``source_location``/``qualified_type_name``/
``resolve_cv_restrict``/``type_name`` are NOT here even though record parsing
needs them: each is also read by function/variable/typedef parsing (some
still in ``dumper_castxml.py``), so per this package's own "shared across
entity kinds" rule they live in ``location.py``/``type_resolution.py``
instead, the same way ``qualified_name``/``decl_is_public`` already do for
``functions.py``.
"""

from __future__ import annotations

import re
from typing import Any

from ....model import Fact, RecordType, TypeField
from ....model.identity import entity_id_for_type
from ....name_classification import strip_anonymous_type_location
from .context import CastxmlParserContext
from .location import (
    access_level,
    deprecation_marker as _deprecation_marker,
    is_builtin_element,
    optional_int_attr,
    source_location,
)
from .names import (
    _parse_vtable_index,
    _virtual_method_mangled_name,
    _vt_sort_key,
)
from .scope import scope_path
from .type_resolution import qualified_type_name, resolve_cv_restrict, type_name


def parse_types(ctx: CastxmlParserContext) -> list[RecordType]:
    """Every public ``RecordType`` — direct declarations plus anonymous
    struct/union bodies reachable only through a ``typedef``."""
    # Build reverse mapping: struct/union ID → typedef name for anonymous
    # types. This allows us to include `typedef struct { ... } Foo;` where
    # the struct itself is anonymous (name="") but reachable via the
    # typedef.
    typedef_name_for: dict[str, str] = {}
    for el in ctx.typedef_els:
        td_name = el.get("name", "")
        if not td_name:
            continue
        target_id = el.get("type", "")
        target_el = ctx.resolve(target_id)
        # Follow through ElaboratedType / CvQualifiedType wrappers
        # that castxml may insert between Typedef and the actual Struct.
        while target_el is not None and target_el.tag in (
            "ElaboratedType",
            "CvQualifiedType",
        ):
            target_id = target_el.get("type", "")
            target_el = ctx.resolve(target_id)
        if target_el is not None and target_el.tag in ("Struct", "Class", "Union"):
            target_name = target_el.get("name", "")
            if not target_name:
                # Anonymous struct/union with a typedef alias — record it.
                # Use the struct's own id as key (may differ from the
                # Typedef's type attr when ElaboratedType is involved).
                struct_id = target_el.get("id", "")
                if struct_id:
                    typedef_name_for[struct_id] = td_name

    types = []
    for el in ctx.record_els:
        if is_public_record_type(ctx, el):
            types.append(build_record_type(ctx, el))
        else:
            # ctx.record_els is already pre-filtered to Struct/Class/Union
            # (see CastxmlParserContext.build_id_map), so this is every
            # record type is_public_record_type rejected. Check if it's an
            # anonymous struct reachable via typedef.
            eid = el.get("id", "")
            override_name = typedef_name_for.get(eid)
            if override_name and not is_builtin_element(ctx, el):
                types.append(build_record_type(ctx, el, override_name=override_name))
    return types


def is_public_record_type(ctx: CastxmlParserContext, el: Any) -> bool:
    if el.tag not in ("Struct", "Class", "Union"):
        return False
    name = el.get("name", "")
    if not name or el.get("artificial") == "1":
        return False
    if name.startswith("__"):
        return False
    # Skip compiler built-ins and command-line synthetic types
    if is_builtin_element(ctx, el):
        return False
    return True


def build_record_type(
    ctx: CastxmlParserContext, el: Any, override_name: str | None = None
) -> RecordType:
    name = strip_anonymous_type_location(override_name or el.get("name", ""))
    is_opaque = el.get("incomplete") == "1"
    vtable = [] if is_opaque else build_vtable(ctx, el.get("id", ""))

    def _base_names(*, virtual: bool) -> list[str]:
        return [
            type_name(ctx, b.get("type", ""))
            for b in el
            if b.tag == "Base" and (b.get("virtual") == "1") == virtual
        ]

    bases = [] if is_opaque else _base_names(virtual=False)
    virtual_bases = [] if is_opaque else _base_names(virtual=True)
    # Polymorphic (non-empty vtable) → vtable pointer at offset 0; None when non-polymorphic so the diff can tell "gained a vptr" apart.
    vptr_offset_bits = 0 if vtable else None
    # Best-effort layout descriptor (layout-closure work): direct (non-virtual) base subobject offsets from each ``<Base offset=...>``; the unit only has to be consistent across snapshots for change detection, and it is.
    base_offsets: dict[str, int] = {}
    if not is_opaque:
        for b in el:
            if b.tag == "Base" and b.get("virtual") != "1":
                off = optional_int_attr(b, "offset")
                if off is not None:
                    base_offsets[type_name(ctx, b.get("type", ""))] = off
    # is_standard_layout / is_trivially_copyable / data_size_bits are left None: "not polymorphic and no virtual bases" is not a sound standard-layout signal (a mixed-access class is already non-standard-layout, so the heuristic would flip True→False on gaining a virtual and emit a spurious STANDARD_LAYOUT_LOST), and CastXML doesn't expose the trivially-copyable trait directly (Codex review #345).
    # castxml records the `final` class-key specifier as a `final` token
    # inside the compound ``attributes`` string (e.g. ``attributes="final"``),
    # the same channel used for noexcept -- header mode always knows the
    # answer, so this is a concrete bool, never None on the castxml path.
    is_final = bool(re.search(r"\bfinal\b", el.get("attributes", "")))
    # ADR-063 Phase 5 (Codex review): qualified_type_name()'s own None
    # return means EITHER a genuine, confirmed global-scope record OR a
    # broken/cyclic context chain it gave up walking (see that function's
    # own docstring) -- but the former is overwhelmingly the common shape
    # a None return actually takes (a truly cyclic/16-deep XML context
    # chain is a pathological, essentially unobserved case), so treating
    # the header-AST determination as PRESENT here -- matching is_final's
    # own "always a concrete value on this path" precedent -- is correct
    # for the case this matters for. The rare pathological walk-failure
    # case is a known, accepted imprecision, the same class of trade-off
    # AGENTS.md's own "Known gaps" entries already document elsewhere.
    qualified_name = qualified_type_name(ctx, el, leaf_name=name)
    return RecordType(
        name=name,
        kind=el.tag.lower(),
        size_bits=optional_int_attr(el, "size"),
        alignment_bits=optional_int_attr(el, "align"),
        fields=[] if is_opaque else parse_record_fields(ctx, el),
        bases=bases,
        virtual_bases=virtual_bases,
        vtable=vtable,
        is_union=el.tag == "Union",
        is_opaque=is_opaque,
        vptr_offset_bits=vptr_offset_bits,
        base_offsets=base_offsets,
        # castxml genuinely resolves these itself (real semantic analysis, not a heuristic reconstruction), opaque or not -- stated explicitly (kept as individual kwargs, not a **record_layout_facts() spread, so scripts/backend_capabilities.py's AST scanner can still see each field named).
        bases_fact=Fact.present(bases),
        virtual_bases_fact=Fact.present(virtual_bases),
        vtable_fact=Fact.present(vtable),
        # 0-if-vtable-else-None is the Itanium primary-base heuristic above, not a real offset read -- partial, not present (Codex review; matches vptr_offset_bits's own PARTIAL row).
        vptr_offset_bits_fact=Fact.partial(vptr_offset_bits),
        # ADR-063 Phase 2: resolved from the typed `context`-chain walk,
        # which keeps each parent's own XML tag -- never reconstructed from
        # the flattened `qualified_name` on the next line, which cannot say
        # whether a segment was a namespace or a record.
        entity_id=entity_id_for_type(scope_path(ctx, el), name),
        qualified_name=qualified_name,
        qualified_name_fact=Fact.present(qualified_name),
        is_final=is_final,
        # ADR-063 Phase 5: Fact[bool | None] sibling of is_final -- see the
        # local variable's own comment above for why this is always a real,
        # concrete determination on the castxml path.
        is_final_fact=Fact.present(is_final),
        source_location=source_location(ctx, el),
        # castxml's `abstract="1"` marks a class/struct with at least one
        # pure virtual function (cannot be instantiated). Header mode
        # always knows the answer for a complete type, matching the
        # `is_final` convention above; left None for an opaque/incomplete
        # record (no member list to have judged it from).
        is_abstract=None if is_opaque else el.get("abstract") == "1",
        # `[[deprecated("msg")]]` -> the message text verbatim; a bare
        # `[[deprecated]]` with no message -> "" (see _deprecation_marker:
        # castxml only emits the `deprecation` XML attribute when there
        # IS a message, so a bare marker must be read from the
        # compound `attributes` string instead); not deprecated -> None.
        deprecated=_deprecation_marker(el),
    )


def parse_record_fields(ctx: CastxmlParserContext, el: Any) -> list[TypeField]:
    """Parse struct/class/union fields.

    castxml uses two layouts depending on version / output mode:
    - Inline children: ``<Struct><Field .../></Struct>``
    - Members attribute: ``<Struct members="_14 _15 _16 ..."/>`` (IDs resolved via id_map)

    We support both: first scan inline children, then fall back to the
    ``members`` attribute so we never miss fields in either format.
    """
    fields: list[TypeField] = []

    # Collect Field elements: inline children first
    field_elements: list[Any] = [c for c in el if c.tag == "Field"]

    # Fallback: resolve via space-separated "members" attribute
    if not field_elements:
        for mid in el.get("members", "").split():
            member_el = ctx.id_map.get(mid)
            if member_el is not None and member_el.tag == "Field":
                field_elements.append(member_el)

    for child in field_elements:
        child_name = child.get("name", "")
        if not child_name:
            # Anonymous struct/union member — flatten its fields into parent
            fields.extend(expand_anonymous_field(ctx, child))
            continue
        bitfield_bits, is_bitfield = parse_bitfield_bits(child.get("bits"))
        field_type_id = child.get("type", "")
        field_type = type_name(ctx, field_type_id)
        # Resolved from the real XML type chain (following through any
        # Typedef indirection), not a regex over `field_type`: a field
        # declared through a typedef to a cv-qualified type (`typedef
        # const int T; struct S { T x; };`) renders as the bare alias
        # name ("T"), which a spelling-based regex could never see
        # through (Codex review, PR #582).
        field_const, field_volatile, _ = resolve_cv_restrict(ctx, field_type_id)
        fields.append(
            TypeField(
                name=child_name,
                type=field_type,
                offset_bits=optional_int_attr(child, "offset"),
                is_bitfield=is_bitfield,
                bitfield_bits=bitfield_bits,
                is_const=field_const,
                is_volatile=field_volatile,
                # castxml's Field element carries its own `mutable="1"`
                # attribute (fixed xs:int, per castxml.xsd) rather than
                # deriving it from the referenced type like const/volatile.
                is_mutable=child.get("mutable") == "1",
                access=access_level(child),
                # Default member initializer expression, verbatim
                # (castxml's Field ``init`` attribute — the same channel
                # already used for Variable/constant initializers).
                default=child.get("init"),
                # See RecordType.deprecated for the message-text convention.
                deprecated=_deprecation_marker(child),
            )
        )
    return fields


def expand_anonymous_field(
    ctx: CastxmlParserContext,
    field_el: Any,
    _depth: int = 0,
    _outer_offset: int = 0,
) -> list[TypeField]:
    """Flatten anonymous struct/union field into the parent's field list.

    In castxml output, anonymous unions/structs inside a struct appear as
    ``Field`` elements with ``name=""`` pointing to a ``Union`` or ``Struct``
    element.  We inline their named fields at the correct offset to prevent
    false ``TYPE_FIELD_REMOVED`` reports when a named field moves into an
    anonymous union (issue #58).

    ``_depth`` guards against malformed/cyclic XML (max nesting: 16).
    ``_outer_offset`` carries the accumulated offset from outer anonymous
    members so doubly-nested fields get correct absolute ``offset_bits``.
    """
    if _depth > 16:
        return []
    type_id = field_el.get("type", "")
    type_el = ctx.resolve(type_id)
    if type_el is None or type_el.tag not in ("Union", "Struct"):
        return []

    this_offset = _outer_offset + (optional_int_attr(field_el, "offset") or 0)
    result: list[TypeField] = []

    # Collect inner Field elements (inline children or members attribute)
    inner_fields: list[Any] = [c for c in type_el if c.tag == "Field"]
    if not inner_fields:
        for mid in type_el.get("members", "").split():
            member_el = ctx.id_map.get(mid)
            if member_el is not None and member_el.tag == "Field":
                inner_fields.append(member_el)

    for inner in inner_fields:
        inner_name = inner.get("name", "")
        if not inner_name:
            # Doubly-nested anonymous member — recurse, passing accumulated offset
            result.extend(
                expand_anonymous_field(
                    ctx,
                    inner,
                    _depth + 1,
                    _outer_offset=this_offset,
                )
            )
            continue
        inner_offset = optional_int_attr(inner, "offset") or 0
        bitfield_bits, is_bitfield = parse_bitfield_bits(inner.get("bits"))
        inner_type_id = inner.get("type", "")
        inner_type = type_name(ctx, inner_type_id)
        inner_const, inner_volatile, _ = resolve_cv_restrict(ctx, inner_type_id)
        result.append(
            TypeField(
                name=inner_name,
                type=inner_type,
                offset_bits=this_offset + inner_offset,
                is_bitfield=is_bitfield,
                bitfield_bits=bitfield_bits,
                is_const=inner_const,
                is_volatile=inner_volatile,
                is_mutable=inner.get("mutable") == "1",
                access=access_level(inner),
                # Same channel as the direct-field path in
                # parse_record_fields — a field inside an anonymous
                # struct/union must not lose its initializer/deprecation
                # just because it was flattened (Codex review, PR #582).
                default=inner.get("init"),
                deprecated=_deprecation_marker(inner),
            )
        )
    return result


def parse_bitfield_bits(bits_raw: str | None) -> tuple[int | None, bool]:
    try:
        bitfield_bits = int(bits_raw) if bits_raw is not None else None
    except ValueError:
        return (None, False)
    return (bitfield_bits, bitfield_bits is not None)


def build_vtable(ctx: CastxmlParserContext, class_id: str) -> list[str]:
    slots = collect_virtual_methods(ctx, class_id)
    ordered = sorted(slots.values(), key=_vt_sort_key)
    return [name for _, name in ordered]


def collect_virtual_methods(
    ctx: CastxmlParserContext,
    cid: str,
    seen: set[str] | None = None,
) -> dict[int | str, tuple[int | None, str]]:
    """Ordered mapping of *canonical vtable-slot key* -> ``(vtable_index, mangled)``.

    Keyed so a derived override replaces its base's entry **in place**
    rather than appending a duplicate: dict re-assignment to an existing
    key keeps that key's original insertion position (Python dict
    semantics), so a reused slot stays where the base declared it while a
    genuinely new virtual still appends at the end.

    ``vtable_index`` is the preferred slot identity when castxml emits it
    (unchanged from prior behavior). But that attribute is not always
    present — this castxml/Clang build may track no slot indices at all —
    and without it, a same-signature override (which reuses its base's
    slot per the Itanium ABI) has no other signal tying it to the base
    entry it replaces, so it was appended as a spurious extra slot,
    growing the reconstructed vtable by one entry it never actually
    gained (case185's false-positive ``type_vtable_changed``: a
    `Derived::paint(int) override` reusing `Base::paint(int)`'s slot read
    as vtable growth instead of a compatible rename in place).
    castxml's ``overrides`` attribute — the id of the method declaration
    this one overrides — is the fallback signal: resolved (through
    ``ctx.vtable_slot_root``, to survive multi-level override chains where
    ``overrides`` points at an intermediate override rather than the
    slot's original declarer) to the same key the overridden entry was
    stored under, so the override replaces it instead of duplicating it.
    """
    if seen is None:
        seen = set()
    if cid in seen:
        return {}
    seen.add(cid)
    class_el = ctx.id_map.get(cid)
    if class_el is None:
        return {}

    slots = inherited_vtable_slots(ctx, class_el, seen)
    for method_el in ctx.virtual_methods_by_class.get(cid, []):
        mangled_name = _virtual_method_mangled_name(method_el)
        if not mangled_name:
            continue
        mid = method_el.get("id", "")
        key, extra_keys, idx = vtable_slot_key(ctx, method_el, mid, mangled_name)
        if mid:
            # Record the *actual* slot key (int index or str id) this method
            # landed under, not just a self-reference -- a downstream override
            # in a mixed indexed/unindexed chain (e.g. Base has vtable_index,
            # Mid overrides it losing the index, Derived overrides Mid via
            # `overrides="Mid's id"`) must still resolve back to the int index
            # Base's slot is keyed by, or it would append instead of replace.
            ctx.vtable_slot_root[mid] = key
            if extra_keys:
                # This id itself touches more than one slot -- a further-
                # derived override referencing it by `overrides` must
                # propagate to all of them.
                ctx.vtable_slot_extra_roots[mid] = list(extra_keys)
        slots[key] = (idx, mangled_name)
        for extra_key in extra_keys:
            prev_idx, _ = slots.get(extra_key, (None, ""))
            slots[extra_key] = (prev_idx, mangled_name)

    return slots


def inherited_vtable_slots(
    ctx: CastxmlParserContext, class_el: Any, seen: set[str]
) -> dict[int | str, tuple[int | None, str]]:
    """Every base class's slots, in base-declaration order."""
    slots: dict[int | str, tuple[int | None, str]] = {}
    for base in class_el:
        if base.tag != "Base":
            continue
        base_type_el = ctx.resolve(base.get("type", ""))
        if base_type_el is not None:
            slots.update(collect_virtual_methods(ctx, base_type_el.get("id", ""), seen))
    return slots


def resolved_override_keys(
    ctx: CastxmlParserContext, overrides_id: str
) -> list[int | str]:
    """Every existing slot key the ``overrides`` attribute resolves to.

    castxml can list more than one overridden declaration as a
    whitespace-separated id list when a single override simultaneously
    covers more than one base-class branch (e.g. non-virtual multiple
    inheritance -- ``Derived : Base1, Base2`` -- where one final overrider
    satisfies both ``Base1::foo()`` and ``Base2::foo()``). Each resolved id
    is a genuinely distinct position in the object's real vtable-group
    layout (typically an adjusting thunk for all but one), and an exact
    lookup of the raw composite string never matches ``ctx.vtable_slot_root``,
    so every id is resolved separately.

    A resolved id can itself carry extra roots from an earlier multi-slot
    override (a further-derived override referencing an intermediate
    override's id by ``overrides`` must propagate to every slot that
    intermediate one touched, not just its primary), so both
    ``ctx.vtable_slot_root`` and ``ctx.vtable_slot_extra_roots`` are
    consulted per id.
    """
    resolved: list[int | str] = []
    for oid in overrides_id.split():
        candidates: list[int | str] = []
        primary = ctx.vtable_slot_root.get(oid)
        if primary is not None:
            candidates.append(primary)
        candidates.extend(ctx.vtable_slot_extra_roots.get(oid, ()))
        for candidate in candidates:
            if candidate not in resolved:
                resolved.append(candidate)
    return resolved


def vtable_slot_key(
    ctx: CastxmlParserContext, method_el: Any, mid: str, mangled_name: str
) -> tuple[int | str, list[int | str], int | None]:
    """``(key, extra_keys, vtable_index)`` for one virtual method declaration.

    An override always reuses whatever slot its base declaration landed
    under -- checked BEFORE falling back to this declaration's own
    ``vtable_index``. Preferring a fresh index would miss the reverse mixed-
    index direction: a base that lacks ``vtable_index`` (so its slot is
    keyed by its own string id) but is overridden by a declaration that DOES
    carry an index would otherwise open a new int-keyed slot instead of
    collapsing onto the base's string-keyed one.

    The first resolved slot becomes this entry's own key; every OTHER
    resolved slot keeps its own key and prior sort position (*extra_keys*)
    with only its content updated to this override, rather than collapsing
    them into one entry -- which would under-report the vtable's true size
    -- or leaving them with stale pre-override content.
    """
    idx = _parse_vtable_index(method_el.get("vtable_index"))
    overrides_id = method_el.get("overrides")
    if not overrides_id:
        return (idx if idx is not None else (mid or mangled_name)), [], idx

    resolved_keys = resolved_override_keys(ctx, overrides_id)
    if resolved_keys:
        key: int | str = resolved_keys[0]
        extra_keys = resolved_keys[1:]
    else:
        key, extra_keys = overrides_id, []
    if isinstance(key, int):
        # Consistently-indexed lineage: adopt the resolved index for
        # sorting when this declaration has none of its own, so
        # build_vtable's final _vt_sort_key sort places it at the
        # inherited position instead of the unindexed tail (which would
        # silently reorder it past any indexed sibling slot declared after
        # this one, an apparent "vtable reordered" that never happened).
        if idx is None:
            idx = key
    else:
        # Unindexed lineage (key is a string): a fresh vtable_index on THIS
        # declaration has no verified relationship to sibling unindexed
        # slots' true positions (e.g. Base has unindexed foo then bar;
        # Derived overrides bar with its own vtable_index="1" -- that "1"
        # doesn't mean "after foo", it's not comparable to foo's unknown
        # position at all), so it must not be trusted for cross-slot
        # ordering. Discard it and let _vt_sort_key treat this slot as
        # unindexed, preserving its original discovery-order position.
        idx = None
    return key, extra_keys, idx
