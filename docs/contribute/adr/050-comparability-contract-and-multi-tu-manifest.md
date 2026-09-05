# ADR-050: Comparability Contract — Profile/Scope Fingerprints and the Multi-TU Manifest

**Date:** 2026-07-22
**Status:** Accepted — implemented (Phase 0 and Phases A–E; D1–D6). See
[G32](../plans/g32-comparability-contract-and-multi-tu-manifest.md) for the
per-phase implementation record, including the post-merge D5/D6 review
follow-up. One thing remains intentionally unbuilt, not a gap in this ADR's
model: `service.run_dump` itself accepts `dump_manifest` (public API,
documented), so a direct caller can already dump both sides manifest-driven
and hand the snapshots to `compare_snapshots` — but the one-call
`CompareRequest`/`run_compare_request` path and the MCP tools have no
`dump_manifest` field at all, so *those specific* entry points can't reach
a manifest-driven comparison in a single call. This is deliberate (no code
for a hypothetical caller — none needs the one-call path yet), not an
oversight; should `CompareRequest` gain manifest support later,
`run_compare_request`'s existing `ABICHECK_PARALLEL_EXTRACTION` sequential
fallback is already the right lever to route it through.
**Verified:** main@2e43d53 on 2026-08-04
**Decision maker:** (pending — recorded per repository convention.)

**Amendments:**

- **2026-07-26:** D3's standalone `plan --dump-manifest` diagnostic command
  was folded into `dump --dump-manifest --dry-run` by
  [ADR-054](054-cli-project-integration-surface-consolidation.md) — the
  `scope_fingerprint` contract below is unchanged, only its CLI entry point
  moved (a second, parallel "resolve, don't execute" vocabulary next to
  `--dry-run` was avoidable drift).

---

## Context

abicheck already solves several pieces of what "safe to compare" requires:
`ScopeOrigin` classifies every declaration as
`PUBLIC_HEADER`/`PRIVATE_HEADER`/`SYSTEM_HEADER`/`GENERATED`/`EXPORT_ONLY`
(ADR-024, `model.py:131-147`), `DumpDepthNotSatisfiedError`
(`cli_dump_helpers.py:313-431`) hard-fails rather than silently degrading
when an explicitly requested `--depth` isn't reached, `snapshot_cache.py`
hashes the actual transitively-reachable content of every header (not just a
depfile's path list, so a shadowing header earlier in the search path is
already a correct cache miss), and `serialization.py` already sorts every
set before emitting JSON (ADR-015). None of this was reinvented by mistake —
it means most of a "make snapshots trustworthy" proposal is already shipped
under different names, and this ADR only needs to decide the parts that
genuinely are not.

Two gaps are real and unaddressed:

1. **`dump()` collapses every requested header into one synthetic
   translation unit.** `dumper.py:370` builds the AST input as
   `"".join(f'#include "{h.resolve()}"\n' for h in hdrs)` and runs exactly
   one castxml/clang invocation over it, with one flat 120s timeout
   (`dumper.py:1043`). There is no way to give one header group its own
   forced include (e.g. an Arrow-derived adapter header that needs
   `arrow/api.h` included first) without injecting that include into every
   other header's parse, and no way to mark one header group "optional
   evidence" vs. "required — its absence must shrink the reported surface,
   never silently disappear from it."
2. **No gate runs before `checker.compare` to prove two snapshots were
   extracted under a comparable contract.** `checker_policy.py` has
   `SOURCE_FACT_COVERAGE_INCOMPLETE` (`:618`) and a tri-state
   `ReachabilityState` (`:1024`), but both degrade to a RISK-tier finding
   *inside* a verdict that still gets produced — they annotate, they don't
   block. If an old snapshot was dumped with `-H oneapi/dal.hpp` and a new
   snapshot was dumped with `-H oneapi/dal.hpp -H oneapi/dal/graph.hpp` (a
   manifest/CLI-flag drift between two CI runs, not a real API change),
   `compare` still runs and reports every `graph.hpp` declaration as an
   addition. That is a true statement about the two snapshot *files* and a
   false one about the *library* — the two snapshots don't cover the same
   declared surface, and nothing records that the comparison itself isn't
   sound, only its output.

Both gaps were identified, in much greater depth, in a review of abicheck's
snapshot architecture prompted by a real multi-TU/DPC++ scenario (a project
whose public surface spans an umbrella header, an Arrow-derived adapter
needing its own forced include, and a SYCL host/device compilation split).
This ADR extracts the decisions from that review that are genuinely new
work. Where the review's proposal re-described something abicheck already
has — public/private/external classification, deterministic serialization,
content-hash caching, RAM-aware parallel extraction (see D6) — this ADR
cross-references the existing ADR instead of re-deciding it, so the two
descriptions cannot drift apart.

## The one rule that does not change

Same authority boundary as ADR-028 D3, `buildsource/CLAUDE.md`'s "one rule,"
and ADR-041's restatement of it: nothing in this ADR may **manufacture** a
`BREAKING_KINDS`/`API_BREAK_KINDS` verdict, and nothing in it may
**suppress** one that artifact-backed L0–L2 evidence already proves. What
this ADR adds is a **precondition gate**: when two snapshots' extraction
contracts are not comparable, `compare` must say so instead of producing
*any* verdict — generalizing the same shape of decision
`DumpDepthNotSatisfiedError` already makes for depth, to profile and scope.
"Not comparable" must never render as `compatible` (a green check hiding
risk) and must never render as `breaking` (a false positive that erodes
trust in every other finding abicheck reports).

## Decision

### D1. `ExtractionContract` — profile fingerprint and scope fingerprint

Two new fields on `AbiSnapshot` (`model.py`), carried under a new
`contract: ExtractionContract | None` sub-object. Unlike ADR-041's
`extractor_passes`/`narrowed_passes` — purely advisory fields where an old
reader silently not recognizing them degrades to the accepted, documented
"under-call" failure mode (a RISK finding that doesn't fire, never a false
compatible/breaking verdict) — the comparability gate this ADR adds (D2) is
a **hard, verdict-blocking** mechanism whose entire purpose is preventing a
false verdict on incomparable data. An old abicheck binary that predates
this ADR has no code path that even looks for `contract`, so if the field
were added the same additive, no-bump way, that old binary would silently
compare two contract-bearing (and possibly incomparable) snapshots and
produce an ordinary verdict — exactly the failure mode this ADR exists to
close, just relocated to the reader-version boundary instead of the
extraction boundary.

**`serialization.py`'s existing forward-version handling is not, on its
own, that mechanism — it only warns.** `snapshot_from_dict` (`:556-572`)
already inspects `schema_version` against the running `SCHEMA_VERSION` and,
when the snapshot's is newer, calls `warnings.warn(...)` (a `UserWarning`)
and then **continues deserializing** — it never raises. A bare
`SCHEMA_VERSION` bump alone (11 → 12) does not close this ADR's gap: an old
abicheck reading a schema-12, `contract`-bearing snapshot would print a
warning most CI setups never surface, ignore the unrecognized `contract`
key, and still produce an ordinary verdict — the exact silent-incomparable-
data failure mode this ADR exists to prevent. D1 therefore adds a real
incompatible-reader guard, not just a version bump: a new
`_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION = 12` constant in
`serialization.py` (same naming convention as the existing
`_MIN_SCHEMA_VERSION_FOR_CV_FACTS`, `:88`), checked in `snapshot_from_dict`
*before* today's warn-only branch. **The guard is keyed off "the snapshot
is newer than what this reader supports," not "this reader predates the
threshold"** — the two are not the same condition, and only the first one
is actually what "Phase-A-or-later code hard-rejects unsupported schemas"
requires: `IncompatibleSnapshotSchemaError` (`errors.py`) — **a subclass of
the existing `SnapshotError`, not a bare sibling of `AbicheckError`**,
following the identical precedent `HeaderToolchainError(SnapshotError)`
already documents ("a subclass of `SnapshotError` — existing `except
SnapshotError` handling still catches it unchanged"). `cli_resolve.py`'s
snapshot-loading paths (`:294-296`, `332-335`) already translate
`ValidationError`/`SnapshotError` into a clean `click.UsageError`/
`click.ClickException`; a schema-rejection raised while `compare`/`dump`
loads an on-disk `.abi.json` snapshot must surface through that same
existing translation, not bypass it as an unhandled internal failure the
CLI has no branch for. Semantically it fits directly too —
`SnapshotError`'s own docstring is "raised when an ABI snapshot cannot be
loaded or parsed," exactly what a too-new `schema_version` is — is raised
when
the snapshot's `schema_version` is both **greater than the running
`SCHEMA_VERSION`** (genuinely unsupported by this reader) *and* at or
above the threshold — not merely "the running version is below the
threshold." Keying it off the running version alone stops protecting the
moment a reader itself reaches schema 12: that reader would correctly
reject a schema-12 snapshot (a version *older or equal* to what it
already knows), but would silently warn-and-continue on a hypothetical
future schema-13 snapshot carrying its own new comparability-critical
field, precisely the failure mode this guard exists to close, just moved
one schema bump later. The `>` running-version comparison generalizes
correctly to that future bump without any change to this guard's logic:
a schema-13 bump only needs its own new threshold (or reuses `12` if 13
doesn't add another hard-rejection-worthy field) — the guard doesn't need
updating just because the running binary caught up to the current
threshold. Versions below the threshold keep today's
warn-and-continue behavior unchanged (the existing, deliberately lenient
default for ordinary additive fields, per ADR-041's `extractor_passes`
precedent) — only the specific jump that first introduces a
verdict-blocking field becomes a hard failure for an older reader.

**Known, permanent limitation — not something a later phase can close.**
This guard protects any reader running Phase-A-or-later code: it makes
*that* code hard-reject a schema it doesn't support instead of warning past
it, and is the right pattern for any *future* comparability-critical bump.
It does **not**, and structurally cannot, protect an already-deployed
pre-Phase-A binary — that binary's `snapshot_from_dict` has no
`_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION` check compiled into it at
all, only the unconditional warn-and-continue branch, and no change to
future abicheck releases can retroactively alter code already running
elsewhere. A fleet where some environments have upgraded past Phase A and
others haven't can still see a not-yet-upgraded reader silently produce an
ordinary verdict on a `contract`-bearing snapshot. This is the same
unavoidable boundary every additive capability gate has (an abicheck old
enough to predate `DumpDepthNotSatisfiedError` doesn't enforce it either)
— the mitigation is operational (upgrade a comparison pipeline's producer
and consumer together), not something this ADR's on-disk format can
guarantee unilaterally. Documented here so it's a known, accepted limit,
not a latent surprise discovered after Phase A ships.

- `profile_fingerprint: str` — a `sha256:`-prefixed digest of the
  **resolved** compile context: compiler family/version, target triple,
  `abi_dialect` (Itanium/MSVC), language standard, pointer width/endianness,
  and the *ordered* sequence of macro define/undef operations and include
  paths (order matters for `-D`/`-U`/`-I` — last-one-wins semantics are
  real). Computed from fields `dumper.py` already resolves today
  (`ast_producer`, `ast_toolchain`, `build_context_defines`,
  `language_profile`, `platform` — `model.py:507-648`); this is a
  normalization + hashing pass over existing data, not new extraction.
  Unknown/unrecognized compiler flags are hashed by default (fail closed,
  matching the review's "unknown ⇒ contract-affecting until proven
  otherwise" principle) rather than silently ignored. **`frontend_context`
  (`host`/`device`, D5) is also one of these hashed fields, once D3/D5
  introduce it** — this field doesn't exist at Phase-A-ships time (D1
  ships before D3/D5), so it's necessarily added to the hash set later,
  not omitted by design; see D5's own note for why this can't be deferred
  once the field exists.
- `scope_fingerprint: str` — a `sha256:`-prefixed digest of the
  **manifest-normalized** analysis scope: the set of translation units (by
  `name`, not by list position), each TU's ordered includes and forced
  includes, each TU's `required`/`contributes_to_abi` flags, and the
  `public_header_paths`/`public_header_dirs`/filtering policy already
  threaded through `dumper.py` today. **For the legacy single-TU path this
  filtering policy is exactly today's `--public-header`/`--public-header-dir`
  CLI flags; for an explicit `--dump-manifest`, the same policy is a
  base-profile field on the manifest document itself (D3), not the CLI
  flags** — `--dump-manifest` and `--public-header`/`--public-header-dir`
  are mutually exclusive on `dump`, so the manifest document is always the
  complete, sole source of this fingerprint's inputs, never a CLI-flag/
  manifest split that `plan --dump-manifest` (D3) couldn't see half of.
  The `contributes_to_abi` flag is a
  hashed input, not just a manifest-validation detail (D3): flipping a TU
  from `contributes_to_abi: false` to `true` changes which declarations
  feed the ABI model without necessarily changing that TU's includes at
  all, so a fingerprint computed only from includes/forced-includes would
  let exactly the kind of scope drift this ADR exists to catch pass through
  as "identical scope." Computed from the *normalized* manifest (D3), not
  raw YAML bytes — reordering two independent TU entries, or adding a
  comment, must not change the fingerprint; reordering includes *within*
  one TU, or changing either flag, must.

**Both fingerprints hash root-relative paths, never absolute or
side-specific ones — this is not optional, it protects abicheck's single
most common workflow.** `compare` already supports side-scoped
`--header old=v1/foo.h --header new=v2/foo.h` and
`--include old=inc1 --include new=inc2` (ADR-040, `cli_options.py:225+`)
for the ordinary two-checkout-tree comparison — the old and new sides
*necessarily* resolve to different absolute paths even when they cover the
identical logical surface, precisely because they live in different
checkouts. Hashing resolved absolute paths directly would make every
routine `compare` invocation fingerprint-mismatch and hard-fail as
`not_comparable` — the gate would break its primary use case on day one,
the exact inverse of what it's for.

**The two fingerprints normalize their path inputs *separately*, each
against its own root — they must not share one combined root.**
`scope_fingerprint`'s inputs are header/TU paths (the declared surface);
`profile_fingerprint`'s inputs are `-I` include-*search* directories (how
the compiler resolves `#include`, not what's declared). Header paths and
include-search directories commonly point to unrelated places on disk — a
project's own headers live under the checkout root, while `-I` dependency
directories (`--include old=/opt/dep --include new=/opt/dep`, a shared,
often *identical*, external path on both sides) can sit anywhere,
including well outside either checkout. Computing one shared root from
*both* categories together — a mistake an earlier revision of this
paragraph made — lets an out-of-checkout `-I` directory drag the common
ancestor up to the filesystem root (`/`) once it shares no meaningful
prefix with the project headers, which reintroduces the exact bug this
fix exists to close: the header paths then normalize relative to `/`, so
`old=/work/v1/foo.h` and `new=/work/v2/foo.h` still carry their diverging
checkout roots (`work/v1/foo.h` vs. `work/v2/foo.h`) into
`scope_fingerprint`, hard-failing an otherwise-identical comparison.

For the legacy, non-manifest CLI path, `scope_fingerprint`'s root is the
common ancestor **directory** of that side's own header paths' *parent*
directories only (never `-I` directories). Computing it from the header
paths directly, rather than their parents, degenerates in the single-entry
case that's actually the common one: the "common prefix" of a one-element
path set is that whole path, so `old=v1/foo.h` and `new=v2/bar.h` would
both normalize their sole header to the same empty/root marker, losing the
filename entirely — two genuinely different public scopes would then hash
identically and wrongly pass the gate. Taking the parent directory first
means a lone header's basename survives normalization (`v1/foo.h` → root
`v1/`, normalized path `foo.h`).

**Known, accepted limitation: this rule only preserves the basename, not
the header's own subpath, so it can't distinguish a harmless checkout-root
difference from a genuine relocation between two single-header inputs that
happen to share a basename.** `old=old/include/foo.h` and
`new=new/private/foo.h` both normalize to the identical `foo.h` — correct
for the intended ADR-040 case (`old=v1/foo.h`/`new=v2/foo.h`, the same
logical header at two checkout-version labels), but the identical
normalization also fires when `foo.h` genuinely moved from a public
`include/` directory to a `private/` one between the two sides — a real
scope change (arguably: has the header stopped being public?) that this
rule cannot tell apart from the harmless case, since both examples have the
same shape (a single header, differing parent directory name) and opposite
correct answers — the identical structural reason no `-I`-directory
path-shape heuristic could be made correct either (see below). This is not
solved by a cleverer rule; it's the same "undecidable from path shape
alone" limitation this ADR already accepts for `-I` directories, now
recorded for the single-header `scope_fingerprint` case too. The
manifest-driven path (D3) has no such gap: a manifest's TU paths are
explicit, declared identities, not inferred from directory shape, so a
manifest that moved a header from one section to another would show up as
a real, explicit scope change, not a silent non-event.

**`profile_fingerprint`'s `-I` directories are fingerprinted by *resolved
content*, not by path shape — three path-shape heuristics were tried and
rejected in turn before landing here, worth recording in full so none of
the three is rediscovered.** A first attempt applied the header
parent-directory rule unchanged (root = the `-I` directory's own parent),
right for the common real-world shape — `--include old=old/include
--include new=new/include` (the project's own include root, exactly the
ADR-040 "same project, two checkouts" case; the [user-guide's real-world
compare example](../../start/real-world-example.md) uses this exact
shape) — but wrong for a lone *external dependency* directory: `--include
old=/opt/dep-v1/include --include new=/opt/dep-v2/include` would normalize
both to `include` relative to their own root, silently erasing a genuine
dependency-version difference. A second attempt hashed each `-I`
directory's last two path components instead (`/opt/dep-v1/include` →
`dep-v1/include`) — this broke the *other* direction, making the ordinary
`old/include`/`new/include` project-root case hash as different and
hard-fail the routine, documented two-checkout compare. A third attempt
reverted to the parent-directory rule uniformly (single or multiple `-I`
directories, common ancestor of parents, no special case) and accepted the
dependency-version gap as a documented limitation — this in turn broke a
*third* direction once a side declares more than one `-I` directory of
different kinds: a normal compare with a side-specific project include
plus a shared external dependency (`old=/work/v1/include` +
`old=/opt/dep`, `new=/work/v2/include` + `new=/opt/dep`, the dependency
identical on both sides) computes each side's common ancestor as `/` (the
external `/opt/dep` shares no meaningful prefix with `/work/v{1,2}`), which
normalizes the project include back to its diverging checkout root
(`work/v1/include` vs. `work/v2/include`) and hard-fails `PROFILE_MISMATCH`
on an otherwise-identical, routine two-checkout upgrade — reintroducing
the exact class of bug this whole fingerprint design exists to close, one
level deeper (mixing heterogeneous `-I` *categories* into one shared root
computation, the same mistake `scope_fingerprint`/`profile_fingerprint`
splitting into separate roots already fixed once, recurring *within*
`profile_fingerprint` itself).

All three attempts fail for the same underlying reason, and it's now clear
no path-shape function of `-I` directories can be made correct: **whether
a differently-rooted `-I` path means "same dependency, different checkout
mount point" or "a genuinely different dependency" is not decidable from
path shape alone, and combining multiple `-I` directories under one shared
root additionally risks corrupting entries that would have normalized
correctly on their own.** `profile_fingerprint` therefore does not compute
a root from `-I` path text at all. Each `-I` directory (per side, in
declared order — order is already a hashed input, per the note above)
contributes its own, independent digest: the sorted set of (path relative
to that `-I` directory, content hash) pairs for every header file the
preprocessor actually opened from inside it.

**The digest's input must be the full transitive include list, not just
headers that end up owning a declaration.** An earlier revision of this
paragraph proposed sourcing it from `dumper_castxml.py`/`dumper_clang.py`'s
existing per-declaration `_source_location`/`header_from_location`
tracking — cheap, since that data is already collected, but wrong: a
header pulled in purely for macros/pragmas/other preprocessing state (a
`abi_config.h` that `#define`s an ABI-affecting layout macro but declares
nothing itself) never owns a declaration, so it would never appear in that
per-declaration set. Two dependency versions differing *only* in such a
header would silently produce the same digest, letting a genuinely
non-comparable pair back through the gate — reintroducing this section's
own problem one level deeper, through an under-counted file set instead of
an ambiguous path. The digest is instead built from each `-I` directory's
**actual resolved file list** — every file the preprocessor opened from
inside it, declaration-bearing or not — obtained the same way
`abicheck/buildsource/include_graph.py`'s existing depfile mechanism
already does for the L3 include graph (`parse_depfile()`, a pure,
already-unit-tested parser over standard Make-rule depfile output), **using
the same system-inclusive flag that module already had to learn to use for
the same reason**: the L2 castxml/clang invocation additionally requests a
depfile via `-MD -MF <path>` (not `-MMD`) alongside the AST dump — `-MD`
lists system-classified headers (those reached via `-isystem`/the sysroot/
standard library) as well as user headers, while `-MMD` silently omits them.
`include_graph.py:354-356` already documents exactly this: it deliberately
uses `-M`, not `-MM`, "so depfiles include *system*-classified headers,"
after an earlier review caught the same omission there. Using `-MMD` here
would reintroduce that identical bug on a new code path: a public header (or
supporting header) reached only through a system/sysroot include path would
never appear in the depfile, so two dumps that actually parsed different
system-resolved headers (a libstdc++ upgrade changing an ABI-relevant macro,
for instance) could still produce matching `profile_fingerprint`s — the
exact under-counting failure mode this whole digest redesign exists to
close, through a flag choice instead of a data-source choice this time.
castxml already wraps a real compiler, so the same `-MD` flag applies to its
underlying invocation. Every listed path is attributed to whichever declared
`-I` directory contains it. This reuses a proven parser at a new call site —
one additional cheap compiler flag per TU, not a second compiler invocation
or a directory-tree walk — rather than inventing new file-discovery logic.

**Project-ownership isn't only a declared-`-I`-directory concept — a
declared header's own parent directory is implicitly project-owned too,
even with no `--include` naming it at all, or the single most common
workflow (header-only, no `-I`) reintroduces this whole section's bug.**
The C/C++ preprocessor resolves a quote-include (`#include "detail.h"`)
by first searching the *including file's own directory*, independent of
any `-I` search path — standard behavior no compiler flag is needed to
get. A side declared with only `--header old/include/foo.h` (no
`--include` at all — an entirely ordinary, minimal invocation) still has
`foo.h`'s own directory searched for its quote-includes; if `foo.h`
`#include`s a private `detail.h` from that same directory, the depfile
lists `detail.h` under `old/include/`, but no `-I` directory was ever
declared there for the ancestor rule above to classify. Left unhandled,
this path falls through to "not under any declared `-I` directory" (the
system/toolchain bucket described next) and gets full content hashing —
so an ordinary, project-internal edit to `detail.h` flips
`profile_fingerprint` and hard-fails the gate on the single most common
compare shape (headers only, no explicit `-I`), not an edge case. The
project-ownership predicate is therefore not "is this path under a
*declared* `-I` directory that's project-owned" alone; it also treats the
**parent directory of every declared `--header`/manifest TU path** as an
implicitly project-owned root, whether or not that same directory was
separately passed via `--include` — the same exclusion-in-entirety
treatment (not file-by-file) the explicit ancestor rule already applies,
just triggered by a header's own location instead of a declared `-I`
flag. This closes the gap for exactly the workflow variant the explicitly-
declared-`-I` fix above didn't cover: no `--include` present at all.

**Not every `-MD`-listed path falls under a declared `-I` directory, and
those paths cannot be silently dropped or mis-attributed.** `dumper.py`
already introduces header search paths that are never part of the
user-declared `includes` list: `--sysroot` (`--sysroot=<path>`), the
GNU-toolchain `-isystem` directories `dumper.py` probes and injects
automatically (`_probe_gnu_system_includes`), and any `-isystem`/`-I`
embedded in `--gcc-options`/`--gcc-option` pass-through flags. A depfile
entry resolved through one of these has no declared `-I` directory to be
attributed to under the per-directory rule above. Leaving this case
unspecified would recreate the exact under-counting bug this whole redesign
exists to close, one layer further out: a toolchain/sysroot/stdlib upgrade
changing an ABI-relevant system header would never be attributed anywhere,
so it would never affect `profile_fingerprint`, silently letting a genuine
environment change through the gate. These paths — everything the depfile
lists that isn't under any declared `-I` directory — instead feed one
additional, explicitly-labeled **system/toolchain bucket**: a content
digest of that unordered set (no path-shape normalization attempted here,
since these paths aren't tied to any user-declared, order-sensitive `-I`
sequence to begin with — unlike declared `-I` directories, order doesn't
carry search-precedence *meaning* the fingerprint needs to preserve for
this bucket). `profile_fingerprint`'s `-I` component is therefore the hash
of the **ordered** sequence of per-`-I`-directory digests, **plus** this one
additional system/toolchain bucket appended last, deterministically
positioned so its presence or absence never depends on iteration order.

**The depfile's own generated driver file must be excluded before any of
this bucketing runs — not swept into the system/toolchain bucket as "just
another unattributed path."** `dumper.py` writes a synthetic aggregate
`#include` header via `tempfile.NamedTemporaryFile` (`:364,1019`) and
compiles *that* as the TU's real source; `parse_depfile`'s own contract
(`buildsource/include_graph.py:210-235`, confirmed by
`tests/test_include_graph.py`'s `parse_depfile("foo.o: foo.cpp a.h b.h") ==
["foo.cpp", "a.h", "b.h"]`) returns the compiled source itself as the first
prerequisite, not only the headers it pulls in. That generated `/tmp` file
is under no declared `-I` directory, so the rule above would otherwise
sweep it straight into the system/toolchain bucket — and its *content*
embeds the side-specific absolute `#include "..."` paths `dumper.py` wrote
for that run's own header list, which necessarily differ between old and
new sides for the ordinary two-checkout case (different checkout roots
mean different absolute paths), even when the actual compile environment
is identical. Bucketing it would make `profile_fingerprint` differ on
*every* routine compare, not an edge case — the single worst-case version
of the failure mode this whole redesign exists to close. The generated
driver TU (identified as `dumper.py`'s own synthesized source path, not a
declared `-I`/`-H` input) is therefore dropped before any bucketing runs,
never hashed into either the per-`-I` digests or the system/toolchain
bucket — it is abicheck's own scaffolding, not a dependency.

**The digest must exclude every path already claimed by `scope_fingerprint`
— this is not an optional refinement, it is the difference between a
working gate and one that hard-fails on every ordinary compare.** The
documented real-world workflow (`docs/start/real-world-example.md:61-63`)
passes the project's own include root as *both* `--header` (the declared
public headers being compared) *and* `--include` (so `#include "foo.h"`
resolves) — the same directory serves both roles. A depfile for that TU
necessarily lists `foo.h` itself alongside its supporting headers, since
`foo.h` is exactly what got compiled. If the naive digest above hashed
every depfile-listed path unconditionally, `foo.h` — the header the diff
exists to compare — would feed `profile_fingerprint` too, and an ordinary,
intentional edit to `foo.h` (changing its content hash) would flip
`profile_fingerprint` and hard-fail `PROFILE_MISMATCH` *before* the diff
ever ran, on literally the routine case this whole ADR exists to support.

**Excluding only the explicitly-named header is not enough — the exclusion
has to cover the whole project-owned `-I` directory, or an ordinary edit
to any *unnamed* internal header still breaks the same way.** A first
version of this fix excluded only the specific paths `scope_fingerprint`
names (the explicit `--header`/manifest entry points) from the digest —
correct for `foo.h` itself, but most real projects have far more headers
than the ones named on the command line: `foo.h` typically
`#include`s project-internal support headers (`detail.h`, a private
implementation header) that are never individually named, reached only
because they live under the same declared `-I` root. Those files are still
depfile-listed and still fall under a declared `-I` directory, so the
first-version fix would still feed their content into that directory's
digest — meaning an ordinary internal refactor (renaming `detail_v1.h` to
`detail_v2.h`, or editing its content, with `foo.h` itself untouched) would
still flip `profile_fingerprint` and hard-fail the gate before the diff
ran, on a routine internal change, not an edge case. The fix generalizes
from "exclude the named file" to "exclude the whole `-I` directory when
it's the project's own": a declared `-I` directory is **project-owned**
when it equals, or is an ancestor of, any of that side's declared
`--header`/manifest TU paths — every file under it (named or not) is
scope-adjacent, not environment, and is excluded from `profile_fingerprint`
in its entirety, not file-by-file. A declared `-I` directory with no such
relationship to any declared header is **external** and keeps the full
per-file content digest described above — a genuine third-party dependency,
where a change anywhere in it *is* meaningful profile drift.

**The ancestor rule alone misses a common, non-nested project layout: a
support directory declared as a *sibling* of the public header root, not
underneath it.** A public header `include/foo.h` frequently `#include`s a
build-generated header from `generated/`, or a private implementation
header from `src/` or `config/` — directories passed via their own
`--include`, but not an ancestor of any declared `--header`, since they sit
next to `include/`, not inside it. The ancestor rule classifies each of
these as **external** today, so an ordinary edit to a build-generated or
private support header — exactly the same routine-internal-change case
the whole-directory exclusion above exists to protect — still flips
`profile_fingerprint` and hard-fails the gate, on a project layout common
enough (any CMake/Meson build with a generated-headers directory) that
this is not the same "unusual declaration shape" class as the
nested-vendor-dependency gap below; it is a routine one.

**A separate, independently-repeatable `--project-include` option cannot
carry this information at all, regardless of its own value grammar — Click
does not preserve declaration order *across* two differently-named
repeatable options.** Verified against Click's actual parsing model (and
independently against real Click behavior): a `multiple=True` option's
callback receives that option's own accumulated values as one tuple, in
the order *that option* was repeated on the command line, but Click never
records the interleaved position of one option's occurrences relative to
a *different* option's — `--include dep --project-include support=src` and
`--project-include support=src --include dep` arrive at the command
callback as the identical `(include=('dep',), project_include=('support=src',))`,
with no way to recover which came first. Since `profile_fingerprint`'s
whole `-I` ordering design is search-precedence order — the actual
relative position the compiler sees — a second, separately-declared
option can never feed it correctly no matter how its own value is
shaped; the earlier `SidedLabeledPathParam`-as-its-own-option design was
wrong on this axis before its value grammar was even considered. **The fix
is to not add a second option at all: the label rides on `--include`
itself**, the one option whose repeated occurrences Click already keeps
in true declaration order (the same guarantee the whole `-I`-sequence
design already depends on for plain `--include`/`--include` pairs).
`abicheck/cli_params.py`'s existing `SidedPathParam` (ADR-040 Lever 1,
shared today by `--include`, `--header`, and other sided-path options) is
extended for `--include` specifically into a new `SidedIncludePathParam`
— `--header` and the other sided-path options keep the unchanged,
2-tuple `SidedPathParam`, since only `--include` needs a label slot.
`SidedIncludePathParam` recognizes an optional labeled form layered on
top of the existing `[old=|new=|both=]PATH` grammar. **The labeled form
requires the literal colon prefix — `old:`, `new:`, or `both:` — with no
colon-less/bare labeled variant at all; the bracket in
`[old:|new:|both:]LABEL=PATH` means "one of these three literal prefixes
is present," never "the whole prefix segment, colon included, may be
omitted while still parsing as `LABEL=PATH`."** Getting this backwards
would silently break existing usage: `SidedPathParam.convert` (the type
`--include` uses today) checks only `s.startswith("old=")` /
`"new="` / `"both="`, so an ordinary external directory that happens to
contain a literal `=` past that point — `build/config=asan/include`, a
real, valid `--include` value today — never matches any of those three
prefixes and falls through unchanged to `("both", Path(...))`. If the new
type additionally tried a bare (colon-less) `LABEL=PATH` split on *any*
value with no recognized prefix, `build/config=asan/include` would be
reinterpreted as `label="build/config"`, `path="asan/include"` — a
different compiler argument and a directory now wrongly eligible for the
labeled per-slot token, breaking a currently-valid, unrelated value that
never opted into labeling. The fix is definitional, not a runtime check to
add: a value is only ever inspected for a label *after* one of the three
literal `old:`/`new:`/`both:` prefixes has already matched; every other
value — bare, `old=`/`new=`/`both=`-prefixed, or containing an unrelated
`=` — takes the exact, unmodified path `SidedPathParam` already takes
today, `label=None` unconditionally, `=` treated as an ordinary path
character precisely as it is now. A genuine two-checkout compare with
side-specific support-root paths under one shared logical identity is
declared `--include old:support=old/src --include new:support=new/src`
— same `support` label on both invocations (so the per-slot token below
matches across sides for the same logical root), different paths (each
side resolves its own checkout's directory) — interleaved with any number
of ordinary `--include old=/opt/dep` entries in exactly the order they
were typed, since it is all one option's accumulated tuple. The label is
required for this labeled form specifically — not a path-derived name, a
short user-supplied logical identifier, the same "name a TU instead of
inferring one from path shape" choice the manifest path (D3) already
makes for its `name` field — because an explicitly-declared support root
has no natural "owned declared header" for the per-slot token below to
derive from the way an ancestor-derived root does; asking the user for
one avoids inventing yet another path-shape heuristic that could break in
some other way, the repeated lesson of every rejected attempt in this
section. **This labeled `--include` form is legacy-CLI-only — but the
manifest path (D3) is not automatically exempt from the same gap, and an
earlier revision of this section wrongly claimed it was.**
`forced_includes` (D3) is a per-TU list of individual header *files*
force-included into that TU's compile (`-include foo.h`), the manifest
equivalent of a single named header — it says nothing about a TU's
`includes` list, the manifest's own `-I` *search-path* entries used for
ordinary `#include "..."` resolution. A manifest TU declaring `includes:
[../src]` or `includes: [generated/]` to resolve a private support header
or a build-generated one has exactly the same problem the legacy CLI just
got fixed for: `../src`/`generated/` is a *sibling* of the TU's own
declared path, not an ancestor of it, so D1's ancestor rule alone
classifies it **external**, and an ordinary edit to a header inside it
still flips `profile_fingerprint` before the diff ever runs. The manifest
schema therefore gains the same escape hatch, in its own idiom: an
`includes` entry is either a bare path string (external-by-default,
ancestor rule decides ownership, unchanged) or a mapping
`{path: ..., project_owned: true}` explicitly asserting the entry is
project-owned regardless of ancestry. Unlike the legacy CLI's labeled
`--include` form, a manifest entry needs **no separate user-supplied
label** for the per-slot token: manifest paths are already
root-relative and side-normalized by design (D1's "both fingerprints hash
root-relative paths" principle), so two manifests describing the same
logical support root — `includes: [{path: ../src, project_owned: true}]`
on both the old and new side's manifest — already share the same stable,
mount-point-independent path string; that string itself serves as the
per-slot token, the same way a manifest TU's declared `name` already
serves as stable identity elsewhere in this ADR. This closes the gap in
the manifest's own structured-YAML idiom rather than reusing the CLI's
colon/`=` string grammar, which has no reason to exist in a schema that
already supports mapping values natively.

**`--include` is not one Click registration shared by `compare`/`dump`/`scan`
— it is three separate ones today, and `SidedIncludePathParam` only fixes
the one this section has been describing.** Verified against the actual
code: `cli_options.py`'s `two_sided_input_options` (the `SIDED_PATH_PARAM`
registration this section extends) is applied only to native `compare`;
`dump_cmd`'s own `--include` (`cli.py:486`) is declared inline as a plain,
non-sided `click.Path` (`dump` has one input, no old/new side concept at
all, so it never carried `SidedPathParam` in the first place); `scan_cmd`'s
own `--include` (`cli_scan.py:487-495`) is *also* declared inline, with its
own separate `type=SIDED_PATH_PARAM` registration, not the `compare`
decorator. Fixing only `compare`'s registration leaves a snapshot produced
via `abicheck dump` or `abicheck scan --against` with no way to express a
project-support label at all — `project_include_labels` stays empty for
those commands, and a sibling support root a `dump`/`scan` invocation
declares stays classified as external, reproducing the exact
`PROFILE_MISMATCH` this whole fix exists to close, just on two commands
instead of one. `scan_cmd`'s inline registration switches to
`SidedIncludePathParam` too (it already has old=/new= side semantics
identical to `compare`'s, scoping to the current artifact vs. the
`--against` side); its `split_sided_paths(include_pairs)` call becomes
`split_sided_include_paths(include_pairs)`.

**`dump_cmd` cannot reuse `SidedIncludePathParam` as-is — doing so
silently breaks a currently-valid `dump --include` value, the exact class
of regression this design has already had to catch twice for other
grammars.** `SidedIncludePathParam` still honors the *unlabeled*
`[old=|new=|both=]PATH` grammar `SidedPathParam` already implements —
that's deliberate, so `compare`/`scan` (which already had this prefix
rule) don't change behavior. But `dump_cmd`'s `--include` is a plain
`click.Path` today: it has *never* recognized `old=`/`new=`/`both=` as a
side prefix, so a directory literally named `old=foo/` is a valid,
existing `--include` value there. Switching `dump_cmd` to
`SidedIncludePathParam` wholesale would newly start stripping that prefix
on `dump` too — a real, if narrow, behavior change on a flag that never
had this ambiguity before, unlike `compare`/`scan`. `dump_cmd` therefore
gets its own, `dump`-specific type, `LabeledIncludePathParam`: it
recognizes **only** the colon-terminated `both:LABEL=PATH` form (colon
was never meaningful to `dump`'s plain `click.Path` before, so adding it
is purely additive) and treats *every other value* — bare, or one that
happens to literally start with `old=`/`new=`/`both=` — as an ordinary,
unlabeled path exactly as `click.Path` does today, with **no** equals-form
side-prefix stripping at all. `dump`'s labeled form is therefore
`--include both:support=path` (side is not a real concept for `dump`, so
only `both:` is accepted — there is no `old:`/`new:` on a single-input
command); a value like `--include old=foo/` keeps meaning the literal
directory `old=foo/`, unchanged.
This is a strict partition on `-I` *directories*, not individual files:
`scope_fingerprint` owns everything under a project-owned root (declared
or not); `profile_fingerprint` owns only external roots, in full.

**Excluding a project-owned directory's *content* must not also erase its
*position* in the declared `-I` sequence — flag order changes which root
wins an ambiguous `#include`, and the fingerprint has to keep tracking
that even though it stops tracking the directory's content.** `-I` order is
search-precedence order: given `-I project -I dep` and `-I dep -I project`
over otherwise-identical files, an `#include "config.h"` present in both
`project/` and `dep/` resolves to a *different* file depending on which
flag came first — a real difference in what got compiled, not a
cosmetic reordering. If the project-owned exclusion above simply dropped
that directory's slot from the per-`-I`-directory sequence, both orderings
would degrade to the same single-element sequence (`dep`'s digest alone),
since the project root contributes nothing once excluded — collapsing two
extractions with genuinely different, ambiguity-resolving `#include`
behavior into one identical `profile_fingerprint`, exactly the false-match
failure mode this whole digest exists to close, reintroduced through the
exclusion mechanism itself. The fix keeps the sequence positional: each
declared `-I` directory still occupies its own slot in the ordered
sequence, in declaration order; a project-owned slot's *content* is
replaced with a per-slot logical token (not being omitted) rather than a
single generic constant — **a single shared sentinel for every
project-owned slot loses order information again, one level down, when
there are two or more project-owned roots.** `-I include -I generated`
vs. `-I generated -I include` (both directories project-owned, both
byte-identical between old and new, but declared in swapped order) is the
same ambiguous-`#include`-resolution problem as the project/external
case above, and a shared constant sentinel hashes both orderings to the
identical `[SENTINEL, SENTINEL]` sequence — silently losing exactly the
order information this whole fix exists to keep. The token is instead
derived per slot from one of two sources depending on *why* the slot is
project-owned: for an **ancestor-derived** root, the **sorted set of
declared `--header`/manifest TU names that directory is an ancestor of**
(not its path, not its content) — two ancestor-derived directories that
are ancestors of different declared headers get different tokens, so
swapping their declared order changes the hashed sequence, while a
directory that is ancestor of the *same* declared header set on both old
and new sides still tokenizes identically regardless of its own mount
point, consistent with `scope_fingerprint` already treating declared
header *names* (not paths) as legitimate, already-tracked identity, so
this leaks nothing beyond what `scope_fingerprint` exposes today; for an
explicitly-labeled **`--include old:LABEL=PATH`/`--include
new:LABEL=PATH`** support root (which owns no declared header by
construction — that is exactly why it needs the label form above), the
token is its required user-supplied **`label`** instead, namespaced
separately from the ancestor-derived token space so a label string can
never accidentally collide with a declared header name.
`-I project -I dep` (`project` ancestor of declared header `foo.h`)
therefore hashes `[token(foo.h), digest(dep)]` and `-I dep -I project`
hashes `[digest(dep), token(foo.h)]` — different sequences, different
`profile_fingerprint`s, correctly flagging non-comparability; two
ancestor-derived roots for different declared headers (`include/` →
`foo.h`, a second header root → `bar.h`) produce distinguishable,
order-sensitive tokens instead of collapsing to one interchangeable
constant, and two labeled `--include` roots (`--include
both:support=old/src`, `--include both:generated=old/gen`) are
distinguished by their distinct labels the same way. **Residual
limitation, same class as the vendored-nested-dependency
gap below:** two separately declared `-I` roots that are both ancestors of
the *same* declared header (an outer directory and one of its own
subdirectories, both passed as separate `-I` entries) tokenize identically
and so remain order-indistinguishable from each other — an unusual
declaration shape, not the routine case this fix targets. The
system/toolchain bucket is unaffected — it is explicitly unordered (see
above) because its inputs were never part of a user-declared,
precedence-bearing `-I` sequence to begin with.
A known, accepted residual gap: a vendored dependency nested *inside* a
project-owned root (e.g. `include/thirdparty/foo.h` under the project's
own `include/`) is swept into the project-owned exclusion along with
everything else there, so a content change confined to that nested vendor
copy is invisible to `profile_fingerprint` on the legacy CLI path — the
same class of "can't disambiguate from path/directory-tree shape alone"
limitation this ADR already documents for the mixed-roots case, not a new
kind of gap. The manifest path (D3) has no such gap: it can express a
per-TU forced-include for exactly this case instead of relying on
directory-tree inference.

This is lossless with respect to every case the three rejected attempts
traded off against each other, because content, unlike a path, is not
ambiguous: two checkouts of a byte-identical dependency normalize
identically regardless of mount point (`old=/opt/dep-v1`,
`new=/opt/dep-v2`, same header content on both sides — attempt one's
routine case, still correct); two mount points with genuinely different
header content normalize differently regardless of naming, including the
`dep-v1`/`dep-v2` case attempt one broke and attempt two overcorrected for;
and a shared external dependency alongside a side-specific project
include normalizes each independently, since there is no shared-root
computation left to corrupt — attempt three's regression is structurally
impossible here, not just untested for. If a resolved header's content
cannot be read at fingerprint time (permission error, file removed between
parse and fingerprinting), extraction fails outright with a dedicated
error rather than folding an "unresolvable" sentinel into the hash — two
runs that are each unresolvable for different underlying reasons must not
spuriously fingerprint-match. `scope_fingerprint` is unaffected by any of
this: it hashes header/TU *paths* because declared naming is itself part
of the public surface being compared (a header renamed with identical
content is still a scope change), whereas `profile_fingerprint`'s `-I`
directories describe *how* `#include` resolves, where identity should
track resolved content, not the path label pointing at it. The
manifest-driven path (D3) is unaffected either way — it already had no
such gap, since every manifest-declared path is relative to one explicit
document rather than inferred from directory shape.

Both fingerprints live in a new `contract: ExtractionContract | None` field
on `AbiSnapshot` rather than flattening two more top-level fields onto an
already-large dataclass — `ExtractionContract` is the one new nested type
this ADR introduces on the model, deliberately scoped to just the two
fingerprints plus the resolved fields that produce them (so a report can
show *what* differs, not just that the hashes don't match).

**Modeling the field is not the same as populating it, and this ADR
requires both.** `dump()` (`dumper.py`) is the one place that already
resolves every input both fingerprints are computed from — it must call
`comparability.compute_extraction_contract(...)` and attach the result to
the `AbiSnapshot` it returns, for every dump, not only a manifest-driven
one (D3). Without this wired in from D1, `contract` stays `None` on every
freshly-produced snapshot, and since D2's gate only ever raises when
**both** sides carry a `contract`, two perfectly ordinary dumps would
silently take the same code path as the intentionally-lenient mixed-pair
case (D2) forever — the gate would be fully specified and fully inert.

**Wiring the call site into `dumper.py` is not free real estate — `dumper.py`
is already at its file-size hard cap with zero headroom, and D1 can ship
before D3's manifest split exists to absorb the cost.** `dumper.py` is
exactly `2000` lines today (`wc -l`), the same fact D3/D6's own per-TU-loop
split (`dumper_manifest.py`, below) is justified by — but D1 lands
independently of D3 (D1 requires only Phase 0; it does not depend on D3),
so an implementation following D1 alone cannot rely on D3's later split
having already created headroom. Any net-positive line addition to
`dumper.py` — even the few lines this call site and its
`project_include_labels` parameter need — immediately trips the
AI-readiness `file-size` gate's `>2000` ERROR threshold. D1 therefore
requires, in the same change that wires in `compute_extraction_contract`,
a net-neutral-or-negative line-count delta to `dumper.py`: relocating a
self-contained, currently-unrelated chunk of `dumper.py`'s own existing
content to a suitable existing or new sibling module first, reclaiming
enough headroom before (or alongside) adding the new call site — not
prose alone, verified by the same `file-size` check in the same PR. This
is deliberately not deferred to D3's later `dumper_manifest.py` split:
that module is scoped to the per-TU loop D3 introduces, a different piece
of work than D1's own attachment glue, and waiting for it would leave D1
unable to land on its own at all, contradicting D1's own "ships
independently" property.

**"Every dump" means every dump with at least one resolved input to
fingerprint — a symbols-only/binary-only dump (no `--header`/`-H`, no
manifest; native binaries fall back to this mode by design) attaches no
`profile_fingerprint`, not a fingerprint computed from unused inputs.**
`profile_fingerprint` hashes the resolved compiler/macros/`-I` inputs a
castxml/clang invocation actually used — for a symbols-only dump, no such
invocation ever runs, so those fields describe nothing the snapshot
actually depends on. Attaching a `profile_fingerprint` anyway (computed
from whatever compile-context flags happen to be set on the CLI
invocation, unused or not) would make two snapshots that are genuinely
comparable at the L0/binary evidence tier spuriously mismatch on
compile-context differences that never affected either snapshot — the
opposite failure mode from the one D1/D2 exist to close, this time an
*over*-counting bug instead of an under-counting one.

**But `--public-header`/`--public-header-dir` are still real, active
inputs on a symbols-only dump, even with no `-H`/manifest at all — `dump()`
calls `apply_provenance(snapshot, public_headers, public_header_dirs)`
unconditionally at the end of every dump (`dumper.py:1267-1272`),
regardless of whether an L2 frontend ran, and public-surface filtering
(`--scope-public-headers`, ADR-024) reads the `ScopeOrigin` that call
produces.** Two symbols-only dumps of otherwise-identical DWARF data but
different `--public-header-dir` sets can therefore produce genuinely
different *filtered* diffs — exactly the silent scope drift D1/D2 exist to
catch — yet the blanket "no contract at all for a symbols-only dump" rule
above would leave the gate blind to it, since neither side would carry
anything to compare. `contract`'s two fingerprints are therefore
independently optional, not an all-or-nothing pair: a symbols-only dump
attaches a `contract` with `profile_fingerprint: None` (nothing to
compute — no L2 invocation ran) but a real `scope_fingerprint`, computed
from `public_header_paths`/`public_header_dirs`/the filtering policy
alone, **whenever either is non-empty**; a symbols-only dump with *neither*
`-H` nor `--public-header`/`--public-header-dir` still attaches no
`contract` at all (both fingerprints would be computed from nothing), the
original rule applying correctly to the one case where genuinely no
scope-affecting input was used on either side.

`check_contracts_comparable` (D2) compares each fingerprint
independently rather than treating `contract` as one opaque all-or-nothing
unit: `scope_fingerprint` is compared whenever both sides have one
(regardless of whether either side's `profile_fingerprint` is present),
and `profile_fingerprint` is compared whenever both sides have one. A
symbols-only side with only a `scope_fingerprint` compared against a full
L2 header-AST side (which has both) therefore still gets its scope
checked — the real risk this fix closes — without spuriously hard-failing
on `profile_fingerprint` alone just because one side never ran an L2
frontend at all, which is an ordinary, intentional depth difference (e.g.
`scan --depth binary` against a fuller stored baseline), not scope drift,
and must stay lenient exactly like today's "mixed pair" case (D2) — this
is a refinement of that leniency rule to per-fingerprint granularity, not
a narrowing of it. `contract` staying entirely `None` for a symbols-only
dump with no provenance inputs either is not a gap in D2's leniency rule;
it is that same rule applying correctly to the one case where no
scope-affecting input was used on either side to disagree about in the
first place.

**The whole-snapshot cache is the same bypass by a different route, and it
matters from day one, not just at D6's later cache-key extension.**
`service_dump_cache.cached_run_dump` looks up `snapshot_cache` *before*
calling `run_dump`/`dump()` and returns a cache hit unchanged — so a warm
cache entry written by a pre-this-ADR abicheck (schema 11, no `contract`
computed at all) served after upgrading to a version that implements this
ADR would still come back with `contract=None`, for the same reason a
never-populated `dump()` would: the code path that would have called
`compute_extraction_contract(...)` never runs on a cache hit. D1 therefore
also bumps `snapshot_cache._SNAPSHOT_CACHE_VERSION` (`:48`, currently
`"3"`) in the same change — folded into `_cache_key()` (`:196`) already, so
every pre-this-ADR cache entry misses exactly once and gets rebuilt through
the now-`contract`-populating `dump()`. This is deliberately separate from
D6's later manifest-driven `scope_fingerprint` cache-key work (see D6): that
closes a *different* gap (pre-dump-knowable manifest fields, `contributes_to_abi`/
`required`, that today's filesystem-only cache key can't see); this one
closes "the cache doesn't know `contract` exists yet at all," and cannot
wait for D6's phase without leaving the gate inert for every warm-cache user
in the interim.

**A third, ongoing cache gap — not a one-time migration issue like the
two above — also has to land in this phase: `_cache_key()`'s own hashing
is order-*insensitive*, while D1's fingerprints are explicitly
order-*sensitive*.** `snapshot_cache._cache_key()` (`:159,168`) iterates
`sorted(headers)`/`sorted(includes)` when building the cache key — so
`-I a -I b` and `-I b -I a` hash identically today. That was already a
latent correctness gap independent of this ADR (include-search order
affects real header shadowing/resolution in the underlying compile,
regardless of caching), but D1 makes it acutely consequential: a caller
that reorders `-I`/header flags between two runs would get a cache *hit*
under the sorted key, and `cached_run_dump` returns that cached
`AbiSnapshot` — whose `contract.profile_fingerprint`/`scope_fingerprint`
were computed once, for whichever order happened to populate the cache
entry first — without ever re-running `compute_extraction_contract(...)`
for the new order. The comparability gate would then be working from a
fingerprint that doesn't reflect the actual current invocation, in either
direction: a real reorder-driven profile change could be silently
cache-masked as unchanged, or an immaterial reorder could keep comparing
against a stale fingerprint from a differently-ordered prior run. Fixed
by dropping `sorted(...)` for `headers`/`includes` in `_cache_key()` and
hashing them in caller-supplied order instead — landing in this phase,
not deferred to D6, since D6's cache-key work addresses a different,
narrower gap (a pure profile change with *identical* header content) and
does not by itself make order-sensitive hashing order-preserving.

### D2. Comparability gate — hard-fail before symbol diff, not a RISK finding

New `ProfileMismatchError` / `ScopeMismatchError` (`errors.py`), raised from
a new `comparability.check_contracts_comparable(old, new)` called at the top
of `checker.compare`, before any `diff_*` module runs. Mirrors
`DumpDepthNotSatisfiedError`'s existing shape exactly: a `click.ClickException`
subclass at the CLI boundary (`cli.py`), a plain exception at the
`service.py`/`mcp_server.py` boundary (closing the same gap AGENTS.md's
"Known gaps" section already names for the depth contract — this ADR's gate
must not repeat that CLI-only mistake; D2 lands in `service.py`'s
`ScanRequest`/`compare_snapshots` and `mcp_server.py`'s MCP tools from the
start, not as a follow-up).

**A profile mismatch confined to target triple/pointer width/endianness
must not preempt the existing, more specific platform-identity
detectors — this is a required carve-out, not an edge case to leave
implicit.** `profile_fingerprint` (D1) deliberately includes target
triple, pointer width, and endianness, since they genuinely affect what
the L2 AST frontend parses (a 32- vs. 64-bit `sizeof(long)`, an
`__aarch64__`-gated declaration) — omitting them from the fingerprint
would reintroduce the exact under-counting bug this whole design exists
to close. But `diff_platform.py` already has artifact-backed, dedicated
detectors for exactly this axis — `elf_machine_changed`,
`elf_class_changed`, `elf_endianness_changed`, and the PE/Mach-O
equivalents — computed directly from the binaries' own ELF/PE/Mach-O
headers, independent of any AST extraction, and already classified
`BREAKING` (a real, load-incompatible ABI difference: an x86_64 build
compared against an aarch64 build of "the same" library). Comparing two
binaries for genuinely different target architectures is `profile_fingerprint`'s single most likely mismatch source — and, unlike
every other profile-drift case this ADR is built to catch, it is not an
*unexplained* drift: the diff pipeline already has a specific, correct,
artifact-grounded answer for it. Gating it into a generic
`not_comparable` before `diff_platform.py` ever runs would silently
downgrade a proven, informative `BREAKING` verdict into a strictly less
useful "couldn't tell" result — the opposite of this ADR's purpose. The
gate therefore inspects *which* resolved fields differ, not only whether
the overall hash differs: `check_contracts_comparable` computes the
mismatch at field granularity (the same resolved fields
`ExtractionContract` already stores so a report can show *what* differs,
not just that the hashes don't match — D1). Any *other* differing field
(compiler family, macros, `-I` content, language standard) still
hard-fails exactly as before, even if a target difference happens to
co-occur with it — this carve-out is scoped to the platform-identity
fields alone, not a general loosening of the gate.

**"Only the compile-context target differs" is necessary but not
sufficient — the carve-out must also confirm the binaries themselves
genuinely differ on that axis, or a misconfigured extraction slips
through with nothing left to catch it.** A profile-only target mismatch
can happen without the compared binaries actually targeting different
architectures at all — a cross-compiler flag or `--gcc-prefix` set for
only one side while both `.so`/`.dll` files are genuinely, say, `x86_64`
is exactly this: `profile_fingerprint`'s target component differs (the
*extraction* used a different target), but `diff_platform.py` has no
`elf_machine_changed`/`elf_class_changed`/`elf_endianness_changed`
finding to emit, because the binaries' own metadata never differed in the
first place. Skipping the gate unconditionally in this case would let a
misconfigured, genuinely non-comparable extraction proceed as an ordinary
diff — any AST difference the wrong target introduced (a
target-conditional `#ifdef` branch, a different `sizeof`) would surface
as an apparently real reported `Change`, with no detector output to flag
that anything was wrong, the opposite of "an artifact-backed detector
already has a correct answer for this." The carve-out therefore requires
*both* conditions, not just the profile-fingerprint one: (1) the only
differing `profile_fingerprint` fields are target triple/pointer
width/endianness, **and** (2) the old/new snapshots' own binary-derived
platform metadata (the same ELF/PE/Mach-O header fields
`elf_machine_changed` et al. already read) shows a genuine difference on
that same axis. Only when both hold does the gate skip raising and let
`compare()` proceed to `diff_platform.py`'s detectors; when (1) holds but
(2) doesn't — the compile context's target differs but the real binaries
agree — the gate still raises `ProfileMismatchError`, correctly treating
it as an extraction misconfiguration rather than a legitimate
cross-architecture comparison.

**A fourth surface reaches `checker.compare` besides the three named
above: `cli_compare_release.py`'s directory/package fan-out, and it needs
its own explicit fix, not just inherited behavior.**
`_compare_one_library` (`cli_compare_release.py:180-269`) wraps its entire
per-library flow in `except (click.ClickException, click.UsageError):` /
`except Exception:`, both returning `{"verdict": "ERROR", ...}` —
documented at `:1142` as flooring the release's exit code at 4 "regardless
of severity settings." `ProfileMismatchError`/`ScopeMismatchError` are
plain exceptions (not `click.ClickException`), so today's broad
`except Exception` would swallow them into the exact same `"ERROR"`/exit-4
bucket as a genuine crash — meaning one incomparable library inside a
release comparison would silently report as the *worst possible*
classification (an ABI break) instead of `not_comparable`, precisely
inverting this ADR's purpose on its one multi-library entry point.
`_compare_one_library` therefore gains a dedicated
`except (ProfileMismatchError, ScopeMismatchError) as exc:` branch, ordered
before the generic `except Exception`, returning a distinct
`{"verdict": "not_comparable", "reason": ...}` entry; the release-level
aggregator and exit-code computation (`docs/reference/exit-codes.md`'s
multi-library section) are extended to recognize that verdict value the
same way the single-library path does, rather than folding it into
`"ERROR"`.

**This `"not_comparable"` string entry is a different JSON document from
the canonical `verdict: null` shape, by design, not a second incompatible
contract for the same shape.** `_compare_one_library`'s return dict feeds
`summary.json`'s top-level `verdict` (`worst_verdict`) and its nested
`libraries` array — both already string-only fields today (the existing
`"ERROR"` case is exactly this: a non-`Verdict`-enum sentinel string, the
same class `"not_comparable"` joins). That JSON document is not, and never
was, governed by `compare_report.schema.json` — it is
`cli_compare_release.py`'s own long-standing summary shape, extended in
its own established idiom. Separately, when `--output-dir` is set,
`_compare_one_library`'s success path *also* writes a full per-library
report (`{stem}.json`) via `to_json(result)` — that file **is** governed
by `compare_report.schema.json`, and for a `not_comparable` library it
must use the canonical `verdict: null` + `reason` shape, assembled the
same way every other front-end's exception handler assembles it (there is
no `DiffResult` to call `to_json` on). The two documents disagreeing in
shape is not an inconsistency to fix; each already followed its own
distinct schema before this ADR existed, and each keeps doing so now —
`aggregate.py`'s not-comparable detection (which reads whichever of these
two document shapes it was actually pointed at) must check both: `verdict
is None` for a canonical `compare_report.schema.json` document, or
`verdict == "not_comparable"` for a release `summary.json`/per-library
entry.

**`cli_compare_release.py` also needs `diagnostic_comparison` itself
threaded, not just the exception branch above — it does not go through
`CompareRequest`/`run_compare_request`, so fixing that chokepoint doesn't
reach it.** `_compare_one_library` calls `_run_compare_pair`
(`cli_compare_release.py:91-141`), whose own docstring says it "routes
through the single Tier-2 chokepoint (`service.run_compare`, ADR-037
D1)" — the legacy keyword shim, a different function from
`run_compare_request`. `_run_compare_pair`'s own fixed, explicit signature
has no `diagnostic_comparison` slot, and its `service.run_compare(...)`
call only forwards parameters it already names, so giving
`service.run_compare` the parameter (above) is not sufficient on its own.
`_run_compare_pair` and `_compare_one_library` each gain their own
`diagnostic_comparison: bool = False` parameter, threaded into
`_run_compare_pair`'s `service.run_compare(...)` call — the same
multi-layer threading this ADR has already had to apply more than once
elsewhere.

**A fifth surface calls `checker.compare` directly, with its own,
independent exit-code contract that this ADR must not silently
break: `abicheck/compat/cli.py`'s ABICC-compatible `compat check`
command** (`from ..checker import compare`, called around `:967`).
Because `compare()` there is the exact same function D2's gate wraps,
`ProfileMismatchError`/`ScopeMismatchError` propagate out of that call —
but, verified against the actual call site, **not into any existing
classifier**: unlike `check`'s other operations (descriptor parsing,
logging setup, dump, report writing), each individually wrapped in its own
narrow `except ... : _compat_fail(...)` block, the bare `result =
compare(old_snap, new_snap, ...)` call has no surrounding `try` at all
today. Left alone, the new exceptions would propagate uncaught out of the
Click command entirely — not into `_classify_compat_error_exit_code`'s
generic `10` fallback (which would at least be a *wrong but classified*
outcome), but past classification altogether, as an unhandled traceback.
This phase adds the missing `try`/`except (ProfileMismatchError,
ScopeMismatchError) as exc: _compat_fail("comparing snapshots", exc)`
around that call site — a real call-site change, not merely a classifier
update — so the gap is not "the gate doesn't fire," it's that nothing
today would catch the resulting exception at all, let alone classify it
into a *deliberate* compat-mode outcome. `compat/CLAUDE.md` documents a
closed exit-code contract (`0`
compatible, `1` `BREAKING`, `2` `API_BREAK`, `3`–`11` errors via
`_classify_compat_error_exit_code` in `compat/_errors.py`) that "requires
a CHANGELOG note and downstream coordination" to change — this ADR cannot
reuse `16` here (that would silently break the documented ABICC-mimicking
numbering, which has nothing to do with the native `compare` command's own
scheme) nor let the exception fall through to `_classify_compat_error_exit_code`'s
generic `10` fallback (an existing, different meaning — "generic
internal/tool error" — that a `not_comparable` result must not be
conflated with, for the same reason it must not be folded into `"ERROR"`
on the release path). `_classify_compat_error_exit_code` gains an explicit
`isinstance(exc, (ProfileMismatchError, ScopeMismatchError))` check —
mirroring its existing `KeyboardInterrupt` special case — returning **`9`**,
the one integer the current 3–11 range documents no meaning for
(3/4/5/6/7/8/10/11 are all taken; 9 is the sole gap), with `compat/CLAUDE.md`'s
exit-code table and a changelog fragment updated in the same phase per that
file's own stated policy.

**A sixth surface calls `compare_snapshots` through a different code path
than any of the previous five, with yet another independent exit-code
contract: `abicheck scan --against`.** `cli_scan_baseline.py`'s
`_run_baseline_compare` (called from `scan_engine.run_scan_core` around
`:852`) calls `service.compare_snapshots` — which, being a thin wrapper
over `checker.compare` with no exception handling of its own, lets
`ProfileMismatchError`/`ScopeMismatchError` through untouched, exactly as
D2 intends at the `service.py` boundary. `scan`'s own CLI command
(`cli_scan.py`'s `scan_cmd`) has an independent, narrower exit-code
contract (`0`/`2`/`4`/`5`/`64`, `docs/reference/exit-codes.md`), but the
catch does **not** belong in `scan_cmd`'s own `try`/`except` (which today
only catches `_BudgetOverflow`/`_EvidenceContractError`): `scan_cmd`'s
outer `try` only has a finished `ScanCoreResult` to work with once
`run_scan_core` returns, and every existing exception branch there
(`_BudgetOverflow`, `_EvidenceContractError`) bypasses `scan_cmd`'s own
`_emit_scan_report` call — the one function that renders JSON/text, writes
the `-o` file, and exits on a nonzero code — and exits/raises bare
instead. A `scan_cmd`-level catch would inherit that same bypass: `scan
--against ... --format json -o report.json` on a mismatched pair would
exit `6` but write no report file at all, even though `scan`'s own
`SCAN_SCHEMA_VERSION` bump (D2, above) is framed as versioning exactly
this envelope's new verdict. The catch instead belongs inside
`run_scan_core` itself, around the `_run_baseline_compare` call — where
`ScanOutcome`'s other fields (mode, resolved method, depth, risk,
coverage, etc.) are already locally resolved and about to feed the
function's own `ScanOutcome(...)` construction a few lines below. A new
`except (ProfileMismatchError, ScopeMismatchError) as exc:` branch there
sets `verdict="NOT_COMPARABLE"`/`exit_code=6`/a reason field, skips the
crosscheck-severity-promotion step (a `NOT_COMPARABLE` gate result is not
something a promoted finding should be able to soften), and falls through
to the normal `ScanOutcome` construction — so `scan_cmd`'s existing,
unconditional `_emit_scan_report` call renders and writes it exactly like
any other scan result, needing no `scan_cmd`-level change at all. Since
`run_scan_core`'s own docstring already calls it "the one body the CLI,
`service.run_scan`, and the MCP scan tool share," this single fix also
reaches `service_scan.run_scan` (which already builds its typed
`ScanResult` from `core.outcome` without re-running anything) and
`run_scan_subprocess`'s worker/`mcp_server.abi_scan` (which no longer has
an exception to swallow into a generic `RuntimeError` at all) without a
separate change at either. Exit **`6`** is the next integer after
`scan`'s own highest documented code (`5`, the budget-overflow exit),
distinct from both native `compare`'s `16` and `compat check`'s `9` since
all three commands maintain independent, non-overlapping exit-code
schemes. `docs/reference/exit-codes.md`'s `scan` table gains this row.

**A seventh surface imports `checker.compare` directly and swallows every
exception, including these new ones, into an undifferentiated `None`:
`stack_checker.py`'s `_run_abi_diff`, driving `abicheck deps compare`.**
`stack_checker.py:32` imports `compare` from `checker` (not through
`service.compare_snapshots`), and `_run_abi_diff` (`:396-410`) wraps its
whole body — the `dump()` calls *and* the `compare()` call — in one broad
`except Exception as exc: log.warning(...); return None`. A
`ProfileMismatchError`/`ScopeMismatchError` from a changed dependency DSO
would be swallowed into that same `None`, indistinguishable from the
"file unreadable" case a few lines above (`:363-364`, also `abi_diff=None`)
or a genuine crash — the per-library `StackChange` this produces carries no
`not_comparable` reason at all, just a silent absence of a diff, which
`cli_stack.py`'s `deps compare` reporters and exit-code contract (`0`/`1`
`WARN`/`4` `FAIL`/`64`, `docs/reference/exit-codes.md`) then read no
differently than "nothing to report for this library." `StackChange` gains
a `not_comparable_reason: str | None = None` field (additive, alongside its
existing `abi_diff: DiffResult | None`); `_run_abi_diff`'s caller (the loop
building `StackChange` entries) gains a dedicated `except
(ProfileMismatchError, ScopeMismatchError) as exc:` branch around the
`_run_abi_diff(...)` call, ordered so it is never reached by
`_run_abi_diff`'s own broad `except Exception` first — `_run_abi_diff`
itself re-raises `ProfileMismatchError`/`ScopeMismatchError` rather than
swallowing them, since only its caller can attach the result to a
`StackChange` — setting `not_comparable_reason` instead of leaving
`abi_diff` as an unexplained `None`. `deps compare` gains its own exit code
for "at least one dependency was not_comparable": **`5`**, the next integer
after the currently documented ceiling (`4`, `FAIL`) in that command's own
`0`/`1`/`4`/`64` scheme — distinct from `scan`'s `6`, `compat check`'s `9`,
and native `compare`'s `16`, continuing the same "each command keeps its
own disjoint scheme" rule the previous three surfaces already established,
never folded into the existing `FAIL`/`4` the way a swallowed exception
would today.

On the reporting surface (`reporter.py`,
`sarif.py`, `junit_report.py`), a `not_comparable` result is a distinct
top-level state — `verdict: null`, a `reason` object naming the mismatched
fingerprint field(s) — never coerced into `COMPATIBLE`/`BREAKING`'s existing
enum values. A `--diagnostic-comparison` opt-in flag (default off) downgrades
the hard-fail to a tentative diff, the whole result stamped `assurance:
"none"` for exploratory use — never the default, and never silent.

**This has to be a parameter *into* `compare()`, not a CLI-level catch
around it — a post-hoc recovery is structurally impossible here.** The
gate runs "at the top of `checker.compare`, before any `diff_*` module
runs" (above): once it raises, no `diff_*` module has executed and no
`DiffResult` — tentative or otherwise — exists for any caller to recover.
A CLI `except (ProfileMismatchError, ScopeMismatchError)` wrapped around
`compare()`, the way every other surface in this ADR handles the gate,
would have nothing left to downgrade; it can only report the failure, not
resurrect a diff that never ran. `--diagnostic-comparison` therefore
threads all the way to the gate check itself: `checker.compare(...,
diagnostic_comparison: bool = False)` passes the flag to
`comparability.check_contracts_comparable(old, new,
diagnostic=diagnostic_comparison)`, which — only when set — returns a
mismatch descriptor instead of raising, letting `compare()` proceed through
the normal `diff_*` pipeline and stamp `assurance: "none"` on the resulting
`DiffResult` afterward. `service.compare_snapshots` gains the same
`diagnostic_comparison` keyword, threaded from `compare()`.

**`compare_snapshots` is not the front-end chokepoint, though — `api_types.CompareRequest`
is, and it needs the field too, or the documented front-ends can never reach
it.** `CompareRequest` (`api_types.py:125`) is, by its own docstring, "the
single input to `run_compare`" that "every front-end (CLI, MCP,
`compare-release` fan-out, `appcompat`)" assembles and hands to
`service.run_compare_request` — the actual ADR-037 D1/D2 classification
chokepoint, one level above `compare_snapshots`. **Neither the "appcompat"
nor the "`compare-release` fan-out" half of that docstring claim holds up
against the actual code, the same kind of docstring/reality gap already
caught once below for `mcp_server`:** `appcompat.py`'s
`check_appcompat`/`check_plugin_host_contract` call `compare_snapshots(...)`
directly (see the dedicated bullet further below), and
`cli_compare_release.py`'s `_compare_one_library` → `_run_compare_pair`
calls `service.run_compare` — the *legacy keyword shim*, whose own
docstring says it "routes through the single Tier-2 chokepoint
(`service.run_compare`, ADR-037 D1)," not `run_compare_request` — so
"every documented front-end goes through `CompareRequest`" is already not
literally true today for either. Both get their own dedicated
`diagnostic_comparison` threading below (`appcompat.py`'s own bullet;
`cli_compare_release.py`'s own bullet) rather than inheriting reachability
from this fix — a test asserting `run_compare_request` accepts
`diagnostic_comparison` proves nothing about either path. The
CLI/MCP-facing front-ends this paragraph is really about (the ones a
`--diagnostic-comparison` flag or equivalent API parameter needs to
reach) are the ones this fix threads it through. `run_compare_request`
calls `compare_snapshots(old, new, suppression=..., policy=..., ...,
env_matrix=...)` today with a fixed keyword list that has no slot for this
flag; adding `diagnostic_comparison` only to `compare_snapshots` itself
would be unreachable from `CompareRequest`-based front-ends specifically.
`CompareRequest` therefore gains
`diagnostic_comparison: bool = False`, and `run_compare_request` passes
`diagnostic_comparison=request.diagnostic_comparison` into its
`compare_snapshots(...)` call. The legacy keyword-argument shim
`run_compare` (`service.py:1757`, "existing callers keep working while the
typed request is the real chokepoint") gains the same parameter too,
appended after every pre-existing one — matching the precedent already set
for `debuginfod_url`, so a positional caller's existing argument bindings
don't shift.

**`mcp_server.abi_compare` is itself a direct `compare_snapshots` caller —
the one this ADR previously (wrongly) said nothing in the codebase makes —
and exposing the parameter there is not enough on its own.** Verified
against the actual code: `abi_compare`'s inner `_do_compare` calls
`compare_snapshots(...)` directly, bypassing `CompareRequest`/
`run_compare_request` entirely; its result is awaited via
`future.result(timeout=MCP_TIMEOUT)` under a narrow `except
_futures.TimeoutError`, with a broader `except Exception as exc: ...
{"status": "error", ...}` catching everything else, including
`ProfileMismatchError`/`ScopeMismatchError` today — collapsing a
deliberate not-comparable result into the same generic error shape as any
other tool failure. Adding `diagnostic_comparison` as an input parameter
lets a caller opt into the tentative diff, but the *default* hard-fail
path still needs its own dedicated `except (ProfileMismatchError,
ScopeMismatchError)` branch in `abi_compare`, rendering a structured
`{"status": "not_comparable", "reason": ...}` result distinct from
`{"status": "error"}` — mirroring the CLI/service layers' `verdict: null`
distinction, not merely exposing the escape-hatch flag.

**`appcompat.py` is a third, independent bypass of the same shape, not
covered by fixing `CompareRequest`/`run_compare_request` or `mcp_server.py`
alone.** Verified against the actual code: `check_appcompat` and
`check_plugin_host_contract` each call `compare_snapshots(...)` directly,
with no surrounding `try` and no `CompareRequest` anywhere in either call
path — and unlike `mcp_server.abi_compare`, there is no natural place to
put a structured not-comparable result: both `AppCompatResult` and
`PluginHostContractResult` carry `full_diff: DiffResult | None`, which has
nothing to hold when the gate raises *before* any `DiffResult` exists.
Rather than inventing a new outcome field on either dataclass for this
phase, both functions gain the `diagnostic_comparison: bool = False`
opt-in (forwarded into their own `compare_snapshots(...)` calls, the same
as `run_compare_request`'s), and letting the mismatch exception propagate
uncaught remains each function's *documented default* — made explicit in
their docstrings rather than left as an unstated gap, since these two are
public, directly user-callable Python API entry points, not internal
helpers a wrapper could quietly retrofit around later.

**`abicheck aggregate` is a consumer of these reports, not just a producer
of new ones, and it has its own blind spot D2 must close.**
`aggregate.py`'s `parse_report_verdict` returns `None` whenever the
`verdict` field isn't a string (`:589-596`) — which is exactly what
`verdict: null` is by design, but it is *also* what a missing or corrupt
report produces, and `aggregate.py` has no way today to tell these apart:
both collapse into the same `compatibility_verdict=None`/"unavailable"
`TargetReport` state. In **discovered-only** mode specifically,
`coverage_blocking` is unconditionally `False` (`and not
self.discovered_only`, `:406-410`) and an unavailable target's `gate` is
`None`, so it contributes nothing to `exit_code()`'s `max(...)` — a
`not_comparable` target can silently reduce to exit `0`, the exact
"missing evidence reads as safe" failure this whole ADR exists to prevent,
resurfacing at the one consumer surface this design hadn't yet reached.
`aggregate.py` gains a way to distinguish a deliberate `not_comparable`
report from a genuinely missing/corrupt one (its `reason` object is
present only for the former), and treats it as an unconditionally blocking
state — dominating `exit_code()` regardless of `discovered_only`, matching
the same "a `not_comparable` result must never read as safe" rule D2
already applies to the native `compare`/`compat check`/`scan`/`deps
compare` schemes and the release-level rollup's rank-6 precedence.
**Pinned to `1`, not a new number:** `docs/reference/exit-codes.md`'s
`aggregate` table already documents `1` as covering both a coverage gap
and "a non-verdict per-report failure" (its own stated example being
`scan`'s budget-overflow `5` folding in there) — `not_comparable` is
exactly that same class of failure, so it joins the existing bucket rather
than reserving a new disjoint code the way each *producer* command
(`compare`/`scan`/`deps compare`) did for its own scheme; `aggregate`
never invents a code per producer, it has one shared "not a clean verdict"
bucket. Both the `aggregate` table and the `## Summary table` cross-command
matrix in `docs/reference/exit-codes.md` gain the corresponding row.

**The GitHub Action wrapper is another consumer with the same blind spot,
one layer further from the Python package.** `action/run.sh` maps each
command's known exit codes to a `VERDICT` string via `case` statements with
an unconditional `*) VERDICT="ERROR"` fallback for anything unrecognized —
native `compare`'s new `16`, `scan`'s new `6`, and `deps compare`'s new `5`
all fall through it today, since the script predates this ADR. Worse,
`_maybe_post_pr_comment` unconditionally skips posting when `VERDICT ==
"ERROR"` — so a deliberate `not_comparable` result would both misreport as
a generic internal error *and* silently suppress the one PR comment meant
to surface it, the combination this ADR most needs to avoid on its most
visible first-party consumer. `action/run.sh` gains a matching `VERDICT`
value (e.g. `NOT_COMPARABLE`) for each new code, and
`_maybe_post_pr_comment`'s `ERROR`-only skip is joined by an explicit
carve-out that still posts for `NOT_COMPARABLE` — this result deserves the
comment more than an ordinary pass, not less.

**The bash-level `VERDICT != "ERROR"` carve-out is not what actually
decides whether a comment gets posted — the real suppression point is one
layer deeper, in the Python renderer `_maybe_post_pr_comment` delegates
to, and it was never taught about `not_comparable` at all.** Verified
against the actual code: `_maybe_post_pr_comment` (`action/run.sh:897`)
invokes `python3 -m abicheck.cli_pr_comment` (`:968`), whose
`build_model`/`should_post` (`abicheck/pr_comment.py:628,643`) decide the
actual outcome. `should_post(model, on="changes")` — the Action's own
default (`INPUT_PR_COMMENT_ON:-changes`) — returns `True` only when
`model.total_changes > 0` or `removed_libraries`/`added_libraries` is
non-empty; a `not_comparable` report has none of these (the gate raised
before any `Change` was ever produced), so even though the bash-level
carve-out above correctly avoids an early return, `cli_pr_comment` itself
would still silently decide there is "nothing to report" and post no
comment at all — the exact suppression this whole fix exists to prevent,
just one call deeper. Closing this needs three changes in
`abicheck/pr_comment.py`, not just the bash-level one: (1) `CommentModel`
gains a `not_comparable_reason: str | None = None` field; (2)
`build_model` (or the mode-specific builder it dispatches to,
`_from_compare`/`_from_release`) detects the canonical `verdict: null`
shape (compare mode) or a release library entry's string
`verdict == "not_comparable"` (the pre-existing `summary.json` idiom,
D2) and populates it from the report's `reason`; (3) `should_post` checks
`model.not_comparable_reason is not None` **before** the `on == "changes"`
bucket-count logic and returns `True` regardless of bucket counts (only
`on == "never"` still suppresses it) — mirroring "this result deserves the
comment more than an ordinary pass, not less" at the layer that actually
enforces it. The rendered comment body also needs its own distinct
not-comparable headline (a `_header`-level case checked before the
existing `scoped_verdict`/`removed_libraries`/bucket-count branches,
since a not-comparable result means none of that bucket data is
trustworthy) rather than falling through to whatever an empty
`breaking`/`review`/`safe` set would otherwise render. **Mapping the `VERDICT`
string is necessary but not sufficient — `run.sh`'s separate final
exit-code section has no `NOT_COMPARABLE` branch of its own, verified
against the actual code (`:1074-1145`): it starts `FINAL_EXIT=0` and only
ever sets it to `1` inside `ERROR`, or inside per-mode branches gated by
their own `fail-on-*` inputs.** Adding only the `VERDICT` mapping leaves
every mode's final-exit branch falling through unmatched, `FINAL_EXIT`
staying `0` — a `compare`/`scan`/`deps-compare` step whose CLI exited
`16`/`6`/`5` would still report a green composite Action step. This is
fixed with its own unconditional check, alongside (not gated by, matching
how `ERROR` itself is treated) the existing top-of-section `ERROR` check
— whether a comparison could be attempted at all is not something a
`fail-on-breaking`/`fail-on-api-break` toggle should be able to waive.

**`assurance` is a single field on `DiffResult` (alongside
`contract_coverage`), not a per-`Change` field.** A forced diagnostic
comparison is uniformly tentative — the contract gate failed for the pair
as a whole, before any `diff_*` module ran, so every finding the tentative
diff produces shares the identical, single reduced-assurance reason; there
is no per-finding split to encode. `checker_types.Change` gains no new
field for this; `checker_types.py` gains `assurance: str | None = None`
on `DiffResult` itself, set to `"none"` only on the `--diagnostic-comparison`
path (`None` — i.e. absent — on every ordinary comparison, matching
`contract_coverage`'s own default).

**`html_report.py` is a reporting surface too, not an omission this ADR can
leave implicit — and it is not the only one that needs this treatment.**
AGENTS.md's own module map lists it alongside
`reporter.py`/`sarif.py`/`junit_report.py` under "Reporting," and
`service_render.py`'s format dispatch (`:36-132`) routes `--format html` to
`generate_html_report(result: DiffResult, ...)` exactly like the other three
route to their renderers. **Verified against the actual code: `render_output`
has five format branches requiring a real `DiffResult` — `sarif`
(`to_sarif_str`), `html` (`generate_html_report`), `junit`
(`to_junit_xml`), `review` (`to_review_digest`), and the default
`markdown` (`to_markdown`) — not `html` alone. **`--stat` is a sixth,
cross-cutting path with the identical requirement, checked *before* any
of those five format branches — `render_output`'s `if stat and fmt !=
"junit":` guard calls `to_stat_json(result, ...)` for `--format json
--stat` or `to_stat(result, ...)` for every other `--stat` combination,
both requiring the same real `DiffResult` the five format renderers do.**
Two distinct gaps follow, for every one of these six paths, not
just HTML: for the hard-gate `not_comparable` case, no
`DiffResult` exists at all (the gate raises before any diff runs), so
`service_render.render_output` must not attempt to call any of these six
renderers (or the `--stat` summarizer) on that path — the front-end's exception handler renders (or
declines to render) each requested format directly, the same way it
assembles `verdict: null` JSON, rather than any of them growing an
optional-`DiffResult` parameter none was designed to accept. Two of these
five format branches carry a structured, externally-consumed schema and need a defined
not-comparable shape, not just "skip the call": **SARIF** represents it as
one `run` with `invocations[0].executionSuccessful: false` and a
`toolExecutionNotifications` entry carrying the reason — SARIF's own
mechanism for "the tool didn't complete analysis," so a downstream SARIF
consumer (e.g. GitHub Code Scanning) can't misread an empty `results`
array as a clean pass; **JUnit** represents it as one `<testsuite>` with a
single `<testcase>` wrapping an `<error message="...">`, not a
`<failure>` — JUnit's own convention for "the test itself couldn't run,"
distinct from an ordinary reported ABI break. Markdown/review, being
plain human-readable text with no schema to satisfy, get a simple "NOT
COMPARABLE: `<reason>`" line. For the mixed-pair `contract_coverage` case, a real
`DiffResult` does exist, so `generate_html_report` needs to surface
`contract_coverage` in its headline cards the same way the JSON/Markdown/SARIF/JUnit
reporters do — silently dropping it there would make the HTML report the one
output format that can't tell a reader the comparison ran on unequal
evidence.

**`verdict: null` is JSON-output shape, not a change to `checker_types.DiffResult`'s
own typing — this needs to be explicit, or an implementer reasonably reads
D2 as requiring `DiffResult.verdict: Verdict | None`.** `DiffResult`
(`checker_types.py:234,239`, `verdict: Verdict = Verdict.NO_CHANGE`) is
never constructed for a `ProfileMismatchError`/`ScopeMismatchError` case at
all — the gate raises *before* any `diff_*` module runs, so there is no
comparison to build a `DiffResult` from. `verdict: null` in JSON is
assembled fresh by each front-end's own exception-handling path (`cli.py`,
`service.py`, `mcp_server.py`, `cli_compare_release.py`'s dict literal,
`compat/cli.py`) when it catches the exception — `DiffResult.verdict`
itself stays exactly as typed today, `Verdict`, never `Verdict | None`, so
no downstream consumer that already assumes a concrete `Verdict` needs to
change. `contract_coverage` (the mixed-pair annotation) is a genuinely
different case, and does need a real field: unlike the hard-fail path, a
mixed pair *does* produce an ordinary `DiffResult` — `checker_types.py`
gains `contract_coverage: str | None = None` on `DiffResult` itself
(additive, mirroring how `assurance` already needs the same treatment for
`--diagnostic-comparison`'s tentative-diff findings), and `checker.py`'s
`compare()` sets it when exactly one side carries a `contract`.
`verdict: null` is a **published contract change**, not just an internal
one: `abicheck/schemas/compare_report.schema.json` currently requires
`verdict` and restricts it to a fixed string enum with no `null` member, and
`tests/test_report_schema.py` validates emitted reports against exactly
that file — both must change in the same phase that starts emitting
`not_comparable`, or JSON output goes invalid (or the published schema goes
stale) the moment the gate first fires. This includes the schema's own
version metadata, not just its `verdict` constraint:
`abicheck/schemas/__init__.py`'s `REPORT_SCHEMA_VERSION` (currently
`"2.12"`, a documented `MAJOR.MINOR` policy — every JSON report emits it as
`report_schema_version`) is bumped in the same change, and the published
mirror `docs/reference/schemas/v1/compare_report.schema.json` is regenerated via the
existing `scripts/publish_schemas.py` so it stays byte-identical to the
packaged schema — `tests/test_report_schema.py`'s
`test_docs_mirror_matches_packaged_schema` already asserts that identity
and fails the build otherwise. **The exit code is part of this
same contract and must be pinned explicitly, not left implicit.**
`docs/reference/exit-codes.md` documents two co-existing `compare` exit
schemes (legacy: 0/2/4; severity-aware, with any `--severity-*` flag:
0/1/2/4) where `0` means *compatible* in both — a `not_comparable` result
must never exit `0` in either scheme, or the exact failure mode this ADR
exists to prevent (missing evidence reading as "safe") reappears one layer
down, at the process-exit boundary instead of the JSON `verdict` field. D2
reserves exit code **`16`** for `not_comparable` — pinned, not left as "a
new code TBD" — in **both** single-library schemes identically (legacy
and severity-aware alike; `not_comparable` fires before any severity
classification runs, so it is orthogonal to the flag that distinguishes
the two schemes), continuing the doubling pattern the codebase already
uses across `compare`'s exit-code space one step further. **Not `8`**: an
earlier draft of this decision picked `8` by checking only the two
single-library tables (which top out at `4`) and missed that `compare`'s
*release* (directory/package) table — a separate, already-published
scheme — already assigns `8` to `--fail-on-removed-library`
(`docs/reference/exit-codes.md:134-139`). Reusing `8` would have either
silently clobbered the removed-library signal or left release-level CI
unable to tell the two states apart; `16` is unused across *all three*
tables (both single-library schemes and the release table), so it is
documented as its own new row in all three, not folded into any existing
scheme's numbering. (`compat`'s separate 3–11 error range is a different
command's own codespace, per `docs/reference/exit-codes.md`'s per-command
split, and does not constrain `compare`'s either way.)

**Release-level (directory/package) aggregation needs its own explicit
precedence against *two* existing mechanisms, not one.** `cli_compare_release.py`'s
`_RELEASE_VERDICT_ORDER` (`cli_compare_release_helpers.py:45`) already
ranks per-library verdicts for the "worst verdict wins" release rollup —
`NO_CHANGE` < `COMPATIBLE` < `COMPATIBLE_WITH_RISK` < `API_BREAK` <
`BREAKING` < `ERROR` (rank 5, currently the ceiling). `not_comparable`
gets its own rank **above** `ERROR` (rank 6): a `not_comparable` result is
a definitive, correctly-diagnosed outcome (this ADR's whole point), not a
crash, but it carries strictly less trustworthy information about the
library than even an `ERROR` entry's partial context — so for the purpose
of picking one release-level exit code, a `not_comparable` library
dominates every other outcome in the same release, including a genuine
`ERROR`. This closes the release fan-out gap directly (see below): once
`not_comparable` is a real rank in this ordering, a mixed release
(one `not_comparable` library, one `BREAKING`, N `COMPATIBLE`) reports and
exits as `not_comparable` overall, not silently as `BREAKING` or folded
into a generic `ERROR`. It must also dominate the **separate**
`--fail-on-removed-library` mechanism (exit `8`), which today has its own
scheme-dependent precedence against `ERROR`/`2`/`4` — unlike that existing
rule, `not_comparable`'s precedence over removed-library exit `8` is
**unconditional in both schemes**: a `not_comparable` result means the
comparison couldn't establish what actually changed at all, so it cannot
be trusted to have correctly detected a removal either — an apparent
"library removed" reading from an incomparable pair is exactly the kind
of unproven inference this ADR exists to block, not a real removal
finding entitled to its own exit code.

**Mixed pairs (one side lacks a given fingerprint, the other has it) never
hard-fail on that fingerprint — this is unambiguous, not left to
implementer discretion, and applies per-fingerprint (D1's independently-
optional `profile_fingerprint`/`scope_fingerprint`), not only to a
side missing `contract` entirely.** The backward-compatibility promise
("a snapshot from before this ADR compares exactly as it does today") is
not a soft goal to reconcile with the gate; a side's *absence* of a given
fingerprint's evidence — whether because it carries no `contract` at all,
or because it carries a `contract` with that one field unset (D1's
symbols-only-with-provenance-inputs case) — is exactly the "missing
evidence must never manufacture a block" situation ADR-028 D3's authority
rule already covers, extended here to the comparability contract instead
of symbol facts. `check_contracts_comparable` therefore only ever raises
`ProfileMismatchError` when **both** sides have a non-`None`
`profile_fingerprint` and it mismatches, and only ever raises
`ScopeMismatchError` when **both** sides have a non-`None`
`scope_fingerprint` and it mismatches — a side missing one fingerprint (or
`contract` itself) takes the exact same lenient code path for *that*
fingerprint as a pair where neither side carries one, so comparing a
newly-produced snapshot against a pre-ADR baseline (the common "upgrade
abicheck, keep the stored CI baseline" workflow), or a symbols-only side
against a full-header-AST side, never regresses into an unexpected
`not_comparable` result on the fingerprint that side never had to begin
with. `UNKNOWN_PROFILE` is **not** a `not_comparable`
reason and never blocks: it is a non-authoritative annotation on
an otherwise-ordinary verdict, surfaced whenever at least one side is
missing a fingerprint the other side has, to tell the
reader "this comparison ran without being able to check profile/scope
drift on one side," without withholding the verdict itself.

**`UNKNOWN_PROFILE` is report-level metadata, not a `ChangeKind`/`Change`
finding at all — this went through two wrong designs before landing here,
worth recording so it isn't rediscovered.** The first attempt classified it
`RISK_KINDS`, matching `SOURCE_FACT_COVERAGE_INCOMPLETE`
(`checker_policy.py:618`)'s shape; that broke under
`--severity-potential-breaking=error`/`--severity-preset strict`, which
promotes any `RISK_KINDS` finding to a build failure (exit 2) — turning
every comparison against a pre-this-ADR baseline into a mass,
abicheck-version-triggered CI failure the instant a strict-severity team
upgrades, exactly the "upgrading abicheck breaks an unrelated, unchanged
pipeline" regression the backward-compatibility promise above exists to
rule out. The second attempt reclassified it `COMPATIBLE_KINDS`'s
`QUALITY_KINDS` subset instead, reasoning that `SOURCE_FACT_COVERAGE_INCOMPLETE`'s
`RISK_KINDS` tier is justified by reporting genuine *per-comparison*
evidence uncertainty (a fact family that failed or came back partial *this
run*) — a "fair game to fail strict CI on" property `UNKNOWN_PROFILE`
doesn't share, since it fires purely from being compared against a
pre-ADR baseline, a one-time rollout artifact untied to any real change.
That reclassification only relocated the same collision:
`--severity-quality-issues=error`/`--severity-preset strict` promotes
`QUALITY_KINDS` findings too (exit 1, "quality-only error") — proving the
underlying problem was never "which `ChangeKind` category," it's that
**every** category is reachable by *some* `--severity-*` flag, by design
(that's the whole point of severity gating existing). No `ChangeKind`
classification can be permanently severity-immune. `UNKNOWN_PROFILE`
therefore isn't one: it's a new field on the comparison result (alongside
the existing `assurance` field D2 already introduced for
`--diagnostic-comparison`) — e.g. `contract_coverage: "partial"` — set
whenever exactly one side carries a `contract`. It never enters the
`changes`/findings list any `--severity-*` flag scans, so it is
structurally, not just by convention, unreachable by severity promotion —
true under every current and future severity flag, not merely the ones
checked so far. `reporter.py`/`sarif.py`/`junit_report.py` surface it the
same way they already surface `assurance` — a plain report field, not a
finding.

**Additive-only header-set carve-out (found live, PR #641: the pvxs
full-version-matrix scan's F8).** A real-world scan through
epics-base/pvxs's current `master` hit exactly the failure mode D2's
"Context" section predicted in the abstract: comparing `1.5.2` against
`master` raised `ScopeMismatchError` because `master` had added exactly one
new public header, `include/pvxs/json.h`, with nothing else added, removed,
or renamed. This is not the "manifest/CLI-flag drift between two extraction
runs" mistake `scope_fingerprint` exists to catch — it is ordinary,
unremarkable library evolution (a new feature getting its own header
between minor releases), and every *other* tag-to-tag pair in pvxs's
history keeps an identical header set and never triggers the gate, which is
exactly why neither of two earlier, narrower validation passes against
this same project surfaced it: it only shows up once a scan genuinely
reaches a project's live tip rather than stopping at the last tagged
release.

The gate cannot, from the fingerprint alone, distinguish "upstream added a
real header" from "the caller's CLI/manifest drifted between two
extraction runs" — both symptoms are identical (a different declared
header-file set). What *can* be distinguished, without any new evidence
beyond what `ExtractionContract` already carries, is **direction**: a
manifest/CLI-flag drift can shrink, grow, or entirely replace the declared
set in an unprincipled way, but a library's own ordinary evolution between
releases overwhelmingly shows up as the new side's declared surface being a
strict superset of the old side's — nothing the old side named is missing,
and if anything is newly present, the ordinary diff engine already knows
how to classify a newly-declared symbol as an addition. So rather than
requiring the two declared-file *sets* to be identical, the gate is
relaxed to require only that neither `SCOPE_FIELD_KEYS` field (`"headers"`,
`"public_header_dirs"`) ever *shrinks* — checked independently per field
(`_scope_field_is_additive_superset`), so a genuine removal or rename
hiding behind a co-occurring, unrelated addition still correctly hard-fails
(a library that drops one public header while adding another is not "pure
addition," and this carve-out must not paper over it).

This is deliberately narrower than it might first appear, for the same
reason the single-header collapse already built into `scope_fields`
(`"<single-header>"`/`"<single-header-dir>"` — see
`compute_extraction_contract`'s docstring) exists: with only one entry
declared on a side, there is no real per-entry identity to verify a
superset claim against at all — the one name isn't load-bearing scope
identity, by the same design decision that already lets a lone header be
renamed between releases without tripping the gate. Guessing "it's
probably still additive" in that case would be exactly the kind of silent
assumption D2 exists to refuse to make, so the carve-out explicitly
declines (falls through to the existing hard-fail) whenever either side's
value for a differing field is one of these sentinels, rather than treating
"can't tell" as "assume yes."

Like the platform-identity and build-context carve-outs above, this one
only ever **widens** what the gate accepts as comparable — it introduces no
new way to suppress a genuine mismatch the gate would otherwise correctly
catch, and the confirmed workaround for the cases it still declines
(`--diagnostic-comparison`, already documented above) remains available
unchanged. Unlike those two carve-outs, this is the first one gating the
**scope** branch rather than the **profile** branch — the platform-identity
and build-context carve-outs both operate on `profile_fields`/`PROFILE_FIELD_KEYS`;
this one is the first to give `check_contracts_comparable`'s scope check
its own field-level (rather than opaque-hash-level) reasoning, following
the same pattern the profile branch already established: keep the raw,
attributable field values on `ExtractionContract` (not just the fingerprint
hash) specifically so a later carve-out can reason about *what* changed,
not merely *that* something did.

**Correction (Codex review, PR #641 follow-up): the carve-out's original
`return None` was placed inside the scope `if` block, exiting the whole
function and silently skipping the profile check that follows.** A release
that both adds a header (safely waived) *and* changes an unrelated,
uncorroborated profile field (compiler flags, macros, include order —
none of which any existing carve-out covers) would have been wrongly
treated as fully comparable instead of correctly raising
`ProfileMismatchError` for the second, genuine drift. Fixed by gating the
carve-out into the scope condition's own boolean expression instead of an
early return inside the block — waiving the scope mismatch now falls
through to the profile check unconditionally, the same as an ordinary
non-mismatching scope comparison always has.

**Header-sequence-growth carve-out (Codex review, same follow-up round):
that correction immediately exposed a second, deeper gap — the identical
"pure addition" scenario now correctly reached the profile check, and
promptly failed it anyway.** `profile_fields["header_sequence"]` tracks
declared-header *order* as its own genuine extraction-context fact
(`compute_extraction_contract`'s docstring: header order can change how a
*later* header's macros/pragmas resolve, so it is deliberately not folded
into scope's order-independent declared set) — and adding a header
necessarily changes this sequence too, by construction. With only the
scope-side carve-out, `check_contracts_comparable` still raised
`ProfileMismatchError` on the unmodified real pvxs scenario (confirmed by
direct repro: `declared_headers=[a,b]` → `[a,b,c]` with everything else
held constant differed only on `header_sequence`, and still hard-failed).
A second, symmetric carve-out closes this: a `profile_fingerprint`
mismatch confined to `header_sequence` alone does not raise when the new
sequence, with exactly the newly-added headers removed (preserving order),
reconstructs the old sequence exactly
(`_header_sequence_is_additive_reorder_free`) — proving no *existing*
header was reordered relative to another, only new ones appended or
inserted. A reorder of existing headers entangled with growth (e.g.
`[a,b]` → `[b,a,c]`) still raises, since that genuinely could change what
an earlier-declared header's parse sees. Verified end-to-end against the
exact real pvxs F8 scenario (both carve-outs together): `check_contracts_comparable`
now returns `None` — no exception of either kind — reproducing what the
original F8 fix was supposed to achieve but, until this round, never
actually did for a snapshot pair that also carries `profile_fingerprint`
(i.e. ran the L2 frontend, the ordinary case for a real header-AST dump).

**A fourth round (Codex review) found that the header-sequence fix above
still didn't reach the real production invocation shape, plus a second,
more general structural gap in how carve-outs compose.**

The production `dump` path (`cli_dump_helpers.py`) calls
`resolve_inferred_header_roots` to auto-add the header-owning directory as
a declared include, so a real `-H old=<dir> -H new=<dir>` invocation
changes `profile_fields["include_sequence"]` too — that auto-added slot's
own token encodes the declared-header set it owns
(`_slot_token_for_ancestor`). Confirmed by direct repro reproducing the
*exact* production code path (calling `resolve_inferred_header_roots`
itself, not just hand-building a contract that skips it): `differing =
{"header_sequence", "include_sequence"}`, and the header-sequence carve-out
alone — which only ever considered `differing <= _HEADER_SEQUENCE_FIELDS`
— declined because `include_sequence` was also present, so
`check_contracts_comparable` still raised `ProfileMismatchError` for the
real F8 CLI shape even after the previous round's fix. A fourth carve-out,
`_include_sequence_is_additive_owned_growth`, closes this the same way as
`header_sequence`: a mismatch confined to `include_sequence` doesn't raise
when every differing slot's owned `"hdrs:..."` token is itself a pure
superset growth — an `"ext:"`/`"label:"` slot differing, a slot-count
change, or a `<single-header>` sentinel on either side all still raise.

The second, more general gap this round found: **carve-outs didn't
compose.** Each carve-out required `differing <= its own static field-set`
in full — so a release combining two *independently already-sanctioned*
deltas (e.g. adding a header *and* making a corroborated C++-standard
raise) produced `differing = {"header_sequence", "language_standard"}`, a
set matching *neither* carve-out's field-set on its own, and still raised
even though each half was individually fine. Confirmed by direct repro
before any fix. Restructured the profile check from four independent
"does `differing` match this exact field-set" tests into one composing
loop: each carve-out claims and verifies only the subset of `differing` it
understands, narrowing a shared `unexplained` working set; the pair is
comparable once nothing remains unexplained. The four carve-outs' field-sets
(`_PLATFORM_IDENTITY_FIELDS`/`_BUILD_CONTEXT_FIELDS`/`_HEADER_SEQUENCE_FIELDS`/
`_INCLUDE_SEQUENCE_FIELDS`) are mutually disjoint, so this restructuring
changes no single carve-out's own verification logic or safety
invariants — it only widens *which combinations* of independently-safe
deltas are recognized together, never weakens what counts as "safe" for
any one field.

**One further gap surfaced during this investigation, deliberately left as
a documented limitation, not fixed:** a header added *outside* the old
side's common ancestor directory (e.g. existing headers under
`include/foo/`, a new one under a sibling `include/bar/`) shifts the common
root every remaining `headers` identity is computed relative to
(`compute_extraction_contract`), so even the *existing* headers' identity
strings change shape (`"a.h"` → `"foo/a.h"`) and the additive-superset
check correctly declines. This is the conservative, safe failure mode — a
real hard-fail, never a silently wrong verdict — for a case genuinely
outside the real pvxs F8 scenario, which adds its new header *within* the
existing common directory. Closing it properly would mean re-deriving one
side's header identities relative to a root chosen with knowledge of the
*other* side (a cross-snapshot computation `compute_extraction_contract`'s
current one-side-at-a-time design doesn't support), which is exactly the
kind of `comparability.py`-internals redesign this file's own conventions
flag as needing its own ADR treatment, not a fifth drive-by carve-out
appended to an already four-deep list. `--diagnostic-comparison` remains
the correct workaround.

**A fifth review pass** (Codex, two P1s) found the sequence carve-outs and
the composing-loop restructuring above each had one more gap:

- **The header/include-sequence carve-outs accepted an additive *shape* on
  their own, without corroborating it against an actual scope-level
  change.** `scope_fields["headers"]` deliberately treats a file reaching it
  via `declared_headers` (fed to the L2 frontend via `-H`) and via
  `public_header_paths` (bare `--public-header` provenance, never actually
  parsed) as the *same* declared-surface membership (see
  `compute_extraction_contract`'s own docstring) — so a header already
  declared identically on both sides via `--public-header`, but fed to the
  L2 frontend only on the new side, leaves `scope_fingerprint` completely
  unchanged while `profile_fields["header_sequence"]` (and, via
  `resolve_inferred_header_roots`, `include_sequence` too) still grows
  additively, purely because of which mechanism happened to feed the
  parser. The old snapshot then has *no* parsed AST content for that header
  at all, so a real removal made inside it between old and new would be
  silently invisible rather than reported — a false negative, not merely
  extra noise, and P1-severity for exactly that reason. Fixed with a new
  helper, `_scope_growth_corroborated`, requiring `scope_fingerprint` to
  genuinely differ *and* verify as additive (the same check the scope
  carve-out itself uses) before either sequence carve-out is allowed to
  fire. Confirmed by direct repro before any fix (both new regression tests
  failed with "DID NOT RAISE ProfileMismatchError" without the corroboration
  requirement), and re-verified the real F8/directory-based end-to-end
  tests still pass afterward — the genuine pvxs scenario's scope-level
  "headers" field really does grow when a wholly new header is added, so
  requiring corroboration doesn't regress it.
- **An opaque `profile_fingerprint` mismatch was silently accepted as
  comparable.** The composing loop starts `unexplained = set(differing)`,
  where `differing` is the subset of `PROFILE_FIELD_KEYS` that actually
  differ between the two sides' `profile_fields`. If `profile_fields` was
  entirely absent or malformed on deserialization (the serialization
  layer's `_extraction_contract_from_dict` substitutes `{}` for a missing/
  invalid field, rather than failing), every field trivially compares
  `"" == ""`, so `differing` — and therefore `unexplained` — comes out
  empty even though `profile_fingerprint` genuinely differs. `if not
  unexplained: return None` then treated "nothing left to explain" as
  "already explained," bypassing the fail-closed gate exactly when the
  granular data needed to verify safety was missing. Fixed by
  distinguishing an entirely-empty `differing` (nothing was positively
  verified, so this raises unconditionally) from a non-empty `differing`
  (still requires the carve-outs to explain everything before returning
  comparable, exactly as before) — see the sixth review pass below for the
  related, still-open gap this round's fix didn't yet close.

Both fixes add regression tests proven to fail without them (`git stash`
the fix, confirm the new test fails, restore). Full fast unit suite green,
mypy/ruff clean. `tests/test_comparability.py` crossed the file-size hard
cap once these tests landed; the `check_contracts_comparable`-focused
tests (already about two-thirds of the file) were split into a sibling
`tests/test_comparability_gate.py`, leaving the parent file scoped to
`compute_extraction_contract`'s own fingerprint-computation tests, per this
repo's usual large-file-split convention.

**A sixth review pass** (Codex, one P1, one P2) found the fifth pass's own
opaque-mismatch fix still had a gap, plus a defensive-coding gap shared by
all four carve-out helpers:

- **A differing field outside `PROFILE_FIELD_KEYS` entirely could still
  hide behind a legitimate, waived delta.** The fifth pass's fix only
  covered `differing` (over `PROFILE_FIELD_KEYS`) coming out completely
  *empty* — it left unaddressed a contract carrying an extra field this
  build doesn't recognize (a newer schema key) that also differs, mixed
  with an otherwise-legitimate, carve-out-waived delta (e.g. additive
  `header_sequence` growth corroborated by real scope growth). Since
  `differing`'s computation only ever iterates `PROFILE_FIELD_KEYS`, the
  unrecognized field's delta was structurally invisible to `unexplained` —
  once the recognized delta was waived, `unexplained` came out empty and
  the pair was wrongly reported comparable, silently ignoring the
  unrecognized field entirely. Fixed with a new, independently-computed
  `unknown_differing` set (every key outside `PROFILE_FIELD_KEYS`, over the
  union of both sides' field-dict keys, that actually differs) — its
  presence is now unconditionally fatal, checked before any carve-out
  result is trusted, since no carve-out understands an unrecognized key's
  semantics well enough to vouch for it.
- **The carve-out helpers didn't handle malformed JSON.**
  `_scope_field_is_additive_superset`,
  `_header_sequence_is_additive_reorder_free`, and
  `_include_sequence_is_additive_owned_growth` all call `json.loads` on
  their `str` inputs unguarded — but a serialized or externally
  constructed `ExtractionContract` can carry an arbitrary string there
  (`_extraction_contract_from_dict` preserves field values without
  validating their JSON encoding), so a malformed value (e.g.
  `headers: "not-json"`) raised an unhandled `JSONDecodeError` that escaped
  `check_contracts_comparable` as a raw traceback instead of the clean
  `ScopeMismatchError`/`ProfileMismatchError` the gate exists to fail
  closed with. Fixed with a shared `_json_load_list` helper (decodes to a
  list or returns `None` on any decode failure or non-list result); every
  carve-out now declines instead of crashing when either side fails to
  decode. `_include_sequence_is_additive_owned_growth`'s inner per-slot
  owned-pairs decode gets the same treatment, plus a guard against a
  structurally-malformed-but-valid-JSON pair (`tuple(p)` on a non-sequence
  element) declining instead of raising `TypeError`.

New regression tests: two gate-level tests for the unknown-differing-key
case (one alone, one mixed with a waived recognized delta — proven to fail
without the fix), one direct unit test per carve-out helper pinning
malformed-JSON decline (plus a non-list-JSON case for the scope helper),
and one gate-level end-to-end test confirming a malformed scope field
raises `ScopeMismatchError` rather than crashing. Full fast unit suite
green, mypy/ruff clean.

**A seventh review pass** (Codex, one P1, one P2) found the sixth pass's
own fixes each had one more gap, symmetric to ones already closed:

- **The scope-side carve-out had the identical unknown-field gap the
  profile side's `unknown_differing` check had just closed.** The scope
  carve-out's `all(...)` only ever checks `SCOPE_FIELD_KEYS`
  (`headers`/`public_header_dirs`), so a contract carrying an extra
  `scope_fields` key this build doesn't recognize (a newer schema field)
  was invisible to it — if the two known fields happened to be equal or
  additive, the whole `scope_fingerprint` mismatch was silently waived
  without ever examining the unrecognized field. Confirmed by direct repro:
  equal known fields plus `future_scope: "old"` → `"new"` returned `None`
  (comparable). Fixed with a `scope_unknown_differing` set, computed the
  same way as the profile side's `unknown_differing` and checked before
  the additive-superset carve-out is trusted; its presence is
  unconditionally fatal.
- **The malformed-JSON fix from the sixth pass validated only the outer
  list shape, not its elements.** `_json_load_list` (added last round)
  correctly rejects non-JSON and non-list values, but a syntactically
  valid list with non-scalar members (e.g. `scope_fields["headers"] =
  "[{}]"`) decodes fine as `[{}]` — every caller then immediately does
  something requiring hashable strings (`in
  _SCOPE_SINGLE_ENTRY_SENTINELS`, `set(...)` membership/superset checks),
  and a `dict` element there raises `TypeError: unhashable type: 'dict'`
  instead of the clean decline the previous round's fix was meant to
  guarantee. Fixed with a new `_json_load_str_list` helper (an
  `_json_load_list` result additionally validated to be all-`str`), used
  everywhere a plain string-identity list is expected
  (`_scope_field_is_additive_superset`,
  `_header_sequence_is_additive_reorder_free`, and
  `_include_sequence_is_additive_owned_growth`'s outer per-slot decode —
  which also let this round remove that function's now-redundant manual
  `isinstance(..., str)` guard, added ad hoc in the sixth pass before this
  shared helper existed). `include_sequence`'s inner owned-pairs decode
  (a list of pairs, not plain strings) keeps using the generic
  `_json_load_list`, since its `tuple(p)`/set-construction step already
  had its own `try`/`except TypeError` guard from the sixth pass.

New regression tests: two gate-level tests for the unknown-scope-delta
case (equal known fields, and known fields growing additively — both
proven to fail without the fix), one direct unit test per affected carve-out
helper pinning the non-string-list-member decline, and one gate-level
end-to-end test confirming a non-string scope field member declines
cleanly rather than raising `TypeError`. Full fast unit suite green,
mypy/ruff clean.

**An eighth review pass** (Codex, one P1, one P2) found one more gap in
the include-sequence carve-out's inner decode, plus the scope-side
equivalent of a gap already closed on the profile side:

- **`_include_sequence_is_additive_owned_growth`'s owned-pairs validation
  still accepted malformed members that happened to look harmless.** The
  sixth pass's `try`/`except TypeError` around `tuple(p)` doesn't catch a
  bare string member like `"xx"`: strings are themselves iterable, so
  `tuple("xx")` silently succeeds as `("x", "x")` instead of raising —
  if that coincidentally matched an already-owned pair (or a wrong-arity
  member sat alongside a genuinely new, valid one), the resulting set
  comparison could look like real additive growth even though the
  evidence was malformed, letting the gate fail open. Confirmed by direct
  repro: `old_owned = {("x", "x")}` (a real pair) against
  `new_owned = {tuple("xx")}` (the malformed member) satisfied
  `new_owned >= old_owned` and the function wrongly returned `True`.
  Fixed with a new `_is_owned_header_pair` validator (an exact two-element
  `list`/`tuple` of `str`) checked for every member of both sides before
  any `tuple(p)` conversion runs — the now-provably-unreachable
  `try`/`except TypeError` around the set-comprehension is removed.
- **The scope-side carve-out had the same opaque-mismatch gap the profile
  side's `if not differing` check (fifth pass) already closed.**
  `_scope_field_is_additive_superset` returns `True` on `old_value ==
  new_value`, so calling it over *every* `SCOPE_FIELD_KEYS` entry (as the
  carve-out did) means an entirely-*unchanged* set of known fields always
  trivially satisfies `all(...)` — if a deserialized/externally-constructed
  contract carries a `scope_fingerprint` that doesn't actually match what
  this version would recompute from `scope_fields` (the same "opaque
  hash" class of problem as the profile side), nothing recognized ever
  explains the differing fingerprint, yet the carve-out still waived it.
  Fixed by restricting the additive-superset check to a new
  `scope_differing` set (only the `SCOPE_FIELD_KEYS` entries that actually
  differ, mirroring the profile side's `differing`) and requiring it to be
  non-empty before the carve-out can apply — an entirely-unchanged known
  field set now correctly raises instead of being trivially "verified."

New regression tests: two direct unit tests for the owned-pairs gap (a
malformed member matching an existing pair, and a wrong-arity member
alongside a genuinely new valid one — both proven to fail without the
fix), and two gate-level tests for the opaque scope mismatch (raise mode
and diagnostic mode, both proven to fail without the fix). Full fast unit
suite green, mypy/ruff clean.

**A ninth review pass** (Codex, one P1, one P2) found one more gap in the
`unknown_differing`/`scope_unknown_differing` checks, plus an unrelated
gap in the customization-point allowlist regex:

- **`.get(k, "")` conflated "key absent" with "key present, empty
  value."** Both unknown-field checks fall back to `""` for a missing
  key, matching every other field-presence check in this module — but for
  *these* checks specifically, that means a newer-schema field added on
  only one side with an empty value (`{"future_scope": ""}` vs. no key at
  all) compares `"" == ""` and stays invisible, even when combined with an
  otherwise-legitimate, corroborated delta (additive `header_sequence`/
  `headers` growth). Confirmed by direct repro before any fix: this exact
  shape returned `None` (comparable) on both the profile and scope sides.
  Fixed with a module-level `_FIELD_ABSENT = object()` sentinel, distinct
  from every valid field value, used as the `.get()` fallback in both
  checks instead of `""`.
- **The customization-point allowlist regex didn't account for libc++'s
  inline ABI namespace.** libc++ wraps its entire standard-library
  implementation in a versioned inline namespace (`__1` in mainline
  libc++, `__ndk1` on Android NDK) between the `St`/`3std` substitution
  and the actual class name — so a user specialization of `std::hash<X>`
  under libc++ mangles as `_ZZNKSt3__14hashI1XEclERKS1_E4salt` rather than
  the bare GCC/libstdc++ shape `_USER_SPECIALIZABLE_STD_TEMPLATE_RE`
  originally expected. `_STDLIB_LOCAL_NAME_RE` still matched the `St`
  prefix (classifying the symbol as stdlib-owned) while the exclusion
  regex missed it entirely, so a real user-owned regression under libc++
  was wrongly suppressed. Fixed by inserting an optional, non-greedy
  `(?:\d+__[A-Za-z0-9_]+?)?` between the substitution and the
  customization-point alternation — non-greedy specifically, since a
  greedy quantifier would consume into the customization-point name
  itself (`3__1` followed directly by `4hash` reads as one contiguous
  word-character run) and the engine needs to backtrack to the shortest
  split that still matches. Verified this doesn't spuriously match
  ordinary (non-customization-point) stdlib types under the same inline
  namespace (`std::__1::vector<...>` still correctly classifies as
  stdlib-owned, not excluded).

New regression tests: one gate-level test per side for the empty-vs-absent
gap (both proven to fail without the fix — the scope one specifically
constructed with `headers` also growing additively, so the already-fixed
opaque-scope-mismatch check from the previous pass doesn't coincidentally
mask this one), one parametrized case for the libc++ example in
`test_name_classification.py`, and the Hypothesis grammar suite
(`test_name_classification_properties.py`) extended with an optional
libc++ inline-namespace component so the property test covers this shape
generatively, not just the one hand-picked example. Full fast unit suite
green, mypy/ruff clean.

**A tenth review pass** (Codex, one P1) found the deepest gap yet — not
another carve-out corner case, but a soundness gap in the whole carve-out
framework's founding assumption:

- **No carve-out ever verified that a contract's stored fingerprint was
  actually computed from its own stored fields.** Every carve-out above
  reasons entirely from `scope_fields`/`profile_fields` — "this recognized
  field grew additively, so the fingerprint mismatch is explained" — but
  that reasoning is only sound if the fingerprint genuinely reflects those
  fields. For a snapshot `compute_extraction_contract` produced, that
  invariant always holds by construction (the fingerprint is a hash of the
  exact fields stored alongside it), so this was never an issue in
  practice for the real pvxs scenario this whole review chain is about.
  It is completely unenforced, though — confirmed by direct repro before
  any fix: two arbitrary, unrelated fingerprint strings (`"s-old"`/
  `"s-new"`, not real hashes of anything) alongside `headers` genuinely
  growing additively still made `check_contracts_comparable` return `None`
  (comparable), on both the scope and profile sides independently. Every
  prior round's carve-out narrowing (`unknown_differing`,
  `scope_unknown_differing`, the opaque-mismatch checks, `_FIELD_ABSENT`)
  made the *known-fields* reasoning progressively tighter, but none of them
  actually tied the fingerprint itself back to the fields it's supposed to
  attribute a mismatch to.
  Fixed with `_fingerprint_matches_fields(fingerprint, fields, keys)` —
  recomputes `_sha256_of(*[fields.get(k, "") for k in keys])` and compares
  against the stored fingerprint, exactly mirroring
  `compute_extraction_contract`'s own algorithm. Called on BOTH sides,
  for BOTH scope and profile, as the very first check inside each
  fingerprint-mismatch branch — before `unknown_differing`/
  `scope_unknown_differing`, before `differing`/`scope_differing`, before
  any carve-out. A mismatch on either side is unconditionally fatal: it
  means the fields on that side cannot be trusted to explain that side's
  own fingerprint, so nothing reasoned from them is safe to act on.
  Verified this doesn't regress any real-world path: every existing test
  that expects a carve-out to succeed builds its contracts via the real
  `compute_extraction_contract` (which always satisfies this invariant by
  construction) rather than hand-typed placeholder fingerprints, so only
  two existing tests (both intentionally using placeholder scope
  fingerprints to isolate an unrelated *profile*-side check) needed
  updating to use a real, matching scope fingerprint so execution still
  reaches the profile check those tests actually exercise.

New regression tests: two direct unit tests for `_fingerprint_matches_fields`
itself, and four gate-level tests (raise mode and diagnostic mode, for
both scope and profile) reproducing arbitrary/unrelated fingerprints
alongside genuinely additive-shaped fields — all six proven to fail
without the fix. Full fast unit suite green, mypy/ruff clean.

**An eleventh review pass** (Codex, one P1) found that the header-sequence
carve-out's own definition of "additive" was too permissive:

- **Mid-sequence or leading insertion was wrongly treated as safe as a
  trailing append.** `_header_sequence_is_additive_reorder_free` originally
  only checked that the *existing* headers kept their relative order to
  each other (`[a.h, c.h]` -> `[a.h, b.h, c.h]` passed, since `a.h` still
  precedes `c.h`). But the aggregate driver TU parses declared headers
  sequentially, so inserting `b.h` between them means `c.h` is now parsed
  with `b.h`'s macros/pragmas already in effect — a genuinely different
  extraction context than before, even though the shape superficially
  looks like a pure addition. The same risk applies to insertion *before*
  all existing headers (`[b.h, c.h]` -> `[a.h, b.h, c.h]`): `b.h`'s own
  parsing context changes even though the existing headers' relative order
  to each other is untouched. Confirmed by direct repro before any fix:
  both shapes returned `True`. Fixed by replacing the "existing headers
  keep their relative order" check with a strictly stronger one: the new
  sequence must be the old sequence, byte-for-byte unchanged, with new
  entries appended *only* after it (`new_list[:len(old_list)] ==
  old_list`) — proving every existing header's own preprocessing context
  is identical to before, not merely that existing headers didn't swap
  places. This is a narrower, more conservative definition of "additive"
  than the carve-out originally used; only a strict trailing append is
  waived now, matching the shape the real pvxs F8 scenario (and every
  other test in this file building genuine `compute_extraction_contract`
  end-to-end scenarios) already produces, so no real-world carve-out
  outcome regresses — only one existing unit test, which had specifically
  pinned the too-permissive mid-sequence-insertion behavior as `True`,
  needed its expectation flipped to `False` (and renamed to match).

New regression tests: the previously-passing mid-sequence-insertion test
is now a `..._false_for_insertion_in_middle` case (flipped expectation,
proven to fail against the pre-fix code), plus a new
`..._false_for_insertion_before_all` case for the leading-insertion shape.
Full fast unit suite green, mypy/ruff clean.

**A twelfth review pass** (Codex, one P1) found a second gap left as a
**documented limitation, not fixed** — the same category as the
common-root-rebasing gap above, not a further carve-out round: an appended
public header that itself pulls in a *new* dependency reachable only
through a non-owned `-I` directory (an `"ext:"` slot, or the trailing
`"sys:"` system bucket) changes that slot's digest alongside the owned
`"hdrs:"` slot's legitimate growth. Confirmed by direct repro: `[a.h,
b.h]` -> `[a.h, b.h, c.h]`, where `c.h` transitively includes a header
resolved only via a separate external include directory, still raises
`ProfileMismatchError` — the per-slot loop declines the instant a
non-`"hdrs:"` slot differs at all, with no way to tell "this dependency is
new, brought in solely by the accepted header addition" from "this
external directory's contents genuinely drifted between the two
extraction runs." Unlike the owned `"hdrs:"` slot (which stores an
explicit, JSON-encoded list of `(identity, relative_path)` pairs precisely
so superset growth can be verified), an `"ext:"`/`"sys:"` slot's token is
a single opaque `_sha256_of` digest over that bucket's *entire* file set —
by construction, there is no per-file identity recoverable from two hash
strings to diff against each other, so "did this bucket grow strictly, or
did an existing file's content change" is genuinely unanswerable from the
stored data alone. Closing this safely would mean changing what an
`"ext:"`/`"sys:"` slot stores — a JSON pairs list like `"hdrs:"` already
uses, instead of a collapsed hash — which is a `profile_fingerprint`
wire-format change (every existing fixture/test constructing an `"ext:"`/
`"sys:"` token, and this file's own fingerprint-reproduction invariant,
would need to change in lockstep), not a logic fix within the existing
shape. That is exactly the kind of `comparability.py`-internals redesign
this file's own conventions reserve for its own ADR treatment, the same
bar the common-root-rebasing limitation was held to. `--diagnostic-comparison`
remains the correct workaround for this specific scenario.

### D3. Manifest and real multi-TU dump

New `abicheck/dump_manifest.py`: a strict YAML parser (unknown fields are
errors, and duplicate mapping keys are errors too — `yaml.safe_load`'s
default last-value-wins duplicate-key handling is exactly the kind of
silent scope drift this ADR exists to catch, not silently ignored) for a
`roots` / `translation_units` document —
each TU carries `name` (unique), `includes` (ordered), `forced_includes`
(ordered, local to that TU only), `required: bool`, and
`contributes_to_abi: bool`, with the invariant
`contributes_to_abi=True ⇒ required=True` enforced at parse time (a TU whose
declarations feed the ABI model cannot also be allowed to fail silently —
this is the review's sharpest correctness point: "optional but
contributes" is the exact shape that produces false removals). All existing
single-header/`-H` CLI invocations construct a single-TU manifest internally
(one `legacy-main` TU) — no behavior change for a caller not opting into a
manifest file.

The base profile also carries `public_header_paths`/`public_header_dirs`
(both optional, root-relative, D1's provenance-classification inputs) —
the manifest-mode equivalent of today's `--public-header`/
`--public-header-dir` CLI flags. **`--dump-manifest` and `--public-header`/
`--public-header-dir` are mutually exclusive on `dump`** (a `UsageError`,
the same "manifest is the sole source of truth for scope" pattern D3
already applies to `--frontend-context`'s CLI/manifest split): a caller
using an explicit manifest declares provenance classification inside it,
so `scope_fingerprint` (D1) always has one complete, manifest-only source
for these inputs rather than a CLI-flag/manifest-field split that D3's
`plan --dump-manifest` diagnostic (see below) — which reads the manifest
document only, never invoking a compiler or resolving CLI flags — could
never fully see.

`dumper.py`'s `dump()` gains a manifest-driven path: **one castxml/clang
invocation per TU** (base compile profile + that TU's own forced includes),
each producing a normalized `TuFragment` (entities keyed by `entity_key`,
not raw AST), instead of today's single aggregate-then-parse call. This is
additive — the existing single-TU code path becomes the manifest path's
one-TU special case, not a parallel implementation to keep in sync.

A base compile profile (compiler, target, language standard, global flags,
and `frontend_context` — `host` by default, D5's requested AST context)
is shared across all TUs in one manifest; **different compilers or target
triples across TUs in the same manifest are rejected at parse time** — that
is two different ABI contexts, which stay two separate snapshots (and two
separate `profile_fingerprint`s) rather than one snapshot pretending to
speak for both. Only forced includes and include order vary per TU.
`frontend_context` is declared here, in the base profile, precisely
because D5 needs an accepted input path to request it — a manifest schema
that only carries `roots`/`translation_units` gives a DPC++ flow needing a
non-default context nowhere to put the request. The legacy, non-manifest
CLI path gains a matching `--frontend-context host|device` flag (default
`host`), threaded the same way `--lang`/other base-profile flags already
are, so a caller not using a manifest can still opt into the non-default
context.

### D4. Compatible merge across translation units

New `abicheck/tu_merge.py`, deliberately reusing `buildsource/crosscheck.py`
(`:215`, `run_crosschecks`)'s existing merge/cross-validate shape rather
than a new algorithm: for each `entity_key` seen in more than one TU's
fragment, merge is only trivial (union provenance, keep the richer
declaration) when the two declarations are **compatible** —
forward-declaration + definition, declaration + redeclaration, differing
only in an added default argument. Two full declarations disagreeing on
return type, layout, or calling convention is an `INCONSISTENT_DECLARATION`
conflict; a heterogeneous-context conflict (should D3's per-manifest
single-profile rule ever be relaxed later) is
`HETEROGENEOUS_ABI_CONTEXT`.

**Both are extraction-time conflict codes on a new `TuMergeError`
(`errors.py`), not `ChangeKind` enum members — this needs saying
explicitly, since the all-caps naming otherwise reads exactly like one.**
The distinction is structural, not stylistic: a `ChangeKind` is something
`checker.compare`'s diff produces when comparing two already-`Complete`
snapshots; these two fire *before* a snapshot is ever considered complete
enough to diff at all — a snapshot with unresolved conflicts is not a
`CompleteSnapshot` and cannot feed D2's comparability gate as a clean
side. A merge conflict at TU-fragment level is the D3/D4 layer's own
extraction-time failure (parallel to `IncompatibleSnapshotSchemaError` from
D1, or `DumpDepthNotSatisfiedError`'s existing precedent), not a
comparison finding — so they are correctly *outside* the `ChangeKind`
registry and its four-step procedure, `changekind-partition`/
`changekind-detector` completeness gates, and `RISK_KINDS`/`QUALITY_KINDS`
severity classification entirely. `tu_merge.merge_fragments(...)` raises
`TuMergeError(code=...)` (`code` one of the two strings above, plus the
conflicting `entity_key` and both fragments' provenance) when any conflict
is unresolved; `dumper.py`'s manifest-driven `dump()` lets it propagate,
producing an `IncompleteAttempt`/extraction failure the same way a
required TU's compile failure already does (D3).

`entity_key` deliberately excludes return type (keeping it in `abi_facts`,
not the merge key) — folding return type into identity turns a return-type
change into an unrelated add+remove pair instead of one detected change,
the same failure mode ADR-045/048 already fixed for old/new type matching,
applied here to same-version cross-TU identity instead.

### D5. SYCL/DPC++ host vs. device AST context selection

`sycl_metadata.py` today only classifies a **compiled binary's** exported
`piextDevice*` symbols (`:234,238`) — it has no visibility into which AST
context (host vs. `spir64` device target) a DPC++ frontend invocation
actually parsed. New `abicheck/sycl_context.py`: when the L2 clang backend
(`dumper_clang.py`) invokes a DPC++-capable compiler, it decodes the
frontend's possibly-multi-document JSON output as a sequence of
`{kind, target, ast}` contexts (streaming document boundaries, not a
bracket/string split), tags each with the compiler-reported target triple,
and selects the context matching the manifest's/CLI's requested
`frontend_context` (`host` by default).

**Selection is by `kind`, not by target-triple string matching — this
needs to be explicit, since "host vs. device" reads like it could mean
either.** Each decoded context's `kind` (`"host"` or `"device"`, read
directly from the compiler's own JSON output, the same authoritative
source the target triple comes from) is what's compared against the
requested `frontend_context`; the target triple (`spir64`, etc.) is
carried alongside for diagnostics and provenance, never itself the
selection key — a frontend could in principle label a context's target
triple ambiguously or use a triple this ADR doesn't enumerate, and
`sycl_context.py` must not be in the business of pattern-matching triple
strings to guess intent when the compiler already states `kind` plainly.
Three outcomes — only the two error outcomes are extraction-time failures
that never reach D1's fingerprinting at all (there is no snapshot for
either to describe); the successful-selection outcome instead *feeds*
D1's fingerprinting, per the next paragraph below, not the reverse:

- **Exactly one decoded context has the requested `kind`** — the normal
  case, selected and passed on to normal extraction, whose resolved `kind`
  is then hashed into `profile_fingerprint` (below) exactly like every
  other resolved compile-context field.
- **Zero decoded contexts have the requested `kind`** — `AST_CONTEXT_MISSING`
  (e.g. only a `spir64`/device context when `host` was requested), an
  extraction failure with no resulting snapshot, so nothing to fingerprint.
- **More than one decoded context shares the requested `kind`** —
  `AST_CONTEXT_AMBIGUOUS`, never resolved by picking the first, the
  smallest, or any other implicit tiebreaker; an ambiguous frontend output
  is exactly the kind of "the extraction can't prove what it captured"
  situation this ADR's authority rule (ADR-028 D3) says must not be
  silently resolved in either direction — also an extraction failure with
  no resulting snapshot, so nothing to fingerprint.

**Selecting the right context is not the same as the gate knowing which
context was selected — `profile_fingerprint` must hash the resolved
`frontend_context`/`kind`, or a host-vs-device mismatch passes D2's gate
silently.** Two extractions requesting different `frontend_context`
values (`host` on one side, `device` on the other) with identical
compiler/target/macros/includes otherwise would parse genuinely different
ASTs — different declarations, different macro state — but D1's
`profile_fingerprint` field list (above) predates this section
(`frontend_context` doesn't exist until D3/D5), so leaving it un-added
would silently reopen exactly the under-counting bug D1 exists to close,
one field short: a host/device extraction drift would be invisible to the
gate, and any AST difference between the two contexts would surface as an
ordinary reported `Change` — indistinguishable from a real ABI edit —
instead of the pre-diff `not_comparable` this whole design exists to
produce for exactly this class of drift. `profile_fingerprint`'s hashed
fields therefore include the resolved `kind` (`"host"`/`"device"`) once
this section's selection logic runs, not only the requested
`frontend_context` string — the two would otherwise agree even when a
`AST_CONTEXT_AMBIGUOUS`-adjacent frontend quirk resolved to a
differently-labeled context on each side for the same requested value, an
edge case the requested-string alone can't see but the actually-resolved
`kind` can.

A run that produces only a `spir64`/device context when `host` was
requested is an extraction failure, not a successful-but-wrong snapshot.
Fixture-first per the review's own sequencing advice: a real captured
multi-document DPC++ AST fixture and a plain single-context clang fixture
land before the stream parser (Phase 0), so the parser — and this `kind`
vs. target-triple distinction — is built against real output shape, not
an assumption of it; if a captured real fixture turns out not to carry a
`kind` field at all, that is exactly the kind of discovery Phase 0 exists
to surface before D5 is implemented against a guess.

### D6. Resource-aware scheduling for the frontend, shared with `buildsource`

`buildsource/source_replay.py` already implements exactly the scheduling
policy the review asks for — a thread/process pool sized by
`min(cpu-derived cap, cgroup-`MemAvailable`-derived cap)`, documented in
`buildsource/CLAUDE.md`. Rather than a second implementation in the
manifest driver, the RAM-probing/pool-sizing helper is factored out of
`source_replay.py` into a new leaf module, `abicheck/process_resources.py`,
that both `source_replay.py` and the new per-TU invocation loop (D3)
import — the "move the shared logic to a leaf module both sides can
depend on" rule AGENTS.md's import-cycle guidance already states, applied
here instead of growing a second scheduler. **The per-TU loop itself
lives in a new sibling module, `abicheck/dumper_manifest.py`, not
`dumper.py` — `dumper.py` is already at its file-size hard cap with no
headroom, so D3's new per-TU logic (and this section's scheduler wiring)
must not be added to it directly; `dumper.py` keeps only a thin
manifest-vs.-single-TU dispatch that calls into the new module.** The
per-TU castxml/clang calls in `dumper_manifest.py` run
under this pool instead of a fully sequential loop; a killed/timed-out
TU is recorded with its exit signal, never silently retried as a clean
empty TU.

**Adding `profile_fingerprint` itself as a cache-key input is impossible,
not merely undesirable — D1's own depfile design rules it out.**
`service_dump_cache.cached_run_dump` looks up `snapshot_cache` *before*
calling `dump()` (D1's "whole-snapshot cache is the same bypass" note
above); `profile_fingerprint`'s `-I` component is a depfile-derived digest
that only exists *after* an L2 castxml/clang invocation runs. A cache-key
input computed only by running the extraction the cache exists to skip is
circular — this was an error in an earlier revision of this paragraph, not
a deferred detail. `scope_fingerprint`, for the manifest-driven path (D3),
is different: it is fully determined by the normalized manifest document
itself, known *before* any TU is dumped, so it genuinely can feed a cache
key without running anything. `snapshot_cache.py`'s existing content-hash
cache key (`:130`) already closes the practical gap this section originally
set out to close for the legacy CLI path, without needing either
fingerprint: it recurses every `-I`/`-H` directory
(`header_utils.iter_cache_header_files`, `rglob` over header-suffix files)
and hashes each matched file's content and mtime — entirely pre-dump, no
compiler invocation required. This over-approximates deliberately (it hashes
every header-like file reachable under the directory, not only files a
given compile would actually resolve), which is the *correct* asymmetry for
a cache key: a false cache **miss** just costs a redundant dump, while a
false cache **hit** would serve a stale `contract`, so erring toward "hash
too much" is the safe direction here — the opposite of `profile_fingerprint`
itself, which must be exactly right or the gate spuriously fires. This
existing mechanism, plus D1's own order-sensitivity fix (Phase A, not
deferred here) already invalidates on the cases that matter; D6 adds no
further cache-key work for the `-I`/profile side beyond that. D6's cache-key
extension is instead scoped to what genuinely is pre-dump-knowable and not
already covered: **the manifest-driven `scope_fingerprint`'s full set of
inputs, not only its `required`/`contributes_to_abi` flags** — an earlier
revision of this paragraph narrowed to just those two flags, but D1's own
`scope_fingerprint` definition also covers each TU's *name*, its *ordered*
`includes`/`forced_includes`, and (D1's sibling-support-root fix) each
`includes` entry's `project_owned` bit. A manifest-only change confined to
any of those — a TU rename, a per-TU include/forced-include reorder, or
flipping `project_owned` on an unchanged path — leaves filesystem content
untouched (so `iter_cache_header_files`'s content/mtime walk can't see it)
while still changing what the extraction contract actually computes; a
cache key scoped to only the two flags would miss exactly this class of
drift and serve a stale `contract` from a warm cache, defeating D2's gate
through a route this whole cache-key extension exists to close. D6
therefore hashes the manifest-driven `scope_fingerprint`'s **full**
computed input set — TU names, per-TU ordered includes/forced-includes,
the `contributes_to_abi`/`required` flags, and each `includes` entry's
`project_owned` bit, together, not any one piece in isolation — none of
which the existing `iter_cache_header_files` walk has any way to see,
since none of it is filesystem content at all.

## Non-goals

- Not a rewrite of `AbiSnapshot` into a four-layer contract/model/evidence/
  run-metadata document. `model.py`'s fields already sort into those
  buckets informally (see Context); this ADR adds two fingerprint fields and
  one gate, not a new top-level schema shape.
- Not a change to `ScopeOrigin`, `provenance.py`'s classification, or any
  existing public/private/external filtering — ADR-024 already solves the
  "reportable vs. supporting entity" problem the review's §6/§7 asked for.
- Not a rewrite of `crosscheck.py`'s intra-version evidence-source merge —
  D4 reuses its shape for a new axis (cross-TU, same evidence source), it
  does not change what `crosscheck.py` itself does today.
- Not a canonical/hash-only serialization mode distinct from the persisted
  JSON. `serialization.py` already sorts sets; D1's fingerprints are
  computed from specific resolved fields, not a whole-snapshot canonical
  hash, so no second serialization path is needed.
- Not a coverage-of-expected-public-headers check (the review's §1.6). A
  manifest-declared `expected_public_headers` inventory is a plausible
  future addition once D3 ships, but is not required for the comparability
  gate itself and is left to a follow-up phase (see G32) rather than
  bundled into this decision.
- Not a change to exit codes or the legacy (non-severity-aware) `compare`
  contract for a snapshot pair that carries no `contract` field — see D2's
  backward-compatibility note.

## Consequences

**Positive:** a manifest/flag drift between two extraction runs (the
motivating oneDAL-style scenario — an umbrella header gaining a new
top-level include between CI runs, unrelated to any real API change) is
caught and reported as `SCOPE_MISMATCH` instead of a page of false
`*_added` findings. A genuine per-TU forced-include need (Arrow-style
adapter headers) becomes expressible without contaminating every other
header's parse. DPC++ host/device context confusion becomes a hard
extraction failure instead of a silently-wrong snapshot.

**Costs:** D3 is the highest-risk, highest-effort piece — it changes
`dumper.py`'s hot path from one invocation to N, and D4's merge lattice is
new surface with real edge cases (the review's own worked examples:
forward-decl + definition, ambiguous default-argument-only differences).
D5 needs a real captured DPC++ multi-document fixture before implementation
can proceed safely, which is external-tool-dependent to acquire. A
snapshot's `profile_fingerprint` is sensitive to any resolved-field
addition in future ADRs (a later ADR that adds a new ABI-affecting compile
flag to what `dumper.py` resolves must remember to fold it into D1's
fingerprint inputs, or the two silently drift apart) — this is called out
explicitly in G32 so it isn't rediscovered the hard way.

## Amendment (2026-09, clarification): dimensional comparability, not an
all-or-nothing gate

A documentation review of this ADR against `vision.md`'s support for
*intentional* cross-profile comparison (e.g. deliberately comparing an x86
build against an ARM build, or a Release against a Debug build, to answer a
narrower question than full binary interchangeability) found this ADR's
decision as shipped reads as stricter than the vision requires: today,
`ProfileMismatchError`/`ScopeMismatchError` refuse the *entire* comparison
outright whenever the two sides' extraction contracts disagree (see D2/D5
above and the "References" list's `not_comparable` call sites) — there is no
path that computes any answer for a genuinely incomparable pair, however
narrow.

This does not conflict with D1–D6's actual mechanism — the profile/scope
fingerprint comparison, the multi-TU manifest, and every `not_comparable`
call site remain the ADR's normative design — but it does mean this ADR
never described the *further* distinction the vision's use case needs. This
amendment states that distinction without changing today's shipped
behavior (a genuinely incomparable pair still refuses outright; nothing
here authorizes silently proceeding), so it does not conflict with anything
already accepted:

1. **Pair selection** — was this comparison requested between two
   *intentional* counterparts (a user explicitly asked "how does the ARM
   build compare to the x86 one"), or is it the ordinary same-profile
   release-over-release case? This is a fact about the *request*, not the
   binaries — nothing in a snapshot alone can tell them apart, and this ADR
   never modeled a way for a caller to state which one is meant.
2. **Dimension comparability** — given an intentional cross-profile pair,
   *which questions can still be answered*, and which cannot, split by
   dimension rather than treated as one pass/fail gate: symbol-table
   availability, source-level declaration facts, compiled layout facts
   (`sizeof`/offsets are only comparable within one ABI-compatible profile
   family — an x86-64 vs. AArch64 struct layout comparison is not a layout
   question with a real answer), and deployment requirements (a
   `SONAME`/runtime-floor requirement is a property of the target platform,
   not comparable cross-arch at all). A single `ProfileMismatchError`
   conflates all four into one refusal.

   **Each of the first two dimensions needs its own identity/build-context
   comparability predicate before a raw diff is trusted, not name/spelling
   equality alone** (a documentation review found the original wording of
   this item understated that): a name present in one side's export table
   and absent from the other's is only the same *question* across an
   intentional cross-profile pair when both sides' name mangling and
   decoration scheme agree (Itanium vs. MSVC mangling, or a
   target-conditioned export present under one arch's calling convention and
   not another's, is not a removal — it is two different name-encoding
   spaces that happen to share this ADR's symbol-table dimension but are not
   directly diffable by string equality). The same caveat applies to
   source-level declaration facts: "compiles under the same language
   dialect" does not by itself make two sides' header facts comparable when
   target macros, preprocessor conditionals, or the platform data model
   (e.g. LP64 vs. LLP64) can change a declared signature or field layout
   between the two profiles being compared. Until a real per-dimension
   identity/build-context predicate is implemented and used to gate the
   diff, a cross-profile symbol-table or source-declaration comparison
   stays `unverified` (item 3 below) rather than `established` — this
   amendment names the requirement; it does not itself supply the
   predicate.
3. **Result aggregation** — an intentional cross-profile comparison's report
   must distinguish, per dimension: *established* (a real comparable
   answer), *unverified* (the dimension exists on both sides but this pair's
   mismatch makes it unanswerable), and *inapplicable* (the dimension has no
   meaning for at least one side, e.g. a layout question for a header-only
   library with no compiled artifact on one side). Collapsing these three
   into either "compatible" or "not_comparable" is exactly the loss this
   amendment is naming.

Two things this amendment explicitly does **not** authorize, stated because
the failure mode on either side is a real regression:

- **It must not mean silently ignoring a real arch/compiler mismatch.** A
  same-profile comparison mistakenly run across two different ABIs must
  still refuse (or, once implemented, must still mark every layout-bearing
  finding `unverified`) — an intentional cross-profile pair result is
  opt-in via pair selection (item 1), never inferred by relaxing the
  mismatch check itself.
- **It must not let a source-level (header-declaration) comparison
  masquerade as proof of binary interchangeability.** "The two sides declare
  the same function signature" is a dimension-2 answer, not a dimension-3
  layout answer — a report must never fold the two into one verdict that
  reads as "these binaries are ABI-compatible" when only the source
  dimension was actually established.

This amendment is a clarification of decision scope, not an implementation
commitment: no code, schema, or CLI flag changes with this document. See
[ADR-065](065-comparison-scope-selection-and-completeness.md) (whose own
selection/acquisition/execution state split is the closest existing
precedent for pair-selection-as-an-explicit-input) and
`docs/contribute/plans/vision-api-abi-evolution.md` for where implementing
this split, if picked up, would be sequenced.

## References

- `abicheck/model.py` — `AbiSnapshot`, `ScopeOrigin` (`:131-147`)
- `abicheck/dumper.py:370,397,1043` — current single-aggregate-TU dump path
- `abicheck/cli_dump_helpers.py:313-431` — `DumpDepthNotSatisfiedError`,
  the existing hard-fail precedent this ADR generalizes
- `abicheck/checker_policy.py:618,1024` — `SOURCE_FACT_COVERAGE_INCOMPLETE`,
  `ReachabilityState`
- `abicheck/snapshot_cache.py:130` — existing content-hash cache key
- `abicheck/serialization.py:85,88,91-103,556-572` — `SCHEMA_VERSION`,
  `_MIN_SCHEMA_VERSION_FOR_CV_FACTS` (naming precedent), set-sorting, and the
  existing forward-version handling, which today only warns — D1 adds a
  real hard-rejection threshold rather than relying on it as-is
- `abicheck/schemas/compare_report.schema.json`,
  `tests/test_report_schema.py` — the published JSON contract D2's
  `not_comparable` state must update alongside the reporters
- `abicheck/sycl_metadata.py:234,238` — current binary-only SYCL/PI
  classification
- `abicheck/buildsource/crosscheck.py:215` — `run_crosschecks`, the merge
  shape D4 reuses
- `abicheck/buildsource/source_replay.py` — RAM-aware scheduling D6 factors
  out (see `abicheck/buildsource/CLAUDE.md`)
- [ADR-015](015-snapshot-serialization.md) (schema versioning),
  [ADR-024](024-public-abi-surface-resolution.md) (`ScopeOrigin`),
  [ADR-028](028-source-build-evidence-pack.md) D3 (authority rule),
  [ADR-035](035-pr-tier-source-intelligence-and-crosscheck.md) D4
  (`crosscheck.py`), [ADR-038](038-build-integrated-fact-collection-variants.md),
  [ADR-041](041-compiler-facts-semantic-impact-graph.md) (coverage-honesty
  pattern this ADR's gate follows), [ADR-045](045-identity-based-old-new-entity-matching.md)
  (return-type-out-of-identity precedent for D4)
- [G32](../plans/g32-comparability-contract-and-multi-tu-manifest.md) —
  phased implementation plan
