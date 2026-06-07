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

### Why abicheck combines layers

abicheck is strongest because it does **not** rely on a single row. It overlays
three **independent, additive** evidence layers (see
[Architecture](architecture.md) and ADR-003):

| Layer | Evidence it contributes |
|-------|-------------------------|
| **Binary metadata** | ELF symbols, SONAME, versioning, visibility, dependencies (and PE/COFF + Mach-O equivalents) |
| **Header AST** (CastXML) | Function signatures, classes, structs, vtables, enums, typedefs, templates, `noexcept`, access |
| **Debug info** (DWARF/PDB) | Layout, offsets, enum values, calling convention, vtable slots, type cross-checks |

The best input you can give it is therefore:

> **old library + new library + matching public headers + debug info + build
> context.**

With less, abicheck degrades gracefully *down the matrix* — a stripped binary
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
