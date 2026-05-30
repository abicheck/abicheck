# Limitations & Known Boundaries

`abicheck` is designed to catch real ABI and API breaks with high accuracy, but has specific
limitations you should understand before relying on it in production.

---

## Platform support matrix

| Platform | Binary format | Binary metadata | Header AST (castxml) | Debug info cross-check |
|----------|--------------|:---------------:|:--------------------:|:----------------------:|
| Linux | ELF (`.so`) | Yes (pyelftools) | Yes (GCC, Clang) | Yes (DWARF) |
| Windows | PE/COFF (`.dll`) | Yes (pefile) | Yes (MSVC, MinGW) | Yes (PDB) |
| macOS | Mach-O (`.dylib`) | Yes (macholib) | Yes (Clang, GCC) | Yes (DWARF) |

**Header AST analysis** (via castxml) is available on all platforms. castxml is
maintained by Kitware and available via conda-forge, Homebrew, apt, or direct download.

**Debug info cross-check** uses DWARF (Linux and macOS) and PDB (Windows). PDB
support extracts struct/class/union layouts, enum types, calling conventions, and
toolchain info from PDB files produced by MSVC (`/Zi` flag). Use `--pdb-path` to
specify the PDB file location if automatic discovery fails.

---


### Windows toolchain distinction

Windows support depends on the compiler/toolchain used for headers and binary production:

| Toolchain | Status | Notes |
|----------|--------|-------|
| MinGW (GCC) | **Experimental** | Covered by current CI smoke/integration jobs. |
| MSVC (`cl.exe`) | **Untested in CI** | Expected to work in many cases, but not yet validated end-to-end in project CI. |

Tracked ABICC compatibility issues for this area: **#9, #50, #56, #121**.
For detailed matrix + per-issue notes, see [Platform Support](../reference/platforms.md#windows-toolchain-support-matrix).

## Header / Binary Mismatch Risk

**The most important limitation.** `abicheck` uses `castxml` to parse headers and
compares the result against the compiled `.so`. If the headers passed to analysis
don't exactly match what was compiled, results will be unreliable.

**This happens when:**
- You pass generic system headers but the library was compiled with custom `#define` flags
- Preprocessor macros change the public API surface (`#ifdef FEATURE_X`)
- Third-party dependency headers differ between versions
- Platform-specific code paths (`#ifdef __linux__`) differ between compile and analysis environments

**Mitigation:**
- Always use the exact same headers that were used to build the `.so`
- Pass compile-time defines to castxml: `abicheck dump libfoo.so -H foo.h --castxml-arg=-DFEATURE_X`
- For `abicheck compat`, use `-s` (strict mode) to promote `COMPATIBLE`/`API_BREAK` to BREAKING:
  `abicheck compat check -lib foo -old OLD.xml -new NEW.xml -s`
  (use `--strict-mode api` to promote only `API_BREAK`; `-s` is not available on `abicheck compare`)
- Cross-check with `abicheck compat check` (ABICC mode) for independent validation

---

## Stripped Production Binaries

Tiers 3 and 4 (DWARF layout + Advanced DWARF) require debug symbols (`-g`).
Production `.so` files are typically stripped — in this case:

- Struct field offset changes may be missed (Tier 3 unavailable)
- Calling convention drift, struct packing changes not detected (Tier 4 unavailable)
- Tier 1 (castxml/headers) and Tier 2 (ELF symbols) still run — most critical breaks caught

**Mitigation:** Use `--debug-root` to point abicheck at separate debug files
(distro debuginfo packages, build-id trees, or dSYM bundles). abicheck
automatically searches for debug artifacts via a resolver chain. For
Fedora/RHEL, use `--debuginfod` to fetch debug info by build-id from
debuginfod servers. See the [CLI usage guide](../user-guide/cli-usage.md) for
details. For production binaries without debug info, Tier 1+2 analysis covers
the majority of real-world ABI breaks.

---

## Template Instantiation

C++ template instantiations with complex type parameters can produce unexpected results:
- Explicit instantiations in `.so` are analyzed; implicit instantiations in headers are not
- Template specializations may not all be captured
- `case17_template_abi` in the examples demonstrates a detectable case

**Mitigation:** Use explicit template instantiation (`template class Foo<int>;`) for
ABI-sensitive types you want to guarantee are tracked.

---

## `COMPATIBLE` Does Not Mean "Invisible"

`COMPATIBLE` changes are detected and reported — they are not silent. Examples:
- Adding a new export symbol is `COMPATIBLE` but grows the library's API surface
  (relevant for semver policy: additive changes may still require a minor version bump)
- Enum member addition is `COMPATIBLE` but can affect exhaustive `switch` statements

For `abicheck compat` pipelines, use `-s` to treat `COMPATIBLE` as blocking.
For `abicheck compare` pipelines, enforce via CI exit code logic (treat exit `2` as failure).

---

## `compat` Mode Verdict Limitations

`abicheck compat` *does* emit exit code `2` for `API_BREAK` conditions, but the
report text uses ABICC-style phrasing rather than a bare `API_BREAK` verdict string.
Source-level-only breaks (e.g. `case31_enum_rename`, `case34_access_level`) will
appear as warnings in the compat HTML/text report.

Use `abicheck compare --format json` for precise machine-readable `API_BREAK` verdicts.

---

## Inline / Header-Only Code

Functions defined entirely in headers (inline, `constexpr`, template) may not appear
in the `.so` symbol table. `abicheck` analyzes the public exported ABI — header-only
changes that don't affect exported symbols will not be detected.

---

## Dependency Limitations & Known Bugs

Known issues in third-party dependencies that affect `abicheck` behavior.

### castxml: `__has_cpp_attribute` not defined on macOS (Xcode 16.4+)

**Status:** Open — upstream castxml issue to be filed.

**Affected platforms:** macOS with Xcode 16.4+ (Apple Clang headers).

**Symptom:** When castxml processes a C header that includes `<stddef.h>`, the
macOS SDK resolves this through the libc++ `__config` header, which uses the
`__has_cpp_attribute` preprocessor macro. castxml does not define this macro,
causing parse failures:

```text
.../MacOSX.sdk/usr/include/c++/v1/__config:1009:7: error:
  function-like macro '__has_cpp_attribute' is not defined
```

Multiple lines in `__config` trigger the same error wherever
`__has_cpp_attribute(...)` appears in `#if` / `#elif` directives.

**Root cause:** Per the C++ standard, `__has_cpp_attribute` should be a built-in
macro that evaluates to 0 for unknown attributes. castxml's internal
preprocessor does not predefine it, so the preprocessor treats the bare
identifier as an error rather than defaulting to 0.

**Workaround:** In castxml-specific shim headers (not general project headers),
replace `#include <stddef.h>` with `typedef __SIZE_TYPE__ size_t;` to avoid the
libc++ header chain entirely. `__SIZE_TYPE__` is a GCC/Clang built-in that
castxml supports.

> **Caution:** This typedef only supplies `size_t` — other `<stddef.h>`
> definitions (`NULL`, `ptrdiff_t`, `offsetof`, `max_align_t`) are not
> available. Do not use this substitution in normal build headers as it will
> break compilation that depends on those definitions. Safer alternatives:
> create an isolated shim header used only by castxml invocations, or provide
> a minimal custom header that supplies all needed type definitions.

---

## Troubleshooting

See [troubleshooting.md](../troubleshooting.md) for a diagnostic decision tree
covering common false positives, false negatives, and unexpected verdicts.

---

## ELF-Only Mode and Symbol Filtering

When `abicheck compare` (or `abicheck dump`) is run **without header files** — i.e.
directly against `.so` binaries — the tool operates in *ELF-only mode*.  In this
mode the public ABI surface is inferred entirely from exported ELF symbols (`.dynsym`),
with no source-level type information available.

### Why false positives can occur in ELF-only mode

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

### How abicheck filters these symbols

`abicheck` applies an ABI-relevance filter (`_is_abi_relevant_symbol`) when parsing
`.dynsym` in ELF-only mode.  Symbols are excluded when they match any of the following:

**GCC / compiler-internal prefixes** (`ix86_`, `x86_64_`, `__cpu_model`, `__cpu_features`,
`_ZGV*`, `__svml_*`, `__libm_sse2_*`, `__libm_avx_*`)

**C++ standard-library prefixes** (`_ZNSt`, `_ZNKSt`, `_ZNSt3__1`, `_ZdlPv`, `_ZnwSt`,
`_ZnaSt`, `_ZdaPv`, `_ZTVN10__cxxabiv`, `_ZTI`, `_ZTS`, `_ZSt`)

**Private C double-underscore separator** — any non-C++-mangled symbol (i.e. not
starting with `_Z`) whose name contains `__` after the first two characters.
This matches patterns like `H5C__flush` or `MPI__send` while leaving system symbols
(which start with `__` or `_[A-Z]`) unaffected.

### Limitations of the filter

- The filter is heuristic.  A library that intentionally exports a symbol matching
  one of the filtered prefixes (unlikely but possible) will have it silently ignored.
- Non-standard SIMD / math libraries with different naming conventions are not covered;
  open an issue if you encounter new patterns causing false positives.
- In **header mode** (when headers are supplied), this filter is not applied — castxml
  provides accurate type information and the ELF surface is used only for visibility
  decisions, not for inferring the API surface.

### Mitigation for header mode

For the most accurate results, always supply public headers:

```bash
abicheck compare old.so new.so -H include/foo.h
```

This eliminates ELF-only mode entirely and removes the need for heuristic filtering.

### Header scoping on PE and Mach-O

Headers supplied via `-H/--header` (and the per-side `--old-header`/`--new-header`)
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
from the public API, with an auditable trail of what was filtered and why) is available as
an **opt-in** mode: pass `--scope-public-headers` (add `--show-filtered` to print the audit
ledger) to `abicheck compare`. Findings about symbols/types not reachable from the
public-header-declared exported API are recorded as *filtered* rather than reported, while
internal-type *leaks* are never hidden. Full source-header provenance (distinguishing a
privately-included header from a public one independently of reachability) remains future
work. See [ADR-024](../development/adr/024-public-abi-surface-resolution.md).
