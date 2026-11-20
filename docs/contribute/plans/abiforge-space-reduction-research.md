---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Abiforge: compatibility-space coverage and justified test reduction

**Status:** Proposed research plan, 2026-11-20. This document does not change
coverage claims, runtime behavior, or CI selection. Initial local pilot results
are exploratory; publishing reproducible receipts is an acceptance condition below.

## Problem and intended result

A maintainer needs to know which compatibility obligations a product creates,
which are checked with the available evidence, and how much validation costs.
Counting generator operators or finding kinds does not answer that question:
a model can cover every class it lists while omitting a language mechanism,
composition context, platform, or consumer use.

The research goal is to derive exact reductions for selected contract fragments,
and conservative or explicitly bounded alternatives elsewhere. The deliverable
is a product-specific coverage profile and validation plan, with the assumptions
behind every reduction. It is not a percentage of all C++ or a universal cutoff
on program size, nesting depth, or number of interacting features.

This plan owns the research questions, literature reuse, and acceptance criteria
for reduction beyond bounded enumeration. It complements, rather than replaces:

- [Formal methods and generator completeness](formal-methods-and-generator-completeness.md):
  existing formal experiments, generator enumeration and oracle claims.
- [Completeness by evidence and the generator-validator](abicheck-abiforge-completeness.md):
  evidence-conditioned checker coverage and integration workstreams.
- [Closed layout fragment](https://github.com/abicheck/abicheck/blob/b0b8793989c150a5b05f48e4e999a7df4219365a/formal/FRAGMENT.md) and
  [record-edit fragment](https://github.com/abicheck/abicheck/blob/b0b8793989c150a5b05f48e4e999a7df4219365a/formal/RECORD_EDIT_FRAGMENT.md): existing
  bounded spaces and their empirical/assumed/proved boundaries.

## Research questions

1. **Scope:** what independent grammar and ABI rules define the obligation space?
   Which constructs, edits, consumer contexts and configurations remain outside it?
2. **Reduction:** which observations admit a finite exact abstraction, an inductive
   rule, or a proved small-counterexample bound? Which compositions preserve it?
3. **Implementation transfer:** when does checking representatives validate the
   checker itself, rather than only its abstract specification?
4. **Relevance and cost:** how much of real product contracts falls inside these
   fragments, and do reductions save time after analysis and extraction overhead?

An obligation is an entity or relation in the public contract, a change class,
a consumer context, and a feasible configuration. Identify old and new contexts:
a new dependency can invalidate selection based on the old graph alone.

## Evidence vocabulary and denominators

| Result | Permitted claim | Not established |
|---|---|---|
| Modeled class has an operator | Generation support for that class | Completeness of the class taxonomy |
| Exhaustive bounded corpus | Every valid case within explicit bounds ran | Arbitrary sizes, nesting or interactions |
| Exact abstraction / quotient | A stated observation is preserved under its premises | Numeric values or other observations omitted from the abstraction |
| Sound overapproximation | A proved safe conclusion is valid; unknown may remain | Complete classification without false alarms |
| Safe regression selection | Fault-revealing tests from an existing suite are retained under stated assumptions | Detection of faults absent from that suite |
| Constrained t-way coverage | All feasible assignments to t factors are represented | Faults requiring more factors or a correct oracle |
| Statistical evidence | A distribution-specific estimate with sampling assumptions | Adversarial worst-case completeness |

Report model coverage, generated cases, executed cells, oracle-confirmed cases,
observable obligations, exact matches, false positives, misses and unknowns
separately. An unavailable toolchain is not an impossible configuration; an
unobservable finding is not a clean compatibility result. Retain exclusions in
the product profile instead of silently shrinking the denominator.

A proof that two cases have the same ABI observation does not prove that a buggy
checker treats them identically. Transfer needs a refinement/factorization proof,
or remains experimental evidence. Do not replace the full regression suite merely
because all model classes have representatives.

## Reuse existing work

| Work | Reuse in this project | Boundary / integration cost |
|---|---|---|
| Ramananandro, Dos Reis, Leroy, **Formal verification of object layout for C++ multiple inheritance**, POPL 2011 ([paper, Coq sources](https://gallium.inria.fr/~tramanan/cpp/object-layout/)) | Well-formed hierarchies, layout correctness constraints, common-vendor ABI algorithm, empty-base optimization proofs | Not a cross-version compatibility theorem. Published development targets Coq 8.2pl1; reproduction/porting must be evaluated before committing to reuse |
| Cousot & Cousot, **Abstract interpretation**, POPL 1977 ([author copy](https://cs.nyu.edu/~pcousot/COUSOTpapers/POPL77.shtml)) | Sound abstraction, fixpoint computation, relation between abstract and concrete semantics | Soundness alone does not imply exactness; retain unknown/spurious alarms |
| Clarke et al., **Counterexample-Guided Abstraction Refinement**, CAV 2000 ([DOI](https://doi.org/10.1007/10722167_15), [author repository](https://doi.org/10.1184/R1/6604547)) | Refine the model only when a counterexample exposes a missing distinction | Splitting classes after sampled disagreements is only CEGAR-inspired until soundness obligations are supplied |
| Jacobs & Sakr, **Analyzing Guarded Protocols: Better Cutoffs, More Systems, More Expressivity**, 2017 ([preprint](https://arxiv.org/abs/1707.01369)) | Small-counterexample/cutoff proof structure | Protocol-specific bounds cannot be reused as C++ depth bounds |
| Boyapati, Khurshid, Marinov, **Korat: Automated Testing Based on Java Predicates**, ISSTA 2002 ([paper](https://web.stanford.edu/class/archive/cs/cs295/cs295.1086/papers/issta02.pdf)) | Independent validity predicate, finitization, pruning, nonisomorphic input generation | Exact within the bound; naming or ordering symmetries need a property-preservation argument |
| Cobleigh, Giannakopoulou, Pasareanu, **Learning Assumptions for Compositional Verification**, TACAS 2003 ([related technical report](https://ntrs.nasa.gov/citations/20030017771)) | Assume-guarantee summaries and explicit environment obligations | Nested types are substitutable only in contexts proved to respect the summary |
| Rothermel & Harrold, **A Safe, Efficient Regression Test Selection Technique**, TOSEM 1997 ([paper](https://www.cs.purdue.edu/homes/xyzhang/spring07/Papers/p173-rothermel.pdf)) | Old/new graph comparison and conservative change-impact selection | Safety is relative to the existing tests and analysis assumptions |
| Kuhn, Kacker, Lei, **Practical Combinatorial Testing**, NIST SP 800-142, 2010 ([publication and tools](https://csrc.nist.gov/pubs/sp/800/142/final)) | Constrained covering arrays and t-way coverage; evaluate ACTS before implementing another selector | Empirical interaction strength is not a theorem excluding higher-order faults |
| Lopes et al., **Provably Correct Peephole Optimizations with Alive**, PLDI 2015 ([paper](https://www.microsoft.com/en-us/research/publication/provably-correct-peephole-optimizations-alive/)) | Small rule language, solver obligations, counterexamples and generated implementation | LLVM rewrite semantics are not ABI compatibility semantics |
| Yang et al., **Finding and Understanding Bugs in C Compilers**, PLDI 2011 ([paper](https://www.flux.utah.edu/paper/yang-pldi11/)) | Valid generation, differential compiler checks and unusual feature combinations | Cross-tool agreement is evidence, not an independent oracle or completeness proof |

## Work packages and acceptance criteria

### R1 — Independent obligation inventory

Build a machine-readable inventory from selected language and ABI rules, not the
operator registry. Separate source/API, binary/ABI and deployment obligations.
Record consumer use (pointer, by-value, inheritance, inline instantiation),
configuration conditions and interactions. Link current operators and formal
fragments to entries; preserve entries with no implementation.

**Done when:** every in-scope entry has its specification reference, conditions,
observation, generating mechanism or explicit gap, and required evidence. A
small product's extracted contract can be mapped to the inventory with unknowns
listed. An independent review examines missing mechanisms and compositions.

### R2 — Exact reduction for scalar-layout suffixes

Start with one inserted scalar field in ordinary sequential layout. Common
suffix fields have size = alignment in {1,2,4,8}. Observe only whether each
suffix field moves, not total size, argument classification or API type identity.

Candidate summary: `(old_cursor mod 8, new_cursor - old_cursor)`. Insertion plus
padding gives a nonnegative initial displacement at most 15; subsequent alignment
keeps it at most 16. Translation by a multiple of 8 commutes with every permitted
alignment. These are candidate induction premises for arbitrary finite suffixes.

A preliminary local pilot reported 136 reachable states, 544 transitions, 58
minimized output classes, 2,796,192 concrete bounded sequences and 1,088 GCC
struct-pair probes with no disagreement. Twenty arithmetic obligations were
checked by Z3 over unbounded integers. These numbers are **exploratory**, not a
checked-in coverage floor or a proof of abicheck. Publish scripts, environment,
input hashes and receipts before using them as reproducible paper evidence.

**Done when:** Lean checks initialization, transition preservation and observation
preservation, then their induction over an arbitrary suffix, without `sorry` or
new axioms. Reuse existing layout semantics or prove correspondence to it. Export
representatives and compare actual compiler probes and checker findings. Include
negative controls for omitted residue, mistaken displacement bounds and errors
triggered only by long concrete records. State exactly which layer each control tests.

**Stop/refine if:** the summary is insufficient or realization disagrees with the
model. A counterexample is a useful result; never redefine scope silently to hide it.

### R3 — Composition and additional fragments

Investigate enum propagation, ABI argument/return classification and ordinary
nested layout. For each, state summaries and allowed parent contexts first.
Prove congruence under those contexts before claiming arbitrary nesting.
Packing, bit-fields, unions, inheritance and multiple simultaneous edits are
separate extensions with explicit obligations, not automatically covered cases.

**Done when:** at least two additional families have a reproducible bounded result,
and at least one has a proved reduction or an explicit counterexample showing why
it needs a richer summary. Avoid a universal C++ cutoff claim.

### R4 — Product profile and safe selection

Use the old and new contract/dependency graphs to identify affected obligations.
Unknown edges or missing extraction evidence cause conservative retention or an
explicit unknown, never automatic exclusion. Start with a simple C library and a
bounded public surface of oneDAL. Add backend/CPU axes only where they change the
contract or an explicitly selected behavioral/performance property.

**Done when:** the report states modeled, supported, validated and unknown
obligations; the proposed selection is checked against a full bounded baseline;
new-dependency and missing-edge controls fail unsafe selectors. Compare with
all-tests, existing tier selection and constrained t-way baselines. Preserve
consumer context, compiler versions and evidence provenance in every result.

### R5 — Cost and validation study

Measure generation, build, extraction, comparison, oracle execution and selection
separately. Include memory and selected-case counts. Repeated measurements need
fixed environments and concurrency records; batching and caching gains must not
be attributed solely to mathematical reduction.

Evaluate real regression cases where available, artificial mutants and held-out
large/deep cases separately. A mutation score is not a real-world correctness
probability. A statistical bound needs a declared sampling distribution and
independence assumptions; generator output is not automatically independent.

**Done when:** a reader can reproduce cost/coverage tradeoffs and identify the
trust assumptions. Report failures and exclusions alongside successful reductions.

## Tools and trust boundaries

- **Lean:** induction, exact quotient/congruence and completeness theorems. Keep
  the existing formal toolchain unless an arithmetic requirement justifies a change.
- **Z3:** counterexample search and arithmetic obligations; distinguish bounded
  exploration from unbounded solver checks and retain solver-trust assumptions.
- **TLA+/TLC:** bounded safety checks for selection/execution/receipt lifecycle,
  such as unknown never becoming a clean result. Not the main layout proof tool.
- **GCC/Clang and consumers:** realization and observation checks, with compiler
  differences classified rather than voted away.
- **abiforge adapters:** implementation conformance; no accidental dependency on
  a checker's result when defining the expected result.

## Scope, effort and intermediate deliverables

Planning estimates for one or two experienced contributors, not commitments:

| Milestone | Rough effort | Independently useful result |
|---|---|---|
| R1 inventory | 1–2 person-weeks | Coverage profile with named gaps |
| R2 mechanized pilot and integration | 2–4 person-weeks | One exact reduction with implementation-transfer limits |
| R3 additional families | 4–8 person-weeks | Evidence that the approach generalizes, plus counterexamples |
| R4 product selection | 3–6 person-weeks | Conservative test guidance and measured product relevance |
| R5 evaluation and paper | Overlaps other packages | Reproducible tradeoff study; focused paper target of roughly 2–4 calendar months |

Full modern-C++ semantics, all ABI variants and verified extraction are not this
paper's deliverables. Multiple inheritance, templates/constraints and cross-ABI
composition may each require separate research. Assess the existing Coq model
before estimating a modern port.

Useful results can ship before a general proof: a reviewed scope inventory,
bounded completeness receipts, proved local rules, conservative selection relative
to a suite, interaction-coverage curves, reproducible negative controls and
product-specific unsupported-feature profiles. Label each with its own guarantee.

## Tracking

- [ ] R1 inventory schema and independent review
- [ ] R2 pilot scripts and receipts published
- [ ] R2 Lean induction and model correspondence
- [ ] R2 compiler/checker integration and mutation controls
- [ ] R3 second and third fragment experiments
- [ ] R4 real-product mapping and conservative selector
- [ ] R5 repeatable cost study and paper claims audit

The next implementation PR should publish R2's proof, executable reproduction and
negative controls together. It must not lower an existing test tier's coverage
floor or claim a production speedup before the comparison study is complete.
