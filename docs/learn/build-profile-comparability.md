---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - build-profile-comparability
depends_on:
  - abicheck/comparability.py
lifecycle: active
generated: false
---

# Build Profile Comparability

Every other page in this series asks "did the library change?" This one asks
the question that has to be answered *first*: **were the two things you're
comparing even built under conditions that make comparing them meaningful at
all?** [Part 0](abi-series/00-product-contract.md) names this as one of the
eight compatibility dimensions — "were the two things being compared even
built under conditions that make the comparison meaningful?" — but doesn't
give it a narrative page of its own. This is that page.

## Why this is a different kind of question

Every other dimension on this site — source, binary, behavioral, data/wire,
deployment, ecosystem, operational — asks whether a **real change to the
library** broke something. Build-profile comparability asks a question that
comes *before* any of those can even be evaluated: is the diff you're about
to read a diff of the **library**, or is it just a diff of the **two
compilers/flags/environments** that happened to produce OLD and NEW?

Comparing a GCC 11 build of `libfoo.so` against a Clang 18 build of the exact
same, unchanged source is not "the library is compatible with itself" — it's
comparing two artifacts that were never going to look identical in the first
place, for reasons that have nothing to do with the library's own ABI/API
surface: different name-mangling edge cases, different default visibility
behavior, different struct-packing decisions for corner-case layouts,
different macro/builtin sets. A checker that doesn't gate on this either (a)
floods you with findings that are actually toolchain noise, or (b) — worse —
one real regression drowns in that noise and gets missed.

## Three outcomes, not one

A comparison that accounts for build profile has three possible outcomes,
not the two you'd expect ("compatible" / "broken"):

1. **The library changed.** OLD and NEW were extracted under a comparable
   profile, and the detected differences are attributable to real source/ABI
   changes. This is the case every other page on this site is about.
2. **The environment changed.** OLD and NEW disagree on compiler identity,
   target triple, language standard, or another profile axis — the
   comparison itself is not trustworthy, independent of whether the library's
   own source changed at all. Reporting a verdict here would be answering a
   question nobody asked ("is GCC's ABI compatible with Clang's ABI?")
   instead of the one that was actually asked ("did *my* library change?").
3. **The comparison is invalid and must not produce a verdict.** This is
   deliberately not "silently downgrade to a warning" — abicheck's stance
   (ADR-050 D2) is that a genuinely incomparable pair is a **hard failure**:
   no verdict, exit `16`/`not_comparable`, with a structured reason
   explaining which axis disagreed. You can force a tentative diff anyway
   with the explicit
   [`--diagnostic-comparison`](../reference/cli-reference.md) opt-out, and
   the resulting report is stamped `assurance: none` everywhere so nobody
   downstream mistakes it for an ordinary, trustworthy result.

The reason outcome 3 exists as a hard stop rather than a soft warning: a
"maybe compatible, maybe not, we compared apples to oranges" result is worse
than no result, because it looks exactly like a clean, comparable pass in
any pipeline that only checks the exit code.

**This hard-fail promise is qualified, not absolute — it depends on both
sides actually carrying the axis being checked.** Each of the three
coverage axes — `profile_fingerprint`, `scope_fingerprint`, and
`dependency_scope` (whether dependency-header scoping mode was tagged) —
is only compared when **both** OLD and NEW carry it; a side that never
went through an L2 frontend at all (a symbols-only dump, or a pre-contract
baseline) has no `profile_fingerprint` to disagree with, and the gate does
not fail the comparison on that axis. When exactly one side is missing any
one of the three, the report is stamped `contract_coverage: partial`
(`checker._contract_coverage_status`) — an explicit signal that axis was
never actually checked, not silently treated as verified. When **both**
sides are missing the same axis, there's nothing to mark: neither side
asserted that fact in the first place, so there's no disagreement to
detect and no gap to flag beyond what a symbols-only or pre-contract
comparison already implies. Don't read a clean `--depth`-limited or
legacy-baseline comparison as having verified build profile at all — the
hard-fail guarantee is real, but it's a guarantee about *comparable
extractions*, not a guarantee that every comparison checked comparability.

## What actually gets fingerprinted

abicheck's comparability gate (`comparability.py`, ADR-050 D1/D2) doesn't
guess at "did the build environment change" impressionistically — it hashes
two separate, named fingerprints out of every dump that went through an L2
header/AST frontend, and refuses to produce a verdict when they disagree
without a carved-out explanation.

**`profile_fingerprint`** — the *resolved compile context* used to extract
the snapshot:

| Field | What it pins |
|-------|--------------|
| `compiler_family` | GCC vs. Clang vs. MSVC — different name-mangling and layout corner cases |
| `compiler_version` | Toolchain version — builtin macro sets, default flag behavior can shift release to release |
| `abi_dialect` | e.g. the libstdc++ dual-ABI flag ([case104](../reference/examples/case104_glibcxx_dual_abi_flip.md)) |
| `language_standard` | `-std=c++17` vs. `-std=c++20` — different implicit behavior, different library surface |
| `target_triple` | Architecture/OS/environment — pointer width, calling convention, struct packing |
| `pointer_width` | 32-bit vs. 64-bit — every pointer-containing layout differs |
| `endianness` | Byte order — layout and wire-format sensitive |
| `macro_ops` | Which `-D`/`-U` macros were in effect — conditional compilation changes what's even declared |
| `pass_through_flags` | ABI-relevant compiler flags forwarded as-is. Today `pass_through_flags_from_tokens()` recognizes only `-include <path>` — the one currently-known must-handle repeatable, order-sensitive frontend flag; other flags (e.g. `-fvisibility=`, `-fshort-enums`) are not yet classified into this field and are simply omitted rather than mis-hashed |
| `include_sequence` / `header_sequence` | *Content* of the resolved `-I` search path and header set — never absolute path shape, since a two-checkout compare's old/new sides necessarily resolve to different paths for what may be an identical logical surface |
| `frontend_context_kind` | Appended only for a DPC++-capable frontend (a SYCL host/device split); absent from the fingerprint on an ordinary clang/castxml dump |

This table is a narrative summary, not the field list's fact owner — the
exact, current set is `abicheck/comparability.py`'s `PROFILE_FIELD_KEYS`
(plus the conditional `frontend_context_kind` addition); check there directly
before relying on which flags are actually pinned today.

**`scope_fingerprint`** — the *declared surface* being compared: which
public headers and header directories were in scope, and — for a
manifest-driven multi-TU extraction — which translation units contributed.
A path *inside* the checkout is relativized so two checkouts at different
nesting depths fingerprint identically — that's the "never absolute paths"
case that matters for the common two-checkout comparison. A genuinely
*external* path (declared absolute in the manifest, e.g. `/usr/include`, or
any include path with no structural relationship to the checkout root) is
deliberately kept as its resolved absolute path instead, since relativizing
it would climb a `../` distance that depends on checkout nesting rather than
on anything about the external path itself — so relocating an SDK referenced
by such a path *can* change `scope_fingerprint` and make an otherwise-identical
pair read as not comparable.

The split matters: two dumps can legitimately have different
`scope_fingerprint`s (you scoped OLD to a smaller header set than NEW,
deliberately) while still sharing a `profile_fingerprint` — that's a scoping
decision, not an environment mismatch, and `comparability.py`'s own
superset-growth check (§ below) treats a scope that only *grew* between OLD
and NEW as legitimate rather than incomparable. A `profile_fingerprint`
mismatch is the one that means "this isn't the same kind of build at all."

## Carve-outs: a real difference is not always an error

Not every fingerprint mismatch is toolchain noise to be rejected — some are
exactly the *finding* a check like this exists to surface. The gate
distinguishes them from genuine incomparability with four narrow,
evidence-gated carve-outs (`_unexplained_profile_fields()`), all deliberately
conservative and mutually disjoint — they widen the set of comparisons the
gate allows, never the set it silently trusts, and they compose (a release
combining two independently-sanctioned deltas at once, e.g. a header
addition *and* a corroborated C++-standard raise, is still accepted):

- **Platform-identity carve-out.** If `target_triple`/`pointer_width`/
  `endianness` differ, but both snapshots' own *binary-derived* platform
  metadata (read from the ELF/PE/Mach-O headers themselves, not just the
  compile invocation) confirms a genuine, deliberate architecture
  difference — comparing an x86-64 build against an arm64 build of the same
  release, say — the mismatch is corroborated rather than treated as an
  extraction inconsistency. This is a *deliberate cross-architecture
  comparison*, not drift.
- **Build-context carve-out.** If **`language_standard` or `macro_ops`**
  specifically differ (the only two fields this carve-out currently
  waives — not any arbitrary profile field), but both snapshots were parsed
  against *real build-system evidence* — a genuine, recorded build (not an
  inferred/guessed compile line) on both sides — the difference is treated
  as a real, corroborated fact about the build, not noise. This is exactly
  the shape [`case98`](../reference/examples/case98_cxx_standard_floor_raised.md)
  covers: a *deliberately* raised C++-standard floor is a real, reportable
  change (`CXX_STANDARD_FLOOR_RAISED`/`ABI_RELEVANT_BUILD_FLAG_CHANGED`),
  not a reason to refuse the whole comparison. A difference on any other
  profile field — compiler family/version, ABI dialect, pass-through flags,
  frontend context — still hard-fails even with build-context evidence on
  both sides.
- **Header/include-sequence-growth carve-outs.** If `header_sequence` and/or
  `include_sequence` differ *and* `scope_fingerprint` itself independently
  confirms a genuine, additive superset growth for the same newly-added
  header(s) — not merely a same-shaped "the sequence got longer" pattern —
  the mismatch is treated as an ordinary header addition, not drift. This
  double-check exists because a profile-level "sequence grew" shape *alone*
  isn't sufficient evidence: a header declared identically on both sides via
  `--public-header`, but only fed to the L2 frontend via `-H` on the new
  side, produces the identical additive-growth shape in `header_sequence`
  while `scope_fingerprint` stays completely unchanged — and the old
  snapshot never actually parsed that header's content, so a real removal
  inside it would go silently unreported if the carve-out trusted sequence
  shape on its own.

Everything else — an uncorroborated compiler-family swap, an uncorroborated
target-triple change, any profile mismatch none of the four carve-outs can
explain — still hard-fails. The bar for "this is a real fact about the
build, not noise" is independent, corroborating evidence on **both** sides,
not just a differing or superficially-growing flag.

## What this looks like in practice

- **Comparing two releases of the same project, built the same way** — the
  common case. Both fingerprints match (or `scope_fingerprint` differs only
  by legitimate growth); the gate is invisible and every other page's
  guidance applies directly.
- **Comparing a baseline built by CI against a live rebuild on a developer's
  machine** — the profile fingerprint is only as trustworthy as how
  reproducibly both were built. A toolchain pin drift here (different GCC
  minor version between the CI image and the dev machine) is exactly what
  the gate is designed to catch before it's misread as a library regression.
- **Deliberately comparing across architectures or compilers** (e.g. "is our
  x86-64 build ABI-equivalent to our arm64 build?") — name what you're doing
  explicitly. The platform-identity carve-out handles genuine
  cross-architecture pairs; forcing `--diagnostic-comparison` is the
  explicit, unmistakably-marked way to compare anything the gate can't
  otherwise corroborate.
- **A single project maintaining multiple supported toolchain profiles**
  (e.g. a GCC-targeting build and an MSVC-targeting build of the same
  library, or several `compile.compiler_family`-scoped profiles — see
  [Project Targets Schema](../reference/project-targets-schema.md)) —
  compare *within* a profile (GCC-old vs. GCC-new), never *across* profiles
  (GCC-old vs. MSVC-new) unless that cross-profile question is genuinely
  what you're asking. Each profile is its own comparability lineage.

## Rule of thumb

> Before reading a single finding, ask: **did OLD and NEW come from the same
> kind of build?** If you can't answer that with confidence, the diff you're
> about to read may be a diff of your toolchain, not your library — and
> abicheck's own default posture is to refuse to answer rather than guess.

## Where the mechanics live

This page is the narrative background; the enforced mechanics — the exact
exit codes, the `--diagnostic-comparison` flag, and the JSON `reason` shape
— are documented where they're enforced rather than duplicated here:

- [Exit Codes](../reference/exit-codes.md) — the `16`/`not_comparable`
  (and per-command equivalent) exit codes, and what a `verdict: null`
  report looks like.
- [CLI Reference](../reference/cli-reference.md) — `--diagnostic-comparison`
  and the comparability-relevant flags.
- [Environment & Toolchain Drift](environment-drift.md) — the sibling
  question of what happens when a dependency's own runtime-floor
  requirement drifts, rather than the comparison's own extraction context.

---

_See also: [Part 0 — Compatibility as a Product Contract](abi-series/00-product-contract.md) ·
[Evidence & Detectability](evidence-and-detectability.md) ·
[Environment & Toolchain Drift](environment-drift.md)._
