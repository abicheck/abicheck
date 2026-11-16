# ADR-011: ABI Change Classification Taxonomy

**Date:** 2026-03-18
**Status:** Accepted — implemented. Amendment (2026-07-14): the change-kind
counts below (119 kinds in Context, 114 in Consequences) were a
point-in-time snapshot and are now stale — the registry has grown since this
ADR was written. Treat `len(list(ChangeKind))` in `checker_policy.py` as the
generated source of truth for the current count, not any number in this
document. Amendment (2026-11-10, #1977): `SYMBOL_MOVED_VERSION_NODE` moves
from the COMPATIBLE_WITH_RISK tier to BREAKING and now means "a surviving
symbol is no longer defined at a version the old library defined it at"
(default or compat). Old consumers' versioned references then fail to load,
so "typically intentional during major releases" is a policy question
(reclassify the kind in policy), not a reason to score a load failure as a
risk. A move that keeps every old version stays the compatible
`SYMBOL_VERSION_ALIAS_CHANGED`.
Amendment (2026-11-14): `SOURCE_LEVEL_KIND_CHANGED` (struct ↔ class with
the same members) moves from API_BREAK to COMPATIBLE. Rule 2 asks whether
source fails to compile against the new headers; a class-key change alone
does not (a mismatched class-key in a forward or elaborated declaration is at
most a warning, `-Wmismatched-tags`), and it changes neither layout nor
Itanium mangling. Each real consequence already has its own, correctly graded
kind: default member/base access (`FIELD_ACCESS_CHANGED`,
`METHOD_ACCESS_CHANGED`, `BASE_CLASS_ACCESS_NARROWED`), and on MSVC the
class-key is part of the decorated name (`U` vs `V`), so every exported
function mentioning the type shows as a PE export removal (BREAKING) plus
addition. Grading the keyword itself API_BREAK double-counted those
consequences and flagged harmless edits; the `sdk_vendor` override for it is
removed as redundant (ADR-010).
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
| `SYMBOL_MOVED_VERSION_NODE` | A surviving symbol is no longer defined at a version the old library defined it at (e.g. `foo@LIBFOO_1.0` gone, `foo@@LIBFOO_2.0` new). Consumers holding `foo@LIBFOO_1.0` fail to load. (Amended 2026-11-10; was COMPATIBLE_WITH_RISK.) |
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
| `SOURCE_LEVEL_KIND_CHANGED` | COMPATIBLE | Reported (ABICC) | `struct` ↔ `class` with the same members: same layout and Itanium mangling, source still compiles. Access and MSVC-mangling consequences are reported by their own kinds. (Amended 2026-11-14; was API_BREAK.) |
| `VERSION_SCRIPT_MISSING` | COMPATIBLE | Not detected | Quality advisory: library exports symbols without a version script. No ABI break, but makes future versioning harder. |

#### API_BREAK tier — source-level only

| ChangeKind | Rationale |
|------------|-----------|
| `ENUM_MEMBER_RENAMED` | Same numeric value, different name — source code using old name won't compile. |
| `FIELD_RENAMED` | Same offset and type, different name — source code using old name won't compile. |
| `PARAM_DEFAULT_VALUE_REMOVED` | Callers relying on the default must now provide the argument. |
| `FUNC_BECAME_INLINE` | Symbol may vanish from DSO if compiler fully inlines it — needs manual review. |
| `CONSTANT_CHANGED` / `CONSTANT_REMOVED` | `#define` values are compile-time only — semantic source-level change. |

#### COMPATIBLE_WITH_RISK tier

| ChangeKind | Rationale |
|------------|-----------|
| `SYMBOL_VERSION_REQUIRED_ADDED` | New glibc/version requirement. Existing binaries unaffected (already linked). Risk: library won't load on systems with older glibc. |
| `ENUM_LAST_MEMBER_VALUE_CHANGED` | Sentinel/MAX value changed. Binary-safe but source code using it as array bound may overflow. |
| `SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED` | Symbol originates from dependency (libstdc++, libgcc); change is real but root cause is dependency versioning, not library's own API. |

### Migration note for ABICC/libabigail users

Users migrating from ABICC will notice that some classifications are more
lenient (e.g., `ENUM_MEMBER_ADDED` is COMPATIBLE in abicheck, BREAKING in
ABICC). This reflects abicheck's stricter binary-vs-source distinction —
adding an enum member does not shift existing values in C/C++. Value shifts
are caught separately by `ENUM_MEMBER_VALUE_CHANGED`.

Under the `sdk_vendor` policy profile (ADR-010), the following API_BREAK
kinds are downgraded to COMPATIBLE: `ENUM_MEMBER_RENAMED`,
`FIELD_RENAMED`, `PARAM_RENAMED`, `METHOD_ACCESS_CHANGED`,
`FIELD_ACCESS_CHANGED`,
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

## Amendment (2026-11-14, accepted): a pointer field retargeted at a renamed record is `ABI_EQUIVALENT_TYPE_CHANGED`

`TYPE_FIELD_TYPE_CHANGED` compares a field's spelled type. When a field's
type is a pointer or reference (any depth: `T *`, `T **`, `T &`,
`const T *`) to a record that was only *renamed*, the slot still stores an
address at the same offset, so the field change is not an ABI break.
`compare/abi_equivalent_retype.py` re-classifies it as
`ABI_EQUIVALENT_TYPE_CHANGED` (`COMPATIBLE_WITH_RISK`) only when all of
these hold:

- the field's offset is known and unchanged, and neither side is a bitfield;
- the pointer/reference shape (qualifiers and indirections) is spelled
  identically;
- the old pointee name is gone from the new side and the new pointee name
  is absent from the old side — a rename, not a repointing at another type
  that already existed;
- both pointee records are found and have the same size, alignment and
  member offsets, or are both opaque.

The rename itself stays visible as its own finding (`TYPE_REMOVED` /
`TAG_TYPE_RENAMED`), which carries the source break; the re-classification
never lowers a verdict on missing evidence (an unknown offset or an
unresolved pointee keeps `TYPE_FIELD_TYPE_CHANGED`).

## Amendment (2026-11-16, accepted): a newly needed provider's version nodes are floors

`SYMBOL_VERSION_REQUIRED_ADDED_COMPAT` was assigned to every version node of a
library the old binary did not need at all (`lib_is_new`). That treated a
provider addition as "covered by `NEEDED_ADDED`", so a deployment floor that
rose from no requirement (a binary that needed no `libc` symbol versions, then
requires `GLIBC_2.25`) was reported only as the new dependency, with no version
floor named. The node is a new constraint on consumers on older runtimes, and
the classification now says so.

For a newly needed provider, the floor is the binary-wide maximum of the
version prefix (glibc's `GLIBC_` nodes are one runtime whichever library names
them):

- a version node above that maximum is `SYMBOL_VERSION_REQUIRED_ADDED`
  (BREAKING), and `RUNTIME_FLOOR_RAISED` fires with an empty old tag when the
  floor rises from no requirement, or with the old maximum's tag when it rises
  above an existing one;
- a version node at or below it stays `SYMBOL_VERSION_REQUIRED_ADDED_COMPAT`
  and raises no floor. This is the TBB 2021.13 shape: a new `libdl.so.2` with
  `GLIBC_2.2.5` under a `libc.so.6` that already needs `GLIBC_2.34` is not a
  raise, and must not be reported as one;
- an unparseable marker (`GLIBC_PRIVATE`, `GLIBC_ABI_DT_RELR`) is not a floor
  and stays `_COMPAT`.

The floor is never below the architecture's `GLIBC_` baseline: the oldest node
every glibc on that architecture provides (`model/glibc_baseline.py`). A node at
or below it raises nothing, so a newly linked `libc` symbol version of
`GLIBC_2.2.5` on x86-64 (or `GLIBC_2.0` on i386) stays
`SYMBOL_VERSION_REQUIRED_ADDED_COMPAT`. Above it, the floor is measured from the
baseline and its tag names the baseline and the reason. The table covers the
architectures whose baseline was measured on a host libc (x86-64, i386); any other
architecture has no exemption, which errs toward a possible floor finding rather
than a suppressed one.

An already-needed provider keeps its per-library rule: a node no newer than that
provider's own old maximum is `_COMPAT`, and `RUNTIME_FLOOR_RAISED` fires only
for a raise of an existing floor.

**Migration**: a library that newly links a versioned provider above its
architecture's baseline can now get
`BREAKING` where it was `COMPATIBLE` before, whenever that provider's version
node is above the binary's existing floor. The verdict changes only for that
shape. Re-check the affected build with the new release, or add an explicit
suppression for the floor if the raised requirement is acceptable.
`diff_platform_elf_symbols._diff_elf_symbol_versioning` owns the rule; the
pinned tests are `test_sprint2_elf`, `test_symbol_versioning_coexistence`,
`test_diff_platform_deep` and `test_version_floor_new_provider`.
