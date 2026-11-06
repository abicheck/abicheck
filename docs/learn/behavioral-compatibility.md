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

A static comparison cannot decide this dimension;
[§5 of Evidence & Detectability](evidence-and-detectability.md#5-what-abi-tools-cannot-prove)
says why. This page is the fuller treatment the dimension
[Compatibility as a Product Contract §2](abi-series/00-product-contract.md#2-compatibility-is-not-one-question-name-which-kind-you-mean)
names: what behavioral compatibility means, and what verifies it.


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

A behavioral change leaves every declaration, symbol and layout byte-identical;
there is nothing in either artifact for a comparison to read. The general
argument is [§5 of Evidence & Detectability](evidence-and-detectability.md#5-what-abi-tools-cannot-prove);
what follows is specific to behaviour.

## How to defend against behavioral breaks

Testing is not the only defence, but it is one of only two kinds of
evidence that can *prove* a behavior is unchanged: running the code, and a
formal proof. Every other technique either stops a behavioral break from
being possible or narrows down where to look for one. A sound strategy
combines them, roughly from cheapest to most expensive:

| Approach | What it gives you | Proves compatibility? |
|---|---|---|
| 1. Put the contract in the type system | Turns a future behavioral break into a declaration change | Prevents a whole class |
| 2. Differential execution (old vs new) | A concrete failing input | Yes, for the inputs exercised |
| 3. Runtime checking tools | Catches the consequences (memory errors, crashes) | Yes, for the paths exercised |
| 4. Static heuristics on function bodies | "This behavior probably changed, look here" | No, signals only |
| 5. Formal methods | Equivalence or a counterexample | Yes, within the model |
| 6. Review against the documented contract | Human or AI judgement | No |

### 1. Put the contract where a compiler can see it

Most behavioral breaks happen because the contract lives only in a comment.
Move it into the declaration, and a later change to it becomes a source or
binary change that compilers and static checkers see:

- **Units and scales** go in the type: `std::chrono::seconds` rather than
  `long`, a `Meters` strong typedef rather than `double`.
- **Ownership** goes in the type: `std::unique_ptr<T>` for a caller-owned
  object, `std::shared_ptr<T>` or a reference for a shared one, never a bare
  `T*` whose owner is documented in prose.
- **Error reporting** goes in the signature: `noexcept` on a function that
  must not throw, or `std::expected<T, E>` / `std::optional<T>` instead of a
  sentinel value such as `-1`.
- **Preconditions** go in the declaration: `restrict` on the declaration, not
  only on the definition; `[[nodiscard]]`; language contracts
  (`pre`/`post` in C++26) where the toolchain supports them.

When the contract *must* change, add a new entry point (`timeout_ms()`,
`parse_or_throw()`) and deprecate the old one, so existing consumers keep
the behavior they were written against.

### 2. Run the old and the new version on the same inputs

This is the most direct evidence there is, and it goes beyond "the new
version passes its own tests". The new version's tests were updated
together with the behavior, so they confirm the new behavior, not
compatibility with the old one. Compare the versions instead:

- **The old test suite, unchanged, against the new version.** A test that
  had to be edited to pass is a behavior change that needs a decision.
- **Consumer test suites.** Rebuild and run the tests of real dependents
  against the new release, the way language ecosystems test a compiler
  release against every published package and Linux distributions rebuild
  reverse dependencies. These catch the contracts consumers actually rely
  on, including undocumented ones.
- **Golden-output / snapshot testing.** Record representative outputs of
  the old version and replay the same inputs against the new one.
- **Differential fuzzing.** Feed both versions the same generated inputs and
  diff the outputs, for input spaces too large to cover by hand.
- **Property-based testing across versions.** State an invariant such as
  `old(x) == new(x)` for every documented input, or properties such as
  idempotence or monotonicity, and let the framework search for a
  counterexample.

As one shell line, the cheapest version is the consumer's own suite,
rebuilt and run against the new release:

```bash
cmake --build consumer-build && ctest --test-dir consumer-build --output-on-failure
```

### 3. Let runtime tools catch the consequences

Some behavioral breaks show up as memory or concurrency errors before
anyone notices a wrong value. Run the tests from §2 under AddressSanitizer,
UndefinedBehaviorSanitizer, ThreadSanitizer or Valgrind: an ownership change
becomes a reported double free or use-after-free on the first test that
deletes the object. After the release, canary or staged rollouts with error
and crash monitoring are the last line of defence.

### 4. Look for suspicious changes in function bodies

A static checker cannot decide whether a body change breaks behavior: a bug
fix and a contract break look identical. It can still point at changes worth
a human look in an exported function's implementation:

- a new `throw` or a newly reachable call that may throw;
- a changed literal or constant on a return path;
- a returned pointer that used to come from `new` and now points into
  static or cached storage;
- a newly added side effect: I/O, logging, a global or a lock.

Treat these as review prompts, not findings. Their value is in shrinking
the diff a reviewer has to read, not in a verdict.

### 5. Prove equivalence where it matters

For small, critical functions such as parsers, numeric kernels and protocol
state machines, formal methods can settle the question. **Regression
verification** (equivalence checking, symbolic execution) either proves
that two versions return the same outputs for all inputs or produces a
concrete counterexample. **Formal specifications** (pre/postconditions
checked by a verifier) make the contract machine-checkable in the first
place. These are expensive to set up and rarely cover a whole library, but
the answer they give is exact.

### 6. Review the change against the documented contract

Finally, read the body diff of every exported function next to its
documentation: "documented as returning `-1` on error, now throws" is
obvious to a reviewer who has both side by side. Human review, assisted by
AI review if you use one, is not deterministic, but it is cheap and catches
contracts no test was written for. Pair it with the changelog: a behavior
change that is part of the promised contract needs a major version, whether
or not it was announced.

### Where a static ABI/API checker fits

A static checker answers the *shape* question and does it reliably; it
cannot answer the behavior question at any evidence depth. A clean result
from it is a statement about the declared interface, not about the behavior
behind that interface. Approaches 1 to 6 are its complement: §1 makes more
breaks visible to it, and §2 to §6 cover what it cannot see.

## Behavioral breaks: a separate kind of incompatibility

A release can be incompatible for three different reasons, and each one needs
different evidence:

| Kind of break | What breaks | What shows it |
|---|---|---|
| **Source (API) break** | Old source no longer compiles against the new headers | Public headers (L2) |
| **Binary (ABI) break** | An old binary no longer links, loads, or agrees on layout | Binary, debug info (L0/L1) |
| **Behavioral break** | Old code compiles, links and runs, but gets a different result | Only running a consumer (tests) |

A behavioral break is a real compatibility break: an unchanged consumer that
was correct against the old release is wrong against the new one. It is not
an API or ABI break, because nothing that consumer compiles or links against
changed. The contract it violates (an error convention, a unit, an
ownership rule, an aliasing rule) lives in documentation.

**More evidence does not help.** abicheck's deterministic checks compare what
the artifacts *declare*: symbols, layout, header declarations, build flags,
source-graph dependencies. A behavioral break changes none of these. Giving
abicheck the build data (`compile_commands.json`) and the full source trees
(`--depth source`) still yields `NO_CHANGE`, because the change sits inside an
out-of-line function body. No static rule can tell a bug fix from a contract
break there, so abicheck reports nothing rather than guessing.

### Worked examples in the catalog

Each of these catalog cases is a `NO_CHANGE` result at every evidence depth,
paired with a runtime witness: an unchanged old consumer (`app.cpp`/`app.c`)
that passes against v1 and fails against v2. The catalog records each one as a
behavioral break next to the `NO_CHANGE` verdict, so the clean verdict is
never read as "safe to ship".

| Case | What changed behind an identical declaration | Old consumer against v2 |
|---|---|---|
| [case230](../reference/examples/case230_error_reporting_return_code_to_exception.md) | Invalid input throws `std::invalid_argument` instead of returning the documented `-1` | Uncaught exception, `std::terminate` |
| [case231](../reference/examples/case231_return_value_units_changed.md) | `session_timeout()` returns milliseconds instead of seconds | 30 s timeout becomes ~8 h |
| [case232](../reference/examples/case232_factory_ownership_convention_changed.md) | `make_widget()` returns a library-owned cached object instead of a new caller-owned one | Shared state; `delete` frees library memory |
| [case208](../reference/examples/case208_restrict_added_to_definition_only.md) | The definition (not the declaration) gains `restrict` | Overlapping call computes a wrong prefix sum at `-O3` |

All four are grouped on the subject page
[Behavioral breaks outside static evidence](../reference/examples/by-subject/behavioral-breaks-outside-static-evidence.md).

Each case's "Safe redesign" shows the same fix: **move the contract into the
type system**, where the compiler and abicheck can see it. `std::chrono::seconds`
instead of `long`, `std::unique_ptr<Widget>` instead of `Widget*`, `noexcept`
on a function that must not throw, `restrict` on the declaration rather than
the definition. A later change to any of those is a declaration change, and
abicheck reports it. The full set of defences, including what to do when the contract
cannot live in a type, is in
[How to defend against behavioral breaks](#how-to-defend-against-behavioral-breaks).

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

**Ladder:** ← [Packages and Consumers](packages-and-consumers.md) · Step 9 · Beyond Static ABI · [Data, Wire & Storage Compatibility](data-wire-compatibility.md) →
