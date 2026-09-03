## ABI review — `libfoo.so` 1.0 → 2.0

**Verdict:** ✅ `NO_CHANGE` — no ABI/API change — safe to merge

> ⚠️ Detector 'elf_layout' disabled: missing ELF metadata on one side
> ⚠️ Detector 'fingerprint_renames' disabled: requires ELF metadata in elf_only_mode
> ⚠️ Detector 'kabi' disabled: missing Module.symvers (kABI) metadata
> ⚠️ Detector 'dwarf_layout_coherence' disabled: neither snapshot has a DWARF-vs-header-AST layout coherence mismatch
> ⚠️ Detector 'long_double' disabled: missing ELF metadata on one side
> ⚠️ Detector 'pe' disabled: missing PE metadata
> ⚠️ Detector 'macho' disabled: missing Mach-O metadata
> ⚠️ Detector 'python_ext' disabled: missing CPython extension metadata
> ⚠️ Detector 'python_api' disabled: missing Python API surface (no .pyi stub recovered)
> ⚠️ Detector 'sycl' disabled: missing SYCL metadata
> ⚠️ Detector 'unnamed_types' disabled: missing ELF metadata on one side
> ⚠️ Detector 'vtable_layout' disabled: missing DWARF/header type metadata (inheritance)
> ⚠️ Detector 'advanced_dwarf' disabled: missing DWARF advanced metadata
> ⚠️ No binary metadata available; verdict is based on header analysis only

| Category | Count |
|---|---|
| ❌ Breaking (ABI) | 0 |
| ⚠️ API breaks (source) | 0 |
| ⚠️ Risk findings | 0 |
| ✅ Public additions | 0 |
| 🔒 Filtered (internal/private) | 0 |

**Release recommendation:** `none` version bump · SONAME `no_bump_needed`
