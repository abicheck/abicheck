# ABI Cheat Sheet

Quick-reference card for shared-library maintainers. Scannable in 2 minutes.

For deeper explanations see [ABI/API Handling & Recommendations](abi-api-handling.md) and [Verdicts](verdicts.md).

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
| Change a **non-public**, scoped internal struct | Not part of the public surface — no consumer can observe it | [case118](../examples/case118_internal_struct_field_added_scoped.md), [case120](../examples/case120_internal_struct_reordered_scoped.md) |

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
| Remove default parameter | Call sites relying on default fail to compile; ABI unchanged | -- |

---

## Risk Changes (deployment concern)

Binary-compatible, but may break at deployment time. Verdict: 🟡 COMPATIBLE_WITH_RISK.

| Change | Risk | Example |
|--------|------|---------|
| New GLIBC/GLIBCXX version requirement | Binaries won't load on older distros missing the required symbol version | -- (detected via `SYMBOL_VERSION_REQUIRED_ADDED`) |
| Leaked dependency symbol changed | Transitive dependency update shifts symbols your consumers never directly linked | -- |
| `noexcept` removed | Callers compiled assuming `noexcept` omit landing pads; a real throw calls `std::terminate` | [case15](../examples/case15_noexcept_change.md) |
| Drop a CPU-dispatch ISA family | Binaries still load, but the optimized path the consumer expected is gone | [case83](../examples/case83_cpu_dispatch_isa_dropped.md) |

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
  --old-header include/old/foo.h \
  --new-header include/new/foo.h \
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

Full verdict semantics: [Verdicts](verdicts.md) | All example cases: [Scenario Catalog](https://github.com/napetrov/abicheck/tree/main/examples)
