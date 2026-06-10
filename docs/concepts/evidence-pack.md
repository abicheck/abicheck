# Source & Build Evidence Packs

abicheck primarily compares **built artifacts** — binaries (L0), debug info
(L1), and public headers (L2). An **EvidencePack** is an *optional* sidecar
that augments a snapshot with **source and build evidence** (ADR-028): build
context (L3), and — in later releases — source ABI replay (L4) and source
graph summaries (L5).

The pack exists to give the existing ABI/API decision engine **more facts** —
to reduce false positives, explain and localize breaks, and detect
source/API risks artifact comparison cannot see. It does **not** turn abicheck
into a general static analyzer.

## The authority rule (the one rule that matters)

> **Artifact-backed L0/L1/L2 evidence remains authoritative for shipped-ABI
> verdicts.** Source/build evidence (L3/L4/L5) may *explain, localize, scope,
> add confidence/provenance, or correlate* an artifact-proven break — but it
> **never silently deletes** one.

Findings produced *only* by build/source evidence are ordinary
[change kinds](../reference/change-kinds.md) that default to **`API_BREAK`**
(source-level breaks) or **risk** (deployment/context risk), never **breaking**
unless an artifact diff also proves the break. They flow through the normal
[verdict](verdicts.md) computation with worst-verdict-wins.

## Evidence layers

| Layer | Source | Purpose | Verdict authority |
|---|---|---|---|
| L0 | ELF/PE/Mach-O | Exported binary ABI facts | Authoritative |
| L1 | DWARF/PDB/BTF/CTF | Layout/type/calling-convention | Authoritative when matched to binary |
| L2 | castxml/public headers | Public API declarations | Authoritative for header-visible API |
| **L3** | compile DB, CMake, Ninja, Bazel, Make | Toolchain, flags, target graph, generated-file provenance | Context/confidence |
| **L4** | per-TU source ABI replay | Source-visible ABI/API facts | API/source-risk evidence; never sole shipped-ABI authority |
| **L5** | Clang/Kythe/CodeQL graph summaries | Include/type/call/build reasoning | Explanation, localization, impact |

L3 and L4 are implemented today (ADR-029, ADR-030). L4 ships three extractor
backends — **clang** (the source-based default: inline/template/constexpr body
fingerprints + default arguments), **castxml** (declarations/types/const values),
and an **Android** header-checker adapter — plus the linker, source-replay diff,
replay scopes, and per-TU cache (see [L4 findings](#source-abi-replay-findings-l4)).
L5 is planned (ADR-031).

> **Source ABI replay (L4) requires clang** (or castxml for the declaration
> subset, or a pre-captured Android dump). It is the one tier gated on a C++
> front-end. If the tool is missing, abicheck **fails gracefully**: L4 is marked
> partial, the source-only checks are reported as disabled, and the
> artifact-backed tiers (L0–L2) remain fully authoritative — the comparison is
> never aborted.

## Workflow

The default path is unchanged. Evidence is **post-build and opt-in** — it never
rebuilds your project or runs arbitrary commands; it reads existing build
outputs and build-system query interfaces only.

```bash
# 1. Collect an evidence pack from an existing build tree (no rebuild).
abicheck collect-evidence \
  --compile-db build/compile_commands.json \
  --build-dir build --cmake \
  --output libfoo.evidence/

# 2a. Attach it to a snapshot (stores a lightweight content-addressed ref).
abicheck dump build/libfoo.so -H include/ \
  --evidence libfoo.evidence/ -o libfoo.abi.json

# 2b. Or compare two snapshots together with their packs.
abicheck compare old.abi.json new.abi.json \
  --old-evidence old.evidence/ --new-evidence new.evidence/
```

To additionally collect **L4 source ABI replay**, add `--source-abi` (requires
clang). The replay scope (ADR-030 D7) decides how many translation units are
parsed:

```bash
abicheck collect-evidence \
  --compile-db build/compile_commands.json \
  --source-abi \
  --source-abi-extractor clang \          # clang (default) | castxml | android
  --source-abi-scope target \             # off | headers-only | changed | target | full
  --source-abi-cache .abicache/source \   # optional per-TU dump cache (ADR-030 D8)
  --output libfoo.evidence/
```

- `--source-abi-scope changed --changed-path src/foo.cpp` replays only changed
  TUs (and TUs of any target whose public header changed) — PR mode.
- `--source-abi-extractor android --android-dump libfoo.lsdump` reuses a
  pre-captured Android `header-abi-dumper`/`header-abi-linker` dump instead of
  running a compiler.

`collect-evidence` accepts:

- `--compile-db PATH` / `-p DIR` — a `compile_commands.json` (the universal,
  low-friction input).
- `--build-dir DIR --cmake` — the CMake File API *reply* directory (target
  graph, public/private header file sets, toolchains).
- `--build-dir DIR --ninja` / `--ninja-compdb FILE` — Ninja `-t compdb`/`graph`
  output (live query or pre-captured for hermetic CI).
- `--bazel-cquery FILE` / `--bazel-aquery FILE` — pre-captured
  `bazel cquery --output=jsonproto` (configured target graph) and
  `bazel aquery --output=jsonproto` (compile/link action graph). Use the
  textual `jsonproto` form: a binary `--output=proto` blob is reported with a
  diagnostic rather than decoded (binary-proto ingestion is a documented
  follow-up).
- `--make-dry-run FILE` — a pre-captured `make -n`/`make --trace` transcript.
  Make has no authoritative target graph, so the recovered compile units are
  **reduced confidence**; prefer a generated `compile_commands.json` when one
  is available.
- `--read-compiler-record` (with `--binary`) — recover compiler provenance from
  the built binary itself: the `.GCC.command.line` ELF section
  (`-frecord-gcc-switches` / `-frecord-command-line`) and DWARF
  `DW_AT_producer`. These signals are **advisory** unless cross-checked against
  build-system evidence.

## Build-evidence findings (L3)

When two packs are compared, abicheck diffs their normalized build evidence and
emits these change kinds (ADR-029 D9):

| Kind | Category | Meaning |
|---|---|---|
| `build_context_changed` | compatible (quality) | Non-ABI build metadata changed |
| `abi_relevant_build_flag_changed` | risk | An ABI-affecting flag changed (`-std`, `_GLIBCXX_USE_CXX11_ABI`, `-fvisibility`, `-fpack-struct`, `-fabi-version`, …) |
| `header_parse_context_drift` | risk | Headers were parsed under a different context than the real build |
| `toolchain_version_changed` | risk | Compiler/stdlib/sysroot changed |
| `generated_file_dependency_unstable` | risk | Build graph indicates generated-file dependency risk |
| `link_export_policy_changed` | risk | Version script / export map / `.def` file changed |

None of these escalate to *breaking* on their own. When an export-policy change
actually removes exported symbols, the artifact diff (L0) emits the breaking
`symbol_removed` finding separately; `link_export_policy_changed` explains and
localizes it.

## Source ABI replay findings (L4)

Some API/ABI-relevant facts are weakly represented or absent in final
binary/debug artifacts — macro constants, default arguments, inline/template
bodies, `constexpr` values, and uninstantiated templates. ADR-030 adds an
**optional** source ABI replay layer that parses selected translation units and
public headers under their real per-TU build context (from L3) and links the
result against the library's exported surface (`source/source_abi.json`).

Comparing two linked source surfaces emits these change kinds (ADR-030 D6):

| Kind | Category | Meaning |
|---|---|---|
| `public_macro_value_changed` | API break | A macro constant in a public header changed value |
| `default_argument_changed` | API break | A default argument changed (signature unchanged) |
| `constexpr_value_changed` | API break | A public `constexpr` constant changed value |
| `uninstantiated_template_removed` | API break | A public template was removed without any binary presence |
| `inline_body_changed` | risk | A public inline body changed with no exported-symbol change (mixed-build/ODR risk) |
| `template_body_changed` | risk | An uninstantiated public template implementation changed (the ADR-026 `case122` residual) |
| `source_decl_binary_symbol_mismatch` | risk | A public declaration no longer maps to an exported symbol |
| `odr_source_conflict` | risk | The same type name resolves to different definitions across TUs |
| `generated_header_changed` | risk | A generated public configuration header changed (policy may escalate) |

Per the authority rule, **none of these are `breaking` on their own**: they are
source/API findings (`API_BREAK`) or deployment/context risks. Every L4 finding
carries an explicit `L4_SOURCE_ABI` evidence-tier boundary (ADR-030 D10) so a
source/API risk is never read as a proven shipped-binary ABI break. A shipped
binary ABI break is still proven only by the artifact diff (L0/L1/L2), and
policy profiles decide whether a source-only finding blocks a release.

## Evidence coverage

Every compare run that involves a pack prints an **evidence-coverage table** so
you can tell which findings are artifact-proven vs. build-context-only:

```text
Evidence coverage:
  L0 binary metadata         present, high confidence
  L1 debug info              present, high confidence: DWARF
  L2 public header AST       present, high confidence: header-scoped
  L3 build context           present, high confidence: cmake+ninja, 142 compile units, 1 target
  L4 source ABI replay       present, high confidence: clang extractor, scope=target, parsed 142/142 TUs
  L5 source graph summary    not_collected
```

The same rows are emitted as a structured `evidence_coverage` array in the
`--format json` report (schema `report_schema_version` 1.2+), so machine
consumers can key off layer status and confidence.

### What is being checked — and what is not, and why

Right below the coverage table, every pack-aware compare prints a **capability
report** that translates the available evidence into the concrete *check
categories* it enables — and, for each disabled category, the precise reason.
This makes the cumulative picture explicit as you add inputs (binary → +debug
info → +headers → +build data → +sources):

```text
Checks enabled for this scan (and why others are not):
  [on]  Symbol presence & linkage (added/removed/SONAME) — from the binary's dynamic symbol table
  [on]  Type layout, members, vtables, signatures — from DWARF/PDB debug info
  [on]  API decls absent from the symbol table; public-surface scoping — from the public header AST
  [on]  Build-flag & toolchain drift (visibility, std, ABI flags) — from build-system data
  [off] Macros, default args, inline/template/constexpr bodies — no sources/clang: source-only API changes are not detected
  [off] Impact / call / reachability graph — no graph evidence: cross-symbol impact is not analyzed
```

Each category is gated on exactly one evidence layer, so a `[off]` line tells you
exactly which input (or tool) to add to enable it — e.g. installing clang and
passing `--source-abi` turns on the macro / default-argument / inline-body /
template-body / constexpr checks.

### Header parse context

`header_parse_context_drift` fires when the new side carries a public-header AST
that was **not** parsed with the build's ABI-relevant flags. To avoid this,
dump the snapshot with the build's compile database — `abicheck dump … -p build/`
records `parsed_with_build_context` on the snapshot, and a later `compare`
honors it and suppresses the drift finding.

## Schema & storage

- The pack is **content-addressed** and **versioned independently**
  (`evidence_pack_version`) from the ABI snapshot schema, so it never bloats an
  ordinary dump. The snapshot stores only a lightweight `evidence_pack`
  reference (content hash + coverage summary); old readers ignore it.
- Every extractor writes both a **raw** artifact (under `raw/`, for
  provenance/debugging) and an abicheck-owned **normalized** fact model (e.g.
  `build/build_evidence.json`). Only normalized facts feed comparison and the
  content hash.
- Command lines and paths are **redacted** (home prefixes, secret-looking
  `-D` macros) before they are persisted.

See ADR-028 (umbrella) and ADR-029 (build context) under
[Development → ADRs](../development/adr/index.md) for the full design.
