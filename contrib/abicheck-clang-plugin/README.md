# abicheck Clang plugin (`abicheck-facts`)

> Status: **optional optimization**, reference implementation (ADR-038 Flow C).
> Not built or gated in abicheck CI — it is ABI-locked to the loading clang's
> LLVM major (ADR-038 C.5). The supported portable producers are Flow A
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

### Coverage (ADR-038 C.7)

Implemented and matching `clang.py`, validated by the C.6 CI matrix:

- **functions/methods/ctors/dtors** — `id`, `qualified_name`, `mangled_name`
  (mangled-name rule: a mangled name equal to the plain name is left empty so
  `identity()` falls back to `qualified_name#signature_hash`), `signature_hash`
  from `type.qualType`, and default-argument `value`;
- **inline bodies** — `body_hash` = subtree hash of the `CompoundStmt`;
- **records / enums** — `type_hash` = subtree hash (definitions only);
- **function / class templates** — `body_hash` = subtree hash of the whole
  template node (members of a class template are *not* re-emitted — no descent);
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

```bash
cmake -S . -B build -DCMAKE_PREFIX_PATH="$(llvm-config --cmakedir)/.."
cmake --build build            # -> libabicheck-facts.so
```

## Use

Pass plugin arguments with the **`-Xclang -plugin-arg-abicheck-facts -Xclang
<arg>`** cc1 form, not the `-fplugin-arg-abicheck-facts-<arg>` shorthand: the
shorthand mis-parses the *hyphenated* plugin name (clang splits it at the first
hyphen and hands `out=…` to a plugin named `abicheck`; verify with `clang++
-###`). `public-roots=` is **mandatory** — it is the plugin's equivalent of the
wrapper's `ABICHECK_CC_HEADERS`; without it every decl classifies non-public and
the plugin emits an empty public surface.

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
