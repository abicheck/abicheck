# Source-scan levels (`abicheck scan`)

`abicheck scan` is the one-shot orchestrator over `dump`/`compare`: it classifies
the changed paths, runs the always-on compiler-free pattern pre-scan, then runs a
**pinned evidence level** and (with `--baseline`) compares against it.

!!! tip "First time here? Read the model, then the flags."
    The `s0…s6`, `L0…L5`, `--mode`, and `--depth` knobs name **two different
    axes** (`S` = the method, `L` = the evidence) plus presets over them. If they
    look like they overlap, read [Scan Levels (S vs L)](../concepts/scan-and-evidence-levels.md)
    for the mental model first — this page is the practical flag reference and the
    [worked examples](#worked-examples) below.

**One dial selects how deep it goes — `--depth`, named by the evidence you get:**

- **`--depth binary|headers|build|source|full`** — the single knob (ADR-037 D5).
  `binary` = L0/L1 exported symbols + binary metadata; `headers` = +L2 header AST;
  `build` = +L3 build context; `source` = +L4 replay & the L5 graph; `full` =
  deepest (whole-library replay). **Omit it for `auto`** — the default: risk-driven
  when a `--since`/`--changed-path` seed is present, else a sensible preset.
- **`--audit`** is orthogonal: a single-build, no-baseline hygiene lint (it does
  not need a previous version).

!!! warning "A pinned depth is a contract (fail-loud)"
    Pinning a deep depth (`--depth build|source|full`) with **no source input**
    (`--sources`/`--build-info`) is an error, not a silent shallow scan: there is
    nothing to collect L3/L4/L5 from. Pass the evidence, or use the default `auto`
    for a best-effort binary scan. (The `auto` default never errors this way.)

??? note "Expert axes (`--source-method`, `--mode`) — deprecated aliases"
    The precise **S-axis** (`--source-method s0…s6`, the technique) and the
    **`--mode pr|pr-deep|baseline|audit`** presets still work but are now hidden,
    **deprecated** aliases (they warn and map onto `--depth`) for one release.
    Prefer `--depth`. The `s0…s6` / `L0…L5` model is still the spine — see
    [Scan Levels (S vs L)](../concepts/scan-and-evidence-levels.md) for the mental
    model; `--depth` is its friendly façade. (`--depth symbols` was renamed to
    `--depth binary`; `symbols` keeps working as a warned alias.)

## What each level reaches

| Level | Technique | Evidence reached |
|-------|-----------|------------------|
| `s0` | diff classifier (risk tags) | L0/L1 binary + DWARF + always-on pattern scan |
| `s1` | compile-DB / build-flag scan | + **L3** build context |
| `s2` | preprocessor (macros/includes) | conditional S2 tier over L3 (`clang -E` macro/include capture) |
| `s3` | lexical pattern scan | pattern facts only (same always-on scan) |
| `s4` | symbol / reference index | + L3 + **L5** source graph (no L4) |
| `s5` | targeted semantic AST (changed TUs) | + **L4** source-ABI replay + L5 edges |
| `s6` | full AST (all TUs) | + L4 over the whole library |

### What each method does, in plain terms

- **`s0` — binary diff (the always-available floor).** Compares the two
  binaries' exported symbols, SONAME, and dependencies (plus DWARF types if the
  build ships them), and runs a compiler-free pattern pre-scan. Needs only the
  two artifacts — no source, no build. It is worth naming because the **default
  is not binary-only**: `--mode pr` is `s5`, so `s0` (or `--depth binary`) is how
  you deliberately **opt out** of source analysis — a fast gate, or when no
  sources/compile DB are available — and pin that choice reproducibly.
- **`s1` — build context.** Reads a compile database to see the flags each
  translation unit was built with, so it can flag `-fvisibility`/`-D`/standard or
  toolchain **drift** between the two builds. Needs a compile DB.
- **`s2` — preprocessor.** Runs `clang -E` to capture macro *values* and the
  include graph — catches macro-value changes, include divergence, and
  private/generated-header leaks. Needs a compile DB.
- **`s3` — lexical pattern scan.** A pure text/regex pass over the sources (no
  compiler) — the same always-on pattern facts `s0` already runs, pinned as a
  level. The cheapest source-aware option.
- **`s4` — reference graph.** Builds a source→symbol reachability graph — *which
  public exports reach a changed internal declaration*. Needs a compile DB; no
  semantic replay (no L4).
- **`s5` — semantic replay of changed TUs (the `pr` default).** Re-parses the
  *changed* translation units with `clang` and replays their ABI — the only level
  that sees inline / template / macro / default-argument / `constexpr` **body**
  changes. Needs a compile DB, the source checkout (`--sources`), and a diff seed
  (`--since`/`--changed-path`); without a seed it falls back to a headers-only
  replay.
- **`s6` — full semantic replay.** Like `s5` but over the **whole** library, not
  just the changed TUs — the most thorough and the most expensive (the one real
  cost cliff). Used by `--mode baseline`.

`--mode` presets: `pr` = `(s5, source)`, `pr-deep` = `(s5, graph)` (full L5
reachability), `baseline` = `(s6, full)`, `audit` = `(s5, source)` intra-version
(single-build hygiene, no baseline).

## What input each use case needs — and how to get it

Every level needs a specific **input**; without it the matching coverage row is
`not_collected` (the scan never silently pretends it ran). Pick the row that
matches your goal, then supply the input named in column 3.

| Goal (use case) | Level | Input you must provide | How to obtain it | If the input is missing |
|---|---|---|---|---|
| Binary-only ABI gate (removed/changed exports; no-DWARF vtable/RTTI size) | `--depth binary` (`s0`) | two `.so` (or `.abi.json`) | release artifacts / conda / `.deb` | always available (L0/L1) |
| Header-aware API surface + internal-vs-public scoping + cross-source checks | L2 (intrinsic, with `-H`) | a public-header **directory** + a C/C++ frontend | `-H include/ --public-header-dir include/`; `castxml` **or** `clang` on `PATH` | a lone `-H file.h` does not establish a boundary → provenance/cross-checks stay dormant |
| Build-flag / toolchain / visibility drift | `s1` / `--depth build` | an L3 compile database | `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` (configure-only), `meson setup`, `bazel aquery --output=jsonproto`, or `bear -- make`; pass via `--build-info`/`--compile-db` | L3 `not_collected`; the scan advises the exact remedy |
| Macro-value / include divergence; private/generated-header leaks | `s2` | L3 compile DB + `clang -E` | same as `s1` (the `-E` pass needs the TU's full flag set) | preprocessor tier skipped (coverage row, not a pass) |
| Source→symbol reachability graph (which exports reach a changed internal decl) | `s4` / `--source-method s4` | L3 compile DB | same as `s1` | L5 `not_collected` (no L4 replay either); reach it via `--source-method s4` (there is no user-facing `--depth` rung for the graph) |
| Semantic source-ABI replay of changed TUs (macro/default-arg/inline/template/constexpr **body** changes) | `s5` / `--depth source` / `--mode pr` | L3 compile DB + source checkout + `clang` + generated headers present | configure for the DB; **codegen/partial build** for generated headers; seed with `--since`/`--changed-path` | without a seed, `s5` falls back to a headers-only public-API replay and emits an advisory (not a full per-TU replay); missing generated headers → L4 `partial` |
| Full-library source replay | `s6` / `--depth full` / `--mode baseline` | as `s5`, whole library | amortized baseline build | expensive — the one cost cliff is at L4 |
| Single-build hygiene lint (accidental exports, leaks, unversioned/RTTI) | `--audit` (no baseline) | binary + public-header **dir** (+ optional L3/L4) | as above | `header_build_context_mismatch` needs L3; `odr_type_variant` needs L4 |

### Obtaining a compile database without a full build

The L3+ levels need a `compile_commands.json`; a pristine checkout has none.
Generate one — none of these compiles the library, they only configure / query
the build graph:

```bash
# CMake: configure-only (s5/s6 also need --sources . and a diff seed --since)
cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
abicheck scan --binary new/libfoo.so -H include/ --build-info build --depth source …

# Bazel: query the action graph (no build); --build-info sniffs the aquery
# jsonproto and routes it straight to the Bazel adapter (ADR-037 D5 — no pack step)
bazel aquery 'mnemonic("CppCompile", //...)' --output=jsonproto > aq.json
abicheck scan --binary new/libonedal_core.so -H include/ --build-info aq.json --depth build …
```

!!! tip "`--build-info` auto-detects the format (ADR-037 D5)"
    `--build-info` sniffs its argument by content, so each kind "just works":
    a `compile_commands.json` (CMake/Meson/`bear`), a Bazel
    `--output=jsonproto` **aquery** or **cquery** dump, a build **directory**
    (searched for `compile_commands.json`), or a `collect` **pack**. A Bazel
    query result is routed to the Bazel adapter — not mis-read as a compile DB.

!!! note "Generated headers"
    L4 replay re-parses each TU with `clang`. If a TU `#include`s a header that
    is *generated* during the build (e.g. `version.h`, `*.pb.h`, TableGen
    `*.inc`), a configure-only tree won't have it and that TU's replay is
    reported `partial` — run the project's codegen step first.

### Letting `abicheck` drive the build query

**You usually don't pre-generate a compile DB at all — just pass `--sources`.**
When a source-level depth needs build evidence and no compile DB exists,
`abicheck` **detects the build system (CMake/Make/Bazel) and runs the query
itself** (`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`, `make -n`, `bazel aquery`)
— no flag, no manual build step. The old `--allow-build-query` flag is gone
(deprecated to a no-op): asking for a source-level scan *is* the request to
collect build evidence.

Only an abicheck-constructed command runs automatically. An *arbitrary*
`build.query` command runs only when it is operator-supplied — an explicit
`--config` (the project `.abicheck.yml` contract) or `--build-query` on the CLI.
An auto-discovered `.abicheck.yml` sitting inside the `--sources` tree is never
trusted to execute its `build.query` (it may be attacker-controlled); its
non-executing settings are still honoured. Pre-generating and passing a
`--compile-db` yourself remains supported as an advanced option.

```yaml
# .abicheck.yml
build:
  query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
```

```bash
abicheck scan --binary new/libfoo.so -H include/ --sources . \
  --config .abicheck.yml --source-method s5 --baseline old/libfoo.abi.json
```

## Compile context for header parsing (L2)

The L2 header AST is what establishes the **public/internal boundary** — which
declarations are API, so the cross-source checks and public-surface scoping can
tell an *internal* symbol removal (compatible) from a public one (breaking). To
build it, the frontend must parse your public headers the way your compiler does:
it needs the include roots they `#include`, the C++ standard they assume, and any
`-D` feature macros that gate declarations. When that context is missing the
header parse fails, the scan falls back to a binary-strict scope, and internal
removals get reported as BREAKING.

`scan` now takes the **same** compile-context flags as `dump` (they share one
definition, so they never drift):

| Flag | Purpose |
|---|---|
| `--ast-frontend {auto,castxml,clang}` | which frontend parses the headers (env `ABICHECK_AST_FRONTEND`) |
| `-I/--include DIR` | an include root your headers need (repeatable) |
| `--gcc-options "…"` | extra compiler flags (whitespace-split), e.g. `--gcc-options "-std=c++20 -DFOO=1"` |
| `--gcc-option TOK` | one flag verbatim (repeatable; for a flag + spaced value) |
| `--gcc-path` / `--gcc-prefix` | a cross-compiler / cross-toolchain prefix |
| `--sysroot DIR` | an alternate system root |
| `--nostdinc` | do not search system includes (and disable the auto-probe below) |

### Where each setting belongs (CLI vs config)

Four layers resolve the context, **highest precedence first**:

1. **Explicit CLI flag** — a per-run override (`--gcc-options`, `--sysroot`, …).
2. **`.abicheck.yml` `compile:` block** — your project's stable contract,
   reviewed in PRs (see below). Put include roots, `std`, and `defines` here so
   every scan/CI run is reproducible without re-typing them.
3. **Compile-DB-derived flags** — *planned*: per-TU `-I`/`-std`/`-D` taken from a
   `--compile-db`. Today the compile DB feeds L3–L5 only.
4. **Auto-detected system includes** — the default floor (below).

```yaml
# .abicheck.yml
compile:
  frontend: auto          # auto | castxml | clang
  std: c++20
  include_dirs: [include, third_party/include]
  defines: [FOO_ENABLE_FEATURE=1]
  # sysroot: /opt/sysroot
  # nostdinc: false
```

### Auto-detection of system includes (on by default)

`castxml` finds the host C++ standard library for free, because it runs your real
compiler to discover its built-in include paths. The `clang` frontend did not —
so on a minimal container, a non-standard prefix, or a Conda-clang setup it could
not find `<cstddef>` and the parse failed. The clang backend now **probes the
host GNU compiler** (`g++ -E -v`) for its system include dirs and injects them, so
a bare `scan -H include/` finds libstdc++ without extra flags. Disable it with
`--nostdinc`, an explicit `--sysroot`, or `ABICHECK_AUTO_SYSTEM_INCLUDES=0`.

!!! warning "Auto-detection is partial — know its limits"
    - It recovers **system** headers (libstdc++/libc), **not your project's own**
      include roots or `-D` feature macros. Umbrella headers still need
      `-I`/the `compile:` block for their own include root.
    - A **wrong `-std`** changes the ABI surface (concepts, `char8_t`,
      `noexcept`-in-type, inline-namespace versioning) — parse at the standard the
      library was *built* with or L2 shows phantom add/remove churn.
    - **Wrong/missing `-D` defines** change which declarations are visible —
      macro-gated internals (e.g. `mylib::detail::*`) or the libstdc++ dual ABI
      (`_GLIBCXX_USE_CXX11_ABI`) — and produce exactly the "scope divergence"
      false BREAKINGs this feature exists to remove.
    - Auto-detection reads the **host** toolchain → it is wrong for
      cross-compiles (use `--gcc-prefix`/`--sysroot` or the config block) and
      makes results host-dependent (pin context in config for reproducible CI).

## Worked examples

Each example shows the command, what level it pins, and what to read in the
output. Every `scan` ends with a coverage block — always read it before trusting
the verdict (see [Reading the coverage block](#reading-the-coverage-block)).

### PR gate (the default) — diff-seeded `s5`

The common CI case: gate a PR by comparing the just-built library against the
baseline from `main`, scoping the expensive L4 replay to the files the PR
touched. The `--since` seed is what makes `pr` cheaper than a full `baseline`
scan — without it, `s5` replays every TU.

```bash
abicheck scan \
  --binary build/libfoo.so --headers include/ \
  --sources . --since origin/main \
  --baseline artifacts/libfoo-main.abi.json
```

- **Level:** `--mode pr` (the default) = `(s5, source)`.
- **Exit code:** `0` compatible, `2` source/API break, `4` ABI break (from the
  baseline compare), `5` `--budget` overflow.
- Add `--mode pr-deep` to also fold the full L5 reachability graph when you want
  cross-symbol impact in the report.

### Single-build audit — no baseline

`--audit` runs the intra-version cross-source hygiene checks against **one**
build — no previous version required. With just the binary and headers it catches
accidental exports, private-header leaks, and unversioned symbols:

```bash
abicheck scan --binary libfoo.so --headers include/ --audit
```

Worked example cases for each audit finding:
[case143](../examples/case143_audit_accidental_export.md) (`exported_not_public`),
[case144](../examples/case144_audit_private_header_leak.md) (`private_header_leak`),
[case145](../examples/case145_audit_unversioned_export.md) (`unversioned_exported_symbol`),
[case146](../examples/case146_audit_rtti_for_internal.md) (`rtti_for_internal_type`).
[case150](../examples/case150_xcheck_export_public_pair.md) shows the
bidirectional `exported_not_public` ↔ `public_not_exported` pair, and
[case151](../examples/case151_xcheck_provider_matrix.md) shows confidence growing
with the number of corroborating sources (the provider-agreement matrix).

Some audit checks need more evidence than the artifact tiers provide:
`header_build_context_mismatch` compares the headers' parse context against the
real build flags, so it only fires when you also pass an L3 build input
(`--build-info`/`--compile-db` or `--sources`) — without one it is reported as a
skipped coverage row, not a pass:

```bash
abicheck scan --binary libfoo.so --headers include/ \
  --build-info build/compile_commands.json --audit
```

This is `(s5, source)` run intra-version; it reports the eight ADR-035
cross-source / single-release findings rather than a two-version diff. The
flagship cross-source cases —
[case148](../examples/case148_xcheck_header_build_mismatch.md)
(`header_build_context_mismatch`, L2 macros ↔ L3 flags) and
[case149](../examples/case149_xcheck_odr_variant.md) (`odr_type_variant`, L4
layout ↔ layout) — are findings that are invisible or ambiguous to any single
source and resolve only by crosschecking two.

### Cheap gate — no compiler, no sources

When you only have the two binaries (or want a fast pre-check), pin a cheap
level. `--depth build` (`s1`) adds build-flag/toolchain drift, but only when you
also give it a build input to read — a compile DB or build dir via
`--build-info`/`--compile-db` (or a `--sources` tree); without one, L3 is
reported `not_collected` and no drift is checked. `--depth headers` (`s0`) stays
on the always-on pattern scan and the artifact tiers only:

```bash
# build-flag drift only, flat ~0.3–0.5s regardless of project size
# (the compile DB is what supplies L3 — without it the scan is artifact-only)
abicheck scan --binary new/libfoo.so --baseline old/libfoo.abi.json \
  --build-info build/compile_commands.json --depth build

# artifact + always-on lexical scan only (no L3/L4/L5; no build input needed)
abicheck scan --binary new/libfoo.so --baseline old/libfoo.abi.json --depth headers
```

### Estimate before you spend — `--estimate`

L4 cost scales with C++ template depth, so on a heavy library project the per-TU
replay cost first. `--estimate` is a dry run: it prints the projected per-layer
cost for *this* project and scans nothing.

```bash
abicheck scan --binary libfoo.so --sources . --mode pr --estimate
```

### Release baseline — full `s6`

The reusable `--baseline` that PR scans compare against is a **`dump`-produced
snapshot**, not a scan report. `scan -o` writes the rendered scan report (text or
JSON), so it cannot be fed back as a `--baseline`; produce the baseline with
`abicheck dump` instead. Pass `--sources` to embed the full-depth L3/L4/L5 facts
so the later PR compare carries them:

```bash
# Produce the reusable baseline snapshot once per release
# (dump uses -H/--header — the plural --headers alias is scan-only):
abicheck dump build/libfoo.so -H include/ \
  --sources . --version 1.0 -o artifacts/libfoo-1.0.abi.json

# PR scans then compare against it:
abicheck scan --binary build/libfoo.so --headers include/ \
  --sources . --since origin/main --baseline artifacts/libfoo-1.0.abi.json
```

To get a full-depth scan *report* of a release (replays every TU, folds the full
graph) for human review — as opposed to the reusable baseline above — run
`scan --mode baseline` and send its report to `-o`:

```bash
abicheck scan --binary build/libfoo.so --headers include/ \
  --sources . --mode baseline -o artifacts/libfoo-1.0-scan.json
```

### Let risk pick the depth — `--source-method auto` (local/dev only)

`auto` reads the risk of the changed paths and picks an S-method (capped at
`s5`). It is opt-in and **never** fires for a pinned CI level — keep CI on a
fixed `--mode`/`--source-method` for reproducibility.

```bash
abicheck scan --binary new.so -H include/ --source-method auto --since origin/main
```

### Reading the coverage block

`S` is a *method* and `L` is *evidence*, so a scan can request a deep level and
only reach a shallow one (clang missing, no sources, a parse error). `scan` never
reports that as "failed" — it states the depth it **actually reached** and, for
each disabled check, the input or tool to add:

```text
Checks enabled for this scan (and why others are not):
  [on]  Symbol presence & linkage … — from the binary's dynamic symbol table
  [on]  Build-flag & toolchain drift … — from build-system data
  [off] Macros, default args, inline/template/constexpr bodies — no sources/clang:
        source-only API changes are not detected
```

An `[off]` line is the precise input to add (here: install clang and pass
`--sources`). See
[Build Info & Sources § Evidence coverage](../concepts/build-source-data.md#evidence-coverage)
for the full coverage and capability report.
[case147](../examples/case147_scan_depth_ladder.md) is the legibility anchor:
the *same* input scanned at S3 (pattern only), then deeper, with the coverage
block showing exactly what each depth proved and what it could not.

## Cost guide (rules of thumb)

Measured on two UXL libraries (full data: `validation/`):

| Tier | Levels | Relative cost |
|------|--------|---------------|
| **Cheap** | `s0`–`s4` | One price — dominated by the binary dump + lexical scan, *not* the source layer. |
| **Expensive** | `s5`, `s6`, and the `pr`/`pr-deep`/`baseline`/`audit` modes | clang per-TU AST replay (L4). |

- **The cliff is at L4 (`s4`→`s5`), and its height tracks C++ complexity.** L4
  cost scales with template/STL instantiation depth, not `.so`/TU count — a
  heavy-C++ library can be ~7× slower at `s5` than `s4`, while a plain-C library
  is barely affected (~1.3×).
- **Choose a cheap level by coverage, not cost.** `s0` ≈ `s3` (binary + pattern
  only); `s1` adds L3 build context; **`s4` adds the L5 reachability graph
  without paying for L4** — the best cheap level when you want impact/call
  structure.
- **`s5`/`pr` is only cheaper than `s6` if you give it a diff seed.** Without
  `--since <ref>` or `--changed-path <file>`, the changed-TU set is empty and
  `s5` replays every TU — the same cost as `s6`. With a real PR diff, `s5`
  scopes L4 to the touched TUs and can be **an order of magnitude faster** for
  the identical verdict. Always pass `--since`/`--changed-path` in PR CI.
- **The verdict usually does not change with depth** — the binary diff sets the
  gate; L3–L5 add localization/explanation. For a pass/fail **gate**, the cheap
  tier is enough; spend on L4 (`s5`/`s6`) when you want source-body semantics or
  per-PR localization for humans.

See [Comparison Performance](../development/performance.md#scan-level-cost-model-one-cliff-at-l4)
for the measured numbers.
