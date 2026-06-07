# Change Kind Reference

This page lists all `ChangeKind` values detected by abicheck, their default verdict,
and what they mean. Use this reference to understand what each detected change implies
for binary ABI compatibility, source API compatibility, or neither.

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
| `vector_abi_changed` | The vector-function (SIMD clone) ABI selection drifted between builds (from vector-ABI flags in DWARF `DW_AT_producer`: `-mveclibabi=` GCC, `-fveclib=` clang, `-vecabi=` Intel-style). Vectorized call variants resolve to a different ABI, breaking callers of the vector entry points. Downgraded to compatible under the `plugin_abi` policy. |
| `struct_packing_changed` | `__attribute__((packed))` was added or removed. Changes every field offset and the total size — complete struct layout break. |
| `type_visibility_changed` | RTTI typeinfo or vtable visibility changed. Cross-DSO `dynamic_cast` and exception matching can silently fail. |

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

### Symbol Rename

| Kind | Description |
|------|-------------|
| `symbol_renamed_batch` | Multiple symbols were renamed in a coordinated way (e.g. namespace prefix added or removed, mass refactor). Old binaries reference the old names and get undefined-symbol errors at load time. Emitted as a single roll-up finding so a namespace rename does not flood the report with one entry per symbol. |

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

### API-surface intelligence transitions (ADR-027)

These breaks are recognised from the declaration *graph* (idioms), not a single per-symbol diff. They fire when an opacity or handle guarantee that callers relied on is lost between versions. See the [API Surface Intelligence](../concepts/api-surface-intelligence.md) guide.

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

### Template and Overload Set Changes

| Kind | Description |
|------|-------------|
| `mandatory_template_param_added` | A template parameter that was previously defaulted (or deduced) became mandatory. Consumer source that wrote `Foo<int>` without supplying the new parameter no longer compiles; mangled instantiations also change because the template-argument tuple differs. |
| `unspecified_return_now_named` | A factory function's return type changed between an unspecified placeholder (`auto`, lambda type, anonymous class) and a named type — or vice versa. Source that stored the result with `auto x = make_X();` keeps compiling; source that wrote the type out by hand fails. |

### Header Re-exports

| Kind | Description |
|------|-------------|
| `std_reexport_removed` | A public header used to re-export a name from `std::` (e.g. `using std::execution::par;`) and the re-export was deleted. Consumer source that referenced the library-qualified name (`lib::par`) no longer compiles, even though the underlying `std::par` is still available. Source-only break — no symbol disappears. |

---

## Deployment Risk (`COMPATIBLE_WITH_RISK`)

These changes do **not** break existing compiled binaries (consumers already linked
against the old library continue to work). However, they may prevent the **new**
library from loading in some deployment environments. Manual review is required.

| Kind | Description |
|------|-------------|
| `symbol_size_changed_internal` | `st_size` changed on an **internal-looking** exported data symbol (reserved/underscore-prefixed, e.g. `_XkeyTable`, `_pcre2_ucd_records_8`, `_UCD_accessors`). Such symbols are usually private implementation state rather than intended public ABI, so the change is a risk rather than a hard break. A size change on a *public-looking* data symbol is still `symbol_size_changed` (`BREAKING`). Override either way via a `--policy-file`. |
| `symbol_version_required_added` | A new required symbol version appeared in `DT_VERNEED` (e.g., a new `GLIBC_2.17` dependency). Existing compiled consumers are unaffected — they are already linked. However, the new library will fail to load on systems whose libc does not provide that version. Verify that all target deployment environments satisfy the new requirement. |
| `symbol_leaked_from_dependency_changed` | A symbol exported by this library that appears to originate from a **dependency** (e.g., `libstdc++.so.6`, `libgcc_s.so.1`, `libc.so.6`) was removed, added, or changed. This is a real ABI fact — the library is leaking dependency symbols into its public ABI surface (a common side-effect of missing `-fvisibility=hidden`). Direct consumers of this library typically resolve those symbols through the dependency directly and are not immediately affected. However, the risk is that on other systems with a different version of the dependency, the leaked symbols may differ — causing failures. **Recommended action:** apply `-fvisibility=hidden` to prevent leaking dependency symbols. |
| `func_likely_renamed` | A function likely was renamed (binary fingerprint match: identical code size and hash, different symbol name). Old binaries reference the old name and will fail to resolve at load time. **This is a heuristic signal** — the match is based on function size and code hash fingerprinting in stripped binaries (elf_only_mode). Verify the rename is intentional. Only fires in symbols-only analysis mode. |
| `symbol_moved_version_node` | A symbol moved from one version node to another (e.g., `LIBFOO_1.0` → `LIBFOO_2.0`). Applications linked against the old version node will not find this symbol at the expected version. This is typically intentional during a major release, but should be verified. |
| `symbol_version_alias_changed` | The default symbol version alias changed (e.g. `foo@@VER_1.0` → `foo@@VER_2.0`). Existing binaries that requested the previous default version may fail to link or load if the old alias is not retained. **Recommended action:** retain the old alias as a non-default version to preserve resolution for existing consumers. |
| `protected_visibility_changed` | An ELF symbol's visibility changed between `STV_DEFAULT` and `STV_PROTECTED`. For data symbols this can break copy relocations; for functions it changes interposition semantics. The symbol remains exported, but consumers using `LD_PRELOAD`-based interposition may stop seeing the override. |
| `vtable_symbol_identity_changed` | A vtable or `typeinfo` symbol's identity changed (e.g. via a visibility or version-script change) while the class layout is stable. Cross-DSO `dynamic_cast` and exception matching can silently fail because they compare RTTI pointers, not contents. |
| `overload_set_rerouted` | The overload set under a public name changed in a way where some overloads were removed and others added. Existing call sites that previously resolved to a removed overload now resolve to a different one (often via implicit conversion or a templated catch-all) — compiles, links, runs, but runs **different** code. |
| `behavioural_default_changed` | A documented default value changed without altering any signature — e.g. the default device selector, the default execution backend, or the default policy. Source compiles and links unchanged; runtime behaviour silently differs. Read from the probe manifest's `defaults:` section. |
| `relro_weakened` | RELRO protection was weakened (e.g. **full → partial** or **→ none**). The GOT is no longer fully read-only after relocation, widening the GOT-overwrite attack surface. Captured from `PT_GNU_RELRO` + `BIND_NOW`. Not a binary-compatibility break, but a hardening regression. Gate it via the shipped `security` policy (`--policy-file security`). |
| `pie_disabled` | A position-independent **executable** became non-PIE (`DF_1_PIE` dropped on an `ET_DYN` image), so it loads at a fixed address and ASLR no longer randomizes it. Hardening regression; gate via `--policy-file security`. |
| `stack_canary_removed` | The stack-smashing protector (`-fstack-protector`) is no longer referenced (`__stack_chk_fail` / `__stack_chk_guard` absent). Stack-buffer overflows are no longer detected at runtime. Gate via `--policy-file security`. |
| `fortify_source_weakened` | `_FORTIFY_SOURCE` fortified libc wrappers (the `*_chk` family, e.g. `__memcpy_chk`) are no longer referenced, dropping compile-time/runtime buffer-overflow checks. Gate via `--policy-file security`. |
| `writable_executable_segment` | A loadable segment is now simultaneously writable **and** executable (a W^X violation). Injected code in that page becomes executable. Gate via `--policy-file security`. |
| `public_api_exposes_stl_by_value` | A public function takes or returns a `std::` type by value across the library boundary. Standard-library layouts differ across toolchains, standard-library versions, and the C++11 dual-ABI setting, so a consumer built with a different STL silently reads the wrong layout. A graph-shaped anti-pattern (ADR-027 A2): reported by `surface-report`, and at diff time only when newly introduced. |
| `polymorphic_type_non_virtual_dtor` | A type with virtual methods (it has a vtable) is used as a factory return or base class but declares no virtual destructor. Deleting a derived object through a base pointer is undefined behaviour. A graph-shaped anti-pattern (ADR-027 A2): reported by `surface-report`, and at diff time only when newly introduced. |

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
| `type_lost_final` | A class/struct lost the `final` specifier. Strictly more permissive — deriving from it is now allowed and previously-valid code still compiles. Header/castxml-mode only. |

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
| `func_noexcept_removed` | `noexcept` removed from a function. Existing binaries continue to resolve the symbol. A source-level exception-specification concern only. |

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
| `toolchain_flag_drift` | Toolchain flags drifted between builds (e.g., `-fshort-enums`, `-fpack-struct`). Informational — may indicate a real break that other checks (size, alignment) would catch. |

### ABI Surface Diagnostics

| Kind | Description |
|------|-------------|
| `glibcxx_dual_abi_flip_detected` | Mass symbol churn matches a libstdc++ dual ABI toggle (`_GLIBCXX_USE_CXX11_ABI`). Individual removed/added symbols are likely caused by this single root cause rather than intentional API changes — the underlying per-symbol findings are reported separately. |
| `abi_surface_explosion` | The public ABI surface grew or shrank dramatically (e.g. a lost `-fvisibility=hidden` flag). This is a configuration/packaging signal, not a per-symbol break, but may indicate an unintended visibility regression. |

### Surface-metric drift (ADR-027, opt-in `--surface-metrics`)

Aggregate roll-up signals computed from the [API surface metrics](../concepts/api-surface-intelligence.md). Informational only — the individual additions/removals are reported per-symbol; these never drive a verdict on their own and are emitted only with `--surface-metrics`.

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
