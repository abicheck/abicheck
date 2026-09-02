---
doc_type: hub
audience:
  - library-maintainer
  - ci-owner
level: beginner
lifecycle: active
generated: false
---
# ABI/API Compatibility — A Learning Series

Nine steps, from "what is an ABI?" to checking a multi-binary product in
CI. Steps 1–5 need no abicheck knowledge at all; the tool enters at Step 6.
Read the steps in order — each assumes the ones before it — and follow the
**Ladder** line at the bottom of every page to the next one. The sidebar
lists the same steps in the same order.

## Start here

- **New to the topic?** Read [ABI in Five Minutes](abi-series/abi-in-5-minutes.md),
  then keep following the ladder.
- **Already know what an ABI is?** Start at Step 2 with
  [Part 0 — Compatibility as a Product Contract](abi-series/00-product-contract.md),
  or take your role's shortcut below.
- **Looking something up?** The [ABI Cheat Sheet](abi-cheat-sheet.md) and
  the [Glossary](abi-series/glossary.md).

## The path

<!-- BEGIN GENERATED: learning-ladder -->
<!-- This block is rendered from docs/_meta/learning-ladder.yaml by gen_learning_ladder.py — do not edit by hand. Edit the YAML and run `python scripts/gen_learning_ladder.py`. -->

1. **Start Here** *(beginner)* — [ABI in Five Minutes](abi-series/abi-in-5-minutes.md) → [How a Break Shows Up](how-a-break-shows-up.md) → [ABI Cheat Sheet](abi-cheat-sheet.md) → [Glossary](abi-series/glossary.md)
2. **Foundations** *(beginner → intermediate)* — [Part 0 — Compatibility as a Product Contract](abi-series/00-product-contract.md) → [Part 1 — Foundations](abi-series/01-foundations.md) → [What Is Part of Your ABI Surface?](abi-surface.md)
3. **How Breaks Happen** *(intermediate)* — [Part 2 — Symbol Contract Breaks](abi-series/02-symbol-contracts.md) → [Part 3 — Type Layout Breaks](abi-series/03-type-layout.md) → [Part 4 — C++ ABI Specifics](abi-series/04-cpp-abi.md) (go deeper: [Class Layout ABI & API](class-layout-abi.md), [Exception Unwinding](exception-unwinding-abi.md), [Modern C/C++ and Toolchain ABI Hazards](modern-cpp-toolchain-hazards.md)) → [Part 5 — ELF & Linker-Level Concerns](abi-series/05-linker-elf.md) (go deeper: [The MSVC/PE ABI Model](msvc-pe-abi-model.md)) → [Part 6 — Subtle & Transitive Breaks](abi-series/06-transitive-breaks.md)
4. **Designing for Stability** *(intermediate)* — [Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md)
5. **Define Your Contract** *(intermediate)* — [Compatibility Direction](compatibility-direction.md) → [Consumer Models](consumer-models.md) → [Build Profile Comparability](build-profile-comparability.md) → [Static & Header-Only Contracts](static-and-header-only.md) — also: [Contract-Aware Compatibility](contract-aware-compatibility.md) (Concepts tab)
6. **Detect Breaks** *(intermediate)* — [Detecting Breaks](abi-series/08-detection.md) → [Assurance Beyond Static Checking](assurance-methods.md) — also: [Evidence & Detectability](evidence-and-detectability.md) (Concepts tab); [What Each Level Sees](what-each-level-sees.md) (Concepts tab)
7. **In Practice** *(intermediate)* — [Where in the Pipeline](where-in-the-pipeline.md) → [Report the Surface, Not Only the Breaks](surface-growth.md) → [Rollout and Governance](rollout-and-governance.md) → [Triage a Suspicious Finding](triage-a-finding.md) — also: [Baseline Management](../use/baseline-management.md) (tool guide)
8. **At Scale** *(advanced)* — [Products, Not Libraries](products-not-libraries.md) → [Template- and Header-Heavy Libraries](template-heavy-libraries.md) → [How System Libraries Stay Compatible](system-library-discipline.md) → [Dependency & Runtime Floors](dependency-floors.md) → [Environment & Toolchain Drift](environment-drift.md) → [Packages and Consumers](packages-and-consumers.md)
9. **Beyond Static ABI** *(advanced)* — [Behavioral & Semantic Compatibility](behavioral-compatibility.md) → [Data, Wire & Storage Compatibility](data-wire-compatibility.md) → [Ownership & Lifetime Contracts](ownership-and-lifetime.md) → [Concurrency & Initialization Contracts](concurrency-and-initialization.md)

**Concepts tab — the tool's own sequence**

- **Concepts c1 · Reading a result** *(intermediate)* — [Verdicts](verdicts.md) → [Contract-Aware Compatibility](contract-aware-compatibility.md)
- **Concepts c2 · The evidence model** *(intermediate)* — [Evidence & Detectability](evidence-and-detectability.md) → [What Each Level Sees](what-each-level-sees.md) → [ELF-Only Mode and Symbol Filtering](elf-symbol-filtering.md) → [Limitations & Known Boundaries](limitations.md)
- **Concepts c3 · Internals** *(advanced)* — [Architecture](architecture.md) → [Source & Build Data](build-source-data.md) → [Graph Coverage & Negative Evidence](graph-coverage.md) → [Unified Impact Assessment](impact-analysis.md)

<!-- END GENERATED: learning-ladder -->

A "go deeper" page is an optional side read; it returns you to the step it
hangs from. An "also" page belongs to another tab but is worth reading at
that point.

## Shortcuts by role

Each shortcut walks the steps in order, skipping what the role does not
need, and ends with the tool page to open next.

<!-- BEGIN GENERATED: learning-paths -->
<!-- This block is rendered from docs/_meta/learning-ladder.yaml by gen_learning_ladder.py — do not edit by hand. Edit the YAML and run `python scripts/gen_learning_ladder.py`. -->

| Role | Read, in order (step · page) | Then |
|---|---|---|
| New C/C++ library author | 1 · [ABI in Five Minutes](abi-series/abi-in-5-minutes.md) → 1 · [How a Break Shows Up](how-a-break-shows-up.md) → 2 · [Part 0 — Compatibility as a Product Contract](abi-series/00-product-contract.md) → 2 · [Part 1 — Foundations](abi-series/01-foundations.md) → 3 · [Part 2 — Symbol Contract Breaks](abi-series/02-symbol-contracts.md) → 3 · [Part 3 — Type Layout Breaks](abi-series/03-type-layout.md) → 4 · [Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| C++ library maintainer | 2 · [Part 1 — Foundations](abi-series/01-foundations.md) → 3 · [Part 4 — C++ ABI Specifics](abi-series/04-cpp-abi.md) → 3 · [Class Layout ABI & API](class-layout-abi.md) → 3 · [Part 6 — Subtle & Transitive Breaks](abi-series/06-transitive-breaks.md) → 4 · [Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md) → 8 · [Template- and Header-Heavy Libraries](template-heavy-libraries.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| CI / release engineer | 1 · [How a Break Shows Up](how-a-break-shows-up.md) → 5 · [Compatibility Direction](compatibility-direction.md) → 6 · [Detecting Breaks](abi-series/08-detection.md) → 7 · [Where in the Pipeline](where-in-the-pipeline.md) → 7 · [Report the Surface, Not Only the Breaks](surface-growth.md) → c1 · [Verdicts](verdicts.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| Distribution / package maintainer | 3 · [Part 5 — ELF & Linker-Level Concerns](abi-series/05-linker-elf.md) → 8 · [Products, Not Libraries](products-not-libraries.md) → 8 · [How System Libraries Stay Compatible](system-library-discipline.md) → 8 · [Dependency & Runtime Floors](dependency-floors.md) → 8 · [Packages and Consumers](packages-and-consumers.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| Product / SDK owner (several binaries) | 2 · [Part 0 — Compatibility as a Product Contract](abi-series/00-product-contract.md) → 5 · [Consumer Models](consumer-models.md) → 7 · [Where in the Pipeline](where-in-the-pipeline.md) → 8 · [Products, Not Libraries](products-not-libraries.md) → 8 · [Template- and Header-Heavy Libraries](template-heavy-libraries.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| Plugin / SDK author | 3 · [Part 2 — Symbol Contract Breaks](abi-series/02-symbol-contracts.md) → 4 · [Part 7 — Designing for Stability](abi-series/07-designing-for-stability.md) → 5 · [Compatibility Direction](compatibility-direction.md) → 5 · [Consumer Models](consumer-models.md) → 7 · [Rollout and Governance](rollout-and-governance.md) | [Choose Your Workflow](../start/choose-your-workflow.md) |
| AI agent / automated reviewer | 1 · [How a Break Shows Up](how-a-break-shows-up.md) → 7 · [Triage a Suspicious Finding](triage-a-finding.md) → c1 · [Verdicts](verdicts.md) → c2 · [Evidence & Detectability](evidence-and-detectability.md) | [Output Formats](../use/output-formats.md) |

<!-- END GENERATED: learning-paths -->

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
leaves the API alone. Examples are ELF/Linux and Itanium-C++-ABI flavoured
unless a page says otherwise; [Part 5](abi-series/05-linker-elf.md#pecoff-and-mach-o-parallels)
carries the PE/COFF and Mach-O parallels and the
[Platform Support reference](../reference/platforms.md) the exact matrix.

## After the series

- [Getting Started](../start/getting-started.md) — install abicheck, run a
  first check, wire it into CI.
- [Choose Your Workflow](../start/choose-your-workflow.md) — maps your
  situation to the right command.
- [Examples & Case Encyclopedia](../reference/examples/index.md) — every
  break in this series as a compilable, runnable fixture.
