---
doc_type: explanation
audience:
  - library-maintainer
level: advanced
canonical_for:
  - behavioral-compatibility
depends_on:
  - abicheck/checker.py
lifecycle: active
generated: false
---

# Behavioral & Semantic Compatibility

[Compatibility as a Product Contract §2](abi-series/00-product-contract.md#2-compatibility-is-not-one-question-name-which-kind-you-mean)
names eight compatibility dimensions and is explicit that a static ABI/API
checker is strongest on two of them — source and binary compatibility — and
weakest on this one. This page is the fuller treatment that dimension needs:
what behavioral compatibility actually means, why static comparison
structurally cannot decide it, and what to do instead.

## The question this dimension answers

Source and binary compatibility both ask a *shape* question: does this
declaration still compile against, or link against, that one? Behavioral
compatibility asks a different question entirely: **for the same inputs,
does the operation still produce the same outputs and the same
side effects?**

A change can pass every source and binary check and still be a behavioral
break:

- A function's signature, symbol, and layout are all untouched, but its
  return value for a given input changed (a rounding-mode fix, an off-by-one
  correction, a changed default).
- A function that used to be a pure computation now also logs, allocates, or
  mutates shared state — the same call site, a different program.
- An error path that used to return an error code now throws, or vice versa,
  with no change to the declared exception specification.
- A function documented as idempotent stops being idempotent under
  concurrent calls.
- Latency, memory, or I/O characteristics change enough to violate an
  implicit performance contract, even though the functional result is
  identical.

None of these move a symbol, change a type's layout, or alter a declaration
signature — the exact three things source/binary comparison is built to
observe.

## Why this is structurally outside what a static comparer can decide

[Evidence & Detectability](evidence-and-detectability.md) explains the L0–L5
evidence model: every layer, up through source replay (L4) and the
reachability graph (L5), answers *what changed structurally* — a symbol, a
type, a macro value, a call edge. None of them execute the code. Proving two
function bodies compute the same result for all inputs is, in the general
case, undecidable (it reduces to program equivalence) — no static analysis
tool, abicheck included, can close this gap by adding another evidence
layer. This is a difference in *kind*, not degree, from every other
dimension on this page's companion table: source/binary/build-profile
compatibility are static properties of declarations and artifacts;
behavioral compatibility is a property of *execution*.

A source-level diff can sometimes give a hint — a changed literal inside a
function body, a changed conditional, a rewritten loop — and abicheck's L4
source replay does surface some of these as informational findings when
sources are supplied. But a hint is not a proof: the same textual change can
be behavior-preserving (a refactor) or behavior-changing (a real fix or
regression), and telling those apart requires understanding what the code
*means*, not just what it *says*.

## What actually verifies behavioral compatibility

Because this dimension needs execution, not structure, the tools that can
speak to it are execution-based:

- **Regression test suites** run against the new version, ideally the exact
  test suite the old version shipped with (or a compatibility-focused subset
  of it) — this is the most direct evidence available.
- **Golden-output / snapshot testing** — capture representative
  inputs/outputs from the old version and replay them against the new one.
- **Differential testing / fuzzing** — run both versions against the same
  generated inputs and diff the results, useful when a hand-written test
  suite doesn't cover the input space well.
- **Property-based testing** — state invariants the operation must preserve
  (idempotence, commutativity, a monotonicity property) and check them
  across versions.
- **Documented behavioral contracts** (pre/postconditions, complexity
  guarantees, thread-safety guarantees) reviewed by a human against the
  changelog — for the cases too subtle for any of the above to catch
  reliably.

None of this is abicheck's job, and no ABI/API static checker's job — it is
the complement, not a competitor. A clean binary-compatibility verdict is a
statement about the *shape* of the contract; it says nothing about whether
the *behavior* behind that shape changed on purpose or by accident.

## How to reason about it in a release

When evaluating whether a release preserves behavioral compatibility, ask:

1. **Did the changelog claim a behavior change, and does that change keep the
   documented contract?** Merely *communicating* an incompatible behavior
   change does not make it SemVer-compatible — if the old behavior was part
   of the promised public contract, an incompatible change to it still needs
   a major bump, documented or not; only a fix that *restores* documented
   behavior (or a change that was never part of the promised contract to
   begin with) can go out as a patch or minor release. See
   [Product Contract §4](abi-series/00-product-contract.md#4-semantic-versioning-turning-the-promise-into-a-number)
   for the full versioning rule.
2. **Did the regression suite catch it?** If the old test suite passes
   unchanged against the new build, that is real (if incomplete) evidence —
   treat a test-suite regression failure with the same seriousness as a
   binary-compatibility break, since it's evidence of exactly the dimension
   static analysis cannot see.
3. **Is the change reachable from documented behavior at all?** An internal
   algorithm swap that produces bit-identical output for every documented
   input isn't a behavioral break even though the implementation changed
   completely — the contract was on the *outputs*, not the *mechanism*.

See also: [Data & Wire Compatibility](data-wire-compatibility.md) for the
adjacent question of whether *serialized* values stay meaningful across a
release, and
[Evidence & Detectability](evidence-and-detectability.md) for the full
model of what static comparison can and cannot prove.

---

**Ladder:** ← [Environment & Toolchain Drift](environment-drift.md) · Tier 8 · Beyond static ABI · [Data, Wire & Storage Compatibility](data-wire-compatibility.md) →
