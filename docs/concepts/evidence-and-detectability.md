# Evidence & Detectability: What Each Method Can and Cannot See

> **One idea drives this whole page:** *different methods observe different
> evidence, and **no single method detects every compatibility issue.*** A tool
> can only report what its inputs let it see. Feed it symbols only and it sees
> symbol changes; feed it debug info and it sees layout; feed it headers and it
> sees source-level API. Some changes (`#define` macros, inline/template
> *bodies*, uninstantiated templates) are invisible to *any* artifact
> comparison.

This page is the conceptual companion to the practical
[Limitations](limitations.md) and [Tool Comparison](../reference/tool-comparison.md)
pages. It answers the question users ask most often:

> "Why did tool A catch this and tool B didn't?"

Almost always, the answer is **evidence**: the two tools were looking at
different inputs.

---

## 0. The five sources of information

A release engineer can hand a compatibility checker up to **five different
sources of information** about a library, ordered from the least to the most.
Each one *adds* facts the previous cannot see; none of them is complete on its
own. abicheck names them with the layer codes `L0`–`L4` used throughout the
docs. You can see which **artifact** layers (`L0`–`L2`) a given input exposes
with `abicheck dump --show-data-sources`; the build/source layers (`L3`/`L4`)
are not reported there — they surface in the pack-aware `compare`
`evidence_coverage` table once you supply an EvidencePack:

| # | Source you provide | Layer | abicheck input | What it newly reveals |
|---|--------------------|:-----:|----------------|------------------------|
| 1 | **Just the binary** | **L0** | a stripped `.so`/`.dll`/`.dylib` | Exported symbols, SONAME/install-name, symbol versions, visibility, binding, `DT_NEEDED`/`LC_LOAD_DYLIB` dependencies |
| 2 | **+ Debug symbols** | **L1** | a `-g` build (DWARF/PDB) or sidecar debug file | Type **layout**: struct/class sizes, field offsets, enum *values*, vtable slots, calling convention, packing/alignment |
| 3 | **+ Public headers** | **L2** | `-H include/` (parsed by castxml) | Source-level **API**: signatures, overloads, access (`public`/`private`), `final`/`explicit`/`noexcept`, templates, declared default args, public/internal **scoping** |
| 4 | **+ Build system data & options** | **L3** | `-p build/` (compile DB, CMake/Ninja/Bazel/Make) | The **flags the library was actually built with**: `-std`, `_GLIBCXX_USE_CXX11_ABI`, `-fvisibility`, `-fabi-version`, toolchain/sysroot, target graph, export maps |
| 5 | **+ Sources** | **L4** | an EvidencePack (per-TU source ABI replay, ADR-030) | Facts that never reach the binary: macro constants, `constexpr` values, default-argument *values*, inline/template **bodies**, uninstantiated templates |

Read this as a staircase: **each step up the table can both *find* breaks the
step below is blind to and *prevent false positives* the step below would
raise.** A struct-field insertion is invisible at L0 but obvious at L1
([case07](../examples/case07_struct_layout.md)); an internal-struct change that
*looks* like a break at L1 is correctly dismissed once L2 headers reveal the
struct is non-public ([case118](../examples/case118_internal_struct_field_added_scoped.md)).

### How they combine

The layers are **independent and additive**, not a fallback chain — abicheck
overlays every source you give it and lets the strongest evidence win, under
one rule (the *authority rule*, see [Evidence Packs](evidence-pack.md)):

> **Artifact-backed evidence (L0/L1/L2) is authoritative for the shipped-ABI
> verdict.** Build/source evidence (L3/L4) *explains, localizes, scopes, or
> adds confidence to* a finding, and can raise source-/API-level findings of
> its own — but it never silently deletes an artifact-proven break.

Concretely: L0 says *a symbol changed*; L1 says *its layout changed by N
bytes*; L2 says *and the public declaration that names it changed too*; L3 says
*and it was built with a different `-std`, so expect churn*; L4 says *and the
macro it expands actually changed value*. The verdict is computed worst-wins
across all of them. The **design** of how the layers are collected and
reconciled is in [Architecture](architecture.md#evidence-layers-the-five-sources);
the per-case evidence each example needs is benchmarked in
[Tool Comparison §Benchmarking by evidence tier](../reference/tool-comparison.md#benchmarking-by-evidence-tier).

> **Best input you can give abicheck:** old + new library, **matching public
> headers**, **debug info**, and the **build's compile database** — L0+L1+L2+L3
> together. With less, abicheck degrades *down the staircase* and tells you
> exactly which layers it had via the `--show-data-sources` / `evidence_coverage`
> report.

### Why call it "evidence"?

First, concretely: "evidence" is just the umbrella term for the **sources of
information** in the table above. The artifact sources are the binary (L0), its
debug info (L1), and its public headers (L2); the additional sources are the
project's **build-system data** (L3 — compile flags, toolchain, target graph),
its **source tree** (L4 — per-TU source ABI replay), and a **source/build graph**
(L5 — include/type/call reachability). When the docs say "build/source evidence
(L3/L4/L5)", that is exactly what they mean.

The umbrella word is a deliberate **forensic metaphor**, not decoration: abicheck
treats "is this compatible?" as something it must **prove from facts**, the way a
case is built from evidence, rather than as a single computation over one data
source. Three properties of evidence are exactly the properties abicheck needs,
and "tier" or "level" would imply the wrong ones:

- **Independent and partial.** Each source contributes *some* facts and none is
  complete on its own — a binary shows symbols but not layout, headers show API
  but not what was actually built. Evidence is **additive and overlaid**, not a
  ranked ladder you fall back down. (Call them "tiers" and readers assume a
  fallback chain; they aren't one.)
- **Different authority.** Just like physical vs. circumstantial evidence in a
  courtroom, not all of it carries equal weight. **Artifact evidence (L0–L2) is
  what was actually built and shipped, so it is *authoritative*** — only it can
  declare a binary `BREAKING`. **Build/source evidence (L3/L4/L5) is
  *corroborating*** — it explains, localizes, scopes, adds confidence, removes
  false positives, and can raise its *own* source-/API-level findings, but it can
  **never overturn or silently delete an artifact-proven break**. This is the
  *authority rule* ([ADR-028](../development/adr/028-source-build-evidence-pack.md)).
- **Honest about what it had.** Because the verdict is only as strong as the
  evidence behind it, every run reports the evidence it actually collected (the
  `evidence_coverage` table and the "checks enabled… and why others are not"
  capability report). The output literally says *"here is the evidence I had, so
  here is what I could and couldn't check."*

So "evidence" + the authority rule is the mental model that lets abicheck keep
*adding* sources for more accuracy without ever letting a weaker source override
a proven break.


---

## 1. The detectability matrix

The most important table on this page. Read it as: *given only this evidence,
what can a checker conclude — and what is it structurally blind to?*

| Evidence available | Detects well | Cannot detect well |
|--------------------|--------------|--------------------|
| **Exported symbol table only** (stripped binary, no headers) | Removed/added exported symbols, symbol versions, visibility, SONAME/install-name, dependency (`DT_NEEDED`) changes | Struct layout, enum values, calling convention, source-only API changes, macro changes, inline/template body changes |
| **Debug info (DWARF / PDB / BTF)** | Type layout, field offsets, enum values, class sizes, vtables, calling convention, packing/alignment | Source-only API *intent*, macros, default arguments, some template/header-only changes |
| **Headers / AST** (CastXML / Clang) | Source signatures, overloads, default args, access/`final`/`explicit`/`noexcept`, templates visible in headers | Inline body *semantics*, macro expansion policy (unless modeled), runtime behavior |
| **Source diff / compiler-based API extraction** | Macros, inline function bodies, `constexpr` bodies, uninstantiated templates, source-level API | The binary layout actually *emitted* into a shipped library (unless paired with the binary/debug info) |
| **Runtime app swap / integration test** | Real loader/linker behavior and tested execution paths | Untested public API, *future* consumers, silent layout corruption (unless a test happens to expose it) |
| **Bundle scan** (multi-library) | Cross-DSO dependency / provider / entry-point problems | Pure source compatibility and semantic behavior not represented in artifacts or manifests |

> The first four rows are exactly the five sources of [§0](#0-the-five-sources-of-information)
> (L0/L1/L2 and the L4 source row); the last two — runtime app swap and bundle
> scan — are *orthogonal* evidence axes, not extra rungs on the staircase.

### Why abicheck combines layers

abicheck is strongest because it does **not** rely on a single row. It overlays
the five **independent, additive** sources of [§0](#0-the-five-sources-of-information)
(see [Architecture](architecture.md#evidence-layers-the-five-sources) and ADR-003 / ADR-028):

| Layer | Source | Evidence it contributes |
|-------|--------|-------------------------|
| **L0** | Binary metadata | ELF symbols, SONAME, versioning, visibility, dependencies (and PE/COFF + Mach-O equivalents) |
| **L1** | Debug info (DWARF/PDB) | Layout, offsets, enum values, calling convention, vtable slots, type cross-checks |
| **L2** | Header AST (CastXML) | Function signatures, classes, structs, vtables, enums, typedefs, templates, `noexcept`, access, public/internal scoping |
| **L3** | Build context | ABI-relevant flags, toolchain/sysroot, target graph, export-policy changes |
| **L4** | Source ABI replay | Macro/`constexpr` values, default-argument values, inline/template bodies, uninstantiated templates |

The best input you can give it is therefore:

> **old library + new library + matching public headers + debug info + build
> context** — L0+L1+L2+L3 together.

With less, abicheck degrades gracefully *down the staircase* — a stripped binary
with no headers collapses toward symbol-only checking, where layout and
source-only breaks are invisible. See
[Recommendation: feed `.so` + debug info + headers](limitations.md#recommendation-feed-abicheck-so-debug-info-headers-for-the-best-result).

---

## 2. Methods compared, by the evidence they use

Each method is good at what its evidence exposes and blind to the rest. None is a
complete contract check on its own.

### a. Build an app and swap the library

The most realistic *consumer-level* test — but **not** a complete contract
check. It only exercises what one app imports and runs.

| Strength | Example |
|----------|---------|
| Loader/linker failures | App fails because a required symbol is missing |
| Real runtime behavior | App crashes when it calls into changed ABI |
| Consumer-specific risk | App doesn't use the removed function, so *this* app still works |
| End-to-end deployment validation | RPATH/RUNPATH, search path, symbol versions all exercised |

| It misses | Why |
|-----------|-----|
| Unused public APIs | The app only tests what it imports/executes |
| Silent data corruption | Tests may pass while layout is subtly wrong |
| Source compatibility | Binary may run, but *recompiling* may fail |
| Future consumers | One app is not the whole public contract |
| Header-only / source-only breaks | Existing binary doesn't exercise changed source |

This maps to abicheck's [`appcompat`](../user-guide/appcompat.md) command. See
[§4](#4-app-mode-consumer-scoped-vs-library-compare-contract-scoped) for its
exact scope.

### b. libabigail (`abidiff`)

Primarily **DWARF-based**: `abidw` extracts ABI XML, `abidiff` compares it. Falls
back to CTF/BTF or ELF symbol names; with no debug info it degrades toward
ELF-only.

- **Good for:** emitted binary ABI from debug builds (struct/class layout, type
  changes, symbols); no headers required in the common DWARF workflow; a mature
  ABI diff model.
- **Limits:** stripped binaries degrade to symbol-only; a header directory is
  mostly a *public-symbol filter*, not a full source-AST analysis, so source-only
  changes (default args, access changes, `noexcept`) stay hard; not
  product/bundle/app-policy oriented by default.

### c. ABI Compliance Checker (ABICC)

Two workflows:

- **`abi-dumper` workflow** — DWARF-based dump from a debug `.so`, optional
  public-header filter. Lacks a full AST, so it misses many source-only API
  breaks.
- **XML / header workflow** — GCC-compiled AST from headers. GCC-only, with
  known slowness/reliability issues, path sensitivity, and timeouts on complex
  C++. Lacks ELF binary metadata, so it's weaker on exported-symbol/platform
  linker facts.

Coarser verdict vocabulary than abicheck `compare` (no `API_BREAK` modeling).
abicheck's [`compat` mode](../reference/tool-comparison.md) is a drop-in
replacement for ABICC-style flags; new integrations should prefer `compare`.

### d. abicheck

The combined-evidence model above (§1). Strongest with library + headers + debug
info + build context. See [Tool Comparison](../reference/tool-comparison.md) for
the benchmark showing why combining ELF + CastXML + DWARF beats single-source
tools.

### e. Methods beyond ABI diff tools

ABI diffing is one tool in a release-engineering kit. Complementary methods:

| Method | What it adds |
|--------|--------------|
| Downstream rebuilds | Detect *source* API breaks by recompiling real consumers |
| Runtime smoke / [probe tests](../user-guide/probe-harness.md) | Detect loader errors and common runtime failures |
| [ABI/API snapshot baselines](../user-guide/baseline-management.md) | Treat release snapshots as immutable contract records |
| Symbol-version script / export-map linting | Enforce the intended public/private boundary |
| Header/source API extraction | Catch macros, inline definitions, template surface |
| Fuzz / integration tests | Catch *behavioral* changes behind a stable ABI |
| Reverse-dependency CI | Ecosystem/distribution-wide validation |
| [Security-hardening scanners](../user-guide/security-hardening.md) | Catch non-ABI deployment regressions (RELRO/PIE/canary/FORTIFY) |

The [security-hardening check](../user-guide/security-hardening.md) is the clean
example of "not ABI, but still a release-compatibility risk": an ABI-compatible
upgrade can weaken hardening while a normal ABI gate stays green. abicheck
reports that as **deployment risk**, not an ABI break.

---

## 3. Traditional shared libraries vs header-only libraries

This distinction trips people up constantly, so it gets its own section.

### Traditional `.so` / `.dll` / `.dylib`

There is a real **binary contract** to compare — exported symbols, symbol
versions, dependency metadata, layout in debug info, public declarations in
headers. abicheck's model is strongest here:

> For compiled shared libraries, ABI compatibility is mainly about whether
> existing, already-built consumers can keep linking, loading, and calling into
> the new binary using the *old* contract.

### Header-only libraries

A header-only library often has **no exported library ABI** — the code is
compiled into *each consumer*. Compatibility is therefore mostly:

| Compatibility type | Meaning |
|--------------------|---------|
| Source API compatibility | Will existing users recompile? |
| Generated ABI compatibility | Will rebuilt objects stay compatible with other objects? |
| Semantic compatibility | Does inline/`constexpr`/template behavior still mean the same thing? |
| Configuration compatibility | Do macros/features/flags produce the same public surface? |

abicheck can still help in *some* cases:

| Case | How abicheck helps |
|------|--------------------|
| Header-only API also gates a shared-library boundary | Header-AST comparison catches some API changes |
| Explicit template instantiations shipped in a `.so` | The emitted instantiations can be checked |
| Header constants / default args / source signatures in the AST | Some source-level API breaks are found |
| App links a runtime helper library | [App mode](../user-guide/appcompat.md) checks the app's imported symbols |

But it **cannot fully validate a pure header-only library**: implicit
header-only template instantiations are not in any shipped artifact (the
documented mitigation is *explicit instantiation* of public templates that form
part of the ABI — see [Template Instantiation](limitations.md#template-instantiation)).

!!! tip "Header-only compatibility strategy"
    Use **source API extraction**, **compile tests** across supported
    compilers/standards, **downstream rebuilds**, and **behavioral tests**. Use
    abicheck for emitted artifacts, explicit template instantiations, or
    companion runtime libraries — not as the sole gate for header-only code.

---

## 4. App mode: consumer-scoped vs library-compare: contract-scoped

[`appcompat`](../user-guide/appcompat.md) answers a deliberately narrow question:
*will **this** application still work with the new library?* It parses the app's
required symbols, compares old/new libraries in full mode, checks new-symbol
availability, and **filters** findings to changes that matter to that app.

That scope cuts both ways:

| App mode **can** say | App mode **cannot** say |
|----------------------|-------------------------|
| "This app doesn't import the removed symbol." | "The library is generally ABI-compatible." |
| "This app needs symbol version X and the new lib lacks it." | "All *future* consumers are safe." |
| "This app is unaffected by this library-wide break." | "Header-only source users can recompile." |
| "This deployment path is OK for this app." | "No *semantic* behavior changed." |

> **App mode is consumer-scoped compatibility. Library `compare` is
> product-contract compatibility.** Use both: `compare` protects the library
> contract; `appcompat` protects a specific consumer deployment.

For header-only libraries, app mode is less central unless there's a companion
runtime library — an existing app binary already contains the header-only code
it compiled earlier, so swapping a library may not exercise the changed
header-only implementation at all.

---

## 5. What ABI tools cannot prove

Even with perfect evidence, artifact comparison has hard boundaries. These are
**not abicheck's job** — they need tests, specs, or source-AST tooling. Treat
this as a guard against *over-trusting* any ABI tool (see
[Limitations](limitations.md) for the authoritative list):

| Case | Why it's invisible / out of scope |
|------|-----------------------------------|
| **Macro-only changes** | Macros are preprocessor behavior; not in the artifact |
| **Inline function body changed, same signature** | No exported ABI change; body is compiled into the consumer |
| **`constexpr` behavior changed** | Source/semantic compatibility, no symbol change |
| **Template body changed but not instantiated** | No emitted artifact to compare |
| **Uninstantiated template signature change** | Not in the shipped `.so` unless instantiated ([`case122`](../examples/case122_template_signature_uninstantiated.md)) |
| **Header-only change not affecting exports** | There may be no shared-library ABI surface |
| **Stripped binary, no headers/debug** | Mostly symbol-level comparison only |
| **Header/binary mismatch** | The tool may analyze a contract the binary wasn't built with — false results |
| **Static archives (`.a` / `.lib`) as archive containers** | abicheck analyzes linkable images/shared libraries/objects, not archive containers ([details](limitations.md#static-import-library-archives-a-lib)) |
| **Pure behavioral / semantic changes** | Same ABI/API, different meaning — needs tests/spec review |
| **Ownership / lifetime / thread-safety guarantee changes** | A signature can be byte-identical while the *contract* it implements flips |

The takeaway is the same one [Part 0](abi-series/00-product-contract.md) opens
with: **a stable ABI is necessary but not sufficient for a compatible release.**
ABI tools prove the binary contract held; behavioral compatibility still needs
your tests and your specification.

---

_See also: [Part 0 — Compatibility as a Product Contract](abi-series/00-product-contract.md) ·
[Limitations](limitations.md) · [Tool Comparison](../reference/tool-comparison.md) ·
[Application Compatibility](../user-guide/appcompat.md) ·
[Multi-Binary Releases](../user-guide/multi-binary.md)._
