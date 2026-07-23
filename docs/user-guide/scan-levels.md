---
doc_type: how-to
audience:
  - library-maintainer
  - ci-owner
level: beginner
summarizes:
  - evidence-model
lifecycle: active
generated: false
---

# Source-scan depth (`abicheck scan`)

`abicheck scan` is the one-shot orchestrator over `dump`/`compare`: it classifies
the changed paths, runs the always-on compiler-free pattern pre-scan, then runs a
**pinned evidence depth** and (with `--against`) compares against it.

`abicheck scan ARTIFACT [OPTIONS]` takes the scanned binary/snapshot as a
**positional** argument (not a flag). `--against OLD` is the previous
dump/library/directory/package to compare against; **omitting** `--against`
already means a one-build audit/hygiene/source-consistency scan (there is no
separate `--audit` flag any more — presence or absence of `--against` is what
selects the mode).

!!! info "This topic in three pages — you are on **Flags**"
    **Model** — [Evidence & Detectability](../concepts/evidence-and-detectability.md):
    the `L0`–`L5` evidence layers, what each can and cannot see, and the
    `--depth` dial that collects them. Read it first if the dial and the layers
    look like they overlap.
    **Worked example** — [What Each Level Sees](../concepts/what-each-level-sees.md):
    one tiny library walked up every level, with the actual data.
    **Flags** — this page: the practical flag reference and the
    [worked examples](#worked-examples) below.

**One dial selects how deep it goes — `--depth`, named by the evidence you get:**

- **`--depth binary|headers|build|source`** — the single knob (ADR-037 D5 /
  ADR-043 D2). `binary` = L0/L1 exported symbols + binary metadata; `headers` =
  +L2 header AST; `build` = +L3 build context; `source` = +L4 replay & the L5
  graph. **Omit it for `auto`** — the default: risk-driven when a
  `--since`/`--changed-path` seed is present, else a sensible preset.
- **`--depth source` always analyses *something* real, never a zero-TU no-op**
  (ADR-043 D3): with a `--since`/`--changed-path` seed it replays the *changed*
  TUs; without one it replays the **whole current library target** (what an
  older, now-removed `--depth full` rung used to require explicitly) — so it is
  never silently empty, just potentially more expensive unseeded.
- Absence of `--against` is already a single-build, no-baseline hygiene lint;
  there is no separate `--audit` flag any more.

!!! warning "A pinned depth is a contract (fail-loud)"
    Pinning a deep depth (`--depth build|source`) with **no source input**
    (`--sources`/`--build-info`) is an error, not a silent shallow scan: there is
    nothing to collect L3/L4/L5 from. Pass the evidence, or use the default `auto`
    for a best-effort binary scan. (The `auto` default never errors this way.)

!!! note "`--mode`/`--source-method` are gone"
    Earlier releases exposed a precise `--source-method s0…s6` axis and
    `--mode pr|pr-deep|baseline|audit` presets as deprecated aliases for
    `--depth`. Both have since been **removed outright** (passing either is now
    a plain usage error, exit 64) — use `--depth`. (`--depth symbols` was
    likewise renamed to `--depth binary`, with no alias kept.)

## Headers and includes — ARTIFACT side vs. `--against` side

`-H/--header [old=|new=]PATH` and `-I/--include [old=|new=]PATH` are
repeatable and side-aware. A bare path applies to the current ARTIFACT;
prefix it with `old=` to scope it to the `--against` side instead (`new=` is
the explicit, symmetric spelling of the default), e.g.
`--header old=old/include --header new=new/include`. This replaces the old,
separate `--baseline-header`/`--baseline-include` flags — there is no longer
a distinct flag name for the `--against` side, only a prefix on the same flag
(the same `old=`/`new=` convention `dump`/`compare` already use).

```bash
# Same header layout works for both ARTIFACT and the --against side
abicheck scan new/libfoo.so -H include/ --against old/libfoo.abi.json

# The header layout moved between the old release and the new build
abicheck scan new/libfoo.so \
  --header old=old/include --header new=new/include \
  --against old/libfoo.so
```

## What each depth reaches

| `--depth` | Reaches | Needs |
|-----------|---------|-------|
| `binary` | L0/L1 exported symbols + binary metadata + debug-info *presence* (no deep DWARF type walk, no L2 AST) + always-on pattern scan | just the artifact(s) |
| `headers` | + **L2** header AST (the public/internal boundary) | a public-header directory + a C/C++ frontend |
| `build` | + **L3** build context (flag/toolchain drift) | a compile DB / build dir |
| `source` | + **L4** source-ABI replay of changed TUs (seeded) or the whole library target (unseeded) + the **L5** graph | sources **and** `clang` (+ a diff seed to scope it to just the changed TUs) |

### What each depth does, in plain terms

- **`binary` — the always-available floor.** Compares the two binaries' exported
  symbols, SONAME, and dependencies, and runs a compiler-free pattern pre-scan.
  Needs only the two artifacts — no source, no build, no compiler. It is the
  deliberate way to **opt out** of source analysis: a fast gate, or when no
  sources/compile DB are available. It skips the deep DWARF type walk and the L2
  AST.
- **`headers` — the header API surface.** Adds the L2 header AST, which
  establishes the **public/internal boundary** — so an internal-symbol removal
  (compatible) is told apart from a public one (breaking). Needs a public-header
  directory (`-H`/`--header`) and a C/C++ frontend (`castxml` or `clang`).
- **`build` — build context.** Reads a compile database to see the flags each
  translation unit was built with, so it can flag `-fvisibility`/`-D`/standard or
  toolchain **drift** between the two builds, plus (when `clang -E` is available)
  macro-value and include-graph divergence. Needs a compile DB / build dir.
- **`source` — semantic replay, scope depends on whether you seed it.**
  Re-parses translation units with `clang` and replays their ABI — the only
  depth that sees inline / template / macro / default-argument / `constexpr`
  **body** changes, and it folds the L5 reachability graph. With a diff seed
  (`--since`/`--changed-path`) it replays only the *changed* TUs (cheap,
  PR-sized); without one it replays the **whole current library target**
  (ADR-043 D3 — never a zero-TU no-op, but potentially as expensive as a full
  release-baseline replay). Needs a compile DB and the source checkout
  (`--sources`).

### Benefits and cost at a glance

Each rung *adds* to the one below it — the benefit column is what that rung newly
catches, the cost/implication column is what it asks of you in return.

| `--depth` | What it newly catches (benefit) | Cost & implication | Pin it when |
|-----------|--------------------------------|--------------------|-------------|
| `binary` | removed/changed exports, SONAME, dependency & version changes, no-DWARF vtable/RTTI size shifts | cheapest, flat with project size; **no** source-only API changes, and every exported symbol is treated as ABI (public/internal churn not separated) | you only have the two binaries, or want a fast pre-check |
| `headers` | the **public/internal boundary** → separates real API breaks from internal churn; signature / type-layout / enum / `noexcept` changes | still cheap; needs public headers **and** a C/C++ frontend on `PATH`, else it falls back to binary-strict scope and over-reports | you have the public headers — this is the floor for a *trustworthy* verdict |
| `build` | build-flag / toolchain / `-std` / visibility **drift**; macro-value & include-graph divergence | cheap (~0.3–0.5s more); needs a compile DB / build dir — without one L3 is `not_collected` (reported, not a pass) | the two builds may differ in flags, standard, or visibility |
| `source` | inline / template / macro / default-argument / `constexpr` **body** changes, **plus** the L5 reachability graph that localizes and scopes findings | **the one cost cliff (L4)** — scales with C++ template depth; needs `--sources` + `clang` + a `--since` seed to stay cheap (unseeded, it replays every TU — the same cost as an amortized whole-library replay) | a per-PR gate that must catch source-body changes or wants per-symbol impact; unseeded, the same rung also serves as the whole-library replay for producing an amortized release baseline |

**The one rule that ties it together:** the binary diff (`binary`/`headers`) sets
the pass/fail **gate**; `build`/`source` mostly *localize and explain* and
add their own source-/API-level findings — they rarely flip the verdict. So spend
on L4 (`source`) for humans reviewing a PR or a release, and stay in the
cheap tier for a fast CI gate.

### Example-catalog status

The scan-depth table is scoped to the comparable v1/v2 shared-library targets.
That scope is complete: **141/141 targets scanned at every pinned depth**. The
full catalog is larger, but audit, cross-source, bundle, BTF, and snapshot
cases run through their dedicated proof lanes rather than this compare-style
scan matrix. `Eval targets` now covers that whole comparable-target scope: the
`NO_CHANGE` sentinel cases are checked as compatible/no-change outcomes. Bundle
component results are structural diagnostics, never separate ground truths.

| `--depth` | Comparable targets | Eval targets | Correct verdicts | Correct verdict coverage | False positives | False negatives | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| `binary` | 141 | 141 | 79 | 56.0% | 1 | 61 | Fast artifact gate; intentionally misses header/source-only breaks. |
| `headers` | 141 | 141 | 115 | 81.6% | 0 | 26 | Best low-cost CI gate when public headers are available. |
| `build` | 141 | 141 | 115 | 81.6% | 0 | 26 | Adds build context; no false positives in this matrix after advisory-crosscheck fix. |
| `source` | 141 | 141 | 141 | 100.0% | 0 | 0 | Highest recall in this matrix; source-smoke proofs cover consumer-only API hazards. Figures are for the diff-seeded rung; an unseeded whole-library replay reaches the same verdict signal at higher cost. |

False positives and false negatives are listed directly. Bundle-component rows
remain structural diagnostics in the 141-target matrix; only the dedicated
bundle lane scores the single canonical case-level verdict and proves findings
such as dangling intra-bundle imports and provider drift.

## What input each depth needs — and how to get it

Every depth needs a specific **input**; without it the matching coverage row is
`not_collected` (the scan never silently pretends it ran). Pick the row that
matches your goal, then supply the input named in column 3.

| Goal (use case) | `--depth` | Input you must provide | How to obtain it | If the input is missing |
|---|---|---|---|---|
| Binary-only ABI gate (removed/changed exports; no-DWARF vtable/RTTI size) | `binary` | two `.so` (or `.abi.json`) | release artifacts / conda / `.deb` | always available (L0/L1) |
| Header-aware API surface + internal-vs-public scoping + cross-source checks | `headers` | a public-header **directory** + a C/C++ frontend | `-H include/ --public-header-dir include/`; `castxml` **or** `clang` on `PATH` | a lone `-H file.h` does not establish a boundary → provenance/cross-checks stay dormant |
| Build-flag / toolchain / visibility drift (+ macro/include divergence) | `build` | an L3 compile database | `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` (configure-only), `meson setup`, `bazel aquery --output=jsonproto`, or `bear -- make`; pass via `--build-info`/`--compile-db` | L3 `not_collected`; the scan advises the exact remedy |
| Semantic source-ABI replay of changed TUs (macro/default-arg/inline/template/constexpr **body** changes) + L5 graph | `source` | L3 compile DB + source checkout + `clang` + generated headers present | configure for the DB; **codegen/partial build** for generated headers; seed with `--since`/`--changed-path` | without a seed, `source` replays the **whole current library target** instead of just the changed TUs (ADR-043 D3 — never a zero-TU no-op, but more expensive); missing generated headers → L4 `partial` |
| Full-library source replay (an amortized release baseline) | `source` (unseeded — no `--since`/`--changed-path`) | as above, whole library | amortized baseline build | expensive — the one cost cliff is at L4 |
| Single-build hygiene lint (accidental exports, leaks, unversioned/RTTI) | any depth, no `--against` | binary + public-header **dir** (+ optional L3/L4) | as above | `header_build_context_mismatch` needs L3; `odr_type_variant` needs L4 |

### Obtaining a compile database without a full build

The L3+ depths need a `compile_commands.json`; a pristine checkout has none.
Generate one — none of these compiles the library, they only configure / query
the build graph:

```bash
# CMake: configure-only (source also needs --sources . and a diff seed --since)
cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
abicheck scan new/libfoo.so -H include/ --build-info build --depth source …

# Bazel: query the action graph (no build); --build-info sniffs the aquery
# jsonproto and routes it straight to the Bazel adapter (ADR-037 D5 — no pack step)
bazel aquery 'mnemonic("CppCompile", //...)' --output=jsonproto > aq.json
abicheck scan new/libonedal_core.so -H include/ --build-info aq.json --depth build …
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
`abicheck` **detects the build system and runs the query
itself** for CMake (`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`), Bazel
(`bazel aquery`), and Make (`make -B -n -k -w`) — no flag, no manual build step.
The old `--allow-build-query` flag is no longer needed for `--sources`-driven
auto-querying (it is now a deprecated no-op): asking for a source-level scan *is*
the request to collect build evidence.

Make is queried with a fixed dry-run command (`make -B -n -k -w`) and the transcript
is scraped as reduced-confidence L3 evidence. This lets Make/EPICS-style projects
work without a manual `compile_commands.json`; a real compile DB (for example
from `bear -- make`, then `--compile-db compile_commands.json`) is still preferred
when available.

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
abicheck scan new/libfoo.so -H include/ --sources . \
  --config .abicheck.yml --depth source --against old/libfoo.abi.json
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
| `--ast-frontend {auto,castxml,clang,hybrid}` | which frontend parses the headers (env `ABICHECK_AST_FRONTEND`); `hybrid` runs castxml and clang together |
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
  frontend: auto          # auto | castxml | clang | hybrid
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

Each example shows the command, what depth it pins, and what to read in the
output. Every `scan` ends with a coverage block — always read it before trusting
the verdict (see [Reading the coverage block](#reading-the-coverage-block)).

### PR gate (the default) — diff-seeded `source`

The common CI case: gate a PR by comparing the just-built library against the
baseline from `main`, scoping the expensive L4 replay to the files the PR
touched. The `--since` seed is what keeps this cheaper than an unseeded,
whole-library `source` scan — without it, `source` replays every TU.

```bash
abicheck scan build/libfoo.so \
  -H include/ \
  --sources . --since origin/main \
  --against artifacts/libfoo-main.abi.json
```

- **Depth:** `auto` with a diff seed resolves to `source` (`--depth source`); pin
  it explicitly if you want a fixed rung.
- **Exit code:** `0` compatible, `2` source/API break, `4` ABI break (from the
  `--against` compare), `5` `--budget` overflow.
- `--depth source` folds the L5 reachability **edges scoped to the changed TUs**
  for cross-symbol impact in the report. The *whole-library* reachability graph
  is an internal level (`GRAPH`, D6) with no user-facing `--depth` rung.

### Single-build audit — no `--against`

Omitting `--against` already runs the intra-version cross-source hygiene checks
against **one** build — no previous version required (there is no separate
`--audit` flag any more). With just the binary and headers it catches accidental
exports, private-header leaks, and unversioned symbols:

```bash
abicheck scan libfoo.so -H include/
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
abicheck scan libfoo.so -H include/ \
  --build-info build/compile_commands.json
```

This reports the eight ADR-035 cross-source / single-release findings rather than
a two-version diff. The flagship cross-source cases —
[case148](../examples/case148_xcheck_header_build_mismatch.md)
(`header_build_context_mismatch`, L2 macros ↔ L3 flags) and
[case149](../examples/case149_xcheck_odr_variant.md) (`odr_type_variant`, L4
layout ↔ layout) — are findings that are invisible or ambiguous to any single
source and resolve only by crosschecking two.

### Cheap gate — no compiler, no sources

When you only have the two binaries (or want a fast pre-check), pin a cheap
depth. `--depth build` adds build-flag/toolchain drift, but only when you also
give it a build input to read — a compile DB or build dir via
`--build-info`/`--compile-db` (or a `--sources` tree); without one, L3 is
reported `not_collected` and no drift is checked. `--depth binary` stays on the
exported-symbol surface (L0) plus cheap debug-info *presence* and the always-on
pattern scan — it skips the deep DWARF type walk, so no compiler, headers, or
sources are needed. (`--depth headers` is the next rung up: it adds the L2 header
AST, which needs a header directory via `-H`/`--header` and a C/C++ frontend on
`PATH`.)

```bash
# build-flag drift only, flat ~0.3–0.5s regardless of project size
# (the compile DB is what supplies L3 — without it the scan is artifact-only)
abicheck scan new/libfoo.so --against old/libfoo.abi.json \
  --build-info build/compile_commands.json --depth build

# exported symbols + always-on lexical scan only (no DWARF walk, no L2 AST,
# no L3/L4/L5; no compiler needed)
abicheck scan new/libfoo.so --against old/libfoo.abi.json --depth binary
```

### Estimate before you spend — `--dry-run`

L4 cost scales with C++ template depth, so on a heavy library project the per-TU
replay cost first. `--dry-run` resolves and validates the invocation (depth,
scope, tool availability) and prints the projected per-layer cost for *this*
project without scanning anything or writing output (always exits 0).

```bash
abicheck scan libfoo.so --sources . --depth source --dry-run
```

### Release baseline — unseeded `source`

The reusable `--against` target that PR scans compare against is a
**`dump`-produced snapshot**, not a scan report. `scan -o` writes the rendered
scan report (text or JSON), so it cannot be fed back as `--against`; produce the
baseline with `abicheck dump` instead. Pass `--sources` to embed all of the
L3/L4/L5 facts so the later PR compare carries them:

```bash
# Produce the reusable baseline snapshot once per release
# (dump uses -H/--header, same as scan):
abicheck dump build/libfoo.so -H include/ \
  --sources . --version 1.0 -o artifacts/libfoo-1.0.abi.json

# PR scans then compare against it:
abicheck scan build/libfoo.so -H include/ \
  --sources . --since origin/main --against artifacts/libfoo-1.0.abi.json
```

To get a whole-library scan *report* of a release (replays every TU, folds the
full graph) for human review — as opposed to the reusable baseline above — run
`scan --depth source` **without** a `--since`/`--changed-path` seed (which
resolves to the whole current library target, ADR-043 D3 — what a now-removed
`--depth full` rung used to require explicitly) and send its report to `-o`:

```bash
abicheck scan build/libfoo.so -H include/ \
  --sources . --depth source -o artifacts/libfoo-1.0-scan.json
```

### Let risk pick the depth — `auto` (local/dev only)

Omit `--depth` and, when a diff seed is present, `auto` reads the risk of the
changed paths and picks a depth. It is the default and **never** overrides a
pinned depth — keep CI on a fixed `--depth` for reproducibility.

```bash
abicheck scan new.so -H include/ --since origin/main
```

### Reading the coverage block

`--depth` requests a level but `L` is *evidence*, so a scan can request a deep
level and only reach a shallow one (clang missing, no sources, a parse error).
`scan` never reports that as "failed" — it states the depth it **actually
reached** and, for each disabled check, the input or tool to add:

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
the *same* input scanned at `--depth headers` (pattern + AST), then deeper, with
the coverage block showing exactly what each depth proved and what it could not.

## Cost guide (rules of thumb)

Measured on two UXL libraries (full data: `validation/`):

| Tier | Depths | Relative cost |
|------|--------|---------------|
| **Cheap** | `binary`, `headers`, `build` | One price — dominated by the binary dump + lexical scan, *not* the source layer. |
| **Expensive** | `source` | clang per-TU AST replay (L4). |

- **The cliff is at L4 (`build`→`source`), and its height tracks C++ complexity.**
  L4 cost scales with template/STL instantiation depth, not `.so`/TU count — a
  heavy-C++ library can be ~7× slower at `source` than `build`, while a plain-C
  library is barely affected (~1.3×).
- **Choose a cheap depth by coverage, not cost.** `binary` (symbols + pattern
  only); `headers` adds the L2 API surface; `build` adds L3 build context.
- **`source` is only cheap when you give it a diff seed.** Without
  `--since <ref>` or `--changed-path <file>`, `source` replays the **whole
  current library target** instead of just the changed TUs (ADR-043 D3 — never a
  zero-TU no-op, but the most expensive shape, same cost as an amortized release
  baseline). With a real PR diff, `source` scopes L4 to the touched TUs and can be
  **an order of magnitude faster** for the identical verdict. Always pass
  `--since`/`--changed-path` in PR CI.
- **The verdict usually does not change with depth** — the binary diff sets the
  gate; L3–L5 add localization/explanation. For a pass/fail **gate**, the cheap
  tier is enough; spend on L4 (`source`) when you want source-body semantics or
  per-PR localization for humans.

See [Comparison Performance](../development/performance.md#scan-level-cost-model-one-cliff-at-l4)
for the measured numbers.
