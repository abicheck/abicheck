---
doc_type: tutorial
audience:
  - library-maintainer
level: beginner
depends_on:
  - abicheck/semver.py
lifecycle: active
generated: false
---
# Part 0 — Compatibility as a Product Contract

> **Series navigation:** **0. Product Contract** ·
> [1. Foundations](01-foundations.md) ·
> [2. Symbol Contracts](02-symbol-contracts.md) ·
> [3. Type Layout](03-type-layout.md) ·
> [4. C++ ABI](04-cpp-abi.md) ·
> [5. Linker & ELF](05-linker-elf.md) ·
> [6. Transitive Breaks](06-transitive-breaks.md) ·
> [7. Designing for Stability](07-designing-for-stability.md) ·
> [Detecting Breaks](08-detection.md)

**What you'll learn on this page**

- Why ABI/API compatibility is a **promise the product makes**, not just a
  property a tool reads out of a binary.
- That "compatible" is really several different questions — source, binary,
  behavioral, data, deployment, ecosystem, operational, and build-profile —
  and why naming which one you mean resolves most "is this a break?"
  arguments before they start.
- How to write down your **public surface** — the thing the promise is about —
  before you ever run a checker.
- How [Semantic Versioning](https://semver.org/) turns that promise into a
  version-number convention, and how abicheck's verdicts map onto SemVer
  decisions.
- Why the *same* technical change can be a release-blocking break for one
  product and a non-event for another.

This is the **prologue** (part 0) of the nine-part series. The later parts teach the
*mechanisms* (what bytes move, what the loader does). This part teaches the
*framing* that makes those mechanisms matter: a change is only a "break" if it
breaks something you promised.

> **New here?** If you want the build/link/load mental model first, you can read
> [Part 1 — Foundations](01-foundations.md) and come back. But most of the
> confusion people have about ABI tools ("why did it flag this? why didn't it
> flag that?") dissolves once the contract is written down — so start here if
> you can.

---

## 1. The core idea: detection finds facts, the product decides breakage

abicheck — like every ABI/API tool — gathers **evidence** (symbols, type
layout, headers, dependencies) and reports **facts**: "function `foo` was
removed", "struct `S` grew by 8 bytes", "the SONAME changed". Whether a given
fact is a *break* is a separate question, and it is **not** a property of the
binary. It is a property of the **contract** the product published.

> **Detection finds facts. Policy decides whether those facts are breaking for
> this product.**

A worked example: abicheck reports `func_removed` for a symbol that disappeared.
Is that a break?

- If the symbol was part of your **promised public API** → yes, existing
  consumers will fail to link or load. Breaking.
- If the symbol was an **internal helper** that merely happened to be exported
  (no visibility annotation, no version script) → it was never part of the
  contract. Removing it is *housekeeping*, not a break — even though the symbol
  table changed.

The tool sees the same fact in both cases. Only the contract distinguishes them.
This is why abicheck has [policy profiles](../../use/policies.md) and a
[public-surface scoping model](../../reference/change-kinds.md): they are how you
tell the tool what your contract actually is.

---

## 2. Compatibility is not one question — name which kind you mean

"Is this change compatible?" is really several independent questions, and
conflating them is the single most common source of confusion in ABI/API
discussions — a change can be compatible along one axis and breaking along
another at the same time. Before reading further, know which of these you're
actually asking about:

| Dimension | The question it answers | A representative break |
|-----------|--------------------------|-------------------------|
| **Source / API compatibility** | Does existing source code still *compile* against the new headers? | A default argument was removed; a function became `explicit` |
| **Binary / ABI compatibility** | Does an already-*built* consumer binary still link, load, and run correctly against the new library? | A symbol was removed; a struct's layout changed |
| [**Behavioral / semantic compatibility**](../behavioral-compatibility.md) | For the same inputs, does the operation still mean the same thing? | A function starts returning a different value, or changes its side effects, for inputs that used to behave one way |
| [**Data / wire / storage compatibility**](../data-wire-compatibility.md) | Are values that cross a boundary — serialized files, network messages, shared memory, on-disk records — still interpreted the same way? | An enum value's meaning was reassigned; a struct used as a wire format changed layout |
| [**Deployment / environment compatibility**](../dependency-floors.md) | Will the binary still run on every platform/OS version it was promised to support? | A dependency's minimum runtime version (glibc floor, macOS deployment target) was silently raised by a rebuild |
| [**Ecosystem / consumer compatibility**](../../use/plugin-systems.md) | Does *this specific* application, plugin, or binding keep working? | A plugin's vtable/callback contract changed; a language binding's assumed struct layout moved |
| [**Operational compatibility**](../compatibility-direction.md) | Can you upgrade, roll back, or run two versions side by side? | A SONAME wasn't bumped for an incompatible change, so upgrade-in-place breaks running processes |
| [**Build-profile comparability**](../build-profile-comparability.md) | Were the two things being compared even built under conditions that make the comparison meaningful? | Comparing a GCC build against a Clang build of "the same" library, or two different C++ standard-library ABI modes |

Three more contracts don't map to a single row above — each cuts across
several rows at once, so each gets its own page rather than forcing a
misleading ninth row:
[**Ownership & lifetime contracts**](../ownership-and-lifetime.md) (who
allocates, who frees, how long a pointer stays valid — invisible to any
signature), [**concurrency & initialization contracts**](../concurrency-and-initialization.md)
(thread-safety, init/destruction order, fork safety — the same
signature-invisibility problem, concurrency-specific), and
[**static & header-only library contracts**](../static-and-header-only.md)
(what changes when there's no separate dynamic-loading boundary at all).

Coverage varies by dimension rather than falling off in a straight line. A
static ABI/API checker like abicheck reports source and binary
incompatibilities it can observe in the evidence it was given — Parts 1–6
are organized around exactly these two mechanisms, since they're what a
static comparison can observe at all. **Exactly what it can observe, how
that scales with `--depth`, and its measured false-positive/false-negative
rate at each evidence level are not restated here** — see
[Evidence & Detectability](../evidence-and-detectability.md), the one page
that owns those guarantees precisely, rather than risk this page drifting
out of sync with it. The takeaway for this page: a clean result means "no
incompatibility found in the evidence supplied," not "compatibility
proved." It also *enforces* two of the other dimensions
outright rather than merely gesturing at them — a genuinely
incomparable build profile is a hard failure
([exit `16`/`not_comparable`](../../reference/exit-codes.md), escapable only
with the explicit
[`--diagnostic-comparison`](../../reference/cli-reference.md) opt-out that
stamps the result untrustworthy), and a deployment/runtime-floor regression
*with observable evidence* — a versioned ELF dependency requirement, or
captured platform metadata — is its own detected finding, not a gap (see
[Dependency & Runtime Floors](../dependency-floors.md)). That evidence isn't
universal, though: an unversioned import (the typical Windows case — a new
function pulled in from an *existing* dependency, with no per-symbol version
to compare) currently produces no finding at all, a blind spot that page
documents and recommends covering with an oldest-target load test. What it genuinely
cannot prove from artifacts alone, at any `--depth`, is behavioral and
data/wire-format compatibility — don't read a clean binary-compatibility
result as a behavioral or wire-format guarantee it never claimed to make.
The other dimensions get their own, narrower
treatment where the series already covers them —
[deployment/runtime floors](../dependency-floors.md),
[plugin/ecosystem contracts](../../use/plugin-systems.md), and
[build-profile comparability](../build-profile-comparability.md) each have
their own page — rather than being folded into "ABI" generically. The
mechanics build-profile comparability *enforces* — the exact exit codes and
the `--diagnostic-comparison` opt-out — are documented where they're
enforced, in the [exit-code reference](../../reference/exit-codes.md) and
[CLI reference](../../reference/cli-reference.md); the narrative page
explains *why* the gate exists and what its two possible gate outcomes
(comparable, sitting in front of the ordinary verdict space; or
not-comparable, a hard failure) mean.

> **Rule of thumb:** name the dimension before you argue about whether a
> change is "a break." A change that's a real source break and a total binary
> non-event (e.g. a removed default argument) and a change that's the reverse
> — source-compatible but binary-breaking (e.g. a struct gains a field that
> shifts existing members' offsets: code that only names existing fields
> still compiles unchanged, but a consumer built against the old layout now
> reads the wrong bytes) — are both real; they're just answers to different
> questions.

---

## 3. Define the public surface *before* you check

Before checking ABI/API stability, write down what is actually promised. The
public surface is the union of:

| Surface element | What it pins | Where abicheck sees it |
|-----------------|--------------|------------------------|
| **Public headers** | The source-level API: function signatures, types, macros, default arguments | Header AST (CastXML), if you pass `--header old=`/`--header new=` |
| **Exported symbols** | The link/load-level ABI: which names a consumer can bind to | ELF `.dynsym` / PE export table / Mach-O export trie |
| **Struct/class layout exposed in headers** | Field offsets, sizes, alignment that consumers bake in | DWARF/PDB debug info |
| **Plugin / `dlopen` entry points** | The dynamic-loading contract between host and plugin | [Plugin manifest](../../use/plugin-systems.md) |
| **Supported platforms & architectures** | Which ABIs you ship (x86-64, arm64, …) | Per-binary; compared per-platform |
| **Supported compilers & standard-library ABI** | e.g. the libstdc++ dual-ABI flag, MSVC version range | Build context / toolchain flags |
| **Calling conventions & exception model** | How calls and unwinding are wired | DWARF / mangling |
| **SONAME / install-name policy** | When the soname bumps (and consumers must relink) | ELF SONAME / Mach-O install name |
| **Symbol-version policy** | Which versioned symbols are promised stable | ELF symbol versions (`GLIBC_2.x`-style) |
| **Source-compatibility promise** | Whether *recompiling* against new headers must keep working | Policy choice (see [verdicts](../verdicts.md)) |

!!! tip "The single most useful sentence in your project's docs"
    > "Our public API is everything declared in `include/foo/*.h` and exported
    > with `FOO_PUBLIC`. Everything under `detail/` or not marked `FOO_PUBLIC`
    > is private and may change at any time."

    With that sentence written down, most "is this a break?" arguments answer
    themselves — and you can tell abicheck the same thing via
    [public-surface scoping](../../reference/change-kinds.md) and
    [suppressions](../../use/suppressions.md).

If you *don't* write this down, the default contract is brutal: **everything you
export is part of the ABI**, because some consumer somewhere may have bound to
it. That is exactly why accidental exports (missing `-fvisibility=hidden`, no
version script) are a recurring source of "we broke an ABI we didn't know we
had" — see [Part 5 — Linker & ELF](05-linker-elf.md).

---

## 4. Semantic Versioning: turning the promise into a number

[SemVer](https://semver.org/) says a project **must declare a public API**, and
then the version number communicates compatibility:

- **MAJOR** — incompatible API/ABI changes.
- **MINOR** — backward-compatible additions.
- **PATCH** — backward-compatible bug fixes.

A verdict **constrains** the version number; it does not choose it. Read the
table below in one direction only: a finding can tell you a version bump is
*not sufficient*, but no finding can tell you a release is *only* a bug fix.
That is a claim about everything the release changed — behavior, wire formats,
performance and concurrency contracts, documentation promises — and a scan of
two binaries has no visibility into most of it.

abicheck detects the change and classifies it; **you** decide what the
classification means for your version number and release, *and only after* the
public API is declared (§3).

### abicheck verdict → SemVer action

| abicheck verdict / class | Product meaning | Typical SemVer action |
|--------------------------|-----------------|-----------------------|
| **`BREAKING`** | Existing **binary** consumers may fail to link, load, or behave correctly | **Major** bump; SONAME/install-name bump, new symbol version, or block the release |
| **`API_BREAK`** | **Source** users may fail to recompile, but already-built binaries may still load | **Major** bump *if source compatibility is promised*; otherwise a documented source migration |
| **`COMPATIBLE` (addition)** | Existing users keep working; new public API added | Usually **minor** bump |
| **`COMPATIBLE_WITH_RISK`** | ABI likely intact, but a deployment/security/runtime assumption changed | Usually a **release note** + policy review; sometimes block |
| **`NO_CHANGE`** | No relevant public-contract change detected *in the evidence provided* | **Not a version decision on its own.** It says this scan found no ABI/API diff — not that the release is a bug fix. Any level from patch to major can still be correct; behavioral, wire-format, and semantic changes are invisible here |
| **Internal / private change** | No public-contract change *if truly hidden* | **No** SemVer impact |

A `NO_CHANGE` release still lands at minor if it added public functionality
somewhere this scan didn't look, and at major if it made an **incompatible**
change to a documented behavior, a serialized format, or a threading guarantee. Those contracts are
covered by [Behavioral](../behavioral-compatibility.md) and
[Data/Wire](../data-wire-compatibility.md) compatibility, and neither is
provable by comparing two artifacts.

> abicheck's `compare` mode is the only one with the full verdict vocabulary —
> in particular the `API_BREAK` distinction between *source* breaks and *binary*
> breaks. Legacy `compat` mode and other tools generally collapse that
> distinction. See [Verdicts](../verdicts.md) and
> [Tool Comparison](../../reference/tool-comparison.md).

### The same change, two verdicts

Because breakage is contract-relative, the *same* technical change can land in
different rows above depending on policy:

- Making a conversion constructor `explicit` is an **`API_BREAK`** (old source
  that relied on the implicit conversion won't compile) but **not** a binary
  break (mangled names and layout are unchanged). Under a strict
  source-compatible SDK contract that's a major bump; under a binary-only
  plugin contract it may be acceptable. abicheck's
  [`sdk_vendor` vs `plugin_abi` policies](../../use/policies.md) encode
  exactly this difference.

---

## 5. Name your contract shape

"Public surface" looks different for different kinds of products. Identify which
shape you are before reasoning about breaks. This section covers four common
product shapes narratively; [Consumer Models](../consumer-models.md) formalizes
all eight consumer shapes (including FFI bindings, header-only consumers, and
static-linked consumers) into one table and composes that axis with the
dimension/direction/surface/build-profile questions this page already covers.

### Traditional C shared library

The contract is typically: **public headers + exported symbols + struct layout
exposed in headers + SONAME/symbol-version policy + the supported platform
ABI.** Already-built consumers must keep linking, loading, and calling into the
new binary using the *old* contract. This is the case abicheck models most
directly — there is a real binary boundary to compare.

### C++ SDK

Everything above, **plus**: supported compiler version range, standard library
ABI (e.g. the libstdc++ dual-ABI flag — see
[`case104`](../../reference/examples/case104_glibcxx_dual_abi_flip.md)), exception model,
RTTI, visibility rules, inline-namespace policy, template instantiation policy,
and toolchain flags. C++ contracts are wider and more fragile;
[Part 4 — C++ ABI](04-cpp-abi.md) covers the mechanisms.

### Plugin / SDK with `dlopen`

A **two-sided** ABI contract between host and plugin: fixed entry points,
`dlopen`/`dlsym` names, callback structs, registration functions, and
host/plugin ownership & lifetime rules. This is usually a *manually declared*
dynamic-loading contract, not ordinary link-time ABI — so abicheck checks it
against a [plugin manifest](../../use/plugin-systems.md).

### Multi-library bundle / product release

The contract is **product-level**: not just whether each `.so` changed, but
whether the *collection* still satisfies all intra-bundle dependencies, provider
relationships, entry points, symbol versions, and manifest promises. Per-library
comparison is necessary but **insufficient** — see
[Part 6 — Transitive Breaks](06-transitive-breaks.md) and
[Multi-Binary Releases](../../use/multi-binary.md).

> **Rule of thumb:** *For products that ship more than one public or semi-public
> library, per-library compatibility is necessary but not sufficient. The
> product contract is the bundle contract.*

---

## 6. Where this leaves you

You now have the framing the rest of the series builds on:

> A product **declares** a compatibility contract → abicheck **gathers
> evidence** from binaries, headers, debug info, applications, bundles, and
> manifests → **policy maps** the detected facts onto a release decision.

Carry these two questions into every later part:

1. **Was the thing that changed part of the promised public surface?** (§3)
2. **What does my versioning policy say I must do about a change of this
   class?** (§4)

Next: [Part 1 — Foundations](01-foundations.md) shows *how* a change becomes a
break at the machine level. If you want to know *which* evidence abicheck (or
any other tool) needs to even see a given change, read
[Evidence & Detectability](../evidence-and-detectability.md).

---

_See also: [Verdicts](../verdicts.md) · [Policy Profiles](../../use/policies.md) ·
[Evidence & Detectability](../evidence-and-detectability.md) ·
[Examples Encyclopedia](../../reference/examples/index.md)._

---

**Ladder:** ← [Glossary](glossary.md) · Tier 1 · Foundations · [Part 1 — Foundations: From Source Code to a Running Process](01-foundations.md) →
