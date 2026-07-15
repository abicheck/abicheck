# ADR-038: Working With Sources — Full-Scan and Two Build-Injection Flows, and the Clang Plugin Specification

**Date:** 2026-07-01
**Status:** Accepted — implemented. Formalizes and extends ADR-035 D5 (Flow 1 /
Flow 2) into a complete, three-flow producer contract and pins the Clang-plugin
specification. Flow A (full source scan / no injection) and Flow B (the
`abicheck-cc` wrapper) already ship; Flow C (the Clang plugin,
`contrib/abicheck-clang-plugin/`) now implements this spec —
functions/mangled-name rule/signatures/default args,
typedefs, constexpr, records/enums/templates **including the AST subtree hashes**
(`type_hash`/`body_hash`), macros (in-compile `PPCallbacks`), and visibility. The
subtree hashes are produced by serializing the in-memory AST with clang's own
JSON dumper (`Decl::dump(…, ADOF_JSON)`) and porting `clang.py`'s
canonicalization onto it, so parity holds by construction for a given clang
version. The **C.6 differential-conformance gate runs green in CI across LLVM/Clang
16, 17, and 18** (`.github/workflows/clang-plugin.yml`) — the plugin's public
surface is entity-equivalent to the clang backend on each. The one documented
residual is a floating-point literal's textual value inside a hashed subtree, plus
the pragmatic visibility-classifier edge cases (C.7).
**Decision maker:** Nikolay Petrov (@napetrov)

---

## Context

Every source-aware check in abicheck rests on one expensive step: turning a
library's C/C++ **headers and sources** into abicheck's normalized source-ABI
model. Concretely that means running a C++ front end (castxml or
`clang -ast-dump=json`) over translation units and folding the result into the
L4/L5 evidence layers:

- **L0/L1/L2** — artifact-authoritative binary / debug-info / header-AST scan
  (`dumper.py`, `elf|pe|macho_metadata.py`, `dwarf_*.py`). Always the source of
  truth for a *shipped-ABI* verdict.
- **L3** — build/toolchain context from a compile DB, CMake, Ninja, Bazel, or
  Make (`buildsource/adapters/*`, `build_evidence.py`).
- **L4** — scoped per-TU **source-ABI replay**: parse each TU under its real
  build flags → a normalized `SourceAbiTu` (`source_extractors/*`,
  `source_replay.py`, `source_abi.py`).
- **L5** — the source/implementation graph folded from L3+L4
  (`source_graph.py`).

L4 is the cost centre. For template-heavy C++ a single TU's `clang` JSON AST can
be **multi-GiB** — the reason `source_replay.py` carries RAM-aware worker caps,
AST-spill-to-tempfile, and an opt-in process pool. The strategic lever is *where*
that parse happens and *who pays for it*.

ADR-035 D5 named two answers — *Flow 1* (abicheck runs the replay) and *Flow 2*
(the build emits normalized facts) — and shipped the `abicheck_inputs/` artifact
protocol plus the `abicheck-cc` wrapper. It left two things underspecified:

1. **The "work with sources" story is not written down end-to-end.** A user has
   to reverse-engineer, from flags and module docs, that there is a full-scan
   path *and* two injection paths, that they interoperate, and how to move
   between them.
2. **The plugin has no build-to specification.** ADR-035 D5 calls it a
   "performance optimization" but never states what it must emit, how its records
   must match the wrapper's byte-for-byte, how it is built/versioned, or how it
   is validated.

This ADR closes both: it documents the **three flows** as one interchangeable
family and gives the plugin a complete, testable contract.

---

## Decision

Support **three flows** for producing source evidence, all converging on one
normalized `SourceAbiTu` contract and one `merge`/`compare` consumer. They differ
only in *where the parse happens* and *how much the product build must change*.

| Flow | Name | Build change | Extra parse | Who parses | Consumer path | Status |
|------|------|--------------|-------------|------------|---------------|--------|
| **A** | **Full source scan** (`dump --sources` / `collect`) | **None** | 1, post-build | **abicheck** | inline, or `collect` → `dump --build-info` | ✅ Shipped (Flow 1) |
| **B** | **Wrapper injection** (`abicheck-cc`) | Set `CC`/`CXX` | 1 companion, in-build | The build (as a companion action) | `merge` an `abicheck_inputs/` pack | ✅ Shipped (Flow 2) |
| **C** | **Plugin injection** (`-fplugin`) | Add a clang flag | **0** — rides the compile's AST | The compile itself | `merge` an `abicheck_inputs/` pack | ⚙️ Reference — implemented (incl. subtree hashes); C.6 gate in CI matrix |

**D0 — the shared-contract invariant.** A *comparison* is always old-vs-new
produced by the **same** producer, and every producer is deterministic and
diff-stable (same TU → same records). Producers are **not** required to be
byte-identical to each other: the castxml and clang backends already hash the
same declaration differently — castxml builds a `ret(params)` signature string
(`base.py`), clang hashes its `type.qualType` and uses an alpha-normalized AST
subtree hash for types (`clang.py`). Cross-producer equivalence is required only
between the plugin and the **specific backend it substitutes**: because the
plugin reads the clang AST, its reference is the **clang extractor** (`clang.py`),
and the C.6 conformance gate compares the plugin against a *clang-backed* wrapper
(not castxml). The linker folds by `SourceEntity.identity()` — the mangled name,
else `qualified_name#signature_hash` — so mangled decls fold consistently even
across producers, while unmangled-decl hashes are guaranteed stable only *within*
one producer. The binary dump (L0–L2) stays artifact-authoritative for
shipped-ABI verdicts; source evidence only *explains, localizes, scopes, or adds
source-level (`API_BREAK`/`RISK`) findings* and never deletes an artifact-proven
break (ADR-028 D3 authority rule). This is what makes the three flows a
**migration path, not a lock-in**: a project adopts at Flow A with zero build
changes and moves to B or C only when parse cost demands it — with no change to
the compare side. The one operational rule this implies: **produce the old and
new baselines of a comparison the same way** — the only sanctioned cross-producer
pair is the plugin and the clang backend it is conformance-tested against (C.6),
which agree on the *whole* surface including macros; an *arbitrary* producer mix
(e.g. castxml vs clang) is reliable only on the mangled surface.

---

## The shared contract: `SourceAbiTu` and `SourceEntity`

All three flows emit the same abicheck-owned normalized schema
(`buildsource/source_abi.py`, `SOURCE_ABI_VERSION`). Raw front-end output
(castxml XML, clang AST JSON, Android `.lsdump`) is provenance only and is never
compared (ADR-028 D4).

**`SourceAbiTu`** — one per translation unit:

| Field | Meaning |
|-------|---------|
| `schema_version` | `SOURCE_ABI_VERSION` |
| `tu_id` | `cu://<source>#cfg:<hash>` — stable per-TU id |
| `target_id` | `target://<library>` |
| `extractor` | `{"name", "version"}` producer id |
| `compile_context_hash` | `sha256:` over standard/triple/sysroot/defines/includes (D8 cache key) |
| `source` | the TU source path |
| `public_header_roots` | configured public-header roots (ADR-015) |
| `functions`/`types`/`variables`/`macros`/`templates`/`inline_bodies`/`constexpr_values`/`declarations` | `SourceEntity[]` buckets |
| `source_edges` | optional intra-TU decl edges (→ L5) |
| `read_files` | every file the parse actually read (cache invalidation) |
| `diagnostics` | non-fatal producer notes |

**`SourceEntity`** — one per public declaration:

| Field | Meaning |
|-------|---------|
| `id` | content hash; the primary key within a TU |
| `kind` | `function`/`record`/`enum`/`typedef`/`union`/`variable`/`macro`/`template`/`inline`/`constexpr` |
| `qualified_name` | fully-qualified source name |
| `mangled_name` | C++ ABI symbol, or `""` when indistinct (see the mangled-name rule) |
| `signature_hash` | type-level signature (params/return + cv/ref) — stable across default-arg edits |
| `body_hash` | inline/template body fingerprint |
| `type_hash` | record/enum/typedef structural hash |
| `value` | macro/`constexpr` value, or the function's default-argument string |
| `source_location` | `{path, line, origin}` where `origin ∈ PUBLIC_HEADER/PRIVATE_HEADER/SYSTEM_HEADER/GENERATED/SOURCE/UNKNOWN` |
| `visibility` | `public_header`/`private_header`/`system_header`/`generated`/`unknown` |
| `api_relevant` | on the callable public surface? |
| `confidence` | `LayerConfidence` |

`SourceEntity.identity()` — the key the linker/diff fold on — is the
`mangled_name` when present, else `qualified_name#signature_hash`, else the bare
`qualified_name`. Because folding is by `identity()`, **entity ordering within a
TU does not affect the verdict**; the contract is per-entity equality, not raw
file-byte equality.

---

## Flow A — Full source scan (no injection)

**The straight way: change nothing in the build.** abicheck reads the build's
existing `compile_commands.json` (or infers one) *post-build* and replays the
in-scope TUs itself. Nothing is injected into the compile; the build is untouched
and unaware. This is the default and the recommended starting point.

### A1 — Inline (`dump --sources`)

One command materializes a baseline with L3/L4/L5 folded in:

```bash
# Binary L0–L2 from the .so + L3/L4/L5 replayed from ./src, in-process.
# No wrapper, no plugin, no build edit. (L4 needs public-header roots — see the
# note below; the inline path takes them from config, not a dump flag.)
abicheck dump libfoo.so --sources ./src -o libfoo.baseline.json

abicheck compare libfoo.old.baseline.json libfoo.new.baseline.json
```

*Compile-DB* resolution is **zero-config** (`buildsource/inline.py` +
`build_query.py`, ADR-032 amended): explicit `--build-info` → a trusted
`--build-query` command → a `build.compile_db` glob → an auto-discovered
`compile_commands.json` → an inferred, abicheck-authored build-system query
(`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` / `bazel aquery` / `make -B -n -k -w`).
So `--sources ./src` alone yields L3 with no flag and no manual compile-DB step.
An arbitrary `build.query` from an *auto-discovered* (untrusted) `.abicheck.yml`
is never auto-run — it needs an explicit `--config`.

**Public-header roots are a separate input from the compile DB — and the inline
`dump --sources` path takes them from *config*, not a CLI flag.** L4 provenance is
opt-in (`provenance.classify_origin`): with no public-header set, every
declaration classifies `UNKNOWN`, `link_source_abi` drops it, and the L4 surface
is *empty* even though the TUs parsed. A plain `compile_commands.json` carries no
public-header metadata, and `dump`'s `--public-header`/`--public-header-dir` flags
feed only the **L2** header-AST provenance — `embed_build_source` does not forward
them to the inline L4 collection. So give the inline L4 surface its roots via
`.abicheck.yml` `sources.public_headers` or a CMake File API build dir (whose
fileSets populate `target.public_headers`). For roots specified **on the command
line**, use the A2 `collect -H/--header` path (which *does* feed L4).

### A2 — Split producer/consumer (`collect` → `dump --build-info`)

To parse on a build host and compare elsewhere, materialize a `BuildSourcePack`
with `collect`, then attach it to the binary dump with `dump --build-info`:

```bash
# build host: L3 build evidence + L4 source-ABI replay → an evidence pack.
# --binary relinks the L4 surface against the library's exports (the source-decl
# ↔ binary-symbol map); --header gives the public-header roots that classify
# which decls are on the public surface — without them the extractor marks decls
# UNKNOWN and the linker drops them. dump --build-info only embeds the
# pre-captured pack; it does not relink.
abicheck collect --binary libfoo.so --header include/ --compile-db build/compile_commands.json --source-abi -o libfoo.evidence/
# analysis host: attach the pre-captured pack to the binary dump (no re-parse)
abicheck dump libfoo.so --build-info libfoo.evidence/ -o libfoo.baseline.json
```

`collect` produces a `BuildSourcePack` consumed via `dump --build-info`/`--sources`
— **not** an `abicheck_inputs/` pack via `merge` (that protocol is Flow B/C).

### Scope, cost, and when to use

- **Backend:** on inline `dump --sources` the knob is `--ast-frontend
  auto|clang|castxml` (ADR-037 D8; the same dial drives the L2 header AST);
  `collect` spells it `--source-abi-extractor auto|clang|castxml|android`. `clang`
  adds inline/template/constexpr **body** fingerprints + default args; `castxml`
  gives declarations/types/const values only. On `collect` a requested `clang`
  not on PATH falls back to castxml; the inline `dump --sources` path instead
  **disables** source-only checks when the selected frontend is unavailable (it
  records "source-only checks disabled" rather than switching backends), so on a
  clang-less host pass `--ast-frontend castxml` explicitly.
- **Scope:** on inline `dump --sources` use the `--depth`/`--max` dial (ADR-037
  D5; `binary|headers|build|source|full`) to bound how deep to collect. The
  fine-grained replay scopes (`--source-abi-scope off|headers-only|changed|
  target|full` + `--changed-path`) are **`collect`-only**; inline `dump --sources`
  has no `--changed-path` and defaults to a broader `source-target` collection.
  **Changed-only** replay is therefore a `collect --source-abi-scope changed
  --changed-path …` (or `scan --since`) capability — reach for it on PR jobs; the
  inline `dump` default is not changed-scoped.
- **Cost:** one parse per in-scope TU, paid **by abicheck**, not the build. The
  measured cost cliff is at L4 for template-heavy C++ (`scan_levels.py` cost
  model); scope + the per-TU content-addressed cache (`ABICHECK_L4_CACHE_DIR`)
  are the levers that keep it bounded.
- **Use it when:** you can't or won't alter the build; open-source consumers;
  first adoption. Requires a compatible front end on the analysis host.

---

## Flow B — Wrapper injection (`abicheck-cc`)

Prefix the real compiler with `abicheck-cc` (`abicheck/cc_wrapper.py`). The
wrapper runs the real compile pass-through (preserving its exit code), then
**best-effort** extracts one `SourceAbiTu` per source TU using that TU's *exact*
flags/macros and appends it to an `abicheck_inputs/` pack.

```bash
export ABICHECK_INPUTS_DIR=abicheck_inputs
export ABICHECK_CC_HEADERS=include        # public-header roots
export ABICHECK_CC_LIBRARY=libfoo

make CC='abicheck-cc gcc' CXX='abicheck-cc g++'   # CMake: -DCMAKE_CXX_COMPILER_LAUNCHER=abicheck-cc

# merge folds pre-existing .abi.json dumps + packs; dump the binary side first.
abicheck dump libfoo.so -o libfoo.so.json
abicheck merge libfoo.so.json ./abicheck_inputs/ -o libfoo.baseline.json
```

- **Best-effort authority (ADR-028 D3):** extraction is skipped on a failed
  compile and any extraction error is downgraded to a warning — it **never fails
  the build**. A preprocess-/dependency-only invocation (`-E`, `-M`/`-MM`,
  `/E /P /EP`) is detected and skipped so no non-shipping TU pollutes the pack.
  A multi-source compile (`g++ -c a.cpp b.cpp`) contributes *both* objects'
  facts, per-TU isolated.
- **Exact per-TU context:** flags, macros, includes, sysroot, and target triple
  are captured from the real argv, so `compile_context_hash` matches the build.
- **Cost:** one *companion* parse per TU, inside the build. More than Flow C,
  but no version-pinned artifact and it wraps any compiler.
- **Config (argv-transparent, all env):** `ABICHECK_INPUTS_DIR`,
  `ABICHECK_CC_EXTRACTOR`, `ABICHECK_CC_HEADERS`, `ABICHECK_CC_LIBRARY`,
  `ABICHECK_CC_VERSION`, `ABICHECK_CC_DISABLE`.
- **Use it when:** you control the build invocation, want exact-build-context
  facts, and can't/won't pin a Clang-plugin `.so` to your toolchain.

---

## Flow C — Plugin injection: the specification

Load an abicheck Clang plugin during the normal compile
(`contrib/abicheck-clang-plugin/`). It emits the *same* `source_facts` from the
AST **Clang already built for the real compile** — **zero** extra front-end pass.
This is the fastest producer and the strategic answer for large/template-heavy
builds, because the fact stream falls out of a compile the project was already
running.

```bash
clang++ -std=c++17 -Iinclude \
  -fplugin=./libabicheck-facts.so \
  -Xclang -plugin-arg-abicheck-facts -Xclang out=abicheck_inputs \
  -Xclang -plugin-arg-abicheck-facts -Xclang public-roots=include \
  -c src/foo.cpp -o foo.o
# real compile only; abicheck_inputs/source_facts/<tu>.jsonl appended

# dump the binary side first, then fold the emitted facts in (no re-parse).
abicheck dump libfoo.so -o libfoo.so.json
abicheck merge libfoo.so.json ./abicheck_inputs/ -o libfoo.baseline.json
```

Two argument details matter and are part of the spec:

- **Use the `-Xclang -plugin-arg-<name> -Xclang <arg>` form, not the
  `-fplugin-arg-<name>-<arg>` shorthand.** The shorthand mis-parses a *hyphenated*
  plugin name: clang splits `-fplugin-arg-abicheck-facts-out=…` at the first
  hyphen and delivers it to a plugin named `abicheck` (verify with `clang++ -###`),
  so `abicheck-facts`'s `ParseArgs` never sees `out=`. The `-Xclang` cc1 form is
  unambiguous. (Alternatively, register the action under a hyphen-free name so the
  shorthand works — but the reference skeleton uses `abicheck-facts`.)
- **`public-roots=` is mandatory** — it is the plugin's equivalent of the
  wrapper's `ABICHECK_CC_HEADERS`. L4 provenance is opt-in
  (`provenance.classify_origin`): with no public-header roots every declaration
  classifies non-public and `link_source_abi` drops it, so the plugin would emit
  an empty public surface. Repeatable; may also be sourced from build metadata.

### C.1 — Structure and lifecycle

The plugin is a `clang::PluginASTAction` registered as `abicheck-facts` with
`getActionType() == AddAfterMainAction` — it runs **after** the real codegen
action, so it never perturbs the object output. `ParseArgs` reads `out=<dir>`
(default `abicheck_inputs`) and the repeatable `public-roots=<path>` (see the
invocation note above for the correct `-Xclang -plugin-arg-abicheck-facts` form).
To capture macros without a second parse (C.2), it registers `PPCallbacks` on the
`CompilerInstance`'s `Preprocessor` in `CreateASTConsumer`/`ParseArgs`.
`HandleTranslationUnit` walks the TU with a `RecursiveASTVisitor`, buffers one
`SourceEntity` per public declaration, wraps them in a `SourceAbiTu` envelope,
and appends **one JSON object per line** to a **per-TU** file.

### C.2 — What it MUST emit (record-equivalence with the clang backend)

The plugin reads the clang AST, so its reference is
`buildsource/source_extractors/clang.py` (`source_abi_from_clang_ast`) — **not**
`base.py`, which is the castxml recipe. The hashing recipe is fixed and part of
the contract:

- **Content hash:** `_hash(*parts) = "sha256:" + hex(sha256(parts joined by "\x00"))`
  — same construction as `base.py`, but the *parts* below are the clang recipe.
  Any deviation changes `identity()`/`*_hash` and fails the C.6 gate.

| Kind | `id` parts | key hashes / fields |
|------|-----------|---------------------|
| function | `"function", mangled_or_name, sig` | `sig = type.qualType` (clang's printed function type); `signature_hash = _hash("sig", sig)`; `value = _default_arg_repr` → `p<pos>=<literal-or-subtree_hash>` per defaulted param |
| inline body | `"inline", mangled_or_name, sig` | emitted when the function has a `CompoundStmt` body; `body_hash = subtree_hash(body, param_ids)` |
| record / enum | `"type", qualified_name` | `type_hash = subtree_hash(node)`; definitions only (skip forward decls) |
| typedef / alias | `"typedef", qualified_name, underlying` | `underlying = type.qualType`; `type_hash = _hash("typedef-target", underlying)`; `value = underlying` |
| constexpr var | `"constexpr", qualified_name, value` | `value` = lone-literal value, else `subtree_hash(init)` |
| template | `"template", qualified_name` | `body_hash = subtree_hash(node)`; do not descend into the templated pattern |
| macro | `"macro", name, value` | captured **in-compile** via `PPCallbacks` (`MacroDefined`/`MacroUndefined`) — never a second `-E -dD` pass (C.3); public-header macros only, include guards dropped, value normalized to match `clang.py::macros_from_preprocessor` |

- **`subtree_hash`** was the hard part and is now **implemented**: `clang.py`
  hashes an **alpha-renamed, commutative-operator-normalized, build-root-stripped
  canonical form of clang's *JSON* AST** (`_canonical`/`_alpha_rename_map`/
  `_subtree_hash`). Rather than hand-reproduce clang's JSON, the plugin
  serializes the relevant subtree with **clang's own JSON dumper in-process** —
  `Decl::dump(os, false, ADOF_JSON)`, the exact code path `-ast-dump=json` uses —
  and ports `_alpha_rename_map`/`_canonical`/`_subtree_hash` (plus `_expr_value`/
  `_default_arg_repr`) onto that JSON. Because the wrapper's clang backend
  consumes the *same* clang JSON, the hashes match by construction for a given
  clang version; cross-version drift is caught by the C.6 CI matrix (C.6). No
  second parse is added — the dump reads the AST clang already built.
- **Mangled-name rule:** take clang's `mangledName`; if it equals the plain
  `name` (e.g. some constructors), leave `mangled_name` **empty** so `identity()`
  falls back to `qualified_name#signature_hash` and keeps unmangled overloads
  distinct. Copying the bare name verbatim would collapse `Widget(int)` and
  `Widget(double)`.
- **Visibility / api_relevant:** classify each decl's declaring file (via
  `SourceManager`, threading clang's sticky `loc.file`) into
  `PUBLIC_HEADER/PRIVATE_HEADER/SYSTEM_HEADER/GENERATED`, mirroring
  `clang.py::_ClassifyContext`. Only public-surface decls are emitted; a
  private/protected member of a public class is dropped (its whole subtree stays
  non-public). Public-header roots come from the plugin arg / build.
- **Determinism:** the same TU compiled twice must yield identical records
  (`clang.py` sorts macros; AST-order for the rest). Folding is order-independent,
  but determinism keeps the pack diff-stable.

### C.3 — What it MUST NOT do

- **Never fail or slow the real compile abnormally** — a fact-emission error is
  swallowed (write to `stderr` at most), exactly like the wrapper's best-effort
  rule. A plugin exception must not abort codegen.
- **Never emit a verdict.** It produces evidence, not decisions.
- **Never ship raw AST as the comparison format.** `raw_ast/` is forensic only
  and is never ingested (ADR-035 D5); the plugin normalizes to `source_facts`
  itself.
- **No second parse.** If a mapping needs data the AST does not cheaply expose,
  approximate within the visitor — do not re-invoke the front end.

### C.4 — Output layout (per-TU, race-free)

Append to `<out>/source_facts/<stem>.<sha256(source)[:12]>.jsonl`, mirroring
`inputs_emit.facts_filename()`, so parallel `-j` compiles never race on one file.
The plugin also ensures `<out>/manifest.json` exists (`kind: abicheck_inputs`,
`created_by: "abicheck-clang-plugin <ver>"`) — idempotent, atomic write, matching
`init_inputs_pack`.

### C.5 — Build and versioning

```bash
cmake -S contrib/abicheck-clang-plugin -B build \
  -DCMAKE_PREFIX_PATH="$(llvm-config --cmakedir)/.."
cmake --build build     # → libabicheck-facts.so
```

The plugin is a CMake `MODULE` linked against the **loading clang's** symbols
(`find_package(LLVM/Clang CONFIG)`, `cxx_std_17`, no bundled LLVM). It is
therefore **ABI-locked to its LLVM major**: a plugin built against LLVM *N* only
loads into that `clang`. abicheck cannot ship one `.so` for every LLVM, so the
plugin is **not a required gate in the main abicheck CI**; a product build has a
pinned toolchain image and builds the plugin once against that image. That
ABI-lock is exactly why validation is *per-version*: the `clang-plugin` workflow
(`.github/workflows/clang-plugin.yml`) builds the plugin against a **matrix of
LLVM/Clang majors** and runs the C.6 conformance test on each (C.6). It is a
standalone, non-blocking workflow, path-filtered to the plugin and the
clang-recipe modules it mirrors — validation without gating merges, consistent
with "not a required gate".

### C.6 — Validation: differential conformance

The plugin is correct **iff** it is a drop-in for the **clang** backend. The gate
is a differential test (`contrib/abicheck-clang-plugin/tests/conformance.py`):
compile one fixture TU both ways with the **same** clang — the plugin (with
`public-roots=include`), and the wrapper pinned to clang with the same roots
(`ABICHECK_CC_EXTRACTOR=clang`, *not* `auto`, plus `ABICHECK_CC_HEADERS=include`,
so both sides use the recipe *and* the public surface the plugin targets) —
ingest both packs, and assert the two surfaces are **entity-equivalent**: equal
sets keyed by `SourceEntity.identity()`, with equal
`signature_hash`/`type_hash`/`body_hash`/`value`/`visibility`/`api_relevant` per
entity. Non-macro entities are compared **strictly**; macro *values* are compared
leniently (operator-adjacent spacing is the documented soft edge, C.7). The
`clang-plugin` workflow runs this on a **matrix of LLVM/Clang majors** (pinning
`clang`/`clang++` on `PATH` to each matrix version so the plugin and the wrapper's
extractor use the identical clang — the precondition for byte-for-byte parity),
and — because the plugin is a plain LLVM shared module — builds it with **both
GCC and Clang as the host compiler** (LLVM 18 both ways; 16/17 on the distro
default) to keep it host-toolchain-portable. It runs only where a matching clang
is available and is never a required abicheck-CI gate.

Beyond entity equivalence, each matrix leg also runs an **end-to-end scan
validation** (`tests/scan_flow.py`): it compiles the fixture into a shared
library *with the plugin active* (one build both links the `.so` and drops
`abicheck_inputs/` beside it), then drives the real user pipeline — `abicheck
dump` the binary (L0/L1), `abicheck merge` the plugin pack into the baseline
(asserting the L4 source-ABI and L5 graph layers were ingested with a non-empty
entity set), and `abicheck compare` the merged baseline against itself (asserting
a clean verdict). This proves a plugin-emitted pack is *consumable by the
ordinary scan*, not merely entity-equivalent to the clang backend.

### C.7 — Non-goals / limitations

- **Compiler coverage:** clang only. GCC (`-fdump-lang-class`/`-fdump-tu`) and
  MSVC remain documented fallbacks via the wrapper; a small normalizer to
  `source_facts` is out of scope here.
- **AST-subtree hashes:** `type_hash` (records/enums) and `body_hash`
  (inline/template bodies) are **implemented** by dumping the subtree with
  clang's own JSON dumper in-process and porting `clang.py`'s canonicalization
  (C.2); the C.6 CI matrix proves parity per clang version. The one residual is a
  **floating-point (or fixed-point) literal's textual value inside a hashed
  subtree**: clang's JSON emits an *approximate* numeric `value`
  (`getValueAsApproximateDouble()`) whose shortest-round-trip textual form the
  plugin reproduces only best-effort. This is self-consistent within the producer,
  so under D0 (both baselines produced the same way) it never yields a false
  finding; only the cross-producer C.6 gate can surface it. Should a dump fail at
  runtime, the entity is still emitted without the subtree hash (partial, never
  wrong) plus a diagnostic.
- **Macros:** macro parity is delivered by in-compile `PPCallbacks` (`MacroCollector`
  in `AbicheckFactsPlugin.cpp`, registered on the preprocessor in
  `CreateASTConsumer`/`ParseArgs`), never a second `-E -dD` pass — a companion
  preprocess would reintroduce exactly the extra front-end pass Flow C exists to
  avoid (C.2/C.3). Captured macro values are normalized to match
  `macros_from_preprocessor` so the plugin stays entity-equivalent to the clang
  backend it substitutes; the C.6 gate covers macros (leniently — operator-adjacent
  spacing is the documented soft edge). A project that still hits a macro mismatch
  runs Flow A/B for **both** sides of the comparison rather than mixing producers —
  the sanctioned plugin↔clang-backend equivalence of D0, not a licence to mix
  arbitrary producers.
- **Public-surface classifier (pragmatic):** the plugin classifies a decl/macro
  as public by matching its declaring file's path segments against the
  `public-roots` set (with a `SourceManager::isInSystemHeader` guard so stdlib
  headers reached through a coincidental path segment like `include` do not
  leak). This is an approximation of `clang.py`'s include-spelling model
  (`build_public_set`/`classify_origin`), not a byte-port. It agrees with the
  backend for the common `-Iinclude` layout (the C.6 gate passes), but two
  configurations are known to diverge until the full matcher is ported: public
  headers reached via a **system include path** (`-isystem`, CMake
  `SYSTEM PUBLIC`) are dropped by the system-header guard even though they are
  explicitly public, and exact-file public roots given from a **different tree**
  than the compile's are matched only by segment-subsequence. A project hitting
  either runs Flow A/B for both sides of the comparison.
- **Compiler-implicit special members:** the plugin does **not** emit
  compiler-*implicit* (never user-declared) special members — the default/copy/
  move constructors, destructor, and assignment operators a class gets for free.
  `RecursiveASTVisitor::shouldVisitImplicitCode()` is left at its default
  (false), so implicit members are not traversed. The clang backend, walking
  clang's JSON, *does* emit those it finds materialized in the TU (e.g. a public
  API that returns a record by value odr-uses its copy/move ctor), so a header
  exposing such a record shows a benign cross-producer MISSING on the C.6 gate.
  Matching it is a deliberate non-goal: it would require visiting all implicit
  code and then filtering to exactly the set the wrapper's invocation
  materialized — a set that depends on the capture point (the plugin runs
  post-codegen, `AddAfterMainAction`; the wrapper does not), so parity would be
  fragile rather than exact. Under D0 (same-producer baselines) the implicit
  surface is identical on both sides, so it never yields a false finding; a
  project needing implicit-member facts in a cross-producer comparison runs
  Flow A/B for both sides. `= default`-ed members are user-declared (not
  implicit) and *are* emitted normally.

### C.8 — Canonical fact-set identity and coverage honesty

**One canonical fact set, explicitly versioned — never a user-selectable
collection mode.** The plugin (and the reference `clang.py` wrapper extractor)
always collects the complete mandatory family list for its declared fact-set
version; there is no `--minimal`/`--types-only`/`--no-macros`/`--skip-*` flag,
and there will not be one. A build that wants less evidence simply does not
enable the plugin for that target (see "Producer selection" below and the
deployment guidance in the plugin `README.md`) — the collector itself has one
profile.

Every `SourceAbiTu` record (and the plugin's `manifest.json`) carries:

```json
"fact_set": {
  "name": "abicheck-clang-canonical",
  "version": 1,
  "producer": "abicheck-clang-plugin",
  "producer_version": "0.4",
  "compiler_family": "clang",
  "compiler_version": "18.1.3"
}
```

`SOURCE_ABI_FACT_SET_NAME`/`SOURCE_ABI_FACT_SET_VERSION`
(`buildsource/source_abi.py`) are the single source of truth both producers
stamp from (`default_fact_set()`); the plugin's `kFactSetName`/
`kFactSetVersion` C++ constants are a literal mirror, kept in sync by comment.
`fact_set.version` describes the *semantic contract* (the mandatory family
list) — it is bumped only when that list changes, never for a
performance/producer change (those bump `producer_version` instead).

Each TU also carries per-family **coverage** — `complete` /
`empty-confirmed` / `partial` / `unsupported` / `failed`
(`buildsource.source_abi.COVERAGE_STATES`), derived by
`coverage_state_for_family()`'s pure decision table so every producer reports
it the same way:

```json
"coverage": {
  "functions": "complete", "variables": "complete", "types": "complete",
  "macros": "complete", "templates": "complete", "inline_bodies": "complete",
  "constexpr_values": "complete",
  "source_edges": "unsupported", "read_files": "unsupported"
}
```

The plugin derives this from its **existing** per-declaration "JSON dump
failed" diagnostics (no new state threading through the visitor): a family
with such a diagnostic and at least one collected entity is `partial`; with
the diagnostic and zero entities, `failed`; otherwise `complete` (entities
present) or `empty-confirmed` (none — collection still ran). `source_edges`
and `read_files` are `unsupported` for the plugin (neither is collected yet)
but `read_files` is `complete`/`empty-confirmed` for the `clang.py` wrapper
(which already resolves every read file, D8) — an honest, producer-specific
capability difference rather than an empty array that looks identical to
"nothing changed here".

`buildsource/fact_set.py` implements the comparison-compatibility rules over
these fields: `rollup_fact_set()`/`rollup_coverage()` fold per-TU records up
to the linked `SourceAbiSurface.coverage["fact_set"]`/`["fact_family_states"]`
(`source_link.link_source_abi`, worst-coverage-wins per family), and
`check_fact_set_compatibility()` flags a `fact_set.version` mismatch (error)
or a `compiler_family`/`producer` mismatch (warning — opaque body/template
hashes are producer-specific, C.7). `source_diff.diff_source_abi` calls this
via `_diff_fact_coverage()`, emitting `SOURCE_FACT_COVERAGE_INCOMPLETE`
(RISK) when there is something to report — an incompatible fact-set pairing,
or a mandatory family rolled up `partial`/`failed` on either side — so an
absent L4 finding for that family is never silently read as "unchanged".
Silent (as before C.8) when *neither* side has ever populated this metadata,
so existing baselines and hand-built fixtures are unaffected.

`abicheck inputs validate <pack>` (`buildsource/inputs_validate.py`) runs the
same checks **before** an authoritative merge: manifest validity, fact-set
version, duplicate TU identities, incomplete mandatory-family coverage, and
empty-public-surface detection — 0 clean / 1 warnings / 2 errors / 64 not a
readable pack, so a CI evidence-production job can fail closed on a
mis-collected pack instead of a much-later confusing missing finding.

---

## Producer selection

```text
Can you change the build at all?
 └─ No  ───────────────────────────────► Flow A (full scan). Default. Zero integration.
 └─ Yes, and it's a large template-heavy
    build where a companion parse hurts,
    and you own the toolchain image ─────► Flow C (plugin). Zero extra parse.
 └─ Yes, otherwise ───────────────────── ► Flow B (wrapper). Exact context, portable.
```

Because all three share the D0 contract, this is a spectrum, not a fork: start at
A, graduate to B or C when parse cost bites, and mixed fleets are fine — different
targets in one release may use different flows and still `merge` into one
baseline.

---

## The shared consumer: `abicheck_inputs/` and `merge`

Flows B and C drop a self-describing pack next to the binary; Flow A feeds the
same linker in-process. The pack (ADR-035 D5) is:

```text
abicheck_inputs/
  manifest.json               # kind: abicheck_inputs, library/version, created_by
  binary/…  headers/…         # shipped artifact + public headers (dumped separately, L0–L2)
  build/compile_commands.json # optional → L3 build evidence
  source_facts/*.jsonl        # THE PAYLOAD — normalized SourceAbiTu, one per line → L4/L5
  raw_ast/…  pp/…  deps/…     # optional, forensic only, NEVER ingested
```

`abicheck merge libfoo.so.json ./abicheck_inputs/` auto-detects the pack
(`is_inputs_pack()` → `kind: abicheck_inputs`) and routes it to
`ingest_inputs_pack()`: **pure parsing**, no compiler. It reads
`source_facts/*.jsonl` → L4 surface (`link_source_abi`), the optional compile DB
→ L3, folds the L5 graph, and embeds the result. Third-party packs are guarded
(pack-root path constraint, symlink-escape safe, per-record skip-with-diagnostic).

---

## Consequences

**Positive.**
- The full end-to-end story of working with sources is documented as one
  interchangeable family; the cheapest path (Flow A, no injection) is explicitly
  first-class.
- The plugin has a complete, testable build-to spec — hashing recipe, mangled-name
  rule, visibility model, output layout, versioning, and a differential
  conformance gate — instead of an open TODO.

**Negative / costs.**
- Flows B and C add build-time cost and CI wiring on the **product** side; Flow A
  moves that cost to the analysis host. There is no free parse — the ADR only
  lets a team choose *where* to pay it.
- The plugin carries real maintenance burden: rebuilt per LLVM major, and its
  differential test must track any change to `clang.py`'s field/hash mapping (the
  recipe the plugin mirrors — *not* `base.py`, which is castxml). It stays
  optional for exactly this reason.

**No new `ChangeKind`s, no schema bump.** This ADR governs *producers*; the
`SourceAbiTu`/`abicheck_inputs` contract is unchanged (ADR-035 D5), so old
baselines and readers are unaffected.

---

## Relationship to other ADRs

- **ADR-035 (D5)** — introduced Flow 1/Flow 2 and the `abicheck_inputs/` protocol;
  this ADR expands them into the three-flow family and specifies the plugin.
- **ADR-028 (D3/D6)** — authority rule + non-executing ingest; unchanged.
- **ADR-030** — `SourceAbiTu`/`SourceEntity` schema; `clang.py`
  (`source_abi_from_clang_ast`) is the recipe the plugin mirrors, `base.py` the
  castxml recipe.
- **ADR-032** — extractor action/security model; producers of the same normalized
  facts, ingested by the non-executing `inputs_pack` path.
- **ADR-033** — replay scopes, per-TU caching, and CI cost model that bound Flow
  A's parse cost to changed scope.
- **ADR-037** — the `--depth`/`--ast-frontend` CLI dials that drive Flow A.
