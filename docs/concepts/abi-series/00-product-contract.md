# Part 0 — Compatibility as a Product Contract

> **Series navigation:** **0. Product Contract** ·
> [1. Foundations](01-foundations.md) ·
> [2. Symbol Contracts](02-symbol-contracts.md) ·
> [3. Type Layout](03-type-layout.md) ·
> [4. C++ ABI](04-cpp-abi.md) ·
> [5. Linker & ELF](05-linker-elf.md) ·
> [6. Transitive Breaks](06-transitive-breaks.md) ·
> [7. Designing for Stability](07-designing-for-stability.md)

**What you'll learn on this page**

- Why ABI/API compatibility is a **promise the product makes**, not just a
  property a tool reads out of a binary.
- How to write down your **public surface** — the thing the promise is about —
  before you ever run a checker.
- How [Semantic Versioning](https://semver.org/) turns that promise into a
  version-number convention, and how abicheck's verdicts map onto SemVer
  decisions.
- Why the *same* technical change can be a release-blocking break for one
  product and a non-event for another.

This is the **prologue** to the seven-part series. The later parts teach the
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
This is why abicheck has [policy profiles](../../user-guide/policies.md) and a
[public-surface scoping model](../../reference/change-kinds.md): they are how you
tell the tool what your contract actually is.

---

## 2. Define the public surface *before* you check

Before checking ABI/API stability, write down what is actually promised. The
public surface is the union of:

| Surface element | What it pins | Where abicheck sees it |
|-----------------|--------------|------------------------|
| **Public headers** | The source-level API: function signatures, types, macros, default arguments | Header AST (CastXML), if you pass `--old-header`/`--new-header` |
| **Exported symbols** | The link/load-level ABI: which names a consumer can bind to | ELF `.dynsym` / PE export table / Mach-O export trie |
| **Struct/class layout exposed in headers** | Field offsets, sizes, alignment that consumers bake in | DWARF/PDB debug info |
| **Plugin / `dlopen` entry points** | The dynamic-loading contract between host and plugin | [Plugin manifest](../../user-guide/plugin-systems.md) |
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
    [suppressions](../../user-guide/suppressions.md).

If you *don't* write this down, the default contract is brutal: **everything you
export is part of the ABI**, because some consumer somewhere may have bound to
it. That is exactly why accidental exports (missing `-fvisibility=hidden`, no
version script) are a recurring source of "we broke an ABI we didn't know we
had" — see [Part 5 — Linker & ELF](05-linker-elf.md).

---

## 3. Semantic Versioning: turning the promise into a number

[SemVer](https://semver.org/) says a project **must declare a public API**, and
then the version number communicates compatibility:

- **MAJOR** — incompatible API/ABI changes.
- **MINOR** — backward-compatible additions.
- **PATCH** — backward-compatible bug fixes.

That maps cleanly onto abicheck's [verdicts](../verdicts.md) — *but only after*
the public API is declared (§2). abicheck detects the change and classifies it;
**you** decide what the classification means for your version number and release.

### abicheck verdict → SemVer action

| abicheck verdict / class | Product meaning | Typical SemVer action |
|--------------------------|-----------------|-----------------------|
| **`BREAKING`** | Existing **binary** consumers may fail to link, load, or behave correctly | **Major** bump; SONAME/install-name bump, new symbol version, or block the release |
| **`API_BREAK`** | **Source** users may fail to recompile, but already-built binaries may still load | **Major** bump *if source compatibility is promised*; otherwise a documented source migration |
| **`COMPATIBLE` (addition)** | Existing users keep working; new public API added | Usually **minor** bump |
| **`COMPATIBLE_WITH_RISK`** | ABI likely intact, but a deployment/security/runtime assumption changed | Usually a **release note** + policy review; sometimes block |
| **`NO_CHANGE`** | No relevant public-contract change detected | **Patch** / implementation-only release |
| **Internal / private change** | No public-contract change *if truly hidden* | **No** SemVer impact |

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
  [`sdk_vendor` vs `plugin_abi` policies](../../user-guide/policies.md) encode
  exactly this difference.

---

## 4. Name your contract shape

"Public surface" looks different for different kinds of products. Identify which
shape you are before reasoning about breaks.

### Traditional C shared library

The contract is typically: **public headers + exported symbols + struct layout
exposed in headers + SONAME/symbol-version policy + the supported platform
ABI.** Already-built consumers must keep linking, loading, and calling into the
new binary using the *old* contract. This is the case abicheck models most
directly — there is a real binary boundary to compare.

### C++ SDK

Everything above, **plus**: supported compiler version range, standard library
ABI (e.g. the libstdc++ dual-ABI flag — see
[`case104`](../../examples/case104_glibcxx_dual_abi_flip.md)), exception model,
RTTI, visibility rules, inline-namespace policy, template instantiation policy,
and toolchain flags. C++ contracts are wider and more fragile;
[Part 4 — C++ ABI](04-cpp-abi.md) covers the mechanisms.

### Plugin / SDK with `dlopen`

A **two-sided** ABI contract between host and plugin: fixed entry points,
`dlopen`/`dlsym` names, callback structs, registration functions, and
host/plugin ownership & lifetime rules. This is usually a *manually declared*
dynamic-loading contract, not ordinary link-time ABI — so abicheck checks it
against a [plugin manifest](../../user-guide/plugin-systems.md).

### Multi-library bundle / product release

The contract is **product-level**: not just whether each `.so` changed, but
whether the *collection* still satisfies all intra-bundle dependencies, provider
relationships, entry points, symbol versions, and manifest promises. Per-library
comparison is necessary but **insufficient** — see
[Part 6 — Transitive Breaks](06-transitive-breaks.md) and
[Multi-Binary Releases](../../user-guide/multi-binary.md).

> **Rule of thumb:** *For products that ship more than one public or semi-public
> library, per-library compatibility is necessary but not sufficient. The
> product contract is the bundle contract.*

---

## 5. Where this leaves you

You now have the framing the rest of the series builds on:

> A product **declares** a compatibility contract → abicheck **gathers
> evidence** from binaries, headers, debug info, applications, bundles, and
> manifests → **policy maps** the detected facts onto a release decision.

Carry these two questions into every later part:

1. **Was the thing that changed part of the promised public surface?** (§2)
2. **What does my versioning policy say I must do about a change of this
   class?** (§3)

Next: [Part 1 — Foundations](01-foundations.md) shows *how* a change becomes a
break at the machine level. If you want to know *which* evidence abicheck (or
any other tool) needs to even see a given change, read
[Evidence & Detectability](../evidence-and-detectability.md).

---

_See also: [Verdicts](../verdicts.md) · [Policy Profiles](../../user-guide/policies.md) ·
[Evidence & Detectability](../evidence-and-detectability.md) ·
[Examples Encyclopedia](../../examples/index.md)._
