# ABI Cheat Sheet

Quick-reference card for shared-library maintainers. Scannable in 2 minutes.

For deeper explanations see [ABI Breaks Explained](abi-breaks-explained.md) and [Verdicts](verdicts.md).

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

See the full breaking catalog in [Breaking Cases Catalog](breaking-cases-catalog.md).

---

## Source-Only Breaks (API_BREAK)

Binary-compatible, but recompilation against new headers fails. Verdict: 🟠 API_BREAK.

| Change | Impact | Example |
|--------|--------|---------|
| Rename enum member (same value) | `LOG_ERR` no longer compiles; binary still uses integer `1` | [case31](../examples/case31_enum_rename.md) |
| Narrow access level (public to private) | Downstream code calling `helper()` gets compile error | [case34](../examples/case34_access_level.md) |
| Remove default parameter | Call sites relying on default fail to compile; ABI unchanged | -- |

---

## Risk Changes (deployment concern)

Binary-compatible, but may break at deployment time. Verdict: 🟡 COMPATIBLE_WITH_RISK.

| Change | Risk | Example |
|--------|------|---------|
| New GLIBC/GLIBCXX version requirement | Binaries won't load on older distros missing the required symbol version | -- (detected via `SYMBOL_VERSION_REQUIRED_ADDED`) |
| Leaked dependency symbol changed | Transitive dependency update shifts symbols your consumers never directly linked | -- |
| `noexcept` removed | C++17 callers compiled with `noexcept` in function type get UB on throw | [case15](../examples/case15_noexcept_change.md) |

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
