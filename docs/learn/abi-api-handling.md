# ABI/API Compatibility — A Learning Series

This series teaches ABI/API compatibility from first principles to running a
scanner on a multi-binary product in CI. Start at Tier 0; each tier assumes
the ones before it, and every page ends with a **Ladder** footer that names
the page before and after it.

## Start here

1. [**ABI in Five Minutes**](abi-series/abi-in-5-minutes.md) — the gentlest
   introduction: what an ABI is, in one sentence, and why an app can crash
   after a library upgrade nobody recompiled it for.
2. [**How a Break Shows Up**](how-a-break-shows-up.md) — the eight ways a
   break reaches you, which mechanism is behind each, and which kind of
   evidence first reveals it.
3. [**Part 0 — Compatibility as a Product Contract**](abi-series/00-product-contract.md)
   — the framing every later page rests on: a change is only a "break" if it
   breaks a promise, and "compatible" needs a dimension named.

## The ladder

Every page has a tier and a level. Read tiers in order; within a tier follow
the arrows. "Go deeper" pages are optional side reads that return you to the
spine, and "also" entries are pages that belong to another tab but are worth
reading at that point. Both tables are rendered from
`docs/_meta/learning-ladder.yaml`, the one owner of the reading order.

<!-- BEGIN GENERATED: learning-ladder -->
<!-- This block is rendered from docs/_meta/learning-ladder.yaml by gen_learning_ladder.py — do not edit by hand. Edit the YAML and run `python scripts/gen_learning_ladder.py`. -->

**ABI/API Compatibility**

| Tier | Level | Pages |
|---|---|---|
| Tier 0 · Orientation | beginner | [ABI in Five Minutes](abi-series/abi-in-5-minutes.md) → [How a Break Shows Up](how-a-break-shows-up.md) → [ABI Cheat Sheet](abi-cheat-sheet.md) → [Glossary](abi-series/glossary.md) |
| Tier 1 · Foundations | beginner → intermediate | [Part 0 — Compatibility as a Product Contract](abi-series/00-product-contract.md) → [Part 1 — Foundations: From Source Code to a Running Process](abi-series/01-foundations.md) → [What Is Part of Your ABI Surface?](abi-surface.md) |
| Tier 2 · Mechanics | intermediate | [Part 2 — Symbol Contract Breaks](abi-series/02-symbol-contracts.md) → [Part 3 — Type Layout Breaks](abi-series/03-type-layout.md) → [Part 4 — C++ ABI Specifics](abi-series/04-cpp-abi.md) (go deeper: [Class Layout ABI & API: Problems and Detection](class-layout-abi.md), [Exception Unwinding: The Machinery Behind `noexcept`](exception-unwinding-abi.md), [Modern C/C++ and Toolchain ABI Hazards](modern-cpp-toolchain-hazards.md)) → [Part 5 — ELF & Linker-Level Concerns](abi-series/05-linker-elf.md) (go deeper: [The MSVC/PE ABI Model](msvc-pe-abi-model.md)) → [Part 6 — Subtle & Transitive Breaks](abi-series/06-transitive-breaks.md) |
| Tier 3 · Define the contract | intermediate | [Compatibility Direction](compatibility-direction.md) → [Consumer Models](consumer-models.md) → [Build Profile Comparability](build-profile-comparability.md) → [Static & Header-Only Contracts](static-and-header-only.md)<br>also: [Contract-Aware Compatibility](contract-aware-compatibility.md) (on the Concepts tab) |
| Tier 4 · Evidence and detection | intermediate | [Detecting Breaks: Evidence, Tools, and Why One Method Is Never Enough](abi-series/08-detection.md) → [Assurance Beyond Static Checking: What Each Verification Method Actually Proves](assurance-methods.md)<br>also: [Evidence & Detectability: What Each Method Can and Cannot See](evidence-and-detectability.md) (on the Concepts tab); [What Each Level Sees — a level-by-level walk-through](what-each-level-sees.md) (on the Concepts tab) |
| Tier 5 · Practice | intermediate | [Where in the Pipeline](where-in-the-pipeline.md) → [Report the Surface, Not Only the Breaks](surface-growth.md)<br>also: [Baseline Management](../use/baseline-management.md) (tool guide) |
| Tier 6 · Design | intermediate | [Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md) |
| Tier 7 · At scale | advanced | [Dependency & Runtime Floors](dependency-floors.md) → [Environment & Toolchain Drift](environment-drift.md) |
| Tier 8 · Beyond static ABI | advanced | [Behavioral & Semantic Compatibility](behavioral-compatibility.md) → [Data, Wire & Storage Compatibility](data-wire-compatibility.md) → [Ownership & Lifetime Contracts](ownership-and-lifetime.md) → [Concurrency & Initialization Contracts](concurrency-and-initialization.md) |

**Concepts**

| Tier | Level | Pages |
|---|---|---|
| c1 · Reading a result | intermediate | [Verdicts](verdicts.md) → [Contract-Aware Compatibility](contract-aware-compatibility.md) |
| c2 · The evidence model | intermediate | [Evidence & Detectability: What Each Method Can and Cannot See](evidence-and-detectability.md) → [What Each Level Sees — a level-by-level walk-through](what-each-level-sees.md) → [ELF-Only Mode and Symbol Filtering](elf-symbol-filtering.md) → [Limitations & Known Boundaries](limitations.md) |
| c3 · Internals | advanced | [Architecture](architecture.md) → [Source & Build Data](build-source-data.md) → [Graph Coverage & Negative Evidence](graph-coverage.md) → [Unified Impact Assessment](impact-analysis.md) |

<!-- END GENERATED: learning-ladder -->

### Reading paths by role

Each path is a walk *up* the ladder — it may skip tiers but never steps
back — and ends with the one tool-track page that role needs next.

<!-- BEGIN GENERATED: learning-paths -->
<!-- This block is rendered from docs/_meta/learning-ladder.yaml by gen_learning_ladder.py — do not edit by hand. Edit the YAML and run `python scripts/gen_learning_ladder.py`. -->

| Role | Path (tier · page) | Then |
|---|---|---|
| New C/C++ library author | 0 · [ABI in Five Minutes](abi-series/abi-in-5-minutes.md) → 0 · [How a Break Shows Up](how-a-break-shows-up.md) → 1 · [Part 0 — Compatibility as a Product Contract](abi-series/00-product-contract.md) → 1 · [Part 1 — Foundations: From Source Code to a Running Process](abi-series/01-foundations.md) → 2 · [Part 2 — Symbol Contract Breaks](abi-series/02-symbol-contracts.md) → 2 · [Part 3 — Type Layout Breaks](abi-series/03-type-layout.md) → 6 · [Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| C++ library maintainer | 1 · [Part 1 — Foundations: From Source Code to a Running Process](abi-series/01-foundations.md) → 2 · [Part 4 — C++ ABI Specifics](abi-series/04-cpp-abi.md) → 2 · [Class Layout ABI & API: Problems and Detection](class-layout-abi.md) → 2 · [Part 6 — Subtle & Transitive Breaks](abi-series/06-transitive-breaks.md) → 6 · [Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| CI / release engineer | 0 · [How a Break Shows Up](how-a-break-shows-up.md) → 3 · [Compatibility Direction](compatibility-direction.md) → 4 · [Detecting Breaks: Evidence, Tools, and Why One Method Is Never Enough](abi-series/08-detection.md) → 5 · [Where in the Pipeline](where-in-the-pipeline.md) → 5 · [Report the Surface, Not Only the Breaks](surface-growth.md) → c1 · [Verdicts](verdicts.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| Distribution / package maintainer | 2 · [Part 5 — ELF & Linker-Level Concerns](abi-series/05-linker-elf.md) → 7 · [Dependency & Runtime Floors](dependency-floors.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| Product / SDK owner (several binaries) | 1 · [Part 0 — Compatibility as a Product Contract](abi-series/00-product-contract.md) → 3 · [Consumer Models](consumer-models.md) → 5 · [Where in the Pipeline](where-in-the-pipeline.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| Plugin / SDK author | 2 · [Part 2 — Symbol Contract Breaks](abi-series/02-symbol-contracts.md) → 3 · [Compatibility Direction](compatibility-direction.md) → 3 · [Consumer Models](consumer-models.md) → 6 · [Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| AI agent / automated reviewer | 0 · [How a Break Shows Up](how-a-break-shows-up.md) → c1 · [Verdicts](verdicts.md) → c2 · [Evidence & Detectability: What Each Method Can and Cannot See](evidence-and-detectability.md) | [Output Formats](../use/output-formats.md) |

<!-- END GENERATED: learning-paths -->

## The one idea to carry through the whole series

If you remember nothing else:

> **The compiler bakes the library's ABI facts — sizes, offsets, register
> choices, vtable slot numbers, symbol names — into every caller, as immediate
> constants, and never re-checks them.** When the library changes one of those
> facts in a later release, the old caller keeps using the old number. Nobody
> re-validates it. That is why an ABI break is *silent*: no linker error, often
> no crash, just wrong bytes at the wrong address.
>
> Every fix in [Part 7](abi-series/07-designing-for-stability.md) is therefore a
> variation on a single move: **stop publishing the fact** — hide it behind a
> pointer, a version node, or hidden visibility — so you stay free to change it.

abicheck exists to catch these breaks *before* they ship: it dumps a snapshot of
each binary, diffs them structurally, and classifies every difference into one of
five verdicts mapped to CI exit codes — see [Verdicts](verdicts.md).

Three things the series keeps coming back to, each owned by its own page:

- A public entry point's *runtime* call chain is not the consumer's contract;
  only what crosses the compile/link/load boundary is —
  [What Is Part of Your ABI Surface?](abi-surface.md).
- No single kind of evidence sees every break, and the artifact tiers (L0–L2)
  decide any `BREAKING` verdict while the build/source tiers (L3–L5) explain,
  scope and add but never delete an artifact-proven break — the *authority
  rule*, defined in [Evidence & Detectability](evidence-and-detectability.md#how-they-combine)
  and walked level by level in [What Each Level Sees](what-each-level-sees.md).
  Feeding abicheck the debug-enabled binary *and* the public headers is what
  gives it the most to work with —
  [Limitations § Recommendation](limitations.md#recommendation-feed-abicheck-so-debug-info-headers-for-the-best-result).
- The most realistic consumer-level test is to build an app against the old
  library, swap in the new one, and run it: `compare --used-by APP`, which is
  consumer-scoped where library `compare`/`scan` is contract-scoped —
  [Evidence & Detectability §4](evidence-and-detectability.md#4-app-mode-consumer-scoped-vs-library-compare-contract-scoped).

## ABI and API, defined

- **ABI** (Application Binary Interface) — the binary-level contract between a
  compiled library and its consumers: symbol names, calling conventions,
  struct/class layout, vtable order. Changing it can break *already-compiled*
  callers without anyone recompiling.
- **API** (Application Programming Interface) — the source-level contract
  (declarations, signatures, semantics) a caller compiles against. Changing it
  can break *recompilation* even when the ABI is intact.

The two overlap but neither contains the other: a renamed enum member breaks
the API and leaves the ABI alone; a reordered struct field breaks the ABI and
leaves the API alone. [Part 1](abi-series/01-foundations.md) develops both
from the ground up.

Examples are ELF/Linux and Itanium-C++-ABI flavoured unless a page says
otherwise — [Part 5](abi-series/05-linker-elf.md#pecoff-and-mach-o-parallels)
carries the PE/COFF and Mach-O parallels and the
[Platform Support reference](../reference/platforms.md) the exact matrix.

## Where the tool track begins

The [User Guide](../start/getting-started.md) takes you from install and a
first check to CI integration, and [Choose Your Workflow](../start/choose-your-workflow.md)
maps your situation to the right command. The **Concepts** sequence above is
the tool's own mental model — what a verdict means, what each evidence source
can see, and how the pipeline is built — and the
[Examples & Case Encyclopedia](../reference/examples/index.md) holds every
break in this series as a compilable, runnable fixture.
