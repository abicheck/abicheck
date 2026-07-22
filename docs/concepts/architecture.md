---
doc_type: explanation
audience:
  - library-maintainer
  - contributor
level: intermediate
summarizes:
  - platform-support-matrix
lifecycle: active
generated: false
---

# Architecture

## Overview

abicheck is a Python CLI tool that compares two versions of a C/C++ shared library
to detect ABI and API incompatibilities. Its core design idea is to reason over
**five independent sources of information** about a library — the binary, its
debug symbols, its public headers, its build-system data, and (optionally) its
sources — instead of relying on a single data source. Each source is an additive
**evidence layer** (`L0`–`L4`); feeding more layers both finds breaks the
weaker layers are blind to and suppresses false positives they would raise. See
[Evidence layers: the five sources](#evidence-layers-the-five-sources) below for
the model, and [Evidence & Detectability](evidence-and-detectability.md) for the
conceptual companion.

abicheck supports Linux (ELF), Windows (PE/COFF), and macOS (Mach-O), each with
full binary-metadata, header-AST, and debug-info cross-check support — see the
[platform support matrix](limitations.md#platform-support-matrix) for the
per-platform tool/format breakdown.

---

## Analysis pipeline

The CLI dumps each input into a normalized snapshot, enriches it with header
AST and debug-info layers, then diffs the two snapshots to produce a verdict:

```mermaid
flowchart TD
    CLI["abicheck CLI<br/>(dump · compare · compat check/dump)"]
    FMT{"Format detection<br/>(ELF / PE / Mach-O)"}
    ELF["ELF<br/>pyelftools"]
    PE["PE/COFF<br/>pefile"]
    MACHO["Mach-O<br/>macholib"]
    SNAP["L0 — Binary metadata<br/>Snapshot (JSON model)"]
    AST["L2 — Header AST<br/>castxml (all platforms)"]
    DBG["L1 — Debug-info cross-check<br/>DWARF (Linux, macOS) · PDB (Windows)"]
    CHK["Checker → Changes → Verdict"]

    CLI --> FMT
    FMT --> ELF
    FMT --> PE
    FMT --> MACHO
    ELF --> SNAP
    PE --> SNAP
    MACHO --> SNAP
    SNAP --> AST
    AST --> DBG
    DBG --> CHK
```

The analysis layers are independent and additive — each catches changes the
others miss, and the checker reconciles them into a single verdict. The
artifact layers (L0/L1/L2) are described in detail below; the build/source
layers (L3/L4, plus the optional L5 reachability graph) are covered in
[Build & Source Packs](build-source-data.md).

---

## Evidence layers: the five sources

abicheck's accuracy comes from treating compatibility analysis as a question of
*evidence*: the more independent sources of information you give it about a
library, the more it can prove — and the fewer false positives it raises. You
**provide five** sources (`L0`–`L4`); abicheck **derives a sixth**, the `L5`
graph — **six evidence layers in all**, layered from the least input to the most:

| Layer | Source | Collected from | Authority | Reveals |
|:-----:|--------|----------------|-----------|---------|
| **L0** | Just the **binary** | ELF/PE/Mach-O parsers (`elf_metadata.py`, `pe_metadata.py`, `macho_metadata.py`) | Authoritative | Exported symbols, SONAME/install-name, versions, visibility, binding, dependencies |
| **L1** | **Debug symbols** | DWARF/PDB/BTF/CTF (`dwarf_*`, `pdb_*`, `btf_metadata.py`, `ctf_metadata.py`) | Authoritative when matched to the binary | Type **layout**: sizes, field offsets, enum values, vtable slots, calling convention, packing |
| **L2** | **Public headers** | castxml or clang AST (`dumper_castxml.py` / `dumper_clang.py`, `--ast-frontend`) | Authoritative for header-visible API | Source **API**: signatures, overloads, access, `final`/`explicit`/`noexcept`, templates, public/internal scoping |
| **L3** | **Build system data & options** | compile DB / CMake / Ninja / Bazel / Make (`build_context.py`, build/source pack ADR-029) | Context / confidence | ABI-relevant flags (`-std`, `_GLIBCXX_USE_CXX11_ABI`, `-fvisibility`, `-fabi-version`), toolchain, target graph, export policy |
| **L4** | **Sources** | per-TU source ABI replay (build/source pack ADR-030) | Source-/API-risk evidence, never sole shipped-ABI authority | Macro/`constexpr` values, default-argument values, inline/template bodies, uninstantiated templates |
| **L5** | **Source/build graph** *(derived)* | folded from L3 (+ any L4 surface) into a graph summary (build/source pack ADR-031) | Explanation / localization / impact, never shipped-ABI authority | Include/type/call reachability: which public surface a change reaches; prioritizes cross-symbol impact |

```mermaid
flowchart LR
    L0["L0 · binary<br/>(stripped .so)"] --> L1["L1 · + debug<br/>(DWARF/PDB)"]
    L1 --> L2["L2 · + headers<br/>(castxml / clang AST)"]
    L2 --> L3["L3 · + build data<br/>(compile DB)"]
    L3 --> L4["L4 · + sources<br/>(build/source pack)"]
    L3 -.derived.-> L5["L5 · source/build graph<br/>(reachability)"]
    L4 -.derived.-> L5
    L0 -.weaker evidence.-> L4
```

**The authority rule (ADR-028).** The layers are not a fallback chain — abicheck
overlays everything it is given and computes one worst-wins verdict. But not all
evidence carries the same weight:

> Artifact-backed **L0/L1/L2** evidence is **authoritative** for the shipped-ABI
> verdict. Build/source **L3/L4/L5** evidence may *explain, localize, scope, or
> add confidence to* a finding, and may raise its own source-/API-level findings
> (default `API_BREAK` or risk) — but it **never silently deletes** an
> artifact-proven break.

So L3 noticing a `-std` bump or L4 noticing a changed macro can *add* a finding
or *explain* one, but only L0/L1/L2 can declare a binary `BREAKING`. Every
compare that uses build/source evidence prints an **evidence-coverage** table
(and a structured `layer_coverage` array in JSON) so consumers can tell which
findings are artifact-proven vs. context-only — see [Build & Source Packs](build-source-data.md).

**Graceful degradation.** `abicheck dump --dry-run` reports exactly
which of L0/L1/L2 a binary affords (as of this writing it lists per-layer
presence and basic stats — symbol/type/enum counts — not a detector-enabled
fraction). With less input abicheck degrades down the staircase rather than
failing; with more it both finds more and false-positives less. The empirical
per-tier behaviour across the example catalog is benchmarked in [Tool
Comparison §Benchmarking by evidence
tier](../reference/tool-comparison.md#benchmarking-by-evidence-tier) — that
page's detector-fraction table is a stale snapshot too (registered-detector
count has grown since it was captured); re-run
`scripts/benchmark_comparison.py --evidence-tiers` for current numbers.

---

## Artifact layers in detail

### Layer L0: Binary metadata

Reads native binary metadata using format-specific parsers:

**ELF** (Linux, via `pyelftools`):
- Exported symbols (functions, variables) from `.dynsym`
- SONAME, symbol binding (GLOBAL, WEAK, LOCAL), symbol versioning
- NEEDED dependencies, visibility attributes

**PE/COFF** (Windows, via `pefile`):
- Exported functions and ordinals from the export table
- Imported DLLs and functions from the import table
- Machine type, characteristics, DLL characteristics
- File and product version from VS_FIXEDFILEINFO resource

**Mach-O** (macOS, via `macholib`):
- Exported symbols from the symbol table (including weak definitions)
- Install name (LC_ID_DYLIB — equivalent of ELF SONAME)
- Dependent libraries (LC_LOAD_DYLIB — equivalent of ELF DT_NEEDED)
- Re-exported libraries (LC_REEXPORT_DYLIB)
- Current and compatibility versions, minimum OS version
- Fat/universal binary support (automatic architecture selection)

### Layer L2: Header AST (castxml / Clang) — all platforms

Parses C/C++ headers through a selectable frontend — `--ast-frontend
auto|castxml|clang|hybrid` (or `ABICHECK_AST_FRONTEND`);
`auto` prefers castxml and
falls back to clang `-ast-dump=json` on clang-only hosts (ADR-003); `hybrid`
(G28 Phase 3) runs both and merges them. The rest of
this section describes the castxml backend. The clang backend exposes the same
declaration surface (signatures, classes/bases, enums, typedefs, access,
`noexcept`, templates) but is a **syntactic** AST: it does **not** compute record
layout, so `size_bits`/`offset_bits`/vtable slots stay unset and the layout
detectors skip an unknown-vs-unknown comparison — **DWARF (L1) remains the layout
authority** on a clang-only host. With that caveat, the header AST extracts:

- Function signatures (parameters, return types)
- Class/struct definitions; layout when backed by castxml or DWARF evidence
- Virtual method tables (vtable slot ordering) when backed by castxml or DWARF
  evidence
- Enum values and member names
- Typedefs and template instantiations
- `noexcept` specifications
- Access levels (public, protected, private)

castxml is a cross-platform tool maintained by Kitware (available via conda-forge,
system packages, or direct download for Linux, Windows, and macOS). It is the primary
source for type-level analysis, catching changes invisible to debug-info-only tools:
`noexcept`, `static` qualifier, const qualifier, access level changes.

**Compiler support:** castxml uses an **internal Clang compiler** for parsing but
**emulates** the preprocessor defines, include paths, and target platform of an external
compiler via `--castxml-cc-<id> <compiler-binary>`. At invocation castxml calls the
external compiler to discover its built-in defines (e.g. `__GNUC__`, `__GNUC_MINOR__`,
`_MSC_VER`) and default include search paths, then injects those into its internal Clang
so the resulting AST matches what the external compiler would produce.

| Compiler ID | Compiler | Typical platforms |
|-------------|----------|-------------------|
| `gnu` | GCC / g++ | Linux, macOS, Windows (MinGW) |
| `gnu-c` | GCC / gcc (C mode) | Linux, macOS, Windows (MinGW) |
| `msvc` | Microsoft Visual C++ (cl) | Windows |
| `msvc-c` | Microsoft Visual C (cl, C mode) | Windows |

**Auto-detection logic** (see `dumper.py:_castxml_dump()`): abicheck extracts the
*filename* from the compiler binary path (via `Path(cc_bin).name`), lower-cases it, and
checks whether it is `cl` or `cl.exe`. If so, it passes `--castxml-cc-msvc`; otherwise it
passes `--castxml-cc-gnu`. The comparison is case-insensitive so `CL.EXE`, `Cl.exe`, etc.
are all correctly detected on Windows.

**Compiler resolution priority** (highest to lowest):

1. `--gcc-path /path/to/compiler` — explicit path override, used as-is
2. `--gcc-prefix <prefix>` — cross-toolchain prefix; abicheck appends `g++` (C++ mode)
   or `gcc` (C mode) automatically
3. Default mapping — logical name (`c++` → `g++`, `cc` → `gcc`, `clang++` → `clang++`)

**Scanning with a specific compiler version:** use `--gcc-path` to point at the exact
binary. castxml queries that binary for its version-specific predefined macros and include
paths, so the parse reflects exactly what that compiler version defines:

```bash
abicheck dump libfoo.so -H foo.h --gcc-path /usr/bin/g++-9   # GCC 9
abicheck dump libfoo.so -H foo.h --gcc-path /usr/bin/g++-12  # GCC 12
```

**Limitations — non-C/C++ languages and compiler extensions:**

castxml can only parse **C and C++** because its internal engine is Clang. It cannot parse
Fortran, Rust, Ada, or other languages — there is no `--castxml-cc-fortran` equivalent.
For compilers that add language extensions beyond standard C/C++ (e.g. Intel DPC++/SYCL
`__attribute__((sycl_kernel))`, CUDA `__global__`, OpenACC pragmas), castxml can query
the external compiler's preprocessor state but its internal Clang will reject
extension-specific syntax during parsing. To scan such headers you would need either a
CastXML build linked against the matching Clang fork (e.g. Intel's DPC++ Clang for SYCL)
or a different AST extraction tool that uses that compiler's libclang directly.

### Layer L1: Debug info cross-check (optional)

When debug info is available in the binary:

**DWARF** (Linux `.so`, macOS `.dylib` — via `pyelftools`):
- Cross-validates struct/class sizes against header-computed sizes
- Verifies member offsets (catches `#pragma pack` or `-march`-specific alignment differences)
- Checks vtable slot offsets
- Detects calling convention and frame register changes

**PDB** (Windows `.dll` — via built-in PDB parser):
- Extracts struct/class/union sizes and field layouts from TPI stream
- Extracts enum underlying types and member values
- Detects calling convention changes (`__cdecl`, `__stdcall`, `__fastcall`,
  `__thiscall`, `__vectorcall`) from `LF_PROCEDURE` / `LF_MFUNCTION` records
- Extracts MSVC toolchain info (version, machine type, ABI flags) from DBI stream
- Auto-discovers PDB files from PE debug directory; use `--pdb-path` to override

**Debug artifact resolution** (via `debug_resolver` module):

When debug info is not embedded, abicheck searches a configurable resolver
chain: split DWARF (.dwo/.dwp), build-id trees, path mirrors, dSYM bundles,
PDB files, and optionally debuginfod servers. Use `--debug-root` to point at
separate debug file directories, or `--debuginfod` for network-based resolution.

### Layers L3 / L4: Build & source evidence (optional)

The build (L3) and source (L4) layers are **post-build, opt-in, and never
authoritative on their own** — abicheck reads existing build outputs and
build-system query interfaces; it does not rebuild your project. They are
collected into a content-addressed **build/source pack** and attached to a snapshot:

- **L3 — build context** (`build_context.py`, ADR-029): parses a
  `compile_commands.json` (`-p build/`) or a CMake/Ninja/Bazel/Make graph to
  recover the exact ABI-relevant flags and toolchain the library was built with.
  Diffs emit context/risk kinds like `abi_relevant_build_flag_changed`,
  `toolchain_version_changed`, and `link_export_policy_changed`.
- **L4 — source ABI replay** (ADR-030): parses selected TUs and public headers
  under their real per-TU build context and links the result against the
  exported surface, catching `public_macro_value_changed`,
  `default_argument_changed`, `constexpr_value_changed`, and the uninstantiated
  templates that no artifact carries.

Both are described in full in [Build & Source Packs](build-source-data.md). Per the
authority rule, every L3/L4 finding defaults to `API_BREAK` or risk and carries
an explicit evidence-tier boundary so it is never read as a proven shipped-ABI
break.

---

## Key modules

For the module-by-module map — every source file grouped by area (data model,
input resolution, binary/debug metadata, core diffing, policy, post-processing,
workflows, reporting, compatibility) — see the
[Codebase Overview](../development/codebase-overview.md#1-architecture-overview),
which is the contributor-facing source of truth for the package layout.

---

## Policy model

Policies control how detected changes are classified (BREAKING, API_BREAK, COMPATIBLE).

**Built-in profiles:**

| Profile | Behavior |
|---------|----------|
| `strict_abi` (default) | Every ABI change at maximum severity |
| `sdk_vendor` | Source-only changes downgraded to COMPATIBLE |
| `plugin_abi` | Calling-convention changes downgraded to COMPATIBLE |

**Custom policies:** YAML files with per-kind `break|warn|ignore` overrides.

Source of truth: `BREAKING_KINDS`, `API_BREAK_KINDS`, `COMPATIBLE_KINDS`, and `RISK_KINDS` sets in `checker_policy.py`.

---

## Verdict system

| Verdict | Exit code | Meaning |
|---------|-----------|---------|
| `NO_CHANGE` | 0 | Identical snapshots |
| `COMPATIBLE` | 0 | Safe changes (new symbols, weak binding) |
| `COMPATIBLE_WITH_RISK` | 0 | Binary-compatible but deployment risk present |
| `API_BREAK` | 2 | Source-level break, binary-safe (rename, access change) |
| `BREAKING` | 4 | Binary ABI break — old binaries will fail |

---

## Error model

Public exceptions are defined in `abicheck/errors.py`. Tool errors produce exit code `1`.
