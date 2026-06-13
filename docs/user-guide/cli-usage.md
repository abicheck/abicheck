# Using abicheck, Compatibility Modes, and Coverage

## What abicheck is

**abicheck** checks C/C++ library compatibility on both API and ABI layers.
It is designed to be a practical, modern replacement for legacy ABI tooling in CI,
especially when you need structured output and automation.

abicheck is inspired by:

- [libabigail / abidiff](https://sourceware.org/libabigail/)
- [ABI Compliance Checker (ABICC)](https://lvc.github.io/abi-compliance-checker/)

Huge thanks to both projects for pioneering ABI compatibility analysis.

> **Not sure which command fits your situation?** See
> [Choose Your Workflow](choose-your-workflow.md) — a decision guide that maps
> your artifacts (single library, release bundle, package, application, stripped
> binaries…) and CI policy to the exact command and options. This page is the
> per-command flag reference.

## How to use abicheck

### 1) Compare two libraries directly (primary flow)

The simplest way — pass `.so` files and their public headers directly to
`compare`. Each library version gets its own header(s):

```bash
# Each version has its own header
abicheck compare libfoo.so.1 libfoo.so.2 \
  --old-header include/v1/foo.h --new-header include/v2/foo.h

# Multiple headers per version, with include dirs and version labels
abicheck compare libfoo.so.1 libfoo.so.2 \
  --old-header include/v1/foo.h --old-header include/v1/bar.h \
  --new-header include/v2/foo.h --new-header include/v2/bar.h \
  -I include/ --old-version 1.0 --new-version 2.0

# Shorthand: -H applies the same header to both sides
# (only when the header itself didn't change between versions)
abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h

# Header directory input is supported (recursive)
abicheck compare libfoo.so.1 libfoo.so.2 -H include/

# Output formats
abicheck compare libfoo.so.1 libfoo.so.2 \
  --old-header v1/foo.h --new-header v2/foo.h --format sarif -o abi.sarif
abicheck compare libfoo.so.1 libfoo.so.2 \
  --old-header v1/foo.h --new-header v2/foo.h --format junit -o results.xml
```

`compare` auto-detects each input: `.so` files are dumped on-the-fly, `.json`
snapshots and ABICC Perl dumps (Data::Dumper `.dump` files) are loaded directly.
You can mix them freely (see below).

If ELF headers are not provided, `compare` falls back to symbols-only analysis
and prints a warning. This mode is useful for quick checks but may miss
signature/type-level ABI breaks.

### 2) Dump snapshots and compare later (for CI baselines)

When you want to cache ABI baselines as CI artifacts or commit them to the repo:

```bash
# Step 1: Dump snapshots (each version uses its own header)
abicheck dump libfoo.so.1 -H include/v1/foo.h --version 1.0 -o libfoo-1.0.json
abicheck dump libfoo.so.2 -H include/v2/foo.h --version 2.0 -o libfoo-2.0.json

# Step 2: Compare snapshots (no headers needed — already baked in)
abicheck compare libfoo-1.0.json libfoo-2.0.json
```

#### Language mode

By default castxml uses C++ mode. For pure C libraries, pass `--lang c`:

```bash
abicheck dump libfoo.so -H foo.h --lang c -o snap.json
abicheck compare libv1.so libv2.so -H foo.h --lang c
```

#### Cross-compilation

When analysing libraries built for a different architecture, pass cross-compilation
flags to `dump`:

```bash
abicheck dump libfoo.so -H include/foo.h \
  --gcc-prefix aarch64-linux-gnu- \
  --sysroot /opt/sysroots/aarch64 \
  --gcc-options "-march=armv8-a" \
  -o snap.json

# Or specify the cross-compiler binary directly:
abicheck dump libfoo.so -H include/foo.h \
  --gcc-path /usr/bin/aarch64-linux-gnu-g++ \
  -o snap.json
```

Available cross-compilation flags:
- `--gcc-path` — path to the cross-compiler binary
- `--gcc-prefix` — toolchain prefix (e.g. `aarch64-linux-gnu-`)
- `--gcc-options` — extra compiler flags passed to castxml
- `--sysroot` — alternative system root directory
- `--nostdinc` — do not search standard system include paths

#### Build-context capture (`compile_commands.json`) — evidence layer L3

This is **evidence layer L3** in abicheck's [five-source evidence
model](../concepts/evidence-and-detectability.md): on top of the binary (L0),
debug info (L1), and headers (L2), it feeds abicheck the flags the library was
*actually* built with. Modern build systems (CMake, Meson, Ninja) generate a
`compile_commands.json` file that captures the exact compiler flags for every
source file. abicheck can ingest this file directly, eliminating manual flag
specification:

```bash
# Generate compile_commands.json during build
cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON .
cmake --build build

# Dump ABI with exact build flags derived automatically
abicheck dump build/libfoo.so -H include/ -p build/
```

The `-p build/` flag tells abicheck to look for `build/compile_commands.json`
and derive all flags automatically: defines, include paths, language standard,
target triple, sysroot, and ABI-affecting options like `-fvisibility=hidden`.

| Flag | Description |
|------|-------------|
| `-p <dir>` / `--build-dir <dir>` | Build directory containing `compile_commands.json` |
| `--compile-db <file>` | Explicit path to `compile_commands.json` (alias for `-p`) |
| `--compile-db-filter <glob>` | Filter entries by source file pattern (e.g., `src/libfoo/**`) |

When both `-p` and explicit flags (`--gcc-options`, `--sysroot`) are specified,
explicit flags take precedence.

```bash
# Override a single flag while inheriting the rest from compile_commands.json
abicheck dump libfoo.so -H include/ -p build/ \
    --gcc-options "-DEXTRA_DEFINE=1"
```

#### Evidence packs — build & source context (L3 / L4)

The build context above (L3) and **source evidence** (L4) can also be bundled
into a reusable *build/source pack* — a post-build, opt-in artifact that abicheck
reads alongside your binaries. A pack never rebuilds your project or runs
arbitrary commands; it reads existing build outputs and build-system query
interfaces only. See [Source & Build Evidence
Packs](../concepts/build-source-data.md) for the full model.

```bash
# 1. Collect a pack from an existing build tree (no rebuild).
abicheck collect \
    --compile-db build/compile_commands.json \
    --build-dir build --cmake --source-abi \
    --output libfoo.bs/

# 2. Embed the build + source facts inline in the snapshot. The resulting
#    .abi.json is self-contained.
abicheck dump build/libfoo.so -H include/ \
    --build-info libfoo.bs/ --sources libfoo.bs/ -o libfoo.abi.json

# 3. Compare two snapshots — the embedded facts diff automatically, with no
#    pack directories to carry around.
abicheck compare old.abi.json new.abi.json
```

!!! tip "Build/source data travels inside the snapshot"
    `dump --build-info`/`--sources` **embed** the normalized build + source
    facts in the `.abi.json`, so `compare old.json new.json` carries them with
    no out-of-band directories (single-artifact UX). For advanced use, the
    `--old-build-info`/`--new-build-info` and `--old-sources`/`--new-sources`
    flags supply or override those facts per side from a pack directory; raw
    provenance is never embedded — only the normalized facts that feed the
    comparison.

| Flag | Command | Description |
|------|---------|-------------|
| `--build-info <dir>` | `dump` | Embed a pack's L3 build-info facts inline in the snapshot |
| `--sources <dir>` | `dump` | Embed a pack's L4/L5 source facts (source ABI replay + graph) inline in the snapshot |
| `--old-build-info <dir>` / `--new-build-info <dir>` | `compare` | Out-of-band L3 build-info pack per side (overrides embedded) |
| `--old-sources <dir>` / `--new-sources <dir>` | `compare` | Out-of-band L4/L5 source pack per side (overrides embedded) |
| `--collect-mode <mode>` | `compare` | Inline collection mode. Only `off` (the default) is functional in this release: it uses embedded facts and any explicitly-provided pack directories. Other modes are accepted and reported in the coverage table, but inline collection for them is not implemented yet — run `abicheck collect` separately instead. |

To additionally capture **L4 source ABI replay** (macro/`constexpr` values,
default-argument values, uninstantiated templates), add `--source-abi` to
`collect`. L4 requires `clang` (or castxml for the declaration subset);
if it is missing, abicheck **degrades gracefully** — L4 is marked partial and
the artifact-backed tiers (L0–L2) remain fully authoritative. Build/source
evidence (L3/L4) *explains, localizes, and scopes* findings or raises its own
source-level findings, but it **never silently deletes an artifact-proven
break** (the *authority rule*, ADR-028 D3).

!!! tip "Diagnosing which layers you have"
    Run `abicheck dump libfoo.so --show-data-sources` to print which evidence
    layers (L0 binary metadata, L1 debug info, L2 header AST) abicheck found for
    a binary, then exit — useful for confirming a stripped build really is
    missing its debug info before you trust a symbols-only verdict.

#### Debug artifact resolution

abicheck achieves its highest accuracy with DWARF debug information, but in
many deployments debug info is not embedded in the binary (stripped builds,
split DWARF, distro debuginfo packages, dSYM bundles, PDB files). abicheck
automatically searches for debug artifacts across multiple locations:

```text
1. Split DWARF (.dwo files or .dwp package)
2. Embedded DWARF (binary itself has .debug_info)
3. Build-id tree (/usr/lib/debug/.build-id/<ab>/<cdef...>.debug)
4. Path mirror (/usr/lib/debug/usr/lib/libfoo.so.debug)
5. dSYM bundle (macOS: Foo.dylib.dSYM/Contents/Resources/DWARF/Foo.dylib)
6. PDB (Windows: adjacent .pdb or _NT_SYMBOL_PATH)
7. debuginfod (opt-in network: query by build-id)
```

| Flag | Description |
|------|-------------|
| `--debug-root <dir>` | Directory containing separate debug files. Can be repeated. |
| `--debug-root1 <dir>` | Debug root for old side only (`compare` command). |
| `--debug-root2 <dir>` | Debug root for new side only (`compare` command). |
| `--debuginfod` | Enable debuginfod network resolution (opt-in). |
| `--debuginfod-url <url>` | Override debuginfod server URL. |

```bash
# Stripped distro packages with separate debuginfo
abicheck compare \
    old/usr/lib64/libfoo.so.1 new/usr/lib64/libfoo.so.1 \
    --debug-root1 old-debug/usr/lib/debug \
    --debug-root2 new-debug/usr/lib/debug

# Fedora/RHEL: debug info fetched automatically by build-id
export DEBUGINFOD_URLS="https://debuginfod.fedoraproject.org/"
abicheck compare old-libfoo.so new-libfoo.so --debuginfod
```

#### Verbose output

Add `-v` / `--verbose` to any native command to enable debug logging:

```bash
abicheck dump libfoo.so -H foo.h -v
abicheck compare old.json new.json -v
```

#### Surface and source-graph reports

Three companion commands report on the public surface and the L5 source graph
rather than producing a compare verdict:

```bash
# Structural metrics + idiom/anti-pattern report for one library's public surface
abicheck surface-report libfoo.so -H include/ --idioms --anti-patterns

# Diff two L5 source-graph summaries (from `collect --source-graph`)
abicheck compare-graph old-pack/ new-pack/

# Localize a compare finding through the L5 source graph (which TU/include chain)
abicheck explain-finding --report report.json --finding-id <id> --sources new-pack/
```

See [API Surface Intelligence](../concepts/api-surface-intelligence.md) for what
the surface metrics and idiom recognizers mean, and
[Build & Source Packs](../concepts/build-source-data.md) for producing the packs
that `compare-graph` / `explain-finding` consume.

### Report filtering and display options

`compare` provides several flags to control what is shown in the report.
These flags are **display-only** — they do not affect the verdict or exit codes.

#### Redundancy filtering

By default, abicheck collapses derived changes caused by a root type change.
For example, if a struct's size changes, the 30 `FUNC_PARAMS_CHANGED` entries
for functions that take that struct are hidden. The root type change is annotated
with the count and list of affected interfaces.

```bash
# Show all changes, including redundant derived ones
abicheck compare old.json new.json --show-redundant
```

#### `--show-only`: filter displayed changes

Limit which changes appear in the report using three dimensions (AND across
dimensions, OR within):

- **Severity**: `breaking`, `api-break`, `risk`, `compatible`
- **Element**: `functions`, `variables`, `types`, `enums`, `elf`
- **Action**: `added`, `removed`, `changed`

```bash
# Only breaking function removals
abicheck compare old.json new.json --show-only breaking,functions,removed

# All type changes (any action, any severity)
abicheck compare old.json new.json --show-only types

# Breaking + risk changes only
abicheck compare old.json new.json --show-only breaking,risk
```

Invalid tokens are caught immediately with a clear error message.

#### Severity configuration (`--severity-*`)

Control exit codes and report labels by assigning severity levels to four issue
categories:

```bash
# Block on API additions
abicheck compare old.json new.json --severity-addition error

# Everything is an error (strict)
abicheck compare old.json new.json --severity-preset strict

# Custom: breaks are errors, additions are warnings, rest is info
abicheck compare old.json new.json \
  --severity-abi-breaking error \
  --severity-potential-breaking info \
  --severity-quality-issues info \
  --severity-addition warning
```

See the [severity guide](severity.md) for the full reference.

#### `--stat`: one-line CI summary

```bash
# Human-readable one-liner
abicheck compare old.json new.json --stat
# BREAKING: 3 breaking, 1 risk (42 total) [12 redundant hidden]

# JSON summary (no changes array)
abicheck compare old.json new.json --stat --format json
```

#### `--report-mode leaf`: root-type-grouped output

Groups output by root type changes, listing affected interfaces under each:

```bash
abicheck compare old.json new.json --report-mode leaf
```

This is useful for large diffs where you want to understand the root causes
rather than reading hundreds of individual change entries.

#### `--show-impact`: impact summary table

Appends a summary table showing which root type changes affected the most
interfaces:

```bash
abicheck compare old.json new.json --show-impact
```

All filtering flags work with the main `compare` command output formats:
Markdown, JSON, SARIF, and HTML. The ABICC-compatible XML output (produced via
`abicheck compat check`) does not support `--show-only` filtering, though it
does include redundancy annotations (`<redundant_changes>`, `<caused_by>`,
`<caused_count>`).

### 3) Mixed mode: snapshot baseline vs live build

```bash
# CI baseline snapshot vs current build
abicheck compare baseline-1.0.json ./build/libfoo.so \
  --new-header include/foo.h --new-version 2.0-dev

# Live old build vs stored new snapshot
abicheck compare ./build-old/libfoo.so new-release.json \
  --old-header include/foo.h --old-version 1.0-rc1
```

### 4) ABICC-compatible invocation (for migration)

For teams migrating from `abi-compliance-checker` — same flags, same XML descriptors.
See [ABICC compatibility reference](from-abicc.md) for the full flag list.

```bash
# Minimal (identical to abi-compliance-checker):
abicheck compat check -lib foo -old old.xml -new new.xml

# With strict mode and version labels:
abicheck compat check -lib foo -old old.xml -new new.xml -s -v1 1.0 -v2 2.0

# Source/API compat only (ignore ELF metadata):
abicheck compat check -lib foo -old old.xml -new new.xml -source

# Skip known symbols:
abicheck compat check -lib foo -old old.xml -new new.xml -skip-symbols skip.txt
```

## abicheck as a drop-in replacement for ABICC

abicheck intentionally supports ABICC-like CLI semantics and XML descriptor flow,
while modernizing internals and outputs.

### Why teams replace ABICC with abicheck

- Python-native implementation, easier to embed and extend in CI.
- Structured outputs (`json`, `markdown`, `sarif`) for machine + human consumption.
- Works well in stripped-binary workflows when combined with headers.
- Better integration path for modern C++ workflows and policy checks.
- **Full ABICC flag parity** — `-s/-strict`, `-source`, `-skip-symbols/-skip-types`, `-v1/-v2`, `-stdout` and more.
- **Superset detectors** — catches everything ABICC catches plus: `FUNC_DELETED`, `VAR_BECAME_CONST`, `TYPE_BECAME_OPAQUE`, `BASE_CLASS_POSITION_CHANGED`, `BASE_CLASS_VIRTUAL_CHANGED`.

### Practical migration path

1. Keep your existing ABICC XML descriptor generation.
2. Replace ABICC compare call with `abicheck compat check ...` (flags are identical).
3. Optionally move to native `dump/compare` commands for explicit snapshot control.
4. Switch CI gates to JSON/SARIF-based policy checks.

## Change classification: BREAKING vs COMPATIBLE

abicheck classifies every detected change into a verdict:

- **BREAKING** — binary ABI incompatibility; existing binaries will malfunction.
- **COMPATIBLE** — informational/warning; does not break binary compatibility on its own.
- **NO_CHANGE** — identical ABI.

A change is BREAKING only when it causes binary-level failures: symbol resolution errors,
type layout corruption, vtable mismatch, or calling convention incompatibility.

Changes like enum member addition, union field addition (without growth),
GLOBAL→WEAK binding, and IFUNC transitions are classified as **COMPATIBLE** — they are
detected and reported for awareness but do not trigger a BREAKING verdict.
`noexcept` removal is classified **COMPATIBLE_WITH_RISK** (binary-linkable but a
deployment/behavioral hazard). See
[ABI/API Handling & Recommendations](../concepts/abi-api-handling.md) for the full
rationale.

## ABI/API breakages and what each tool mode can detect

This section maps breakage types to example cases under `examples/` and compares:

- `abicheck` (header + ELF metadata pipeline)
- `abidiff + headers`
- `ABICC Usage #2` (header-based ABICC mode)
- `ABICC Usage #1` (abi-dumper / DWARF dump mode)

Legend: ✅ strong support, ⚠️ partial/conditional, ❌ generally not covered.

| Case | Breakage type | Verdict | abicheck | abidiff + headers | ABICC #2 (headers) | ABICC #1 (dumps) |
|---|---|---|:---:|:---:|:---:|:---:|
| case01_symbol_removal | Public symbol removed | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case02_param_type_change | Function parameter type changed | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case03_compat_addition | Compatible API addition | COMPATIBLE | ✅ | ✅ | ✅ | ✅ |
| case04_no_change | No ABI change baseline | NO_CHANGE | ✅ | ✅ | ✅ | ✅ |
| case05_soname | SONAME / packaging policy issue | BREAKING | ✅ | ⚠️ | ⚠️ | ⚠️ |
| case06_visibility | Visibility/export policy drift | BREAKING | ✅ | ✅ | ⚠️ | ⚠️ |
| case07_struct_layout | Struct layout changed | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case08_enum_value_change | Enum value changed | BREAKING | ✅ | ⚠️ | ✅ | ✅ |
| case09_cpp_vtable | VTable/method order/signature drift | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case10_return_type | Function return type changed | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case11_global_var_type | Global variable type changed | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case12_function_removed | API function removed | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case13_symbol_versioning | Symbol version policy regression | COMPATIBLE | ✅ | ⚠️ | ⚠️ | ⚠️ |
| case14_cpp_class_size | C++ class size/layout changed | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case15_noexcept_change | `noexcept` contract changed | COMPATIBLE | ✅ | ⚠️ | ✅ | ❌ |
| case16_inline_to_non_inline | Inline/ODR surface change | BREAKING | ✅ | ⚠️ | ✅ | ❌ |
| case17_template_abi | Template-instantiation ABI drift | BREAKING | ✅ | ⚠️ | ✅ | ✅ |
| case18_dependency_leak | Transitive dependency leaked into API | BREAKING | ✅ | ⚠️ | ✅ | ✅ |
| case19_enum_member_removed | Enum member removed | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case20_enum_member_value_changed | Enum member value changed | BREAKING | ✅ | ⚠️ | ✅ | ✅ |
| case21_method_became_static | Method became static | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case22_method_const_changed | Method const-qualifier changed | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case23_pure_virtual_added | Added pure virtual method | BREAKING | ✅ | ✅ | ✅ | ✅ |
| case24_union_field_removed | Union field removed | BREAKING | ✅ | ✅ | ✅ | ✅ |

### Summary by breakage category

- **API surface breaks** (removed/changed signatures): all modes generally catch these.
- **C++ semantic contract breaks** (`noexcept`, inline/ODR): header-aware analysis is strongest.
- **DWARF-only detail** (some anonymous/internal layout details): ABICC dump mode can be strongest when debug info exists.
- **Policy/linking hygiene** (SONAME/versioning/visibility): best handled by a tool that includes explicit ELF policy checks.

## Architecture and dependencies

### 5) Full-stack dependency validation (Linux ELF)

Resolve the full dependency tree, simulate symbol binding, and produce a
stack-level ABI compatibility verdict.

```bash
# Show dependency tree + symbol binding status
abicheck deps /usr/bin/python3
abicheck deps /usr/bin/python3 --format json

# Compare a binary's full stack across two sysroots
abicheck stack-check usr/bin/myapp \
    --baseline /rootfs/v1 --candidate /rootfs/v2

# Include dependency info in dump/compare
abicheck dump libfoo.so -H foo.h --follow-deps -o snap.json
abicheck compare old.so new.so -H foo.h --follow-deps
```

The `deps` command resolves the transitive dependency closure and displays:
- Dependency tree with resolution reasons (rpath, runpath, default, etc.)
- Unresolved libraries
- Symbol binding summary (resolved, missing, version mismatches)

The `stack-check` command compares two environments and reports:
- Loadability verdict (will the binary load?)
- ABI risk verdict (are there breaking changes in dependencies?)
- Per-library ABI diffs intersected with actual symbol usage

The `--follow-deps` flag on `dump` and `compare` includes dependency graph
and binding information in the output alongside the regular ABI diff.

### 6) Debian symbols file integration

Generate, validate, and diff [Debian symbols files](https://manpages.debian.org/unstable/dpkg-dev/dpkg-gensymbols.1.en.html) for integration with Debian/Ubuntu packaging workflows where `dpkg-gensymbols` and `dpkg-shlibdeps` use symbols files for fine-grained dependency tracking.

#### Generate a symbols file from a shared library

```bash
# Generate and print to stdout
abicheck debian-symbols generate libfoo.so

# Write to file with explicit package name and version
abicheck debian-symbols generate libfoo.so -o debian/libfoo1.symbols \
    --package libfoo1 --version 1.0
```

Output format (Debian symbols file):

```
libfoo.so.1 libfoo1 1.0
 _ZN3foo3barEv@Base 1.0
 _ZN3foo3bazEi@Base 1.0
 _ZN3foo9new_thingEv@LIBFOO_2.0 1.1
 (c++)"foo::Config::Config()@Base" 1.0
 (c++)"foo::Config::~Config()@Base" 1.0
```

Mapping:

- **Library SONAME** → first line (library, package name, minimum version)
- **Each exported symbol** → one line with `@Base` or `@VERSION_NODE` and version
- **C++ symbols** → demangled form with `(c++)` prefix (Debian convention)
- **Version** comes from ELF symbol version nodes if present, else `@Base`
- **Package name** is derived from SONAME (e.g. `libfoo.so.1` → `libfoo1`) or set with `--package`

Use `--no-cpp` to emit mangled names only (no demangling):

```bash
abicheck debian-symbols generate libfoo.so --no-cpp -o symbols
```

#### Validate a symbols file against a binary

```bash
abicheck debian-symbols validate libfoo.so debian/libfoo1.symbols
```

Example output:

```
Symbols validation for libfoo.so.1:
  MISSING from binary (in symbols file but not exported):
    _ZN3foo6legacyEv@Base 1.0
  NEW in binary (exported but not in symbols file):
    _ZN3foo9new_thingEv@Base
  Result: FAIL (1 missing symbol)
```

Exit codes: `0` = match (symbols file is valid), `2` = mismatch (missing symbols).

Symbols tagged `(optional)` are not required — their absence does not cause failure, matching `dpkg-gensymbols` semantics.

#### Diff two symbols files

```bash
abicheck debian-symbols diff old/libfoo1.symbols new/libfoo1.symbols
```

Example output:

```
Symbols diff: old/libfoo1.symbols -> new/libfoo1.symbols
  ADDED:
    + _ZN3foo9new_thingEv@Base 1.1
  REMOVED:
    - _ZN3foo6legacyEv@Base 1.0
  VERSION CHANGED:
    (none)
  Total changes: 2
```

#### Options reference

| Subcommand | Option | Description |
|------------|--------|-------------|
| `generate` | `-o` / `--output` | Output file path (stdout if omitted) |
| `generate` | `--package` | Debian package name (derived from SONAME if empty) |
| `generate` | `--version` | Minimum version string (default: `#MINVER#`) |
| `generate` | `--no-cpp` | Emit mangled names only, no `(c++)` demangled form |
| `validate` | _(positional)_ | `SO_PATH SYMBOLS_PATH` — binary and symbols file |
| `diff` | _(positional)_ | `OLD_SYMBOLS NEW_SYMBOLS` — two symbols files |

#### Supported tag syntax

The parser handles the full Debian symbols tag syntax:

- `(c++)` — C++ demangled symbol with quoted name
- `(optional)` — symbol is not required during validation
- `(arch=amd64)` — architecture-specific symbol (parsed, not filtered yet)
- `(c++|optional)` — pipe-separated tag groups (round-trip preserved)
- `(symver)`, `(regex)` — parsed but not evaluated

#### Limitations

- `#include` directives and `#PACKAGE#` substitution are not supported.
- `(regex)` and `(symver)` pattern-matching tags are parsed but not evaluated.
- `(arch=...)` tags are parsed but not filtered (no `--arch` option yet).

## High-level architecture

```text
CLI
  dump                         — dump ABI snapshot to JSON
  compare                      — compare two ABI surfaces
  deps                         — show dependency tree + binding status (Linux ELF)
  stack-check                  — full-stack comparison across environments (Linux ELF)
  debian-symbols generate      — generate Debian symbols file from shared library
  debian-symbols validate      — validate symbols file against binary
  debian-symbols diff          — diff two Debian symbols files
  compat check                 — ABICC drop-in comparison
  compat dump                  — dump from ABICC XML descriptor
    -> dumper (castxml AST + ELF metadata)
    -> checker (rule-based diff + severity)
    -> resolver (transitive dependency resolution)
    -> binder (symbol binding simulation)
    -> stack_checker (stack-level ABI comparison)
    -> debian_symbols (Debian symbols file adapter)
    -> reporters (markdown/json/sarif/html)
```

## Core modules and purpose

- `abicheck.cli` — command-line entrypoints.
- `abicheck.dumper` — snapshot construction from headers + binary metadata.
- `abicheck.checker` — change detection and breakage classification.
- `abicheck.resolver` — transitive ELF dependency resolution with loader-accurate search order.
- `abicheck.binder` — symbol binding simulation across a resolved dependency graph.
- `abicheck.stack_checker` — stack-level ABI comparison and verdict computation.
- `abicheck.stack_report` — JSON and Markdown output for stack-level results.
- `abicheck.build_context` — compile_commands.json parsing and flag extraction.
- `abicheck.debug_resolver` — debug artifact resolution (DWARF, PDB, dSYM, debuginfod).
- `abicheck.baseline` — baseline registry (push/pull/list/delete with integrity checks).
- `abicheck.compat` — ABICC compatibility layer (`abicheck.compat.descriptor`, `abicheck.compat.xml_report`, `abicheck.compat.cli`, `abicheck.compat.abicc_dump_import`).
- `abicheck.debian_symbols` — Debian symbols file generation, parsing, validation, and diffing.
- `abicheck.reporter` / `abicheck.sarif` / `abicheck.html_report` — output generators.
- `abicheck.elf_metadata`, `abicheck.dwarf_metadata`, `abicheck.dwarf_advanced` — low-level binary metadata extraction.

## Runtime dependencies (practical view)

- **Python 3.10+**
- **castxml** (for header-driven API/ABI modeling)
- **pyelftools** (ELF/DWARF metadata)
- **click** (CLI)
- **defusedxml** (safe XML parsing for ABICC descriptor mode)

Optional ecosystem tools for comparisons/benchmarks:

- `abidiff` / libabigail tools
- ABICC + abi-dumper toolchain

