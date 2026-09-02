---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - static-and-header-only
depends_on:
  - abicheck/dumper_clang.py
  - abicheck/cli.py
  - abicheck/cli_buildsource.py
lifecycle: active
generated: false
---

# Static & Header-Only Contracts

Everything else in this documentation set defaults to one library shape: a
dynamically-loaded shared library (`.so`/`.dll`/`.dylib`) that a consumer
links against and loads separately from its own binary. Two other common
shapes — a static library and a header-only library — don't have a dynamic
ABI boundary in the same sense, but they are not exempt from compatibility
concerns; the concerns just move.

## Static libraries: no dynamic ABI, but not "no compatibility contract"

A static library (`.a`/`.lib`) is linked *into* the consumer's own binary at
build time — there is no runtime symbol resolution, no SONAME, no loader
involved, and the classic "upgrade the library, keep the old consumer
binary" scenario [Part 1](abi-series/01-foundations.md) builds the whole
series around simply doesn't apply: **every consumer must be relinked
against a new static library, and most practically must be recompiled
against it too.**

That does not mean nothing can go wrong. What remains:

- **Source compatibility** — a consumer's `#include`s and call sites still
  need to compile against the new headers. Every mechanism in
  [Part 2 — Symbol Contracts](abi-series/02-symbol-contracts.md) and
  [Part 6's source-only section](abi-series/06-transitive-breaks.md#source-only-api-breaks-binary-identical)
  still applies at the source level, unchanged by the fact that the eventual
  binary is statically linked.
- **Object-file/archive compatibility** — a consumer that links a
  *precompiled* static archive (rather than building it from source as part
  of the same build) needs that archive's object format, symbol names,
  and compiler-ABI-relevant conventions to match its own build. This is
  effectively a compile-time version of the ordinary binary-compatibility
  question, checked once at link time instead of continuously at load time.
- **LTO and bitcode compatibility** — a static library distributed as LTO
  bitcode (rather than native object code) additionally needs the
  consumer's toolchain version/configuration to be compatible with the
  bitcode format itself — a much narrower, toolchain-version-pinned
  contract than ordinary object-code linking.
- **Compiler and runtime compatibility** — see
  [Dependency & Runtime Floors](dependency-floors.md) and the
  build-profile-comparability row in
  [Product Contract §2](abi-series/00-product-contract.md#2-compatibility-is-not-one-question-name-which-kind-you-mean)
  (the exit-code/`--diagnostic-comparison` mechanics on the same theme
  apply here too) for why "the same compiler family/version, standard
  library, and ABI flags on both sides" still matters even with no dynamic
  loader involved.
- **Inline/template body behavior** — see the header-only section below;
  a static library's *headers* carry the same inline/template-body concerns
  a header-only library's full API does, just for whatever subset of the
  API is exposed inline rather than compiled into the archive.

`abicheck` itself is built around comparing dynamic-loading artifacts
(`.so`/`.dll`/`.dylib`) directly; a static library's `.a`/`.lib` archive
isn't a first-class input the way a shared object is, and `compare`
positionally requires two such artifacts (or JSON snapshots) — there's no
`-H`-alone, no-operand invocation, and (see the header-only section below
for why) no fully-supported way to compare two no-binary source snapshots
against each other either. Unlike a header-only library, though, a static
library's implementation is real, compilable `.cpp` code — so the practical
way to validate it today is to build an ordinary shared object from those
same sources (a purpose-built `.so` consumers never ship, existing purely
to give the archive's own API a real binary to attach evidence to) and run
the standard binary+headers comparison against *that binary*, the same way
you would for a genuine dynamic library. Compare that built `.so` directly
— never the static archive itself, and never two no-binary source-only
snapshots against each other (see the header-only section below for why
that path isn't fully supported).

**This is only a limited proxy, not equivalent to checking the archive
itself, and the two builds' export visibility has to genuinely match for
the comparison to mean anything.** A static archive's own linkable symbols
follow the archive's own visibility rules at compile time (which, for
common patterns like `-fvisibility=hidden` with no explicit DLL/export
macro, may not match what a shared object built from identical sources
ends up exporting in its *dynamic* symbol table). Verified directly: a
minimal library built both ways, with `foo(int)` changed to `foo(long)` in
the header, produced a real linkable symbol in `libfoo.a` but an *empty*
export table in the purpose-built `.so` under `-fvisibility=hidden` with no
export macro — `compare` reported `NO_CHANGE` even with both headers
supplied, because there was nothing in either side's export table to
diff. Build the surrogate `.so` with export visibility that matches how the
archive's symbols are actually meant to be consumed (an explicit export
macro, or `-fvisibility=default` for a library that doesn't use one), and
keep a static consumer link/source test as a second, independent check
for the archive itself — don't treat the `.so` surrogate's clean result
alone as proof the archive is unchanged. Validating the *compiled*
archive's object-format/compiler-ABI compatibility from its `.a`/`.lib`
file directly isn't something abicheck does today.

## Header-only libraries: the whole surface is the inline-body concern

A header-only library has no separate compiled artifact at all — a
declaration a consumer *actually uses* is compiled *into their own binary*
at their own build time, not shipped to them from a prebuilt library. That
removes source/binary compatibility as separate questions only for the
library's own relationship to *a* consumer — there's no separately-shipped
provider `.so` for a consumer's binary to diverge from. It does **not**
mean a header-only type or function can't produce a real ABI break: when a
header-only declaration is used across a boundary between two
*independently compiled* components — a host and a plugin, two libraries
in the same process, anything not rebuilt together from the same header at
the same time — its layout, calling convention, and inline definitions are
baked into each component's own binary, and a change to any of that is
exactly the ordinary cross-boundary ABI question this whole site is about,
just with the header-only library itself never being one of the compiled
artifacts under comparison. The single-consumer,
everything-rebuilt-together case is what makes the concern
[Part 4](abi-series/04-cpp-abi.md) and
[Unified Impact Assessment](impact-analysis.md)
describe for a single *public inline function* apply to the **entire
public API a consumer actually reaches**, not unconditionally to every
declaration the header happens to contain — an unused `inline` function
ordinarily emits no code at all, and a class/function template is compiled
only for the argument combinations a consumer actually instantiates
(`std::vector<int>` vs. `std::vector<Widget>` are two independent
instantiations, not "the template"). What follows applies to whatever
subset of the header a given consumer's own code actually reaches:

- Every function body a consumer reaches is compiled fresh into their own
  binary at their build time, against their own compiler, flags, and
  standard-library implementation — not something the header-only library
  ships them.
- A change to a function body's *behavior* reaches a consumer only the next
  time *that consumer* rebuilds — an existing, already-compiled consumer
  binary keeps running the old inlined behavior indefinitely, with no
  equivalent of a dynamic library's "drop in a new `.so`, every consumer
  picks up the change immediately" propagation. A header-only library's
  versioning is effectively always at the granularity of "whatever headers
  were included at each consumer's most recent build" — which cuts both
  ways: a fix doesn't reach anyone until they rebuild, and a *break* is
  silently absent from every consumer that hasn't rebuilt either, right up
  until the moment they do.
- Because there's no separate binary artifact, checking a header-only
  library needs source-level evidence rather than binary evidence. **This is
  a genuine gap in today's tooling, not a solved problem with an
  unintuitive flag combination**: `dump`'s no-binary path
  (`dump --sources`/`--build-info` with no artifact) produces an L3/L4/L5
  source-fact snapshot that is **diagnostic output with no supported
  consumer today** — no user-facing command folds it onto a binary-side
  snapshot (the `merge` command that once did was removed in ADR-043, and
  nothing replaced it), and comparing two such snapshots against each other
  isn't a supported path either. The closest
  practical workaround today is compiling a small stub translation unit
  that `#include`s and instantiates the public API into an actual `.so`,
  and dumping/comparing *that* — an ordinary binary+headers comparison, on
  an artifact that exists only to give the header-only API something to
  attach evidence to — rather than relying on any no-binary snapshot
  comparison this tool doesn't yet fully support. The stub carries the same
  export-visibility caveat as the static-library surrogate above, and one
  extra: it must both *force emission of* and *default-export* every
  representative entry point (explicit instantiation plus an export
  annotation; note `-fvisibility-inlines-hidden`, common in real builds,
  hides instantiated inline/template functions by default). Once a binary
  carries any ELF exports at all, the header-declared surface is narrowed
  to that export set — so a stub whose instantiations stay hidden yields a
  clean comparison of almost nothing. Keep source/consumer compile tests as
  an independent second check either way. See
  [Producing Source Facts](../use/producing-source-facts.md) and
  [Dump/Compare Flags](../use/dump-compare-flags.md) for how the supported
  binary+headers path works; every mechanism in
  [Evidence & Detectability](evidence-and-detectability.md) applies once
  you have a real snapshot to apply it to.
- ODR (One Definition Rule) violations are a risk here too, as they are for
  any inline/template declaration in a *dynamic* library's public headers
  (the same declarations that make a dynamic library's own public inline
  functions "public inline" in
  [the dispatcher scenario on Unified Impact Assessment](impact-analysis.md))
  — this isn't unique to the header-only shape, just proportionally larger:
  a header-only library's *entire* public surface is inline/template
  declarations, so the exposure to "two translation units silently seeing
  different versions of the same header" (a partial update, a vendored copy
  alongside a system-installed one) is much wider than for a dynamic
  library, where only the inline/template subset of the public headers
  carries this risk.

## Practical guidance

- For a static library, treat header/source compatibility with the same
  rigor as a dynamic library's — build the purpose-built shared object
  described above and run the standard binary+headers comparison against
  *that artifact*, not against the `.a`/`.lib` archive or a pair of
  source-only snapshots — and additionally pin/document the supported
  compiler and standard-library ABI range explicitly, since there's no
  runtime check equivalent to a loader refusing an incompatible SONAME.
- For a header-only library, be explicit that every function a consumer
  *actually uses or instantiates* is effectively "public inline" in the sense
  [the hub's dispatcher
  scenario](impact-analysis.md)
  describes — a behavior change in such a function reaches that consumer on
  their next rebuild, with no version boundary to soften it. (An unused
  inline function or an uninstantiated template emits nothing into a consumer
  that never reaches it, so changing it doesn't affect them — but you can't
  know which subset any given consumer reaches, so design as if every
  declaration is reached by someone.) Document behavior
  changes as prominently as you would a binary-breaking ABI change in a
  dynamic library, because from the consumer's perspective, that's what
  they are.
- If your project ships both a compiled fallback and a header-only fast
  path (a common pattern for numerics/SIMD dispatch libraries), the two
  paths need *independent* compatibility review — a change safe for the
  compiled path (hidden behind a stable ABI boundary) can still be a
  behavior change for any consumer using the header-only path.

See also: [Dependency & Runtime Floors](dependency-floors.md) for the
toolchain/runtime side of static linking, and
[Behavioral & Semantic Compatibility](behavioral-compatibility.md) for why
an inline body's behavior — not just its signature — is the real contract
in the header-only case.

---

**Ladder:** ← [Build Profile Comparability](build-profile-comparability.md) · Tier 3 · Define the contract · [Detecting Breaks: Evidence, Tools, and Why One Method Is Never Enough](abi-series/08-detection.md) →
