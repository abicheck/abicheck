---
doc_type: reference
audience:
  - library-maintainer
level: advanced
lifecycle: active
generated: false
---

# Build Evidence Setup

This page is the operational reference for *setting up* build/source evidence
collection — the Clang-plugin build/wiring/traps, the `.abicheck.yml`
project-contract block, out-of-band packs, a full worked CMake example, and
external CLI extractors. For the `abicheck-cc` wrapper — the portable,
supported producer, including build-system wiring and extractor selection —
see [Producing source facts](producing-source-facts.md#wrapper-injection-the-abicheck-cc-compiler-wrapper),
which is also the place to start when deciding *which* producer to use. For
the concept and the [authority rule](../concepts/build-source-data.md#the-authority-rule-the-one-rule-that-matters),
see [Build Info & Sources](../concepts/build-source-data.md).

## Producing a pack — the Clang plugin (zero extra parse)

The **Clang plugin** (`contrib/abicheck-clang-plugin/`) emits the *same*
`source_facts` schema straight from the AST Clang already built during the real
compile, so — unlike the wrapper's companion parse — it adds **no second
front-end pass**. It registers as an `AddAfterMainAction`, so it runs *after*
codegen and can never change or fail the object file (the authority rule,
enforced not just claimed). Reach for it on large/template-heavy builds where
the wrapper's second-parse cost is measurable **and** you own the toolchain
image — because the plugin `.so` links the loading clang's libraries, it is
**ABI-locked to that clang's LLVM major** (see the version-coupling box below).

### Step 1 — build the plugin, once, against *your* clang

```bash
# Debian/Ubuntu needs the matching libclang-XX-dev + llvm-XX-dev packages,
# not only clang-XX. CMAKE_PREFIX_PATH pins WHICH LLVM the plugin links.
cmake -S contrib/abicheck-clang-plugin -B build \
  -DCMAKE_PREFIX_PATH="$(llvm-config --cmakedir)/.."
cmake --build build            # -> build/libabicheck-facts.so
```

Validated across LLVM/Clang **16, 17, and 18** (ADR-038 C.6 differential
conformance gate). The `.so` you build here must be loaded by a clang of the
**same** major.

!!! warning "The plugin is ABI-locked to one LLVM major"
    A plugin built against LLVM *N* loaded into a clang of a different major
    fails to `dlopen` — clang aborts the compile with an error like
    `unable to load plugin '.../libabicheck-facts.so': undefined symbol: _ZN4llvm...`.
    There is no auto-fallback. If your build image can ship more than one
    clang, build (or fetch) one plugin `.so` per major and select it by the
    compiler's version. Detect the major with
    `clang --version` / `llvm-config --version` and keep the `.so` path keyed
    on it (e.g. `plugins/clang-18/libabicheck-facts.so`). This coupling is the
    single reason to prefer the portable `abicheck-cc` wrapper or
    `compile_commands.json` replay unless the second-parse cost is a real
    problem.

### Step 2 — connect it to your build (not one hand-edited compile)

The plugin is turned on by two things on the compile line: `-fplugin=<path>`
to load it, and `-Xclang -plugin-arg-abicheck-facts -Xclang <key=value>` to
configure it. Use the `-Xclang` form — the `-fplugin-arg-` shorthand
mis-parses the hyphenated plugin name and silently hands the argument to a
plugin called `abicheck`.

Rather than hand-editing every compile, define the flags **once** and inject
them into your build system's compile flags. `public-roots=` is strongly
recommended — it is the plugin's equivalent of `ABICHECK_CC_HEADERS`, the
public-header boundary that decides which declarations are ABI-relevant.

```bash
# Build the flag string once (absolute out= — see the parallel-build note).
ABICHECK_PLUGIN_FLAGS="\
-fplugin=$PWD/build/libabicheck-facts.so \
-Xclang -plugin-arg-abicheck-facts -Xclang out=$PWD/abicheck_inputs \
-Xclang -plugin-arg-abicheck-facts -Xclang public-roots=$PWD/include"
```

Then wire it into whichever build system you use.

**CMake** — append the flags to your normal build:

```bash
# Separate arguments (not one quoted string) so CMake passes each token through.
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_CXX_FLAGS="$ABICHECK_PLUGIN_FLAGS"
cmake --build build
```

Or, inside `CMakeLists.txt`, scope it to the library target only:

```cmake
separate_arguments(ABICHECK_FLAGS UNIX_COMMAND "$ENV{ABICHECK_PLUGIN_FLAGS}")
target_compile_options(foo PRIVATE ${ABICHECK_FLAGS})
```

**Make / autotools**:

```bash
make CXXFLAGS="$CXXFLAGS $ABICHECK_PLUGIN_FLAGS"
```

**Bazel** — in the `cc_library`/`cc_binary` rule (or a `--per_file_copt`):

```python
cc_library(
    name = "foo",
    # ... srcs/hdrs ...
    copts = [
        "-fplugin=$(location //tools:libabicheck-facts.so)",
        # Absolute out= (see the notes below) — a relative path scatters across
        # per-target working dirs and is discarded by Bazel's sandbox.
        "-Xclang", "-plugin-arg-abicheck-facts", "-Xclang", "out=/abs/path/to/abicheck_inputs",
        "-Xclang", "-plugin-arg-abicheck-facts", "-Xclang", "public-roots=include",
    ],
)
```

!!! warning "Bazel sandboxes the compile — the pack is discarded by default"
    The plugin writes `out=` as a side effect Bazel does not declare as an
    action output, so the sandbox throws it away. Run the fact-collecting build
    with the compile step un-sandboxed (`--strategy=CppCompile=local`, or
    `--spawn_strategy=local`) and point `out=` at one **absolute** path outside
    the sandbox so the pack survives and every TU converges on it.

!!! danger "Compiler caches skip the compile — and the plugin with it"
    `ccache`/`sccache` key on preprocessed output plus arguments, **not** on
    plugin side effects. A cache **hit** replays the cached object file
    *without running clang*, so **no facts are emitted for that TU** — a
    silent coverage hole, not an error. Run the fact-collecting build with the
    cache disabled (`CCACHE_DISABLE=1` / `SCCACHE_RECACHE=1`), or collect facts
    in a dedicated pass separate from your cached incremental build.
    `distcc`/`icecc` run the compile on a remote host, so `source_facts/` lands
    on the remote filesystem — collect on the driver, or fetch the pack back.

!!! note "Make `out=` absolute for parallel / out-of-tree builds"
    `out=` resolves against each compile's working directory. A relative
    `out=abicheck_inputs` in an out-of-tree build where the compiler runs in
    per-target subdirectories scatters the pack into several
    `abicheck_inputs/` trees. Point `out=` at one **absolute** path (as in the
    `$PWD/abicheck_inputs` above) so every TU converges on one pack.
    Parallelism *within* one directory is safe — the plugin uses per-TU
    race-free filenames and publishes `manifest.json` atomically. For a fresh
    baseline, collect into an empty `out=` so an earlier build's stale
    per-TU facts (e.g. for a since-deleted source) don't linger in the pack.

### Step 3 — fold the emitted pack in

```bash
# One step, no re-parse — the pack is auto-detected from its manifest.json,
# no separate merge command.
abicheck dump libfoo.so -H include/ --build-info ./abicheck_inputs/ \
  -o libfoo.baseline.json
```

Treat warnings about zero public declarations or `0/N` matched exported
symbols as a pack-quality problem, not as a clean success: choose a compile unit
that includes the public API for the library target, and point `public-roots=` at
the physical header path printed by `clang -H`.

The plugin's full contract, coverage, and limitations live in
`contrib/abicheck-clang-plugin/README.md`. GCC (`-fdump-lang-class`) and MSVC
have documented wrapper fallbacks. In every case the output contract is
identical, so `dump --build-info` folds them in the same way, with no separate
merge step; the portable default remains `compile_commands.json` replay
(`dump --sources`).

## Project-contract blocks (ADR-037 D4)

`.abicheck.yml` is also the home for the project's stable comparison contract —
the settings that are version-controlled and reviewed in a PR rather than typed
per run. `compare` auto-discovers the nearest config and merges CLI flags over
it (precedence **CLI > config > built-in default**). Loading is **strict**
(ADR-043): an unknown top-level or block key, a wrong-typed value, or a bad
enum is a hard error (exit 64), not a warning. A top-level `version:` records
the schema version.

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
parsing and source-ABI replay (ADR-037 D8). `hybrid` (G28 Phase 3) is header-AST
only for now — it runs castxml and clang together and merges them, but has no
L4 source-ABI-replay path yet.

## Advanced: out-of-band packs, and what `collect`/`graph` left behind

> **History note:** the standalone `collect` command (which wrote a raw,
> on-disk pack directory) and the `graph explain`/`graph compare` commands
> were both removed outright in the ADR-043 CLI reset — neither has a direct
> CLI replacement. `collect`'s capability lives on as `dump --sources`/
> `--build-info`'s inline collection (below) plus a few library-only
> functions for advanced producers; `graph explain`/`graph compare`'s
> structural diff/localization is Python-API only (see "The L5 graph's own
> diff/localize" below) — only its *derived findings* still surface
> automatically through an ordinary `compare`.

A pack directory (from the `abicheck-cc` wrapper, the Clang plugin, or a
hand-written producer) can be embedded (`dump --build-info <pack>` /
`--sources <pack>` auto-detect a pack by its `manifest.json`) or supplied
out-of-band per side at compare time instead:

```bash
# (Advanced) Override or supply facts out-of-band per side instead of embedding:
abicheck compare old.abi.json new.abi.json \
  --build-info old=old.bs/ --build-info new=new.bs/
```

### The one-step inline flow replaces `collect` + a separate embed

`dump --sources <tree>` **is** the collection step now — it resolves the
compile database (inferring and running the CMake/Bazel/Make query itself
when none is given), runs the L4 replay, and folds the L5 graph, embedding
everything straight into the `.abi.json` in the same invocation that dumps
the binary+headers:

```bash
abicheck dump libfoo.so -H include/ --sources . --depth source -o new.abi.json
```

There is no longer a separate pack-then-embed step, and no `--source-abi-scope`/
`--source-abi-extractor`/`--source-graph` flags to pick — `--depth source`
always runs L4 replay + folds the L5 graph (with the same three automatic
edge kinds `dump --sources` has always added: approximate call edges
`DECL_CALLS_DECL`, type/field-dependency edges, and compile-unit include
edges — each degrading gracefully without `clang++` rather than aborting).
`--depth build` stops at L3 (structural graph only, no L4 replay) for a
cheaper run when you don't need the source-ABI findings.

### What `collect`'s advanced flags have no CLI replacement for

A few capabilities `collect` exposed as flags never got a `dump`/`compare`
equivalent — ADR-043 D4 judged them below the five-command bar — but the
underlying library functions were *not* deleted, only their Click wiring:

| `collect` flag (removed) | Library function to call instead |
|---|---|
| `--from cmake/ninja/bazel/make` (explicit build-system adapter) | `abicheck.buildsource.adapters.{cmake_file_api,ninja,bazel,make}` — `--sources` already infers one of these automatically for the common case |
| `--read-compiler-record` | `abicheck.buildsource.compiler_record` (ELF `.GCC.command.line` / DWARF `DW_AT_producer`, advisory) |
| `--source-abi-cache` (persistent per-TU replay cache) | `abicheck.buildsource.source_replay.SourceAbiCache` / the `ABICHECK_L4_CACHE_DIR` env var still works with `dump --sources` |
| `--extractor-manifest` (external CLI extractors) | `abicheck.buildsource.extractor_manifest.load_extractor_manifest()` / `run_external_extractor()` — see "External CLI extractors" below |
| `--collection-mode {permissive,strict,audit}` | No survivor — a failed producer step degrades coverage silently when scripted directly; call the library function inline in your own producer script if you need one of these behaviors |
| `--kythe-entries`/`--codeql-results` | `abicheck.buildsource.graph_backends.ingest_kythe_entries()` / `ingest_codeql_call_results()` |

See `abicheck/buildsource/CLAUDE.md` for the full module map if you need to
script one of these directly.

### The L5 graph's own diff/localize (`graph explain`/`graph compare`) is Python-API only

```python
from abicheck.buildsource.source_graph import diff_source_graph, localize_symbol

# Structural delta between two source-graph summaries (nodes/edges added/removed)
delta = diff_source_graph(old_graph, new_graph)

# What produced and reaches a symbol: exporting target, source declaration(s),
# declaring public header(s), ABI-relevant build option(s), static callees.
explanation = localize_symbol(graph, "_ZN3foo3barEv")
```

Both load a `SourceGraphSummary` — from a pack directory's
`graph/source_graph_summary.json`, or from `BuildSourcePack.load(path).source_graph`.
Per the authority rule, this only explains and prioritizes impact; it never
decides or suppresses an artifact-proven ABI break on its own. What *did*
carry forward automatically into `compare` is the graph's **derived
findings** (`SOURCE_TO_BINARY_MAPPING_CHANGED`, `PUBLIC_REACHABILITY_CHANGED`,
`INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT`, etc.) — those are ordinary `ChangeKind`s
a `--sources`/`--build-info` comparison reports like any other finding, with
no separate command needed to see them.

## Worked example: a CMake library, end to end

Two releases of `libfoo`, each built with CMake. The goal is a full
**L0+L1+L2+L3+L4** compare so a build-flag change or a source-only API change
is caught alongside the binary diff.

```bash
# --- For EACH release (old and new), at build time ---
# 1. Build with -g and export the compile database (one extra CMake flag).
cmake -S libfoo-1.0 -B build-old -DCMAKE_BUILD_TYPE=Debug \
      -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build build-old

# 2. Snapshot the built library WITH headers, the build context, AND the
#    source tree — one step folds L0-L2 (binary+headers), L3 (build-old's
#    compile DB), and L4/L5 (replay + graph from libfoo-1.0) into one
#    self-contained snapshot. --build-info points at the compile DB since
#    build-old lives outside the libfoo-1.0 --sources tree.
abicheck dump build-old/libfoo.so -H libfoo-1.0/include \
    --sources libfoo-1.0 --build-info build-old/compile_commands.json \
    --depth source --version 1.0 -o libfoo-1.0.abi.json

# (repeat steps 1-2 for the new release → libfoo-2.0.abi.json)

# --- At compare time (CI), just the two snapshots — facts are already embedded ---
abicheck compare libfoo-1.0.abi.json libfoo-2.0.abi.json
```

The compare prints the [coverage table and capability report](../concepts/build-source-data.md#evidence-coverage)
first, so you can confirm every layer landed before trusting the verdict — if a
row says `not_collected` or `[off]`, that is exactly the input or tool to add.

Because `dump --sources`/`--build-info` embeds the normalized facts into the
`.abi.json`, a normal `compare old.json new.json` carries the L3/L4/L5
findings with **no out-of-band directories** to manage or keep in sync —
pass `--build-info old=/new=` only when you deliberately want to override a
side's facts at compare time instead of what's already embedded.

## External CLI extractors & the security model (ADR-032)

A build system abicheck does not natively support can be integrated through an
**external CLI extractor** — a separate program registered by a YAML manifest,
talked to over a subprocess boundary with declared inputs, outputs, and actions.
No untrusted Python is ever imported into the abicheck process. There is no
longer a CLI command to invoke this (the removed `collect --extractor-manifest`
was the only wiring); call it from Python instead:

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

```python
from pathlib import Path
from abicheck.buildsource.extractor import CollectionContext, DEFAULT_ALLOWED_ACTIONS
from abicheck.buildsource.extractor_manifest import (
    load_extractor_manifest, run_external_extractor,
)

manifest = load_extractor_manifest(Path("my-extractor.yaml"))
context = CollectionContext(
    binary_paths=[Path("libfoo.so")],
    header_roots=[Path("include")],
    compile_db=Path("build/compile_commands.json"),
    # allowed_actions defaults to DEFAULT_ALLOWED_ACTIONS (inspect only); add
    # CollectionAction.QUERY_BUILD_SYSTEM etc. explicitly to permit more.
)
result, ledger_record = run_external_extractor(manifest, context, Path("libfoo.evidence"))
```

The security model has three pillars, enforced the same way whether the
manifest is driven by this library call or (historically) the deleted CLI:

- **Trusted-by-operator, never auto-discovered.** A manifest runs only when
  your own code loads it explicitly by path. abicheck never scans a
  filesystem path, the working tree, or any plugin directory looking for one.
- **Declared actions are a ceiling, not a grant.** `inspect` (read existing
  files) is the only action allowed by default; `query_build_system`,
  `run_compiler`, `run_build`, `wrap_build`, and `network` are denied by
  default (network always) unless the caller explicitly opts in. A
  manifest's `allowed_actions` are *intersected* with what the caller
  permits, so a manifest can never escalate beyond what you turned on — and
  an extractor that needs an action you did not enable is **skipped** with a
  diagnostic, never run.
- **No shell, sanitized environment.** Commands are an argv list (never a
  shell string) run with `shell=False` and a minimal environment, so a
  third-party tool never receives your full environment (which may hold
  tokens). Note the action model gates *invocation* — abicheck refuses to
  launch an extractor that needs a disallowed action — but it does not
  sandbox a process once launched; `network` being denied means no extractor
  that *declares* it is run, not a kernel-level block. This is why manifests
  are trusted-by-operator: register only extractors you vet.

Every external run records a full **reproducibility ledger** row in the pack
manifest (ADR-032 D10): the redacted command, its content hash, declared
capabilities, start/finish timestamps, status, and diagnostics.
