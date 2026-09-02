---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
summarizes:
  - evidence-model
  - ast-frontend-resolution
depends_on:
  - scripts/evidence_tiers.py
lifecycle: active
generated: false
---
# Detecting Breaks: Evidence, Tools, and Why One Method Is Never Enough

> This page opens Step 6 of the series, *Detect Breaks*. It is not "Part 8":
> Parts 0–7 teach what breaks and how to design against it; this page asks
> how you catch a break before you ship, which is where abicheck enters.

Parts 0–7 explained the *mechanisms*: what the compiler bakes into a binary, and
which changes corrupt that contract. This page turns the telescope around and asks
the engineering question: **how do you actually catch each of those breaks before
you ship?**

Three things matter, and this page covers all three:

1. **The general approaches** to ABI/API tracking — and the failure mode each one
   has when used alone.
2. **What evidence each break family requires** — matching every family from the
   [break-families table](../abi-cheat-sheet.md#break-families-and-where-each-is-explained) to
   the minimum input that makes it visible, with the example cases that prove it.
3. **Why classic single-method checkers (libabigail's `abidiff`, ABICC) are not
   sufficient** — and, just as honestly, where *any* static tool stops, including
   abicheck.

> **Tool-track companion pages:** this page teaches the concepts; the precise
> per-source capability matrix lives in
> [Evidence & Detectability](../evidence-and-detectability.md), measured accuracy
> numbers in [Tool Comparison & Benchmarks](../../reference/tool-comparison.md),
> and the boundary of static checking in [Limitations](../limitations.md).

---

## 1. The general approaches to ABI/API tracking

Every team tracks compatibility somehow, even if only by hope. The approaches
below are ordered roughly by how much they *observe*; each catches something the
previous ones cannot, and each has a blind spot that motivates the next.

| # | Approach | What it observes | Catches | Blind spot |
|---|----------|------------------|---------|------------|
| 1 | **Process discipline** — SemVer policy, review checklists, "don't touch public headers" rules | Human judgement | Anything a reviewer happens to notice | Everything a reviewer doesn't notice — layout shifts from an "internal" change, transitive leaks, toolchain flips. Unverifiable by construction. |
| 2 | **Runtime swap testing** — build an app against v1, run it against v2 | One consumer's actual usage | Real crashes in the paths the app exercises | Surface the test app doesn't call (usually most of it); silent corruption that doesn't crash; needs a representative app per consumer. |
| 3 | **Symbol-table diffing** — `nm`/`readelf` diff, or any tool run on stripped binaries (**L0**) | Exported symbol names, versions, SONAME | Removed/renamed symbols, C++ mangled-signature changes, linker metadata drift | Everything that doesn't change a symbol name: struct layout, enum values, vtable order, C parameter types. |
| 4 | **Debug-info diffing** — DWARF/PDB-based tools (**L1**) | Type layout as compiled: sizes, offsets, enum values, vtables | The whole layout family from [Part 3](03-type-layout.md) and most of [Part 4](04-cpp-abi.md) | Requires `-g` artifacts (release builds are usually stripped); largely blind to *source-level* API facts — access control, default arguments, `explicit`, hidden friends — which DWARF doesn't record or tools don't model. |
| 5 | **Header/AST diffing** — compiling public headers and comparing the AST (**L2**) | The declared source contract | Source-only API breaks, plus *scoping*: knowing which types are actually public | Blind to binary truth: what was *actually* exported and with which SONAME/versions, and what flags the shipped binary was really built with. |
| 6 | **Build- and source-aware overlay** (**L3/L4**) | Compile flags, default-argument *values*, inline/template bodies, uninstantiated templates | Facts that never reach any shipped artifact — the [source-only tail](../limitations.md#source-only-changes-invisible-to-binaryobject-analysis) | Highest setup cost; meaningless without the artifact layers underneath it to anchor the shipped-ABI verdict. |

The pattern: **each approach is a projection of the library onto one kind of
evidence.** None of the projections is the library. A checker is only complete to
the extent that it overlays several projections and lets the strongest evidence
win — which is exactly the [five-layer evidence model](../evidence-and-detectability.md#0-the-five-sources-of-information)
abicheck implements, and why runtime testing (approach 2) still belongs in your
release pipeline *next to* static checking: it is the only approach that observes
behaviour.

### 1a. The hidden prerequisite of header/AST diffing: the compile context

Approach 5 (L2 header/AST) has a subtlety the table glosses: a header is not a
self-contained fact, it is *source code*. To turn it into an AST the frontend
must parse it **the way your compiler does** — with the include roots it
`#include`s, the C++ standard it assumes (`-std`), and the `-D` feature macros
that gate which declarations even exist. Get that context wrong and L2 does not
fail loudly; it produces a *different, plausible* AST. Two consequences matter
for compatibility:

- **L2 is what decides "public."** The public/internal boundary — and therefore
  whether a removed symbol is a compatible internal cleanup or a breaking API
  removal — comes from the header AST. If L2 cannot be built, the scan only has
  the binary, so it must treat the export table as the surface and (correctly, by
  that narrower rule) flags *internal* removals as BREAKING. This "scope
  divergence" is a missing-context artifact, not a real break: with L2 those
  demote to COMPATIBLE. A field run of oneTBB / oneDNN / oneDAL hit exactly this —
  `dnnl::impl::*` and bundled `DGETRF`/`SGETRF` removals reported as breaking
  purely because the headers could not be parsed.
- **The wrong context manufactures phantom diffs.** Parse at `-std=c++17` a
  library built at `-std=c++20` and concepts, `char8_t`, `noexcept`-in-type, and
  inline-namespace versions shift — L2 shows add/remove churn that no consumer
  would ever observe. Likewise a mismatched `-D` (a feature macro, or
  libstdc++'s `_GLIBCXX_USE_CXX11_ABI` dual-ABI switch) changes which
  declarations are visible at all.

This is why the **source of the compile context matters as much as the frontend
choice**, and why the two frontends are only interchangeable when fed the same
context:

| Scan source | What it supplies to L2 | What it cannot supply alone |
|---|---|---|
| **castxml** (`--ast-frontend castxml`) | runs your real `g++`/MSVC, so system includes + predefined macros + the compiler's default dialect come for free | your project's own `-I` roots, `-D`, and the exact `-std` (still pass these) |
| **clang** (`--ast-frontend clang`) | the alternative for clang-only hosts; now auto-probes the host GNU compiler for system includes so libstdc++ resolves like castxml | same as above — auto-detection is system-headers only |
| **`-I` / `--compiler-option` (CLI)** | per-run include roots, `-std`, `-D` | reproducibility — a human/CI must retype them each run |
| **`.abicheck.yml` `compile:` block** | the project's stable, reviewed include roots / `std` / `defines` | per-invocation cross-compile specifics (those stay CLI) |
| **compile database** (`compile_commands.json`) | the authoritative per-TU `-I`/`-std`/`-D` the library was actually built with | *(threading it into L2 is a planned step; today it feeds L3–L5)* |

The practical takeaway for [`abicheck scan`](../../use/scan-levels.md#compile-context-for-header-parsing-l2):
auto-detection makes the common case (find the C++ stdlib) work with no flags,
but the **project-specific** context — include roots, dialect, feature macros —
must come from a compile DB, the config `compile:` block, or explicit flags, or
L2 (and the public/internal scoping that depends on it) is only as good as the
context it was handed.

**What a header AST dump is.** `abicheck dump -H include/` produces one: the
frontend parses the public headers under the compile context above and
records every declaration it finds — functions with their parameter types,
records with their fields and bases, enums, typedefs — as the L2 half of the
snapshot. The compile context decides its contents; the frontend decides
how much of each declaration is captured.

**castxml and clang capture different facts.** The two L2 frontends expose
the same parser interface but do not populate every model field alike —
castxml records template instantiations only, while the clang backend also
records the uninstantiated pattern, and a handful of C++20 facts are
clang-only. The per-field matrix is generated from the parsers' own source
in [Header Backend Capabilities](../../reference/header-backend-capabilities.md);
consult it before attributing a missing finding to the library.

**The same idea, applied per translation unit.** L4 source replay runs the
identical declaration-level parse over each compiled translation unit under
its own recorded flags. That is what recovers the facts no header AST can
carry: a change to an *uninstantiated* template's signature is L4-only
([case122](../../reference/examples/case122_template_signature_uninstantiated.md)),
two providers corroborating one declaration is what the cross-source pass
reads ([case151](../../reference/examples/case151_xcheck_provider_matrix.md)),
and a declaration's *source file* changing while its symbol stays put is a
fact only the L5 graph derived from those TUs can see
([case162](../../reference/examples/case162_symbol_source_owner_changed.md)).

---

## 2. What it takes to find each break family

Every family in the
[break-families index](../abi-cheat-sheet.md#break-families-and-where-each-is-explained)
has a *minimum evidence* level — `L0` binary, `L1` +debug info, `L2`
+headers, `L3` +build data, `L4` +sources — below which no tool can see it,
and the per-case minimums are machine-readable in
[`examples/ground_truth.json`](https://github.com/abicheck/abicheck/blob/main/examples/ground_truth.json)
(`min_evidence`). The family-by-family table lives with the level-by-level
walk-through, in
[What Each Level Sees § Reference: which input proves which family](../what-each-level-sees.md#reference-which-input-proves-which-family);
the two lessons it carries — evidence runs in both directions (more input
also *dismisses* false alarms, because header scoping is what lets a checker
say "that struct was never public"), and the staircase is measurable but
not strictly rising at every rung — are measured in
[Evidence & Detectability § What each layer buys](../evidence-and-detectability.md#what-each-layer-buys-fewer-false-negatives-and-fewer-false-positives).

---

## 3. Why an abidiff- or ABICC-class checker is not sufficient

Each classic checker is capped at one rung of the staircase (`abidiff` is
DWARF-first and degrades to symbol-only on a stripped release; ABICC's
header workflow sees the declared contract but not the binary truth) and
neither scopes findings to the public surface, so each misses families the
other catches and both drown teams in internal-churn noise — the structural
comparison, per case, is [Tool Comparison](../../reference/tool-comparison.md).
And where everything stops: no static tool, abicheck included, can prove
*behaviour* — the honest boundary is
[What ABI tools cannot prove](../evidence-and-detectability.md#5-what-abi-tools-cannot-prove),
and [Assurance Beyond Static Checking](../assurance-methods.md) names what to
run for the rest.

---

## 4. Using the encyclopedia as a detection atlas

Every capability claim in this series is backed by a runnable fixture, and the
mapping is maintained mechanically — CI checks that every `ChangeKind` is
produced by a detector, documented, and (for the catalog) carries a verified
verdict and minimum evidence tier:

- **Capability → meaning:** the [Change Kind Reference](../../reference/change-kinds.md)
  lists every detectable change kind with its classification.
- **Capability → proof:** each [example page](../../reference/examples/index.md) names the
  change kinds it triggers, its verdict, and includes a *Real Failure Demo*; the
  expected results live in `ground_truth.json`, which the benchmark gates on.
- **Capability → required input:** the `min_evidence` field per case, aggregated
  in the [evidence-tier benchmark](../../reference/tool-comparison.md#benchmarking-by-evidence-tier),
  tells you exactly which input you must provide before that break becomes
  visible — which is the practical answer to "what do I need to feed the
  checker in *my* CI?"

---

## Where to go next

- Back to the [series hub](../abi-api-handling.md) for the other parts.
- [Assurance Beyond Static Checking](../assurance-methods.md) — this page's
  direct sibling in Step 6: what to run for the
  part §3 says no static tool (abicheck included) can reach — behaviour,
  wire/storage compatibility, lifetime, and concurrency contracts.
- [Evidence & Detectability](../evidence-and-detectability.md) — the full
  per-source capability matrix this page summarizes.
- [Choose Your Workflow](../../start/choose-your-workflow.md) — turn the
  evidence you *have* into the right command for your CI.

---

**Ladder:** ← [Static & Header-Only Contracts](../static-and-header-only.md) · Step 6 · Detect Breaks · [Assurance Beyond Static Checking](../assurance-methods.md) →
