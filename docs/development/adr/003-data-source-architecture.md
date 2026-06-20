# ADR-003: Data Source Architecture — Checks, Instruments, and Binary Types

**Date:** 2026-03-17
**Status:** Accepted — implemented
**Decision maker:** Nikolay Petrov

---

## Context

abicheck collects ABI data from three independent layers:

| Layer | Source | Tool | What it provides |
|-------|--------|------|-----------------|
| **L0: Binary metadata** | ELF/PE/Mach-O | pyelftools, pefile, macholib | Exported symbols, versioning, SONAME, DT_NEEDED, binding, visibility |
| **L1: Debug info** | DWARF (.debug_info) | pyelftools | Struct layouts (size, field offsets, alignment), enum values, calling conventions |
| **L2: Header AST** | C/C++ headers | castxml | Function signatures, parameter types, return types, typedefs, #define constants |

Today, L2 (headers + castxml) is the **primary** type source. L1 (DWARF) is used only
for **cross-checking** L2 data (struct sizes, field offsets). Without headers, abicheck
falls back to `elf_only_mode` — only L0 symbol-level checks run, and DWARF data is
largely unused for type comparison.

This creates a gap: a binary with full DWARF but no headers gets only symbol-level
analysis. DWARF contains complete type information (struct definitions, function
prototypes, enum values, inheritance, vtables) that is currently ignored.

### Current detector → data source mapping

```text
30 detectors in compare()
├── 24 AST detectors (L2)      → old.functions, old.types, old.enums, old.typedefs, old.constants
│   └── Only fire when elf_only_mode=False (headers were provided)
├── 3 binary metadata detectors (L0) → old.elf, old.pe, old.macho
│   └── Always fire (unconditional)
├── 2 DWARF detectors (L1)     → old.dwarf, old.dwarf_advanced
│   └── Cross-check only: filters to types already known from L2
└── 1 fallback detector         → old.elf.symbols
    └── Fires when elf_only_mode=True (no headers)
```

**Problem**: In `elf_only_mode`, 24 out of 30 detectors are skipped. The DWARF
detector also skips because it filters against the (empty) L2 type list.

---

## Decision

### 1. Promote DWARF to a primary data source (L1 → L1+L2 capable)

Create `abicheck/dwarf_snapshot.py` — a `DwarfSnapshotBuilder` that constructs
a full `AbiSnapshot` from DWARF `.debug_info` alone:

```python
def build_snapshot_from_dwarf(elf_path: str, elf_meta: ElfMetadata) -> AbiSnapshot:
    """Build complete AbiSnapshot from DWARF, no headers required."""
```

This populates the same `AbiSnapshot` fields as castxml: `functions`, `variables`,
`types`, `enums`, `typedefs`. The comparison engine doesn't know or care whether
the snapshot came from castxml or DWARF.

### 2. Updated fallback chain in dumper.py

```text
dump(binary_path, headers=None):
  │
  ├── L0: Binary metadata (always)
  │     ELF → parse_elf_metadata()      → ElfMetadata
  │     PE  → parse_pe_metadata()       → PeMetadata
  │     Mach-O → parse_macho_metadata() → MachoMetadata
  │
  ├── L1: Debug info (when present)
  │     DWARF → parse_dwarf()           → DwarfMetadata + AdvancedDwarfMetadata
  │     BTF   → parse_btf()             → BtfMetadata (future, ADR-007)
  │     PDB   → parse_pdb()             → PdbMetadata (PE only)
  │
  ├── L2: Header AST (when headers provided)
  │     castxml → _castxml_dump()       → functions, types, enums, typedefs, constants
  │
  └── Snapshot assembly:
        headers provided?
        ├── YES → L2 primary + L1 cross-check (current behavior)
        │         elf_only_mode = False
        │         All 30 detectors fire
        │
        └── NO  → L1 available?
                  ├── YES → DwarfSnapshotBuilder (NEW)
                  │         elf_only_mode = False
                  │         All 24 AST detectors fire (DWARF-derived types)
                  │         L1 cross-check skipped (same source)
                  │         Warning: #define constants and default params unavailable
                  │
                  └── NO  → L0 only
                            elf_only_mode = True
                            Only L0 detectors fire (symbol-level)
                            Warning: type info unavailable
```

### 3. Detector data source matrix

This table defines which checks come from which data source, and what's available
in each mode:

| Detector | Data Source | Headers mode | DWARF-only mode | Symbols-only mode |
|----------|------------|:---:|:---:|:---:|
| **functions** (added/removed/changed) | L2 (castxml) or L1 (DWARF) | Yes | Yes | — |
| **variables** (added/removed/type changed) | L2 or L1 | Yes | Yes | — |
| **types** (size, fields, bases, vtable) | L2 or L1 | Yes | Yes | — |
| **enums** (members, values) | L2 or L1 | Yes | Yes | — |
| **typedefs** | L2 or L1 | Yes | Yes | — |
| **method_qualifiers** (const, static, access) | L2 or L1 | Yes | Yes | — |
| **unions** (field changes) | L2 or L1 | Yes | Yes | — |
| **param_defaults** | L2 only | Yes | — | — |
| **constants** (#define values) | L2 only | Yes | — | — |
| **template_inner_types** | L2 or L1 (partial) | Yes | Partial | — |
| **elf** (soname, needed, versions, symbols) | L0 | Yes | Yes | Yes |
| **pe** (exports, imports, machine) | L0 | Yes | Yes | Yes |
| **macho** (exports, compat_version, deps) | L0 | Yes | Yes | Yes |
| **dwarf** (struct layout cross-check) | L1 | Yes (cross-check) | — (same source) | — |
| **advanced_dwarf** (calling conv, packing) | L1 | Yes | Yes | — |
| **elf_deleted_fallback** | L0 | — | — | Yes |
| **reserved_fields** | L2 or L1 | Yes | Yes | — |
| **field_renames** / **enum_renames** | L2 or L1 | Yes | Yes | — |
| **pointer_levels** / **param_restrict** | L2 or L1 | Yes | Yes | — |

### 4. DWARF type extraction — what DWARF provides and what it doesn't

| ABI element | DWARF availability | Notes |
|-------------|-------------------|-------|
| Function signatures | `DW_TAG_subprogram` + `DW_TAG_formal_parameter` | Full: name, return type, param types |
| Struct/class layout | `DW_TAG_structure_type` + `DW_TAG_member` | Full: size, field offsets, alignment |
| Enum definitions | `DW_TAG_enumeration_type` + `DW_TAG_enumerator` | Full: names, values, underlying type |
| Variables | `DW_TAG_variable` with `DW_AT_external` | Full: name, type, linkage |
| Typedefs | `DW_TAG_typedef` | Full: name → base type |
| Inheritance | `DW_TAG_inheritance` | Full: base classes, access, virtuality |
| Vtable entries | `DW_AT_vtable_elem_location` | Partial: depends on compiler |
| Templates | `DW_TAG_template_type_parameter` | Full: template parameter types |
| `#define` constants | **NOT IN DWARF** | Preprocessor — headers only |
| Default param values | **NOT IN DWARF** | C++ frontend — headers only |
| Inline function bodies | No exported symbol | Out of scope |

### 5. Visibility filtering in DWARF-only mode

DWARF contains **all** types and functions (including static/internal). We must
filter to only ABI-relevant items:

```python
# Intersection: DWARF functions × ELF exported symbols
exported = {s.name for s in elf_meta.symbols if s.binding in ('GLOBAL', 'WEAK') and s.defined}
for func in dwarf_functions:
    if func.linkage_name in exported or func.name in exported:
        func.visibility = Visibility.PUBLIC
    else:
        continue  # skip internal functions
```

Same for variables: only include `DW_TAG_variable` with `DW_AT_external=True`
that appear in the ELF dynamic symbol table.

For types: include types reachable from exported function signatures and
exported variable types. Transitively follow type references.

### 6. CLI changes

```bash
# Current behavior unchanged:
abicheck dump libfoo.so -H /usr/include/foo/   # Headers mode (castxml primary)
abicheck dump libfoo.so                          # Auto-detect: DWARF if present, else symbols-only

# New explicit flags:
abicheck dump libfoo.so --dwarf-only             # Force DWARF even when headers available
abicheck compare old.so new.so --dwarf-only      # Compare using DWARF-derived snapshots

# Diagnostic:
abicheck dump libfoo.so --show-data-sources      # Print which layers are available
```

`--show-data-sources` output example:
```text
Data sources for libfoo.so:
  L0 Binary metadata: ELF (x86_64, SONAME=libfoo.so.1, 47 exported symbols)
  L1 Debug info:      DWARF 4 (142 types, 89 functions, 23 enums)
  L2 Header AST:      not available (no -H provided)

Using: DWARF-only mode (24/30 detectors active)
Missing: #define constants, default parameter values
```

### 7. Snapshot interchangeability

DWARF-derived and castxml-derived snapshots produce identical JSON schema.
This means:
- `abicheck dump lib.so > dwarf.json` and `abicheck dump lib.so -H inc/ > ast.json`
  are both valid inputs to `abicheck compare`
- You can compare a DWARF snapshot against an AST snapshot (cross-mode comparison)
- The `schema_version` field remains the same

### 8. Per-platform data source availability

| Platform | L0 (binary) | L1 (debug) | L2 (headers) | Typical mode |
|----------|:---:|:---:|:---:|---|
| **Linux ELF** | pyelftools | DWARF (pyelftools) | castxml | All three |
| **Linux ELF (stripped)** | pyelftools | — | castxml | L0+L2 |
| **Linux ELF (no headers)** | pyelftools | DWARF | — | L0+L1 (NEW) |
| **Linux ELF (bare)** | pyelftools | — | — | L0 only |
| **Windows PE** | pefile | PDB (partial) | castxml | L0+L2 |
| **macOS Mach-O** | macholib | DWARF (pyelftools) | castxml | All three |
| **Kernel modules** | pyelftools | BTF/DWARF | — | L0+L1 (future) |

## Consequences

### Positive
- 24 detectors become available for header-less binaries (vs 6 today)
- Removes castxml requirement for the majority of use cases
- Same `AbiSnapshot` model — zero changes to checker, reporter, suppression
- Interchangeable JSON snapshots: DWARF ↔ castxml ↔ mixed comparisons work
- Clear mental model: L0/L1/L2 layers with documented coverage per detector

### Negative
- DWARF parsing for full type extraction is slower than castxml (~5-20×)
- Two code paths to build `AbiSnapshot` — need validation that they produce equivalent results
- `#define` constants and default params are L2-only (warn user)
- pyelftools DWARF 5 has gaps (string offsets, macro info)

## Implementation Plan

| Phase | Scope | Effort |
|-------|-------|--------|
| 1 | `DwarfSnapshotBuilder` — structs, enums, typedefs | 3-5 days |
| 2 | Function/variable signatures with full type resolution | 3-5 days |
| 3 | C++ features: inheritance, vtable, templates from DWARF | 3-5 days |
| 4 | Visibility filtering (DWARF × ELF symbol intersection) | 1-2 days |
| 5 | CLI: auto-detection, `--dwarf-only`, `--show-data-sources` | 1-2 days |

---

## Extension: Binary Fingerprint Rename Detection (Exploratory)

**Date:** 2026-03-23
**Status:** Exploratory prototype implemented

### Context

In `elf_only_mode` (L0 only, no DWARF or headers), symbol renames appear as
"removed + added" pairs — noisy churn that obscures real ABI changes. When a
library renames `libfoo_v1_create()` → `libfoo_create()` without changing the
code, the diff engine reports one BREAKING removal and one COMPATIBLE addition
instead of a single "renamed" signal.

### Approach

Lightweight binary fingerprinting using data already available in L0:

1. **Function size fingerprinting**: Use `st_size` from `.dynsym` to match
   removed/added symbol pairs with identical code sizes.
2. **Code hash fingerprinting**: When the binary file is available (not just
   a serialized snapshot), read the function's code bytes from `.text` and
   compute SHA-256 for exact matching.
3. **Section-level triage**: Compare `.text`/`.rodata`/`.data` section hashes
   for a coarse "did the binary change significantly" signal.

### Implementation

- `abicheck/binary_fingerprint.py` — standalone module with:
  - `compute_function_fingerprints(binary_path)` → code-hash fingerprints
  - `match_renamed_functions(old_fps, new_fps)` → 3-pass matching (exact,
    size-only, fuzzy within 5% tolerance)
  - `compute_section_summary(binary_path)` → section-level triage
- `fingerprint_renames` detector registered in `diff_symbols.py` — fires only
  in `elf_only_mode` when both snapshots have ELF metadata.
- New `FUNC_LIKELY_RENAMED` change kind (verdict: `COMPATIBLE_WITH_RISK`).

### Scope boundaries (not in scope)

- Full disassembly or CFG extraction
- BinDiff/Ghidra integration
- Instruction-level analysis
- Architecture-specific knowledge

### Next steps

If the prototype shows value (measurable reduction in false removed/added
pairs on real-world libraries), write a full ADR and integrate into the
post-processing pipeline to suppress redundant `FUNC_REMOVED` + `FUNC_ADDED`
pairs when a `FUNC_LIKELY_RENAMED` exists for the same symbol pair.
| 6 | Validation: DWARF vs castxml snapshot equivalence on test suite | 2-3 days |

---

## Extension: clang as an alternative L2 frontend (implemented)

**Date:** 2026-06-15
**Status:** Implemented — first slice landed. Surfaced by the UXL field run
(`validation/uxl-scan-levels-timing-2026-06.md`, P1).

### Context

L2 (Header AST) has a single producer: **castxml** (`_castxml_dump` →
`dumper_castxml.py`). Two field-observed problems:

1. **castxml-only blocks clang-only hosts.** `dump --headers` / `scan -H`
   hard-fail with `castxml not found` on the many dev/CI images that ship clang
   but not castxml. With no L2 there is no header-aware public-surface scoping,
   and **all four ADR-035 D4 cross-source checks skip** (no public-header
   provenance) — a whole feature class silently off.
2. **castxml's bundled clang lags the system toolchain** and aborts on
   current-standard stdlib headers (e.g. the C++23 `bf16` literal in recent
   libstdc++ — see the `case80` / `case89` `known_gap`s), before any detector
   runs.

ADR-001 originally rejected LLVM/clang tooling (Option C) as too heavy
(~500 MB). That rationale is now **stale**: clang is *already* a dependency —
the L4 source-ABI replay extractor uses `clang -ast-dump=json`
(ADR-030 D3, `buildsource/source_extractors/`), behind the ADR-032 extractor
interface.

### Direction

Add a **clang L2 backend** that produces the same `AbiSnapshot` fields as
castxml (`functions`, `types`, `enums`, `typedefs`, constants) from
`clang -ast-dump=json` (or libclang) over the public headers, selected by a
backend knob (auto: prefer the available/most-capable frontend, mirroring the
L4 `--source-abi-extractor auto` choice). This **revisits ADR-001 Option C**
for L2 only, scoped to a header parse — not a full LLVM rewrite.

Snapshot interchangeability (§7) is the constraint: a clang-derived L2 snapshot
must be schema-equivalent to a castxml-derived one, so the two are a **parity
oracle** for each other (same pattern as the DWARF↔castxml and
libabigail/ABICC parity gates). Using both also maximises host coverage:
clang where castxml is absent or chokes; castxml where the clang JSON-AST
schema (which drifts across clang releases) is unsupported.

### Scope boundaries (not in scope)

- Replacing castxml — it stays the default and the schema reference.
- A new `AbiSnapshot` field or detector — this is a new *producer* of existing
  fields only.

### What landed (first slice)

A `clang -ast-dump=json` → `AbiSnapshot` parser, `abicheck/dumper_clang.py`
(`_ClangAstParser`), a sibling to `dumper_castxml._CastxmlParser` that exposes
the identical `parse_functions`/`parse_variables`/`parse_types`/`parse_enums`/
`parse_typedefs`/`parse_constants` surface. It is selected by a backend knob:

- `dumper.dump(..., header_backend=...)` and `service.run_dump`/`resolve_input`
  thread a `header_backend` of `auto` | `castxml` | `clang`;
- the CLI exposes `--ast-frontend` on `dump` and `compare`;
- the `ABICHECK_AST_FRONTEND` env var sets the global default (so a
  clang-only CI image flips the default with no flag), consulted by
  `dumper._resolve_header_backend`;
- `auto` prefers castxml (the schema reference), then falls back to clang when
  only clang is on `PATH` — closing the P1 hard-fail on clang-only hosts.

`dumper._header_ast_parser` is the single factory both frontends sit behind, so
the per-format `_dump_elf`/`_dump_macho`/`_dump_pe` builders consume either
parser uniformly. The clang JSON cache lives beside the castxml XML cache
(`~/.cache/abi_check/clang/*.json`), keyed on the backend so the two never
collide.

**Coverage trade-off.** clang's JSON AST is syntactic — it does not compute
record layout — so a clang-derived `RecordType` carries field names/types,
bases, and access but **not** `size_bits`/`offset_bits`/vtable slots (left
`None`/empty; the layout detectors skip an unknown-vs-unknown comparison and
DWARF (L1) stays the layout authority). Everything the source-API and
public-surface-scoping detectors need — signatures, `noexcept`/`const`/
`explicit`, enum values, typedef targets, public constant values — is produced,
which is exactly what the D4 cross-source checks consume.

### Next steps

Deepen layout parity (a `-fdump-record-layouts` pass to recover
`size_bits`/`offset_bits`/vtable order) and grow the clang↔castxml
snapshot-equivalence check (`test_clang_header_backend_integration.py`) into a
gate over the full example suite.
