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

As one shell line, the check is the consumer's own suite, rebuilt and run
against the new version — there is no catalog case for what a static
comparison cannot see:

```bash
cmake --build consumer-build && ctest --test-dir consumer-build --output-on-failure
```

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
abicheck reports it.

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
