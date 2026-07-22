# ADR-049: Comparability Contract — Profile/Scope Fingerprints and the Multi-TU Manifest

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
extraction boundary. `serialization.py` already has the right mechanism for
this: an old reader rejects a snapshot whose `schema_version` is *newer*
than it supports (`serialization.py:557-567`,
`f"Snapshot schema_version {_schema_version} is newer than this abicheck..."`)
rather than silently misreading it. `SCHEMA_VERSION` (`serialization.py:85`,
currently 11) is therefore bumped to 12 the same release `contract` starts
being written, so an old reader hits that existing forward-version error
instead of silently producing a verdict on data whose comparability it has
no way to check.

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
  includes, and the `public_header_paths`/`public_header_dirs`/filtering
  policy already threaded through `dumper.py` today. Computed from the
  *normalized* manifest (D3), not raw YAML bytes — reordering two
  independent TU entries, or adding a comment, must not change the
  fingerprint; reordering includes *within* one TU must.

Both fingerprints live in a new `contract: ExtractionContract` sub-object on
`AbiSnapshot` rather than flattening two more top-level fields onto an
already-large dataclass — this is the one new nested type this ADR
introduces on the model, deliberately scoped to just the two fingerprints
plus the resolved fields that produce them (so a report can show *what*
differs, not just that the hashes don't match).

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
start, not as a follow-up). On the reporting surface (`reporter.py`,
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
stale) the moment the gate first fires.

A profile/scope fingerprint is only computed (and only gates) when both
snapshots carry one; a snapshot from before this ADR (no `contract` field)
compares exactly as it does today — this is an opt-in tightening for new
snapshots, not a retroactive break of existing baselines. `UNKNOWN_PROFILE`
is the report reason when one side has a contract and the other doesn't.

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

A base compile profile (compiler, target, language standard, global flags)
is shared across all TUs in one manifest; **different compilers or target
triples across TUs in the same manifest are rejected at parse time** — that
is two different ABI contexts, which stay two separate snapshots (and two
separate `profile_fingerprint`s) rather than one snapshot pretending to
speak for both. Only forced includes and include order vary per TU.

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
`HETEROGENEOUS_ABI_CONTEXT`. A snapshot with unresolved conflicts is not a
`CompleteSnapshot` and cannot feed D2's comparability gate as a clean side.

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
`frontend_context` (`host` by default). A run that produces only a
`spir64`/device context when `host` was requested is an extraction failure
(`AST_CONTEXT_MISSING`), not a successful-but-wrong snapshot — it must not
reach D1's fingerprinting at all. Fixture-first per the review's own
sequencing advice: a real captured multi-document DPC++ AST fixture and a
plain single-context clang fixture land before the stream parser, so the
parser is built against real output shape, not an assumption of it.

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
- `abicheck/serialization.py:85,91-103,557-567` — `SCHEMA_VERSION`,
  set-sorting, the existing forward-version rejection D1 relies on
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
