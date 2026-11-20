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

## R2 follow-up measurements (pinned exploratory run)

Measured against b0b8793989c150a5b05f48e4e999a7df4219365a on Linux x86-64,
GCC 14.2, C11 ordinary scalar structs. This fragment supplies a foundation for
C++ work; it does not measure coverage of the entire C++ language.

| Evidence route | Pair instances | Common member offsets | Offset mismatches |
|---|---:|---:|---:|
| ELF + DWARF | 3,981 | 19,678 | 0 |
| Stripped ELF + CastXML headers | 1,293 | 10,030 | 0 |

Suites overlap. Executed offsetof probes supplied expected offsets; the harness
checked both extraction and exact findings, including unchanged members. Identity
controls reported no record findings. Size/alignment/addition findings were
recorded separately: absence of an offset change is not overall ABI compatibility.
Four header suites also passed through the actual abiforge snapshot/answer adapter;
its verdict and kind sets matched the public CLI. Supplementary Clang call/reference
graphs were unavailable; only declaration graphs were collected.

The exploratory Lean 4.15.0 proof checks trace preservation for arbitrary finite
suffixes in the specified scalar layout model. Reachable state enumeration gives
136 states and 544 transitions. This proves the model's moved/not-moved abstraction,
not the implementation of GCC or abicheck. Exact offset agreement above is empirical.

Mutation experiments expose the implementation-proof gap: 544 transition witnesses
detected all 544 injected output flips but only 104/544 specified successor faults.
Adding a byte continuation detected all 544 of that successor family. Neither
short suite detected an artificial fault triggered only after eleven fields; longer
held-out cases did. These are injected mutants, not discovered abicheck bugs.

For eight prefix residues, four insertions and suffix length at most d, raw cases
number 32 * sum(4**n for n in range(1, d+1)): 2,688 at d=3 and 2,796,192 at d=8.
Do not turn this count ratio into a checker speedup: horizons and implementation
coverage differ. The transferable result is the method of proving a sufficient
state before selecting representatives.

The proof/harness/receipts remain exploratory artifacts pending the R2 implementation
PR; tracking boxes above deliberately remain open. Next test nested records and
arrays, including size and alignment in the observable contract. An inner size
change repeated by an array may invalidate the scalar displacement bound.

## Nested-array counterexample and reproducible probe


Run `python3 research/layout-cutoff-pilot/nested/run.py` from the repository root.
The script generates C11 in a temporary directory, compiles it with GCC, executes
the probe, and writes `results.json`. The JSON records the compiler, target,
counts by array length, and concrete witnesses. The ground truth comes from
compiled `sizeof`, `_Alignof`, and `offsetof` expressions. The Python model
separately computes placement, record padding and array stride.

On GCC 14.2.0 (x86_64-linux-gnu), all 55,296 observations agreed across 3,072
pairs. The matrix crosses two original scalar sizes and an inserted scalar
size (each from 1, 2, 4, 8), three insertion positions, eight array lengths
(1, 2, 3, 4, 8, 16, 32, 64), and two tail sizes (1 and 8). The enclosing
record has a byte prefix, the array, and the scalar tail.

The scalar proof in exploratory LayoutCutoff.lean proof preserves a
cursor displacement of at most 16 for an unchanged scalar continuation.
That state invariant cannot carry over to an enclosing layout with a resized
array element. With the following inner definitions, common member offsets
and alignment stay fixed, while the inner size increases from 16 to 24 bytes.

~~~c
struct Old { uint8_t a; uint64_t b; };
struct New { uint8_t a; uint64_t b; uint8_t added; };
~~~

With three elements, both arrays begin at offset 8. The byte tail moves from
56 to 80 (24 bytes). Its shift for any representable positive length N under
this layout model is 8*N. Separately, a change of inner alignment plus record
padding can exceed 16 even at length 1. In the generated matrix, inserting
uint64_t between two byte members changes inner size from 2 to 24 and moves
the outer byte tail by 29 bytes.

For a fixed grammar with finite bounds on lengths and depth, an exact model can
retain each side's size and alignment and use size times length when composing
arrays. If lengths are symbolic, 8*N shows why exact numeric displacement
requires a symbolic quantity or a bound that depends on the length. This
counterexample leaves open smaller finite abstractions for Boolean observations
such as moved versus unchanged. The experiment checks ordinary sequential
records with one level of nesting, one insertion, and a single compiler/target.
It supplies no checker coverage measurement or mechanized aggregate proof.

Save the following as `nested_probe.py` and run `python nested_probe.py`; only GCC and Python are required.

```python
"""Compare ordinary aggregate layout against executed C11 compiler probes."""
import argparse
from itertools import product
import json
from pathlib import Path
import subprocess
import tempfile

SCALARS = {1: "uint8_t", 2: "uint16_t", 4: "uint32_t", 8: "uint64_t"}
COUNTS = (1, 2, 3, 4, 8, 16, 32, 64)


def align(cursor, alignment):
    return (cursor + alignment - 1) // alignment * alignment


def record(fields):
    cursor = 0
    offsets = {}
    alignment = max(a for _, _, a in fields)
    for name, size, a in fields:
        offsets[name] = align(cursor, a)
        cursor = offsets[name] + size
    return align(cursor, alignment), alignment, offsets


def run(gcc):
    declarations = ["#include <stdint.h>", "#include <stddef.h>",
                    "#include <stdio.h>", "#include <limits.h>",
                    '_Static_assert(CHAR_BIT == 8, "byte width");']
    for size, ctype in SCALARS.items():
        declarations.append(
            f'_Static_assert(sizeof({ctype}) == {size} && '
            f'_Alignof({ctype}) == {size}, "scalar layout");'
        )
    probes, cases = [], []
    for first, second, inserted, pos, count, tail in product(
        SCALARS, SCALARS, SCALARS, range(3), COUNTS, (1, 8)
    ):
        case = len(cases)
        expected = []
        for side in ("old", "new"):
            members = [("a", first), ("b", second)]
            if side == "new":
                members.insert(pos, ("added", inserted))
            inner, outer = f"I{case}_{side}", f"O{case}_{side}"
            fields = " ".join(f"{SCALARS[s]} {name};" for name, s in members)
            declarations.extend([
                f"struct {inner} {{ {fields} }};",
                f"struct {outer} {{ uint8_t prefix; struct {inner} items[{count}];"
                f" {SCALARS[tail]} tail; }};",
            ])
            size, alignment, offsets = record([(name, s, s) for name, s in members])
            osize, oa, oo = record([
                ("prefix", 1, 1), ("items", size * count, alignment),
                ("tail", tail, tail),
            ])
            expressions = [
                f"sizeof(struct {inner})", f"_Alignof(struct {inner})",
                f"offsetof(struct {inner}, a)", f"offsetof(struct {inner}, b)",
                f"sizeof(struct {outer})", f"_Alignof(struct {outer})",
                f"offsetof(struct {outer}, items)", f"offsetof(struct {outer}, tail)",
                f"offsetof(struct {outer}, items[{count - 1}].b)",
            ]
            expected.append([size, alignment, offsets["a"], offsets["b"], osize,
                             oa, oo["items"], oo["tail"],
                             oo["items"] + (count - 1) * size + offsets["b"]])
            probes.append('printf("' + " ".join(["%zu"] * len(expressions))
                          + '\\n", ' + ", ".join(expressions) + ");")
        cases.append(([first, second, inserted, pos, count, tail], expected))

    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "probe.c"
        binary = source.with_suffix("")
        source.write_text("\n".join(declarations + ["int main(void) {"] + probes + ["}"]))
        subprocess.run([gcc, "-std=c11", "-pedantic-errors", "-O0",
                        str(source), "-o", str(binary)], check=True)
        rows = [list(map(int, line.split())) for line in
                subprocess.check_output([binary], text=True).splitlines()]

    assert len(rows) == 2 * len(cases) and all(len(row) == 9 for row in rows)
    by_count = {}
    witness = None
    repetition_witness = None
    mismatches = 0
    for index, (inputs, expected) in enumerate(cases):
        old, new = rows[2 * index:2 * index + 2]
        mismatches += sum(a != b for actual, model in zip((old, new), expected)
                          for a, b in zip(actual, model))
        count = inputs[4]
        stats = by_count.setdefault(count, dict(pairs=0, array_gap_gt_16=0,
                                                tail_gap_gt_16=0, max_tail_gap=0))
        array_gap = count * (new[0] - old[0])
        tail_gap = new[7] - old[7]
        stats["pairs"] += 1
        stats["array_gap_gt_16"] += array_gap > 16
        stats["tail_gap_gt_16"] += tail_gap > 16
        stats["max_tail_gap"] = max(stats["max_tail_gap"], tail_gap)
        if array_gap > 16 and tail_gap > 16 and (
            witness is None or (count, old[0], new[0]) < tuple(witness["rank"])
        ):
            witness = dict(rank=[count, old[0], new[0]], inputs=inputs,
                           old=old, new=new, array_gap=array_gap, tail_gap=tail_gap)
        if (old[1] == new[1] and 0 < new[0] - old[0] <= 16
                and array_gap > 16 and tail_gap > 16
                and (repetition_witness is None or
                     (count, old[0], new[0]) < tuple(repetition_witness["rank"]))):
            repetition_witness = dict(
                rank=[count, old[0], new[0]], inputs=inputs, old=old, new=new,
                array_gap=array_gap, tail_gap=tail_gap,
            )
    return dict(
        compiler=subprocess.check_output([gcc, "--version"], text=True).splitlines()[0],
        target=subprocess.check_output([gcc, "-dumpmachine"], text=True).strip(),
        input_order=["first_size", "second_size", "inserted_size",
                     "insertion_index", "array_length", "tail_size"],
        observation_order=["inner_size", "inner_alignment", "inner_a", "inner_b",
                           "outer_size", "outer_alignment", "array_offset",
                           "tail_offset", "last_element_b"],
        pairs=len(cases), observations=len(rows) * 9, mismatches=mismatches,
        max_inner_size_gap=max(rows[i+1][0]-rows[i][0] for i in range(0,len(rows),2)),
        by_count=by_count, witness=witness, repetition_witness=repetition_witness,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gcc", default="gcc")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("results.json"))
    args = parser.parse_args()
    results = run(args.gcc)
    args.out.write_text(json.dumps(results, indent=2) + "\n")
    raise SystemExit(bool(results["mismatches"] or not results["repetition_witness"]))

```

## R3: Boolean array-tail observation admits finite length representatives

For a fixed old/new element-layout pair, let S0,S1 be its sizes, A0,A1
its alignments, P the common prefix length, T the common scalar tail alignment,
and N the common positive array length. Assume ordinary layout, alignments in
{1,2,4,8}, no packing/overalignment and no arithmetic overflow. The observed offset is

`tail_i(N) = alignUp(alignUp(P, Ai) + N * Si, T)`.

**Claim (mathematical argument with solver-checked arithmetic lemmas):** for the
single Boolean observation `tail_0(N) != tail_1(N)`, lengths 1 through 15 suffice
as representatives of all positive lengths for each fixed layout pair.

- When S0=S1, replacing N with `1+(N-1)%8` preserves equality: both inputs to
  final alignment decrease by the same multiple of 8, which is divisible by T.
- When S0 differs from S1, replace N by `min(N,15)`. For N>=15 the larger-size
  operand exceeds the other before final rounding by at least N-7>=8. Prefix
  alignment can alter their difference by at most 7. Rounding to T<=8 cannot
  erase a gap of 8, so the two tail offsets are necessarily different.

This is a sufficient bound, not a minimality claim. Negative size changes are
handled by exchanging old and new. Exact offsets remain unbounded; this reduction
preserves only equality. It is not a proof that an arbitrary checker has no bugs
that depend on large lengths, and does not cover leaf fields, total size, calling
conventions, changed tail types or unequal old/new array lengths.

Experiment: 6,144 insertion/prefix/tail profiles, 1,591,296 Boolean comparisons
(lengths 1..256 plus 1024, one million and one billion), zero counterexamples.
A separate compiled sample used 2,304 pairs with lengths 7,15,16,31,255,1024:
41,472 sizeof/alignof/offsetof observations matched GCC 14.2 on x86-64. These
compiled observations check the arithmetic model, not abicheck detection.
Z3 5.4.1 returned UNSAT for the negations of 13 supporting arithmetic lemmas
(rounding slack, separation, translation for four alignments; positive size gap).
The full aggregate theorem has not yet been mechanized in Lean.

Reproduction: save the previous nested probe as `nested/run.py` and the script
below as its sibling `array-boolean/run.py`, then run the latter. It writes
`array-boolean/results.json`. Only Python, GCC and z3-solver are required.

```python
"""Finite length representatives for the offset of the first field after an array."""
import importlib.util, itertools, json
from pathlib import Path
import z3
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('nested',HERE.parent/'nested/run.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def up(x,a): return (x+a-1)//a*a
def tail(p,s,a,n,t):return up(up(p,a)+n*s,t)
checks=0; bad=[]; profiles=0
for first,second,insert,pos,p,t in itertools.product((1,2,4,8),(1,2,4,8),(1,2,4,8),range(3),range(8),(1,2,4,8)):
 old=[('a',first,first),('b',second,second)];new=old.copy();new.insert(pos,('added',insert,insert))
 so,ao,_=m.record(old);sn,an,_=m.record(new);profiles+=1
 for n in list(range(1,257))+[1024,1000000,1000000000]:
  representative=(n-1)%8+1 if so==sn else min(n,15)
  moved=tail(p,so,ao,n,t)!=tail(p,sn,an,n,t)
  predicted=tail(p,so,ao,representative,t)!=tail(p,sn,an,representative,t)
  checks+=1
  if moved!=predicted:bad.append([first,second,insert,pos,p,t,n,representative])
# Solver checks the sufficient inequalities, independently of concrete profiles.
obligations=[]
def prove(name,assumptions,claim):
 solver=z3.Solver();solver.set(timeout=10000);solver.add(*assumptions,z3.Not(claim));r=solver.check()
 obligations.append({'name':name,'negated_claim':str(r)})
 assert r==z3.unsat,(name,r)
x,y,n,d=z3.Ints('x y n d')
for a in (1,2,4,8):
 rounded=lambda v:((v+a-1)/a)*a
 prove('rounding_slack_'+str(a),[x>=0],z3.And(rounded(x)-x>=0,rounded(x)-x<=7))
 prove('separation_'+str(a),[x>=0,y>=x+8],rounded(y)>rounded(x))
 prove('translation_'+str(a),[x>=0,n>=0],rounded(x+8*n)==rounded(x)+8*n)
prove('positive_size_gap',[n>=15,d>=1],n*d-7>=8)
# Additional compiled sample: same generator, lengths outside prior short corpus.
m.COUNTS=(7,15,16,31,255,1024)
compiled=m.run('gcc')
assert not compiled['mismatches']
result={'profiles':profiles,'boolean_checks':checks,'counterexamples':bad,'z3':z3.get_version_string(),'obligations':obligations,'compiled':compiled,'representative_rule':'same element size: 1+(N-1)%8; different element size: min(N,15)','scope':'first scalar tail offset; identical prefix length and tail alignment; element alignments divide 8; positive N; ordinary layout; no packing or overflow'}
(HERE/'results.json').write_text(json.dumps(result,indent=2)+'\n')
assert not bad
print(json.dumps({k:v for k,v in result.items() if k not in ('compiled','obligations')},indent=2))
print('compiled pairs',compiled['pairs'],'observations',compiled['observations'])

```

## R3 extension: a vector of observations needs data-dependent representatives

The 1..15 rule is specific to the tail-offset observation. Expanding to arbitrary
old/new element changes and observing the last element's common field yields a
counterexample (this is a field reorder/array resize, not just the earlier insertion):

```c
struct Old { char pad[100]; char x; }; // size 101
struct New { char x; char pad[101]; }; // size 102
```

For arrays starting at zero, the last x has offset difference N-101. It differs
at N=15 but is equal at N=101. GCC confirmed old/new offsets 10200/10200 at 101;
at 100 they are 10099/10098, and at 102 they are 10301/10302. The enclosing
structure remains incompatible (its size differs); equality of this one offset
must not be interpreted as overall compatibility. Replacing 100 with K moves
the equality to N=K+1: no universal small maximum test length covers this family.

A finite **number of data-dependent representatives** is still possible for the
specified vector. For fixed element summaries, prefix and tail, partition N as
r+8*k, r in 1..8, k>=0. Each numeric observation is affine in k because every
rounding alignment divides 8. Therefore its old/new difference is b+d*k.
A nonconstant difference has at most one integer nonnegative root; a constant
one is either always zero or never zero. For each residue select all such roots
and one non-root. This preserves the whole vector of equality/inequality bits.
For m observations, 8*(m+1) representatives suffice (not necessarily minimally).
Their numeric lengths can be arbitrarily large; this is not a uniform depth cutoff.

The script below checks seven observations: element stride/alignment, array start,
tail offset, enclosing size, first-element field offset and last-element field
offset. Across 1,513 summary profiles and 391,867 sampled lengths, all affine
identities and representative-vector comparisons passed. Each comparison has
seven components. At most 15 representatives were selected in this sample; the
proved-by-argument upper bound is 64 for seven observations. Summary profiles
include arithmetic layouts not all instantiated as C definitions. Independently,
seven compiled counterexample lengths agreed with model offsets and outer sizes.
This is not another 391,867 compiler/checker runs.

Scope: one-dimensional arrays, one common byte field, fixed old/new layout
summaries, equal positive N and common prefix/tail, ordinary alignments dividing
8, no overflow/packing. All internal fields, variable array indices, multidimensional
arrays, inheritance and checker implementation conformance remain out of scope.
The affine argument is not yet a mechanized Lean theorem. Next connect this
selector to actual abiforge pairs and audit predictions against public checker
findings, retaining long controls for implementation bugs.

Self-contained reproduction: save as `array_vector.py`, run with Python and GCC.
It writes `results.json` beside the script.

```python
"""Symbolic residue representatives for a vector of aggregate-layout equalities."""
import itertools,json,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
def up(x,a):return (x+a-1)//a*a
def obs(s,a,o,p,t,n):
 start=up(p,a);tail=up(start+n*s,t)
 return [s,a,start,tail,up(tail+t,max(a,t)),start+o,start+(n-1)*s+o]
def diff(pair,n):
 old,new,p,t=pair
 return [y-x for x,y in zip(obs(*old,p,t,n),obs(*new,p,t,n))]
def bits(pair,n):return [x!=0 for x in diff(pair,n)]
def reps(pair):
 result={}
 for r in range(1,9):
  b=diff(pair,r);d=[y-x for x,y in zip(b,diff(pair,r+8))]
  roots={-x//s for x,s in zip(b,d) if s and (-x)%s==0 and -x//s>=0}
  # Every non-root has the same equality vector; include one non-root.
  other=0
  while other in roots:other+=1
  result[r]={'base':b,'slope':d,'roots':sorted(roots),'other':other}
 return result
checks=0;affine_checks=0;maximum=0
# Summaries are valid layout arithmetic inputs, not all generated C type definitions.
layouts=[(s,a,o) for a in (1,2,4,8) for s in range(a,33,a) for o in (0,s-1)]
# Deterministic cross-section including arbitrary offset moves, size increases/decreases.
pairs=[(layouts[i],layouts[j],p,t) for i in range(0,len(layouts),7) for j in range(0,len(layouts),9) for p in (0,1,7) for t in (1,8)]
witness=((101,1,100),(102,1,0),0,1);pairs.append(witness)
for pair in pairs:
 table=reps(pair);maximum=max(maximum,sum(len(x['roots'])+1 for x in table.values()))
 for n in list(range(1,258))+[1024,1000000]:
  r=(n-1)%8+1;k=(n-r)//8;v=table[r]
  assert diff(pair,n)==[b+k*d for b,d in zip(v['base'],v['slope'])]
  affine_checks+=1
  selected=k if k in v['roots'] else v['other']
  assert bits(pair,n)==bits(pair,r+8*selected)
  checks+=1
# Independently compile the counterexample at and around its late equality.
source=['#include <stddef.h>','#include <stdio.h>',
 'struct Old { char pad[100]; char x; };','struct New { char x; char pad[101]; };','int main(void){']
lengths=(1,15,16,100,101,102,1000)
for n in lengths:
 source += [f'struct O{n} {{struct Old a[{n}];char tail;}};',f'struct N{n} {{struct New a[{n}];char tail;}};',f'printf("{n} %zu %zu %zu %zu\\n",offsetof(struct O{n},a[{n-1}].x),offsetof(struct N{n},a[{n-1}].x),sizeof(struct O{n}),sizeof(struct N{n}));']
source+=['return 0;}']
with tempfile.TemporaryDirectory() as tmp:
 c=Path(tmp)/'probe.c';exe=Path(tmp)/'probe';c.write_text('\n'.join(source))
 subprocess.run(['gcc','-std=c11',str(c),'-o',str(exe)],check=True)
 rows=[list(map(int,line.split())) for line in subprocess.check_output([str(exe)],text=True).splitlines()]
for n,old,new,os,ns in rows:
 assert [new-old,ns-os]==[diff(witness,n)[6],diff(witness,n)[4]]
assert bits(witness,15)[6] and not bits(witness,101)[6]
result={'profiles':len(pairs),'vector_checks':checks,'affine_vector_checks':affine_checks,'observations_per_vector':7,'max_selected_lengths':maximum,'counterexample_compiler_rows':rows,'counterexample_columns':['N','old_last_x','new_last_x','old_size','new_size'],'compiler':subprocess.check_output(['gcc','--version'],text=True).splitlines()[0],'observation_order':['stride','element_alignment','array_start','tail_offset','outer_size','first_field_offset','last_field_offset'],'scope':'ordinary one-dimensional arrays, fixed old/new element summaries, same P/T/N, alignments divide 8, integer arithmetic, one common byte field'}
(HERE/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

```
