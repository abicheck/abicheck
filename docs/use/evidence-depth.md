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

`--depth` is one dial on the two commands that collect evidence — **`compare`
and `dump`** — selecting how deep the collection goes (binary → headers →
build → source). `abicheck compare OLD NEW` is the way to run a
depth-pinned, source-aware comparison: it runs the always-on compiler-free
pattern pre-scan and every cross-source check
(`CROSS_SOURCE_EVOLUTION_CHECKS`) automatically on every invocation, and
takes the `--depth`/`--since`/`--changed-path`/`--sources`/`--build-info`
inputs directly. `abicheck dump INPUT --depth …` pins the same dial when you
are capturing a reusable snapshot instead of comparing.

For a single-build audit, use `abicheck compare --no-baseline CANDIDATE`.
Configure individual findings with `policy.overrides` in `.abicheck.yml`.
To gate the audit, select a `--severity-preset` other than `info-only`.
For build-target selection, set `build.targets` in the configuration file.

!!! info "This topic in three pages — you are on **Flags**"
    **Model** — [Evidence & Detectability](../learn/evidence-and-detectability.md):
    the `L0`–`L5` evidence layers, what each can and cannot see, and the
    `--depth` dial that collects them. Read it first if the dial and the layers
    look like they overlap.
    **Worked example** — [What Each Level Sees](../learn/what-each-level-sees.md):
    one tiny library walked up every level, with the actual data.
    **Flags** — this page: the practical flag reference and the
    [worked examples](#worked-examples) below.

**One dial selects how deep it goes — `--depth`, named by the evidence you get:**

- **`--depth binary|headers|build|source`** — the single knob. `binary` = L0/L1 exported symbols + binary metadata; `headers` =
  +L2 header AST; `build` = +L3 build context; `source` = +L4 replay & the L5
  graph. On both `compare` and `dump`, omitting `--depth` is **not** a fixed
  `headers` default: each command infers the deepest rung its other inputs
  already justify. With no `--sources`/`--build-info` at all, that inference
  bottoms out at `headers` (nothing deeper to collect) — but supply
  `--sources`/`--build-info` with no `--depth`, and `compare` infers
  `source`/`build` from whichever was given, while `dump` goes further still
  and always resolves to `source-target` internally, collecting everything
  the supplied evidence reaches. Pin `--depth` explicitly whenever you want a
  specific rung regardless of what other inputs are present, rather than
  relying on this inference.
- **When `--depth source` actually replays source, it always analyses
  *something* real, never a zero-TU no-op**: with a
  `--since`/`--changed-path` seed it replays the *changed* TUs; without one
  it replays the **whole current library target** (what an older,
  now-removed `--depth full` rung used to require explicitly) — so a replay
  that runs is never silently empty, just potentially more expensive
  unseeded. This guarantee is about the replay *itself*, not about whether
  the replay runs at all: with no usable `--sources`/`--build-info` at all,
  `--depth source` never reaches L4 replay in the first place — a *pinned*
  `--depth source` in that state fails loud (see the warning above), never
  silently exiting on a shallow verdict.
- A single-build audit uses `compare --no-baseline CANDIDATE`. The same
  evidence requirement applies when you pin a build or source depth.

!!! warning "When the requested build or source depth cannot be reached"
    `dump` exits with code 1 and writes no snapshot. A live `compare`
    writes its report and exits with code 7. Check `exit.reasons` for
    `evidence_contract_error`. The compatibility verdict still describes
    the changes the comparison could establish. See [Exit Codes](../reference/exit-codes.md).

    **This floor applies to `compare` only when at least one side is a live
    extraction** (a binary/package operand, not a pre-existing
    `.abi.json`/`.abi.snapshot`). Comparing **two already-serialized
    snapshots** is exempted even when neither embeds L3/L4 evidence — a
    pinned `--depth source` over `compare v1.abi.json v1.abi.json` reports
    whatever the snapshots actually carry and exits `0`/`2`/`4` normally,
    it does **not** exit `7`. A clean result from a both-snapshot
    `compare` is therefore not proof the pinned depth was actually
    reached — only a run with at least one live side, or an explicit look
    at each snapshot's own recorded depth, tells you that.
      identical axis.

    `compare --dry-run` does not preview this failure, though: pinning an
    unsatisfiable depth under `--dry-run` still exits `0` and reports `0
    TU(s)` for the affected layers — see [Estimate before you
    spend](#estimate-before-you-spend-dry-run) below.

!!! note "`--mode`/`--source-method` are gone"
    Earlier releases exposed a precise `--source-method s0…s6` axis and
    `--mode pr|pr-deep|baseline|audit` presets as deprecated aliases for
    `--depth`. Both have since been **removed outright** (passing either is now
    a plain usage error, exit 64) — use `--depth`. (`--depth symbols` was
    likewise renamed to `--depth binary`, with no alias kept.)

## Headers and includes — one side or both

`-H/--header [old=|new=]PATH` and `-I/--include [old=|new=]PATH` are
repeatable and side-aware on `compare`. A bare path applies to **both**
sides; prefix it with `old=`/`new=` to scope it to one, e.g.
`--header old=old/include --header new=new/include`. `dump` takes the same
flags without the prefix, since it has only one side.

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
| `binary` | L0 exported symbols + binary metadata + **L1** debug info (DWARF/PDB/BTF/CTF types and layouts) *when the binary carries it* (no L2 AST) + always-on pattern scan | just the artifact(s) |
| `headers` | + **L2** header AST (the public/internal boundary) | a public-header directory + a C/C++ frontend |
| `build` | + **L3** build context (flag/toolchain drift) | a compile DB / build dir |
| `source` | + **L4** source-ABI replay of changed TUs (seeded) or the whole library target (unseeded) + the **L5** graph | sources **and** `clang` (+ a diff seed to scope it to just the changed TUs) |

### What each depth does, in plain terms

- **`binary` — the always-available floor.** Compares the two binaries' exported
  symbols, SONAME, and dependencies, and runs a compiler-free pattern pre-scan.
  Needs only the two artifacts — no source, no build, no compiler. It is the
  deliberate way to **opt out** of source analysis: a fast gate, or when no
  sources/compile DB are available. It skips the L2 header AST, but it does
  **not** ignore debug info: when a binary carries DWARF (or PDB/BTF/CTF), its
  types and layouts are read and compared, so a struct size or field-offset
  change is still reported at this rung. With no debug info it is the
  exported-symbol surface alone.
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
  (never a zero-TU no-op, but potentially as expensive as a full
  release-baseline replay). Needs a compile DB and the source checkout
  (`--sources`).

### Benefits and cost at a glance

Each rung *adds* to the one below it — the benefit column is what that rung newly
catches, the cost/implication column is what it asks of you in return.

| `--depth` | What it newly catches (benefit) | Cost & implication | Pin it when |
|-----------|--------------------------------|--------------------|-------------|
| `binary` | removed/changed exports, SONAME, dependency & version changes, no-DWARF vtable/RTTI size shifts, and — when the binaries carry DWARF — struct/class layout changes | cheapest, flat with project size; **no** source-only API changes, and every exported symbol is treated as ABI (public/internal churn not separated) | you only have the two binaries, or want a fast pre-check |
| `headers` | the **public/internal boundary** → separates real API breaks from internal churn; signature / type-layout / enum / `noexcept` changes | still cheap; needs public headers **and** a C/C++ frontend on `PATH`, else it falls back to binary-strict scope and over-reports | you have the public headers — this is the floor for a *trustworthy* verdict |
| `build` | build-flag / toolchain / `-std` / visibility **drift**; macro-value & include-graph divergence | cheap (~0.3–0.5s more); needs a compile DB / build dir — without one L3 is `not_collected` (reported, not a pass) | the two builds may differ in flags, standard, or visibility |
| `source` | inline / template / macro / default-argument / `constexpr` **body** changes, **plus** the L5 reachability graph that localizes and scopes findings | **the one cost cliff (L4)** — scales with C++ template depth; needs `--sources` + `clang` + a `--since` seed to stay cheap (unseeded, it replays every TU — the same cost as an amortized whole-library replay) | a per-PR gate that must catch source-body changes or wants per-symbol impact; unseeded, the same rung also serves as the whole-library replay for producing an amortized release baseline |

**The one rule that ties it together:** the binary diff (`binary`/`headers`) sets
the pass/fail **gate**; `build`/`source` mostly *localize and explain* and
add their own source-/API-level findings — they rarely flip the verdict. So spend
on L4 (`source`) for humans reviewing a PR or a release, and stay in the
cheap tier for a fast CI gate.

### Example-catalog status

The per-`--depth` eval-target count, correct-verdict coverage, and FP/FN
counts are a volatile, machine-checkable fact with one owner: [Tool
Comparison's "Current scan-quality
snapshot"](../reference/tool-comparison.md#current-scan-quality-snapshot)
(the "Scan-depth matrix" row). Don't re-add a specific target count or
per-depth percentage table here — that page already records whether the
matrix has been re-run against the current catalog, and a second copy here
is exactly how this page's own numbers previously went stale (fixed
targets/percentages pinned to an older, smaller catalog, silently
presented as current). Qualitatively, the shape is stable across catalog
growth: `binary` is the fast artifact gate that intentionally misses
header/source-only breaks; `headers` is the best low-cost CI gate once
public headers are available, since it can see the public/private
boundary; `build` adds build-context corroboration on top; `source` has
the highest recall, since source-smoke proofs cover consumer-only API
hazards the artifact tiers can't see. (The measured matrix at the fact
owner above found zero extra false positives at `headers`/`build` depth —
a real, but catalog- and run-specific, result, not a guarantee this
qualitative description promises on its own.) `source` here is the
diff-seeded rung; [Evidence &
Detectability](../learn/evidence-and-detectability.md#what-each-layer-buys-fewer-false-negatives-and-fewer-false-positives)
explains why an unseeded whole-library replay (the former `full` rung) is
treated as reaching the same verdict signal at higher cost. Bundle-component
results are structural diagnostics only in that matrix; only the dedicated
bundle lane scores the single canonical case-level verdict and proves
findings such as dangling intra-bundle imports and provider drift.

## What input each depth needs, and how to get it

Every depth needs a specific **input**; without it the matching coverage row is
`not_collected` (the scan never silently pretends it ran). Pick the row that
matches your goal, then supply the input named in column 3.

| Goal (use case) | `--depth` | Input you must provide | How to obtain it | If the input is missing |
|---|---|---|---|---|
| Binary-only ABI gate (removed/changed exports; no-DWARF vtable/RTTI size) | `binary` | two `.so` (or `.abi.json`) | release artifacts / conda / `.deb` | always available (L0/L1) |
| Header-aware API surface + internal-vs-public scoping + cross-source checks | `headers` | a public-header **file or directory** + a C/C++ frontend | `-H include/` or `-H include/foo.h` on `compare`/`dump`; `castxml` **or** `clang` on `PATH` | with no `-H` at all, there is no public-header set → provenance/cross-checks stay dormant |
| Build-flag / toolchain / visibility drift (+ macro/include divergence) | `build` | an L3 compile database | `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` (configure-only), `meson setup`, `bazel aquery --output=jsonproto`, or `bear -- make`; pass via `--build-info` | L3 `not_collected`; the scan advises the exact remedy |
| Semantic source-ABI replay of changed TUs (macro/default-arg/inline/template/constexpr **body** changes) + L5 graph | `source` | L3 compile DB + source checkout + `clang` + generated headers present | configure for the DB; **codegen/partial build** for generated headers; seed with `--since`/`--changed-path` | without a seed, `source` replays the **whole current library target** instead of just the changed TUs (never a zero-TU no-op, but more expensive); missing generated headers → L4 `partial` |
| Full-library source replay (an amortized release baseline) | `source` (unseeded — no `--since`/`--changed-path`) | as above, whole library | amortized baseline build | expensive — the one cost cliff is at L4 |
| Single-build hygiene lint (accidental exports, leaks, unversioned/RTTI) | any depth, no `--against` | binary + public-header **dir** (+ optional L3/L4) | as above | `header_build_context_mismatch` needs L3; `odr_type_variant` needs L4 |

### Obtaining a compile database without a full build

The L3+ depths need a `compile_commands.json`; a pristine checkout has none.
Generate one — none of these compiles the library, they only configure / query
the build graph:

```bash
# CMake: configure-only (source also needs --sources . and a diff seed --since)
cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
abicheck compare old/libfoo.abi.json new/libfoo.so -H include/ \
  --build-info new=build --sources new=. --since origin/main --depth source

# Bazel: query the action graph (no build); --build-info sniffs the aquery
# jsonproto and routes it straight to the Bazel adapter (no pack step)
bazel aquery 'mnemonic("CppCompile", //...)' --output=jsonproto > aq.json
abicheck compare old/libonedal_core.abi.json new/libonedal_core.so -H include/ \
  --build-info new=aq.json --depth build
```

!!! warning "A stored baseline needs matching evidence on both sides"
    Every example on this page that compares a stored `.abi.json` baseline
    against a live NEW build and supplies build/source evidence for NEW
    alone (the CMake and Bazel commands above, and the `.abicheck.yml`
    `build.query` example below) only works when that stored baseline was
    itself `dump`ped with matching `--sources`/`--build-info` at bake time.
    A baseline with no embedded L3/L4 facts paired with a NEW side that has
    them gives the two sides different extraction profiles, which can stop
    the run with exit `16` `NOT_COMPARABLE` instead of performing the
    advertised comparison. Either bake the same evidence into the baseline
    at `dump` time, or pass matching `--sources old=`/`--build-info old=`
    alongside the `new=` one shown.

!!! tip "`--build-info` auto-detects the format"
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
No `--allow-build-query` flag is needed for `--sources`-driven auto-querying —
that flag was always a no-op and has since been removed outright: asking for
a source-level scan *is* the request to collect build evidence.

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
abicheck compare old/libfoo.abi.json new/libfoo.so -H include/ --sources new=. \
  --config .abicheck.yml --depth source
```

## Compile context for header parsing (L2)

The L2 header AST is what establishes the **public/internal boundary** — which
declarations are API, so the cross-source checks and public-surface scoping can
tell an *internal* symbol removal (compatible) from a public one (breaking). To
build it, the frontend must parse your public headers the way your compiler does:
it needs the include roots they `#include`, the C++ standard they assume, and any
`-D` feature macros that gate declarations. When that context is missing the
header parse fails, the run falls back to a binary-strict scope, and internal
removals get reported as BREAKING.

**On `compare` and `dump` the compile context is a `.abicheck.yml` property,
not a flag** — the toolchain a project's headers parse under is a stable
property of the project, not a per-run decision. Both commands take exactly two per-run compile-context inputs on the
CLI: `-I/--include DIR` (an include root, repeatable — searched before the
configured `include_dirs`) and `-D NAME[=VALUE]` (a preprocessor
macro, repeatable — merged with `defines` by macro name, the CLI value winning).
Everything else lives in the `compile:` block:

| `compile:` key | Purpose |
|---|---|
| `frontend` | which frontend parses the headers — `auto`/`castxml`/`clang`/`hybrid` (env `ABICHECK_AST_FRONTEND`); `hybrid` runs castxml and clang together |
| `include_dirs` | include roots your headers need |
| `std` | the C++ standard the headers assume, e.g. `c++20` |
| `defines` | `-D` feature macros that gate declarations |
| `options` | extra compiler flags, verbatim |
| `lang` | force `c` or `c++` header parsing |
| `compiler` | a cross-compiler / cross-toolchain prefix |
| `sysroot` | an alternate system root |
| `nostdinc` | do not search system includes (and disable the auto-probe below) |
| `frontend_context`, `ast_frontend_fallback`, `allow_unsupported_castxml` | frontend-selection escape hatches |

These are config-only: the general compiler/frontend CLI family
(`--ast-frontend`, `--compiler-option`, `--compiler`/`--compiler-prefix`,
`--sysroot`, `--nostdinc`, `--lang`) was removed, and the retired `scan`
command took the last of those spellings with it. The one exception is
`defines`, which also has a per-invocation `-D NAME[=VALUE]` on
`dump`/`compare`, merged with this key by macro name. The full key
reference is
[Config Keys](../reference/config-keys-reference.md); the `compile:` block's
own semantics are in [Config File](../reference/config-file.md).

### Where each setting belongs (CLI vs config)

Three layers resolve the context, **highest precedence first**:

1. **`.abicheck.yml` `compile:` block** — your project's stable contract,
   reviewed in PRs (see below). Put the frontend, include roots, `std`, and
   `defines` here so every CI run is reproducible without re-typing them.
2. **Compile-DB-derived flags** — *planned*: per-TU `-I`/`-std`/`-D` taken from a
   `--build-info`. Today the compile DB feeds L3–L5 only.
3. **Auto-detected system includes** — the default floor (below).

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
a bare `compare … -H include/` finds libstdc++ without extra configuration.
Disable it with `compile.nostdinc: true`, an explicit `compile.sysroot`, or
`ABICHECK_AUTO_SYSTEM_INCLUDES=0`.

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
      cross-compiles (set `compile.compiler`/`compile.sysroot`) and
      makes results host-dependent (pin context in config for reproducible CI).

## Worked examples

Each example shows the command, what depth it pins, and what to read in the
output. Every run ends with a coverage block — always read
it before trusting the verdict (see [Reading the coverage
block](#reading-the-coverage-block)).

### PR gate (the default) — diff-seeded `source`

The common CI case: gate a PR by comparing the just-built library against the
baseline from `main`, scoping the expensive L4 replay to the files the PR
touched. The `--since` seed is what keeps this cheaper than an unseeded,
whole-library `source` compare — without it, `source` replays every TU.

```bash
abicheck compare artifacts/libfoo-main.abi.json build/libfoo.so \
  -H include/ \
  --sources new=. --since origin/main --depth source
```

- **Depth:** pinned explicitly here (`--depth source`). Omitting `--depth`
  never picks a rung by risk on either command any more, but with
  `--sources new=.` already given (as above) `compare` would still infer
  `source-target` from that input rather than `headers`; the pin exists for
  reproducibility, not because omitting it would fall back to `headers`
  here.
- If the comparison exceeds its time budget, it exits with code 5.
  See [Exit Codes](../reference/exit-codes.md).
- `--depth source` folds the L5 reachability **edges scoped to the changed TUs**
  for cross-symbol impact in the report. The *whole-library* reachability graph
  is an internal level (`GRAPH`, D6) with no user-facing `--depth` rung.

### Single-build audit — no baseline

`abicheck compare --no-baseline CANDIDATE` runs the
intra-version cross-source hygiene checks against **one** build — no
previous version required. With just the binary and headers it catches
accidental exports, private-header leaks, and unversioned symbols:

```bash
abicheck compare --no-baseline libfoo.so -H include/
```

The findings land under `findings[]` — `changes[]` stays empty and `verdict`
stays `null`, because an audit reports no addition, removal, or
compatibility verdict.

Supply `--sources` or `--build-info` to run checks that need build or
source evidence. By default, audit findings are informational. To gate
on findings, use `--severity-preset default` or `strict`. An audit gate
failure contributes exit code 3. See
[Scenario S5](../integration/scenarios/single-build-audit.md) for the full
CLI account.

Worked example cases for each audit finding:
[case143](../reference/examples/case143_audit_accidental_export.md) (`exported_not_public`),
[case144](../reference/examples/case144_audit_private_header_leak.md) (`private_header_leak`),
[case145](../reference/examples/case145_audit_unversioned_export.md) (`unversioned_exported_symbol`),
[case146](../reference/examples/case146_audit_rtti_for_internal.md) (`rtti_for_internal_type`).
[case150](../reference/examples/case150_xcheck_export_public_pair.md) shows the
bidirectional `exported_not_public` ↔ `public_not_exported` pair, and
[case151](../reference/examples/case151_xcheck_provider_matrix.md) shows confidence growing
with the number of corroborating sources (the provider-agreement matrix).

Some audit checks need more evidence than the artifact tiers provide:
`header_build_context_mismatch` compares the headers' parse context against the
real build flags, so it only fires when you also pass an L3 build input
(`--build-info` or `--sources`) — without one it is reported as a
skipped coverage row, not a pass:

```bash
abicheck compare --no-baseline libfoo.so -H include/ \
  --build-info build/compile_commands.json
```

This reports the full cross-source / single-release finding set
(`CROSS_SOURCE_EVOLUTION_CHECKS`) rather than a two-version diff. The
flagship cross-source cases —
[case148](../reference/examples/case148_xcheck_header_build_mismatch.md)
(`header_build_context_mismatch`, L2 macros ↔ L3 flags) and
[case149](../reference/examples/case149_xcheck_odr_variant.md) (`odr_type_variant`, L4
layout ↔ layout) — are findings that are invisible or ambiguous to any single
source and resolve only by crosschecking two.

### Cheap gate — no compiler, no sources

When you only have the two binaries (or want a fast pre-check), pin a cheap
depth. `--depth build` adds build-flag/toolchain drift, but only when you also
give it a build input to read — a compile DB or build dir via
`--build-info` (or a `--sources` tree); without one, L3 is
reported `not_collected` and no drift is checked. `--depth binary` stays on the
exported-symbol surface (L0), plus the debug info (L1) a binary already carries,
and the always-on pattern scan — it never runs a header frontend, so no
compiler, headers, or sources are needed. (`--depth headers` is the next rung up: it adds the L2 header
AST, which needs a header directory via `-H`/`--header` and a C/C++ frontend on
`PATH`.)

```bash
# build-flag drift only, flat ~0.3–0.5s regardless of project size
# (the compile DB is what supplies L3 — without it the comparison is artifact-only)
abicheck compare old/libfoo.abi.json new/libfoo.so \
  --build-info new=build/compile_commands.json --depth build

# exported symbols + any DWARF the binaries carry + always-on lexical scan
# (no L2 AST, no L3/L4/L5; no compiler needed)
abicheck compare old/libfoo.abi.json new/libfoo.so --depth binary
```

### Estimate before you spend — `--dry-run`

L4 cost scales with C++ template depth, so on a heavy library, preview the per-TU
replay cost first. `--dry-run` resolves and validates the invocation (depth,
scope, tool availability) and prints the projected per-layer cost for *this*
project without comparing anything or writing output. A two-sided `compare --dry-run --depth
source` with no build/source evidence resolvable still exits `0` and simply
reports `0 TU(s)` for the L3/L4/L5 rows, where the equivalent real run would
now exit `7`. Don't trust a clean `--dry-run` as proof the real run will
succeed on a pinned deep depth.

```bash
abicheck compare old.abi.json libfoo.so --sources new=. --depth source --dry-run
```

`compare --no-baseline CANDIDATE --sources . --depth source --dry-run`
previews an *audit-only* run, and unlike two-sided `compare --dry-run` it
**does** preview its own real run's floor: against a live candidate, a
pinned `--depth build`/`--depth source` with no `--sources`/`--build-info`
resolvable blocks the preview rather than reporting a clean plan (see
[Scenario S5](../integration/scenarios/single-build-audit.md)).

### Release baseline — unseeded `source`

The reusable target that PR comparisons compare against is a
**`dump`-produced snapshot**. Pass `--sources` to embed all of the L3/L4/L5
facts so the later PR compare carries them:

```bash
# Produce the reusable baseline snapshot once per release
# (dump uses -H/--header, same as compare):
abicheck dump build/libfoo.so -H include/ \
  --sources . --label 1.0 -o artifacts/libfoo-1.0.abi.json

# PR compares then run against it:
abicheck compare artifacts/libfoo-1.0.abi.json build/libfoo.so -H include/ \
  --sources new=. --since origin/main --depth source
```

To get a whole-library comparison *report* of a release (replays every TU,
folds the full graph) for human review — as opposed to the reusable baseline
above — run `compare --depth source` **without** a `--since`/`--changed-path`
seed (which resolves to the whole current library target — what
a now-removed `--depth full` rung used to require explicitly) and send its
report to `-o`:

```bash
abicheck compare artifacts/libfoo-1.0.abi.json build/libfoo.so -H include/ \
  --sources new=. --depth source -o json=artifacts/libfoo-1.0-report.json
```

### Omitting `--depth` — `auto`, and why it is not risk-driven any more

With `compare`, supplying source or build inputs selects the corresponding
depth automatically. Pin the depth in CI when missing inputs should fail
the run. For example:

```bash
abicheck compare old.so new.so -H include/ --sources new=. --depth source --since origin/main
```

The `--since`/`--changed-path` seed still matters, on both commands, for a
different axis: it scopes a `--depth source` replay to the changed TUs
instead of the whole current library target.

### Reading the coverage block

`--depth` requests a level but `L` is *evidence*, so a run can request a deep
level and only reach a shallow one (clang missing, no sources, a parse error).
On `compare` **with no `--depth` pinned**, this is never reported as
"failed" — the run states the depth it **actually reached** and, for each
disabled check, the input or tool to add. (This best-effort behavior does
not apply once `--depth build|source` is *pinned* and the evidence can't
reach it: live `compare` exits `7` and `dump`
raises `DumpDepthNotSatisfiedError` and exits `1` with no snapshot written;
see the warning above. `compare` is the one exception that still prints a
coverage block alongside its exit `7`, since it writes a report either way.)

```text
Checks enabled for this scan (and why others are not):
  [on]  Symbol presence & linkage … — from the binary's dynamic symbol table
  [on]  Build-flag & toolchain drift … — from build-system data
  [off] Macros, default args, inline/template/constexpr bodies — no sources/clang:
        source-only API changes are not detected
```

An `[off]` line is the precise input to add (here: install clang and pass
`--sources`). See
[Build Info & Sources § Evidence coverage](../learn/build-source-data.md#evidence-coverage)
for the full coverage and capability report.
[case147](../reference/examples/case147_scan_depth_ladder.md) is the legibility anchor:
the *same* input scanned at `--depth headers` (pattern + AST), then deeper, with
the coverage block showing exactly what each depth proved and what it could not.

## Cost guide (rules of thumb)

Measured on two UXL libraries (full data: `skills-src/evaluation/validation/`):

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
  current library target** instead of just the changed TUs (never a zero-TU no-op, but the most expensive shape, same cost as an amortized release
  baseline). With a real PR diff, `source` scopes L4 to the touched TUs and can be
  **an order of magnitude faster** for the identical verdict. Always pass
  `--since`/`--changed-path` in PR CI.
- **The verdict usually does not change with depth** — the binary diff sets the
  gate; L3–L5 add localization/explanation. For a pass/fail **gate**, the cheap
  tier is enough; spend on L4 (`source`) when you want source-body semantics or
  per-PR localization for humans.

See [Comparison Performance](../contribute/performance.md#scan-level-cost-model-one-cliff-at-l4)
for the measured numbers.
