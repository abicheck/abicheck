# ADR-020a: Build-Context Aware Header Extraction

**Date:** 2026-03-23
**Status:** Accepted
**Decision maker:** Nikolay Petrov

---

## Context

### The header parse drift problem

abicheck's Layer 2 (header AST via CastXML) is only as correct as the compile
environment used to parse headers. Today, users manually specify `--compiler-options`,
`--includes`, and `--defines` via CLI flags. The dumper passes these through to
CastXML as a single global set of flags.

This creates the single largest source of false positives and false negatives in
production deployments:

- **False positives**: CastXML sees type definitions that differ from what the
  compiler actually compiled (wrong `-D` flags, missing feature toggles, different
  `-std=` mode)
- **False negatives**: ABI-relevant changes hidden behind `#ifdef` blocks that
  CastXML doesn't enter because it lacks the correct preprocessor defines
- **Configuration variants**: A library built with `-DFOO_ENABLE_SSL=1` has a
  different ABI surface than the same library built without it — same headers,
  different exported types

### Current CLI interface

```bash
abicheck dump libfoo.so \
    -H include/ \
    --compiler-options "-std=c++17 -DFOO_ENABLE_SSL=1" \
    --includes "/usr/include/openssl"
```

This works for simple cases but breaks at scale:
- Multi-TU libraries have per-file flags (different `-D`, `-I`, `-std=` per source file)
- Build systems already know all flags — re-specifying them is error-prone
- Feature-flagged builds require users to know which defines were active
- Cross-compilation targets (sysroot, target triple) are easy to get wrong

### The compilation database standard

The Clang/LLVM ecosystem defines `compile_commands.json` (JSON Compilation Database)
as the standard interchange format for build metadata. CMake, Meson, Ninja, Bear,
and other build systems generate it. Format:

```json
[
  {
    "directory": "/build",
    "file": "src/foo.cpp",
    "arguments": ["c++", "-std=c++17", "-DFOO_ENABLE_SSL=1", "-I/usr/include/openssl", "-Iinclude", "-c", "src/foo.cpp"]
  },
  {
    "file": "src/bar.c",
    "command": "cc -std=c11 -Iinclude -c src/bar.c",
    "directory": "/build"
  }
]
```

Each entry captures the **exact** compiler invocation for one translation unit.
Entries use either `arguments` (JSON array) or `command` (shell string, parsed
via `shlex.split()`). Both forms must be supported.

### What other tools do

- **Android VNDK header-abi-dumper**: Runs per-TU with the exact compiler flags from
  the build system. The build system invokes the dumper — it doesn't ingest a database.
- **clang-tidy / clangd**: Consume `compile_commands.json` natively to match analysis
  to build context. clangd uses per-header TU matching: it finds the "best" compile
  command for each header file.
- **libabigail / ABICC**: Do not consume compilation databases. Users supply headers
  and include paths manually.

### Options considered

| Option | Description | Trade-off |
|--------|-------------|-----------|
| A: Manual flags only (status quo) | User specifies all flags | Error-prone at scale; header parse drift |
| **B: compile_commands.json ingestion** | Parse database, derive flags per public header | Deterministic; matches real build; standard format |
| C: Build system plugins | CMake/Meson plugins invoke abicheck during build | Tight coupling; only works during build; not post-hoc |
| D: Compiler wrapper interception | LD_PRELOAD/wrapper records flags | Fragile; security concerns; doesn't work for all build systems |

---

## Decision

### 1. Accept `compile_commands.json` as a first-class input

New CLI flags:

```bash
# Point to build directory containing compile_commands.json
abicheck dump libfoo.so -H include/ -p /path/to/builddir

# Or point to the file directly
abicheck dump libfoo.so -H include/ --compile-db /path/to/compile_commands.json

# Combined with explicit overrides (overrides take precedence)
abicheck dump libfoo.so -H include/ -p builddir --compiler-options "-DEXTRA=1"

# Filter to specific source files (for large databases)
abicheck dump libfoo.so -H include/ -p builddir --compile-db-filter "src/libfoo/**"
```

`-p <builddir>` mirrors clang-tidy's convention. When specified, abicheck looks
for `<builddir>/compile_commands.json`.

**Prerequisite**: `-p` / `--compile-db` requires `-H` (headers). Without headers,
CastXML has nothing to parse and the build context is irrelevant. If `-p` is
specified without `-H`, emit an error: "compile database requires --headers/-H".

### 2. Per-header TU matching (preferred) with union fallback

The compilation database contains per-TU flags that may differ across source files.
Rather than computing a global union of all flags (which breaks for mutually exclusive
defines like `-DUSE_OPENSSL=1` vs `-DUSE_GNUTLS=1`), abicheck matches each public
header to the best TU and uses that TU's exact flags:

```text
Per-header TU matching (Phase 1 strategy):
  For each public header H in -H directories:
    1. Find TUs that directly #include H
       (scan compile_commands entries for -include or grep source files)
    2. If multiple TUs include H with different flags → warn, use first match
    3. If no TU directly includes H → fall back to union strategy for H
    4. Use the matched TU's exact flags for CastXML invocation on H

Union fallback (for headers not matched to any TU):
  - Union all -D flags; warn on conflicting values
  - Union all -I paths (order: most-common-first)
  - Target/sysroot: must be consistent across TUs (error if conflicting)
```

This mirrors clangd's approach: find the best compile command for each file rather
than merging incompatible commands.

**C vs C++ handling**: If TUs mix C and C++ standards (e.g., `-std=c11` and
`-std=c++17`), these are different languages — not comparable on a single axis.
Resolution: use C++ mode for CastXML when any C++ TU is present, since CastXML
in C++ mode can still parse C headers via `extern "C"` blocks. Emit a warning:
"mixed C/C++ TUs detected; using C++ mode for header parsing." This aligns with
the existing `force_cpp` detection logic in `dumper.py`.

### 3. Flag derivation and the BuildContext model

```python
@dataclass
class BuildContext:
    """Compilation context derived from compile_commands.json."""
    defines: dict[str, str | None]   # -D macro=value pairs (None = defined without value)
    undefines: set[str]              # -U macros
    include_paths: list[Path]        # -I paths (ordered)
    system_includes: list[Path]      # -isystem paths
    language_standard: str | None    # -std=c++17, -std=c11, etc.
    target_triple: str | None        # --target=x86_64-linux-gnu
    sysroot: Path | None             # --sysroot=
    extra_flags: list[str]           # Remaining flags passed through to CastXML
    compile_db_path: Path            # Path to compile_commands.json (for diagnostics)

    # Conflict tracking
    define_conflicts: dict[str, list[str]]  # macro → [value1, value2, ...]
    standard_variants: list[str]            # all -std= values seen

def build_context_for_header(
    compile_db: list[dict],
    header_path: Path,
    source_filter: str | None = None,
) -> BuildContext:
    """Find the best TU for a header and derive its build context."""

def build_context_union_fallback(
    compile_db: list[dict],
    source_filter: str | None = None,
) -> BuildContext:
    """Union strategy for headers not matched to a specific TU."""
```

Flags extracted per-TU:
- `-D` / `-U` defines
- `-I` / `-isystem` include paths
- `-std=` language standard
- `--target=` / `-target` triple
- `--sysroot=`
- `-fvisibility=`
- `-fabi-version=`
- `-f[no-]exceptions`, `-f[no-]rtti`
- `-fpack-struct=`, `-fms-extensions`

The `command` string form is parsed via `shlex.split()` (POSIX mode). On Windows
builds where `command` may use CMD quoting, the parser handles both conventions.

### 4. CastXML integration

CastXML supports configuring its internal Clang preprocessor to match an external
compiler. The build context feeds into CastXML invocation:

```python
def _build_castxml_args(
    context: BuildContext, header: Path, gcc_path: str = "g++"
) -> list[str]:
    args = ["castxml", "--castxml-output=1"]

    # Compiler emulation: --castxml-cc-gnu expects a compiler executable
    # (e.g., "g++", "gcc", or a cross-compiler path), NOT a language standard.
    # The language standard is passed as a separate -std= flag.
    args.extend(["--castxml-cc-gnu", gcc_path])

    # Language standard (separate flag, not part of --castxml-cc-gnu)
    if context.language_standard:
        args.append(f"-std={context.language_standard}")

    # Target
    if context.target_triple:
        args.extend([f"--target={context.target_triple}"])

    # Defines
    for macro, value in context.defines.items():
        if value is not None:
            args.append(f"-D{macro}={value}")
        else:
            args.append(f"-D{macro}")

    # Includes
    for inc in context.include_paths:
        args.extend(["-I", str(inc)])
    for inc in context.system_includes:
        args.extend(["-isystem", str(inc)])

    # Extra flags passthrough
    args.extend(context.extra_flags)

    args.append(str(header))
    return args
```

**Interaction with existing CLI flags**: When both `-p` (compile database) and
`--compiler-options` / `--includes` / `--sysroot` / `--gcc-path` / `--gcc-prefix`
are specified, the explicit CLI flags take precedence and override the corresponding
values from the database. This matches the principle of explicit user intent
overriding automatic detection.

### 5. Deterministic caching

Build-context awareness enables content-addressed caching of CastXML results.

The cache key must include **transitive header content**, not just the top-level
header. This matches the existing cache behavior in `dumper.py` which walks
include directories and hashes mtimes of `.h`/`.hpp` files.

```python
def _cache_key(header_path: Path, context: BuildContext, header_dirs: list[Path]) -> str:
    """Content-addressed cache key for deterministic header parsing.

    Every BuildContext field that affects CastXML output must be included.
    Missing a field here causes false cache hits and wrong ABI results.
    """
    h = hashlib.sha256()
    # Top-level header content
    h.update(header_path.read_bytes())
    # Transitive includes: walk header directories and hash mtimes
    for hdir in sorted(header_dirs):
        for p in sorted(hdir.rglob("*.h")) + sorted(hdir.rglob("*.hpp")):
            h.update(str(p).encode())
            h.update(str(p.stat().st_mtime_ns).encode())
    # All ABI-affecting BuildContext fields
    h.update(json.dumps(context.defines, sort_keys=True).encode())
    h.update(json.dumps(sorted(context.undefines)).encode())
    h.update(json.dumps([str(p) for p in context.include_paths]).encode())
    h.update(json.dumps([str(p) for p in context.system_includes]).encode())
    h.update((context.language_standard or "").encode())
    h.update((context.target_triple or "").encode())
    h.update(str(context.sysroot or "").encode())
    h.update(json.dumps(sorted(context.extra_flags)).encode())
    return h.hexdigest()[:16]
```

Cache location: `$XDG_CACHE_HOME/abicheck/castxml/` or `--cache-dir` with
`castxml/` subdirectory. See ADR-021a for unified cache strategy across subsystems.

Cache invalidation: any change to header content, header mtimes, or build flags
produces a new key.

### 6. Conflict handling

When TUs disagree on flags (relevant only for the union fallback path):

| Conflict type | Resolution | Diagnostic |
|---------------|------------|------------|
| Different `-D` values for same macro | Warning; use value from first-matched TU | Warning: "macro FOO has conflicting values across TUs: 1 (src/a.cpp), 2 (src/b.cpp)" |
| Mixed C and C++ `-std=` | Use C++ mode (CastXML can parse C in C++ mode) | Warning: "mixed C/C++ TUs; using C++ mode" |
| Different C++-only `-std=` | Use the highest standard | Warning: "mixed standards c++14, c++17; using c++17" |
| Different targets | Error — cannot unify | Error: "conflicting target triples; use --compiler-options to override" |
| Different sysroots | Error | Error: "conflicting sysroots" |

### 7. Fallback behavior

```text
-p or --compile-db specified?
├── YES → -H specified?
│         ├── YES → Per-header TU matching with union fallback
│         │         Warn on conflicts, override with --compiler-options
│         └── NO  → Error: "compile database requires --headers/-H"
│
└── NO  → Current behavior (manual flags via --compiler-options / --includes)
```

No silent fallback when `-p` is explicitly given but the file is missing — fail
loud: "compile_commands.json not found in {builddir}".

---

## Consequences

### Positive
- Eliminates header parse drift — the most common source of ABI tool inaccuracy
- Standard format understood by all major build systems (CMake, Meson, Ninja, Bear)
- Per-header TU matching avoids the mutually-exclusive-defines problem
- Enables deterministic caching keyed by (header content + transitive includes + flags)
- Convention (`-p`) familiar to clang-tidy/clangd users
- `--compile-db-filter` provides escape hatch for large databases (e.g., kernel: 30K+ entries)

### Negative
- `compile_commands.json` only captures TU flags — not link-time or install-time transforms
- Per-header TU matching requires scanning source files for `#include` directives (I/O cost)
- Union fallback may over-include when headers can't be matched to specific TUs
- Requires build system to generate `compile_commands.json` (not always default)
- Cache must hash transitive includes (more expensive than single-file hash)
- Per-TU flag extraction parsing must handle both `arguments` array and `command` string
  (via `shlex.split()`) formats

### Known limitation: public header scope resolution

Filtering the ABI surface to only types reachable from public headers is a
valuable improvement but architecturally independent of compile database ingestion.
It deserves a separate ADR. This ADR focuses on getting the right flags to CastXML;
scope filtering can layer on top once build context is reliable.

> **Update:** that separate ADR is now
> [ADR-024 — Public ABI Surface Resolution and False-Positive Traceability](024-public-abi-surface-resolution.md).

---

## Implementation Plan

| Phase | Scope | Effort |
|-------|-------|--------|
| 0 | Plumb `BuildContext` / `compile_db_path` through `dump()` → `_dump_elf()` → `_castxml_dump()` | 1-2 days |
| 1 | `BuildContext` model + `compile_commands.json` parser (both `arguments` and `command` forms) | 2-3 days |
| 2 | Flag extraction (defines, includes, std, target, sysroot) | 2-3 days |
| 3 | Per-header TU matching (source scanning for `#include` directives) | 2-3 days |
| 4 | Union fallback + conflict detection and resolution logic | 1-2 days |
| 5 | CastXML integration — feed `BuildContext` into dumper pipeline | 2-3 days |
| 6 | CLI: `-p`, `--compile-db`, `--compile-db-filter`, interaction with existing flags | 1-2 days |
| 7 | Deterministic cache (content-addressed with transitive include hashing) | 2-3 days |
| 8 | Tests: multi-TU, mixed C/C++, conflict cases, per-header matching, cache hit/miss | 2-3 days |

---

## References

- `abicheck/dumper.py` — current CastXML invocation (`_castxml_dump`) and cache logic
- ADR-003 — Data Source Architecture (L0/L1/L2 pipeline, `--show-data-sources`)
- ADR-021a — Debug Artifact Resolution (unified `--cache-dir` strategy; split DWARF
  flags in compile_commands.json produce artifacts resolved by ADR-021a)
- Clang JSON Compilation Database specification
