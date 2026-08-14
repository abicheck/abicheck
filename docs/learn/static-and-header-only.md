---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - static-and-header-only
depends_on:
  - abicheck/dumper_clang.py
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
the standard binary+headers comparison against *that*, the same way you
would for a genuine dynamic library. Validating the *compiled* archive's
object-format/compiler-ABI compatibility from its `.a`/`.lib` file directly
isn't something abicheck does today.

## Header-only libraries: the whole surface is the inline-body concern

A header-only library has no separate compiled artifact at all — every
declaration a consumer sees, they also compile *into their own binary*,
every time. This removes source/binary compatibility as separate
questions (there's no separately-shipped binary to diverge from the
header) but makes the concern [Part 4](abi-series/04-cpp-abi.md) and
[the hub's L5-graph section](abi-api-handling.md#the-l5-graph-reachability-not-just-structure)
describe for a single *public inline function* apply to the **entire
public API, unconditionally**:

- Every function body is compiled fresh into every consumer at their build
  time, against their own compiler, flags, and standard-library
  implementation.
- A change to a function body's *behavior* reaches every consumer the next
  time they rebuild — there is no "old binary keeps working with old
  behavior" grace period a dynamic library gives you; a header-only
  library's versioning is effectively always at the granularity of "whatever
  headers were included at each consumer's most recent build."
- Because there's no separate binary artifact, checking a header-only
  library needs source-level evidence rather than binary evidence. **This is
  a genuine gap in today's tooling, not a solved problem with an
  unintuitive flag combination**: `dump`'s no-binary path
  (`dump --sources`/`--build-info` with no artifact) produces an L3/L4/L5
  source-fact snapshot whose own documented purpose is to be *combined*
  with a binary-side dump — `dump libfoo.so -H include/ --sources . ...` —
  not compared standalone against another source-only snapshot; there is no
  binary to fold it onto for a library that never produces one. The closest
  practical workaround today is compiling a small stub translation unit
  that `#include`s and instantiates the public API into an actual `.so`,
  and dumping/comparing *that* — an ordinary binary+headers comparison, on
  an artifact that exists only to give the header-only API something to
  attach evidence to — rather than relying on any no-binary snapshot
  comparison this tool doesn't yet fully support. See
  [Producing Source Facts](../use/producing-source-facts.md) and
  [Dump/Compare Flags](../use/dump-compare-flags.md) for how the supported
  binary+headers path works; every mechanism in
  [Evidence & Detectability](evidence-and-detectability.md) applies once
  you have a real snapshot to apply it to.
- ODR (One Definition Rule) violations are a risk here too, as they are for
  any inline/template declaration in a *dynamic* library's public headers
  (the same declarations that make a dynamic library's own public inline
  functions "public inline" in
  [the hub's dispatcher scenario](abi-api-handling.md#the-l5-graph-reachability-not-just-structure))
  — this isn't unique to the header-only shape, just proportionally larger:
  a header-only library's *entire* public surface is inline/template
  declarations, so the exposure to "two translation units silently seeing
  different versions of the same header" (a partial update, a vendored copy
  alongside a system-installed one) is much wider than for a dynamic
  library, where only the inline/template subset of the public headers
  carries this risk.

## Practical guidance

- For a static library, treat header/source compatibility with the same
  rigor as a dynamic library's — check it with the same `dump --sources`
  source-fact comparison described above — and
  additionally pin/document the supported compiler and standard-library ABI
  range explicitly, since there's no runtime check equivalent to a loader
  refusing an incompatible SONAME.
- For a header-only library, be explicit that *every* function is
  effectively "public inline" in the sense [the hub's dispatcher
  scenario](abi-api-handling.md#the-l5-graph-reachability-not-just-structure)
  describes — a behavior change in any function reaches consumers on their
  next rebuild, with no version boundary to soften it. Document behavior
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
