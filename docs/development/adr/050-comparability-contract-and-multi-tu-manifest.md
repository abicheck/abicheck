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

**`profile_fingerprint`'s `-I` directories use the *same* parent-directory
rule as headers, uniformly — this went through two wrong "clever" fixes
before landing on the simple one, worth recording so it isn't
rediscovered.** A first attempt applied the header rule unchanged (root =
the `-I` directory's own parent), which was right for the common
real-world shape — `--include old=old/include --include new=new/include`
(the project's own include root, exactly the ADR-040 "same project, two
checkouts" case the header rule is designed for; the [user-guide's
real-world compare
example](../../user-guide/real-world-example.md) uses this exact shape) —
but wrong for a lone *external dependency* directory:
`--include old=/opt/dep-v1/include --include new=/opt/dep-v2/include`
would normalize both to `include` relative to their own root
(`/opt/dep-v1`, `/opt/dep-v2`), silently erasing a genuine dependency-
version difference. A second attempt tried to fix that by hashing each
`-I` directory's last two path components instead
(`/opt/dep-v1/include` → `dep-v1/include`) — this broke the *other*
direction: it made the ordinary `old/include` vs. `new/include` project-
root case (the common, documented workflow) hash as different, hard-
failing the routine two-checkout compare this whole fingerprint design
exists to keep working.

Both attempts failed for the same underlying reason: **whether a
differently-rooted `-I` path means "same dependency, different checkout
mount point" (should normalize) or "a genuinely different dependency"
(should not) is not decidable from path shape alone, and no heuristic can
resolve it — the two examples above have *identical* shape** (`.../X →
.../include`, two segments, differing prefix) and opposite correct
answers. `profile_fingerprint` therefore uses the header rule as-is (root
= common ancestor of that side's `-I` directories' own parent
directories, single or multiple, no special case) rather than a bespoke
heuristic invented to split a difference that path shape cannot express.
This is a **known, accepted limitation, not a solved problem**: it
correctly keeps the common project-include-root workflow working (the
thing that matters for the gate's primary use case), at the cost of not
being able to detect a dependency-version change expressed purely as a
different `-I` mount point with the same basename — that class of drift
is undetectable by this fingerprint on the legacy CLI path. The
manifest-driven path (D3) has no such gap, since every manifest-declared
path is relative to one explicit document rather than inferred from
directory shape — a user who needs reliable dependency-version detection
without a manifest has `--diagnostic-comparison` (D2) as the sanctioned
fallback, not a silent guess in either direction.

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
D6's later `profile_fingerprint`/`scope_fingerprint`-as-cache-key-input
work: that closes a *different* gap (a pure compile-profile change with
identical header content not invalidating the cache); this one closes
"the cache doesn't know `contract` exists yet at all," and cannot wait for
D6's phase without leaving the gate inert for every warm-cache user in the
interim.

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

On the reporting surface (`reporter.py`,
`sarif.py`, `junit_report.py`), a `not_comparable` result is a distinct
top-level state — `verdict: null`, a `reason` object naming the mismatched
fingerprint field(s) — never coerced into `COMPATIBLE`/`BREAKING`'s existing
enum values. A `--diagnostic-comparison` opt-in flag (default off) downgrades
the hard-fail to a tentative diff with `assurance: none` stamped on every
finding, for exploratory use — never the default, and never silent.
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

`snapshot_cache.py`'s existing content-hash cache key (`:130`) gains the
`profile_fingerprint`/`scope_fingerprint` as additional key inputs — the
cache already invalidates correctly on header-content drift (the review's
"shadowing header" scenario is not a real gap here, see Context); it does
not yet invalidate on a pure compile-profile change with identical header
content, which the two new fingerprints close.

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
