# abicheck Clang plugin (`abicheck-facts`)

> Status: **optional optimization**, implemented (ADR-038 Flow C). Built and
> validated by the dedicated `clang-plugin` workflow across LLVM/Clang 16/17/18
> (the C.6 differential-conformance test), but **never a required gate in the
> main abicheck CI** — it is ABI-locked to the loading clang's LLVM major
> (ADR-038 C.5). The supported portable producers remain Flow A
> (`abicheck dump --sources` / `collect` + `compile_commands.json` replay) and
> Flow B (the `abicheck-cc` compiler wrapper, `abicheck/cc_wrapper.py`). See
> `docs/development/adr/038-build-integrated-fact-collection-variants.md`.

A Clang plugin that, **during a normal compile**, emits abicheck's normalized
source facts (`source_facts/*.jsonl`) directly from the AST Clang already
built — removing the second front-end pass the `abicheck-cc` wrapper otherwise
runs (ADR-038 Flow C: **zero** extra parse). The output is the **same
`abicheck_inputs/` protocol** abicheck ingests via `merge`, so the plugin is a
drop-in faster producer, never a new format.

## Why it is optional

Clang plugins are compiler-version-sensitive: a plugin built against LLVM N must
match the `clang` that loads it. That is why abicheck does **not** require it —
Flow A/B are the portable, supported paths. Reach for the plugin only when the
second-frontend cost is measurable on a large template-heavy build and you own
the toolchain image (ADR-038 producer-selection tree).

## What it emits

One JSON object per translation unit, appended to
`<out>/source_facts/<stem>.<sha256(source)[:12]>.jsonl` (a per-TU, race-free
filename so parallel `-j` compiles never share a file, mirroring
`inputs_emit.facts_filename`). Each line matches
`abicheck.buildsource.source_abi.SourceAbiTu` (the canonical schema).

**Reference recipe (ADR-038 C.2).** Because the plugin reads the *clang* AST,
its reference is `abicheck/buildsource/source_extractors/clang.py`
(`source_abi_from_clang_ast`) — **not** `base.py`, which is the castxml recipe.
Field and hash construction mirror `clang.py` so the emitted records are
entity-equivalent to the clang backend the plugin substitutes (the C.6
differential-conformance gate). The content hash is
`_hash(*parts) = "sha256:" + hex(sha256(parts joined by "\x00"))`.

Per `SourceEntity`: `id`, `kind`, `qualified_name`, `mangled_name`,
`signature_hash`, `value`, `source_location {path,line,origin}`, `visibility`,
`api_relevant`, `confidence`.

### Fact-set identity and per-family coverage (ADR-038 C.8)

Every TU record (and `manifest.json`) carries a `fact_set` block —
`{name: "abicheck-clang-canonical", version, producer, producer_version,
compiler_family, compiler_version}` — and a `coverage` block reporting one of
`complete`/`empty-confirmed`/`partial`/`unsupported`/`failed` per fact
family (`functions`, `variables`, `types`, `macros`, `templates`,
`inline_bodies`, `constexpr_values`, `source_edges`, `read_files`). This is
**not** a collection mode: every family is always attempted; the state only
records what happened, derived from the same per-declaration "JSON dump
failed" diagnostics already described below. `source_edges`
(`DECL_CALLS_DECL`/`DECL_REFERENCES_DECL`/`DECL_HAS_TYPE`/
`TYPE_HAS_FIELD_TYPE`/`TYPE_INHERITS`) and `read_files` are collected during
the same AST walk/compile as every other family (a small `CallRefVisitor`
sub-walk per function body, and `SourceManager::fileinfo_begin()/end()`
respectively) — no second frontend pass.

`abicheck merge` compares `fact_set`/`coverage` across the old/new baselines
(`buildsource/fact_set.py`) and emits `SOURCE_FACT_COVERAGE_INCOMPLETE`
(RISK) when the two sides' fact-set versions/producers are incompatible or a
mandatory family was incomplete on either side — so a missing L4 finding is
never silently read as "nothing changed there". Validate a pack's coverage
*before* merging with:

```bash
abicheck inputs validate ./abicheck_inputs/
```

See ADR-038 C.8 for the full design.

### Coverage (ADR-038 C.7)

Implemented and matching `clang.py`, validated by the C.6 CI matrix:

- **functions/methods/ctors/dtors** — `id`, `qualified_name`, `mangled_name`
  (mangled-name rule: a mangled name equal to the plain name is left empty so
  `identity()` falls back to `qualified_name#signature_hash`), `signature_hash`
  from `type.qualType`, and default-argument `value`;
- **inline bodies** — `body_hash` = subtree hash of the `CompoundStmt`;
- **records / enums** — `type_hash` = subtree hash (definitions only);
- **function / class templates** — `body_hash` = subtree hash of the whole
  template node; public class-template member methods are also emitted as
  declaration patterns such as `Box<T>::get`, so concrete binary instantiations
  can link back to source evidence without guessing;
- **identity/provenance evidence** — entities carry additive `names`,
  `relations`, and `ownership` dictionaries; the plugin fills Clang USR /
  canonical USR when available, and both producer paths stamp template-owner and
  public-root ownership hints for later policy/matching layers;
- **external-linkage variables** — namespace-scope globals and `static` data
  members (which become exported `OBJECT` symbols) are emitted as `variable`
  entities keyed on mangled name + type, so a binary data export maps back to a
  source decl. Internal-linkage namespace/file-scope `static`s and stack locals
  are dropped (no symbol); `constexpr` variables ride the `constexpr` path;
- **typedefs / type-aliases** — `type_hash = _hash("typedef-target",
  underlying)`, `value = underlying`;
- **constexpr variables** — literal *and* computed initializers (a computed
  initializer's `value` is its subtree hash, as in `clang.py`);
- **macros** — captured **in-compile** via `PPCallbacks`, never a second
  `-E -dD` pass; include guards dropped, non-public/system macros filtered;
- **visibility / api_relevant** — public-header classification against the
  `public-roots` set, with inherited access threaded so a public member of a
  private nested class is dropped.

**How the subtree hashes reach parity.** `clang.py`'s `_subtree_hash` hashes a
canonicalized form of clang's *JSON* AST. Rather than hand-reproduce that JSON,
the plugin serializes the subtree with **clang's own JSON dumper in-process**
(`Decl::dump(os, false, ADOF_JSON)` — the exact `-ast-dump=json` path) and ports
`clang.py`'s `_alpha_rename_map`/`_canonical`/`_subtree_hash` onto it. Because the
wrapper's clang backend consumes the same clang JSON, the hashes match by
construction for a given clang version — which is what the C.6 matrix verifies.
No second parse is added: the dump reads the AST clang already built.

**Pruned parse (perf).** clang's JSON dumper emits full location/range/type/flag
detail for every node, but `_canonical` keeps only a small fixed key set (`kind`,
`name`, `value`, `opcode`, `castKind`, `type.qualType`/`desugaredQualType`,
`referencedDecl.id`/`name`, `id`, `storageClass`, `mangledName`, `init`, and the
recursive `inner`). Feeding all of clang's output through `llvm::json::parse` and
then discarding ~90% of it dominated the cost — on a template-heavy TU the dumper
emits ~200 MB of JSON and the reparse was ≈68% of the subtree-hash time. The
plugin now parses that text with a **pruned parser** (`PrunedJsonParser`) that
walks only the structure and delegates every kept leaf token to
`llvm::json::parse`, so scalar/escape/number semantics — and therefore every
emitted hash — are byte-identical to a full parse, while the discarded keys cost
only a linear character skip. Measured on a from-scratch LLVM 18.1.3
`LLVMSupport`+`LLVMDemangle` build (143 TUs, 4 cores): the plugin's compile-time
overhead dropped from **3.44× → 2.39×** (tax 82 s → 47 s) with byte-for-byte
identical `source_facts` and a green C.6 gate. Set `ABICHECK_PLUGIN_PROFILE=1` to
print the per-TU dump/parse/canonicalize split to stderr; set
`ABICHECK_PLUGIN_PROFILE_LOG=<path>` alongside it to append that line to a
file instead (useful once many parallel compiles would otherwise interleave
the summaries unreadably on stderr) — the choice of sink never touches
`source_facts` output (execution-policy invariance, ADR-038 C.9).

The one documented residual is a **floating-point literal's textual value inside
a hashed subtree** (`pyFloat`): clang's JSON emits an approximate numeric value
whose shortest-round-trip form is reproduced only best-effort. It stays
self-consistent within the producer, so under D0 it never yields a false finding;
only the cross-producer C.6 gate can surface it. If a JSON dump fails at runtime,
the entity is emitted without the subtree hash (partial, never wrong) + a
diagnostic.

Raw AST dumps (`raw_ast/`) are **forensic only** — abicheck does not ingest
them (ADR-035 D5); the plugin normalizes to `source_facts` itself.

## Build

The CMake build needs the Clang development package for the same LLVM major as
the `clang` that will load the plugin. On Debian/Ubuntu that means the full
`libclang-XX-dev` package in addition to `clang-XX`/`llvm-XX-dev`; otherwise
CMake can find `ClangTargets.cmake` but fail on missing libraries such as
`libclangBasic.a`.

```bash
# Debian/Ubuntu example for LLVM 18:
sudo apt-get install clang-18 llvm-18-dev libclang-18-dev

cmake -S . -B build -DCMAKE_PREFIX_PATH="$(llvm-config --cmakedir)/.."
cmake --build build            # -> libabicheck-facts.so
```

## Use

Pass plugin arguments with the **`-Xclang -plugin-arg-abicheck-facts -Xclang
<arg>`** cc1 form, not the `-fplugin-arg-abicheck-facts-<arg>` shorthand: the
shorthand mis-parses the *hyphenated* plugin name (clang splits it at the first
hyphen and hands `out=…` to a plugin named `abicheck`; verify with `clang++
-###`). `public-roots=` is the plugin's equivalent of the wrapper's
`ABICHECK_CC_HEADERS` — it scopes which resolved header paths count as the public
surface. It is **strongly recommended** but no longer strictly required: when it
is omitted the plugin auto-derives roots from the compile's own `-I`/`-iquote`
include directories (see below), so a forgotten flag yields a populated surface
instead of a silently empty pack. Pass it explicitly whenever you want to scope
the surface precisely (e.g. only the installed `include/` tree).

```bash
clang++ -std=c++17 -Iinclude \
  -fplugin=./build/libabicheck-facts.so \
  -Xclang -plugin-arg-abicheck-facts -Xclang out=abicheck_inputs \
  -Xclang -plugin-arg-abicheck-facts -Xclang public-roots=include \
  -c src/foo.cpp -o foo.o

# then, exactly as with the wrapper — dump the binary side first, then fold:
abicheck dump libfoo.so -o libfoo.so.json
abicheck merge libfoo.so.json ./abicheck_inputs/ -o libfoo.baseline.json
```

Optional args: `library=<name>` (recorded in the manifest / `target_id`),
`version=<v>`. `public-roots=` is repeatable.

After `merge`, read stderr's L4 coverage line. A healthy pack should report
non-zero public declarations and, when the binary exports symbols, non-zero
symbol matches. `merge` now warns when a pack technically ingests but is unlikely
to help matching, for example:

- public macros/types but no public function or variable declarations;
- public declarations present, but `0/N` exported symbols matched.

Those warnings usually mean the compile unit was internal-only, the pack was
produced for a different target/configuration than the binary, or
`public-roots=` does not match the headers the compiler actually resolved.

### `public-roots` must match how headers *resolve*, not where they are installed

The plugin classifies a declaration as public by the **physical path the
compiler resolved its header to**, then tests that path against `public-roots`.
The trap: your public headers may be installed at `include/pvxs/…`, but if an
earlier `-I` makes `<pvxs/data.h>` resolve to `src/pvxs/data.h`, then
`public-roots=include` matches **nothing** and the pack comes back empty — even
though everything "looks" configured. Include order decides the resolved path,
not the install layout.

Two ways to get it right:

- **Check the resolution** — `clang++ <your -I flags> -H -fsyntax-only x.cpp`
  prints the actual file each `#include` opened; point `public-roots=` at *that*
  directory (e.g. `src/pvxs`), not the installed copy.
- **Trust the diagnostic** — since ADR-038 Flow C the plugin no longer fails
  silently: if `public-roots` matches zero declarations while header decls were
  seen outside the roots, it prints

  ```
  abicheck-facts: public-roots matched 0 declarations for this TU
  (799 header decl(s) were seen outside the root(s) [.../include],
   e.g. .../epicsAssert.h). public-roots must be the directory the compiler
   actually resolves the public headers from (verify with `clang -H`) …
  ```

  and records the same note in the pack's `diagnostics`. An empty pack is now a
  loud error, not a 20-minute debug. A non-empty-but-useless pack is also called
  out later by `abicheck merge` when it has binary exports to match against.

### Auto-derived public roots (when `public-roots=` is omitted)

If you pass no `public-roots=` at all, the plugin derives roots from the
compile's user include search paths — every `-I` (angled) and `-iquote` (quoted)
directory, resolved to an absolute path; compiler/system entries (`-isystem`,
the resource dir, the sysroot) are excluded so libstdc++/SDK headers don't flood
the surface. The plugin then emits a one-time note per pack:

```
abicheck-facts: no public-roots given; inferred 2 public root(s) from the
compile's -I/-iquote include dirs [/proj/include, /proj/gen]. Pass
public-roots=<dir> to scope the public surface precisely.
```

and records it in each TU's `diagnostics`. Inference is scoped to keep the
surface honest: only include dirs **at or below the build working directory** are
used (a third-party `-I/opt/boost/include` is not a public root), and decls
defined in a **translation-unit source** (`.cpp`/`.cc`/…) are excluded even when
an inferred `-I.` root covers them — public API lives in headers. It is still a
convenience, not a replacement for scoping: the inferred surface can be broader
than your true public API (any header reachable through an in-tree `-I` dir), so
for a precise baseline still pass an explicit `public-roots=`. (Explicit roots are
taken verbatim — no source-file/locality filtering — to stay byte-identical to the
`abicheck-cc` wrapper for the C.6 conformance gate.)

## Validation: differential conformance (ADR-038 C.6)

The plugin is correct **iff** it is a drop-in for the **clang** backend. The gate
is `tests/conformance.py`: it compiles one fixture TU both ways with the *same*
clang — the plugin (`public-roots=include`) and the `abicheck-cc` wrapper pinned
to clang (`ABICHECK_CC_EXTRACTOR=clang`, `ABICHECK_CC_HEADERS=include`) — ingests
both packs, and asserts the two surfaces are **entity-equivalent**: equal sets
keyed by `SourceEntity.identity()` with equal
`signature_hash`/`type_hash`/`body_hash`/`value`/`visibility`/`api_relevant`.
Non-macro entities are strict; macro values are lenient (the documented spacing
soft edge).

Run it locally against a built plugin:

```bash
python contrib/abicheck-clang-plugin/tests/conformance.py \
  --plugin build/libabicheck-facts.so --clangxx clang++
```

CI runs it on a **matrix of LLVM/Clang majors** via
`.github/workflows/clang-plugin.yml` (pinning `clang`/`clang++` on `PATH` to each
matrix version, so the plugin and the wrapper's extractor use the identical
clang — the precondition for byte-for-byte parity). It is a standalone,
non-blocking workflow, never a required abicheck-CI gate.

## Compiler fallbacks (documented, not required)

A build that cannot load a Clang plugin can still feed the same
`abicheck_inputs/` protocol:

- **`abicheck-cc` wrapper** (Flow B) — the portable default; wraps any compiler
  and runs the castxml/clang extractor as a companion action. No plugin needed.
- **GCC** — `-fdump-lang-class` / `-fdump-translation-unit` produce class/TU
  dumps; a small normalizer (not shipped) converts them to `source_facts`.
- **MSVC** — no AST plugin ABI; use the `abicheck-cc` wrapper around `cl.exe`, or
  emit `source_facts` from your own tooling.

In every case the *output contract is identical* — the `abicheck_inputs/` pack —
so the ingest (`abicheck merge`) is the same regardless of producer.
