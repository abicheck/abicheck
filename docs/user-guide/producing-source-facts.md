# Producing source facts (Flow A / B / C)

`abicheck`'s deepest evidence — **L4** (the source-ABI replay: inline bodies,
default arguments, templates, `constexpr`, macro values) and **L5** (the source
graph: call/include/dependency edges) — is derived from your **source**, not from
the shipped binary. This page is the practical guide to *producing* that source
evidence. For what the layers mean, see
[Build Info & Sources](../concepts/build-source-data.md) and
[Evidence & Detectability](../concepts/evidence-and-detectability.md); for a
worked example of the concrete L4/L5 data these producers yield (and what the
lower levels miss), see the
[level-by-level walk-through](../concepts/abi-api-handling.md#what-each-level-actually-sees-a-level-by-level-walk-through).
For how a scan *consumes* it, see [Source-scan depth](scan-levels.md).

Whichever producer you pick, the **output contract is identical** — an
`abicheck_inputs/` pack (or an inline `--sources` collection) that
[`abicheck merge`](baseline-management.md) folds onto the binary-side snapshot.
The producer is an implementation choice; the ingest never changes.

## Which producer? (pick one)

| | Flow A — replay | Flow B — `abicheck-cc` wrapper | Flow C — Clang plugin |
|---|---|---|---|
| **How** | `abicheck dump --sources` / `collect` re-parses each TU from `compile_commands.json` | wrap your compiler; it runs the extractor as a companion action | `-fplugin` reads the AST the compile already built |
| **Extra parse** | a full second parse (~5 s/TU on template-heavy C++) | a full second parse | **none** — zero-cost byproduct of the build |
| **Needs** | a compile DB (auto-inferred for cmake/make/bazel) | to front your build with `abicheck-cc` | a plugin built against your exact Clang major |
| **Portable?** | ✅ any toolchain | ✅ any compiler | ❌ ABI-locked to the loading Clang's LLVM major |
| **Reach for it when** | the default — you have (or can generate) a compile DB | you own the build command but not a compile DB | the second-parse cost is measurable on a big build **and** you own the toolchain image |

```mermaid
graph TD
    A{Own the toolchain image<br/>and second-parse cost hurts?} -->|yes| C[Flow C: Clang plugin]
    A -->|no| B{Have / can generate<br/>compile_commands.json?}
    B -->|yes| FA[Flow A: dump --sources]
    B -->|no| FB[Flow B: abicheck-cc wrapper]
```

Flow A is the supported default. Flow B and Flow C are optimizations for
specific situations — they exist to remove a step (a manual compile DB) or a cost
(the second parse), never to change the result.

## Flow A — replay from a compile database

```bash
# Source-only: infer the compile DB, replay L4, fold the L5 graph, all inline.
abicheck dump --sources . -H include/ --depth source -o libfoo.src.json

# Or against a real binary in one shot (L0–L5 in one snapshot):
abicheck dump libfoo.so -H include/ --sources . --compile-db build/compile_commands.json \
  --depth full -o libfoo.full.json
```

With just `--sources`, abicheck infers and runs the build-system query itself
(`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`, `bazel aquery`, or a `make -n`
transcript). Pass `--compile-db` when you already have a `compile_commands.json`
that isn't under the tree — it is the most faithful input.

## Flow B — the `abicheck-cc` compiler wrapper

Front your normal build command with `abicheck-cc`; it compiles as usual and runs
the extractor as a companion action, dropping an `abicheck_inputs/` pack:

```bash
export ABICHECK_INPUTS_DIR=abicheck_inputs
export ABICHECK_CC_HEADERS=include      # the public-header roots (see the trap below)
export ABICHECK_CC_LIBRARY=foo
abicheck-cc c++ -std=c++17 -Iinclude -c src/foo.cpp -o foo.o
```

### Wiring it into a real build system

You rarely invoke the compiler by hand — point the build system's compiler
variable at the wrapper so *every* TU is captured during a normal build. The
wrapper is argv-transparent (it prepends nothing and preserves the exit code),
so it drops in wherever the compiler name is configured:

```bash
# GNU make / autotools — override CC/CXX on the command line:
make CC="abicheck-cc gcc" CXX="abicheck-cc g++"

# EPICS / other make systems that name the C++ compiler CCC:
make CC="abicheck-cc gcc" CCC="abicheck-cc g++"

# CMake — set the launcher (no need to reconfigure the compiler itself):
cmake -DCMAKE_CXX_COMPILER_LAUNCHER="abicheck-cc" -S . -B build && cmake --build build
```

The `ABICHECK_CC_*` variables above are read from the environment, so `export`
them once before the build. Set `ABICHECK_CC_HEADERS` to the public-header root
**as the compiler resolves it** (see the trap below).

### Picking the extractor (`ABICHECK_CC_EXTRACTOR`)

The wrapper runs a second front-end to extract facts. `ABICHECK_CC_EXTRACTOR`
selects which:

| Value | Uses |
|-------|------|
| `auto` *(default)* | castxml if present, else clang |
| `castxml` | castxml (the default L2 backend) |
| `clang` | `clang -ast-dump=json` — **set this on a clang-only host** where castxml is not installed |

On a host without castxml, `auto` already falls back to clang; set
`ABICHECK_CC_EXTRACTOR=clang` explicitly when you want to pin it.

!!! warning "Extraction concurrency is bound by your build's `-jN`, not by `ABICHECK_L4_JOBS`"
    Each `abicheck-cc` invocation extracts its one TU synchronously, so a
    parallel `make -jN` / `cmake --build -jN` runs **up to N** clang/castxml
    front-ends at once. `ABICHECK_L4_JOBS` only throttles the Flow-A
    `dump --sources` replay path — the wrapper does **not** read it. A
    template-heavy TU's clang JSON AST can need several GiB, so on a
    memory-constrained host cap the build parallelism (`-j1`/`-j2`) rather than
    reaching for `ABICHECK_L4_JOBS`.

## Flow C — the Clang facts plugin

A compiled plugin that emits the same facts from the AST Clang already built —
**no second parse**. Build it once against your pinned Clang, then inject it:

```bash
clang++ -std=c++17 -Iinclude \
  -fplugin=./libabicheck-facts.so \
  -Xclang -plugin-arg-abicheck-facts -Xclang out=abicheck_inputs \
  -Xclang -plugin-arg-abicheck-facts -Xclang public-roots=include \
  -c src/foo.cpp -o foo.o
```

See [`contrib/abicheck-clang-plugin/README.md`](https://github.com/abicheck/abicheck/blob/main/contrib/abicheck-clang-plugin/README.md)
for the build. The plugin is **ABI-locked to the loading Clang's LLVM major**
(a plugin built against LLVM 18 only loads into `clang` 18) — that is the price
of the zero-parse path, and why Flow A/B remain the portable defaults.

## The one trap: public-roots must match how headers *resolve*

!!! warning "Point the public-header root at the resolved path, not the install dir"
    Flow B (`ABICHECK_CC_HEADERS`) and Flow C (`public-roots=`) classify a
    declaration as public by the **physical path the compiler resolved its header
    to**. If an earlier `-I` makes `<foo/bar.h>` resolve to `src/foo/bar.h` while
    you set the root to the *installed* `include/`, the root matches **nothing**
    and the pack comes back **empty** — even though it all looks configured.
    Include *order* decides the resolved path, not the install layout.

    **Find the real path** with `-H`, then set the root to that directory:

    ```bash
    clang++ <your -I flags> -H -fsyntax-only src/foo.cpp 2>&1 | grep 'bar.h'
    # . ./src/foo/bar.h   →  public-roots=src/foo  (not include/)
    ```

    Since ADR-038 Flow C, the plugin **fails loud** here instead of silently: if
    `public-roots` matches zero declarations while header decls were seen outside
    the roots, it prints a `public-roots matched 0 declarations` diagnostic naming
    an example header and the `clang -H` tip, and records it in the pack's
    `diagnostics`.

    And if you omit `public-roots=` entirely, the plugin **auto-derives** roots
    from the compile's own `-I`/`-iquote` include dirs (compiler/system paths
    excluded) and emits a one-time inference note — so a forgotten flag yields a
    populated (if slightly broad) surface rather than an empty pack. Still pass an
    explicit `public-roots=` when you want the surface scoped precisely to your
    installed public headers.

## Then: fold the facts onto the binary

However you produced them, ingest is the same — dump the binary side, then merge:

```bash
abicheck dump libfoo.so -o libfoo.bin.json
abicheck merge libfoo.bin.json ./abicheck_inputs/ -o libfoo.baseline.json
```

`merge` links each source declaration to the binary's exported symbol (matching
ctor/dtor ABI clone variants — `C1`/`C2`/`C3`, `D0`/`D1`/`D2` — so one source
constructor claims all of its exported symbols). The result is a single
self-contained `.baseline.json` carrying L0–L5, ready for
[`compare`](local-compare.md) or [`scan --baseline`](scan-levels.md).

### Reading the L4 coverage line

`merge` prints an L4 coverage summary to stderr:

```text
  L4_source_abi: present (471/834 symbols matched, 834/834 accounted, 0 unmatched)
```

Read it as **two different numbers**:

- **`matched`** — exports that map *directly* to a public source declaration.
  For a real C++ library this is often only ~50 %: the rest of the export table
  is compiler-synthesized (vtables/typeinfo/thunks) or stdlib/internal, which
  never carry a source declaration of their own. A low `matched` ratio is
  **not** a coverage gap.
- **`accounted` / `unmatched`** — the completeness number. Every export is
  either matched, *attributed* to its owner (synthesized RTTI/vtable, template
  instantiation, allocator interposer), or *classified* (stdlib/TBB dependency,
  internal/private export). `unmatched` is what's genuinely unexplained — aim
  for **0**.

The full breakdown lives in the merged snapshot under
`build_source.source_abi.coverage` (and the per-symbol reasons under
`…mappings.non_public_symbol_to_reason` / `…synthesized_symbol_to_owner`):

```bash
python -c "import json,sys; \
c=json.load(open('libfoo.baseline.json'))['build_source']['source_abi']['coverage']; \
print(json.dumps(c, indent=2))"
```

A worked, real-library example (EPICS pvxs: 471 matched, 86 synthesized, 277
classified, **0 unmatched**) is in
[`validation/pvxs-source-scan-mapping-2026-07.md`](https://github.com/abicheck/abicheck/blob/main/validation/pvxs-source-scan-mapping-2026-07.md).

If instead you see a warning that the pack carries **no public entities** or
matched **0/N** exports, that is the `public-roots` / `ABICHECK_CC_HEADERS`
resolution trap above — not an empty API.
