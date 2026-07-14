# ADR-011: ABI Change Classification Taxonomy

**Date:** 2026-03-18
**Status:** Accepted — implemented. Amendment (2026-07-14): the change-kind
counts below (119 kinds in Context, 114 in Consequences) were a
point-in-time snapshot and are now stale — the registry has grown since this
ADR was written. Treat `len(list(ChangeKind))` in `checker_policy.py` as the
generated source of truth for the current count, not any number in this
document.
**Decision maker:** Nikolay Petrov

---

## Context

abicheck detects a large and growing number of distinct ABI/API change types
via the `ChangeKind` enum (see `len(list(ChangeKind))` in
`checker_policy.py` for the current count — do not hardcode it here, it
changes every time a kind is added). Each kind must be classified into
exactly one severity tier under the default `strict_abi` policy (see
ADR-010):

- **BREAKING** — binary ABI break; existing compiled binaries will crash or
  fail to load
- **API_BREAK** — source-level break; recompilation will fail, but existing
  binaries are unaffected
- **COMPATIBLE_WITH_RISK** — binary-compatible, but deployment risk present
- **COMPATIBLE** — safe change; informational or additive

Several classifications diverge from ABICC and libabigail. These divergences
are intentional and documented here as the authoritative reference.

### Options considered

| Option | Description | Trade-off |
|--------|-------------|-----------|
| A: Follow ABICC classifications exactly | 1:1 parity with reference tool | Known misclassifications (e.g., `ENUM_MEMBER_ADDED` as BREAKING) |
| B: Follow libabigail classifications | Parity with alternative reference | Incomplete coverage (many kinds not detected) |
| **C: Independent classification with documented divergences** | Each kind classified from first principles | More accurate but requires justification for each divergence |

---

## Decision

### Classification framework

When classifying a new `ChangeKind`, apply these rules in order:

1. **Does the change cause existing compiled binaries to crash, produce wrong
   results, or fail to load?** → BREAKING
2. **Does the change cause source code to fail to compile against the new
   headers?** → API_BREAK
3. **Is the change binary-compatible but creates a deployment/environment
   risk?** → COMPATIBLE_WITH_RISK
4. **Is the change additive or informational with no negative impact on
   existing consumers?** → COMPATIBLE

### Notable classification decisions

#### BREAKING tier — non-obvious inclusions

| ChangeKind | Rationale |
|------------|-----------|
| `TYPE_FIELD_ADDED` | Breaking for polymorphic or non-standard-layout types (shifts vtable or subsequent fields). Standard-layout appends use `TYPE_FIELD_ADDED_COMPATIBLE` instead. |
| `ENUM_MEMBER_REMOVED` | Compiled code may use the stale numeric value in switch statements or comparisons. |
| `ENUM_MEMBER_VALUE_CHANGED` | Compiled code has the old numeric value baked in. |
| `TEMPLATE_PARAM_TYPE_CHANGED` | Different template instantiation = different binary layout (e.g., `vector<int>` → `vector<double>`). |
| `FUNC_DELETED_ELF_FALLBACK` | ELF heuristic: symbol absent from `.dynsym` while header still declares it — binary-incompatible regardless of `= delete`. |
| `VAR_BECAME_CONST` | Variable moved to `.rodata`; old code writing to it gets SIGSEGV. |
| `TYPE_BECAME_OPAQUE` | Complete type → forward-declaration only; `sizeof` and field access fail. |
| `SYMBOL_VERSION_NODE_REMOVED` | Entire version node removed from version script. All symbols under that node become unresolvable for applications linked against it. Deduplicated with `SYMBOL_VERSION_DEFINED_REMOVED` (the more specific node-level change wins). |

#### COMPATIBLE tier — divergences from ABICC/libabigail

| ChangeKind | Our classification | ABICC/libabigail | Rationale |
|------------|-------------------|-----------------|-----------|
| `FUNC_NOEXCEPT_ADDED` | COMPATIBLE | Not detected | Itanium ABI mangling (the encoding of C++ names into linker symbols, e.g., `foo()` → `_ZN3fooEv`) does not change; C++17 function-type concern only. Existing binaries resolve the same symbol. |
| `FUNC_NOEXCEPT_REMOVED` | COMPATIBLE | Not detected | Same rationale — mangling unchanged. Source-level concern only. |
| `ENUM_MEMBER_ADDED` | COMPATIBLE | BREAKING (ABICC) | New enumerator does not shift existing values in C/C++. Switch coverage is a source concern. Value shifts are caught separately by `ENUM_MEMBER_VALUE_CHANGED`. |
| `FUNC_REMOVED_ELF_ONLY` | COMPATIBLE | BREAKING (both) | Symbol present in ELF but not in public headers → likely visibility cleanup, not intentional API removal. |
| `SYMBOL_BINDING_CHANGED` (GLOBAL→WEAK) | COMPATIBLE | Not detected | Symbol still exported and resolvable. Interposition semantics change but existing binaries work. |
| `SYMBOL_BINDING_STRENGTHENED` (WEAK→GLOBAL) | COMPATIBLE | Not detected | Backward-compatible for most consumers. Edge case: interposing libraries lose override ability. |
| `UNION_FIELD_ADDED` | COMPATIBLE | BREAKING (ABICC) | All union fields start at offset 0; existing fields unaffected. Size increase caught by `TYPE_SIZE_CHANGED`. |
| `NEEDED_ADDED` | COMPATIBLE | Not flagged | Load-time deployment concern, not symbol/type ABI break. |
| `IFUNC_INTRODUCED/REMOVED` | COMPATIBLE | Not detected | PLT/GOT mechanism handles resolution transparently. |
| `TYPEDEF_VERSION_SENTINEL` | COMPATIBLE | BREAKING (ABICC) | Version-stamped typedefs (e.g., `png_libpng_version_1_6_46`) are compile-time sentinels only, never exported as ELF symbols. |
| `FIELD_BECAME_CONST` etc. | COMPATIBLE | Not detected | Field qualifiers are informational; do not affect binary layout. |
| `SONAME_BUMP_RECOMMENDED` | COMPATIBLE | Not detected | Policy advisory: breaking changes present but SONAME not bumped. The breaking changes themselves carry the BREAKING verdict; this is informational guidance. |
| `SONAME_BUMP_UNNECESSARY` | COMPATIBLE | Not detected | Policy advisory: SONAME bumped without breaking changes. Informational — forces unnecessary relinking. |
| `VERSION_SCRIPT_MISSING` | COMPATIBLE | Not detected | Quality advisory: library exports symbols without a version script. No ABI break, but makes future versioning harder. |

#### API_BREAK tier — source-level only

| ChangeKind | Rationale |
|------------|-----------|
| `ENUM_MEMBER_RENAMED` | Same numeric value, different name — source code using old name won't compile. |
| `FIELD_RENAMED` | Same offset and type, different name — source code using old name won't compile. |
| `PARAM_DEFAULT_VALUE_REMOVED` | Callers relying on the default must now provide the argument. |
| `FUNC_BECAME_INLINE` | Symbol may vanish from DSO if compiler fully inlines it — needs manual review. |
| `CONSTANT_CHANGED` / `CONSTANT_REMOVED` | `#define` values are compile-time only — semantic source-level change. |
| `SOURCE_LEVEL_KIND_CHANGED` | `struct` ↔ `class` keyword change — binary-identical layout, source-level difference only. |

#### COMPATIBLE_WITH_RISK tier

| ChangeKind | Rationale |
|------------|-----------|
| `SYMBOL_VERSION_REQUIRED_ADDED` | New glibc/version requirement. Existing binaries unaffected (already linked). Risk: library won't load on systems with older glibc. |
| `ENUM_LAST_MEMBER_VALUE_CHANGED` | Sentinel/MAX value changed. Binary-safe but source code using it as array bound may overflow. |
| `SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED` | Symbol originates from dependency (libstdc++, libgcc); change is real but root cause is dependency versioning, not library's own API. |
| `SYMBOL_MOVED_VERSION_NODE` | Symbol migrated between version nodes (e.g., `LIBFOO_1.0` → `LIBFOO_2.0`). Technically breaking for versioned lookups, but typically intentional during major releases. |

### Migration note for ABICC/libabigail users

Users migrating from ABICC will notice that some classifications are more
lenient (e.g., `ENUM_MEMBER_ADDED` is COMPATIBLE in abicheck, BREAKING in
ABICC). This reflects abicheck's stricter binary-vs-source distinction —
adding an enum member does not shift existing values in C/C++. Value shifts
are caught separately by `ENUM_MEMBER_VALUE_CHANGED`.

Under the `sdk_vendor` policy profile (ADR-010), the following API_BREAK
kinds are downgraded to COMPATIBLE: `ENUM_MEMBER_RENAMED`,
`FIELD_RENAMED`, `PARAM_RENAMED`, `METHOD_ACCESS_CHANGED`,
`FIELD_ACCESS_CHANGED`, `SOURCE_LEVEL_KIND_CHANGED`,
`REMOVED_CONST_OVERLOAD`, `PARAM_DEFAULT_VALUE_REMOVED`.

### Adding new ChangeKinds

When adding a new `ChangeKind`:

1. Add the enum value to `ChangeKind` in `checker_policy.py`
2. Classify it in exactly one of: `BREAKING_KINDS`, `API_BREAK_KINDS`,
   `COMPATIBLE_KINDS`, or `RISK_KINDS`
3. Add an entry to `IMPACT_TEXT` with a user-facing explanation of what goes
   wrong
4. If the kind should be downgraded under `sdk_vendor` or `plugin_abi`, add
   it to the appropriate downgrade set — the import-time assertions will
   verify correctness
5. The kind will automatically appear in `POLICY_REGISTRY` and be handled
   by `compute_verdict()`

---

## Consequences

### Positive

- Every classification is explicit and auditable in a single file
- Divergences from ABICC/libabigail are intentional and documented
- Framework guides contributors when adding new change types
- Import-time assertions catch misclassification immediately

### Negative

- All `ChangeKind` values require maintenance as ABI standards evolve (see
  `len(list(ChangeKind))` in `checker_policy.py` for the current count)
- Divergences from reference tools may surprise users migrating from ABICC
- Some classifications are judgment calls (e.g., `noexcept`) that may need
  revisiting as C++ standards evolve

---

## References

- `abicheck/checker_policy.py` — `ChangeKind` enum, `BREAKING_KINDS`,
  `API_BREAK_KINDS`, `COMPATIBLE_KINDS`, `RISK_KINDS`, `IMPACT_TEXT`
- ADR-001 — Contains early classification decisions for `NEEDED_ADDED`,
  `SYMBOL_BINDING_STRENGTHENED`, `SYMBOL_SIZE_CHANGED` (now consolidated here)
- ADR-010 — Policy profile system that varies classification per profile
