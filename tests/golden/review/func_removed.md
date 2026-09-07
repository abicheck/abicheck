## ABI review — `libfoo.so` 1.0 → 2.0

**Verdict:** ❌ `BREAKING` — binary (ABI) break — blocks merge under a strict gate

> ⚠️ Detector 'elf_layout' disabled: missing ELF metadata on one side
> ⚠️ Detector 'fingerprint_renames' disabled: requires ELF metadata in elf_only_mode
> ⚠️ Detector 'kabi' disabled: missing Module.symvers (kABI) metadata
> ⚠️ Detector 'long_double' disabled: missing ELF metadata on one side
> ⚠️ Detector 'elf' disabled: missing ELF metadata
> ⚠️ Detector 'pe' disabled: missing PE metadata
> ⚠️ Detector 'macho' disabled: missing Mach-O metadata
> ⚠️ Detector 'tls_checks' disabled: missing ELF metadata
> ⚠️ Detector 'protected_visibility' disabled: missing ELF metadata
> ⚠️ Detector 'symbol_version_alias' disabled: missing ELF metadata
> ⚠️ Detector 'vtable_identity' disabled: missing ELF metadata
> ⚠️ Detector 'abi_surface' disabled: missing ELF metadata
> ⚠️ Detector 'dwarf' disabled: no DWARF debug info on either side
> ⚠️ Detector 'elf_deleted_fallback' disabled: missing ELF metadata
> ⚠️ Detector 'python_ext' disabled: missing CPython extension metadata
> ⚠️ Detector 'python_api' disabled: missing Python API surface (no .pyi stub recovered)
> ⚠️ Detector 'sycl' disabled: missing SYCL metadata
> ⚠️ Detector 'unnamed_types' disabled: missing ELF metadata on one side
> ⚠️ Detector 'vtable_layout' disabled: missing DWARF/header type metadata (inheritance)
> ⚠️ Detector 'advanced_dwarf' disabled: missing DWARF advanced metadata
> ⚠️ No binary metadata available; verdict is based on header analysis only

| Category | Count |
|---|---|
| ❌ Breaking (ABI) | 1 |
| ⚠️ API breaks (source) | 0 |
| ⚠️ Risk findings | 0 |
| ✅ Public additions | 0 |
| 🔒 Filtered (internal/private) | 0 |

**Release recommendation:** `major` version bump · SONAME `not_determined`

**Disposition audit:**

| Disposition | Count |
|---|---|
| Detected (raw) | 1 |
| Effective (gating) | 1 |
| … non gating | 0 |
| … suppressed | 0 |
| … out of contract | 0 |
| … unresolved relevance | 0 |
| … deduplicated | 0 |

**Not evaluated:** 20 detector(s) — `elf_layout`, `fingerprint_renames`, `kabi`, `long_double`, `elf`, `pe`, … and 14 more

**Additions** (0)

- none

**Removals** (1)

- **_Z6helperi** — `helper`

**Modifications** (0)

- none

**Top impacted symbols:**

- `_Z6helperi` — func_removed
