# ADR-038: Working With Sources — Full-Scan and Two Build-Injection Flows, and the Clang Plugin Specification

**Date:** 2026-07-01
**Status:** Proposed. Formalizes and extends ADR-035 D5 (Flow 1 / Flow 2) into a
complete, three-flow producer contract and pins the Clang-plugin specification.
Flow A (full source scan / no injection) and Flow B (the `abicheck-cc` wrapper)
already ship; Flow C (the Clang plugin) exists as a reference skeleton
(`contrib/abicheck-clang-plugin/`) whose AST visitor is unimplemented — this ADR
is its build-to spec.
**Decision maker:** (pending)

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
| **C** | **Plugin injection** (`-fplugin`) | Add a clang flag | **0** — rides the compile's AST | The compile itself | `merge` an `abicheck_inputs/` pack | ⚠️ Skeleton — spec below |

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

- **`subtree_hash`** is the hard part: `clang.py` hashes an **alpha-renamed,
  commutative-operator-normalized, build-root-stripped canonical form of clang's
  *JSON* AST** (`_canonical`/`_alpha_rename_map`/`_subtree_hash`). Reproducing it
  byte-for-byte from the in-memory AST means emitting the same canonical scalar
  keys (`kind`/`name`/`value`/`opcode`/`castKind` + `type.qualType`), the same
  local-binding placeholders (`$0`…), and the same commutative-operand sort. This
  is why `type_hash`/`body_hash` parity is the plugin's genuine engineering risk
  (see C.7).
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
loads into that `clang`. This is why it is `contrib/` reference and **never gated
in abicheck's own CI** — abicheck cannot ship one `.so` for every LLVM. A product
build has a pinned toolchain image, so it builds the plugin once against that
image and injects it.

### C.6 — Validation: differential conformance

The plugin is correct **iff** it is a drop-in for the **clang** backend. The gate
is a differential test: compile a fixture header both ways — the plugin (with
`public-roots=include`), and the wrapper pinned to clang with the same roots
(`ABICHECK_CC_EXTRACTOR=clang`, *not* `auto`, plus `ABICHECK_CC_HEADERS=include`,
so both sides use the recipe *and* the public surface the plugin targets) —
ingest both packs, and assert the two surfaces are **entity-equivalent**: equal
sets keyed by `SourceEntity.identity()`, with equal
`signature_hash`/`type_hash`/`body_hash`/`value`/`visibility`/`api_relevant` per
entity. It runs only where a matching clang is available (an
`integration`/`libabigail`-style marker), never as a required abicheck-CI gate.

### C.7 — Non-goals / limitations

- **Compiler coverage:** clang only. GCC (`-fdump-lang-class`/`-fdump-tu`) and
  MSVC remain documented fallbacks via the wrapper; a small normalizer to
  `source_facts` is out of scope here.
- **AST-subtree hashes:** `type_hash` (records/enums) and `body_hash`
  (inline/template bodies) depend on reproducing `clang.py`'s JSON-AST
  canonicalization from the in-memory AST (C.2) — the plugin's hardest part.
  Declaration-level fields (`id`, `qualified_name`, `mangled_name`,
  `signature_hash` from `qualType`, default-arg `value`, `visibility`) are
  straightforward and match readily; where the plugin cannot yet reproduce a
  subtree hash it emits the declaration without it (partial, never wrong) and
  records a diagnostic — the clang wrapper/full-scan path stays the reference for
  those fields until parity is proven by the C.6 gate.
- **Macros:** macro parity must be delivered by in-compile `PPCallbacks` (C.2),
  never a second `-E -dD` pass — a companion preprocess would reintroduce exactly
  the extra front-end pass Flow C exists to avoid. Until the `PPCallbacks` path is
  implemented, the plugin marks macros unsupported (emits none, records a
  diagnostic); a project needing macro findings runs Flow A/B for **both** sides
  of the comparison (where the `-E -dD` pass is expected), not a Flow-C/Flow-A
  split within one comparison. Once implemented, the plugin must normalize
  captured macro values to match `macros_from_preprocessor` so it stays
  entity-equivalent to the clang backend it substitutes (the C.6 gate covers
  macros) — the sanctioned plugin↔clang-backend equivalence of D0, not a licence
  to mix arbitrary producers.

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
