# ABI Cheat Sheet

Quick-reference card for shared-library maintainers. Scannable in 2 minutes.

For deeper explanations see [ABI/API Handling & Recommendations](abi-api-handling.md) and [Verdicts](verdicts.md). To see *which evidence level* proves each row below (symbols → debug → headers → build → sources), see [What Each Level Sees](what-each-level-sees.md).

---

## Safe Changes (COMPATIBLE)

These changes preserve binary compatibility. Existing consumers continue to work without recompilation.

| Change | Why Safe | Example |
|--------|----------|---------|
| Add new exported function | Existing binaries never reference it; linker ignores unknown symbols | [case03](../examples/case03_compat_addition.md) |
| Append enum member (end, no value shift) | Compiled binaries use integer values; existing values unchanged | [case25](../examples/case25_enum_member_added.md) |
| Add union field without growing size | Union size = max(fields); fits within existing allocation | [case26b](../examples/case26b_union_field_added_compatible.md) |
| Weaken symbol binding (GLOBAL to WEAK) | Symbol still resolves; interposition semantics relax | [case27](../examples/case27_symbol_binding_weakened.md) |
| Add IFUNC dispatch | Transparent to callers; resolver picks implementation at load time | [case29](../examples/case29_ifunc_transition.md) |
| Outline an inline function (add export) | New symbol appears; callers with inlined copy still work | [case47](../examples/case47_inline_to_outlined.md) |
| Add new global variable | No existing code references it | [case61](../examples/case61_var_added.md) |
| Add field to opaque struct | Callers access through pointers only; layout is hidden | [case62](../examples/case62_type_field_added_compatible.md) |
| Tighten a C++20 concept (still satisfied) | Existing callers compile; no symbol or layout change | [case105](../examples/case105_concept_tightening.md) |
| Graduate `experimental::` → stable (keep old alias) | New stable surface added; old symbols still resolve | [case99](../examples/case99_experimental_graduated.md) |
| Change a **non-public**, scoped internal struct | Not part of the public surface — no consumer can observe it | [case118](../examples/case118_internal_struct_field_added_scoped.md), [case119](../examples/case119_internal_struct_field_removed_scoped.md), [case120](../examples/case120_internal_struct_reordered_scoped.md) |
| Strengthen symbol binding (WEAK → GLOBAL) | Symbol still resolves; the intended definition wins. *Context note:* if a consumer relied on **interposing** the weak symbol, tightening it removes that hook | [case128](../examples/case128_symbol_binding_strengthened.md) |
| Add hardening / deployment metadata (drop exec-stack, add `DT_NEEDED`, change RUNPATH) | Loader still resolves the existing contract; posture improves or is deployment-local. *Context note:* a new `DT_NEEDED` / changed `RUNPATH` can select a different provider or fail on hosts missing the dependency — a deployment concern, not a symbol-contract break | [case136](../examples/case136_executable_stack_removed.md), [case137](../examples/case137_runpath_changed.md), [case138](../examples/case138_needed_added.md) |

> **Scoped to the public surface.** Changes to internal/private types that never
> reach the public header surface are reported as ✅ NO_CHANGE under public-surface
> scoping (cases 118–120). This is why feeding abicheck the real public headers
> matters — it lets the tool tell internal churn apart from a real break.

---

## Breaking Changes (NEVER do in a minor release)

These cause crashes, wrong results, or link failures in pre-compiled consumers.

| Change | What Happens at Runtime | Example |
|--------|------------------------|---------|
| Remove exported symbol | `undefined symbol` on dlopen/startup | [case01](../examples/case01_symbol_removal.md) |
| Change parameter types | Caller passes args in wrong registers/format; garbage or crash | [case02](../examples/case02_param_type_change.md) |
| Change struct layout/size | Stack corruption; reads/writes past allocation boundary | [case07](../examples/case07_struct_layout.md) |
| Change enum member values | Switch/lookup tables use stale integer values; wrong branch taken | [case08](../examples/case08_enum_value_change.md) |
| Reorder virtual methods | Vtable slot mismatch; call dispatches to wrong method silently | [case09](../examples/case09_cpp_vtable.md) |
| Change return type | Caller interprets return register/memory as wrong type | [case10](../examples/case10_return_type.md) |
| Change class size (add members) | `new`/stack allocation undersized; heap corruption, SIGSEGV | [case14](../examples/case14_cpp_class_size.md) |
| Remove enum member | Code referencing removed constant fails at compile time or uses stale value | [case19](../examples/case19_enum_member_removed.md) |
| Change type alignment (`alignas`) | Misaligned access; SIGBUS on strict-alignment architectures | [case42](../examples/case42_type_alignment_changed.md) |
| Change struct packing (`pragma pack`) | Field offsets shift; every member read is wrong | [case56](../examples/case56_struct_packing_changed.md) |
| Change calling convention | Parameters read from wrong registers; total data corruption | [case64](../examples/case64_calling_convention_changed.md) |
| Remove symbol version node | Dynamic linker refuses to load; `version 'FOO_1.0' not found` | [case65](../examples/case65_symbol_version_removed.md) |
| Remove `extern "C"` (language linkage) | Symbol re-mangles (`parse_config` → `_Z12parse_configPKc`); old binaries fail to resolve | [case66](../examples/case66_language_linkage_changed.md) |
| Change TLS variable size/layout | Per-thread storage corruption in existing consumers | [case67](../examples/case67_tls_var_size_changed.md) |
| Add first virtual method to a class | A vptr is prepended; every member shifts by `sizeof(void*)`, `sizeof` grows | [case68](../examples/case68_virtual_method_added.md) |
| Make a trivially-copyable type non-trivial | Pass-by-value flips register↔memory; callee dereferences a value as a pointer | [case69](../examples/case69_trivial_to_nontrivial.md) |
| Change flexible-array element type | `sizeof(header)` matches, but every `data[i]` indexes with the wrong stride | [case70](../examples/case70_flexible_array_member_changed.md) |
| Bump an inline namespace | Every symbol re-mangles (`v1` → `v2`); pre-compiled callers can't resolve | [case71](../examples/case71_inline_namespace_moved.md), [case101](../examples/case101_inline_namespace_version_bumped.md) |
| Change typedef underlying type | Width/representation shifts under callers compiled against the old alias | [case73](../examples/case73_typedef_underlying_changed.md) |
| Leak an internal `detail::` type through a public API | Library symbols look identical; a hidden base/embedded layout shift corrupts consumers | [case74](../examples/case74_detail_base_class_changed.md), [case77](../examples/case77_detail_templated_base_changed.md) |
| Flip libstdc++ dual ABI (`_GLIBCXX_USE_CXX11_ABI`) | `std::string` re-layout; mixed-flavor binaries fail to link or corrupt | [case104](../examples/case104_glibcxx_dual_abi_flip.md) |
| Switch integer model (LP64 → ILP64) | `MKL_INT` 32→64 silently doubles every integer field/argument | [case112](../examples/case112_lp64_ilp64.md) |
| Change an ABI tag (`[abi:cxx11]`) | Symbol re-mangles on the tagged entity; old callers can't resolve | [case113](../examples/case113_abi_tag_changed.md) |
| Migrate `char` family → `char8_t` (C++20) | New distinct type re-mangles signatures and changes overload resolution | [case114](../examples/case114_char8t_migration.md) |
| Change `_BitInt(N)` width (C23) | 64→128 changes size, alignment, and register passing | [case115](../examples/case115_bit_int_width_changed.md) |
| Add `_Atomic` qualifier (C11) | Size/alignment and access semantics change under old callers | [case116](../examples/case116_atomic_qualifier_changed.md) |
| `[[no_unique_address]]` layout overlay | Empty-member overlap shifts subsequent field offsets | [case117](../examples/case117_no_unique_address.md) |
| Return-by-value type became non-trivial (destructor added) | Return convention flips register→hidden-pointer (sret); caller reads a value as a pointer. Mangled name unchanged | [case129](../examples/case129_struct_return_convention.md) |
| Empty base gains a member (EBO lost) | The empty base subobject now takes space; every derived member offset shifts and `sizeof` grows | [case140](../examples/case140_empty_base_optimization_lost.md) |
| Vtable slot count changed (from a **stripped** binary) | `_ZTV` size alone reveals the slot **count** changed (no DWARF) — a slot-renumbering risk: some existing slots may have moved, so old callers can dispatch to the wrong method. Pinpointing *which* slot / whether it was a mid-insert vs. append needs debug info (L1) | [case142](../examples/case142_vtable_slot_count_binary_only.md) |
| Exported data object grew (`symbol_size_changed`) | Consumers sized their copy/relocation to the old `st_size`; a larger object overruns | [case127](../examples/case127_data_object_size_changed.md) |
| Remove a symbol version node | Dynamic linker refuses to load; `version 'FOO_1.0' not found` | [case139](../examples/case139_symbol_version_node_removed.md) |
| Kernel struct field added (BTF) | In-tree/out-of-tree modules baked the old layout; field offsets shift | [case121](../examples/case121_kernel_btf_struct_field_added.md) |

See the full breaking catalog in [ABI/API Handling & Recommendations](abi-api-handling.md).

---

## Source-Only Breaks (API_BREAK)

Binary-compatible, but recompilation against new headers fails. Verdict: 🟠 API_BREAK.

| Change | Impact | Example |
|--------|--------|---------|
| Rename enum member (same value) | `LOG_ERR` no longer compiles; binary still uses integer `1` | [case31](../examples/case31_enum_rename.md) |
| Narrow access level (public to private) | Downstream code calling `helper()` gets compile error | [case34](../examples/case34_access_level.md) |
| Make a converting constructor/operator `explicit` | Implicit conversions at call sites stop compiling; ABI unchanged | [case106](../examples/case106_ctor_became_explicit.md) |
| Remove a hidden-friend operator | ADL call sites fail to compile; no symbol was ever exported | [case96](../examples/case96_hidden_friend_removed.md) |
| Remove default parameter | Call sites relying on default fail to compile; ABI unchanged | [case123](../examples/case123_default_argument_removed.md) |
| Mark a class `final` | Downstream code deriving from it stops compiling; ABI unchanged | [case125](../examples/case125_class_became_final.md) |
| Change a public `const`/`constexpr` constant value | Header-baked constant differs from prebuilt binaries; recompilation shifts behavior | [case124](../examples/case124_header_constant_value_changed.md) |
| Remove a public `#define` macro (needs source — L4) | `#ifdef FOO` / `FOO`-using call sites fail to compile; no symbol trace | [case156](../examples/case156_public_macro_removed.md) |
| Remove a header-only `inline` function (L4) | Callers that inlined it still run, but recompiles fail to find it | [case157](../examples/case157_inline_function_removed.md) |
| Remove a public `typedef` (L4) | Every use of the alias stops compiling; binary is untouched | [case158](../examples/case158_public_typedef_removed.md) |
| Rename a Python extension keyword arg (`.pyi` API) | `import`ing callers passing the old kwarg raise `TypeError`; the `.so` is byte-identical | [case163](../examples/case163_python_kwarg_renamed.md) |

---

## Risk Changes (deployment concern)

Binary-compatible, but may break at deployment time. Verdict: 🟡 COMPATIBLE_WITH_RISK.

| Change | Risk | Example |
|--------|------|---------|
| New GLIBC/GLIBCXX version requirement | Binaries won't load on older distros missing the required symbol version | -- (detected via `SYMBOL_VERSION_REQUIRED_ADDED`) |
| Leaked dependency symbol changed | Transitive dependency update shifts symbols your consumers never directly linked | -- |
| `noexcept` removed | Callers compiled assuming `noexcept` omit landing pads; a real throw calls `std::terminate` | [case15](../examples/case15_noexcept_change.md) |
| Drop a CPU-dispatch ISA family | Binaries still load, but the optimized path the consumer expected is gone | [case83](../examples/case83_cpu_dispatch_isa_dropped.md) |
| Weaken RELRO (`FULL` → `PARTIAL`/none) | GOT stays writable; hardening regressed process-wide | [case134](../examples/case134_relro_weakened.md) |
| Drop the stack canary (`-fstack-protector`) | Overflow detection removed from the shipped binary | [case135](../examples/case135_stack_canary_removed.md) |
| Change the TLS access model | Per-thread access sequence changes; risky when mixed with old callers | [case133](../examples/case133_tls_model_flip.md) |

---

## Build-Flag & Toolchain Drift (needs build data — L3)

The flags the library was *built* with are an ABI input the shipped binary barely
shows. Feed `abicheck` the build data (`-p build/` / `scan --depth build`) and it
diffs them. **On their own** — when no public symbol changes — these are 🟡
COMPATIBLE_WITH_RISK: the flag delta *explains* and localizes churn but never
manufactures a break (the authority rule). If the same flag flip actually
**remangles public symbols**, the L0 symbol diff proves a 🔴 BREAKING on its own
(that is why [case104](../examples/case104_glibcxx_dual_abi_flip.md) is classified
BREAKING, not risk). See
[What Each Level Sees § L3](what-each-level-sees.md#level-3-build-data-the-flags-it-was-actually-built-with).

| Flag drift | Why it matters | Example |
|------------|----------------|---------|
| `_GLIBCXX_USE_CXX11_ABI` flipped | libstdc++ string/list ABI changes. Risk-only when no public symbol changes; **🔴 BREAKING** if it re-mangles exported `std::string`/`std::list` signatures | [case104](../examples/case104_glibcxx_dual_abi_flip.md) |
| `-fexceptions` mode flipped | EH tables/landing pads differ across the boundary | [case130](../examples/case130_exceptions_mode_flip.md) |
| `-frtti` mode flipped | `typeinfo`/`dynamic_cast` support diverges | [case131](../examples/case131_rtti_mode_flip.md) |
| Thread-safe statics (`-fthreadsafe-statics`) flipped | Function-local static init guards change | [case132](../examples/case132_threadsafe_statics_flip.md) |
| `-fshort-enums` flipped | Enum underlying size changes → struct layout shifts | [case152](../examples/case152_enum_size_flag_flip.md) |
| `-fpack-struct` / packing mode flipped | Every field offset moves | [case153](../examples/case153_struct_packing_flip.md) |
| LTO mode flipped | Cross-TU inlining/visibility interactions change | [case154](../examples/case154_lto_mode_flip.md) |
| `char` signedness flipped | `char`-typed values reinterpret sign | [case155](../examples/case155_char_signedness_flip.md) |

---

## Intra-Version Hygiene (audit — no baseline needed)

`abicheck scan --audit` lints a *single* build for bad ABI hygiene — problems you
can see without a previous version. All 🟡 COMPATIBLE_WITH_RISK.

| Finding | What it flags | Example |
|---------|---------------|---------|
| Accidental export | Symbol exported but in no public header | [case143](../examples/case143_audit_accidental_export.md) |
| Private-header leak | Public API pulls an unshipped header | [case144](../examples/case144_audit_private_header_leak.md) |
| Unversioned export | Export with no version node though a scheme exists | [case145](../examples/case145_audit_unversioned_export.md) |
| Exported RTTI for internal type | `_ZTI`/`_ZTV` leaked for a private-header type | [case146](../examples/case146_audit_rtti_for_internal.md) |

---

## Cross-Source & Reachability (two sources beat one)

Findings that surface only when abicheck crosschecks two sources, or derives the
L5 reachability graph. A conflict invisible to any single source resolves by
comparing them.

| Finding | What it catches | Example |
|---------|-----------------|---------|
| Header ↔ build mismatch | Headers parsed without the build's ABI flags → wrong recorded layout | [case148](../examples/case148_xcheck_header_build_mismatch.md) |
| ODR type variant | One type, two per-TU layouts | [case149](../examples/case149_xcheck_odr_variant.md) |
| Export ↔ decl mismatch | Exported-not-public / public-not-exported, both directions | [case150](../examples/case150_xcheck_export_public_pair.md) |
| Public API gained an internal dependency | A public entry newly reaches a non-public entity through the L5 graph — a *risk signal* (later changes to the internal become hidden behavioral risk), not a proven ABI dependency. It is only a hard break if it surfaces via a public header, inline body, or link-time symbol | [case160](../examples/case160_public_api_internal_dep_added.md) |
| Exported symbol's declaring file moved | Stable symbol, but its owning header changed (L5 graph) | [case162](../examples/case162_symbol_source_owner_changed.md) |

---

## Quality Warnings

No immediate breakage, but these compromise the ABI contract or security posture. abicheck flags these as 🟡 COMPATIBLE quality checks (`SONAME_MISSING`, `VISIBILITY_LEAK`, `EXECUTABLE_STACK`, `RPATH_CHANGED`). Fixing them later often causes 🔴 BREAKING changes.

| Warning | Why It Matters | Example |
|---------|---------------|---------|
| Missing SONAME | Consumers record bare filename; library versioning breaks | [case05](../examples/case05_soname.md) |
| Visibility leak (no `-fvisibility=hidden`) | Internal symbols become public ABI surface you must maintain forever | [case06](../examples/case06_visibility.md) (fixing later = BREAKING) |
| Executable stack (`GNU_STACK RWX`) | Disables NX protection process-wide; trivial exploit target | [case49](../examples/case49_executable_stack.md) |
| RPATH leak (hardcoded build path) | Library only works on the build machine; deployment fails everywhere else | [case52](../examples/case52_rpath_leak.md) |
| Namespace pollution (generic names) | Unprefixed symbols like `init()` collide across libraries | [case53](../examples/case53_namespace_pollution.md) (fixing later = BREAKING) |

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

Exits non-zero on any 🔴 BREAKING or 🟠 API_BREAK finding. Add `--suppress suppressions.yaml` to allowlist known acceptable changes. See [CLI Usage](../user-guide/cli-usage.md) and [Policies](../user-guide/policies.md) for options.

---

## Verdict Quick Reference

| Icon | Verdict | Meaning |
|------|---------|---------|
| 🔴 | BREAKING | Binary incompatible -- consumers crash or misbehave |
| 🟠 | API_BREAK | Source incompatible -- recompilation fails, binary works |
| 🟡 | COMPATIBLE_WITH_RISK | Binary works, deployment risk present |
| 🟡 | COMPATIBLE (quality) | Binary works, bad practice detected |
| 🟢 | COMPATIBLE (addition) | New API surface, fully backward-compatible |
| ✅ | NO_CHANGE | Identical ABI |

Full verdict semantics: [Verdicts](verdicts.md) | All example cases: [Scenario Catalog](https://github.com/abicheck/abicheck/tree/main/examples)
