# Build Evidence Setup

This page is the operational reference for *setting up* build/source evidence
collection — the pack producers (`abicheck-cc` wrapper, Clang plugin), the
`.abicheck.yml` project-contract block, the `collect` command and out-of-band
packs, a full worked CMake example, and external CLI extractors. For the concept
and the [authority rule](../concepts/build-source-data.md#the-authority-rule-the-one-rule-that-matters),
see [Build Info & Sources](../concepts/build-source-data.md).

## Producing a pack — `abicheck-cc` (the supported producer)

Prefix any compile with **`abicheck-cc`** to capture each TU's source ABI *during
the real build*, with that TU's exact flags and macros:

```bash
export ABICHECK_INPUTS_DIR=abicheck_inputs
export ABICHECK_CC_LIBRARY=libfoo.so
export ABICHECK_CC_HEADERS=include            # public-header roots (ADR-015)

abicheck-cc c++ -std=c++17 -Iinclude -c src/foo.cpp -o foo.o   # …per TU
abicheck merge libfoo.bin.json ./abicheck_inputs/ -o libfoo.baseline.json
```

`abicheck-cc` runs the real compile (pass-through, preserving the exit code),
then best-effort extracts a normalized `SourceAbiTu` and appends it to the pack.
**Fact extraction never fails the build** (authority rule): a missing front-end
or a parse error degrades to a warning. Set `ABICHECK_CC_DISABLE=1` for a pure
pass-through. The wrapper reuses the castxml/clang extractors, so it is the
**portable, supported producer**.

## Producing a pack — the Clang plugin (zero extra parse)

The **Clang plugin** (`contrib/abicheck-clang-plugin/`) emits the *same*
`source_facts` schema straight from the AST Clang already built during the real
compile, so — unlike the wrapper's companion parse — it adds **no second
front-end pass**. Reach for it on large/template-heavy builds where that cost is
measurable and you own the toolchain image (the plugin links the loading clang's
libraries, so it is ABI-locked to that LLVM major — build it once against your
pinned `clang`).

```bash
# 1. Build the plugin against your toolchain's LLVM (once per image).
#    Debian/Ubuntu needs the matching libclang-XX-dev package, not only clang-XX.
cmake -S contrib/abicheck-clang-plugin -B build \
  -DCMAKE_PREFIX_PATH="$(llvm-config --cmakedir)/.."
cmake --build build                                   # -> build/libabicheck-facts.so

# 2. Add it to the normal compile. Use the `-Xclang -plugin-arg-abicheck-facts`
#    form (the `-fplugin-arg-` shorthand mis-parses the hyphenated plugin name).
#    `public-roots=` is strongly recommended — it is the plugin's ABICHECK_CC_HEADERS.
clang++ -std=c++17 -Iinclude \
  -fplugin=./build/libabicheck-facts.so \
  -Xclang -plugin-arg-abicheck-facts -Xclang out=abicheck_inputs \
  -Xclang -plugin-arg-abicheck-facts -Xclang public-roots=include \
  -c src/foo.cpp -o foo.o

# 3. Fold the emitted pack in, exactly like the wrapper (no re-parse).
abicheck dump libfoo.so -H include/ -o libfoo.bin.json
abicheck merge libfoo.bin.json ./abicheck_inputs/ -o libfoo.baseline.json
```

Treat `merge` warnings about zero public declarations or `0/N` matched exported
symbols as a pack-quality problem, not as a clean success: choose a compile unit
that includes the public API for the library target, and point `public-roots=` at
the physical header path printed by `clang -H`.

The plugin is validated as a drop-in for the clang backend by a differential
conformance gate (ADR-038 C.6) that runs across LLVM/Clang 16, 17, and 18; its
full contract, coverage, and limitations live in
`contrib/abicheck-clang-plugin/README.md`. GCC (`-fdump-lang-class`) and MSVC
have documented wrapper fallbacks. In every case the output contract is
identical, so `abicheck merge` ingests them the same way; the portable default
remains `compile_commands.json` replay (`dump --sources`).

## Project-contract blocks (ADR-037 D4)

`.abicheck.yml` is also the home for the project's stable comparison contract —
the settings that are version-controlled and reviewed in a PR rather than typed
per run. `compare` auto-discovers the nearest config and merges CLI flags over
it (precedence **CLI > config > built-in default**). Unknown keys **warn, never
error** (forward-compat), and a top-level `version:` records the schema version.

```yaml
version: 1
severity:                  # per-category overrides (CLI keeps only --severity-preset)
  preset: strict           # default | strict | info-only
  abi_breaking: error      # error | warning | info
  potential_breaking: warning
  quality_issues: info
  addition: info
scope:                     # public-surface / FP tuning (stable project properties)
  public: true
  collapse_versioned_symbols: false
  public_symbols: ["foo_init", "bar_init"]  # exact match only — globs/wildcards are not supported
suppression:               # suppression hygiene (a project rule, inherited by CI)
  strict: true
  require_justification: true
source:
  method: s4               # legacy S-axis escape hatch (deprecated; prefer the --depth dial)
sources:
  graph: summary           # summary | full — L5 source-graph detail (the key is sources.graph)
exit_code_scheme: auto     # auto | legacy | severity (ADR-037 D12)
```

The matching CLI flags (e.g. `--severity-abi-breaking`, `--strict-suppressions`,
`--collapse-versioned-symbols`) stay as **hidden** per-run overrides — functional
but off the visible surface. The L2/L4 frontend is one knob, `--ast-frontend`
(`auto`/`castxml`/`clang`; env `ABICHECK_AST_FRONTEND`), shared across header-AST
parsing and source-ABI replay (ADR-037 D8).

## Advanced: `collect` and out-of-band packs

The `collect` command (which writes an on-disk pack directory) remains for
advanced use — raw-provenance retention, external CLI extractors (ADR-032 D3),
per-TU caching, and audit mode. The common workflow above never needs it. A
pack directory it produces can still be embedded (`dump --build-info <pack>` /
`--sources <pack>` auto-detect a pack by its `manifest.json`) or supplied
out-of-band per side at compare time:

```bash
# (Advanced) Override or supply facts out-of-band per side instead of embedding:
abicheck compare old.abi.json new.abi.json \
  --build-info old=old.bs/ --build-info new=new.bs/

# (Advanced) Collect a pack from an existing build tree (no rebuild), then embed.
#   --source-abi-extractor : clang (default) | castxml | android
#   --source-abi-scope     : off | headers-only | changed | target | full
#   --source-abi-cache     : optional per-TU dump cache (ADR-030 D8)
abicheck collect \
  --compile-db build/compile_commands.json \
  --source-abi \
  --source-abi-extractor clang \
  --source-abi-scope target \
  --source-abi-cache .abicache/source \
  --source-graph summary \
  --output libfoo.evidence/
abicheck dump libfoo.so -H include/ --sources libfoo.evidence/ -o new.abi.json
```

- `--source-abi-scope changed --changed-path src/foo.cpp` replays only changed
  TUs (and TUs of any target whose public header changed) — PR mode.
- `--source-abi-extractor android --android-dump libfoo.lsdump` reuses a
  pre-captured Android `header-abi-dumper`/`header-abi-linker` dump instead of
  running a compiler.

Add `--call-graph` (requires `clang++`) to also fold approximate direct-call
edges (`DECL_CALLS_DECL`, each labelled with a `call_kind` and `resolution`
confidence) into the graph — enabling the
`call_graph_public_entry_reachability_changed` quality finding. Without `clang`
the graph is still collected, just without call edges.

Further graph layers (all optional, all non-aborting if the tool/file is
absent):

- `--include-graph` (requires `clang++`) folds compile-unit include edges
  (`COMPILE_UNIT_INCLUDES_FILE`, from `clang -MM`), enabling
  `include_graph_public_header_drift`.
- `--kythe-entries FILE` / `--codeql-results FILE` fold a **pre-captured**
  Kythe entries export or CodeQL call-graph query result into the graph
  (ADR-031 D5). abicheck never runs Kythe or CodeQL — it ingests their exported
  JSON and records the external store in `external_graph_refs`.

Localize a single finding through the graph:

```bash
abicheck graph explain --sources libfoo.evidence/ --symbol _ZN3foo3barEv
# or resolve the symbol from a JSON report:
abicheck graph explain --sources libfoo.evidence/ --report report.json --finding-id 0
```

It reports what produced and reaches the symbol — exporting target, source
declaration(s), declaring public header(s), ABI-relevant build option(s), and
static callees — as graph-derived explanation, never an ABI verdict.

Compare two graph summaries directly — pass either the pack directories or the
`graph/source_graph_summary.json` files:

```bash
abicheck graph compare old.evidence/ new.evidence/            # structural delta
abicheck graph compare old.evidence/ new.evidence/ --format json
```

The diff is **structural** (which nodes/edges entered or left the graph). Per
the authority rule it explains and prioritizes impact; it never, on its own,
decides or suppresses an artifact-proven ABI break.

`collect` accepts:

- `--compile-db PATH` / `-p DIR` — a `compile_commands.json` (the universal,
  low-friction input).

Build-system adapters are selected with a single repeatable
`--from ADAPTER[=PATH]`. Live adapters read `--build-dir` and take no path;
pre-captured adapters require a path:

- `--build-dir DIR --from cmake` — the CMake File API *reply* directory (target
  graph, public/private header file sets, toolchains).
- `--build-dir DIR --from ninja` / `--from ninja-compdb=FILE` — Ninja
  `-t compdb`/`graph` output (live query or pre-captured for hermetic CI).
- `--from bazel-cquery=FILE` / `--from bazel-aquery=FILE` — pre-captured
  `bazel cquery --output=jsonproto` (configured target graph) and
  `bazel aquery --output=jsonproto` (compile/link action graph). Use the
  textual `jsonproto` form: a binary `--output=proto` blob is reported with a
  diagnostic rather than decoded (binary-proto ingestion is a documented
  follow-up).
- `--from make=FILE` — a pre-captured `make -n`/`make --trace` transcript.
  Make has no authoritative target graph, so the recovered compile units are
  **reduced confidence**; prefer a generated `compile_commands.json` when one
  is available.
- `--read-compiler-record` (with `--binary`) — recover compiler provenance from
  the built binary itself: the `.GCC.command.line` ELF section
  (`-frecord-gcc-switches` / `-frecord-command-line`) and DWARF
  `DW_AT_producer`. These signals are **advisory** unless cross-checked against
  build-system evidence.

## Worked example: a CMake library, end to end

Two releases of `libfoo`, each built with CMake. The goal is a full
**L0+L1+L2+L3(+L4)** compare so a build-flag change or a source-only API change
is caught alongside the binary diff.

```bash
# --- For EACH release (old and new), at build time ---
# 1. Build with -g and export the compile database (one extra CMake flag).
cmake -S libfoo-1.0 -B build-old -DCMAKE_BUILD_TYPE=Debug \
      -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build build-old

# 2. Collect the build/source pack from the existing build tree (no rebuild).
#    Add --source-abi for L4 (needs clang); drop it for L3-only.
abicheck collect \
    --compile-db build-old/compile_commands.json \
    --build-dir build-old --from cmake \
    --source-abi \
    --output libfoo-1.0.evidence/

# 3. Snapshot the built library WITH headers and the build context.
#    -p bakes the L3 build flags into how the headers are parsed, and records
#    parsed_with_build_context so the later compare won't flag header drift.
abicheck dump build-old/libfoo.so -H libfoo-1.0/include -p build-old \
    --version 1.0 -o libfoo-1.0.abi.json

# (repeat steps 1-3 for the new release → libfoo-2.0.abi.json + libfoo-2.0.evidence/)

# --- At compare time (CI), pass BOTH snapshots AND both packs ---
abicheck compare libfoo-1.0.abi.json libfoo-2.0.abi.json \
    --build-info old=libfoo-1.0.evidence/ \
    --build-info new=libfoo-2.0.evidence/
```

The compare prints the [coverage table and capability report](../concepts/build-source-data.md#evidence-coverage)
first, so you can confirm every layer landed before trusting the verdict — if a
row says `not_collected` or `[off]`, that is exactly the input or tool to add.

Because `dump --build-info/--sources` embeds the normalized facts into the
`.abi.json`, a normal `compare old.json new.json` carries the L3/L4/L5 findings
with **no out-of-band directories**. Keeping the `*.evidence/` pack directories
next to the snapshots (e.g. as CI artifacts) is therefore optional — useful only
when you want to re-attach raw provenance, override a side at compare time with
`--build-info` / `--sources`, or debug what was collected.

## External CLI extractors & the security model (ADR-032)

A build system abicheck does not natively support can be integrated through an
**external CLI extractor** — a separate program registered by a YAML manifest,
talked to over a subprocess boundary with declared inputs, outputs, and actions.
No untrusted Python is ever imported into the abicheck process.

```yaml
# my-extractor.yaml
name: abicheck-cmake-extractor
version: "1.0"
capabilities: { compile_db: true, target_graph: true }
allowed_actions: [inspect, query_build_system]
commands:
  collect:   ["abicheck-cmake-extractor", "collect", "--output", "{raw_dir}"]
  normalize: ["abicheck-cmake-extractor", "normalize", "--raw", "{raw_dir}", "--out", "{normalized_dir}"]
outputs:
  normalized:
    - { kind: build_evidence, path: build/build_evidence.json }
```

```bash
abicheck collect \
  --extractor-manifest my-extractor.yaml \
  --allow-build-query \
  -o libfoo.evidence/
```

The security model has three pillars:

- **Trusted-by-operator, never auto-discovered.** A manifest runs only when you
  register it explicitly with `--extractor-manifest PATH`. abicheck never scans
  `PATH`, the working tree, or any plugin directory.
- **Declared actions are a ceiling, not a grant.** `inspect` (read existing
  files) is the only action allowed by default. `query_build_system` is enabled
  by `--allow-build-query`; `run_compiler`, `run_build`, `wrap_build`, and
  `network` are denied by default (network always). A manifest's
  `allowed_actions` are *intersected* with what the run permits, so a manifest
  can never escalate beyond what you turned on — and an extractor that needs an
  action you did not enable is **skipped** with a diagnostic, never run.
- **No shell, sanitized environment.** Commands are an argv list (never a shell
  string) run with `shell=False` and a minimal environment, so a third-party
  tool never receives your full environment (which may hold tokens). Note the
  action model gates *invocation* — abicheck refuses to launch an extractor that
  needs a disallowed action — but it does not sandbox a process once launched;
  `network` being denied means no extractor that *declares* it is run, not a
  kernel-level block. This is why manifests are trusted-by-operator: register
  only extractors you vet.

Every external run records a full **reproducibility ledger** row in the pack
manifest (ADR-032 D10): the redacted command, its content hash, declared
capabilities, start/finish timestamps, status, and diagnostics.

`--collection-mode` controls how failures are handled (ADR-032 D9):

- `permissive` (default) — a failed extractor degrades coverage; collection
  continues. Good for PR CI.
- `strict` — a failed or invalid extractor exits non-zero. Good for baseline
  generation, where missing evidence must be a hard error.
- `audit` — preserve raw artifacts and full diagnostics for debugging.
