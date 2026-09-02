---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - build-profile-comparability
depends_on:
  - abicheck/comparability.py
  - abicheck/comparability_fields.py
  - abicheck/checker.py
  - abicheck/header_conditionals.py
  - abicheck/dumper_contract.py
  - abicheck/cli_dump_helpers.py
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

## A gate result, sitting in front of the ordinary verdict space

A comparison that accounts for build profile has one extra possible result
sitting *in front of* the ordinary verdict space ("no change" / "compatible
addition" / "broken"), not folded into it:

1. **The comparison is comparable — the ordinary verdict space applies.**
   OLD and NEW were extracted under a comparable profile, and whatever
   verdict comes out (including a clean `NO_CHANGE` when nothing actually
   differs) reflects real source/ABI facts about the library. This is the
   case every other page on this site is about.
2. **The comparison is not comparable, and must not produce a verdict at
   all.** OLD and NEW disagree on compiler identity, target triple,
   language standard, or another profile axis the four carve-outs described
   below don't explain — the comparison itself is not trustworthy, independent
   of whether the library's own source changed at all. This is
   deliberately not "silently downgrade to a warning" — abicheck's stance
   (ADR-050 D2) is that a genuinely incomparable pair is a **hard
   failure**: no verdict, exit `16`/`not_comparable`, with a structured
   reason explaining which axis disagreed. Reporting an ordinary verdict
   here would be answering a question nobody asked ("is GCC's ABI
   compatible with Clang's ABI?") instead of the one that was actually
   asked ("did *my* library change?"). For a single-pair `compare`, you
   can force a tentative diff anyway with the explicit
   [`--diagnostic-comparison`](../reference/cli-reference.md) opt-out, and
   the resulting report is stamped `assurance: none` everywhere so nobody
   downstream mistakes it for an ordinary, trustworthy result. This
   opt-out is **not available for a directory/package (release) fan-out**
   — the release fan-out path rejects it outright — so a
   not-comparable library inside a release fan-out has no escape hatch;
   compare that one library on its own instead.

"The environment changed" is the *reason* outcome 2 fires, not a third
outcome alongside it — the two are cause and effect of the same gate
result, not independent branches.

The reason outcome 2 exists as a hard stop rather than a soft warning: a
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
— an explicit signal that axis was
never actually checked, not silently treated as verified. When **both**
sides are missing the same axis, there's nothing to mark: neither side
asserted that fact in the first place, so there's no disagreement to
detect and no gap to flag beyond what a symbols-only or pre-contract
comparison already implies. Don't read a clean `--depth`-limited or
legacy-baseline comparison as having verified build profile at all — the
hard-fail guarantee is real, but it's a guarantee about *comparable
extractions*, not a guarantee that every comparison checked comparability.

## What actually gets fingerprinted

abicheck's comparability gate (ADR-050 D1/D2) doesn't
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
| `abi_dialect` | The GNU vs. MSVC compiler-driver mode (the compiler-mode resolver sets exactly `"gnu"` or `"msvc"`) — not the libstdc++ dual-ABI setting; two GCC extractions stay `abi_dialect="gnu"` regardless of `_GLIBCXX_USE_CXX11_ABI`, which is only pinned when that macro is explicitly passed and reaches the fingerprint through the `macro_ops` row below ([case104](../reference/examples/case104_glibcxx_dual_abi_flip.md)) |
| `language_standard` | `-std=c++17` vs. `-std=c++20` — different implicit behavior, different library surface |
| `target_triple` | Reserved for architecture/OS/environment. **Not populated by either production call site today** (the extraction-contract builder defaults it to empty, and neither the real-dump path nor the `--dump-manifest` dry-run path passes a value) — always empty on a real dump, so it never contributes a real mismatch on its own |
| `pointer_width` | Reserved for 32-bit vs. 64-bit. Same unpopulated-today status as `target_triple` |
| `endianness` | Reserved for byte order. Same unpopulated-today status as `target_triple` |
| `macro_ops` | Which `-D`/`-U` macros were in effect — conditional compilation changes what's even declared |
| `pass_through_flags` | ABI-relevant compiler flags forwarded as-is. Today only `-include <path>` is recognized — the one currently-known must-handle repeatable, order-sensitive frontend flag; other flags (e.g. `-fvisibility=`, `-fshort-enums`) are not yet classified into this field and are simply omitted rather than mis-hashed |
| `include_sequence` / `header_sequence` | Order and *declared-slot* identity of the include directories/headers fed through the dedicated `extra_includes`/manifest `includes` mechanism (never absolute path shape for those, so a two-checkout compare's differently-nested old/new sides still fingerprint identically) — narrower than "every resolved `-I`/`-isystem`": a raw `-I`/`-isystem` token embedded in `gcc_options` rather than passed through the dedicated mechanism is not collected into this field today, so replacing what a raw compiler flag points at can leave it unchanged. A *labeled* or project-owned slot's identity is genuinely pinned (by its label, or by which declared header it owns); an unlabeled, non-project-owned ("external") slot is only pinned by content when the extraction-contract builder is given resolved dependency-file paths — today's production real-dump path never passes them, so an unlabeled external slot hashes an empty file list there, and swapping one external include directory for another at the same position leaves the fingerprint unchanged |
| `frontend_context_kind` | Appended only for a DPC++-capable frontend (a SYCL host/device split); absent from the fingerprint on an ordinary clang/castxml dump |

This table is a narrative summary, not the field list's fact owner — the
exact, current set is owned by abicheck's own comparability module (plus
the conditional `frontend_context_kind` addition); see this page's
`depends_on` front matter for exactly which file, and check it directly
before relying on which flags are actually pinned today.

**`scope_fingerprint`** — the *declared surface* being compared: which
public headers and header directories were in scope, and — for a
manifest-driven multi-TU extraction — which translation units contributed.
One deliberate exception: when a side declares exactly *one* header or
exactly one public-header directory, the scope-fingerprint builder collapses
its identity to a fixed `<single-header>`/`<single-header-dir>` sentinel
rather than the real name — renaming `foo.h` to `bar.h` while staying a
single-header dump leaves `scope_fingerprint` completely unchanged. The
gate is answering "is this still a single-header scope," not "is it still
*this specific* header" — a rename inside an otherwise-unchanged
single-header scope is exactly the kind of thing the ordinary diff (not
this gate) is responsible for catching. A path *inside* the checkout is
relativized so two checkouts at different
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
decision, not an environment mismatch, and the gate's own
superset-growth check (§ below) treats a scope that only *grew* between OLD
and NEW as legitimate rather than incomparable. A `profile_fingerprint`
mismatch is the one that means "this isn't the same kind of build at all."

## Carve-outs: a real difference is not always an error

Not every fingerprint mismatch is toolchain noise to be rejected — some are
exactly the *finding* a check like this exists to surface. The gate
distinguishes them from genuine incomparability with four narrow,
evidence-gated carve-outs, all deliberately
conservative and mutually disjoint — they widen the set of comparisons the
gate allows, never the set it silently trusts, and they compose (a release
combining two independently-sanctioned deltas at once, e.g. a header
addition *and* a corroborated C++-standard raise, is still accepted):

- **Platform-identity carve-out.** The mechanism: if `target_triple`/
  `pointer_width`/`endianness` differ, but both snapshots' own
  *binary-derived* platform metadata (read from the ELF/PE/Mach-O headers
  themselves, not just the compile invocation) confirms a genuine,
  deliberate architecture difference — comparing an x86-64 build against an
  arm64 build of the same release, say — the mismatch is corroborated
  rather than treated as an extraction inconsistency. In practice, since
  all three profile fields are currently unpopulated on a real dump (see
  the field table above), this carve-out has no live mismatch to
  corroborate today — cross-architecture comparability currently rests
  entirely on the binary-derived platform check elsewhere in the pipeline,
  not on this fingerprint carve-out actually firing.
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

---

**Ladder:** ← [Consumer Models](consumer-models.md) · Tier 3 · Define the contract · [Static & Header-Only Contracts](static-and-header-only.md) →
