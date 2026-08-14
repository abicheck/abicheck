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
isn't a first-class input the way a shared object is. The practical way to
validate a static library's compatibility with abicheck today is to check
the **headers** with `dump`/`compare -H`, which covers the source and
public-surface-scoping concerns fully, independent of the archive format.

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
- Because there's no separate binary snapshot, `abicheck dump`/`compare`
  against a header-only library is inherently a **header-AST comparison**
  (`-H`, no binary operand) — every finding is at the source/API level.
  Structural body content (macro values, template internals) still needs
  [source-level evidence (L4)](evidence-and-detectability.md) the same way
  it does for any other library; a header-only shape doesn't change what
  evidence layer is needed to see it, only that there's no L0/L1 binary
  layer available at all to fall back on.
- ODR (One Definition Rule) violations become a live risk the moment two
  translation units see *different* versions of the same header-only
  declaration (a partial update, a vendored copy alongside a system-installed
  one) — a class of bug that has no equivalent in the dynamic-library world,
  where there's exactly one definition living in the shared object.

## Practical guidance

- For a static library, treat header/source compatibility with the same
  rigor as a dynamic library's — check it with `abicheck compare -H` — and
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
