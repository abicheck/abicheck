---
doc_type: reference
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - change-kinds
depends_on:
  - abicheck/change_registry.py
  - abicheck/checker_policy.py
lifecycle: active
generated: false
---

# Change Kind Reference

This page documents the most commonly encountered `ChangeKind` values, their
default verdict, and what they mean. Use this reference to understand what each
detected change implies for binary ABI compatibility, source API compatibility,
or neither.

> **Scope:** this is a curated reference, not the exhaustive list — specialised
> kinds (multi-library bundle skew, SYCL plugin-interface, build-context and
> source-evidence findings) are documented on their feature pages
> ([Multi-Binary Releases](../user-guide/multi-binary.md),
> [Plugin Systems](../user-guide/plugin-systems.md),
> [Build & Source Packs](../concepts/build-source-data.md)). The authoritative,
> always-complete list is the `ChangeKind` enum in
> [`abicheck/checker_policy.py`](https://github.com/abicheck/abicheck/blob/main/abicheck/checker_policy.py),
> and CI enforces that every kind is classified, produced by a detector, and
> mentioned in the docs.

**Verdict overview:**

| Verdict | Meaning |
|---------|---------|
| `BREAKING` | Binary ABI break — existing compiled binaries may crash, fail to load, or produce incorrect results. |
| `API_BREAK` | Source API break — existing source code will fail to compile, but compiled binaries are still compatible. |
| `COMPATIBLE_WITH_RISK` | Binary-compatible but with a deployment risk — existing compiled binaries are unaffected, but the change may prevent the library from loading on some target environments. **Needs manual review.** |
| `COMPATIBLE` | Compatible change — additive or informational; no impact on existing binaries or source. |

---

## Binary ABI Breaks (`BREAKING`)

These changes are immediately incompatible with existing compiled binaries.

### Function Changes

| Kind | Description |
|------|-------------|
| `func_removed` | Public function removed from the exported symbol table. Callers crash at load time with an undefined symbol error. |
| `func_removed_elf_only` | Exported function symbol removed in binary-only/symbols-only mode. Header evidence is unavailable, so strict ABI policy treats the removed dynamic export as a binary break. |
| `func_return_changed` | Function return type changed. Callers reading the return value will interpret the wrong bytes — silent data corruption or crashes. |
| `func_params_changed` | Function parameter types or count changed. The calling convention breaks: arguments are placed in wrong registers/stack slots. |
| `func_virtual_added` | A non-virtual method became virtual. Changes the vtable layout: any class with this as a base will have a different vtable offset for all methods after this one. |
| `func_virtual_removed` | A virtual method is no longer virtual. Vtable layout collapses — all vtable offsets shift for derived classes. |
| `virtual_method_added` | A new virtual method was added to a class that already exists across versions. If the class had no virtuals it gains a hidden vtable pointer (size/offsets shift); if it was already polymorphic the new slot grows/relayouts the vtable. Derived classes and old binaries dispatch through the wrong slots. This is the KDE "do not add a virtual to a non-leaf class" rule, caught even when the snapshot carries no diff-able vtable array (DWARF/symbol-only mode); when the vtable array *does* change, `type_vtable_changed` reports it instead. |
| `func_static_changed` | A method changed from static to non-static or vice versa. The calling convention changes (implicit `this` pointer added/removed). |
| `func_cv_changed` | `const` or `volatile` qualifier on `this` changed. This changes the mangled name and the overload set — existing binaries resolve the wrong symbol. |
| `func_visibility_changed` | Function visibility changed from default to hidden. The symbol disappears from the dynamic symbol table — callers get undefined symbol at link or load time. |
| `func_pure_virtual_added` | A virtual function became pure virtual. Any concrete class that does not implement it is now abstract — instantiation fails at link time. |
| `func_virtual_became_pure` | A virtual method that had a default implementation is now pure. Derived classes that relied on the base implementation now fail to link. |
| `func_deleted` | A function was marked `= delete`. Previously callable code now gets a link-time error (callers compiled against old header had no error). |
| `func_deleted_dwarf` | A function was marked `= delete`, detected via DWARF debug info (`DW_AT_deleted` in DWARF 5+, or a function declared in headers but absent from the DWARF compilation units). The function was previously callable; callers fail to link. |
| `func_deleted_elf_fallback` | A function declared in the public headers disappeared from `.dynsym` (header-declared but no longer exported) and no explicit `= delete` annotation was present in metadata. Best-effort mixed-mode fallback: consumers' PLT entries fail to resolve at load time. |
| `func_ref_qual_changed` | A C++ member function's ref-qualifier (`&` / `&&`) changed. This alters the Itanium ABI mangled name and the overload resolution result; old binaries link to a symbol that no longer exists under the previous name. |

### Variable Changes

| Kind | Description |
|------|-------------|
| `var_removed` | Exported global variable removed. Callers crash at load time with an undefined symbol error. |
| `var_type_changed` | Global variable type changed. Callers reading the variable will interpret memory incorrectly — wrong size, alignment, or layout. |
| `var_became_const` | A non-const variable became const. The linker may move it to `.rodata` — existing binaries writing to it receive `SIGSEGV`. |
| `var_lost_const` | A const variable lost its `const` qualifier. Callers may have inlined the value at compile time (ODR violation) — stale values or crashes. |

### Type / Struct Changes

| Kind | Description |
|------|-------------|
| `type_size_changed` | struct/class total size changed. Callers allocating instances on the stack or inside other structs will use the wrong allocation size. |
| `type_alignment_changed` | Alignment requirement of a struct/class changed. Critical on ARM/RISC-V where misaligned access causes bus errors or data corruption. |
| `type_field_removed` | A field was removed from a struct/class. All field offsets after the removal point shift — binary layout of any consumer is wrong. |
| `type_field_added` | A field was added to a polymorphic or non-standard-layout struct/class. Can change size and offsets for all following fields. |
| `type_field_offset_changed` | A struct/class field moved to a different byte offset. Any caller accessing that field reads the wrong bytes. |
| `type_field_type_changed` | A struct/class field changed its type (different size or representation). Callers reading or writing the field get wrong values. |
| `type_base_changed` | Base class list changed (class added, removed, or reordered). This-pointer offsets for all bases that follow the change are invalidated. |
| `type_vtable_changed` | Virtual table layout changed. All virtual dispatch through this class or its derivatives will call wrong functions. |
| `type_removed` | A type used in the public API was completely removed. Any caller referencing the type gets a link-time undefined symbol error. |
| `type_became_opaque` | A previously complete type became a forward declaration only. Callers that used the full definition (stack allocation, field access) now fail to compile or link. |
| `type_kind_changed` | The kind of a type changed (e.g., `struct` → `union` or `union` → `class`). The entire memory layout model changes — catastrophic ABI break. |

### Base Class Changes

| Kind | Description |
|------|-------------|
| `base_class_position_changed` | An inherited base class was reordered in the inheritance list. The this-pointer offset for the shifted base changes — virtual dispatch and casts break. |
| `base_class_virtual_changed` | A base class became virtual or stopped being virtual. This alters the vptr placement and diamond-inheritance layout — complete vtable/layout break. |

### Enum Changes

| Kind | Description |
|------|-------------|
| `enum_member_removed` | An enumerator value was removed. Switch statements and comparisons that relied on its existence break silently or crash. |
| `enum_member_value_changed` | An enumerator's numeric value changed. Any binary that serialized, compared, or switched on the old value will behave incorrectly. |
| `enum_last_member_value_changed` | The last (often sentinel) enumerator value changed. Loop bounds, array sizes, and sentinel checks using this value are now wrong. |
| `enum_underlying_size_changed` | The underlying integer type of an enum changed size (e.g., `int` → `long`). Struct layout and function parameter sizes change — full ABI break. |

### Typedef Changes

| Kind | Description |
|------|-------------|
| `typedef_removed` | A public typedef was removed. Any consumer using the typedef gets a compile error or link failure depending on the usage. |
| `typedef_base_changed` | The underlying type of a typedef changed. Callers that assumed the original underlying type get incorrect behavior. |

### Union Changes

| Kind | Description |
|------|-------------|
| `union_field_removed` | A field was removed from a union. Code reading the union through the removed member name fails to compile or reads wrong bytes. |
| `union_field_type_changed` | A union field changed its type. The size interpretation of the union changes — binary consumers reading the field get wrong values. |

### Bitfield Changes

| Kind | Description |
|------|-------------|
| `field_bitfield_changed` | A bitfield's width or position changed. Callers accessing packed bitfields read the wrong bits — silent data corruption. |

### DWARF / Struct Layout

| Kind | Description |
|------|-------------|
| `struct_size_changed` | `sizeof(T)` changed as reported by DWARF. Confirms a binary ABI break for stack/heap allocations. |
| `struct_field_offset_changed` | A struct field's byte offset changed according to DWARF. Callers accessing that field read wrong memory. |
| `struct_field_removed` | A struct field was removed according to DWARF. All following field offsets shift. |
| `struct_field_type_changed` | A struct field changed its type according to DWARF. Layout and semantics change for that field. |
| `struct_alignment_changed` | `alignof(T)` changed according to DWARF. Critical for SIMD types and cross-platform code. |
| `calling_convention_changed` | The calling convention for a function changed (from DWARF `DW_AT_calling_convention`). Arguments are passed via different registers or stack layout. |
| `struct_return_convention_changed` | A public function's aggregate (struct/class/union) **return** convention flipped between in-register and hidden caller-provided pointer (sret) — e.g. via `-freg-struct-return`/`-fpcc-struct-return` or a triviality/size change crossing the register-return threshold (from the DWARF value-ABI return trait). Caller and callee disagree on where the result lives. The return-specific refinement of `value_abi_trait_changed`. |
| `vector_abi_changed` | The vector-function (SIMD clone) ABI selection drifted between builds (from vector-ABI flags in DWARF `DW_AT_producer`: `-mveclibabi=` GCC, `-fveclib=` clang, `-vecabi=` Intel-style). Vectorized call variants resolve to a different ABI, breaking callers of the vector entry points. Downgraded to compatible under the `plugin_abi` policy. |
| `struct_packing_changed` | `__attribute__((packed))` was added or removed. Changes every field offset and the total size — complete struct layout break. |
| `type_visibility_changed` | RTTI typeinfo or vtable visibility changed. Cross-DSO `dynamic_cast` and exception matching can silently fail. |

### Class Layout Descriptor

Fine-grained layout mechanics that the coarse `struct_size_changed` / `struct_field_offset_changed` detectors under-represent. Read from the optional layout fields on a record (`base_offsets`, `vptr_offset_bits`, `data_size_bits`, `is_standard_layout`, `is_trivially_copyable`). Each is **tri-state guarded** — emitted only when *both* sides carry the relevant evidence — so an evidence-tier downgrade (DWARF-only / symbols-only dump, or an older snapshot) never fabricates a finding.

| Kind | Description |
|------|-------------|
| `base_class_offset_changed` | A base-class subobject moved to a different offset within the derived object (e.g. an empty-base optimization was lost, or a member/base was inserted ahead of it) without the base list reordering. The `this`-pointer adjustment for that base and every field after it shifts; old binaries read the wrong addresses. |
| `vptr_introduced` | A previously non-polymorphic class gained its first virtual function, so the compiler prepends a vtable pointer. `sizeof` grows and every data member's offset shifts by a pointer width; existing binaries that embed or derive from the type are laid out incompatibly. |
| `trivially_copyable_lost` | A type stopped being trivially copyable (e.g. a user-declared copy/move constructor, destructor, or a non-trivial member was added). Non-trivially-copyable types are passed and returned by value differently (via a hidden reference / not in registers), so the calling convention for any function taking or returning it by value changes. |

### Binary-only C++ Layout (no DWARF / L0)

Recovered from `.dynsym` symbol sizes alone by `diff_elf_layout.py`. The Itanium C++ ABI fixes the on-disk size of a class's vtable (`_ZTV`) and typeinfo (`_ZTI`) objects, so these break detections work on libraries shipped fully stripped of debug info and headers — closing the blind spot a pure exported-symbol dump has (a virtual-method or base-class change need not rename any mangled symbol). Each fires only when the *same* `_ZTV`/`_ZTI` symbol is present on both sides with a different size.

| Kind | Description |
|------|-------------|
| `vtable_slot_count_changed` | A polymorphic class's vtable (`_ZTV`) object changed size — it now holds a different number of virtual-function slots (a virtual method was added, removed, or reordered). Existing binaries dispatch through fixed vtable offsets, so they call the wrong slot. The binary-only analogue of `func_virtual_added` / `type_vtable_changed`. |
| `rtti_inheritance_changed` | A polymorphic class's RTTI typeinfo (`_ZTI`) object changed size, which in the Itanium ABI means its base-class shape changed: no-base (`__class_type_info`) ↔ single-base (`__si_class_type_info`) ↔ multiple/virtual-base (`__vmi_class_type_info`), or the base count differs. Base-class changes shift `this`-pointer adjustments, member offsets, and the vtable. The binary-only analogue of `type_base_changed`. |
| `vtable_thunk_offset_changed` | A virtual-override thunk's `this`-pointer adjustment offset changed (e.g. `_ZThn8_` → `_ZThn16_` for the same target method). A thunk fixes up `this` when a call arrives through a *secondary* (multiple-inheritance) base's vtable, and the adjustment is baked into every already-compiled consumer's vtables. A changed offset means a base subobject moved, so old binaries adjust `this` by the wrong amount on virtual dispatch — with **no symbol error**, and even when the primary `_ZTV` size is unchanged. Catches the multi-inheritance base-reorder break the slot-count diff misses. |
| `vtable_thunk_set_changed` | A method whose plain symbol persists across versions gained or lost a virtual-override thunk — a virtual inherited through a secondary base was overridden (or the override removed). Because the inherited slot persists, the `_ZTV` size can be unchanged, so this is invisible to the slot-count diff; old binaries dispatch to the wrong target through the secondary vtable. |
| `vtt_slot_count_changed` | A class's VTT (virtual-table-table, `_ZTT`) object changed size. The VTT is the construction scaffolding the Itanium ABI uses to install virtual-base vtable pointers during construction/destruction; its size encodes the sub-vtable count. A change means the virtual-inheritance shape changed, so a constructor compiled against the old VTT installs the wrong vptrs. |
| `secondary_vtable_group_changed` | A polymorphic class's set of *secondary* vtable groups changed even though its own base declaration list did not — a direct or virtual base gained or lost virtual functions, so it started or stopped owning a secondary vtable group. In the Itanium ABI each polymorphic non-primary base contributes its own vtable group with its own `this`-adjustment; adding, removing, or reordering a group shifts the offsets baked into already-compiled consumers, so virtual dispatch through the affected base lands on the wrong slot. Reconstructed from DWARF inheritance (L1) — a cross-type effect the per-type base/field diff, which only sees the unchanged derived class, cannot catch. |
| `virtual_base_offset_changed` | A class's virtual bases were reordered with the base set unchanged, so the virtual-base offset table is laid out in a different order. The `this`-pointer adjustment used to reach a virtual base is baked into old binaries; after a reorder those adjustments point at the wrong subobject. A pure virtual-base reorder is invisible to the non-virtual `base_class_position_changed` check. |

### Pointer / Parameter Level Changes

| Kind | Description |
|------|-------------|
| `param_pointer_level_changed` | A parameter changed its pointer indirection level (e.g., `T*` → `T**`). The ABI representation size changes — callers pass the wrong data. |
| `return_pointer_level_changed` | Return type pointer indirection level changed (e.g., `T*` → `T**`). Callers dereference the return value incorrectly. |

### Anonymous Struct/Union

| Kind | Description |
|------|-------------|
| `anon_field_changed` | An anonymous struct or union member changed. Offset arithmetic for all sibling fields may be affected. |

### Template Changes

| Kind | Description |
|------|-------------|
| `template_return_type_changed` | A function's return type is a template specialization whose inner type argument changed (e.g. `vector<int>` → `vector<double>`). The mangled return type changes, so old binaries fail to resolve the symbol. |
| `template_param_type_changed` | A function *parameter* type is a template specialization whose inner type argument changed (e.g. `vector<int>` → `vector<double>`). The mangled parameter type changes, so old binaries fail to resolve the symbol. The parameter-side counterpart of `template_return_type_changed`. |

### Symbol Rename

| Kind | Description |
|------|-------------|
| `symbol_renamed_batch` | Multiple symbols were renamed in a coordinated way (e.g. namespace prefix added or removed, mass refactor). Old binaries reference the old names and get undefined-symbol errors at load time. Emitted as a single roll-up finding so a namespace rename does not flood the report with one entry per symbol. |
| `versioned_symbol_scheme_detected` | Most removed symbols reappear as added symbols differing only by a numeric version token in the name (e.g. ICU `u_strlen_75` → `u_strlen_78`, or a GNU symbol-version node bump). The large removed/added churn is likely a library-wide versioned-symbol scheme, not independent API removals. **Advisory only** (`COMPATIBLE_WITH_RISK`): it explains the churn and never downgrades the artifact-proven removals. To act on it, opt in with `compare --collapse-versioned-symbols`, which reclassifies the C-style version-rename pairs as compatible so the verdict reflects the real delta (a real SONAME bump and non-versioned removals still drive the verdict). |

### ELF Symbol Versioning

| Kind | Description |
|------|-------------|
| `symbol_version_defined_removed` | A symbol version definition (`GLIBC_2.5`, etc.) was removed from the library. Binaries linked against that version tag cannot find the symbol. |
| `symbol_version_node_removed` | A version node (e.g., `LIBFOO_1.0`) was entirely removed from the version script. All symbols that were under that node become unresolvable for applications linked against it. More specific than `symbol_version_defined_removed` — includes which symbols were affected. |


### ELF Dynamic Section

| Kind | Description |
|------|-------------|
| `soname_changed` | The library SONAME changed. Any binary linked against the old SONAME will fail to load at runtime — the dynamic linker cannot find the library. |
| `symbol_type_changed` | Symbol type changed in the ELF `.dynsym` (e.g., `STT_FUNC` → `STT_OBJECT`). The dynamic linker may handle it incorrectly — undefined behavior at runtime. |
| `symbol_size_changed` | Symbol size (`st_size`) changed in ELF `.dynsym`. In ELF-only analysis mode, this is the primary signal for variable or vtable layout changes. |
| `symbol_size_changed_internal` | `st_size` changed on an **internal-looking** exported data symbol (reserved/underscore-prefixed, e.g. `_XkeyTable`, `_pcre2_ucd_records_8`, `_UCD_accessors`). Such symbols are often private implementation state rather than intended public ABI, but exported data is still part of the dynamic ABI and size changes can break copy relocations or direct data consumers. This is `BREAKING` by default; use a `--policy-file` override only when the symbol is known private and safe to accept as risk. A size change on a *public-looking* data symbol remains `symbol_size_changed` (`BREAKING`). |
| `symbol_size_changed_const_object` | Size changed for a public exported const object such as `extern char const name[]`. Even when headers do not expose a fixed bound, old non-PIE consumers can carry copy relocations sized from the old DSO symbol, so this remains a hard binary-compatibility break. |


### PE/COFF & Mach-O Platform Metadata (binary-only)

These are recovered from the binary headers / export tables alone — no PDB or DWARF required.

| Kind | Description |
|------|-------------|
| `pe_forwarder_changed` | A DLL export forwarder (`OTHERDLL.Symbol`) was repointed to a different target. The implementation behind the exported name changed; dependents get different — possibly missing — behaviour at load time. |
| `pe_machine_changed` | The PE machine/architecture changed (e.g. `IMAGE_FILE_MACHINE_AMD64` → `IMAGE_FILE_MACHINE_ARM64`). The DLL is a different architecture and cannot be loaded by existing clients. |
| `macho_cpu_type_changed` | A Mach-O architecture slice that used to ship is gone — e.g. `X86_64` → `ARM64`, or a universal `x86_64+arm64` dylib that dropped its `x86_64` slice. Clients built for the removed architecture can no longer link against or load the dylib. Adding a slice (single-arch → universal) is **not** flagged. |
| `elf_machine_changed` | The ELF `e_machine` (target architecture) changed. The two inputs are different-architecture binaries — the ELF-side analogue of `pe_machine_changed` / `macho_cpu_type_changed`. A consumer built for one cannot load the other. |
| `elf_class_changed` | The ELF class changed between 32-bit and 64-bit. Pointer width, type sizes, and the calling convention all differ. |
| `elf_abi_flags_changed` | The `e_flags` ABI-selecting bits changed — the float ABI (hard/soft-float), EABI version, or base ISA differs (ARM / RISC-V / MIPS). Object code compiled against the old convention passes floating-point arguments in the wrong registers/stack slots. Artifact-proven from `e_flags`; the flag-level `float_abi_changed` (L3) stays the explanatory signal. |

---


### Modern C/C++ standard & toolchain ABI hazards

These breaks come from language-standard features or build-model choices (oneAPI-relevant) escaping into the public binary contract.

| Kind | Description |
|------|-------------|
| `integer_model_changed` | The library's integer model flipped (LP64 ↔ ILP64). A public integer typedef (e.g. an `MKL_INT`-style alias) changed its underlying width, or a large fraction of public function parameters/returns flipped integer width together (`int`↔`long`, `int32_t`↔`int64_t`). Callers compiled against the old width pass/return data in the wrong-sized registers/slots — silent corruption. Classic oneMKL LP64-vs-ILP64 mismatch. |
| `abi_tag_changed` | A single exported symbol's Itanium ABI-tag set changed (e.g. gained or lost `[abi:cxx11]` or a `[[gnu::abi_tag]]`). The mangled name changes, so a consumer compiled against the old header links against a symbol that no longer exists. The per-symbol analogue of `glibcxx_dual_abi_flip_detected` (which reports the library-wide flip). |
| `char8t_migration` | A public function parameter/return type or a struct field changed between a `char`-family spelling and C++20 `char8_t` (in either direction). `char8_t` is a distinct fundamental type: the mangled name and overload identity change, so old binaries reference a symbol that no longer exists. |
| `bit_int_width_changed` | A public type used C23 `_BitInt(N)` and the width `N` changed, or a parameter/field/return type changed to or from `_BitInt(N)`. The width is part of the type identity and layout, so old code reads/writes the wrong number of bits and the mangled name changes. |
| `atomic_qualifier_changed` | The `_Atomic` qualifier was added to or removed from a public field, parameter, or return type. The size, alignment, and (for some struct types) representation of atomic-qualified types can diverge from their non-atomic counterparts and across compilers (WG14), so old code misinterprets the data. |
| `long_double_abi_changed` | A function's `long double` parameter/return representation changed — ppc64 IBM double-double ↔ IEEE binary128, or 80-bit x87 ↔ `__float128`. The source signature is unchanged but the floating-point format differs, so old binaries pass/return the value in the wrong size and bit layout. Detected from the Itanium long-double mangling token (`e`/`g`/`u9__ieee128`) on a removed↔added symbol pair (which it collapses into this single finding). |

### Linux kernel ABI (kABI) — `Module.symvers` (binary-only)

Recovered by passing two `Module.symvers` manifests to `compare` (recognized by filename or content). These are the canonical kernel-ABI signals distro kABI guarantees rely on.

| Kind | Description |
|------|-------------|
| `kabi_symbol_removed` | A kernel-exported symbol (`EXPORT_SYMBOL*`) was removed. Out-of-tree modules referencing it fail to load with "Unknown symbol". |
| `kabi_crc_changed` | A kernel-exported symbol's genksyms CRC changed. Under `CONFIG_MODVERSIONS` the loader rejects a module whose embedded CRC disagrees ("disagrees about version of symbol"), even though the symbol name is unchanged — the type signature behind it changed. |
| `kabi_symbol_namespace_changed` | A kernel-exported symbol gained or moved its export namespace (`EXPORT_SYMBOL_NS*`). A module without the matching `MODULE_IMPORT_NS()` fails to load. |
| `kabi_export_type_changed` (`API_BREAK`) | A symbol changed between `EXPORT_SYMBOL` and `EXPORT_SYMBOL_GPL`. A non-GPL module can no longer link against a now-GPL-only symbol — a license-gated availability break. |

### API-surface intelligence transitions (ADR-027)

These breaks are recognised from the declaration *graph* (idioms), not a single per-symbol diff. They fire when an opacity or handle guarantee that callers relied on is lost between versions. See the [API Surface Intelligence](../user-guide/api-surface-intelligence.md) guide.

| Kind | Description |
|------|-------------|
| `opaque_invariant_broken` | A type that was opaque (definition hidden from callers, crossed only by pointer) or PIMPL now exposes its layout — its complete definition became visible in the public include closure, or a public function began passing it by value. Callers can now `sizeof`/embed it, so its size and fields have joined the ABI and any later change to them is a hard break. The pattern-verdict pass emits this **instead** of silently demoting an opaque-layout change once opaqueness is lost. |
| `handle_type_changed` | An opaque handle typedef (a `void*` token or a pointer to a forward-declared struct) changed its underlying token type in a way callers can observe. Code that stored or compared the old handle representation now operates on an incompatible token. |

## Source API Breaks (`API_BREAK`)

These changes break the source-level API contract but do not affect already-compiled binaries.

### Class specifiers

| Kind | Description |
|------|-------------|
| `type_became_final` | A class/struct gained the `final` specifier. Consumers that derive from it no longer compile; layout and mangled names are unchanged so binaries keep running. **Header/castxml-mode only** — `final` is not recorded in DWARF or the object file, so this is invisible to object-only comparison (see `case125_class_became_final`). |

### Naming and Renaming

| Kind | Description |
|------|-------------|
| `enum_member_renamed` | An enumerator was renamed (same value, different name). Source code referencing the old name fails to compile. |
| `field_renamed` | A struct/class field was renamed (same offset and type). Source code accessing the old field name fails to compile. |
| `param_renamed` | A function parameter was renamed. Source code using designated initializers or named argument extensions breaks. |

### Default Argument Changes

| Kind | Description |
|------|-------------|
| `param_default_value_removed` | A default argument was removed from a function parameter. Call sites that omitted that argument now fail to compile. |

### Access Level Changes

| Kind | Description |
|------|-------------|
| `method_access_changed` | A method's access level narrowed (e.g., `public` → `protected` or `private`). Source code calling the method on the old access level fails to compile. |
| `field_access_changed` | A field's access level narrowed (e.g., `public` → `private`). Source code directly accessing the field fails to compile. |
| `var_access_changed` | A global/static variable's access level narrowed. Source code that directly accessed the variable fails to compile. |

### Source-Level Kind Change

| Kind | Description |
|------|-------------|
| `source_level_kind_changed` | A type changed between `struct` and `class`. In C++ these have identical binary layout, but source code using the keyword explicitly may get compilation warnings or errors in strict contexts. |

### Overload Changes

| Kind | Description |
|------|-------------|
| `removed_const_overload` | A `const` method overload was removed. Source code calling the method on a `const` object now fails to compile or selects a different overload silently. |

### Preprocessor Constants

| Kind | Description |
|------|-------------|
| `constant_changed` | A `#define` constant's value changed. Source code that used the constant in a way that depended on its exact value gets different behavior at compile time. |
| `constant_removed` | A `#define` constant was removed entirely. Source code referencing it fails to compile. |

### Source ABI Replay (L4)

| Kind | Description |
|------|-------------|
| `public_typedef_target_changed` | A public typedef/alias now resolves to a different underlying type (e.g. `typedef int32_t handle_t;` became `typedef int64_t handle_t;`). A bare typedef leaves no exported symbol, so source replay (the clang L4 backend, ADR-030) surfaces the change; source relying on the old aliased type may change overload resolution or fail to compile while already-linked binaries are unaffected. |

### Template and Overload Set Changes

| Kind | Description |
|------|-------------|
| `mandatory_template_param_added` | A template parameter that was previously defaulted (or deduced) became mandatory. Consumer source that wrote `Foo<int>` without supplying the new parameter no longer compiles; mangled instantiations also change because the template-argument tuple differs. |
| `unspecified_return_now_named` | A factory function's return type changed between an unspecified placeholder (`auto`, lambda type, anonymous class) and a named type — or vice versa. Source that stored the result with `auto x = make_X();` keeps compiling; source that wrote the type out by hand fails. |

### Header Re-exports

| Kind | Description |
|------|-------------|
| `std_reexport_removed` | A public header used to re-export a name from `std::` (e.g. `using std::execution::par;`) and the re-export was deleted. Consumer source that referenced the library-qualified name (`lib::par`) no longer compiles, even though the underlying `std::par` is still available. Source-only break — no symbol disappears. |

### Inline and Build-Configuration Changes

| Kind | Description |
|------|-------------|
| `func_became_inline` | A function gained the `inline` specifier. An inline function need not emit a standalone external symbol, so its definition can vanish from the DSO and consumers must have the header to inline it themselves. The dual of the compatible `func_lost_inline`. |
| `cxx_standard_floor_raised` | The library's minimum required C++ standard rose between releases (e.g. C++17 → C++20). Consumers still building against the old standard no longer get a working header set, and standard-library facilities removed in newer standards may disappear from the API surface. Detected across a probe matrix of build configurations. |

---

## Deployment Risk (`COMPATIBLE_WITH_RISK`)

These changes do **not** break existing compiled binaries (consumers already linked
against the old library continue to work). However, they may prevent the **new**
library from loading in some deployment environments. Manual review is required.

| Kind | Description |
|------|-------------|
| `symbol_version_required_added` | A new required symbol version appeared in `DT_VERNEED` (e.g., a new `GLIBC_2.17` dependency). Existing compiled consumers are unaffected — they are already linked. However, the new library will fail to load on systems whose libc does not provide that version. Verify that all target deployment environments satisfy the new requirement. |
| `symbol_leaked_from_dependency_changed` | A symbol exported by this library that appears to originate from a **dependency** (e.g., `libstdc++.so.6`, `libgcc_s.so.1`, `libc.so.6`) was removed, added, or changed. This is a real ABI fact — the library is leaking dependency symbols into its public ABI surface (a common side-effect of missing `-fvisibility=hidden`). Direct consumers of this library typically resolve those symbols through the dependency directly and are not immediately affected. However, the risk is that on other systems with a different version of the dependency, the leaked symbols may differ — causing failures. **Recommended action:** apply `-fvisibility=hidden` to prevent leaking dependency symbols. |
| `func_likely_renamed` | A function likely was renamed (binary fingerprint match: identical code size and hash, different symbol name). Old binaries reference the old name and will fail to resolve at load time. **This is a heuristic signal** — the match is based on function size and code hash fingerprinting in stripped binaries (elf_only_mode). Verify the rename is intentional. Only fires in symbols-only analysis mode. |
| `symbol_moved_version_node` | A symbol moved from one version node to another (e.g., `LIBFOO_1.0` → `LIBFOO_2.0`). Applications linked against the old version node will not find this symbol at the expected version. This is typically intentional during a major release, but should be verified. |
| `symbol_version_alias_changed` | The default symbol version alias changed (e.g. `foo@@VER_1.0` → `foo@@VER_2.0`). Existing binaries that requested the previous default version may fail to link or load if the old alias is not retained. **Recommended action:** retain the old alias as a non-default version to preserve resolution for existing consumers. |
| `protected_visibility_changed` | An ELF symbol's visibility changed between `STV_DEFAULT` and `STV_PROTECTED`. For data symbols this can break copy relocations; for functions it changes interposition semantics. The symbol remains exported, but consumers using `LD_PRELOAD`-based interposition may stop seeing the override. |
| `vtable_symbol_identity_changed` | A vtable or `typeinfo` symbol's identity changed (e.g. via a visibility or version-script change) while the class layout is stable. Cross-DSO `dynamic_cast` and exception matching can silently fail because they compare RTTI pointers, not contents. |
| `overload_set_rerouted` | The overload set under a public name changed in a way where some overloads were removed and others added. Existing call sites that previously resolved to a removed overload now resolve to a different one (often via implicit conversion or a templated catch-all) — compiles, links, runs, but runs **different** code. |
| `overload_added` | A new overload was added under a public name that previously had exactly one declaration. Old binaries are unaffected (binary compatible), but it is not source-compatible: taking the function's address (`&Foo::bar`) becomes ambiguous and fails to compile, and call sites relying on an implicit conversion may now resolve to the new overload. KDE's C++ binary-compatibility policy lists adding an overload to a non-overloaded function as a change to avoid. Raise to `API_BREAK` under a strict source-compatibility profile. |
| `func_noexcept_removed` | `noexcept` removed from a function. The function symbol itself is unchanged (Itanium mangling does not encode `noexcept` on the symbol), so existing binaries keep resolving it — not a binary break. But since C++17 `noexcept` is part of the function *type*, so it is encoded in function-pointer and template-argument mangling: a consumer forming `void(*)() noexcept` or passing the function as a non-type template argument no longer compiles, and code relying on the guarantee can hit `std::terminate`. KDE lists this as a change to avoid unless the spec was `noexcept(false)`. Raise to `API_BREAK` under a strict source-compatibility profile. |
| `type_lost_final` | A class/struct lost the `final` specifier. Deriving from it is now allowed and previously-valid source still compiles, so it is not a source break — but consumers compiled while the class was `final` may have had virtual calls **devirtualized**, and if a later version introduces an overriding subclass those old binaries dispatch statically to the wrong target. KDE lists removing `final` as a change to avoid. Header/castxml-mode only (DWARF/symbol mode carries no `final` info). |
| `behavioural_default_changed` | A documented default value changed without altering any signature — e.g. the default device selector, the default execution backend, or the default policy. Source compiles and links unchanged; runtime behaviour silently differs. Read from the probe manifest's `defaults:` section. |
| `relro_weakened` | RELRO protection was weakened (e.g. **full → partial** or **→ none**). The GOT is no longer fully read-only after relocation, widening the GOT-overwrite attack surface. Captured from `PT_GNU_RELRO` + `BIND_NOW`. Not a binary-compatibility break, but a hardening regression. Gate it via the shipped `security` policy (`--policy-file security`). |
| `pie_disabled` | A position-independent **executable** became non-PIE (`DF_1_PIE` dropped on an `ET_DYN` image), so it loads at a fixed address and ASLR no longer randomizes it. Hardening regression; gate via `--policy-file security`. |
| `stack_canary_removed` | The stack-smashing protector (`-fstack-protector`) is no longer referenced (`__stack_chk_fail` / `__stack_chk_guard` absent). Stack-buffer overflows are no longer detected at runtime. Gate via `--policy-file security`. |
| `fortify_source_weakened` | `_FORTIFY_SOURCE` fortified libc wrappers (the `*_chk` family, e.g. `__memcpy_chk`) are no longer referenced, dropping compile-time/runtime buffer-overflow checks. Gate via `--policy-file security`. |
| `writable_executable_segment` | A loadable segment is now simultaneously writable **and** executable (a W^X violation). Injected code in that page becomes executable. Gate via `--policy-file security`. |
| `cet_protection_weakened` | An x86 CET control-flow-protection feature (IBT and/or SHSTK) was dropped from `.note.gnu.property`. CET is enforced per link map, so a single non-IBT DSO disables indirect-branch tracking for the whole process. Gate via `--policy-file security`. The improvement direction is `cet_protection_improved` (`COMPATIBLE`). |
| `branch_protection_weakened` | An AArch64 branch-protection feature (BTI and/or PAC) was dropped from `.note.gnu.property`. Like CET, BTI enforcement is process-wide. Gate via `--policy-file security`. The improvement direction is `branch_protection_improved` (`COMPATIBLE`). |
| `static_tls_introduced` | The library set `DF_STATIC_TLS` — it adopted the static (initial/local-exec) TLS model and may no longer be reliably `dlopen()`ed (the loader can fail with "cannot allocate memory in static TLS block"). Link-time consumers are unaffected, so it defaults to RISK; gate via `--policy-file security` if the library must be dlopen-loadable. Only reported when the library actually participates in TLS. The flag-level `tls_model_changed` (L3) explains which build flag caused it. The improvement direction is `static_tls_removed` (`COMPATIBLE`). |
| `elf_osabi_changed` | The ELF `EI_OSABI` (target OS ABI) changed (e.g. SYSV ↔ GNU/Linux ↔ FreeBSD). This can alter the meaning of OS-specific symbol types and relocations; consumers may resolve or load differently. |
| `symbol_binding_became_unique` | An exported symbol's binding became `STB_GNU_UNIQUE`. GNU-unique symbols are enforced process-wide unique by the loader, and a library defining one becomes non-unloadable — `dlclose()` is inhibited for it. Changes loader semantics for consumers that rely on unloading. |
| `symbol_binding_lost_unique` | An exported symbol was `STB_GNU_UNIQUE` and is no longer. The process-wide ODR-uniqueness guarantee (a single shared instance of an inline/template static across all DSOs) is gone; duplicate per-DSO instances may reappear. |
| `public_api_exposes_stl_by_value` | A public function takes or returns a `std::` type by value across the library boundary. Standard-library layouts differ across toolchains, standard-library versions, and the C++11 dual-ABI setting, so a consumer built with a different STL silently reads the wrong layout. A graph-shaped anti-pattern (ADR-027 A2): reported by `surface-report`, and at diff time only when newly introduced. |
| `polymorphic_type_non_virtual_dtor` | A type with virtual methods (it has a vtable) is used as a factory return or base class but declares no virtual destructor. Deleting a derived object through a base pointer is undefined behaviour. A graph-shaped anti-pattern (ADR-027 A2): reported by `surface-report`, and at diff time only when newly introduced. |
| `unnamed_type_in_public_abi` | A newly-exported symbol embeds an unnamed type in its mangled name — a lambda closure (`Ul…E_`) or an unnamed struct/enum (`Ut…_`). The Itanium mangling of unnamed types is per-translation-unit and compiler-ordering dependent (recompiling, or merely reordering unrelated declarations, can renumber `{lambda#1}` → `{lambda#2}`), so exporting one is an ABI time bomb: a rebuilt consumer can fail to resolve the symbol. Hygiene RISK, reported when newly introduced. |
| `stdlib_implementation_changed` | The two artifacts were built against **different C++ standard-library implementations** (e.g. libstdc++ vs libc++, or vs the MSVC STL) — a third compatibility axis the standard never guarantees, alongside backward and forward compatibility. Any public type embedding a `std::` container/string **by value** (`class A { std::vector<T> v; };`) is laid out differently across implementations, and inline `std::` code can ODR-conflict. Derived from the normalized `BuildMode` capture; stays silent when build-mode evidence is absent rather than guessing. RISK, never breaking on its own: when an embedded `std::` type's layout actually differs, the type diff emits the concrete size/offset `BREAKING` finding separately. **Recommended action:** pin a single standard-library implementation or rebuild consumers against the matching runtime. |
| `libcpp_abi_version_changed` | The libc++ ABI version changed (e.g. `_LIBCPP_ABI_VERSION` 1 → 2). libc++ selects incompatible internal layouts for `std::` types via an inline namespace (`std::__1` vs `std::__2`), so types embedding them by value are laid out differently. **Recommended action:** rebuild consumers against the matching libc++ ABI version. |
| `standard_layout_lost` | A type stopped being standard-layout (e.g. it gained a mix of access specifiers, a base with members, or virtual members). `offsetof` and C interoperability are no longer guaranteed and tail-padding reuse rules change. Tri-state guarded (read from `is_standard_layout`). **Recommended action:** review code relying on the C-compatible layout. |
| `tail_padding_reuse_changed` | The type's **data size** (the bytes its own members occupy, excluding trailing tail padding — `dsize`) changed while `sizeof` stayed the same. A derived class may reuse a base's tail padding, so this can silently shift a derived layout even though the base's `sizeof` is unchanged. Tri-state guarded (read from `data_size_bits`). |
| `layout_unverifiable` | A public type's layout could not be verified at the available evidence tier — one side carries a layout descriptor but the other has no size/offset evidence (e.g. a symbols-only or partial dump), so a real layout change cannot be ruled out. Informational and non-escalating. **Recommended action:** rebuild with debug info (or supply headers) to confirm. |
| `exceptions_mode_changed` | C++ exception support was toggled between builds (`-fexceptions` ↔ `-fno-exceptions`), detected from the captured build evidence (L3). The modes are not link-compatible: an exception unwinding through a frame built with `-fno-exceptions` is undefined behaviour, and the flag changes the codegen/EH tables of every public inline that uses `throw`/`try`/`catch`. RISK, never breaking on its own — the artifact diff confirms a concrete break. **Recommended action:** rebuild consumers in the matching mode if the public API exposes exception types or throwing inlines. |
| `rtti_mode_changed` | C++ RTTI support was toggled between builds (`-frtti` ↔ `-fno-rtti`), from the L3 build evidence. `-fno-rtti` omits typeinfo for polymorphic types, so `dynamic_cast`/`typeid` and cross-DSO exception matching can fail when one side has RTTI and the other does not. RISK; the artifact diff confirms concrete breaks. **Recommended action:** rebuild consumers in the matching mode if the public API exposes polymorphic types or `dynamic_cast`/`typeid` in inlines. |
| `tls_model_changed` | The thread-local storage model changed between builds (`-ftls-model=`, or `-fextern-tls-init` ↔ `-fno-extern-tls-init`), from the L3 build evidence. The TLS access sequence — and, with `-fextern-tls-init`, whether a wrapper mediates access to a dynamically-initialized `thread_local` from another TU — differs, so consumers built against the old model can use the wrong access pattern for an exported `thread_local`. |
| `threadsafe_statics_mode_changed` | Thread-safe initialization of function-local statics was toggled (`-fno-threadsafe-statics` ↔ default), from the L3 build evidence. `-fno-threadsafe-statics` omits the `__cxa_guard` acquire/release around a local static's first-use init, so a public inline holding a function-local static, compiled in different modes across TUs, has mismatched guard expectations — a data race or double-init on concurrent first use. |
| `ctor_explicit_removed` | A constructor or conversion operator lost its `explicit` specifier. Existing code keeps compiling and the mangled name is unchanged, but implicit-conversion paths that previously excluded this function now consider it, which can silently select a different overload. The dual of `ctor_explicit_added`. |
| `api_depends_on_consumer_env` | A public declaration is present under some consumer build configurations (compiler, language standard, macro set) and absent under others, so source that compiled for the library author may not compile for the consumer. Detected only when abicheck is given a probe matrix of snapshots taken under multiple configurations. |

See the [Security-hardening drift](../user-guide/security-hardening.md) guide for how to scan for these across releases.

---

## Compatible Changes (`COMPATIBLE`)

These changes are safe: they add new capabilities or carry diagnostic information without affecting existing consumers.

### New Symbols

| Kind | Description |
|------|-------------|
| `func_added` | A new public function was exported. Existing binaries are unaffected; new callers can use it. |
| `var_added` | A new public global variable was exported. Existing binaries are unaffected. |
| `type_added` | A new type was added to the public API. Additive — existing consumers are unchanged. |
| `type_field_added_compatible` | A field was appended to a standard-layout, non-polymorphic struct. Size increases but no existing field offsets shift. Compatible only for types meeting the standard-layout criteria. |

### Enum Additions

| Kind | Description |
|------|-------------|
| `enum_member_added` | A new enumerator value was added. Existing compiled code that does not switch on all values is unaffected. Value shifts for other members are caught separately by `enum_member_value_changed`. |

### Union Additions

| Kind | Description |
|------|-------------|
| `union_field_added` | A new field was added to a union. All union fields start at offset 0 — no existing field offset shifts. Size increase (if any) is caught by `type_size_changed`. |

### noexcept Changes

| Kind | Description |
|------|-------------|
| `func_noexcept_added` | `noexcept` added to a function. The Itanium ABI mangling does not change in practice; existing compiled binaries resolve the same symbol. A source-level concern for function-pointer typing only. |

### Function Visibility and Inline Attribute Changes

| Kind | Description |
|------|-------------|
| `func_visibility_protected_changed` | A function's visibility changed between `STV_DEFAULT` and `STV_PROTECTED`. The symbol remains exported and is still resolvable by external consumers; intra-library calls bypass interposition (intentional). Existing compiled consumers are unaffected. |
| `func_lost_inline` | A function lost its `inline` attribute. The compiler now emits the symbol with normal external linkage, which is strictly additive — old binaries that previously could not find an inline-only definition will now resolve it. |

### ELF Dynamic Section

| Kind | Description |
|------|-------------|
| `soname_missing` | The old library had no SONAME — a packaging defect. The new library adds a SONAME, which is an improvement. |
| `visibility_leak` | The library exports internal symbols without `-fvisibility=hidden`. This is a diagnostic warning, not a break — no existing consumer relies on those symbols being absent. |
| `needed_added` | A new `DT_NEEDED` dependency was added. Existing consumers may not have the new dependency on their system — warn, but not a hard break. |
| `needed_removed` | A `DT_NEEDED` dependency was removed. Existing consumers that transitively relied on the removed dep may have unresolved symbols — deployment risk but not a proven break. |
| `rpath_changed` | The library `RPATH` changed. Runtime search path for transitive dependencies changes — a deployment/packaging concern. |
| `runpath_changed` | The library `RUNPATH` changed. Runtime search path changes — deployment concern. |
| `symbol_binding_changed` | Symbol binding changed from `GLOBAL` to `WEAK`. The symbol is still exported and resolvable; interposition semantics change but existing compiled binaries continue to work. |
| `symbol_binding_strengthened` | Symbol binding changed from `WEAK` to `GLOBAL`. Backward-compatible for all consumers. |
| `ifunc_introduced` | A function was changed from a regular function to a `STT_GNU_IFUNC` (GNU indirect function). The PLT/GOT mechanism transparently handles resolution — callers are unaffected. |
| `ifunc_removed` | A `STT_GNU_IFUNC` was changed back to a regular function. Transparent to callers via PLT/GOT. |
| `common_symbol_risk` | A `STT_COMMON` symbol is exported. Common symbols have merge semantics that can cause surprising behavior — a risk warning, not a proven break. |
| `symbol_version_defined_added` | Symbol versioning was introduced to the library (a new version definition added). New binaries link against the versioned symbol; old binaries use the unversioned fallback. |
| `symbol_version_required_removed` | A previously required symbol version dependency was dropped. Reduces the minimum libc/glibc requirement — compatible or an improvement. |
| `symbol_version_required_added_compat` | A new required symbol version dependency was added, but it is older than the previous maximum (e.g. `GLIBC_2.14` added while already depending on `GLIBC_2.17`). Existing target systems already satisfy the new requirement — informational only. The breaking case (a *newer* requirement) is reported as `symbol_version_required_added` under `COMPATIBLE_WITH_RISK`. |
| `symbol_elf_visibility_changed` | ELF symbol visibility (`st_other`) changed (e.g. `STV_DEFAULT` → `STV_PROTECTED`). The symbol is still exported, but interposition via `LD_PRELOAD` may stop working for intra-library calls. |

### ELF Symbol-Version Policy

| Kind | Description |
|------|-------------|
| `soname_bump_recommended` | Binary-incompatible changes were detected but the SONAME was not bumped. Consumers linked against the current SONAME will encounter runtime failures. This is a quality/policy advisory — the underlying breaking changes are reported separately. **Recommended action:** bump the SONAME to signal the ABI break. |
| `soname_bump_unnecessary` | The SONAME was bumped but no binary-incompatible changes were detected. This forces all consumers to relink unnecessarily. Consider whether the bump was intentional (e.g., a planned deprecation). |
| `version_script_missing` | The library exports symbols without a version script (`--version-script`). This prevents fine-grained symbol versioning and makes future ABI evolution harder to manage. **Recommended action:** add a version script. |

### DWARF Diagnostics

| Kind | Description |
|------|-------------|
| `dwarf_info_missing` | The new binary was stripped of debug info (`-g`). abicheck cannot perform DWARF-based comparison — this is a coverage gap warning, not a proven ABI break. |
| `layer_coverage_asymmetric` | The base snapshot was analyzed with evidence layers the target lacks (e.g. base scanned with binary + debug + headers + build + sources, target only with binary + headers). The comparison is scoped to the layers both sides share; changes only the missing layers could prove are not reported. Re-scan the target with the same inputs to restore full coverage. |
| `toolchain_flag_drift` | Toolchain flags drifted between builds (e.g., `-fshort-enums`, `-fpack-struct`). Informational — may indicate a real break that other checks (size, alignment) would catch. |

### ABI Surface Diagnostics

| Kind | Description |
|------|-------------|
| `glibcxx_dual_abi_flip_detected` | Mass symbol churn matches a libstdc++ dual ABI toggle (`_GLIBCXX_USE_CXX11_ABI`). Individual removed/added symbols are likely caused by this single root cause rather than intentional API changes — the underlying per-symbol findings are reported separately. |
| `abi_surface_explosion` | The public ABI surface grew or shrank dramatically (e.g. a lost `-fvisibility=hidden` flag). This is a configuration/packaging signal, not a per-symbol break, but may indicate an unintended visibility regression. |

### Surface-metric drift (ADR-027, opt-in `--surface-metrics`)

Aggregate roll-up signals computed from the [API surface metrics](../user-guide/api-surface-intelligence.md). Informational only — the individual additions/removals are reported per-symbol; these never drive a verdict on their own and are emitted only with `--surface-metrics`.

| Kind | Description |
|------|-------------|
| `public_surface_grew` | The net count of public declarations (functions, variables, types, enums) increased between versions. A trendable signal for CI dashboards and release notes. |
| `public_surface_shrank` | The net count of public declarations decreased between versions. A roll-up signal; the individual removals (which may be breaking) are reported separately. |
| `undocumented_export_ratio_increased` | The fraction of exported symbols with no public-header declaration (EXPORT_ONLY origin) rose — a packaging-hygiene regression where a symbol was exported without a corresponding public header. |

### Field Qualifier Changes

| Kind | Description |
|------|-------------|
| `field_became_const` | A struct field became `const`. No binary layout change; a source-level annotation. |
| `field_lost_const` | A struct field lost its `const` qualifier. No binary layout change. |
| `field_became_volatile` | A struct field became `volatile`. No binary layout change; changes compiler optimization behavior. |
| `field_lost_volatile` | A struct field lost its `volatile` qualifier. No binary layout change. |
| `field_became_mutable` | A struct field became `mutable`. No binary layout change; source-level annotation change. |
| `field_lost_mutable` | A struct field lost its `mutable` qualifier. No binary layout change. |

### Parameter Changes (Informational)

| Kind | Description |
|------|-------------|
| `param_default_value_changed` | A default argument value changed. Existing compiled call sites are unaffected (the default is encoded at the call site, not in the library). Informational only. |
| `param_restrict_changed` | A `restrict` qualifier was added or removed from a parameter. `restrict` is an optimization hint — no ABI impact. |
| `param_became_va_list` | A fixed parameter was replaced with a `va_list`. Informational — the actual parameter change is caught separately by `func_params_changed`. |
| `param_lost_va_list` | A `va_list` parameter was replaced with a fixed parameter. Informational. |

### Preprocessor Constants

| Kind | Description |
|------|-------------|
| `constant_added` | A new `#define` constant was added. Purely additive — no existing consumer is affected. |

### Global Data

| Kind | Description |
|------|-------------|
| `var_value_changed` | A global variable's initial value changed. Compile-time values inlined by the compiler may differ, but the binary ABI (symbol presence and type) is unchanged. |
| `used_reserved_field` | A previously `__reserved` field was put into use. Since reserved fields are allocated space but semantically undefined, using them is compatible (was unused). |
| `var_access_widened` | A variable's access level widened (e.g., `private` → `public`). Widening is always compatible. |

### Hidden Friends and Version Sentinels

| Kind | Description |
|------|-------------|
| `hidden_friend_added` | A new in-class `friend` declaration (a "hidden friend", findable only via ADL on one of its argument types) was added. Purely additive: existing code keeps compiling, no symbol disappears, and the new function only joins overload resolution at ADL call sites. The additive dual of `hidden_friend_removed`. |
| `typedef_version_sentinel` | A removed typedef whose name encodes a version number (e.g. `png_libpng_version_1_6_46`) was recognised as a compile-time version sentinel, not real API. Such typedefs are never exported as ELF symbols and change every release by design, so their removal is not an ABI break. |

### Loader Contract and Import Surface

| Kind | Description |
|------|-------------|
| `imported_symbol_added` | The binary gained an undefined (imported) symbol — a new obligation the consumer's link environment must satisfy at load time (`COMPATIBLE_WITH_RISK`). Weak imports are exempt. On PE, per-DLL imported-function drift (including import-by-ordinal) reports the same kind. |
| `imported_symbol_removed` | An undefined symbol is no longer required — one fewer external obligation (`COMPATIBLE`). |
| `interpreter_changed` | The ELF program interpreter (`PT_INTERP`) path changed (`COMPATIBLE_WITH_RISK`). |
| `bind_now_disabled` | Eager binding (`DT_BIND_NOW`/`DF_1_NOW`) was dropped with the RELRO level unchanged: unresolved symbols now fail at first call instead of load time (`COMPATIBLE_WITH_RISK`). |
| `dynamic_loading_flags_changed` | `DF_1_NODELETE` / `DF_1_NOOPEN` / `DF_1_ORIGIN` toggled — the dlopen/dlclose contract changed (`COMPATIBLE_WITH_RISK`). |
| `elf_init_fini_changed` | Load/unload-time code (`DT_INIT`/`DT_FINI`/init- and fini-arrays) appeared or disappeared (`COMPATIBLE_WITH_RISK`). |
| `allocator_replacement_added` / `allocator_replacement_removed` | The library started/stopped exporting a global `operator new`/`operator delete` replacement, interposing allocation for the whole process (`COMPATIBLE_WITH_RISK`). |
| `exported_object_alignment_reduced` | An exported data object's address alignment dropped — a copy-relocation / aligned-access hazard (`COMPATIBLE_WITH_RISK`). |

### Composition Compatibility

Failures that only appear when independently-valid artifacts are combined at
runtime — a symbol resolving to a different provider DSO, a reordered
dependency list, or a Windows consumer's ordinal-only import silently
retargeted — rather than a single library's own declaration diff.

| Kind | Description |
|------|-------------|
| `runtime_symbol_provider_changed` | A consumer's reference to a symbol resolves to a different provider DSO across two resolved environments (baseline vs candidate), even though neither DSO's own export table changed — caused by dependency reordering, a sibling library gaining/losing the export, or interposition drift. Computed by `abicheck deps compare` from the per-environment symbol bindings (`COMPATIBLE_WITH_RISK`). |
| `runtime_weak_resolution_changed` | A weak symbol reference's resolution status flipped between two resolved environments — a reference that used to resolve is now unresolved, or vice versa (`COMPATIBLE_WITH_RISK`). |
| `needed_order_changed` | The `DT_NEEDED` dependency list was reordered while the dependency *set* stayed the same. The dynamic linker searches dependencies breadth-first in `DT_NEEDED` order, so a pure reorder can silently change which DSO wins the lookup for a non-versioned symbol defined in more than one dependency (`COMPATIBLE_WITH_RISK`). |
| `symbolic_binding_mode_changed` | `DT_SYMBOLIC`/`DF_SYMBOLIC` was toggled. When set, the object resolves its own references against its own definitions first, before the global symbol scope — can silently stop honoring an `LD_PRELOAD` or another library's intended interposition (`COMPATIBLE_WITH_RISK`). |
| `text_relocation_introduced` | `DF_TEXTREL`/`DT_TEXTREL` was gained: the loader must write into the (nominally read-only, shared) text segment to apply relocations, defeating W^X and text-segment sharing; some hardened systems refuse to load the object (`COMPATIBLE_WITH_RISK`, matching the other hardening-regression kinds — gate to break via `--policy-file security`). The improvement direction is `text_relocation_removed` (`COMPATIBLE`). |
| `pe_ordinal_retargeted` | A consumer imports a DLL function purely by ordinal number (no name in its import table). The DLL still exports that ordinal, but it now names a **different** function — PE ordinals are commonly auto-assigned and reused when the export table shifts, so an ordinal-only consumer silently calls the wrong function with no link or load error. Detected by `compare --used-by APP` by cross-referencing the app's ordinal imports against both DLLs' export directories (`BREAKING`). |
| `pe_import_load_mode_changed` | An imported DLL function moved between the eager import table (resolved at process load) and the delay-load table (resolved on first call). The two have different failure-timing contracts even though the DLL and symbol both still exist (`COMPATIBLE_WITH_RISK`). |
| `wchar_model_changed` | The `-fshort-wchar` compiler flag drifted between builds (from `DW_AT_producer`). GCC/Clang document that objects built with and without `-fshort-wchar` are not binary compatible: it switches `wchar_t` between the platform default and a 2-byte unsigned type, changing the size/signedness of any public `wchar_t` parameter, field, or return value with no symbol-level signal (`COMPATIBLE_WITH_RISK`). |
| `consumer_required_symbol_removed` | A real consumer binary's own dynamic-symbol table (ELF undefined symbol / PE import / Mach-O undefined symbol) required this exact symbol from the library at load time, and the new library no longer exports it — empirical ground truth from `compare --used-by APP`, independent of any header/namespace/visibility reasoning. Promotes what used to be an ad-hoc "missing symbol" string into a first-class, suppressible finding (ADR-044 P2 item 1) (`BREAKING`). |
| `consumer_runtime_load_failed` | `compare --used-by APP --verify-runtime` (ADR-044 P2 item 2) actually ran the consumer binary once against the old library and once against the new one (`LD_BIND_NOW=1`, Linux-only): it loaded and ran cleanly against the old library, but the dynamic linker itself reported an undefined symbol against the new one. A dynamic corroborating signal alongside the static scanner, never a replacement for it — an execution environment can fail for unrelated reasons, so this never manufactures a `BREAKING` verdict on its own (`COMPATIBLE_WITH_RISK`). |

### Platform Identity and Deployment Floors

| Kind | Description |
|------|-------------|
| `elf_endianness_changed` | The ELF `EI_DATA` byte order flipped (LSB ↔ MSB) — a different binary contract entirely (`BREAKING`). |
| `x86_isa_baseline_raised` | `GNU_PROPERTY_X86_ISA_1_NEEDED` gained a micro-architecture level (e.g. x86-64-v2 → v3): older CPUs can no longer run the library (`COMPATIBLE_WITH_RISK`). |
| `os_deployment_floor_raised` | The minimum OS/kernel floor was raised — Mach-O `LC_BUILD_VERSION` minos, PE `MajorSubsystemVersion`, or the ELF `NT_GNU_ABI_TAG` kernel floor (`COMPATIBLE_WITH_RISK`). |
| `pe_hardening_weakened` / `pe_hardening_improved` | PE `DllCharacteristics` exploit mitigations (DEP, ASLR, HIGH_ENTROPY_VA, Control Flow Guard) lost or gained — see [Security Hardening](../user-guide/security-hardening.md). |
| `library_version_downgraded` | The embedded version regressed (PE `VS_FIXEDFILEINFO` or Mach-O `LC_ID_DYLIB` current_version) (`COMPATIBLE_WITH_RISK`). |
| `macho_filetype_changed` | The Mach-O filetype changed (e.g. `MH_DYLIB` → `MH_BUNDLE`): a bundle cannot be linked against at build time (`BREAKING`). |
| `macho_linkage_flags_changed` | Mach-O two-level-namespace / weak-definition linkage flags flipped (`COMPATIBLE_WITH_RISK`). |
| `macho_reexport_changed` | An `LC_REEXPORT_DYLIB` re-export was repointed to a different target dylib (`COMPATIBLE_WITH_RISK`). |

### Language Contracts (header tier)

| Kind | Description |
|------|-------------|
| `func_variadic_added` / `func_variadic_removed` | A function gained or lost a trailing C ellipsis (`...`). Variadic and fixed-arity calls use different conventions on common ABIs (SysV x86-64 `%al`, Apple AArch64 stack args), so both directions are `BREAKING`. |
| `func_contract_attribute_added` / `func_contract_attribute_removed` | A semantic contract attribute (`nonnull`, `noreturn`, `format`, `alloc_size`, `malloc`, `returns_nonnull`, `warn_unused_result`, `sentinel`, …) was gained or lost (`COMPATIBLE_WITH_RISK`). Calling-convention attribute flips (`stdcall`, `regparm`, `ms_abi`, …) report the dedicated `calling_convention_changed` kind instead. |
| `func_exception_spec_changed` | The dynamic exception specification (`throw(...)`) changed in a way the `noexcept` kinds do not cover (`COMPATIBLE_WITH_RISK`). |
| `var_alignment_changed` | An exported variable's declared alignment (`alignas`) changed — consumers baked the old alignment into copy relocations and aligned loads (`BREAKING`). |
