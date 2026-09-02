---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - compatibility-direction
depends_on:
  - abicheck/checker.py
  - abicheck/cli_options.py
  - abicheck/cli_compare_helpers.py
  - abicheck/appcompat.py
lifecycle: active
generated: false
---

# Compatibility Direction

Most of this documentation set — and most of what `abicheck compare` checks
by default — assumes one specific scenario: an **old, already-compiled
consumer** running against a **new version of the library**. That is the
right default (it's the scenario every ordinary in-place upgrade creates),
but it is one of several distinct directions a compatibility question can
run in, and conflating them is a common source of a check passing when the
real deployment scenario it needed to cover was a different one.

## Naming the directions

| Direction | The scenario | Typical trigger |
|-----------|--------------|------------------|
| **Backward (the default)** | Old consumer, new library | An in-place library upgrade; the consumer wasn't rebuilt |
| **Forward** | New consumer, old library | A downgrade/rollback; or a consumer built against a newer SDK than what's deployed |
| **Host-forward** | Old plugin, new host | A plugin ecosystem where hosts update faster than every plugin |
| **Host-backward** | New plugin, old host | A plugin built against a newer SDK loaded into an older, still-deployed host |
| **Header/binary skew** | Old headers, new binary (or the reverse) | A consumer's build system pins headers independently of the binary it links against |
| **Coexistence** | Two versions loaded in one process, or in communicating processes | A plugin ecosystem, a monorepo with mixed build times, a rolling deployment |

Each row is checking the same underlying contract from a different vantage
point, and — this is the part worth internalizing — **a change can be safe
in one direction and unsafe in another.** A newly *added* optional
parameter with a default is safe, for an ordinary call expression, in old
source recompiled against new headers — though not universally: it changes
the function's *type*, so source taking its address for a function pointer
(`void (*p)(int) = &f;` against `f(int, int = 0)`) stops compiling even in
that same direction. Note that this source-level safety is **not** the same
as backward compatibility in this table's sense (old *binary*, new library):
if the one-parameter overload is replaced rather than kept alongside the new
one, the new library no longer exports the mangled symbol an
already-compiled consumer references, and that is a hard backward binary
break — the recompile-safety and the binary contract are separate answers. In the
*forward* direction, the break isn't limited to source that names the new
argument — it's a *link*-time failure, not just a compile-time one:
`f(int, int = 0)` is a different function type from `f(int)`, so it
normally mangles to a different symbol. New source compiled against the
new declaration — even an ordinary, old-form call like `f(1)`, which the
compiler silently expands with the default argument — compiles cleanly,
but linking that new consumer against an *old* library that only exports
the one-parameter symbol fails outright, because the two-parameter symbol
the new consumer needs was never exported. Code that explicitly supplies
the new argument or takes the function's address fails earlier still, at
*compile* time, against genuinely *old headers* that never declared the
parameter at all (a distinct scenario from linking against an old
*library* with new *headers*, above). A newly
*added* exported symbol is invisible to an old, already-linked consumer
(the backward direction is unaffected — nothing calls a symbol that didn't
exist when it was built) but breaks the forward direction the moment a
*new* consumer, compiled against headers that declare it, gets linked
against an *older* library build that never exported it — exactly the
scenario a downgrade or a rollback creates.

## Why the default direction is the right default, and when it isn't enough

Backward compatibility (old consumer, new library) is the right default
because it's what an ordinary "ship a point release, users update the
library" workflow needs, and it's the direction every ABI/API stability
convention (SONAME bumps, symbol versioning) is built around — see
[Part 5 — Linker & ELF](abi-series/05-linker-elf.md). `abicheck compare
old.so new.so` checks exactly this direction: can the *old* binary's
callers (whose expectations are frozen in the old snapshot) still be
satisfied by the *new* library.

It stops being enough the moment your deployment model allows any of these:

- **Downgrades or rollbacks are a supported operation** — an incident
  response that reverts a bad deploy needs the *forward* direction to also
  hold, or the rollback itself becomes a second incident.
- **You ship a plugin ecosystem** — third-party plugins are compiled at
  different times against different SDK versions, and both host-forward and
  host-backward compatibility matter depending on whether hosts or plugins
  update first in practice. See [Plugin Systems](../use/plugin-systems.md)
  for the two consumer-scoped checks that together cover one specific
  plugin/host pair: `compare --used-by` scopes the comparison to a
  consumer's actual, statically-linked imports, while `compare
  --required-symbol` checks the host's own required entrypoints — the ones
  it resolves dynamically via `dlopen`/`dlsym` rather than importing at link
  time, which `--used-by` alone cannot see. A full plugin/host pair check
  needs both; either one run alone only covers its own half of the
  contract, in either direction.
- **Header and binary versions can diverge** — a build system that
  vendors/pins headers separately from the binary it links (common in
  large, multi-team builds) needs both header→binary and binary→header
  checks, since a stale header can silently miscompile against a
  newer binary and vice versa.
- **Multiple versions coexist in one process or communicating processes** —
  a rolling deployment where old and new service instances talk to each
  other over a shared library's wire format needs *simultaneous* backward
  and forward compatibility for the whole rollout window, not just a single
  before/after comparison. (Where the shared contract is data crossing a
  boundary rather than binary ABI, see
  [Data & Wire Compatibility](data-wire-compatibility.md) — coexistence
  often turns an ABI question into a data-format one.)

## Checking more than one direction

`abicheck compare` takes two snapshots positionally — which one is "old"
and which is "new" is a labeling choice, not a constraint the tool imposes.
Checking the forward direction for a given pair is the same command with
the arguments reversed: `abicheck compare new.so old.so` asks "can callers
compiled against the *new* contract be satisfied by the *old* library" —
useful for validating a rollback path is safe *before* you need it, not
after. For a `--used-by`/plugin scenario, reversing the two library
arguments is *not* by itself enough to check the opposite direction:
`--used-by` scopes the comparison to the *supplied consumer's own actual
imports*, so a forward consumer-scoped check needs a consumer binary that
was actually built against the *new* contract — reusing an old consumer
binary alongside reversed library arguments still only exercises what that
old consumer imports, never a symbol or version a new-SDK consumer would
use. The same applies to `--required-symbol`: the required-entrypoint set
must match the host contract for the direction under test. See
[Plugin Systems](../use/plugin-systems.md) for the consumer-scoped
mechanics `compare --used-by`/`--required-symbol` build on.

There is no single "check every direction" flag, because which directions
matter is a property of your deployment model, not something abicheck can
infer from two binaries — this is exactly the kind of contract-shape
decision [Product Contract §5](abi-series/00-product-contract.md#5-name-your-contract-shape)
asks you to make explicit before reasoning about breaks at all. Naming which
directions your project actually needs (usually: backward always, forward
if rollback is supported, both host directions if you ship plugins) is the
first step; running `compare` once per direction that matters is the
mechanical follow-through.

See also: [Product Contract §5](abi-series/00-product-contract.md#5-name-your-contract-shape) for
naming your contract shape, and [Plugin Systems](../use/plugin-systems.md)
for the consumer-scoped mechanics that make a specific direction checkable
against a specific real consumer.

---

**Ladder:** ← [Part 6 — Subtle & Transitive Breaks](abi-series/06-transitive-breaks.md) · Tier 3 · Define the contract · [Consumer Models](consumer-models.md) →
