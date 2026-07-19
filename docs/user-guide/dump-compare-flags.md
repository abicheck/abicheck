# Evidence, Build-Context, and Debug Flags

This page is the flag reference for everything that widens `dump`/`compare`
beyond the basic `.so` + headers case covered in [CLI Usage](cli-usage.md): C
vs C++ mode, cross-compilation, feeding in the exact build flags (evidence
layer **L3**), embedding build/source evidence packs (**L3**/**L4**), and
resolving debug info that isn't in the binary itself.

> Split out of [CLI Usage](cli-usage.md) to keep that page to the everyday
> compare/dump flow. See [Choose Your Workflow](choose-your-workflow.md) for
> which of these you actually need for your situation, and
> [Evidence & Detectability](../concepts/evidence-and-detectability.md) for
> what each layer buys you conceptually.

## Language mode

By default castxml uses C++ mode. For pure C libraries, pass `--lang c`:

```bash
abicheck dump libfoo.so -H foo.h --lang c -o snap.json
abicheck compare libv1.so libv2.so -H foo.h --lang c
```

## Cross-compilation

When analysing libraries built for a different architecture, pass cross-compilation
flags. The same **compile-context family** is shared verbatim by `dump`, `compare`,
and `scan` (one decorator, so the three never drift), so it works the same on each:

```bash
# dump (single artifact)
abicheck dump libfoo.so -H include/foo.h \
  --gcc-prefix aarch64-linux-gnu- \
  --sysroot /opt/sysroots/aarch64 \
  --gcc-options "-march=armv8-a" \
  -o snap.json

# Or specify the cross-compiler binary directly:
abicheck dump libfoo.so -H include/foo.h \
  --gcc-path /usr/bin/aarch64-linux-gnu-g++ \
  -o snap.json

# compare (two artifacts) — the family applies to BOTH sides
abicheck compare libv1.so libv2.so -H include/foo.h \
  --gcc-prefix aarch64-linux-gnu- --sysroot /opt/sysroots/aarch64
```

Available compile-context flags (on `dump`, `compare`, and `scan`):
- `--gcc-path` — path to the cross-compiler binary. For the `clang`
  `--ast-frontend`, this is honored only when the binary is clang-family
  (basename contains `clang`, or is a known non-`clang`-named clang-based
  fork — currently Intel's `icx`/`icpx`/`dpcpp`/`dpcpp-cl`); a path to a real
  GCC binary is ignored here and the frontend falls back to plain `clang` on
  `PATH` instead (castxml can't take clang-only flags, so this guards against
  a GCC path being misread as a clang toolchain)
- `--gcc-prefix` — toolchain prefix (e.g. `aarch64-linux-gnu-`)
- `--gcc-options` — extra compiler flags passed to the header frontend
- `--sysroot` — alternative system root directory
- `--nostdinc` / `--no-nostdinc` — do not search standard system include paths
- `--ast-frontend {auto,castxml,clang,hybrid}` — which C/C++ AST frontend parses
  the headers; `hybrid` runs both castxml and clang and merges them

On `compare` these apply to **both** old and new sides; the per-side
`--old-ast-frontend` / `--new-ast-frontend` overrides still win for the frontend
when one release parses on a different toolchain than the other.

Rather than repeating these flags on every invocation, set them once in the
project's `.abicheck.yml` `compile:` block — `dump`, `compare`, and `scan` all fold
it into their L2 header parse (CLI flags override config):

```yaml
# .abicheck.yml
compile:
  frontend: castxml          # auto | castxml | clang | hybrid
  std: c++20                 # synthesizes -std=c++20
  defines: [FOO=1, NDEBUG]   # synthesizes -DFOO=1 -DNDEBUG
  include_dirs: [include, third_party/inc]   # appended after -I roots
  sysroot: /opt/sysroots/aarch64
  nostdinc: false
```

`compare` reads the block from `--config` or the nearest `.abicheck.yml` found from
the current directory upward; `dump`/`scan` from `--config` or the one auto-discovered
at the `--sources` tree root. It is applied on every header-scoping path — ELF and
the PE/Mach-O header parse alike. A malformed **explicit** `--config` fails loudly
rather than silently dropping the settings; an auto-discovered one warns and falls
back.

## Build-context capture (`compile_commands.json`) — evidence layer L3

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

## Evidence packs — build & source context (L3 / L4)

The build context above (L3) and **source evidence** (L4) can also be bundled
into a reusable *build/source pack* — a post-build, opt-in artifact that abicheck
reads alongside your binaries. A pack never rebuilds your project or runs
arbitrary commands; it reads existing build outputs and build-system query
interfaces only. See [Source & Build Evidence
Packs](../concepts/build-source-data.md) for the full model and
[Build Evidence Setup](build-evidence-setup.md) for producing a pack
(`abicheck-cc`, the Clang plugin, and a full worked CMake example).

```bash
# 1. Point dump straight at a raw source checkout — it collects L3/L4/L5
#    evidence inline itself (compile DB auto-inferred for cmake/make/bazel),
#    no separate collection step needed. The resulting .abi.json is
#    self-contained.
abicheck dump build/libfoo.so -H include/ \
    --sources . -o libfoo.abi.json

# 2. Or feed in an out-of-band pack produced by the abicheck-cc wrapper or the
#    Clang plugin (abicheck also auto-detects an abicheck_inputs/ pack
#    alongside the binary with no flag at all).
abicheck dump build/libfoo.so -H include/ \
    --build-info abicheck_inputs/ --sources abicheck_inputs/ -o libfoo.abi.json

# 3. Compare two snapshots — the embedded facts diff automatically, with no
#    pack directories to carry around.
abicheck compare old.abi.json new.abi.json
```

!!! tip "Build/source data travels inside the snapshot"
    `dump --build-info`/`--sources` **embed** the normalized build + source
    facts in the `.abi.json`, so `compare old.json new.json` carries them with
    no out-of-band directories (single-artifact UX). For advanced use, the
    `--build-info` and `--sources`
    flags supply or override those facts per side from a pack directory; raw
    provenance is never embedded — only the normalized facts that feed the
    comparison.

| Flag | Command | Description |
|------|---------|-------------|
| `--build-info <dir>` | `dump` | Embed a pack's L3 build-info facts inline in the snapshot |
| `--sources <dir>` | `dump` | Embed a pack's L4/L5 source facts (source ABI replay + graph) inline in the snapshot |
| `--build-info old=<dir>` / `--build-info new=<dir>` | `compare` | Out-of-band L3 build-info pack per side (overrides embedded) |
| `--sources old=<dir>` / `--sources new=<dir>` | `compare` | Out-of-band L4/L5 source pack per side (overrides embedded) |
| `--depth <rung>` | `compare`, `dump` | Evidence-depth dial: `binary` (L0/L1 only), `headers` (+L2 AST, default), `build` (+L3 build context), `source` (+L4 replay & the L5 graph). On `compare`, depths past `headers` collect from an `--sources` tree (or read embedded facts); without a source tree the requested mode is reported in the coverage table only. |

To additionally capture **L4 source ABI replay** (macro/`constexpr` values,
default-argument values, uninstantiated templates), pass `--sources` at
`--depth source` on `dump`/`compare` (a raw source checkout is replayed
inline; a pre-built pack from the `abicheck-cc` wrapper or Clang plugin is
loaded as-is). L4 requires `clang` (or castxml for the declaration subset);
if it is missing, abicheck **degrades gracefully** — L4 is marked partial and
the artifact-backed tiers (L0–L2) remain fully authoritative. Build/source
evidence (L3/L4) *explains, localizes, and scopes* findings or raises its own
source-level findings, but it **never silently deletes an artifact-proven
break** (the *authority rule*, ADR-028 D3).

!!! tip "Diagnosing which layers you have"
    Run `abicheck dump libfoo.so --dry-run` to classify the inputs and print
    which data layers (L0–L5) are available, without producing a snapshot —
    useful for confirming a stripped build really is missing its debug info
    before you trust a symbols-only verdict.

## Dry-run validation

Both `dump` and `compare` accept `--dry-run`: it resolves and validates the
invocation — classifying inputs, resolving depth/scope, discovering config,
and (on `dump`) reporting which data layers (L0–L5) are available — then
prints a report without producing a snapshot or running the diff. It writes
nothing, so it's incompatible with `-o`/`--output`, and its exit codes are
only `0` (ok) or `1` (blocked) or `64` (usage error) — never the verdict codes
`2`/`4`.

```bash
# Check what dump would see before spending time on a full extraction
abicheck dump libfoo.so -H include/ --sources . --dry-run

# Check what compare would resolve/collect before running the diff
abicheck compare old.so new.so -H include/ --depth source --sources . --dry-run
```

## Debug artifact resolution

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
| `--debug-root old=<dir>` | Debug root for old side only (`compare` command). |
| `--debug-root new=<dir>` | Debug root for new side only (`compare` command). |
| `--debuginfod` | Enable debuginfod network resolution (opt-in). |
| `--debuginfod-url <url>` | Override debuginfod server URL. |

```bash
# Locate + report separate debuginfo for stripped .so files
abicheck compare \
    old/usr/lib64/libfoo.so.1 new/usr/lib64/libfoo.so.1 \
    --debug-root old=old-debug/usr/lib/debug \
    --debug-root new=new-debug/usr/lib/debug

# Fedora/RHEL: debug info located automatically by build-id
export DEBUGINFOD_URLS="https://debuginfod.fedoraproject.org/"
abicheck compare old-libfoo.so new-libfoo.so --debuginfod
```

!!! note "What `--debug-root`/`--debuginfod` feed into the DWARF parse today"
    On `dump` and `compare`, a build-id-tree, path-mirror, or debuginfod-fetched
    `.debug` file — a separate ELF file distinct from the input binary — is
    parsed for DWARF instead of the (stripped) input itself: the commands above
    correctly detect the `libpng16` struct-layout example even though
    `old/usr/lib64/libfoo.so.1`/`new/usr/lib64/libfoo.so.1` carry no `.debug_info`
    of their own (P1.1). **Split DWARF** (`.dwo`/`.dwp`, the first entry in the
    resolver chain above) and **dSYM bundles** (macOS) are resolved and reported
    (`Debug info: <source>`) but not yet threaded into the parse — a binary
    whose only debug info takes one of those two shapes still analyzes
    symbols-only. For those, package the debug info as its own artifact
    (RPM/Deb/tar, or a dSYM bundle) and pass it on directory/package inputs with
    the side-aware `--debug-info` flag, which *is* wired into the dump:

    ```bash
    abicheck compare libfoo-1.0.rpm libfoo-1.1.rpm \
        --debug-info old=libfoo-debuginfo-1.0.rpm \
        --debug-info new=libfoo-debuginfo-1.1.rpm
    ```

> **What these binary shapes actually look like** (`nm`/`readelf` output for a
> debug build vs. a fully stripped release vs. a split `.debug` file) is shown
> concretely in [Part 1 §2 of the ABI series](../concepts/abi-series/01-foundations.md#symbols-in-the-wild-full-stripped-and-debug-info-binaries).

## Verbose output

Add `-v` / `--verbose` to any native command to enable debug logging:

```bash
abicheck dump libfoo.so -H foo.h -v
abicheck compare old.json new.json -v
```
