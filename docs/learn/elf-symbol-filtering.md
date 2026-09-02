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
directly against `.so` binaries — the public ABI surface is inferred from
exported ELF symbols (`.dynsym`), with no source-level type information
available. This is not automatically "symbols only," though: if the binary
carries usable DWARF debug info, `dumper._dump_elf()` tries
`_try_dwarf_snapshot()` first and gets real L1 type/layout evidence from it,
falling back to a pure `.dynsym`-derived snapshot
(`_build_symbol_only_snapshot()`) only when DWARF is absent or unusable.
"ELF-only" in the strict, symbols-only sense applies to a stripped binary
with no headers and no usable debug info — an unstripped no-header build can
still produce real layout findings, which aren't the heuristic-filter false
positives this page is about.

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

`abicheck` applies an ABI-relevance filter, `is_abi_relevant_elf_symbol()` in
`abicheck/elf_symbol_filter.py`, when reading `.dynsym`. It excludes a symbol
when it matches any of several
categories — the function itself, not this page, is the exact list, since a
hand-copied prefix table here would drift the moment the filter changes:

- **ELF linker artifacts** — exact names like `_init`/`_fini`/`__bss_start`
  emitted by the toolchain/linker itself, never part of a library's own ABI.
- **Virtual-override thunks** (`_ZTh`/`_ZTv`/`_ZTc`) — compiler-generated
  vtable artifacts whose churn mirrors the owning class's vtable; owned by a
  separate detector, not reported as their own symbol here.
- **GCC/compiler-internal prefixes** (`ix86_`, `x86_64_`, `__cpu_model`,
  `__cpu_features`, `_ZGV*`, `__svml_*`, `__libm_sse2_*`, `__libm_avx_*`) —
  statically-linked compiler runtime (libgcc, SVML) leaking into `.dynsym`.
- **C++ standard-library prefixes** — `std::`/`__gnu_cxx::`/`__cxxabiv1::`
  and their mangled equivalents (`_ZNSt`, `_ZNKSt`, and the volatile/
  ref-qualified variants, `_ZdlPv`/`_ZnwSt`/etc. for `new`/`delete`), plus
  the *stdlib/runtime-namespaced* RTTI prefixes from the shared
  `STDLIB_RTTI_PREFIXES` table (`name_classification.py`) — **not** a
  blanket `_ZTI`/`_ZTS` match: a user type's own RTTI symbols are not
  filtered by this rule, only the standard library's.
- **Private C double-underscore separator** — any non-C++-mangled symbol
  (not starting with `_Z`) whose name contains `__` after the first two
  characters — matches `H5C__flush`/`MPI__send`-style internal naming. Only
  the *first two* characters are exempt, so a leading `__` alone doesn't
  protect a name: `__libc_start_main` survives (no further `__`), but
  `__gmon_start__` is filtered by its trailing `__`.

This filter runs whenever a symbol's visibility is ELF-derived, **whether or
not headers were supplied** — supplying `-H` does not bypass it. A library
that intentionally exports a name matching one of these categories (unlikely,
but the private-separator heuristic in particular can false-positive on a
real convention like `MPI__send`) has that export silently dropped from the
inferred surface either way; open an issue if you hit a real case.

## Limitations of the filter

- The filter is heuristic and, per above, applies with or without headers.
  A library that intentionally exports a name matching one of its categories
  will have it silently ignored regardless of `-H`.
- Non-standard SIMD / math libraries with different naming conventions are not
  covered; open an issue if you encounter new patterns causing false positives.

## Why headers still help

Supplying headers does not bypass the filter above, but it materially
improves accuracy elsewhere: it gives abicheck real source-level type
information (instead of inferring everything from exported names alone) and
scopes the public surface to what the headers actually declare.

```bash
abicheck compare old.so new.so -H include/foo.h
```

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

---

**Ladder:** ← [What Each Level Sees](what-each-level-sees.md) · Concepts c2 · The evidence model · [Limitations & Known Boundaries](limitations.md) →
