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


"""Ordinal part 2 of ``types.py``'s entry list -- not a separate owner.

``types.py`` remains the taxonomy and the single public name
(``TYPES_ENTRIES``); see its docstring for this taxonomy's scope, its
boundary against the other four, and the methodology the entries were
categorized by. This file holds a contiguous slice of that one list and
claims no responsibility of its own, so nothing should import it directly.

The split is by declaration-order line position, not by concern, purely so
each file stays under ADR-061's 800-line ceiling -- the same reason and the
same shape as ``kind_names_{1,2,3}.py``, whose own docstring records that an
ordinal split is the right tool when the content is a data table rather than
behavior. Partitioning *this* list by a named sub-concern would be a
different change: it would move the D9 ownership boundary that
``symbols``/``types``/``platform``/``build``/``source`` already draws, and
the "Adding a new ChangeKind" procedure names those five modules by name.
"""

from __future__ import annotations

from .registry import ChangeEntity, ChangeKindMeta, ChangeOperation, Verdict

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation

TYPES_ENTRIES_2: list[ChangeKindMeta] = [
    _E(
        "sycl_overload_set_removed",
        _B,
        impact="A family of public overloads that take a SYCL queue as the first "
        "parameter was removed in bulk (typical when DPC++ support is "
        "disabled at build time). Reported as one grouped finding rather "
        "than N independent func_removed entries to make the deployment-"
        "level event ('the GPU/SYCL overload family was withdrawn') "
        "visible at a glance.",
        description_template="SYCL overload family withdrawn: {detail}. This is the deployment-level event 'DPC++ build disabled' rather than independent API removals — consumers built against the SYCL surface need a DPC++-enabled rebuild.",
        entity=_ENT.FUNCTION,
        operation=_OP.REMOVED,
    ),
    _E(
        "tag_type_renamed",
        _B,
        impact="An empty tag struct (zero fields, no methods) used solely for "
        "template specialization was renamed. Layout-based detectors see no "
        "change because the type has no layout, but every explicit "
        "instantiation that referenced the old tag is re-mangled and the "
        "old symbol disappears. Consumers built against the old header get "
        "unresolved-symbol errors at load time. Common with "
        "method::* / task::* tag families.",
        description_template="Empty tag struct '{old}' renamed to '{new}'. The type has no fields or vtable, so layout-based detectors see no change, but {detail}. Consumers built against the old header fail to resolve the instantiation at load time.",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "tail_padding_reuse_changed",
        _R,
        impact="The type's data size (the bytes its own members occupy, excluding "
        "trailing tail padding) changed while sizeof stayed the same. A derived "
        "class may reuse a base's tail padding, so this can silently shift a "
        "derived layout even though the base's sizeof is unchanged.",
        description_template="'{name}' data size changed ({old} → {new} bits) while sizeof stayed {detail} bits. A derived class may reuse this type's tail padding, so a derived layout can shift even though sizeof is unchanged.",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "template_param_type_changed",
        _B,
        impact="A template's own parameter's inner type argument changed "
        "(e.g. a member of the type used to instantiate the "
        "template). The detector compares only the parsed argument "
        "text under a matching outer template name, not whether the "
        "two instantiations actually differ in layout or interface "
        "— two same-outer-name specializations that differ only in "
        "a non-type argument (e.g. Tag<1> vs Tag<2>, both empty) "
        "can share the same layout. Where the instantiation's "
        "actual representation does differ, a caller/consumer using "
        "the old instantiation's ABI is no longer compatible with "
        "the new one.",
        description_template="Template parameter inner type changed: {name} param {detail} ({old} → {new})",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "template_return_type_changed",
        _B,
        impact="A template's own return type's inner type argument "
        "changed. The detector compares only the parsed argument "
        "text under a matching outer template name, not whether the "
        "two instantiations actually differ in layout or return "
        "convention — two same-outer-name specializations that "
        "differ only in a non-type argument (e.g. Tag<1> vs Tag<2>, "
        "both empty) can share the same layout. Where the "
        "instantiation's actual representation does differ, a "
        "caller compiled against the old return type reads the "
        "result incorrectly.",
        description_template="Template return type inner argument changed: {name} ({old} → {new})",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "trivially_copyable_lost",
        _B,
        impact="A type stopped being trivially copyable (e.g. a user-declared "
        "copy/move constructor, destructor, or a non-trivial member was added). "
        "Non-trivially-copyable types are passed and returned by value "
        "differently (via a hidden reference / not in registers), so the calling "
        "convention for any function taking or returning it by value changes.",
        description_template="'{name}' is no longer trivially copyable. It is now passed and returned by value differently (via a hidden reference / not in registers), so the calling convention of any function taking or returning it by value changes.",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_added",
        _C,
        is_addition=True,
        impact="New type available; existing binaries are unaffected.",
        description_template="New type: {name}",
        entity=_ENT.TYPE,
        operation=_OP.ADDED,
    ),
    _E(
        "type_alignment_changed",
        _B,
        impact="Misaligned access can cause bus errors on strict architectures or silent data corruption with SIMD.",
        description_template="Alignment changed: {name} ({old} → {new} bits)",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_base_changed",
        _B,
        impact="Base class layout change shifts derived member offsets and vtable pointers; this-pointer arithmetic breaks.",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_became_abstract",
        _A,
        impact="A class/struct gained a pure virtual function (directly or via "
        "an inherited one newly left unimplemented), making it abstract. "
        "Source that directly instantiates the type (`Foo obj;`, "
        "`new Foo()`) no longer compiles. Not recorded in DWARF/the "
        "binary, so detected only in header (castxml) mode.",
        description_template="Class became abstract: {name} — direct instantiation no longer compiles",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_became_final",
        _A,
        impact="A class/struct gained the `final` specifier. Any consumer that "
        "derives from it (`class D : public C`) no longer compiles. The "
        "type layout and mangled names are unchanged so already-built "
        "binaries keep running, but recompilation against the new header "
        "fails — a source/API break. Invisible to binary analysis: "
        "`final` is not recorded in DWARF or the object file, so this is "
        "detected only in header (castxml) mode.",
        description_template="Class gained `final` specifier: {name} — consumers that derive from it no longer compile",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_became_opaque",
        _B,
        impact="Type became forward-declaration only; old code using sizeof or accessing fields fails.",
        description_template="Type became opaque (forward-declaration only): {name} — stack allocation no longer possible",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_deprecated_added",
        _C,
        impact="Type gained [[deprecated]]; consumers get a compiler "
        "warning when naming it. This detector matches types by "
        "identity and only checks the deprecated flag — it doesn't "
        "verify the matched pair's layout or ABI are also "
        "unchanged, so a companion finding for either is possible. "
        "A consumer building with warnings as errors "
        "(e.g. -Werror=deprecated-declarations) has this turn a "
        "previously clean build into a failing one.",
        description_template="Type marked deprecated: {name} ({detail})",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_deprecated_removed",
        _C,
        impact="Type's [[deprecated]] marker was removed; the compiler "
        "warning stops, with no effect on the type's ABI.",
        description_template="Type no longer marked deprecated: {name}",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_field_added",
        _B,
        impact="New field shifts subsequent fields; old code reads wrong offsets for all fields after insertion point.",
        description_template="Field added: {name}::{detail}",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_field_added_compatible",
        _C,
        is_addition=True,
        impact="Field appended without changing existing offsets; old code works but won't initialize the new field.",
        description_template="Field added: {name}::{detail}",
        entity=_ENT.TYPE,
        operation=_OP.ADDED,
    ),
    _E(
        "type_field_offset_changed",
        _B,
        impact="Old code reads/writes fields at stale offsets; silent data corruption.",
        description_template="Field offset changed: {name}::{detail} ({old} → {new} bits)",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_field_removed",
        _B,
        impact="Old code accesses a field that no longer exists at the expected offset; reads garbage or writes out of bounds.",
        description_template="Field removed: {name}::{detail}",
        entity=_ENT.TYPE,
        operation=_OP.REMOVED,
    ),
    _E(
        "type_field_type_changed",
        _B,
        impact="Field has different size or representation; old code misinterprets the data.",
        description_template="Field type changed: {name}::{detail}",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_kind_changed",
        _B,
        impact="An aggregate's declared kind changed (e.g. struct/class ↔ "
        "union); this detector fires whenever a union is involved on "
        "either side, without checking whether the transition "
        "actually moved any member's effective location. Ordinarily "
        "a struct/class↔union change with two or more members "
        "genuinely reinterprets overlapping vs. non-overlapping "
        "member storage — code compiled against the old kind "
        "reads/writes members at the wrong effective location. But "
        "member *count* alone doesn't settle it: a single-member "
        "aggregate is the simplest layout-neutral case (that one "
        "member sits at offset 0 either way), and a multi-member "
        "one can be layout-neutral too when every member is empty "
        "and `[[no_unique_address]]` (C++20) — such members can all "
        "occupy offset 0 with the same aggregate size in either "
        "representation. The risk is specifically whether any "
        "member's own effective offset/size actually differs "
        "between the two layouts, not member count by itself.",
        description_template="Aggregate kind changed: {name} ({old} → {new})",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_lost_abstract",
        _C,
        impact="A class/struct is no longer abstract. This detector "
        "(`_append_type_abstract_changes()`) only compares the "
        "`is_abstract` boolean, not why it flipped — the type "
        "becomes newly instantiable either way, which is purely "
        "additive for previously-valid source (never able to "
        "instantiate it directly). But a class can also lose "
        "abstract status because its last pure virtual was removed "
        "rather than given an implementation, not just the benign "
        "'every pure virtual now has an implementation' case — that "
        "removal is its own break (a companion function-removal "
        "finding is possible on the same method) with real "
        "consequences for callers and overriders, not covered by "
        "this kind's own newly-instantiable read.",
        description_template="Class lost abstract status: {name}",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_lost_final",
        _R,
        impact="A class/struct lost the `final` specifier. Deriving from it is "
        "now allowed and previously-valid source still compiles, so this "
        "is not a source break. The risk is on already-compiled consumers: "
        "code built while the class was `final` may have had its virtual "
        "calls *devirtualized*, and if a later version introduces a "
        "subclass that overrides, those old binaries keep dispatching "
        "statically to the wrong target. KDE's C++ binary-compatibility "
        "policy lists removing `final` as a change to avoid; surfaced as a "
        "deployment risk for review rather than a hard break.",
        description_template="Class lost `final` specifier: {name}",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_removed",
        _B,
        impact="Old code references a type that no longer exists; compilation or link failure.",
        entity=_ENT.TYPE,
        operation=_OP.REMOVED,
    ),
    _E(
        "type_size_changed",
        _B,
        impact="Old code allocates or copies the type with the old size; heap/stack corruption, out-of-bounds access.",
        description_template="Size changed: {name} ({old} → {new} bits)",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_visibility_changed",
        _B,
        impact="A type's visibility attribute changed, affecting whether "
        "its typeinfo/vtable symbols are exported from the shared "
        "library; a consumer relying on RTTI (dynamic_cast, typeid, "
        "exception matching) or virtual dispatch across the library "
        "boundary can fail to find the expected typeinfo/vtable once "
        "the visibility narrows.",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "type_vtable_changed",
        _B,
        impact="Vtable slot reordering; virtual dispatch calls wrong method.",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "typedef_base_changed",
        _B,
        impact="Underlying type changed; old code using the typedef operates on wrong representation.",
        description_template="Typedef base type changed: {name}",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "typedef_removed",
        _B,
        impact="Old code using the typedef name won't compile; binary impact depends on usage.",
        description_template="Typedef removed: {name}",
        entity=_ENT.TYPE,
        operation=_OP.REMOVED,
    ),
    _E(
        "typedef_version_sentinel",
        _C,
        impact="Typedef name encodes a version number (e.g. png_libpng_version_1_6_46) — "
        "this is a compile-time sentinel that changes every release by design; "
        "it is never exported as an ELF symbol and does not affect binary ABI.",
        description_template="Version-stamped typedef removed (compile-time sentinel, not an ABI break): {name}",
        entity=_ENT.TYPE,
        operation=_OP.REMOVED,
    ),
    _E(
        "union_field_added",
        _C,
        is_addition=True,
        impact="Union size may grow; old code allocating with old sizeof gets truncated data.",
        description_template="Union field added: {name}::{detail}",
        entity=_ENT.TYPE,
        operation=_OP.ADDED,
    ),
    _E(
        "union_field_removed",
        _B,
        impact="Old code accessing removed alternative reads uninitialized memory.",
        description_template="Union field removed: {name}::{detail}",
        entity=_ENT.TYPE,
        operation=_OP.REMOVED,
    ),
    _E(
        "union_field_type_changed",
        _B,
        impact="Old code interprets the union member with wrong type layout.",
        description_template="Union field type changed: {name}::{detail}",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "unnamed_type_in_public_abi",
        _R,
        impact="An exported symbol embeds an unnamed type in its mangled name — a "
        "lambda closure (`Ul…E_`) or an unnamed struct/enum (`Ut…_`). The "
        "Itanium mangling of unnamed types is per-translation-unit and "
        "compiler-ordering dependent (recompiling, or merely reordering "
        "unrelated declarations, can renumber `{lambda#1}` → `{lambda#2}`), "
        "so exporting one is an ABI time bomb: a rebuilt consumer can fail to "
        "resolve the symbol. RISK / hygiene — reported when newly introduced.",
        description_template="Unnamed type leaks into the public ABI: {name} ({detail}) — its mangled name is compiler-ordering-fragile",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "unspecified_return_now_named",
        _A,
        impact="A factory function's return type changed between an "
        "unspecified placeholder (`auto`, lambda type, anonymous "
        "class) and a named type — or vice versa. Source that "
        "stored the result with the deduced spelling (`auto x = "
        "make_X();`) keeps compiling; source that wrote out the "
        "type fails to compile.",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "used_reserved_field",
        _C,
        impact="A previously-reserved/padding field was put into real use; "
        "since the space was already part of the struct's layout, "
        "the struct's overall size is usually unaffected. This "
        "detector only checks the rename's type/offset, not "
        "whether existing callers actually zero-initialize that "
        "space, so the risk runs both directions: a consumer "
        "reading a struct the new library populated may see "
        "meaningful, non-zero data where it expected unused "
        "padding, and — the direction this detector cannot rule "
        "out — an old caller that constructs the struct without "
        "explicitly initializing the (formerly reserved) field and "
        "passes it to the new library can hand the new callee "
        "indeterminate bytes that it now interprets as real data.",
        description_template="Reserved field put into use: {name}::{old} → {new}",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "var_value_changed",
        _C,
        impact="A global variable's initial/static value changed; this "
        "detector only compares the value itself, not the "
        "variable's type or size, so this fires purely on the "
        "observed value difference. If a companion finding also "
        "reports the type or size changed, treat that as the more "
        "significant signal — otherwise this is a behavior change, "
        "not an ABI break, and old binaries that inlined the old "
        "value via constant propagation keep using it until "
        "recompiled.",
        description_template="Global data value changed: {name} ({old} → {new})",
        entity=_ENT.VARIABLE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "virtual_base_offset_changed",
        _B,
        impact="A class's virtual bases were reordered with the base set unchanged, "
        "so the virtual-base offset table (vbase offsets stored in the "
        "vtable) is laid out in a different order. The this-pointer "
        "adjustment used to reach a virtual base is baked into old binaries; "
        "after a reorder those adjustments point at the wrong subobject, "
        "corrupting access to virtual-base members with no symbol error. "
        "Detected from the DWARF virtual-inheritance order (L1); a pure "
        "virtual-base reorder is invisible to the non-virtual "
        "base_class_position_changed check.",
        description_template="Virtual base order changed for '{name}': {old} → {new} — vbase offset table reordered; old binaries mis-adjust `this` to virtual bases",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "virtual_method_added",
        _B,
        impact="A new virtual method was added to a class that already exists across "
        "versions. If the class had no virtuals it gains a hidden vtable pointer "
        "(its size and field offsets shift); if it was already polymorphic the new "
        "slot grows/relayouts the vtable. Either way derived classes compiled "
        "against the old layout dispatch through the wrong slots and old binaries "
        "embedding the type read the wrong offsets. This is the KDE "
        '"do not add virtuals to a non-leaf class" rule, caught even when the '
        "snapshot carries no diff-able vtable array (DWARF/symbol-only mode).",
        description_template="New virtual method added to existing class {detail}: {new} — grows/relayouts the vtable, breaking derived classes and old binaries",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "vptr_introduced",
        _B,
        impact="A previously non-polymorphic class gained its first virtual function, "
        "so the compiler prepends a vtable pointer. sizeof grows and every data "
        "member's offset shifts by a pointer width; existing binaries that embed "
        "or derive from the type are laid out incompatibly.",
        description_template="'{name}' gained a vtable pointer (became polymorphic). sizeof grows and every data member's offset shifts by a pointer width; binaries that embed or derive from the type are laid out incompatibly.",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "vtable_slot_count_changed",
        _B,
        impact="A polymorphic class's emitted vtable group changed size. The `_ZTV` object "
        "spans the primary table plus any vcall/vbase offsets and secondary tables, "
        "so the cause is either virtual functions net added/removed or a change in "
        "the inheritance shape — the symbol size alone cannot say which. Either way "
        "existing binaries dispatch through fixed vtable offsets, so they may call "
        "the wrong slot or run off the end of the table. Recovered from the ELF "
        "symbol size without DWARF — the binary-only analogue of FUNC_VIRTUAL_ADDED "
        "/ TYPE_VTABLE_CHANGED; identifying which slot moved needs DWARF or headers.",
        description_template="Vtable for '{name}' changed size: {old} → {new} bytes ({detail}). Virtual functions were net added or removed, or the inheritance shape changed — the symbol size cannot distinguish them; existing binaries dispatch through fixed vtable offsets and may call the wrong slot. Detected from the ELF symbol size without debug info.",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "vtable_thunk_offset_changed",
        _B,
        impact="A virtual-override thunk's this-pointer adjustment offset changed "
        "(e.g. `_ZThn8_` → `_ZThn16_` for the same target method). In the "
        "Itanium C++ ABI a thunk fixes up `this` when a call arrives through "
        "a secondary base's vtable, and the adjustment is baked into the "
        "vtables of every already-compiled consumer. A changed offset means "
        "a base subobject moved, so old binaries adjust `this` by the wrong "
        "amount and corrupt memory on virtual dispatch — with no symbol "
        "error. Recovered from the thunk symbol name alone (no DWARF), so it "
        "is caught even on stripped binaries where the primary-vtable _ZTV "
        "size is unchanged.",
        description_template="Vtable thunk offset changed for {name}: {old} → {new} — a base subobject moved; old binaries mis-adjust `this` on virtual dispatch",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "vtable_thunk_set_changed",
        _B,
        impact="A method that persists across versions gained or lost a "
        "virtual-override thunk. A thunk appears when a class overrides a "
        "virtual inherited through a *secondary* (multiple-inheritance) "
        "base; its appearance/disappearance means the override was added or "
        "removed in a secondary vtable. Because the inherited slot itself "
        "persists, the primary-vtable _ZTV size can be unchanged, so this is "
        "invisible to the slot-count diff. Old binaries dispatch to the "
        "wrong target through the secondary vtable.",
        description_template="Vtable thunk set changed for {name}: {detail} — a secondary-base override was added or removed",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "vtt_slot_count_changed",
        _B,
        impact="A class's VTT (virtual-table-table, `_ZTT`) object changed size. "
        "The VTT is the construction scaffolding the Itanium ABI uses to "
        "initialize the vtable pointers of virtual bases during "
        "construction/destruction; its size encodes the number of "
        "sub-vtables. A change means the virtual-inheritance shape changed, "
        "so a constructor compiled against the old VTT installs the wrong "
        "vptrs. Recovered from the `_ZTT` symbol size alone (no DWARF).",
        description_template="VTT size changed for '{name}': {old} → {new} bytes — virtual-base construction scaffolding changed",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
]
