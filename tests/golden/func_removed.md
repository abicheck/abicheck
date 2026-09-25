# ABI Report: libfoo.so

| | |
|---|---|
| **Old version** | `1.0` |
| **New version** | `2.0` |
| **Verdict** | ❌ `BREAKING` |
| Breaking changes | 1 |
| Source-level breaks | 0 |
| Deployment risk changes | 0 |
| Compatible changes | 0 |

## Analysis Confidence

| Field | Value |
|---|---|
| Confidence | MEDIUM |
| Evidence tier | `header_aware` |
| Evidence tiers | `header` |
| Coverage gap | Detector 'elf_layout' disabled: missing ELF metadata on one side |
| Coverage gap | Detector 'fingerprint_renames' disabled: requires ELF metadata in elf_only_mode |
| Coverage gap | Detector 'kabi' disabled: missing Module.symvers (kABI) metadata |
| Coverage gap | Detector 'long_double' disabled: missing ELF metadata on one side |
| Coverage gap | Detector 'elf' disabled: missing ELF metadata |
| Coverage gap | Detector 'pe' disabled: missing PE metadata |
| Coverage gap | Detector 'macho' disabled: missing Mach-O metadata |
| Coverage gap | Detector 'tls_checks' disabled: missing ELF metadata |
| Coverage gap | Detector 'protected_visibility' disabled: missing ELF metadata |
| Coverage gap | Detector 'symbol_version_alias' disabled: missing ELF metadata |
| Coverage gap | Detector 'vtable_identity' disabled: missing ELF metadata |
| Coverage gap | Detector 'abi_surface' disabled: missing ELF metadata |
| Coverage gap | Detector 'dwarf' disabled: no DWARF debug info on either side |
| Coverage gap | Detector 'elf_deleted_fallback' disabled: missing ELF metadata |
| Coverage gap | Detector 'python_ext' disabled: missing CPython extension metadata |
| Coverage gap | Detector 'python_api' disabled: missing Python API surface (no .pyi stub recovered) |
| Coverage gap | Detector 'sycl' disabled: missing SYCL metadata |
| Coverage gap | Detector 'unnamed_types' disabled: missing ELF metadata on one side |
| Coverage gap | Detector 'vtable_layout' disabled: missing DWARF/header type metadata (inheritance) |
| Coverage gap | Detector 'advanced_dwarf' disabled: missing DWARF advanced metadata |
| Coverage gap | No binary metadata available; verdict is based on header analysis only |

## Relationship Coverage

> A relationship missing from the evidence (an export, a debug type, a header declaration, a source-graph edge) is proven absent only where its producer covered the scope; otherwise it is unknown.

| Side | Relationship | Subjects | Present | Proven absent | Unknown | Producers |
|---|---|---|---|---|---|---|
| old | `debug_type_of` (resolved_join) | debug_types | 0 | 0 | 0 | debug_section[debug]: not_run (no_debug_info)<br>header_ast[dependency_headers,headers]: not_run (no_header_ast) |
| old | `debug_type_of` (resolved_join) | header_types | 0 | 0 | 0 | debug_section[debug]: not_run (no_debug_info)<br>header_ast[dependency_headers,headers]: not_run (no_header_ast) |
| old | `declares` (observed) | — | — | — | — | header_ast[dependency_headers,headers]: not_run (no_header_ast) |
| old | `exports` (resolved_join) | declarations | 0 | 0 | 2 | export_table[export_table]: not_run (no_export_table)<br>header_ast[dependency_headers,headers]: not_run (no_header_ast) |
| old | `exports` (resolved_join) | exports | 0 | 0 | 0 | export_table[export_table]: not_run (no_export_table)<br>header_ast[dependency_headers,headers]: not_run (no_header_ast) |
| old | `references` (resolved_join) | — | — | — | — | header_ast[dependency_headers,headers]: not_run (no_header_ast) |
| new | `debug_type_of` (resolved_join) | debug_types | 0 | 0 | 0 | debug_section[debug]: not_run (no_debug_info)<br>header_ast[dependency_headers,headers]: not_run (no_header_ast) |
| new | `debug_type_of` (resolved_join) | header_types | 0 | 0 | 0 | debug_section[debug]: not_run (no_debug_info)<br>header_ast[dependency_headers,headers]: not_run (no_header_ast) |
| new | `declares` (observed) | — | — | — | — | header_ast[dependency_headers,headers]: not_run (no_header_ast) |
| new | `exports` (resolved_join) | declarations | 0 | 0 | 1 | export_table[export_table]: not_run (no_export_table)<br>header_ast[dependency_headers,headers]: not_run (no_header_ast) |
| new | `exports` (resolved_join) | exports | 0 | 0 | 0 | export_table[export_table]: not_run (no_export_table)<br>header_ast[dependency_headers,headers]: not_run (no_header_ast) |
| new | `references` (resolved_join) | — | — | — | — | header_ast[dependency_headers,headers]: not_run (no_header_ast) |

> **Policy**: `strict_abi`

## ❌ Breaking Changes

- **func_removed**: Public function removed: helper (`helper`)
  > Old binaries call a symbol that no longer exists; dynamic linker will refuse to load or crash at call site. (Evidence note: this run's available evidence does not fully confirm this specific finding -- treat the consequence above as plausible, not confirmed.)


## Disposition audit

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


## Related review groups

### 1. `helper`

- **Observed:** removed
- **Implication:** Public function removed: helper
- **Action:** Review the detailed finding and its evidence.
- **Gate contribution:** 1 finding(s)
- **Members:** `ef5c7cd29844b1ae`
- **Exact symbols:** `_Z6helperi`

---
## Legend

| Verdict | Meaning |
|---------|---------|
| ✅ NO_CHANGE | Identical ABI |
| ✅ COMPATIBLE | No incompatible ABI/API changes — may include additions and quality findings (backward compatible) |
| ⚠️ COMPATIBLE_WITH_RISK | Binary-compatible; verify target environment |
| ⚠️ API_BREAK | Source-level API change — recompilation required |
| ❌ BREAKING | Binary ABI break — recompilation required |

_Generated by [abicheck](https://github.com/abicheck/abicheck)_
