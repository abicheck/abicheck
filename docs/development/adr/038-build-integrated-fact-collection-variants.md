# ADR-038: Build-Integrated Source-Fact Collection — Three Producer Variants

**Date:** 2026-07-01
**Status:** Proposed. Formalizes and extends ADR-035 D5 (Flow 1 / Flow 2). Two of
the three variants already ship (Variant A — replay; Variant B — `abicheck-cc`
wrapper); Variant C (the Clang plugin) exists as a reference skeleton
(`contrib/abicheck-clang-plugin/`) whose AST visitor is not yet implemented. This
ADR pins the shared contract all three obey and makes the **no-injection** path a
first-class, documented default rather than an implicit fallback.
**Decision maker:** (pending)

---

## Context

Turning a library's C/C++ **headers and sources** into abicheck's normalized
source-ABI model (L4 `SourceAbiSurface`, L5 graph) means parsing translation
units with a front end (castxml or `clang -ast-dump=json`). For template-heavy
C++ this is the single most expensive thing abicheck can do: one TU's clang JSON
AST can be **multi-GiB** (the reason `source_replay.py` carries RAM-aware worker
throttling, AST-spill-to-tempfile, and an opt-in process pool).

ADR-035 D5 established two ways to pay that cost — *Flow 1* (abicheck runs the
replay) and *Flow 2* (the build emits normalized facts). It also introduced the
`abicheck_inputs/` artifact protocol (`buildsource/inputs_pack.py` ingest side,
`buildsource/inputs_emit.py` producer side) and two Flow-2 producers: the
portable `abicheck-cc` wrapper (shipped) and a Clang plugin (skeleton only,
`contrib/abicheck-clang-plugin/`).

Two gaps remain, and this ADR closes them:

1. **The plugin variant is undocumented as an operational choice.** ADR-035 D5
   mentions it as a "performance optimization" but never says *whose* build runs
   it, *whose* CI builds the `.so`, or how it is wired next to the wrapper. A
   maintainer choosing between producers has no decision record.

2. **The "inject nothing" path is not stated as first-class.** The cheapest
   integration for most consumers is to change *nothing* about their build and
   let abicheck replay from the `compile_commands.json` the build already emits.
   Today that is implicit in Flow 1; it should be an explicit, supported,
   equally-blessed variant so a team can adopt source-aware checks with **zero**
   build-pipeline changes and graduate to injection only if cost demands it.

All three variants must be interchangeable at the consumer: whatever produced the
facts, `abicheck merge` + `abicheck compare` behave identically. That
interchangeability is the whole point — it lets a project start at zero
integration and move along the spectrum without touching its verdict tooling.

---

## Decision

Support **three producer variants** on one shared contract. They differ only in
*where the parse happens* and *how much the product build must change*; they all
converge on the same normalized `source_facts/*.jsonl` (`SourceAbiTu` schema,
`buildsource/source_abi.py`) and the same consumer path.

| # | Variant | Build change | Extra parses | Portability | Whose CI runs it | Status |
|---|---------|--------------|--------------|-------------|------------------|--------|
| **A** | **No injection — replay** (`dump --sources` / `compile_commands.json`) | **None** | 1, run by **abicheck** post-build | Highest (any compiler, any build) | abicheck's job, reading a build artifact | ✅ Shipped (Flow 1) |
| **B** | **Wrapper injection** (`abicheck-cc CC/CXX`) | Set `CC`/`CXX` | 1 companion, run **in the build**, exact per-TU flags | High (wraps any compiler) | The product's build job | ✅ Shipped (Flow 2) |
| **C** | **Plugin injection** (`-fplugin=libabicheck-facts.so`) | Add a clang flag | **0** — rides the AST clang already built | Clang-only, version-pinned | The product's build job | ⚠️ Skeleton — this ADR's implementation target |

**The invariant (D0).** All three emit byte-compatible `SourceAbiTu` records and
drop a conformant `abicheck_inputs/` pack (or, for Variant A, feed the same
`link_source_abi` linker in-process). The consumer — `abicheck merge <binary>.json
./abicheck_inputs/` then `abicheck compare` — is identical and unaware of which
variant ran. The binary dump (L0–L2) stays artifact-authoritative for shipped-ABI
verdicts; source facts add L3/L4/L5 explanation and source-level
(`API_BREAK`/`RISK`) findings and never delete an artifact-proven break (ADR-028
D3 authority rule).

---

## Variant A — No injection (replay from `compile_commands.json`)

**The straight way: change nothing in the build.** Almost every modern C/C++
build can emit a `compile_commands.json` (CMake `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`,
Bazel/Ninja compdb, Bear over Make). abicheck reads that **post-build** artifact
and replays the selected TUs itself with their recorded flags. Nothing is
injected into the compile; the build is untouched and unaware.

This is Flow 1 (ADR-035 D5, already shipped: `buildsource/inline.py`
`collect_inline_pack`, `source_replay.py`). ADR-035 D6.1 / ADR-032 (amended) even
make the compile DB *zero-config*: `--sources <tree>` alone will auto-discover a
`compile_commands.json` or run abicheck's own fixed, authored build-system query
to produce one (`build_query.py`), so a consumer often needs no compile-DB step
at all.

**Userflow:**

```bash
# The build ran normally and left a compile_commands.json (or none — abicheck
# can infer one). No wrapper, no plugin, no build edit.
abicheck dump libfoo.so --sources ./src -o libfoo.baseline.json
#         ^ binary L0–L2        ^ abicheck replays TUs → L4/L5, in-process

abicheck compare libfoo.old.baseline.json libfoo.new.baseline.json
```

Or split producer/consumer across machines by materializing the pack:

```bash
abicheck collect --sources ./src --build-info ./build -o abicheck_inputs/
abicheck merge libfoo.so.json ./abicheck_inputs/ -o libfoo.baseline.json
```

- **Cost:** one parse per in-scope TU, paid **by abicheck**, not the build. Scope
  is controllable (`--depth`, changed-path/PR localization, per-TU cache) so a PR
  run parses only what changed — it is *not* forced to re-parse the whole tree.
- **When to use:** the default. Any project unwilling or unable to alter its
  build; open-source consumers; first adoption. Recommended starting point.
- **Limitation:** abicheck must have a compatible front end (castxml/clang) on the
  analysis host, and it pays the parse cost that Variants B/C amortize into the
  build. That cost is bounded by scope, not by whole-tree size.

---

## Variant B — Wrapper injection (`abicheck-cc`)

Prefix the real compiler with `abicheck-cc` (`abicheck/cc_wrapper.py`, shipped).
The wrapper runs the real compile pass-through (preserving its exit code), then
**best-effort** extracts one `SourceAbiTu` per source TU using that TU's *exact*
flags/macros and appends it to `abicheck_inputs/`. Fact extraction never fails the
build (a missing front end or parse error degrades to a warning — ADR-028 D3).

**Userflow (in the product's build job):**

```bash
export ABICHECK_INPUTS_DIR=abicheck_inputs
export ABICHECK_CC_HEADERS=include        # public-header roots
export ABICHECK_CC_LIBRARY=libfoo

make CC='abicheck-cc gcc' CXX='abicheck-cc g++'   # or cmake -DCMAKE_CXX_COMPILER=...

abicheck merge libfoo.so.json ./abicheck_inputs/ -o libfoo.baseline.json
```

- **Cost:** one *companion* parse per TU, paid inside the build. More than
  Variant C, but with exact per-TU build context and no version-pinned artifact.
- **Portability:** wraps **any** compiler (gcc/clang/MSVC via the launcher);
  build-system agnostic (anything that honours `CC`/`CXX`).
- **When to use:** you control the build invocation and want exact-build-context
  facts (correct macros/flags per TU) without abicheck re-deriving them, but can't
  or won't pin a Clang-plugin `.so` to your toolchain.
- **Configuration** is entirely by environment (argv-transparent):
  `ABICHECK_INPUTS_DIR`, `ABICHECK_CC_EXTRACTOR`, `ABICHECK_CC_HEADERS`,
  `ABICHECK_CC_LIBRARY`, `ABICHECK_CC_VERSION`, `ABICHECK_CC_DISABLE`.

---

## Variant C — Plugin injection (`-fplugin`) — the implementation target

Load an abicheck Clang plugin during the normal compile
(`contrib/abicheck-clang-plugin/`). It emits the *same* `source_facts` schema from
the AST **Clang already built for the real compile** — so there is **zero** extra
front-end pass. This is the fastest producer and the strategic answer for
large/template-heavy builds, because the fact stream falls out of a compile the
project was already running.

**Userflow (in the product's build job):**

```bash
clang++ -std=c++17 -Iinclude \
  -fplugin=./libabicheck-facts.so \
  -fplugin-arg-abicheck-facts-out=abicheck_inputs \
  -c src/foo.cpp -o foo.o
# real compile only; abicheck_inputs/source_facts/foo.cpp.<hash>.jsonl appended

abicheck merge libfoo.so.json ./abicheck_inputs/ -o libfoo.baseline.json
```

- **Cost:** ~0 extra — a RecursiveASTVisitor walk over the in-memory AST, no
  second parse.
- **Portability:** Clang-only, and the `.so` is **ABI-locked to its LLVM
  version** — a plugin built against LLVM *N* only loads into that `clang`. This
  is why it is `contrib/` reference and **never gated in abicheck's own CI**.
- **When to use:** a large, template-heavy build where the Variant-B companion
  parse is measurably expensive **and** you own the toolchain image (so you can
  build the plugin once against your pinned clang).

**Implementation contract (the gap to close).** The plugin's `FactsVisitor`
(`AbicheckFactsPlugin.cpp`) must produce records **byte-compatible** with the
wrapper by mirroring `buildsource/source_extractors/base.py::entity_from_*`:

| `SourceEntity` field | Clang AST source |
|---|---|
| `id`, `kind` | Decl kind (function/method/record/enum/typedef/union/variable/macro/template/inline/constexpr) |
| `qualified_name` | `NamedDecl::getQualifiedNameAsString()` |
| `mangled_name` | `MangleContext::mangleName()` |
| `signature_hash` | canonical param/return type signature hash |
| `body_hash` | inline/template body token hash |
| `value` | normalized macro / `constexpr` value / default-argument string |
| `source_location {path,line,origin}` | `SourceManager` → PUBLIC/PRIVATE/SYSTEM/GENERATED classification |
| `visibility`, `api_relevant` | visibility attribute + public-header origin |

Records are wrapped in the `SourceAbiTu` envelope in `HandleTranslationUnit` and
appended one-JSON-object-per-line, per-TU filename (mirror `facts_filename()`) to
avoid parallel-build races. **Validation** is a *differential conformance test*:
compile a fixture header under Variant C and Variant B and assert the two
`source_facts` streams are equal. The plugin is valid only if it is a drop-in for
the wrapper. This test runs only where a matching clang is available (an
`integration`-style marker), never as a required abicheck-CI gate.

---

## Producer selection

```
Can you change the build at all?
 └─ No  ───────────────────────────────► Variant A (replay). Default. Zero integration.
 └─ Yes, and it's a large template-heavy
    build where the companion parse hurts,
    and you own the toolchain image ─────► Variant C (plugin). Zero extra parse.
 └─ Yes, otherwise ───────────────────── ► Variant B (wrapper). Exact context, portable.
```

Because all three share the D0 contract, this is a **migration path, not a
lock-in**: adopt at Variant A with no build changes, and if/when a PR-tier or
nightly replay becomes the bottleneck, switch on the wrapper or plugin in the
build with **no change** to the `merge`/`compare` side or to stored baselines.
Mixed fleets are fine — different targets in one release may use different
variants and still `merge` into one baseline.

---

## Consequences

**Positive.**
- The cheapest adoption (Variant A) is explicitly first-class: source-aware
  checks with zero build-pipeline changes.
- One documented spectrum from "inject nothing" to "zero-cost in-compiler,"
  selectable by cost/control without changing the consumer or the stored format.
- The plugin gets a concrete, testable implementation contract (byte-compatible
  with the wrapper, differential-tested) instead of an open TODO.

**Negative / costs.**
- Variants B and C add build-time cost and CI wiring on the **product** side;
  Variant A moves that cost to the analysis host instead. There is no free parse —
  the ADR only lets a team choose *where* to pay it.
- Variant C carries a real maintenance burden: the `.so` must be rebuilt per LLVM
  version, and the differential conformance test must track any change to the
  Python extractor's field/hash mapping. It stays optional precisely for this
  reason.

**No new `ChangeKind`s, no schema bump.** This ADR is about *producers*; the
`SourceAbiTu`/`abicheck_inputs` contract is unchanged (ADR-035 D5), so old
baselines and readers are unaffected.

---

## Relationship to other ADRs

- **ADR-035 (D5)** — introduced Flow 1 / Flow 2 and the `abicheck_inputs/`
  protocol. This ADR formalizes the three producer variants under it and elevates
  the no-injection path to first-class.
- **ADR-028 (D3/D6)** — authority rule (source facts never delete an
  artifact-proven break) and non-executing-ingest discipline; unchanged.
- **ADR-030** — `SourceAbiTu`/`SourceAbiSurface` schema every variant emits.
- **ADR-032** — extractor action/security model; the wrapper/plugin are producers
  of the same normalized facts, ingested by the non-executing `inputs_pack` path.
- **ADR-033** — CI rollout, replay scopes, and caching that bound Variant A's
  parse cost to changed scope.
