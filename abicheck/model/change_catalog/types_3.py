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
"""ADR-093 WS-1: entries of ``types_1.py`` split out at the 800-line cap.

Whole entries moved unchanged from ``types_1.py`` (each gains only its
``observable_at``). Registered in ``types.py``.
"""

from __future__ import annotations

from .observability import ObservabilityCondition, Platform, observable
from .registry import (
    ChangeEntity,
    ChangeKindMeta,
    ChangeOperation,
    ContractSubject,
    Verdict,
)

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation
_CS = ContractSubject

_OBS_B_ELF = observable(
    ("B",), condition=ObservabilityCondition(platform=frozenset({Platform.ELF}))
)
_OBS_H = observable(
    ("B", "H"), rationale="declaration-only fact: visible in the public header"
)
_OBS_DH = observable(
    ("B", "D"),
    ("B", "H"),
    rationale="layout or type fact: DWARF and the public header both carry it",
)

TYPES_ENTRIES_3: list[ChangeKindMeta] = [
    _E(
        "atomic_qualifier_changed",
        _B,
        impact="The `_Atomic` qualifier was added to or removed from a public "
        "field/param/return type. Per WG14 the size and alignment of an "
        "_Atomic-qualified type may differ from the unqualified type and "
        "varies across compilers, so layout and calling convention "
        "diverge and old code is miscompiled.",
        description_template="_Atomic {detail} on {name}: {old} → {new}. _Atomic size/alignment may differ from the unqualified type and varies across compilers.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        entity_from_field="entity_discriminator",
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "base_class_offset_changed",
        _B,
        impact="A base-class subobject moved to a different offset within the derived "
        "object (e.g. an empty-base optimization was lost, or a member/base was "
        "inserted ahead of it) without the base list reordering. The `this` "
        "pointer adjustment for that base and every field after it shifts; old "
        "binaries read the wrong addresses.",
        description_template="Base class '{detail}' moved within '{name}' ({old} → {new} bits). The `this`-pointer adjustment for that base and the offset of every field after it shift; existing binaries read the wrong addresses.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "base_class_position_changed",
        _B,
        impact="Base classes were reordered (same base set, different "
        "order); this detector reports the reorder from the base-name "
        "list alone and never inspects the bases' actual offsets, so "
        "it can't distinguish a real subobject-offset shift from a "
        "supported layout-neutral case — e.g. reordering two empty "
        "bases under the empty-base optimization, which can leave "
        "every offset unchanged. Where the reorder does shift "
        "offsets, the this-pointer adjustment needed to reach a "
        "given base changes, so a caller compiled against the old "
        "layout accesses inherited members and virtual dispatch "
        "through the wrong offset.",
        description_template="Base class order reordered: {name} — this-pointer adjustments changed",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "base_class_virtual_changed",
        _B,
        impact="A base class's virtual-inheritance mode changed (virtual ↔ "
        "non-virtual); this changes how the base subobject is "
        "located (a fixed offset for non-virtual inheritance vs. a "
        "vtable/vbase-offset lookup for virtual inheritance), so old "
        "code compiled against the previous scheme locates the base "
        "subobject incorrectly.",
        description_template="Base class virtual inheritance changed: {name} — {detail}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "default_template_arg_changed",
        _B,
        impact="A default template argument changed (e.g. `Distance = "
        "minkowski_distance<Float>` → `Distance = euclidean_distance<Float>`). "
        "Consumer source compiles unchanged but the substituted instantiation "
        "type differs, producing a different mangled symbol. The library "
        "ships only one instantiation; consumers built against the old "
        "default reference a symbol that no longer exists. Unlike function "
        "default parameter changes (NO_CHANGE), template default arguments "
        "ARE part of the substituted type and affect mangling.",
        description_template="Template instantiation '{name}' substitutes to different arguments than its surviving sibling '{detail}'. This is consistent with a change to a default template argument in the declaring header: consumer source compiles unchanged, but the substituted mangled symbol differs. Consumers built against the old default get unresolved symbols.",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "enum_became_scoped",
        _A,
        impact="A plain enum became a scoped `enum class`/`enum struct`. "
        "Unqualified enumerator lookup (`Red` instead of `Color::Red`) "
        "and implicit conversion to the underlying integer type both "
        "stop compiling — a source break, not a binary one (the "
        "underlying representation is unchanged). (An explicit cast "
        "from an integer to the enum was never implicit either way —  "
        "only the enum-to-integer direction changes here.)",
        description_template="Enum became scoped: {name} — unqualified enumerator lookup and implicit int conversion no longer compile",
        entity=_ENT.ENUM,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "enum_deprecated_added",
        _C,
        impact="Enum type gained [[deprecated]]; consumers get a compiler "
        "warning when naming it. This detector only checks the "
        "deprecated flag itself — it doesn't verify the enum's "
        "underlying type, size, or enumerator values also stayed the "
        "same, so a companion finding for any of those is possible "
        "if the revision changed both at once. A consumer building "
        "with warnings as errors (e.g. -Werror=deprecated-declarations) "
        "has this turn a previously clean build into a failing one.",
        description_template="Enum marked deprecated: {name} ({detail})",
        entity=_ENT.ENUM,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "enum_deprecated_removed",
        _C,
        impact="Enum type's [[deprecated]] marker was removed; the "
        "compiler warning stops, with no effect on the enum's ABI.",
        description_template="Enum no longer marked deprecated: {name}",
        entity=_ENT.ENUM,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "enum_last_member_value_changed",
        _R,
        impact="Sentinel/MAX value changed; old code using it for array sizes allocates wrong amount.",
        description_template="Enum member value changed: {name}::{detail}",
        entity=_ENT.ENUM,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "enum_lost_scoped",
        _R,
        impact="A scoped `enum class`/`enum struct` became a plain enum. "
        "Existing qualified-name source (`Color::Red`) still compiles, "
        "but implicit conversion to the underlying integer type "
        "silently reappears — code that relied on the scoped enum's "
        "type safety to reject stray integer comparisons/arithmetic no "
        "longer gets that protection, a silent behavior change to "
        "review rather than a hard break.",
        description_template="Enum lost scoped status: {name} — implicit int conversion silently reappears",
        entity=_ENT.ENUM,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "enum_member_added",
        _C,
        is_addition=True,
        impact="New enumerator may shift subsequent values in non-fixed enums; switch defaults may miss the new case.",
        description_template="Enum member added: {name}::{detail}",
        entity=_ENT.ENUM,
        contract_subject=_CS.DECLARATION,
        operation=_OP.ADDED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "enum_member_removed",
        _B,
        impact="Old code uses a constant that no longer exists; compile error for source, stale value for binaries.",
        description_template="Enum member removed: {name}::{detail}",
        entity=_ENT.ENUM,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "enum_member_renamed",
        _A,
        impact="Enumerator name changed but value is the same; source code using old name won't compile.",
        policy_overrides={"sdk_vendor": _C},
        description_template="Enum member renamed: {name}::{old} → {new} (value={detail})",
        entity=_ENT.ENUM,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "enum_member_value_changed",
        _B,
        impact="Old binaries use stale numeric values; logic comparisons and switch statements silently break.",
        description_template="Enum member value changed: {name}::{detail}",
        entity=_ENT.ENUM,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "experimental_graduated",
        _C,
        is_addition=True,
        impact="A declaration that previously lived under an `experimental::` "
        "(or similar) namespace is now also available at a stable name "
        "in the same library, while the experimental alias is retained. "
        "Compatible: existing consumers keep compiling; new consumers "
        "are encouraged to migrate to the stable name.",
        description_template="Experimental {detail} '{old}' graduated to stable name '{new}'; experimental alias retained.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        entity_from_field="entity_discriminator",
        operation=_OP.ADDED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "experimental_removed_without_replacement",
        _A,
        impact="A declaration that previously lived under an `experimental::` "
        "(or similar) namespace was removed and no declaration with "
        "the same leaf name appears under a stable namespace in the "
        "new headers. Consumers that depended on the experimental name "
        "no longer compile. The mangled name change is the same as a "
        "func_removed/type_removed for an instantiated template, but "
        "the experimental graduation pattern is named explicitly so "
        "users see whether a replacement was published.",
        description_template="Experimental {detail} '{old}' was removed and no {detail} with leaf '{name}' was published at a stable namespace in the new headers.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        entity_from_field="entity_discriminator",
        operation=_OP.REMOVED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "field_became_const",
        _A,
        impact="A struct/class field gained const. This detector matches "
        "fields by name and only compares the const flag — it "
        "doesn't check whether the same-named field's type or offset "
        "also changed, so a companion finding for either is possible "
        "and takes precedence. When only the qualifier changed, the "
        "field's offset and size are unaffected, but source code "
        "that wrote to it directly (not through a cast) no longer "
        "compiles.",
        description_template="Field became const: {name}::{detail}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "field_became_mutable",
        _C,
        impact="A field gained mutable, letting it be modified even "
        "through a const object/method. This detector matches "
        "fields by name and only compares the mutable flag — it "
        "doesn't check whether the same-named field's type or offset "
        "also changed, so a companion finding for either is possible "
        "and takes precedence. When only the qualifier changed, "
        "layout is unaffected, but this weakens the const-correctness "
        "guarantee callers holding a const reference could previously "
        "rely on.",
        description_template="Field became mutable: {name}::{detail}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "field_became_volatile",
        _R,
        impact="A field gained volatile. This detector matches fields by "
        "name and only compares the volatile flag — it doesn't check "
        "whether the same-named field's type or offset also changed, "
        "so a companion finding for either is possible and takes "
        "precedence. When only the qualifier changed, offset and "
        "size are unaffected, but the compiler now treats every "
        "access as observable and suppresses caching/reordering "
        "around it — code recompiled against the new declaration may "
        "see more memory traffic where it previously assumed the "
        "compiler could cache a read.",
        description_template="Field became volatile: {name}::{detail}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "field_bitfield_changed",
        _B,
        impact="Bit-field width or offset changed; old code reads/writes wrong bits.",
        description_template="Bitfield layout changed: {name}::{detail}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "field_default_initializer_changed",
        _C,
        impact="A field's default member initializer value changed. Existing "
        "source still compiles; objects default-constructed against the "
        "new header silently pick up the new value.",
        description_template="Field default initializer changed: {name}::{detail} ({old} → {new})",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "field_default_initializer_removed",
        _R,
        impact="A field's default member initializer was removed. A "
        "default-initialized object (or a member otherwise left "
        "unset by a defaulted/user constructor that never touches "
        "it) now leaves the member with indeterminate value instead "
        "of the old default — a silent correctness risk, not a "
        "compile break. (Aggregate initialization with the member "
        "omitted is unaffected: an omitted aggregate element is "
        "still copy-initialized from an empty initializer list, not "
        "left indeterminate.)",
        description_template="Field lost its default initializer: {name}::{detail}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "field_deprecated_added",
        _C,
        impact="A field gained [[deprecated]]; consumers get a compiler "
        "warning when accessing it. This detector matches fields by "
        "name and only checks the deprecated flag — it doesn't "
        "verify the field's offset or type are also unchanged, so a "
        "companion finding for either is possible and takes "
        "precedence over this one's stability claim. A consumer "
        "building with warnings as errors "
        "(e.g. -Werror=deprecated-declarations) has this turn a "
        "previously clean build into a failing one.",
        description_template="Field marked deprecated: {name}::{detail} ({new})",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "field_deprecated_removed",
        _C,
        impact="A field's [[deprecated]] marker was removed; the compiler "
        "warning stops, with no effect on the field's layout.",
        description_template="Field no longer marked deprecated: {name}::{detail}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "field_lost_const",
        _C,
        impact="A field lost const. This detector matches fields by name "
        "and only compares the const flag — it doesn't check "
        "whether the same-named field's type or offset also "
        "changed, so a companion finding for either is possible and "
        "takes precedence. When only the qualifier changed, offset "
        "and size are unaffected, but code that relied on the "
        "compiler enforcing (or optimizing based on) read-only "
        "access to it no longer gets that guarantee.",
        description_template="Field lost const: {name}::{detail}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "field_lost_mutable",
        _A,
        impact="A field lost mutable. This detector matches fields by name "
        "and only compares the mutable flag — it doesn't check "
        "whether the same-named field's type or offset also "
        "changed, so a companion finding for either is possible and "
        "takes precedence. When only the qualifier changed, it can "
        "no longer be modified through a const object/method — "
        "source code doing so via a const-qualified path no longer "
        "compiles.",
        description_template="Field lost mutable: {name}::{detail}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "field_lost_volatile",
        _R,
        impact="A field lost volatile. This detector matches fields by "
        "name and only compares the volatile flag — it doesn't "
        "check whether the same-named field's type or offset also "
        "changed, so a companion finding for either is possible and "
        "takes precedence. When only the qualifier changed, offset "
        "and size are unaffected, but a recompiled consumer may now "
        "have accesses to it cached or reordered by the compiler — "
        "code relying on every access reaching memory (e.g. "
        "memory-mapped hardware state) can break once recompiled "
        "against the new declaration.",
        description_template="Field lost volatile: {name}::{detail}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "field_renamed",
        _A,
        impact="Field name changed but offset is the same; source code using old name won't compile.",
        policy_overrides={"sdk_vendor": _C},
        description_template="Field renamed: {name}::{old} → {new}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "flexible_array_member_changed",
        _C,
        impact="Flexible array member (FAM) at end of struct changed: last field with "
        "zero/unknown array size was added, removed, or changed type. The struct "
        "binary layout is unchanged (FAM has zero static size), but runtime "
        "allocation patterns may differ.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "func_cv_changed",
        _B,
        impact="const/volatile on 'this' changes the mangled name; old binaries link to the wrong symbol.",
        description_template="CV qualifier changed: {name}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "func_pure_virtual_added",
        _B,
        impact="Old subclasses don't implement the pure virtual; instantiation causes linker error or UB.",
        description_template="Function became pure virtual: {name}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "func_ref_qual_changed",
        _B,
        impact="Ref-qualifier (&/&&) on a member function changed; this alters the "
        "Itanium C++ ABI mangled name and overload resolution, so old binaries "
        "link to the wrong symbol or fail to resolve it.",
        description_template="Ref-qualifier changed: {name} ({old} → {new})",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "func_static_changed",
        _B,
        impact="Static/non-static transition changes calling convention (implicit this pointer); ABI mismatch.",
        description_template="Static qualifier changed: {name}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "inline_body_references_renamed_member",
        _B,
        impact="An inline public accessor (header-emitted into every consumer "
        "binary) reaches into a pimpl/detail member by name. That member "
        "was renamed in the implementation type, and although the inline "
        "accessor's body was updated in lockstep in the new header, "
        "consumers compiled against the OLD header have the old field "
        "name baked into their binary. At runtime, the inline body "
        "accesses a field at the wrong offset (or by a name that no "
        "longer exists), producing silent wrong data or crashes.",
        description_template="Public class '{name}' has inline accessors {detail} by name. Field '{old}' was renamed to '{new}' in the new internal layout. Consumers compiled against the old header have the old member name baked into their inline accessor bodies; running against the new library reads the wrong offset or fails to resolve the member.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "inline_namespace_version_bumped",
        _B,
        impact="A header-declared symbol or type lives under a versioned "
        "inline namespace (e.g. `inline namespace _V1`) and the "
        "version segment shifted (`_V1` → `_V2`). Declarations look "
        "identical to consumers but every newly compiled TU produces "
        "a different mangled symbol; old TUs in the same program ODR-"
        "violate against new TUs. Specialisation of inline_namespace_"
        "moved that fires from declared-name evidence (works even "
        "when the library ships no .so).",
        description_template="Inline namespace version bumped: '{old}' → '{new}' (version segment changed from {detail}); mangled names change so old and new TUs of the same program ODR-violate.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        entity_from_field="entity_discriminator",
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "mandatory_template_param_added",
        _A,
        impact="A function or class template parameter that was defaulted "
        "(or deduced) became mandatory. Consumer source that wrote "
        "`Foo<int>` without supplying the new parameter no longer "
        "compiles. Mangled symbols also change because the "
        "instantiation tuple differs.",
        description_template="Template '{name}' minimum effective argument count grew from {old} to {new}. Consumers that wrote '{name}<...{old} args...>' without supplying the new parameter no longer compile.",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        entity_from_field="entity_discriminator",
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "polymorphic_type_non_virtual_dtor",
        _R,
        impact="A type with virtual methods (it has a vtable) is used as a factory "
        "return or base class but declares no virtual destructor. Deleting "
        "a derived object through a base pointer is undefined behaviour: the "
        "derived destructor never runs and the wrong amount of memory may be "
        "freed. Declare the base destructor `virtual`.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "removed_const_overload",
        _A,
        impact="Const overload removed; source code calling const version breaks.",
        policy_overrides={"sdk_vendor": _C},
        description_template="Const method overload removed: {name} (non-const version still exists)",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "rtti_inheritance_changed",
        _B,
        impact="A polymorphic class's RTTI typeinfo (`_ZTI`) object changed size, which in "
        "the Itanium C++ ABI means its base-class shape changed: no-base "
        "(`__class_type_info`, 2 words) ↔ single-base (`__si_class_type_info`, "
        "3 words) ↔ multiple/virtual-base (`__vmi_class_type_info`, larger), or the "
        "number of bases differs. Base-class changes shift `this`-pointer "
        "adjustments, member offsets, and the vtable, so derived classes and "
        "by-value users are miscompiled. Recovered from the ELF symbol size without "
        "DWARF — the binary-only analogue of TYPE_BASE_CHANGED.",
        description_template="RTTI typeinfo for '{name}' changed size: {old} → {new} bytes ({detail}). The base-class shape changed, which shifts this-pointer adjustments, member offsets, and the vtable. Detected from the ELF symbol size without debug info.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "source_level_kind_changed",
        _C,
        impact="An aggregate's declared struct/class class-key changed; "
        "`_diff_type_kind_changes()` routes any union-involving transition "
        "to `type_kind_changed`, so this kind is only struct↔class. The "
        "class-key alone changes neither layout nor Itanium mangling, and "
        "source naming the type with the other key still compiles (a "
        "mismatched key is at most a warning). Its consequences are "
        "reported by their own kinds: default member/base access changes "
        "as `field_access_changed`, `method_access_changed` and "
        "`base_class_access_narrowed`; MSVC's class-key mangling (U vs V) "
        "as the PE export removal/addition of every affected function.",
        description_template="Aggregate kind changed: {name} ({old} → {new})",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "standard_layout_lost",
        _R,
        impact="A type stopped being standard-layout (e.g. it gained a mix of access "
        "specifiers, a base with members, or virtual members). `offsetof` and "
        "C interoperability are no longer guaranteed and tail-padding reuse "
        "rules change; review code that relies on the C-compatible layout.",
        description_template="'{name}' is no longer standard-layout. `offsetof` and C interoperability are no longer guaranteed and tail-padding reuse rules change; review code relying on the C-compatible layout.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
]
