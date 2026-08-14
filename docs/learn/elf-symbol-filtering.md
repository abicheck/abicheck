---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - elf-symbol-filtering
depends_on:
  - abicheck/elf_symbol_filter.py
  - abicheck/dumper_elf_symbols.py
  - abicheck/dumper.py
lifecycle: active
generated: false
---

# ELF-Only Mode and Symbol Filtering

When `abicheck compare` (or `abicheck dump`) is run **without header files** — i.e.
directly against `.so` binaries — the tool operates in *ELF-only mode*. In this
mode the public ABI surface is inferred entirely from exported ELF symbols (`.dynsym`),
with no source-level type information available.

## Why false positives can occur in ELF-only mode

Shared libraries often contain exported symbols that are **not** part of their intended
public ABI:

| Symbol category | Example | Root cause |
|---|---|---|
| GCC / compiler internals | `ix86_tune_indices`, `_ZGVbN2v_sin` | Statically-linked compiler runtime (libgcc, SVML) leaks symbols into `.dynsym` |
| Transitive C++ stdlib symbols | `_ZNSt6thread8_M_startEv`, `_ZTISt9exception` | Weak-linked libstdc++ / libc++ symbols that appear in `.dynsym` |
| Private C namespace separators | `H5C__flush_marked_entries`, `MPI__send` | Internal `LibPrefix__FunctionName` naming convention — globally visible but not public API |

Comparing two versions of a library that differ in which compiler or stdlib they were
built against can trigger hundreds of spurious *BREAKING* findings (e.g. `mpfr 4.2.0→4.2.1`
reported 91 false-positive breaks caused by `ix86_*` symbols).

## How abicheck filters these symbols

`abicheck` applies an ABI-relevance filter (`_is_abi_relevant_symbol`) when parsing
`.dynsym` in ELF-only mode. Symbols are excluded when they match any of the following:

**GCC / compiler-internal prefixes** (`ix86_`, `x86_64_`, `__cpu_model`, `__cpu_features`,
`_ZGV*`, `__svml_*`, `__libm_sse2_*`, `__libm_avx_*`)

**C++ standard-library prefixes** (`_ZNSt`, `_ZNKSt`, `_ZNSt3__1`, `_ZdlPv`, `_ZnwSt`,
`_ZnaSt`, `_ZdaPv`, `_ZTVN10__cxxabiv`, `_ZTI`, `_ZTS`, `_ZSt`)

**Private C double-underscore separator** — any non-C++-mangled symbol (i.e. not
starting with `_Z`) whose name contains `__` after the first two characters.
This matches patterns like `H5C__flush` or `MPI__send` while leaving system symbols
(which start with `__` or `_[A-Z]`) unaffected.

## Limitations of the filter

- The filter is heuristic. A library that intentionally exports a symbol matching
  one of the filtered prefixes (unlikely but possible) will have it silently ignored.
- Non-standard SIMD / math libraries with different naming conventions are not covered;
  open an issue if you encounter new patterns causing false positives.
- In **header mode** (when headers are supplied), this filter is not applied — castxml
  provides accurate type information and the ELF surface is used only for visibility
  decisions, not for inferring the API surface.

## Mitigation for header mode

For the most accurate results, always supply public headers:

```bash
abicheck compare old.so new.so -H include/foo.h
```

This eliminates ELF-only mode entirely and removes the need for heuristic filtering.

## Header scoping on PE and Mach-O

Headers supplied via `-H/--header` (and the per-side `--header old=`/`--header new=`)
are now honored for PE (Windows DLL) and Mach-O (macOS dylib) inputs, not just ELF.
When headers are provided, the export-table surface is scoped to the symbols declared
in those public headers via castxml. This is **best-effort**:

- If castxml is unavailable, or the headers fail to parse, abicheck emits a warning and
  falls back to the full export table (the previous behavior).
- For C++ binaries built with **MSVC**, export names use MSVC mangling while castxml
  emits Itanium-mangled names, so declarations may not match the export table. When no
  declaration matches, abicheck warns and falls back to the export table. `extern "C"`
  and MinGW-built exports match by plain name and scope correctly.

Reachability-based public-surface filtering (keeping only the symbols and types reachable
from the public API, with an auditable trail of what was filtered and why) is **on by
default** (`--scope-public-headers`, add `--show-filtered` to print the audit ledger;
opt out with `--no-scope-public-headers`). Findings about symbols/types not reachable from
the public-header-declared exported API are recorded as *filtered* rather than reported, while
internal-type *leaks* are never hidden. Source-header provenance (distinguishing a
privately-included header from a public one independently of reachability) is implemented
across castxml, DWARF, and PDB (ADR-024 Phase 1); the one residual gap is MSVC C++ name
mangling on PE, where castxml can't match a mangled export and the surface falls back to
the export table with a `mangling-fallback` confidence note. See
[ADR-024](../contribute/adr/024-public-abi-surface-resolution.md).

See also: [Limitations & Known Boundaries](limitations.md) for the rest of
abicheck's practical boundary list, and
[Evidence & Detectability](evidence-and-detectability.md) for why L0
(symbols-only) evidence is structurally blind to source-level API changes.
