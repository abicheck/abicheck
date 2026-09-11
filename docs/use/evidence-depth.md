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

# Evidence depth (`--depth`)

`--depth` is the one dial that decides **how much evidence** abicheck collects
about a build. Both `dump` and `compare` take it, with the same four rungs and
the same meaning, so a baseline you dump and a comparison you run can be pinned
to the same depth.

!!! info "This topic in three pages — you are on **Flags**"
    **Model** — [Evidence & Detectability](../learn/evidence-and-detectability.md):
    the `L0`–`L5` evidence layers, what each can and cannot see, and the
    `--depth` dial that collects them. Read it first if the dial and the layers
    look like they overlap.
    **Worked example** — [What Each Level Sees](../learn/what-each-level-sees.md):
    one tiny library walked up every level, with the actual data.
    **Flags** — this page: the practical flag reference and the
    [worked examples](#worked-examples) below.

**The dial is named by the evidence you get:**

- **`--depth binary|headers|build|source`** — `binary` = L0/L1 exported symbols
  + binary metadata; `headers` = +L2 header AST; `build` = +L3 build context;
  `source` = +L4 replay & the L5 graph.
- **Omit it** and abicheck uses whatever evidence the inputs actually carry:
  with no `--sources`/`--build-info` and no `source.method` in the project
  config, the source layers are simply off.
- A pinned deep depth is a **contract**, not a preference — see the warning
  below.

!!! warning "A pinned depth is a contract (fail-loud)"
    Pinning a deep depth (`--depth build|source`) with **no source input**
    (`--sources`/`--build-info`) is an error, not a silent shallow run: there is
    nothing to collect L3/L4/L5 from. Pass the evidence, or omit `--depth` for a
    best-effort artifact-level analysis.

!!! note "`--mode`/`--source-method` are gone"
    Earlier releases exposed a precise `--source-method s0…s6` axis and
    `--mode pr|pr-deep|baseline|audit` presets as deprecated aliases for
    `--depth`. Both have since been **removed outright** (passing either is now
    a plain usage error, exit 64) — use `--depth`. (`--depth symbols` was
    likewise renamed to `--depth binary`, with no alias kept.)

## Headers and includes — per side

On `compare`, `-H/--header [old=|new=]PATH` and `-I/--include [old=|new=]PATH`
are repeatable and side-aware. A bare path applies to both sides; prefix it with
`old=`/`new=` to scope it to one, e.g.
`--header old=old/include --header new=new/include`. `dump` takes the same
flags without the prefixes, since it only ever has one side.

```bash
# Same header layout for both sides
abicheck compare old/libfoo.abi.json new/libfoo.so -H include/

# The header layout moved between the old release and the new build
abicheck compare old/libfoo.so new/libfoo.so \
  --header old=old/include --header new=new/include
```

## What each depth reaches

| `--depth` | Reaches | Needs |
|-----------|---------|-------|
| `binary` | L0/L1 exported symbols + binary metadata + debug-info *presence* (no deep DWARF type walk, no L2 AST) | just the artifact(s) |
| `headers` | + **L2** header AST (the public/internal boundary) | a public-header directory + a C/C++ frontend |
| `build` | + **L3** build context (flag/toolchain drift) | a compile DB / build dir |
| `source` | + **L4** source-ABI replay of the library target + the **L5** graph | sources **and** `clang` |

### What each depth does, in plain terms

- **`binary` — the always-available floor.** Reads the binary's exported
  symbols, SONAME, and dependencies. Needs only the artifact(s) — no source, no
  build, no compiler. It is the deliberate way to **opt out** of source
  analysis: a fast gate, or when no sources/compile DB are available. It skips
  the deep DWARF type walk and the L2 AST.
- **`headers` — the header API surface.** Adds the L2 header AST, which
  establishes the **public/internal boundary** — so an internal-symbol removal
  (compatible) is told apart from a public one (breaking). Needs a public-header
  directory (`-H`/`--header`) and a C/C++ frontend (`castxml` or `clang`).
- **`build` — build context.** Reads a compile database to see the flags each
  translation unit was built with, so it can flag `-fvisibility`/`-D`/standard or
  toolchain **drift** between the two builds, plus (when `clang -E` is available)
  macro-value and include-graph divergence. Needs a compile DB / build dir.
- **`source` — semantic replay.** Re-parses translation units with `clang` and
  replays their ABI — the only depth that sees inline / template / macro /
  default-argument / `constexpr` **body** changes, and it folds the L5
  reachability graph. Needs a compile DB and the source checkout (`--sources`).

### Benefits and cost at a glance

Each rung *adds* to the one below it — the benefit column is what that rung newly
catches, the cost/implication column is what it asks of you in return.

| `--depth` | What it newly catches (benefit) | Cost & implication | Pin it when |
|-----------|--------------------------------|--------------------|-------------|
| `binary` | removed/changed exports, SONAME, dependency & version changes, no-DWARF vtable/RTTI size shifts | cheapest, flat with project size; **no** source-only API changes, and every exported symbol is treated as ABI (public/internal churn not separated) | you only have the two binaries, or want a fast pre-check |
| `headers` | the **public/internal boundary** → separates real API breaks from internal churn; signature / type-layout / enum / `noexcept` changes | still cheap; needs public headers **and** a C/C++ frontend on `PATH`, else it falls back to binary-strict scope and over-reports | you have the public headers — this is the floor for a *trustworthy* verdict |
| `build` | build-flag / toolchain / `-std` / visibility **drift**; macro-value & include-graph divergence | cheap (~0.3–0.5s more); needs a compile DB / build dir — without one L3 is `not_collected` (reported, not a pass) | the two builds may differ in flags, standard, or visibility |
| `source` | inline / template / macro / default-argument / `constexpr` **body** changes, **plus** the L5 reachability graph that localizes and scopes findings | **the one cost cliff (L4)** — scales with C++ template depth; needs `--sources` + `clang`, and replays every TU of the library target | a gate that must catch source-body changes, or a release baseline that wants per-symbol impact |

**The one rule that ties it together:** the binary diff (`binary`/`headers`) sets
the pass/fail **gate**; `build`/`source` mostly *localize and explain* and
add their own source-/API-level findings — they rarely flip the verdict. So spend
on L4 (`source`) for humans reviewing a PR or a release, and stay in the
cheap tier for a fast CI gate.

### Example-catalog status

The per-`--depth` eval-target count, correct-verdict coverage, and FP/FN
counts are a volatile, machine-checkable fact with one owner: [Tool
Comparison's "Current scan-quality
snapshot"](../reference/tool-comparison.md#current-scan-quality-snapshot).
Don't re-add a specific target count or per-depth percentage table here — that
page already records whether the matrix has been re-run against the current
catalog, and a second copy here is exactly how this page's own numbers
previously went stale (fixed targets/percentages pinned to an older, smaller
catalog, silently presented as current). Qualitatively, the shape is stable
across catalog growth: `binary` is the fast artifact gate that intentionally
misses header/source-only breaks; `headers` is the best low-cost CI gate once
public headers are available, since it can see the public/private boundary;
`build` adds build-context corroboration on top; `source` has the highest
recall, since source-smoke proofs cover consumer-only API hazards the artifact
tiers can't see. (The measured matrix at the fact owner above found zero extra
false positives at `headers`/`build` depth — a real, but catalog- and
run-specific, result, not a guarantee this qualitative description promises on
its own.) [Evidence &
Detectability](../learn/evidence-and-detectability.md#what-each-layer-buys-fewer-false-negatives-and-fewer-false-positives)
explains what each added layer buys. Bundle-component results are structural
diagnostics only in that matrix; only the dedicated bundle lane scores the
single canonical case-level verdict and proves findings such as dangling
intra-bundle imports and provider drift.

## What input each depth needs — and how to get it

Every depth needs a specific **input**; without it the matching coverage row is
`not_collected` (the run never silently pretends it collected it). Pick the row
that matches your goal, then supply the input named in column 3.

| Goal (use case) | `--depth` | Input you must provide | How to obtain it | If the input is missing |
|---|---|---|---|---|
| Binary-only ABI gate (removed/changed exports; no-DWARF vtable/RTTI size) | `binary` | two `.so` (or `.abi.json`) | release artifacts / conda / `.deb` | always available (L0/L1) |
| Header-aware API surface + internal-vs-public scoping | `headers` | a public-header **directory** + a C/C++ frontend | `-H include/`; `castxml` **or** `clang` on `PATH` | a lone `-H file.h` does not establish a boundary → public-surface scoping stays dormant |
| Build-flag / toolchain / visibility drift (+ macro/include divergence) | `build` | an L3 compile database | `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` (configure-only), `meson setup`, `bazel aquery --output=jsonproto`, or `bear -- make`; pass via `--build-info` | L3 `not_collected`; the report states the exact remedy |
| Semantic source-ABI replay (macro/default-arg/inline/template/constexpr **body** changes) + L5 graph | `source` | L3 compile DB + source checkout + `clang` + generated headers present | configure for the DB; **codegen/partial build** for generated headers | missing generated headers → L4 `partial` |

### Obtaining a compile database without a full build

The L3+ depths need a `compile_commands.json`; a pristine checkout has none.
Generate one — none of these compiles the library, they only configure / query
the build graph:

```bash
# CMake: configure-only
cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
abicheck dump build/libfoo.so -H include/ --sources . --build-info build \
  --depth source -o artifacts/libfoo.abi.json

# Bazel: query the action graph (no build); --build-info sniffs the aquery
# jsonproto and routes it straight to the Bazel adapter (ADR-037 D5 — no pack step)
bazel aquery 'mnemonic("CppCompile", //...)' --output=jsonproto > aq.json
abicheck dump bazel-bin/libonedal_core.so -H include/ --build-info aq.json --depth build
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
auto-querying (it is now a deprecated no-op): asking for a source-level run *is*
the request to collect build evidence.

Make is queried with a fixed dry-run command (`make -B -n -k -w`) and the transcript
is scraped as reduced-confidence L3 evidence. This lets Make/EPICS-style projects
work without a manual `compile_commands.json`; a real compile DB (for example
from `bear -- make`, then `--build-info compile_commands.json`) is still preferred
when available.

Only an abicheck-constructed command runs automatically. An *arbitrary*
`build.query` command runs only when it is operator-supplied — an explicit
`--config` (the project `.abicheck.yml` contract), which is the only thing that
can authorize it: there is no CLI flag for a query.
An auto-discovered `.abicheck.yml` sitting inside the `--sources` tree is never
trusted to execute its `build.query` (it may be attacker-controlled); its
non-executing settings are still honoured. Pre-generating and passing a
`--build-info` yourself remains supported as an advanced option.

```yaml
# .abicheck.yml
build:
  query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
```

```bash
abicheck compare old/libfoo.abi.json new/libfoo.so -H include/ --sources . \
  --config .abicheck.yml --depth source
```

## Compile context for header parsing (L2)

The L2 header AST is what establishes the **public/internal boundary** — which
declarations are API, so public-surface scoping can tell an *internal* symbol
removal (compatible) from a public one (breaking). To build it, the frontend
must parse your public headers the way your compiler does: it needs the include
roots they `#include`, the C++ standard they assume, and any `-D` feature macros
that gate declarations. When that context is missing the header parse fails, the
analysis falls back to a binary-strict scope, and internal removals get reported
as BREAKING.

`dump` and `compare` take the **same** compile-context flags (they share one
definition, so they never drift):

| Flag | Purpose |
|---|---|
| `--ast-frontend {auto,castxml,clang,hybrid}` | which frontend parses the headers (env `ABICHECK_AST_FRONTEND`); `hybrid` runs castxml and clang together |
| `-I/--include DIR` | an include root your headers need (repeatable) |
| `--compiler-option TOK` | one extra compiler flag verbatim (repeatable), e.g. `--compiler-option -std=c++20 --compiler-option -DFOO=1` |
| `--compiler` / `--compiler-prefix` | a cross-compiler / cross-toolchain prefix |
| `--sysroot DIR` | an alternate system root |
| `--nostdinc` | do not search system includes (and disable the auto-probe below) |

### Where each setting belongs (CLI vs config)

Four layers resolve the context, **highest precedence first**:

1. **Explicit CLI flag** — a per-run override (`--compiler-option`, `--sysroot`, …).
2. **`.abicheck.yml` `compile:` block** — your project's stable contract,
   reviewed in PRs (see below). Put include roots, `std`, and `defines` here so
   every CI run is reproducible without re-typing them.
3. **Compile-DB-derived flags** — *planned*: per-TU `-I`/`-std`/`-D` taken from a
   `--build-info`. Today the compile DB feeds L3–L5 only.
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
a bare `-H include/` finds libstdc++ without extra flags. Disable it with
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
      cross-compiles (use `--compiler-prefix`/`--sysroot` or the config block) and
      makes results host-dependent (pin context in config for reproducible CI).

## Worked examples

Each example shows the command, what depth it pins, and what to read in the
output. Every report ends with an analysis-confidence / coverage block — always
read it before trusting the verdict (see
[Reading the coverage block](#reading-the-coverage-block)).

### PR gate — `source` against a stored baseline

The common CI case: gate a PR by comparing the just-built library against the
baseline snapshot published from `main`.

```bash
abicheck compare artifacts/libfoo-main.abi.json build/libfoo.so \
  -H include/ \
  --sources . --depth source
```

- **Depth:** pinned to `source`, so a missing compile DB / `clang` is an error
  rather than a silently shallower run.
- **Exit code:** `0` compatible, `2` source/API break, `4` ABI break.
- `--depth source` folds the L5 reachability graph for cross-symbol impact in
  the report.

### Single-build audit — `dump`

There is no two-version comparison to run when you only have one build. Dump it
instead: `abicheck dump` records the same L0–L5 facts a comparison would consume
— the export table, the public-header AST and its public/internal boundary, the
build context, and (at `--depth source`) the replay and reachability graph — as
a structured snapshot you can inspect, diff by hand, or keep as the baseline the
*next* release is compared against.

```bash
abicheck dump libfoo.so -H include/ -o libfoo.abi.json
```

Add the deeper evidence when you have it:

```bash
abicheck dump libfoo.so -H include/ \
  --build-info build/compile_commands.json --depth build \
  -o libfoo.abi.json
```

!!! note "Intra-version hygiene checks have no CLI front end"
    The single-build hygiene/cross-source checks (accidental exports,
    private-header leaks, unversioned exports, RTTI for internal types,
    header-vs-build context mismatch, ODR type variants) were only ever exposed
    by the `scan` command, which has been removed. The engine still computes
    them — `abicheck.buildsource.crosscheck.run_crosschecks()` over a dumped
    snapshot — but there is no command that prints them, and no `dump`/`compare`
    equivalent. See the catalog's audit cases for what each check finds:
    [case143](../reference/examples/case143_audit_accidental_export.md),
    [case144](../reference/examples/case144_audit_private_header_leak.md),
    [case145](../reference/examples/case145_audit_unversioned_export.md),
    [case146](../reference/examples/case146_audit_rtti_for_internal.md),
    [case148](../reference/examples/case148_xcheck_header_build_mismatch.md),
    [case149](../reference/examples/case149_xcheck_odr_variant.md),
    [case150](../reference/examples/case150_xcheck_export_public_pair.md),
    [case151](../reference/examples/case151_xcheck_provider_matrix.md).

### Cheap gate — no compiler, no sources

When you only have the two binaries (or want a fast pre-check), pin a cheap
depth. `--depth build` adds build-flag/toolchain drift, but only when you also
give it a build input to read — a compile DB or build dir via `--build-info` (or
a `--sources` tree); without one, L3 is reported `not_collected` and no drift is
checked. `--depth binary` stays on the exported-symbol surface (L0) plus cheap
debug-info *presence* — it skips the deep DWARF type walk, so no compiler,
headers, or sources are needed. (`--depth headers` is the next rung up: it adds
the L2 header AST, which needs a header directory via `-H`/`--header` and a
C/C++ frontend on `PATH`.)

```bash
# build-flag drift only, flat ~0.3–0.5s regardless of project size
# (the compile DB is what supplies L3 — without it the run is artifact-only)
abicheck compare old/libfoo.abi.json new/libfoo.so \
  --build-info build/compile_commands.json --depth build

# exported symbols only (no DWARF walk, no L2 AST, no L3/L4/L5; no compiler needed)
abicheck compare old/libfoo.abi.json new/libfoo.so --depth binary
```

### Estimate before you spend — `--dry-run`

L4 cost scales with C++ template depth, so on a heavy library project the per-TU
replay cost first. `--dry-run` (on `dump` and `compare` alike) resolves and
validates the invocation — depth, scope, tool availability — and prints what it
resolved without analysing anything or writing output. Exits 0 for a resolvable
preview; an invalid invocation or an unsatisfiable requested depth still exits
nonzero, the same as the real run would.

```bash
abicheck dump libfoo.so --sources . --depth source --dry-run
```

### Release baseline — `dump --depth source`

The reusable comparison target is a **`dump`-produced snapshot**, not a rendered
report: a report cannot be fed back as a comparison input. Pass `--sources` to
embed all of the L3/L4/L5 facts so the later PR comparison carries them:

```bash
# Produce the reusable baseline snapshot once per release
abicheck dump build/libfoo.so -H include/ \
  --sources . --depth source --version 1.0 -o artifacts/libfoo-1.0.abi.json

# PR runs then compare against it:
abicheck compare artifacts/libfoo-1.0.abi.json build/libfoo.so \
  -H include/ --sources . --depth source
```

### Reading the coverage block

`--depth` requests a level but `L` is *evidence*, so a run can request a deep
level and only reach a shallow one (clang missing, no sources, a parse error).
abicheck never reports that as "failed" — the report's **Analysis Confidence**
block states the evidence tier it actually reached and, for each disabled
detector, the input that is missing:

```text
| Confidence     | HIGH                                                     |
| Evidence tier  | header_aware                                             |
| Coverage gap   | Detector 'advanced_dwarf' disabled: missing DWARF …      |
```

Each coverage-gap line names the precise input to add. See
[Build Info & Sources § Evidence coverage](../learn/build-source-data.md#evidence-coverage)
for the full coverage and capability report.

## Cost guide (rules of thumb)

Measured on two UXL libraries (full data: `validation/`):

| Tier | Depths | Relative cost |
|------|--------|---------------|
| **Cheap** | `binary`, `headers`, `build` | One price — dominated by the binary dump, *not* the source layer. |
| **Expensive** | `source` | clang per-TU AST replay (L4). |

- **The cliff is at L4 (`build`→`source`), and its height tracks C++ complexity.**
  L4 cost scales with template/STL instantiation depth, not `.so`/TU count — a
  heavy-C++ library can be ~7× slower at `source` than `build`, while a plain-C
  library is barely affected (~1.3×).
- **Choose a cheap depth by coverage, not cost.** `binary` (symbols only);
  `headers` adds the L2 API surface; `build` adds L3 build context.
- **`source` replays every TU of the library target**, so it costs the same as
  an amortized release baseline. Produce that baseline once with
  `dump --depth source` and reuse it, rather than paying the replay on both
  sides of every comparison.
- **The verdict usually does not change with depth** — the binary diff sets the
  gate; L3–L5 add localization/explanation. For a pass/fail **gate**, the cheap
  tier is enough; spend on L4 (`source`) when you want source-body semantics or
  per-symbol localization for humans.

See [Comparison Performance](../contribute/performance.md#depth-cost-model-one-cliff-at-l4)
for the measured numbers.
