---
doc_type: explanation
audience:
  - library-maintainer
level: advanced
canonical_for:
  - concurrency-and-initialization
depends_on:
  - abicheck/checker.py
lifecycle: active
generated: false
---

# Concurrency & Initialization Contracts

A static comparison cannot decide this dimension;
[§5 of Evidence & Detectability](evidence-and-detectability.md#5-what-abi-tools-cannot-prove)
says why. A library's threading model and initialization/destruction order are
part of its contract with every consumer, exactly as much as its symbols
and types are. This page names the specific sub-questions so they are not
silently dropped from a release review just because no tool flags them.


## The sub-questions

- **Thread-safety guarantees.** Is a given function safe to call
  concurrently from multiple threads on the *same* object? On *different*
  objects? Is it safe to call while another thread is inside a different
  function on the same object? A library that used to require external
  synchronization and starts doing its own internal locking (or the
  reverse) has changed a contract with zero signature-level signal —
  and the reverse direction (removing internal locking a consumer was
  silently relying on) is a classic, hard-to-diagnose regression.
- **Atomics and memory ordering.** A documented lock-free operation's
  memory-ordering guarantee *weakening* (e.g. sequentially-consistent to
  acquire/release, or acquire/release to relaxed) breaks a consumer relying
  on the stronger visibility the old guarantee promised, with no change to
  any type or signature. *Strengthening* it (relaxed to acquire/release, or
  acquire/release to sequentially-consistent) is compatible — code correct
  under the weaker guarantee stays correct under the stronger one — though
  it can still be worth documenting as a performance-relevant change.
- **TLS (thread-local storage) semantics.** Whether a piece of state is
  per-thread or process-global is a contract question independent of
  whatever the C++ `thread_local` keyword or ELF TLS model
  ([Part 5](abi-series/05-linker-elf.md) covers the *ABI* mechanics of TLS
  models specifically) says about how it's implemented — a consumer relying
  on "this counter is shared across all my threads" breaks silently if it
  becomes per-thread, and vice versa.
- **Global initialization/destruction order.** C++ static/global objects
  across translation units have no guaranteed initialization order between
  TUs by the language standard; a library that used to tolerate being used
  during "static init" from a consumer's own global constructors (or during
  process shutdown, from a consumer's global destructors) and stops
  tolerating it — or the reverse — is a contract change with no ABI
  signature. This is a genuinely common source of "worked fine for years,
  broke on the next compiler/linker upgrade" bugs, because *toolchain*
  changes (not library changes) can also flip which order actually occurs
  in practice, even when neither side's code changed.
- **Callback threading model.** For a plugin/callback API (see
  [Plugin Systems](../use/plugin-systems.md)), which thread a callback is
  invoked from — the caller's thread, a dedicated library thread, an
  unspecified worker — is a contract as load-bearing as the callback's
  function-pointer type, and just as invisible in that type.
- **Reentrancy.** Whether it's safe to call back into the library from
  within a callback the library itself invoked (or from a signal handler,
  or from an async-cancellation point) is a contract with real consequences
  and, again, nothing in a signature that states it either way.
- **Fork safety.** For POSIX libraries specifically: whether internal state
  (locks, file descriptors, background threads) survives a `fork()` cleanly
  is a contract that has broken more C libraries than almost any other
  single mechanism on this page, entirely invisibly to a symbol/type diff.
- **Async cancellation.** Whether an in-flight operation can be safely
  cancelled, and what state it leaves things in if so, is a contract
  question orthogonal to whatever cancellation API surface (a function, a
  flag, a token type) exists to request it.

## What actually verifies these contracts

The same category of tooling
[Behavioral & Semantic Compatibility](behavioral-compatibility.md)
recommends generally, aimed specifically at concurrency:

- **Thread sanitizers** (TSan) run across the existing test suite catch a
  real fraction of newly-introduced data races, though they prove nothing
  about paths the test suite doesn't exercise concurrently.
- **Stress/soak tests** under realistic concurrent load, run for both the
  old and new version, comparing for new crashes, hangs, or corruption.
- **Explicit fork-safety tests** (fork, then exercise the library in the
  child) for any POSIX library that documents fork safety at all.
- **Static/global init-order stress**: build a minimal consumer that
  exercises the library from its own global constructors/destructors, and
  keep that test in the suite across compiler upgrades, not just library
  upgrades — since, as noted above, a toolchain change alone can flip which
  init order actually occurs.
- **Documented, versioned threading-model statements** reviewed as part of
  every release, the same discipline
  [Ownership & Lifetime Contracts](ownership-and-lifetime.md) recommends for
  ownership — state the guarantee next to the declaration it applies to,
  and treat a change to it as release-note-worthy even when nothing else
  about the release touches ABI or API at all.

As one shell line, the check is the consumer run under ThreadSanitizer with
a workload that actually contends:

```bash
TSAN_OPTIONS=halt_on_error=1 LD_LIBRARY_PATH=new/lib ./consumer-tsan --threads 16 --stress
```

See also: [Behavioral & Semantic Compatibility](behavioral-compatibility.md)
for the broader category and why static analysis structurally cannot decide
it, and [Ownership & Lifetime Contracts](ownership-and-lifetime.md) for the
adjacent, similarly signature-invisible contract about who owns what and for
how long.

---

**Ladder:** ← [Ownership & Lifetime Contracts](ownership-and-lifetime.md) · Step 9 · Beyond Static ABI · [Series overview](abi-api-handling.md) →
