---
doc_type: reference
audience:
  - library-maintainer
level: beginner
summarizes:
  - verdicts
depends_on:
  - abicheck/change_registry.py
lifecycle: active
generated: false
---
# ABI Cheat Sheet

Quick-reference card for shared-library maintainers. Scannable in 2 minutes.

For deeper explanations see [ABI/API Compatibility](abi-api-handling.md) and [Verdicts](verdicts.md). To see *which evidence level* proves each row below (symbols → debug → headers → build → sources), see [What Each Level Sees](what-each-level-sees.md).

---

## Safe Changes (COMPATIBLE)

These changes preserve binary compatibility. Existing consumers continue to work without recompilation.

| Change | Why Safe | Example |
|--------|----------|---------|
| Add new exported function | Existing binaries never reference it; linker ignores unknown symbols | [case03](../reference/examples/case03_compat_addition.md) |
| Append enum member (end, no value shift) | Compiled binaries use integer values; existing values unchanged | [case25](../reference/examples/case25_enum_member_added.md) |
| Add union field without growing size | Union size = max(fields); fits within existing allocation | [case26b](../reference/examples/case26b_union_field_added_compatible.md) |
| Weaken symbol binding (GLOBAL to WEAK) | Symbol still resolves; interposition semantics relax | [case27](../reference/examples/case27_symbol_binding_weakened.md) |
| Add IFUNC dispatch | Transparent to callers; resolver picks implementation at load time | [case29](../reference/examples/case29_ifunc_transition.md) |
| Outline an inline function (add export) | New symbol appears; callers with inlined copy still work | [case47](../reference/examples/case47_inline_to_outlined.md) |
| Add new global variable | No existing code references it | [case61](../reference/examples/case61_var_added.md) |
| Add field to opaque struct | Callers access through pointers only; layout is hidden | [case62](../reference/examples/case62_type_field_added_compatible.md) |
| Tighten a C++20 concept (still satisfied) | Existing callers compile; no symbol or layout change | [case105](../reference/examples/case105_concept_tightening.md) |
| Graduate `experimental::` → stable (keep old alias) | New stable surface added; old symbols still resolve | [case99](../reference/examples/case99_experimental_graduated.md) |
| Change a **non-public**, scoped internal struct | Not part of the public surface — no consumer can observe it | [case118](../reference/examples/case118_internal_struct_field_added_scoped.md), [case119](../reference/examples/case119_internal_struct_field_removed_scoped.md), [case120](../reference/examples/case120_internal_struct_reordered_scoped.md) |
| Strengthen symbol binding (WEAK → GLOBAL) | Symbol still resolves; the intended definition wins. *Context note:* if a consumer relied on **interposing** the weak symbol, tightening it removes that hook | [case128](../reference/examples/case128_symbol_binding_strengthened.md) |
| Add hardening / deployment metadata (drop exec-stack, add `DT_NEEDED`, change RUNPATH) | Loader still resolves the existing contract; posture improves or is deployment-local. *Context note:* a new `DT_NEEDED` / changed `RUNPATH` can select a different provider or fail on hosts missing the dependency — a deployment concern, not a symbol-contract break | [case136](../reference/examples/case136_executable_stack_removed.md), [case137](../reference/examples/case137_runpath_changed.md), [case138](../reference/examples/case138_needed_added.md) |

> **Scoped to the public surface.** Changes to internal/private types that never
> reach the public header surface are reported as ✅ NO_CHANGE under public-surface
> scoping (cases 118–120). This is why feeding abicheck the real public headers
> matters — it lets the tool tell internal churn apart from a real break.

---

## Breaking Changes (NEVER do in a minor release)

These cause crashes, wrong results, or link failures in pre-compiled consumers.

| Change | What Happens at Runtime | Example |
|--------|------------------------|---------|
| Remove exported symbol | `undefined symbol` on dlopen/startup | [case01](../reference/examples/case01_symbol_removal.md) |
| Change parameter types | Caller passes args in wrong registers/format; garbage or crash | [case02](../reference/examples/case02_param_type_change.md) |
| Change struct layout/size | Stack corruption; reads/writes past allocation boundary | [case07](../reference/examples/case07_struct_layout.md) |
| Change enum member values | Switch/lookup tables use stale integer values; wrong branch taken | [case08](../reference/examples/case08_enum_value_change.md) |
| Reorder virtual methods | Vtable slot mismatch; call dispatches to wrong method silently | [case09](../reference/examples/case09_cpp_vtable.md) |
| Change return type | Caller interprets return register/memory as wrong type | [case10](../reference/examples/case10_return_type.md) |
| Change class size (add members) | `new`/stack allocation undersized; heap corruption, SIGSEGV | [case14](../reference/examples/case14_cpp_class_size.md) |
| Remove enum member | Code referencing removed constant fails at compile time or uses stale value | [case19](../reference/examples/case19_enum_member_removed.md) |
| Change type alignment (`alignas`) | Misaligned access; SIGBUS on strict-alignment architectures | [case42](../reference/examples/case42_type_alignment_changed.md) |
| Change struct packing (`pragma pack`) | Field offsets shift; every member read is wrong | [case56](../reference/examples/case56_struct_packing_changed.md) |
| Change calling convention | Parameters read from wrong registers; total data corruption | [case64](../reference/examples/case64_calling_convention_changed.md) |
| Remove symbol version node | Dynamic linker refuses to load; `version 'FOO_1.0' not found` | [case65](../reference/examples/case65_symbol_version_removed.md) |
| Remove `extern "C"` (language linkage) | Symbol re-mangles (`parse_config` → `_Z12parse_configPKc`); old binaries fail to resolve | [case66](../reference/examples/case66_language_linkage_changed.md) |
| Change TLS variable size/layout | Per-thread storage corruption in existing consumers | [case67](../reference/examples/case67_tls_var_size_changed.md) |
| Add first virtual method to a class | A vptr is prepended; every member shifts by `sizeof(void*)`, `sizeof` grows | [case68](../reference/examples/case68_virtual_method_added.md) |
| Make a trivially-copyable type non-trivial | Pass-by-value flips register↔memory; callee dereferences a value as a pointer | [case69](../reference/examples/case69_trivial_to_nontrivial.md) |
| Change flexible-array element type | `sizeof(header)` matches, but every `data[i]` indexes with the wrong stride | [case70](../reference/examples/case70_flexible_array_member_changed.md) |
| Bump an inline namespace | Every symbol re-mangles (`v1` → `v2`); pre-compiled callers can't resolve | [case71](../reference/examples/case71_inline_namespace_moved.md), [case101](../reference/examples/case101_inline_namespace_version_bumped.md) |
| Change typedef underlying type | Width/representation shifts under callers compiled against the old alias | [case73](../reference/examples/case73_typedef_underlying_changed.md) |
| Leak an internal `detail::` type through a public API | Library symbols look identical; a hidden base/embedded layout shift corrupts consumers | [case74](../reference/examples/case74_detail_base_class_changed.md), [case77](../reference/examples/case77_detail_templated_base_changed.md) |
| Flip libstdc++ dual ABI (`_GLIBCXX_USE_CXX11_ABI`) | `std::string` re-layout; mixed-flavor binaries fail to link or corrupt | [case104](../reference/examples/case104_glibcxx_dual_abi_flip.md) |
| Switch integer model (LP64 → ILP64) | `MKL_INT` 32→64 silently doubles every integer field/argument | [case112](../reference/examples/case112_lp64_ilp64.md) |
| Change an ABI tag (`[abi:cxx11]`) | Symbol re-mangles on the tagged entity; old callers can't resolve | [case113](../reference/examples/case113_abi_tag_changed.md) |
| Migrate `char` family → `char8_t` (C++20) | New distinct type re-mangles signatures and changes overload resolution | [case114](../reference/examples/case114_char8t_migration.md) |
| Change `_BitInt(N)` width (C23) | 64→128 changes size, alignment, and register passing | [case115](../reference/examples/case115_bit_int_width_changed.md) |
| Add `_Atomic` qualifier (C11) | Size/alignment and access semantics change under old callers | [case116](../reference/examples/case116_atomic_qualifier_changed.md) |
| `[[no_unique_address]]` layout overlay | Empty-member overlap shifts subsequent field offsets | [case117](../reference/examples/case117_no_unique_address.md) |
| Return-by-value type became non-trivial (destructor added) | Return convention flips register→hidden-pointer (sret); caller reads a value as a pointer. Mangled name unchanged | [case129](../reference/examples/case129_struct_return_convention.md) |
| Empty base gains a member (EBO lost) | The empty base subobject now takes space; every derived member offset shifts and `sizeof` grows | [case140](../reference/examples/case140_empty_base_optimization_lost.md) |
| Vtable slot count changed (from a **stripped** binary) | `_ZTV` size alone reveals the slot **count** changed (no DWARF) — a slot-renumbering risk: some existing slots may have moved, so old callers can dispatch to the wrong method. Pinpointing *which* slot / whether it was a mid-insert vs. append needs debug info (L1) | [case142](../reference/examples/case142_vtable_slot_count_binary_only.md) |
| Exported data object grew (`symbol_size_changed`) | Consumers sized their copy/relocation to the old `st_size`; a larger object overruns | [case127](../reference/examples/case127_data_object_size_changed.md) |
| Remove a symbol version node | Dynamic linker refuses to load; `version 'FOO_1.0' not found` | [case139](../reference/examples/case139_symbol_version_node_removed.md) |
| Kernel struct field added (BTF) | In-tree/out-of-tree modules baked the old layout; field offsets shift | [case121](../reference/examples/case121_kernel_btf_struct_field_added.md) |

See [Break families and where each is explained](#break-families-and-where-each-is-explained) below for the family-by-family index.

---

## Source-Only Breaks (API_BREAK)

Binary-compatible, but recompilation against new headers fails. Verdict: 🟠 API_BREAK.

| Change | Impact | Example |
|--------|--------|---------|
| Rename enum member (same value) | `LOG_ERR` no longer compiles; binary still uses integer `1` | [case31](../reference/examples/case31_enum_rename.md) |
| Narrow access level (public to private) | Downstream code calling `helper()` gets compile error | [case34](../reference/examples/case34_access_level.md) |
| Make a converting constructor/operator `explicit` | Implicit conversions at call sites stop compiling; ABI unchanged | [case106](../reference/examples/case106_ctor_became_explicit.md) |
| Remove a hidden-friend operator | ADL call sites fail to compile; no symbol was ever exported | [case96](../reference/examples/case96_hidden_friend_removed.md) |
| Remove default parameter | Call sites relying on default fail to compile; ABI unchanged | [case123](../reference/examples/case123_default_argument_removed.md) |
| Mark a class `final` | Downstream code deriving from it stops compiling; ABI unchanged | [case125](../reference/examples/case125_class_became_final.md) |
| Change a public `const`/`constexpr` constant value | Header-baked constant differs from prebuilt binaries; recompilation shifts behavior | [case124](../reference/examples/case124_header_constant_value_changed.md) |
| Remove a public `#define` macro (needs source — L4) | `#ifdef FOO` / `FOO`-using call sites fail to compile; no symbol trace | [case156](../reference/examples/case156_public_macro_removed.md) |
| Remove a header-only `inline` function (L4) | Callers that inlined it still run, but recompiles fail to find it | [case157](../reference/examples/case157_inline_function_removed.md) |
| Remove a public `typedef` (L4) | Every use of the alias stops compiling; binary is untouched | [case158](../reference/examples/case158_public_typedef_removed.md) |
| Rename a Python extension keyword arg (`.pyi` API) | `import`ing callers passing the old kwarg raise `TypeError`; the `.so` is byte-identical | [case163](../reference/examples/case163_python_kwarg_renamed.md) |

---

## Risk Changes (deployment concern)

Binary-compatible, but may break at deployment time. Verdict: 🟡 COMPATIBLE_WITH_RISK.

| Change | Risk | Example |
|--------|------|---------|
| New GLIBC/GLIBCXX version requirement | Binaries won't load on older distros missing the required symbol version | -- (detected via `SYMBOL_VERSION_REQUIRED_ADDED`) |
| Leaked dependency symbol changed | Transitive dependency update shifts symbols your consumers never directly linked | -- |
| `noexcept` removed | Callers compiled assuming `noexcept` omit landing pads; a real throw calls `std::terminate` | [case15](../reference/examples/case15_noexcept_change.md) |
| Drop a CPU-dispatch ISA family | Binaries still load, but the optimized path the consumer expected is gone | [case83](../reference/examples/case83_cpu_dispatch_isa_dropped.md) |
| Weaken RELRO (`FULL` → `PARTIAL`/none) | GOT stays writable; hardening regressed process-wide | [case134](../reference/examples/case134_relro_weakened.md) |
| Drop the stack canary (`-fstack-protector`) | Overflow detection removed from the shipped binary | [case135](../reference/examples/case135_stack_canary_removed.md) |
| Change the TLS access model | Per-thread access sequence changes; risky when mixed with old callers | [case133](../reference/examples/case133_tls_model_flip.md) |

---

## Build-Flag & Toolchain Drift (needs build data — L3)

The flags the library was *built* with are an ABI input the shipped binary barely
shows. Feed `abicheck` the build data (`-p build/` / `scan --depth build`) and it
diffs them. **On their own** — when no public symbol changes — these are 🟡
COMPATIBLE_WITH_RISK: the flag delta *explains* and localizes churn but never
manufactures a break (the authority rule). If the same flag flip actually
**remangles public symbols**, the L0 symbol diff proves a 🔴 BREAKING on its own
(that is why [case104](../reference/examples/case104_glibcxx_dual_abi_flip.md) is classified
BREAKING, not risk). See
[What Each Level Sees § L3](what-each-level-sees.md#level-3-build-data-the-flags-it-was-actually-built-with).

| Flag drift | Why it matters | Example |
|------------|----------------|---------|
| `_GLIBCXX_USE_CXX11_ABI` flipped | libstdc++ string/list ABI changes. Risk-only when no public symbol changes; **🔴 BREAKING** if it re-mangles exported `std::string`/`std::list` signatures | [case104](../reference/examples/case104_glibcxx_dual_abi_flip.md) |
| `-fexceptions` mode flipped | EH tables/landing pads differ across the boundary | [case130](../reference/examples/case130_exceptions_mode_flip.md) |
| `-frtti` mode flipped | `typeinfo`/`dynamic_cast` support diverges | [case131](../reference/examples/case131_rtti_mode_flip.md) |
| Thread-safe statics (`-fthreadsafe-statics`) flipped | Function-local static init guards change | [case132](../reference/examples/case132_threadsafe_statics_flip.md) |
| `-fshort-enums` flipped | Enum underlying size changes → struct layout shifts | [case152](../reference/examples/case152_enum_size_flag_flip.md) |
| `-fpack-struct` / packing mode flipped | Every field offset moves | [case153](../reference/examples/case153_struct_packing_flip.md) |
| LTO mode flipped | Cross-TU inlining/visibility interactions change | [case154](../reference/examples/case154_lto_mode_flip.md) |
| `char` signedness flipped | `char`-typed values reinterpret sign | [case155](../reference/examples/case155_char_signedness_flip.md) |

---

## Intra-Version Hygiene (audit — no baseline needed)

`abicheck scan libfoo.so` (with no `--against`) lints a *single* build for bad
ABI hygiene — problems you can see without a previous version. Absence of
`--against` is already a one-build audit; there is no separate `--audit` flag.
All 🟡 COMPATIBLE_WITH_RISK.

| Finding | What it flags | Example |
|---------|---------------|---------|
| Accidental export | Symbol exported but in no public header | [case143](../reference/examples/case143_audit_accidental_export.md) |
| Private-header leak | Public API pulls an unshipped header | [case144](../reference/examples/case144_audit_private_header_leak.md) |
| Unversioned export | Export with no version node though a scheme exists | [case145](../reference/examples/case145_audit_unversioned_export.md) |
| Exported RTTI for internal type | `_ZTI`/`_ZTV` leaked for a private-header type | [case146](../reference/examples/case146_audit_rtti_for_internal.md) |

---

## Cross-Source & Reachability (two sources beat one)

Findings that surface only when abicheck crosschecks two sources, or derives the
L5 reachability graph. A conflict invisible to any single source resolves by
comparing them.

| Finding | What it catches | Example |
|---------|-----------------|---------|
| Header ↔ build mismatch | Headers parsed without the build's ABI flags → wrong recorded layout | [case148](../reference/examples/case148_xcheck_header_build_mismatch.md) |
| ODR type variant | One type, two per-TU layouts | [case149](../reference/examples/case149_xcheck_odr_variant.md) |
| Export ↔ decl mismatch | Exported-not-public / public-not-exported, both directions | [case150](../reference/examples/case150_xcheck_export_public_pair.md) |
| Public API gained an internal dependency | A public entry newly reaches a non-public entity through the L5 graph — a *risk signal* (later changes to the internal become hidden behavioral risk), not a proven ABI dependency. It is only a hard break if it surfaces via a public header, inline body, or link-time symbol | [case160](../reference/examples/case160_public_api_internal_dep_added.md) |
| Exported symbol's declaring file moved | Stable symbol, but its owning header changed (L5 graph) | [case162](../reference/examples/case162_symbol_source_owner_changed.md) |

---

## Break families and where each is explained

Every detected change maps to one of these families, each pointing to the
Learning Series page that explains its mechanism. The verdict column shows
the typical classification; the exact verdict per fixture lives in
`examples/ground_truth.json` and the
[Examples Encyclopedia](../reference/examples/index.md). "mixed" means the
verdict is case-dependent.

| Family | Representative cases | Typical verdict | Explained in |
|--------|---------------------|-----------------|--------------|
| Symbol/function removal & rename | [01](../reference/examples/case01_symbol_removal.md), [12](../reference/examples/case12_function_removed.md), [58](../reference/examples/case58_var_removed.md), [66](../reference/examples/case66_language_linkage_changed.md) | 🔴 BREAKING | [Part 2](abi-series/02-symbol-contracts.md) |
| Signature changes (params, return, pointer level) | [02](../reference/examples/case02_param_type_change.md), [10](../reference/examples/case10_return_type.md), [33](../reference/examples/case33_pointer_level.md), [46](../reference/examples/case46_pointer_chain_type_change.md) | 🔴 BREAKING | [Part 2](abi-series/02-symbol-contracts.md) |
| Global variable type/qualifier/removal | [11](../reference/examples/case11_global_var_type.md), [39](../reference/examples/case39_var_const.md), [58](../reference/examples/case58_var_removed.md) | 🔴 BREAKING | [Part 2](abi-series/02-symbol-contracts.md) |
| Struct/class layout, alignment & packing | [07](../reference/examples/case07_struct_layout.md), [14](../reference/examples/case14_cpp_class_size.md), [40](../reference/examples/case40_field_layout.md), [42](../reference/examples/case42_type_alignment_changed.md), [43](../reference/examples/case43_base_class_member_added.md), [56](../reference/examples/case56_struct_packing_changed.md), [117](../reference/examples/case117_no_unique_address.md) | 🔴 BREAKING | [Part 3](abi-series/03-type-layout.md) |
| Enum value/underlying changes | [08](../reference/examples/case08_enum_value_change.md), [19](../reference/examples/case19_enum_member_removed.md), [20](../reference/examples/case20_enum_member_value_changed.md), [57](../reference/examples/case57_enum_underlying_size_changed.md) | 🔴 BREAKING | [Part 3](abi-series/03-type-layout.md) |
| Union layout | [24](../reference/examples/case24_union_field_removed.md), [26](../reference/examples/case26_union_field_added.md) (grows) · [26b](../reference/examples/case26b_union_field_added_compatible.md) (no growth) | mixed — 🔴 if size grows, else 🟢 | [Part 3](abi-series/03-type-layout.md) |
| C++ vtable & virtual methods | [09](../reference/examples/case09_cpp_vtable.md), [23](../reference/examples/case23_pure_virtual_added.md), [38](../reference/examples/case38_virtual_methods.md), [68](../reference/examples/case68_virtual_method_added.md), [72](../reference/examples/case72_covariant_return_changed.md) | 🔴 BREAKING | [Part 4](abi-series/04-cpp-abi.md) |
| C++ qualifiers, mangling & ABI tags | [21](../reference/examples/case21_method_became_static.md), [22](../reference/examples/case22_method_const_changed.md), [30](../reference/examples/case30_field_qualifiers.md), [71](../reference/examples/case71_inline_namespace_moved.md), [86](../reference/examples/case86_tag_struct_renamed.md), [101](../reference/examples/case101_inline_namespace_version_bumped.md), [113](../reference/examples/case113_abi_tag_changed.md) | mixed — 🔴 BREAKING or 🟠 API_BREAK | [Part 4](abi-series/04-cpp-abi.md) |
| Trivial → non-trivial (calling convention) | [64](../reference/examples/case64_calling_convention_changed.md), [69](../reference/examples/case69_trivial_to_nontrivial.md) | 🔴 BREAKING | [Part 4](abi-series/04-cpp-abi.md) |
| Templates, inline & ODR | [16](../reference/examples/case16_inline_to_non_inline.md), [17](../reference/examples/case17_template_abi.md), [47](../reference/examples/case47_inline_to_outlined.md), [59](../reference/examples/case59_func_became_inline.md), [79](../reference/examples/case79_missing_template_instantiation.md), [85](../reference/examples/case85_internal_template_signature_changed.md), [87](../reference/examples/case87_default_template_arg_changed.md) | mixed — 🔴 BREAKING or 🟢 COMPATIBLE | [Part 4](abi-series/04-cpp-abi.md) |
| Modern C/C++ contract shifts (char8_t, _BitInt, _Atomic, concepts) | [105](../reference/examples/case105_concept_tightening.md), [114](../reference/examples/case114_char8t_migration.md), [115](../reference/examples/case115_bit_int_width_changed.md), [116](../reference/examples/case116_atomic_qualifier_changed.md) | mixed — 🔴 BREAKING or 🟢 COMPATIBLE | [Modern C/C++ and Toolchain ABI Hazards](modern-cpp-toolchain-hazards.md) |
| ELF/linker metadata (SONAME, visibility, versioning, RPATH, TLS) | [05](../reference/examples/case05_soname.md), [06](../reference/examples/case06_visibility.md), [13](../reference/examples/case13_symbol_versioning.md), [49](../reference/examples/case49_executable_stack.md), [51](../reference/examples/case51_protected_visibility.md), [52](../reference/examples/case52_rpath_leak.md), [65](../reference/examples/case65_symbol_version_removed.md), [67](../reference/examples/case67_tls_var_size_changed.md) | mixed — 🔴 BREAKING or 🟢 COMPATIBLE | [Part 5](abi-series/05-linker-elf.md) |
| Transitive/dependency & `detail::` leaks | [18](../reference/examples/case18_dependency_leak.md), [48](../reference/examples/case48_leaf_struct_through_pointer.md), [74](../reference/examples/case74_detail_base_class_changed.md), [75](../reference/examples/case75_detail_embedded_by_value.md), [76](../reference/examples/case76_detail_pimpl_vtable_changed.md), [77](../reference/examples/case77_detail_templated_base_changed.md), [80](../reference/examples/case80_pimpl_shared_to_unique.md), [97](../reference/examples/case97_api_depends_on_consumer_env.md), [104](../reference/examples/case104_glibcxx_dual_abi_flip.md), [112](../reference/examples/case112_lp64_ilp64.md) | 🔴 BREAKING | [Part 6](abi-series/06-transitive-breaks.md) |
| Source-only / API-level (rename, access, explicit, default args, hidden friends) | [31](../reference/examples/case31_enum_rename.md), [34](../reference/examples/case34_access_level.md), [96](../reference/examples/case96_hidden_friend_removed.md), [106](../reference/examples/case106_ctor_became_explicit.md), [123](../reference/examples/case123_default_argument_removed.md), [124](../reference/examples/case124_header_constant_value_changed.md) | 🟠 API_BREAK | [Part 6 §Source-only API breaks](abi-series/06-transitive-breaks.md#source-only-api-breaks-binary-identical) |
| Deployment risk (noexcept, ISA dispatch, version-require) | [15](../reference/examples/case15_noexcept_change.md), [83](../reference/examples/case83_cpu_dispatch_isa_dropped.md) | 🟡 COMPATIBLE_WITH_RISK | [Part 4](abi-series/04-cpp-abi.md) |
| Dependency / runtime floors & environment drift (glibc/libstdc++ floor, DT_RELR, RPATH type) | [170](../reference/examples/case170_env_runtime_floor_raised.md) | 🟡 COMPATIBLE_WITH_RISK — 🔴 or 🟢 once a floor is declared; the 32-bit time64/LFS flip (`time64_abi_changed`) is always 🔴 BREAKING | [Dependency & Runtime Floors](dependency-floors.md) + [Environment & Toolchain Drift](environment-drift.md) |
| Compatible additions & quality signals | [03](../reference/examples/case03_compat_addition.md), [25](../reference/examples/case25_enum_member_added.md), [26b](../reference/examples/case26b_union_field_added_compatible.md), [27](../reference/examples/case27_symbol_binding_weakened.md), [29](../reference/examples/case29_ifunc_transition.md), [61](../reference/examples/case61_var_added.md), [62](../reference/examples/case62_type_field_added_compatible.md), [99](../reference/examples/case99_experimental_graduated.md) | 🟢 COMPATIBLE | [Part 7](abi-series/07-designing-for-stability.md) |
| Scoped/non-public internal changes | [118](../reference/examples/case118_internal_struct_field_added_scoped.md), [119](../reference/examples/case119_internal_struct_field_removed_scoped.md), [120](../reference/examples/case120_internal_struct_reordered_scoped.md) | ✅ NO_CHANGE | [Part 6](abi-series/06-transitive-breaks.md) |
| Security-hardening & deployment metadata (RELRO, canary, exec-stack, RUNPATH, `DT_NEEDED`, TLS model, symbol binding) — artifact/linker facts (L0/L3) | [128](../reference/examples/case128_symbol_binding_strengthened.md), [133](../reference/examples/case133_tls_model_flip.md), [134](../reference/examples/case134_relro_weakened.md), [135](../reference/examples/case135_stack_canary_removed.md), [136](../reference/examples/case136_executable_stack_removed.md), [137](../reference/examples/case137_runpath_changed.md), [138](../reference/examples/case138_needed_added.md) | mixed — 🟡 risk (RELRO/canary/TLS) or 🟢 COMPATIBLE (exec-stack/RUNPATH/`DT_NEEDED`/binding) | [Part 5](abi-series/05-linker-elf.md) |
| **Build-flag & toolchain drift (L3)** — the flags the library was *built* with, as a finding on their own | [130](../reference/examples/case130_exceptions_mode_flip.md), [131](../reference/examples/case131_rtti_mode_flip.md), [132](../reference/examples/case132_threadsafe_statics_flip.md) | 🟡 COMPATIBLE_WITH_RISK | [Source & Build Data](build-source-data.md) |
| **Source-only bodies & macros (L4)** — `#define` macro values, inline/template/`constexpr` **bodies**, uninstantiated templates (none header-reachable) | [122](../reference/examples/case122_template_signature_uninstantiated.md) *(the documented `NO_CHANGE` gap — even L4 can't close it; a detected macro/body change is 🟠 API_BREAK / 🟡 risk)* | mixed — 🟠 API_BREAK / 🟡 risk, or ✅ NO_CHANGE (residual gap) | [Source & Build Data](build-source-data.md) |
| **Intra-version ABI hygiene / audit** — accidental export, private-header leak, unversioned export, RTTI leak (no baseline needed) | [143](../reference/examples/case143_audit_accidental_export.md), [144](../reference/examples/case144_audit_private_header_leak.md), [145](../reference/examples/case145_audit_unversioned_export.md), [146](../reference/examples/case146_audit_rtti_for_internal.md) | 🟡 risk | [§ source scan](evidence-and-detectability.md#the-depth-dial-how-much-evidence-to-collect) |
| **Cross-source validation** — one fact, two sources: header↔build mismatch, ODR variant, export↔decl pair | [148](../reference/examples/case148_xcheck_header_build_mismatch.md), [149](../reference/examples/case149_xcheck_odr_variant.md), [150](../reference/examples/case150_xcheck_export_public_pair.md), [151](../reference/examples/case151_xcheck_provider_matrix.md) | mixed — 🟠 API_BREAK or 🟡 risk | [§ source scan](evidence-and-detectability.md#the-depth-dial-how-much-evidence-to-collect) |

The **security-hardening & deployment** row is *artifact/linker* coverage
(L0/L3, mixed verdicts — an object-size change like
[case127](../reference/examples/case127_data_object_size_changed.md) is a
separate 🔴 BREAKING layout finding, not a hardening risk). The **last four
rows** are the families a plain two-version `compare` of L0–L2 artifacts does
**not** produce on its own — build-flag drift needs the build data (L3),
source-only bodies & macros need the sources (L4), and the intra-version
hygiene and cross-source families need the scan's cross-source pass. All of
them are walked in [What Each Level Sees](what-each-level-sees.md).

---

## Quality Warnings

No immediate breakage, but these compromise the ABI contract or security posture. abicheck flags these as 🟡 COMPATIBLE quality checks (`SONAME_MISSING`, `VISIBILITY_LEAK`, `EXECUTABLE_STACK`, `RPATH_CHANGED`). Fixing them later often causes 🔴 BREAKING changes.

| Warning | Why It Matters | Example |
|---------|---------------|---------|
| Missing SONAME | Consumers record bare filename; library versioning breaks | [case05](../reference/examples/case05_soname.md) |
| Visibility leak (no `-fvisibility=hidden`) | Internal symbols become public ABI surface you must maintain forever | [case06](../reference/examples/case06_visibility.md) (fixing later = BREAKING) |
| Executable stack (`GNU_STACK RWX`) | Disables NX protection process-wide; trivial exploit target | [case49](../reference/examples/case49_executable_stack.md) |
| RPATH leak (hardcoded build path) | Library only works on the build machine; deployment fails everywhere else | [case52](../reference/examples/case52_rpath_leak.md) |
| Namespace pollution (generic names) | Unprefixed symbols like `init()` collide across libraries | [case53](../reference/examples/case53_namespace_pollution.md) (fixing later = BREAKING) |

---

## Prevention Patterns

| Pattern | Protects Against | How |
|---------|-----------------|-----|
| `-fvisibility=hidden` + explicit exports | Visibility leaks, accidental ABI surface | Only annotated symbols enter `.dynsym` |
| Pimpl / opaque handles | Struct layout breaks | Callers see `T*` only; fields are private |
| Symbol versioning (version script) | Symbol removal, version node breaks | Map file controls what's exported per version |
| SONAME with major-version bump | All breaking changes | `libfoo.so.1` to `libfoo.so.2` on ABI break |
| Reserved fields in public structs | Future field additions | `void *_reserved[4]` absorbs growth without size change |
| CI ABI check with abicheck | All of the above | Catches regressions before merge (see below) |

---

## CI One-Liner

```bash
abicheck compare libfoo.so.old libfoo.so.new \
  --header old=include/old/foo.h \
  --header new=include/new/foo.h \
  --policy strict_abi
```

Exits non-zero on any 🔴 BREAKING or 🟠 API_BREAK finding. Add `--suppress suppressions.yaml` to allowlist known acceptable changes. See [CLI Usage](../use/cli-usage.md) and [Policies](../use/policies.md) for options.

---

## Verdict legend

🔴 `BREAKING` · 🟠 `API_BREAK` · 🟡 `COMPATIBLE_WITH_RISK` / `COMPATIBLE` (quality) · 🟢 `COMPATIBLE` (addition) · ✅ `NO_CHANGE` — what each means and how it maps to an exit code is owned by [Verdicts](verdicts.md).

All calibration cases: [Compatibility Catalog](../reference/examples/index.md).

---

**Ladder:** ← [How a Break Shows Up](how-a-break-shows-up.md) · Step 1 · Start Here · [Glossary](abi-series/glossary.md) →
