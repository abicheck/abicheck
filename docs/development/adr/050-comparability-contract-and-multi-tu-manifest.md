# ADR-050: Comparability Contract — Profile/Scope Fingerprints and the Multi-TU Manifest

**Date:** 2026-07-22
**Status:** Proposed — not implemented. This ADR records the target model and
component surface; [G32](../plans/g32-comparability-contract-and-multi-tu-manifest.md)
carries the phased implementation backlog.
**Decision maker:** (pending — recorded per repository convention.)

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
requires: `IncompatibleSnapshotSchemaError` (`errors.py`) is raised when
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
  otherwise" principle) rather than silently ignored.
- `scope_fingerprint: str` — a `sha256:`-prefixed digest of the
  **manifest-normalized** analysis scope: the set of translation units (by
  `name`, not by list position), each TU's ordered includes and forced
  includes, each TU's `required`/`contributes_to_abi` flags, and the
  `public_header_paths`/`public_header_dirs`/filtering policy already
  threaded through `dumper.py` today. The `contributes_to_abi` flag is a
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
compare example](../../user-guide/real-world-example.md) uses this exact
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
documented real-world workflow (`docs/user-guide/real-world-example.md:61-63`)
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
`split_sided_include_paths(include_pairs)`. `dump_cmd`'s inline
registration switches to the *same* `SidedIncludePathParam`, rather than
inventing a second, `dump`-only label grammar: `dump` has no old/new side
to scope, but reusing one type keeps one label syntax across all three
commands instead of asking users to remember a different one for `dump`
(`--include old:support=path` on `compare`/`scan`, `--include
both:support=path` on `dump` — side is parsed but ignored downstream,
since `dump` has nothing to split by side in the first place; the label
and path are what `dump` actually consumes). A bare, colon-less
`--include` value on `dump` behaves exactly as it does today (label=None,
side ignored) — inventing a colon-less-on-`dump`-only label shape would
reopen the exact bare-`LABEL=PATH` ambiguity just closed above, for no
real benefit.
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
not just that the hashes don't match — D1), and when the *only* differing
fields are target triple/pointer width/endianness, the gate does not
raise — it lets `compare()` proceed to the normal `diff_*` pipeline, where
`diff_platform.py`'s existing detectors take over and produce their own,
already-correct verdict. Any *other* differing field (compiler family,
macros, `-I` content, language standard) still hard-fails exactly as
before, even if a target difference happens to co-occur with it — this
carve-out is scoped to the platform-identity fields alone, not a general
loosening of the gate.

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
D2 intends at the `service.py` boundary. But `scan`'s own CLI command
(`cli_scan.py`'s `scan_cmd`) has an independent, narrower exit-code
contract (`0`/`2`/`4`/`5`/`64`, `docs/reference/exit-codes.md`) and its own
`try`/`except` around the `run_scan_core` call that today only catches
`_BudgetOverflow` and `_EvidenceContractError` — not
`ProfileMismatchError`/`ScopeMismatchError`, which would propagate
uncaught out of `scan_cmd` entirely, an unhandled traceback rather than
any of `scan`'s documented exit codes. Listing only `service.py`'s
`ScanRequest`/`compare_snapshots` as "the gate reaches `scan`" describes
where the exception passes through cleanly, not where `scan`'s own CLI
command classifies it into a deliberate outcome — the same distinction
already drawn for `compat/cli.py` above. `scan_cmd` gains a dedicated
`except (ProfileMismatchError, ScopeMismatchError) as exc:` branch
alongside its existing two, exiting **`6`** — the next integer after
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
leave implicit.** AGENTS.md's own module map lists it alongside
`reporter.py`/`sarif.py`/`junit_report.py` under "Reporting," and
`service_render.py`'s format dispatch (`:87-99`) routes `--format html` to
`generate_html_report(result: DiffResult, ...)` exactly like the other three
route to their renderers. Two distinct gaps follow from `generate_html_report`
requiring a real `DiffResult`: for the hard-gate `not_comparable` case, no
`DiffResult` exists at all (the gate raises before any diff runs), so
`service_render.render_output` must not attempt to call `generate_html_report`
on that path — the front-end's exception handler renders (or declines to
render) HTML the same way it assembles `verdict: null` JSON, rather than
`generate_html_report` growing an optional-`DiffResult` parameter it was never
designed to accept. For the mixed-pair `contract_coverage` case, a real
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
mirror `docs/schemas/v1/compare_report.schema.json` is regenerated via the
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

**Mixed pairs (one side has a `contract`, the other doesn't) never hard-fail
— this is unambiguous, not left to implementer discretion.** The backward-
compatibility promise ("a snapshot from before this ADR compares exactly as
it does today") is not a soft goal to reconcile with the gate; a
contract-less snapshot's *absence* of evidence is exactly the "missing
evidence must never manufacture a block" situation ADR-028 D3's authority
rule already covers, extended here to the comparability contract instead of
symbol facts. `check_contracts_comparable` therefore only ever raises
`ProfileMismatchError`/`ScopeMismatchError` when **both** sides carry a
`contract` and it mismatches — a mixed pair takes the exact same code path
as a pair where neither side carries one, and comparing a newly-produced
snapshot against a pre-ADR baseline (the common "upgrade abicheck, keep the
stored CI baseline" workflow) never regresses into an unexpected
`not_comparable` result. `UNKNOWN_PROFILE` is **not** a `not_comparable`
reason and never blocks: it is a non-authoritative annotation on
an otherwise-ordinary verdict, surfaced only for a mixed pair, to tell the
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

### D3. Manifest and real multi-TU dump

New `abicheck/dump_manifest.py`: a strict YAML parser (unknown fields are
errors, not silently ignored) for a `roots` / `translation_units` document —
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
Three outcomes, all extraction-time, none reaching D1's fingerprinting:

- **Exactly one decoded context has the requested `kind`** — the normal
  case, selected and passed on to normal extraction.
- **Zero decoded contexts have the requested `kind`** — `AST_CONTEXT_MISSING`
  (e.g. only a `spir64`/device context when `host` was requested).
- **More than one decoded context shares the requested `kind`** —
  `AST_CONTEXT_AMBIGUOUS`, never resolved by picking the first, the
  smallest, or any other implicit tiebreaker; an ambiguous frontend output
  is exactly the kind of "the extraction can't prove what it captured"
  situation this ADR's authority rule (ADR-028 D3) says must not be
  silently resolved in either direction.

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
`buildsource/CLAUDE.md`. Rather than a second implementation in `dumper.py`,
the RAM-probing/pool-sizing helper is factored out of `source_replay.py`
into a new leaf module, `abicheck/process_resources.py`, that both
`source_replay.py` and `dumper.py`'s new per-TU invocation loop (D3) import
— the "move the shared logic to a leaf module both sides can depend on"
rule AGENTS.md's import-cycle guidance already states, applied here instead
of growing a second scheduler. `dumper.py`'s per-TU castxml/clang calls run
under this pool instead of today's fully sequential loop; a killed/timed-out
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
already covered: the manifest-driven `scope_fingerprint` inputs (TU
`required`/`contributes_to_abi` flags, D3), which the existing
`iter_cache_header_files` walk has no way to see since they aren't
filesystem content at all.

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
