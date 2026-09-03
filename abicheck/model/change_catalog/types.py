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


"""ADR-061 D9 taxonomy: type/layout-level ChangeKind entries.

Struct/class/union/enum/typedef declarations and everything about their
shape: fields, bases, vtables, layout (size/alignment/offset), kind
(struct vs. class vs. union), template parameters, and C++-specific
member-function qualifiers that are properties of the type's own
declaration (``const``/``ref``-qualified, ``static``, pure-virtual) rather
than of a free function's own linkage.

Categorized by which detector module actually produces each kind (verified
against the real ``ChangeKind.X`` construction sites in ``diff_types.py``
and its siblings -- ``diff_types_abicc_parity.py``,
``diff_types_field_facts.py``, ``diff_layout.py``, ``diff_elf_layout.py``,
``diff_vtable_layout.py``, ``diff_namespaces.py``, ``diff_stdlib_impl.py``,
``diff_cpp_patterns.py``, ``diff_templates.py``,
``diff_platform_templates.py`` -- not by which flat
``change_registry_*.py`` sibling an entry happened to live in for pure
line-count reasons before this migration.
"""

from __future__ import annotations

from .registry import ChangeKindMeta, Verdict

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

TYPES_ENTRIES: list[ChangeKindMeta] = [
    _E("atomic_qualifier_changed", _B,
       impact="The `_Atomic` qualifier was added to or removed from a public "
              "field/param/return type. Per WG14 the size and alignment of an "
              "_Atomic-qualified type may differ from the unqualified type and "
              "varies across compilers, so layout and calling convention "
              "diverge and old code is miscompiled.",
       description_template="_Atomic {detail} on {name}: {old} → {new}. _Atomic size/alignment may differ from the unqualified type and varies across compilers."),
    _E("base_class_offset_changed", _B,
       impact="A base-class subobject moved to a different offset within the derived "
              "object (e.g. an empty-base optimization was lost, or a member/base was "
              "inserted ahead of it) without the base list reordering. The `this` "
              "pointer adjustment for that base and every field after it shifts; old "
              "binaries read the wrong addresses.",
       description_template="Base class '{detail}' moved within '{name}' ({old} → {new} bits). The `this`-pointer adjustment for that base and the offset of every field after it shift; existing binaries read the wrong addresses."),
    _E("base_class_position_changed", _B,
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
       description_template="Base class order reordered: {name} — this-pointer adjustments changed"),
    _E("base_class_virtual_changed", _B,
       impact="A base class's virtual-inheritance mode changed (virtual ↔ "
              "non-virtual); this changes how the base subobject is "
              "located (a fixed offset for non-virtual inheritance vs. a "
              "vtable/vbase-offset lookup for virtual inheritance), so old "
              "code compiled against the previous scheme locates the base "
              "subobject incorrectly.",
       description_template="Base class virtual inheritance changed: {name} — {detail}"),
    _E("bundle_soname_skew", _B,
       impact="A co-versioned bundle of shared libraries (e.g. libfoo_core, "
              "libfoo_thread, libfoo_dpc) did not move SONAME in lockstep. "
              "Some siblings bumped the major SONAME, others did not. Distro "
              "packages built on this bundle have inconsistent dependency "
              "metadata; binaries dynamically loading the mixed cohort can fetch "
              "incompatible internal contracts and corrupt at the first cross-"
              "library call."),
    _E("cpo_kind_changed", _B,
       impact="A public customization point object (CPO) changed kind: "
              "what used to be a free function is now a function-object "
              "(variable of an unspecified class type), or vice versa. "
              "Call syntax (`lib::sort(args...)`) keeps working but "
              "`decltype(lib::sort)` is now a different type, breaking "
              "extern templates, trait specializations, and any code that "
              "took the CPO's address.",
       description_template="Public name '{name}' was a {old} in old and is a {new} in new. Call syntax preserved; decltype, extern templates, and trait specializations break."),
    _E("cpu_dispatch_isa_dropped", _R,
       impact="An entire CPU ISA tier (e.g. avx512) of dispatched specializations "
              "was removed. The runtime dispatcher continues to work for callers "
              "that did not pin a specific ISA, but consumers that linked directly "
              "against a now-removed ISA-specific symbol get unresolved symbols. "
              "Reported as one grouped finding listing the affected algorithm "
              "stems.",
       description_template="CPU dispatch ISA '{name}' tier removed: {detail}. Runtime dispatcher continues to work; consumers that pinned directly to '{name}' symbols get unresolved references at load time."),
    _E("default_template_arg_changed", _B,
       impact="A default template argument changed (e.g. `Distance = "
              "minkowski_distance<Float>` → `Distance = euclidean_distance<Float>`). "
              "Consumer source compiles unchanged but the substituted instantiation "
              "type differs, producing a different mangled symbol. The library "
              "ships only one instantiation; consumers built against the old "
              "default reference a symbol that no longer exists. Unlike function "
              "default parameter changes (NO_CHANGE), template default arguments "
              "ARE part of the substituted type and affect mangling.",
       description_template="Template instantiation '{name}' substitutes to different arguments than its surviving sibling '{detail}'. This is consistent with a change to a default template argument in the declaring header: consumer source compiles unchanged, but the substituted mangled symbol differs. Consumers built against the old default get unresolved symbols."),
    _E("enum_became_scoped", _A,
       impact="A plain enum became a scoped `enum class`/`enum struct`. "
              "Unqualified enumerator lookup (`Red` instead of `Color::Red`) "
              "and implicit conversion to the underlying integer type both "
              "stop compiling — a source break, not a binary one (the "
              "underlying representation is unchanged). (An explicit cast "
              "from an integer to the enum was never implicit either way —  "
              "only the enum-to-integer direction changes here.)",
       description_template="Enum became scoped: {name} — unqualified enumerator lookup and implicit int conversion no longer compile"),
    _E("enum_deprecated_added", _C,
       impact="Enum type gained [[deprecated]]; consumers get a compiler "
              "warning when naming it. This detector only checks the "
              "deprecated flag itself — it doesn't verify the enum's "
              "underlying type, size, or enumerator values also stayed the "
              "same, so a companion finding for any of those is possible "
              "if the revision changed both at once. A consumer building "
              "with warnings as errors (e.g. -Werror=deprecated-declarations) "
              "has this turn a previously clean build into a failing one.",
       description_template="Enum marked deprecated: {name} ({detail})"),
    _E("enum_deprecated_removed", _C,
       impact="Enum type's [[deprecated]] marker was removed; the "
              "compiler warning stops, with no effect on the enum's ABI.",
       description_template="Enum no longer marked deprecated: {name}"),
    _E("enum_last_member_value_changed", _R,
       impact="Sentinel/MAX value changed; old code using it for array sizes allocates wrong amount.",
       description_template="Enum member value changed: {name}::{detail}"),
    _E("enum_lost_scoped", _R,
       impact="A scoped `enum class`/`enum struct` became a plain enum. "
              "Existing qualified-name source (`Color::Red`) still compiles, "
              "but implicit conversion to the underlying integer type "
              "silently reappears — code that relied on the scoped enum's "
              "type safety to reject stray integer comparisons/arithmetic no "
              "longer gets that protection, a silent behavior change to "
              "review rather than a hard break.",
       description_template="Enum lost scoped status: {name} — implicit int conversion silently reappears"),
    _E("enum_member_added", _C, is_addition=True,
       impact="New enumerator may shift subsequent values in non-fixed enums; switch defaults may miss the new case.",
       description_template="Enum member added: {name}::{detail}"),
    _E("enum_member_removed", _B,
       impact="Old code uses a constant that no longer exists; compile error for source, stale value for binaries.",
       description_template="Enum member removed: {name}::{detail}"),
    _E("enum_member_renamed", _A,
       impact="Enumerator name changed but value is the same; source code using old name won't compile.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Enum member renamed: {name}::{old} → {new} (value={detail})"),
    _E("enum_member_value_changed", _B,
       impact="Old binaries use stale numeric values; logic comparisons and switch statements silently break.",
       description_template="Enum member value changed: {name}::{detail}"),
    _E("experimental_graduated", _C, is_addition=True,
       impact="A declaration that previously lived under an `experimental::` "
              "(or similar) namespace is now also available at a stable name "
              "in the same library, while the experimental alias is retained. "
              "Compatible: existing consumers keep compiling; new consumers "
              "are encouraged to migrate to the stable name.",
       description_template="Experimental {detail} '{old}' graduated to stable name '{new}'; experimental alias retained."),
    _E("experimental_removed_without_replacement", _A,
       impact="A declaration that previously lived under an `experimental::` "
              "(or similar) namespace was removed and no declaration with "
              "the same leaf name appears under a stable namespace in the "
              "new headers. Consumers that depended on the experimental name "
              "no longer compile. The mangled name change is the same as a "
              "func_removed/type_removed for an instantiated template, but "
              "the experimental graduation pattern is named explicitly so "
              "users see whether a replacement was published.",
       description_template="Experimental {detail} '{old}' was removed and no {detail} with leaf '{name}' was published at a stable namespace in the new headers."),
    _E("field_became_const", _C,
       impact="A struct/class field gained const. This detector matches "
              "fields by name and only compares the const flag — it "
              "doesn't check whether the same-named field's type or offset "
              "also changed, so a companion finding for either is possible "
              "and takes precedence. When only the qualifier changed, the "
              "field's offset and size are unaffected, but source code "
              "that wrote to it directly (not through a cast) no longer "
              "compiles.",
       description_template="Field became const: {name}::{detail}"),
    _E("field_became_mutable", _C,
       impact="A field gained mutable, letting it be modified even "
              "through a const object/method. This detector matches "
              "fields by name and only compares the mutable flag — it "
              "doesn't check whether the same-named field's type or offset "
              "also changed, so a companion finding for either is possible "
              "and takes precedence. When only the qualifier changed, "
              "layout is unaffected, but this weakens the const-correctness "
              "guarantee callers holding a const reference could previously "
              "rely on.",
       description_template="Field became mutable: {name}::{detail}"),
    _E("field_became_volatile", _C,
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
       description_template="Field became volatile: {name}::{detail}"),
    _E("field_bitfield_changed", _B,
       impact="Bit-field width or offset changed; old code reads/writes wrong bits.",
       description_template="Bitfield layout changed: {name}::{detail}"),
    _E("field_default_initializer_changed", _C,
       impact="A field's default member initializer value changed. Existing "
              "source still compiles; objects default-constructed against the "
              "new header silently pick up the new value.",
       description_template="Field default initializer changed: {name}::{detail} ({old} → {new})"),
    _E("field_default_initializer_removed", _R,
       impact="A field's default member initializer was removed. A "
              "default-initialized object (or a member otherwise left "
              "unset by a defaulted/user constructor that never touches "
              "it) now leaves the member with indeterminate value instead "
              "of the old default — a silent correctness risk, not a "
              "compile break. (Aggregate initialization with the member "
              "omitted is unaffected: an omitted aggregate element is "
              "still copy-initialized from an empty initializer list, not "
              "left indeterminate.)",
       description_template="Field lost its default initializer: {name}::{detail}"),
    _E("field_deprecated_added", _C,
       impact="A field gained [[deprecated]]; consumers get a compiler "
              "warning when accessing it. This detector matches fields by "
              "name and only checks the deprecated flag — it doesn't "
              "verify the field's offset or type are also unchanged, so a "
              "companion finding for either is possible and takes "
              "precedence over this one's stability claim. A consumer "
              "building with warnings as errors "
              "(e.g. -Werror=deprecated-declarations) has this turn a "
              "previously clean build into a failing one.",
       description_template="Field marked deprecated: {name}::{detail} ({new})"),
    _E("field_deprecated_removed", _C,
       impact="A field's [[deprecated]] marker was removed; the compiler "
              "warning stops, with no effect on the field's layout.",
       description_template="Field no longer marked deprecated: {name}::{detail}"),
    _E("field_lost_const", _C,
       impact="A field lost const. This detector matches fields by name "
              "and only compares the const flag — it doesn't check "
              "whether the same-named field's type or offset also "
              "changed, so a companion finding for either is possible and "
              "takes precedence. When only the qualifier changed, offset "
              "and size are unaffected, but code that relied on the "
              "compiler enforcing (or optimizing based on) read-only "
              "access to it no longer gets that guarantee.",
       description_template="Field lost const: {name}::{detail}"),
    _E("field_lost_mutable", _C,
       impact="A field lost mutable. This detector matches fields by name "
              "and only compares the mutable flag — it doesn't check "
              "whether the same-named field's type or offset also "
              "changed, so a companion finding for either is possible and "
              "takes precedence. When only the qualifier changed, it can "
              "no longer be modified through a const object/method — "
              "source code doing so via a const-qualified path no longer "
              "compiles.",
       description_template="Field lost mutable: {name}::{detail}"),
    _E("field_lost_volatile", _C,
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
       description_template="Field lost volatile: {name}::{detail}"),
    _E("field_renamed", _A,
       impact="Field name changed but offset is the same; source code using old name won't compile.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Field renamed: {name}::{old} → {new}"),
    _E("flexible_array_member_changed", _C,
       impact="Flexible array member (FAM) at end of struct changed: last field with "
              "zero/unknown array size was added, removed, or changed type. The struct "
              "binary layout is unchanged (FAM has zero static size), but runtime "
              "allocation patterns may differ."),
    _E("func_cv_changed", _B,
       impact="const/volatile on 'this' changes the mangled name; old binaries link to the wrong symbol.",
       description_template="CV qualifier changed: {name}"),
    _E("func_pure_virtual_added", _B,
       impact="Old subclasses don't implement the pure virtual; instantiation causes linker error or UB.",
       description_template="Function became pure virtual: {name}"),
    _E("func_ref_qual_changed", _B,
       impact="Ref-qualifier (&/&&) on a member function changed; this alters the "
              "Itanium C++ ABI mangled name and overload resolution, so old binaries "
              "link to the wrong symbol or fail to resolve it.",
       description_template="Ref-qualifier changed: {name} ({old} → {new})"),
    _E("func_static_changed", _B,
       impact="Static/non-static transition changes calling convention (implicit this pointer); ABI mismatch.",
       description_template="Static qualifier changed: {name}"),
    _E("func_virtual_became_pure", _B,
       impact="Concrete virtual became pure; old binaries calling it get unresolved dispatch.",
       description_template="Function became pure virtual: {name}"),
    _E("handle_type_changed", _B,
       impact="An opaque handle typedef (a `void*` token or a pointer to a "
              "forward-declared struct) changed its underlying token type in a way "
              "callers can observe. Code that stored or compared the old handle "
              "representation now operates on an incompatible token."),
    _E("inline_body_references_renamed_member", _B,
       impact="An inline public accessor (header-emitted into every consumer "
              "binary) reaches into a pimpl/detail member by name. That member "
              "was renamed in the implementation type, and although the inline "
              "accessor's body was updated in lockstep in the new header, "
              "consumers compiled against the OLD header have the old field "
              "name baked into their binary. At runtime, the inline body "
              "accesses a field at the wrong offset (or by a name that no "
              "longer exists), producing silent wrong data or crashes.",
       description_template="Public class '{name}' has inline accessors {detail} by name. Field '{old}' was renamed to '{new}' in the new internal layout. Consumers compiled against the old header have the old member name baked into their inline accessor bodies; running against the new library reads the wrong offset or fails to resolve the member."),
    _E("inline_namespace_version_bumped", _B,
       impact="A header-declared symbol or type lives under a versioned "
              "inline namespace (e.g. `inline namespace _V1`) and the "
              "version segment shifted (`_V1` → `_V2`). Declarations look "
              "identical to consumers but every newly compiled TU produces "
              "a different mangled symbol; old TUs in the same program ODR-"
              "violate against new TUs. Specialisation of inline_namespace_"
              "moved that fires from declared-name evidence (works even "
              "when the library ships no .so).",
       description_template="Inline namespace version bumped: '{old}' → '{new}' (version segment changed from {detail}); mangled names change so old and new TUs of the same program ODR-violate."),
    _E("instantiation_missing_from_binary", _B,
       impact="Header declares an explicit template instantiation that the shipped "
              "library no longer exports. Consumer source compiles cleanly but fails "
              "to link at load time with an undefined-symbol error. Common when a "
              "build trim drops a Float/Method/Task combination without updating "
              "the public header's `extern template` declarations.",
       description_template="Template instantiation '{name}' was exported by the old library but is missing from the new binary. Other instantiations of '{detail}' still exist, so the public header very likely still advertises this one. Consumers built against the old header link cleanly but fail at load time with an undefined-symbol error."),
    _E("layout_unverifiable", _R,
       impact="A public type's layout could not be verified at the available evidence "
              "tier — its size/offsets are not present (e.g. a symbols-only or partial "
              "dump with no debug info), so a real layout change cannot be ruled out. "
              "Informational and non-escalating; rebuild with debug info (or supply "
              "headers) to confirm.",
       description_template="'{name}' layout could not be verified: one side carries a layout descriptor but the other has no layout evidence (no size/offsets). A real layout change cannot be ruled out — rebuild with debug info (or supply headers) to confirm. Informational and non-escalating."),
    _E("libcpp_abi_version_changed", _R,
       impact="The libc++ ABI version changed (e.g. _LIBCPP_ABI_VERSION 1 → 2). "
              "libc++ selects incompatible internal layouts for std:: types via an "
              "inline namespace (std::__1 vs std::__2), so types embedding them by "
              "value are laid out differently. Rebuild consumers against the matching "
              "libc++ ABI version.",
       description_template="libc++ ABI version changed ({old} → {new}). libc++ selects incompatible internal layouts for std:: types via an inline namespace (std::__{old} vs std::__{new}); types embedding them by value are laid out differently. Rebuild consumers against the matching libc++ ABI version."),
    _E("mandatory_template_param_added", _A,
       impact="A function or class template parameter that was defaulted "
              "(or deduced) became mandatory. Consumer source that wrote "
              "`Foo<int>` without supplying the new parameter no longer "
              "compiles. Mangled symbols also change because the "
              "instantiation tuple differs.",
       description_template="Template '{name}' minimum effective argument count grew from {old} to {new}. Consumers that wrote '{name}<...{old} args...>' without supplying the new parameter no longer compile."),
    _E("opaque_invariant_broken", _B,
       impact="A type that was opaque (its definition hidden from callers, crossed "
              "only by pointer) or PIMPL now exposes its layout — its complete "
              "definition became visible in the public include closure, or a "
              "public function began passing it by value. Callers that relied on "
              "never seeing the layout can now `sizeof`/embed it, so the type's "
              "size and fields have joined the ABI and any later change to them is "
              "a hard break."),
    _E("overload_added", _R,
       impact="A new overload was added under a public name that previously had "
              "exactly one declaration. Old binaries are unaffected (binary "
              "compatible), but the change is not source-compatible: taking the "
              "function's address (`&Foo::bar`) becomes ambiguous and fails to "
              "compile, and existing call sites that relied on an implicit "
              "conversion may now resolve to the new overload, silently changing "
              "which function runs. KDE's C++ binary-compatibility policy lists "
              "adding an overload to a non-overloaded function as a change to "
              "avoid. Verdict is policy-adjustable — raise to API_BREAK under a "
              "strict source-compatibility profile.",
       description_template="Overload added to previously non-overloaded function: {name} — `&{name}` becomes ambiguous and overload resolution may change"),
    _E("overload_set_rerouted", _R,
       impact="The overload set under a public name changed in a way "
              "where some overloads were removed and others added. "
              "Existing call sites that previously resolved to a removed "
              "overload now resolve to a different overload (often via "
              "implicit conversion or a templated catch-all), silently "
              "changing the called function. Compiles, links, runs — but "
              "runs different code.",
       description_template="Overload set for '{name}' changed: {detail}. Call sites that previously resolved to a removed overload may silently re-route to a different overload."),
    _E("polymorphic_type_non_virtual_dtor", _R,
       impact="A type with virtual methods (it has a vtable) is used as a factory "
              "return or base class but declares no virtual destructor. Deleting "
              "a derived object through a base pointer is undefined behaviour: the "
              "derived destructor never runs and the wrong amount of memory may be "
              "freed. Declare the base destructor `virtual`."),
    _E("removed_const_overload", _A,
       impact="Const overload removed; source code calling const version breaks.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Const method overload removed: {name} (non-const version still exists)"),
    _E("rtti_inheritance_changed", _B,
       impact="A polymorphic class's RTTI typeinfo (`_ZTI`) object changed size, which in "
              "the Itanium C++ ABI means its base-class shape changed: no-base "
              "(`__class_type_info`, 2 words) ↔ single-base (`__si_class_type_info`, "
              "3 words) ↔ multiple/virtual-base (`__vmi_class_type_info`, larger), or the "
              "number of bases differs. Base-class changes shift `this`-pointer "
              "adjustments, member offsets, and the vtable, so derived classes and "
              "by-value users are miscompiled. Recovered from the ELF symbol size without "
              "DWARF — the binary-only analogue of TYPE_BASE_CHANGED.",
       description_template="RTTI typeinfo for '{name}' changed size: {old} → {new} bytes ({detail}). The base-class shape changed, which shifts this-pointer adjustments, member offsets, and the vtable. Detected from the ELF symbol size without debug info."),
    _E("secondary_vtable_group_changed", _B,
       impact="A polymorphic class's set of *secondary* vtable groups changed even "
              "though its own base declaration list did not — a direct or virtual "
              "base gained or lost virtual functions, so it started or stopped "
              "owning a secondary vtable group in the derived class. In the Itanium "
              "C++ ABI each polymorphic non-primary base contributes its own vtable "
              "group with its own this-adjustment; adding, removing, or reordering "
              "a group shifts every following group and the this-offsets baked into "
              "already-compiled consumers, so virtual dispatch through the affected "
              "base lands on the wrong slot. Reconstructed from DWARF inheritance "
              "(L1), catching a cross-type effect the per-type base/field diff — "
              "which only sees the unchanged derived class — cannot.",
       description_template="Secondary vtable groups changed for '{name}': {old} → {new} — a base's polymorphism changed, restructuring the derived vtable"),
    _E("source_level_kind_changed", _A,
       impact="An aggregate's declared struct/class kind changed at the "
              "source level; `_diff_type_kind_changes()` always routes any "
              "union-involving transition to the separate "
              "`type_kind_changed` kind instead, so this one is reported "
              "only for struct↔class — which differ solely in default "
              "member access and default inheritance access, never in "
              "storage layout. The binary layout is unaffected; source "
              "relying on the old kind's default access may no longer "
              "compile or may behave differently (e.g. a member that was "
              "implicitly public becoming implicitly private).",
       policy_overrides={"sdk_vendor": _C},
       description_template="Aggregate kind changed: {name} ({old} → {new})"),
    _E("standard_layout_lost", _R,
       impact="A type stopped being standard-layout (e.g. it gained a mix of access "
              "specifiers, a base with members, or virtual members). `offsetof` and "
              "C interoperability are no longer guaranteed and tail-padding reuse "
              "rules change; review code that relies on the C-compatible layout.",
       description_template="'{name}' is no longer standard-layout. `offsetof` and C interoperability are no longer guaranteed and tail-padding reuse rules change; review code relying on the C-compatible layout."),
    _E("std_reexport_removed", _A,
       impact="A public header used to re-export a name from `std::` "
              "(e.g. `using std::execution::par;`) and the re-export was "
              "deleted in the new headers. Consumer source that referenced "
              "the library-qualified name (`lib::par`) no longer compiles "
              "even though the underlying `std::par` is still available. "
              "Source break only — no symbol disappears, but every TU that "
              "named the library alias must be edited.",
       description_template="Public re-export '{name}' of standard-library entity '{detail}' was removed. Consumer code that named '{name}' no longer compiles; '{detail}' is still available under its std:: name."),
    _E("stdlib_implementation_changed", _R,
       impact="The two artifacts were built against different C++ standard-library "
              "implementations (e.g. libstdc++ vs libc++, or vs MSVC STL). The "
              "standard does not guarantee ABI compatibility across implementations: "
              "any public type embedding a std:: container/string by value gets a "
              "different layout, and inline std:: code can ODR-conflict. Pin a single "
              "implementation or rebuild consumers against the matching runtime."),
    _E("struct_alignment_changed", _B,
       impact="Struct alignment changed; may cause misaligned access in embedded structs.",
       description_template="Struct alignment changed: {name} ({old} → {new})"),
    _E("struct_field_offset_changed", _B,
       impact="Field moved to different offset; old code accesses wrong memory.",
       description_template="Field offset changed: {name}::{detail} (+{old} → +{new})"),
    _E("struct_field_removed", _B,
       impact="Field removed from struct; old code accessing it reads/writes garbage.",
       description_template="Struct field removed: {name}::{detail}"),
    _E("struct_field_type_changed", _B,
       impact="Field type changed in binary; old code misinterprets the field data.",
       description_template="Field type changed: {name}::{detail} {old} → {new}"),
    _E("struct_packing_changed", _B,
       impact="Packing attribute changed; field offsets differ from what old code expects."),
    _E("struct_size_changed", _B,
       impact="sizeof(T) changed in debug info; confirms layout break visible at binary level.",
       description_template="Struct size changed: {name} ({old} → {new} bytes)"),
    _E("sycl_overload_set_removed", _B,
       impact="A family of public overloads that take a SYCL queue as the first "
              "parameter was removed in bulk (typical when DPC++ support is "
              "disabled at build time). Reported as one grouped finding rather "
              "than N independent func_removed entries to make the deployment-"
              "level event ('the GPU/SYCL overload family was withdrawn') "
              "visible at a glance.",
       description_template="SYCL overload family withdrawn: {detail}. This is the deployment-level event 'DPC++ build disabled' rather than independent API removals — consumers built against the SYCL surface need a DPC++-enabled rebuild."),
    _E("tag_type_renamed", _B,
       impact="An empty tag struct (zero fields, no methods) used solely for "
              "template specialization was renamed. Layout-based detectors see no "
              "change because the type has no layout, but every explicit "
              "instantiation that referenced the old tag is re-mangled and the "
              "old symbol disappears. Consumers built against the old header get "
              "unresolved-symbol errors at load time. Common with "
              "method::* / task::* tag families.",
       description_template="Empty tag struct '{old}' renamed to '{new}'. The type has no fields or vtable, so layout-based detectors see no change, but {detail}. Consumers built against the old header fail to resolve the instantiation at load time."),
    _E("tail_padding_reuse_changed", _R,
       impact="The type's data size (the bytes its own members occupy, excluding "
              "trailing tail padding) changed while sizeof stayed the same. A derived "
              "class may reuse a base's tail padding, so this can silently shift a "
              "derived layout even though the base's sizeof is unchanged.",
       description_template="'{name}' data size changed ({old} → {new} bits) while sizeof stayed {detail} bits. A derived class may reuse this type's tail padding, so a derived layout can shift even though sizeof is unchanged."),
    _E("template_param_type_changed", _B,
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
       description_template="Template parameter inner type changed: {name} param {detail} ({old} → {new})"),
    _E("template_return_type_changed", _B,
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
       description_template="Template return type inner argument changed: {name} ({old} → {new})"),
    _E("trivially_copyable_lost", _B,
       impact="A type stopped being trivially copyable (e.g. a user-declared "
              "copy/move constructor, destructor, or a non-trivial member was added). "
              "Non-trivially-copyable types are passed and returned by value "
              "differently (via a hidden reference / not in registers), so the calling "
              "convention for any function taking or returning it by value changes.",
       description_template="'{name}' is no longer trivially copyable. It is now passed and returned by value differently (via a hidden reference / not in registers), so the calling convention of any function taking or returning it by value changes."),
    _E("type_added", _C, is_addition=True,
       impact="New type available; existing binaries are unaffected.",
       description_template="New type: {name}"),
    _E("type_alignment_changed", _B,
       impact="Misaligned access can cause bus errors on strict architectures or silent data corruption with SIMD.",
       description_template="Alignment changed: {name} ({old} → {new} bits)"),
    _E("type_base_changed", _B,
       impact="Base class layout change shifts derived member offsets and vtable pointers; this-pointer arithmetic breaks."),
    _E("type_became_abstract", _A,
       impact="A class/struct gained a pure virtual function (directly or via "
              "an inherited one newly left unimplemented), making it abstract. "
              "Source that directly instantiates the type (`Foo obj;`, "
              "`new Foo()`) no longer compiles. Not recorded in DWARF/the "
              "binary, so detected only in header (castxml) mode.",
       description_template="Class became abstract: {name} — direct instantiation no longer compiles"),
    _E("type_became_final", _A,
       impact="A class/struct gained the `final` specifier. Any consumer that "
              "derives from it (`class D : public C`) no longer compiles. The "
              "type layout and mangled names are unchanged so already-built "
              "binaries keep running, but recompilation against the new header "
              "fails — a source/API break. Invisible to binary analysis: "
              "`final` is not recorded in DWARF or the object file, so this is "
              "detected only in header (castxml) mode.",
       description_template="Class gained `final` specifier: {name} — consumers that derive from it no longer compile"),
    _E("type_became_opaque", _B,
       impact="Type became forward-declaration only; old code using sizeof or accessing fields fails.",
       description_template="Type became opaque (forward-declaration only): {name} — stack allocation no longer possible"),
    _E("type_deprecated_added", _C,
       impact="Type gained [[deprecated]]; consumers get a compiler "
              "warning when naming it. This detector matches types by "
              "identity and only checks the deprecated flag — it doesn't "
              "verify the matched pair's layout or ABI are also "
              "unchanged, so a companion finding for either is possible. "
              "A consumer building with warnings as errors "
              "(e.g. -Werror=deprecated-declarations) has this turn a "
              "previously clean build into a failing one.",
       description_template="Type marked deprecated: {name} ({detail})"),
    _E("type_deprecated_removed", _C,
       impact="Type's [[deprecated]] marker was removed; the compiler "
              "warning stops, with no effect on the type's ABI.",
       description_template="Type no longer marked deprecated: {name}"),
    _E("type_field_added", _B,
       impact="New field shifts subsequent fields; old code reads wrong offsets for all fields after insertion point.",
       description_template="Field added: {name}::{detail}"),
    _E("type_field_added_compatible", _C, is_addition=True,
       impact="Field appended without changing existing offsets; old code works but won't initialize the new field.",
       description_template="Field added: {name}::{detail}"),
    _E("type_field_offset_changed", _B,
       impact="Old code reads/writes fields at stale offsets; silent data corruption.",
       description_template="Field offset changed: {name}::{detail} ({old} → {new} bits)"),
    _E("type_field_removed", _B,
       impact="Old code accesses a field that no longer exists at the expected offset; reads garbage or writes out of bounds.",
       description_template="Field removed: {name}::{detail}"),
    _E("type_field_type_changed", _B,
       impact="Field has different size or representation; old code misinterprets the data.",
       description_template="Field type changed: {name}::{detail}"),
    _E("type_kind_changed", _B,
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
       description_template="Aggregate kind changed: {name} ({old} → {new})"),
    _E("type_lost_abstract", _C,
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
       description_template="Class lost abstract status: {name}"),
    _E("type_lost_final", _R,
       impact="A class/struct lost the `final` specifier. Deriving from it is "
              "now allowed and previously-valid source still compiles, so this "
              "is not a source break. The risk is on already-compiled consumers: "
              "code built while the class was `final` may have had its virtual "
              "calls *devirtualized*, and if a later version introduces a "
              "subclass that overrides, those old binaries keep dispatching "
              "statically to the wrong target. KDE's C++ binary-compatibility "
              "policy lists removing `final` as a change to avoid; surfaced as a "
              "deployment risk for review rather than a hard break.",
       description_template="Class lost `final` specifier: {name}"),
    _E("type_removed", _B,
       impact="Old code references a type that no longer exists; compilation or link failure."),
    _E("type_size_changed", _B,
       impact="Old code allocates or copies the type with the old size; heap/stack corruption, out-of-bounds access.",
       description_template="Size changed: {name} ({old} → {new} bits)"),
    _E("type_visibility_changed", _B,
       impact="A type's visibility attribute changed, affecting whether "
              "its typeinfo/vtable symbols are exported from the shared "
              "library; a consumer relying on RTTI (dynamic_cast, typeid, "
              "exception matching) or virtual dispatch across the library "
              "boundary can fail to find the expected typeinfo/vtable once "
              "the visibility narrows."),
    _E("type_vtable_changed", _B,
       impact="Vtable slot reordering; virtual dispatch calls wrong method."),
    _E("typedef_base_changed", _B,
       impact="Underlying type changed; old code using the typedef operates on wrong representation.",
       description_template="Typedef base type changed: {name}"),
    _E("typedef_removed", _B,
       impact="Old code using the typedef name won't compile; binary impact depends on usage.",
       description_template="Typedef removed: {name}"),
    _E("typedef_version_sentinel", _C,
       impact="Typedef name encodes a version number (e.g. png_libpng_version_1_6_46) — "
              "this is a compile-time sentinel that changes every release by design; "
              "it is never exported as an ELF symbol and does not affect binary ABI.",
       description_template="Version-stamped typedef removed (compile-time sentinel, not an ABI break): {name}"),
    _E("union_field_added", _C, is_addition=True,
       impact="Union size may grow; old code allocating with old sizeof gets truncated data.",
       description_template="Union field added: {name}::{detail}"),
    _E("union_field_removed", _B,
       impact="Old code accessing removed alternative reads uninitialized memory.",
       description_template="Union field removed: {name}::{detail}"),
    _E("union_field_type_changed", _B,
       impact="Old code interprets the union member with wrong type layout.",
       description_template="Union field type changed: {name}::{detail}"),
    _E("unnamed_type_in_public_abi", _R,
       impact="An exported symbol embeds an unnamed type in its mangled name — a "
              "lambda closure (`Ul…E_`) or an unnamed struct/enum (`Ut…_`). The "
              "Itanium mangling of unnamed types is per-translation-unit and "
              "compiler-ordering dependent (recompiling, or merely reordering "
              "unrelated declarations, can renumber `{lambda#1}` → `{lambda#2}`), "
              "so exporting one is an ABI time bomb: a rebuilt consumer can fail to "
              "resolve the symbol. RISK / hygiene — reported when newly introduced.",
       description_template="Unnamed type leaks into the public ABI: {name} ({detail}) — its mangled name is compiler-ordering-fragile"),
    _E("unspecified_return_now_named", _A,
       impact="A factory function's return type changed between an "
              "unspecified placeholder (`auto`, lambda type, anonymous "
              "class) and a named type — or vice versa. Source that "
              "stored the result with the deduced spelling (`auto x = "
              "make_X();`) keeps compiling; source that wrote out the "
              "type fails to compile."),
    _E("used_reserved_field", _C,
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
       description_template="Reserved field put into use: {name}::{old} → {new}"),
    _E("var_value_changed", _C,
       impact="A global variable's initial/static value changed; this "
              "detector only compares the value itself, not the "
              "variable's type or size, so this fires purely on the "
              "observed value difference. If a companion finding also "
              "reports the type or size changed, treat that as the more "
              "significant signal — otherwise this is a behavior change, "
              "not an ABI break, and old binaries that inlined the old "
              "value via constant propagation keep using it until "
              "recompiled.",
       description_template="Global data value changed: {name} ({old} → {new})"),
    _E("virtual_base_offset_changed", _B,
       impact="A class's virtual bases were reordered with the base set unchanged, "
              "so the virtual-base offset table (vbase offsets stored in the "
              "vtable) is laid out in a different order. The this-pointer "
              "adjustment used to reach a virtual base is baked into old binaries; "
              "after a reorder those adjustments point at the wrong subobject, "
              "corrupting access to virtual-base members with no symbol error. "
              "Detected from the DWARF virtual-inheritance order (L1); a pure "
              "virtual-base reorder is invisible to the non-virtual "
              "base_class_position_changed check.",
       description_template="Virtual base order changed for '{name}': {old} → {new} — vbase offset table reordered; old binaries mis-adjust `this` to virtual bases"),
    _E("virtual_method_added", _B,
       impact="A new virtual method was added to a class that already exists across "
              "versions. If the class had no virtuals it gains a hidden vtable pointer "
              "(its size and field offsets shift); if it was already polymorphic the new "
              "slot grows/relayouts the vtable. Either way derived classes compiled "
              "against the old layout dispatch through the wrong slots and old binaries "
              "embedding the type read the wrong offsets. This is the KDE "
              "\"do not add virtuals to a non-leaf class\" rule, caught even when the "
              "snapshot carries no diff-able vtable array (DWARF/symbol-only mode).",
       description_template="New virtual method added to existing class {detail}: {new} — grows/relayouts the vtable, breaking derived classes and old binaries"),
    _E("vptr_introduced", _B,
       impact="A previously non-polymorphic class gained its first virtual function, "
              "so the compiler prepends a vtable pointer. sizeof grows and every data "
              "member's offset shifts by a pointer width; existing binaries that embed "
              "or derive from the type are laid out incompatibly.",
       description_template="'{name}' gained a vtable pointer (became polymorphic). sizeof grows and every data member's offset shifts by a pointer width; binaries that embed or derive from the type are laid out incompatibly."),
    _E("vtable_slot_count_changed", _B,
       impact="A polymorphic class's emitted vtable group changed size. The `_ZTV` object "
              "spans the primary table plus any vcall/vbase offsets and secondary tables, "
              "so the cause is either virtual functions net added/removed or a change in "
              "the inheritance shape — the symbol size alone cannot say which. Either way "
              "existing binaries dispatch through fixed vtable offsets, so they may call "
              "the wrong slot or run off the end of the table. Recovered from the ELF "
              "symbol size without DWARF — the binary-only analogue of FUNC_VIRTUAL_ADDED "
              "/ TYPE_VTABLE_CHANGED; identifying which slot moved needs DWARF or headers.",
       description_template="Vtable for '{name}' changed size: {old} → {new} bytes ({detail}). Virtual functions were net added or removed, or the inheritance shape changed — the symbol size cannot distinguish them; existing binaries dispatch through fixed vtable offsets and may call the wrong slot. Detected from the ELF symbol size without debug info."),
    _E("vtable_thunk_offset_changed", _B,
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
       description_template="Vtable thunk offset changed for {name}: {old} → {new} — a base subobject moved; old binaries mis-adjust `this` on virtual dispatch"),
    _E("vtable_thunk_set_changed", _B,
       impact="A method that persists across versions gained or lost a "
              "virtual-override thunk. A thunk appears when a class overrides a "
              "virtual inherited through a *secondary* (multiple-inheritance) "
              "base; its appearance/disappearance means the override was added or "
              "removed in a secondary vtable. Because the inherited slot itself "
              "persists, the primary-vtable _ZTV size can be unchanged, so this is "
              "invisible to the slot-count diff. Old binaries dispatch to the "
              "wrong target through the secondary vtable.",
       description_template="Vtable thunk set changed for {name}: {detail} — a secondary-base override was added or removed"),
    _E("vtt_slot_count_changed", _B,
       impact="A class's VTT (virtual-table-table, `_ZTT`) object changed size. "
              "The VTT is the construction scaffolding the Itanium ABI uses to "
              "initialize the vtable pointers of virtual bases during "
              "construction/destruction; its size encodes the number of "
              "sub-vtables. A change means the virtual-inheritance shape changed, "
              "so a constructor compiled against the old VTT installs the wrong "
              "vptrs. Recovered from the `_ZTT` symbol size alone (no DWARF).",
       description_template="VTT size changed for '{name}': {old} → {new} bytes — virtual-base construction scaffolding changed"),
]
