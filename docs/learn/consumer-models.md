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
- **Irrelevant** for a static-linked consumer, who relinks and recompiles
  by construction — see [Static & Header-Only Contracts](static-and-header-only.md).

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
| **FFI / language binding** | A hand-written or generated declaration of your C ABI in another language's own type system (ctypes, cffi, JNI, P/Invoke, …) — frozen at generation time, not re-derived from your headers automatically | A layout or signature change the binding's own frozen declarations don't reflect — the binding has no compiler to catch the drift for it | [Part 4 — C++ ABI](abi-series/04-cpp-abi.md) (for the C++-to-C-ABI boundary), [Behavioral & Semantic Compatibility](behavioral-compatibility.md) |
| **Header-only consumer** | Nothing pre-baked at all — every consumer recompiles your *implementation*, not just your interface, on every build | A source-incompatible header change, *or* a behavioral/semantic change invisible to the compiler entirely | [Static & Header-Only Contracts](static-and-header-only.md) |
| **Static-linked consumer** | Your object code, linked directly into its own binary at build time — no runtime symbol resolution, no SONAME | Nothing at *runtime* (there's no shared object left to swap) — the contract is entirely at build time: source + archive/object-format compatibility | [Static & Header-Only Contracts](static-and-header-only.md) |
| **Bundle / product-release component** | Not just your library's own contract, but the *intra-bundle* relationships — which other components provide symbols it needs, which SONAMEs/versions it was released alongside | A sibling component's change that breaks a relationship your library itself never touched | [Part 6 — Transitive Breaks](abi-series/06-transitive-breaks.md), [Multi-Binary Releases](../use/multi-binary.md) |

Two of these are worth naming as the genuine edge cases they are:

- **FFI bindings are structurally blind.** Every other row has *some*
  mechanism that would at least fail loudly — a missing symbol at link time,
  a compile error against changed headers. A hand-maintained language
  binding has no such backstop: its own declarations are just data, frozen
  the moment someone wrote or generated them, with nothing forcing them to
  track your library's real ABI. A layout change here doesn't fail to
  compile; it silently reads or writes the wrong bytes at runtime. This is
  the single strongest argument for keeping a binding's declarations
  machine-generated from the same headers/snapshot you already maintain,
  rather than hand-transcribed once and left to drift.
- **Header-only has no binary boundary to fall back on at all.** Every other
  row eventually reduces to "did the *interface* change" in some form abicheck
  can observe. A header-only library has no compiled artifact of its own to
  diff — see [Static & Header-Only Contracts](static-and-header-only.md) for
  why this is a genuine, currently-unclosed gap in what static tooling can
  verify, not just an unusual workflow.

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
[per-consumer application checks](../use/multi-binary.md) exist specifically
because the library's own global verdict is the *dynamically-linked
application* row's answer — informative, but not automatically the right
answer for every other row a real product ships to.

---

_See also: [Part 0 — Compatibility as a Product Contract](abi-series/00-product-contract.md) ·
[Compatibility Direction](compatibility-direction.md) ·
[Static & Header-Only Contracts](static-and-header-only.md) ·
[Plugin Systems](../use/plugin-systems.md)._
