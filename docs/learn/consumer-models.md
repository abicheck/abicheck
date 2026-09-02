---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - consumer-models
depends_on:
  - abicheck/appcompat.py
  - abicheck/cli_compare_helpers.py
lifecycle: active
generated: false
---

# Consumer Models

[Part 0 §2](abi-series/00-product-contract.md#2-compatibility-is-not-one-question-name-which-kind-you-mean)
names *which dimension* of compatibility a question is about. This page
names the other axis every real compatibility question also depends on:
**what shape is the thing consuming your library?** The same technical
change can be a non-event for one consumer shape and a hard break for
another, because different consumer shapes remember different things about
the library — and "remember" here means literally: what got baked into the
consumer's own compiled bytes, or its own generated bindings, at the point
it last saw your library.

## Why this is a separate axis from the compatibility dimension

A dimension (source/binary/behavioral/…) asks *what kind of promise* was
broken. A consumer model asks *who is holding that promise, and how*. Naming
both together is what actually answers "is this change safe to ship":

> Dimension × Consumer model × [Direction](compatibility-direction.md) →
> is *this* change safe for *this* audience?

The same struct-layout change is:

- **Silent** for a consumer that only recompiles against your headers before
  every release (a recompiled-source consumer never sees the old layout).
- **A hard corruption bug** for a consumer holding an already-compiled
  binary built against the old layout (a dynamically-linked application).
- **Irrelevant for the ordinary case** of a static-linked consumer, who
  rebuilds and relinks from source by construction — but not for one that
  keeps precompiled object files and only relinks them (see the table row
  below) — see [Static & Header-Only Contracts](static-and-header-only.md).

None of these are "the tool is inconsistent." They're correct, different
answers to a question that silently changed underneath — the consumer
model — while the struct-layout question itself stayed exactly the same.

## The eight consumer shapes

| Consumer shape | What it remembers about your library | What actually breaks it | Where this is covered in depth |
|-----------------|----------------------------------------|---------------------------|-------------------------------|
| **Dynamically-linked application** | Symbols it resolved, struct/class layouts it baked into its own code, the calling convention | Removed/moved symbols, layout shifts, ABI-affecting flag drift | [Part 1 — Foundations](abi-series/01-foundations.md), [Part 2](abi-series/02-symbol-contracts.md), [Part 3](abi-series/03-type-layout.md) |
| **Recompiled source consumer** | Only your public header API, at whatever revision it last compiled against | Source-incompatible header changes (signature changes, removed declarations, macro removal) | [Part 2](abi-series/02-symbol-contracts.md), [Part 6 — source-only breaks](abi-series/06-transitive-breaks.md#source-only-api-breaks-binary-identical) |
| **Plugin implementation** (your library *is* the plugin, loaded by someone else's host) | The host's fixed entry-point symbols, callback signatures, and any host-defined struct it fills in | A changed/removed entry point the host still expects, an incompatible callback signature | [Plugin Systems](../use/plugin-systems.md), [Part 0 §5 — Plugin/SDK with `dlopen`](abi-series/00-product-contract.md#plugin-sdk-with-dlopen) |
| **Host loading plugins** (your library loads *someone else's* code) | Its own advertised plugin ABI — the entry points and structs it promises to call correctly | Its own contract drifting out from under plugins that were built against an earlier version | [Plugin Systems](../use/plugin-systems.md) |
| **FFI / language binding** | A declaration of your C ABI in another language's own type system (ctypes, cffi, JNI, P/Invoke, …). For a hand-maintained, vendored, or one-time-generated binding: frozen at whatever point it was last written, not re-derived from your headers automatically. For a binding generator wired into the consumer's own build: whatever the *current* headers say, re-derived on every build — the same contract as a recompiled-source consumer, just expressed through generated code | For the frozen case: a layout or signature change the binding's own stale declarations don't reflect, with no compiler to catch the drift. For the build-integrated case: the same source-incompatible changes that break a recompiled-source consumer | [Part 4 — C++ ABI](abi-series/04-cpp-abi.md) (for the C++-to-C-ABI boundary), [Behavioral & Semantic Compatibility](behavioral-compatibility.md) |
| **Header-only consumer** | Every consumer recompiles your *implementation*, not just your interface, on every build — but an already-built header-only consumer still has whatever it inlined/instantiated baked into its binary at that point, the same as any other compiled artifact | A source-incompatible header change, a behavioral/semantic change invisible to the compiler entirely, *or* — for two independently-built components (a host and a plugin, say) that pulled in different header revisions — an ODR/ABI mismatch between what each baked in | [Static & Header-Only Contracts](static-and-header-only.md) |
| **Static-linked consumer** | Your object code, linked directly into its own binary at build time — no runtime symbol resolution, no SONAME | In the ordinary full rebuild-and-relink case: the same source-incompatible header changes that break a recompiled-source consumer (a removed declaration or incompatible signature simply fails to compile) — [Static & Header-Only Contracts](static-and-header-only.md) preserves source compatibility as a real part of this shape's contract, not just a formality. Separately: a consumer that keeps *precompiled* object files and only relinks them against a new archive still has the old layout baked into those objects. An ordinary linker only resolves symbol names, not layout compatibility, so the link itself still succeeds; the mismatch is the same silent runtime corruption a dynamically-linked application has, just triggered the next time the program runs after this relink rather than after a `dlopen` | [Static & Header-Only Contracts](static-and-header-only.md) |
| **Bundle / product-release component** | Not just your library's own contract, but the *intra-bundle* relationships — which other components provide symbols it needs, which SONAMEs/versions it was released alongside | A sibling component's change that breaks a relationship your library itself never touched | [Part 6 — Transitive Breaks](abi-series/06-transitive-breaks.md), [Multi-Binary Releases](../use/multi-binary.md) |

Two of the shapes have a command of their own. A **dynamically-linked
application** scopes the comparison to what that application actually
imports, so a removal it never calls is reported but does not decide the
verdict:

```bash
abicheck compare old.so new.so -H include/ --used-by ./app
```

A **host loading plugins** states the entry points it will resolve by
name, and the comparison is scoped to that explicit contract:

```bash
abicheck compare old-host.so new-host.so --required-symbol plugin_init --required-symbol plugin_shutdown
```

The mechanics of both are owned by [Plugin Systems](../use/plugin-systems.md).

Two of these are worth naming as the genuine edge cases they are:

- **A hand-maintained or frozen FFI binding has no regeneration step at
  all — not even the silent one every other shape gets.** A layout change
  silently corrupts an already-built dynamically-linked application too
  (that's the whole point of [Part 1](abi-series/01-foundations.md) — an
  ABI break is silent, not a loud link error), so "loud vs. silent
  failure" isn't what sets FFI bindings apart. What's missing is narrower
  and more specific: a recompiled-source consumer's next build re-reads
  your real headers, and a static-linked consumer's next *full rebuild*
  (recompiling its own sources against your current headers, not merely
  relinking already-compiled objects against a new archive — a bare relink
  reads only what's already baked into those objects, nothing new) picks
  up your current object code — both *automatically* pick up a change the
  next time they touch your library that way. A binding whose declarations
  were
  hand-written once, or generated once and then frozen/vendored, has no
  such step: its declarations are just data, sitting wherever they were
  last written, with nothing that forces them to be regenerated when your
  ABI changes. A binding generator wired directly into the consumer's own
  build — re-invoked against your current headers on every build, the same
  way a recompiled-source consumer's compiler is — doesn't have this
  problem; it has simply moved the recompiled-source-consumer row's
  contract one layer down into generated code, rather than escaping it.
  This is the single strongest argument for keeping any binding
  build-integrated and machine-generated from the same headers/snapshot
  you already maintain, rather than hand-transcribed or vendored once and
  left to drift.
- **Header-only has no separate compiled provider artifact to fall back
  on at all.** Every other row has *some* provider-side artifact distinct
  from the consumer — a `.so`, a static archive, headers paired with a
  binary a compiler can validate them against — that a static comparison
  can build and inspect, even where (as the behavioral/data-wire pages
  document) the *content* of that artifact can't answer every question.
  For a header-only library, the headers themselves are perfectly
  inspectable, but there is no separate compiled provider binary to derive
  a second, provider-side snapshot from — the only "build" of those
  headers that ever happens is the consumer's own, which is exactly the
  case abicheck's comparison model doesn't have a no-binary path for
  today. See [Static & Header-Only Contracts](static-and-header-only.md)
  for why this is a genuine, currently-unclosed gap in what static tooling
  can verify, not just an unusual workflow.

## Composing consumer model with the other axes

A single real-world compatibility question is the product of several
answers, not one:

> *Dimension* (binary/source/behavioral/…) × *[Direction](compatibility-direction.md)*
> (old consumer/new provider? new consumer/old provider? rolling mixed
> deployment?) × *Consumer model* (which of the eight shapes above) ×
> *[Declared surface](abi-series/00-product-contract.md#3-define-the-public-surface-before-you-check)*
> (was the changed thing actually promised?) × *[Build profile](build-profile-comparability.md)*
> (were the two things you're comparing even extracted comparably?)

Naming all five before reasoning about "is this a break" is what actually
resolves most real disagreements — two people can each be completely correct
about a change's effect while silently assuming different consumer models.
[abicheck's plugin/host-contract tooling](../use/plugin-systems.md) and
[per-consumer application checks](../use/appcompat.md) exist specifically
because a library's own global verdict answers "does this change violate the
declared ABI/API policy for the whole public surface" — a real, useful
question, but not automatically the same question any *one* real consumer
needs answered. A dynamically-linked application, for instance, is
unaffected by a policy-scored `API_BREAK` that's purely source-level (an
already-built binary never recompiles), while a plugin evaluated only
against its host's specific required entry points can pass even when the
library's own global verdict is worse. Naming the actual consumer in front
of you is what turns "the tool says X" into "X, and here's whether that
matters for *this* audience."

---

_See also: [Part 0 — Compatibility as a Product Contract](abi-series/00-product-contract.md) ·
[Compatibility Direction](compatibility-direction.md) ·
[Static & Header-Only Contracts](static-and-header-only.md) ·
[Plugin Systems](../use/plugin-systems.md)._

---

**Ladder:** ← [Compatibility Direction](compatibility-direction.md) · Tier 3 · Define the contract · [Build Profile Comparability](build-profile-comparability.md) →
