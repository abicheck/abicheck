# G32 — Comparability Contract: Profile/Scope Fingerprints and the Multi-TU Manifest

**Origin:** A review of abicheck's snapshot architecture, prompted by a
real multi-TU/DPC++ scenario (umbrella header + Arrow-derived adapter
header needing its own forced include + SYCL host/device compilation
split), found two unaddressed gaps: `dump()` collapses every requested
header into one synthetic translation unit (no per-TU forced includes, no
required-vs-optional TU semantics), and `checker.compare` has no gate that
proves two snapshots were extracted under a comparable contract before
running a symbol diff — a manifest/flag drift between two extraction runs
today produces a page of false additions/removals instead of a clear
"these two snapshots aren't comparable" result. Most of what the review
also raised (public/private/external classification, deterministic
serialization, content-hash caching, RAM-aware parallel extraction) turned
out to already be shipped under different names — see ADR-049's Context
for the full audit. This plan implements only the genuinely new decisions.

**ADR:** [ADR-049](../adr/049-comparability-contract-and-multi-tu-manifest.md)
(Proposed — records the target model; this plan carries the phased
backlog).
**Type:** Initiative plan (cross-cutting; spans `abicheck/model.py`,
`abicheck/dumper.py`, `abicheck/checker.py`, `abicheck/service.py`,
`abicheck/mcp_server.py`, `abicheck/cli.py`, `abicheck/snapshot_cache.py`,
`abicheck/sycl_metadata.py`, `abicheck/buildsource/source_replay.py`, new
top-level modules).
**Effort:** Phase 0 — S. Phase A — M. Phase B — XL. Phase C — L. Phase D —
L. Phase E — M. Total: XL, phased over multiple PRs.
**Risk:** Phase 0 — low (fixtures only, no production code path changes).
Phase A — low-medium (new gate at a well-defined entry point, additive
fields, rollout behind report-only mode before hard-fail default — see
Phase A below). Phase B — **high** — changes `dumper.py`'s single hottest
path (every `dump`/`compare` call goes through it) from one frontend
invocation to N. Phase C — medium (new merge surface, but scoped to
data `dumper.py` produces, no external-tool dependency). Phase D — medium
(needs a real captured DPC++ AST fixture before implementation can proceed
safely — see Phase 0). Phase E — low (extends an already-proven pattern
from `buildsource/source_replay.py`).

---

## Sequencing

Phase 0 first, always — it is what makes Phase D (and to a lesser extent B)
design-by-evidence instead of design-by-assumption, per the originating
review's own strongest procedural point ("don't build a stream parser
against a guessed format; capture the real thing first"). Phase A can ship
and start providing value (as a report-only signal) independently of B–E —
it does not require multi-TU support to be useful, since even today's
single-aggregate-TU snapshots have a real, checkable `profile_fingerprint`.
B, C, and D can proceed in parallel once Phase 0's fixtures exist (C
depends on B landing first; D does not depend on B or C). E depends on A
(needs the fingerprints to extend the cache key) and loosely on B (the
per-TU loop it schedules); the cache-key half of E can land right after A
without waiting for B if useful on its own.

```
Phase 0 (fixtures) ──┬──▶ Phase A (contract + gate) ──┬──▶ Phase E (scheduling + cache)
                      │                                │
                      ├──▶ Phase B (manifest/multi-TU) ─┴─▶ Phase C (compatible merge)
                      │
                      └──▶ Phase D (SYCL host/device context)
```

---

## Phase 0 — Regression fixtures and example cases

**Problem.** Every downstream phase needs ground truth to test against, and
two of them (B's merge lattice, D's AST-context selection) are exactly the
kind of thing that goes wrong when designed from a description instead of
real data.

**Goal & acceptance criteria.**
- A real captured DPC++/`clang -ast-dump=json` multi-document output
  fixture exists (from an actual `icpx`/DPC++ invocation, not synthesized),
  alongside a plain single-context clang fixture for contrast.
- A synthetic "ODR-safe" multi-TU fixture pair: one struct forward-declared
  in TU A, fully defined in TU B (must merge cleanly); one function
  declared with different return types across two TUs (must conflict).
- An "external STL noise" fixture: a public function taking
  `std::vector<int>` by value, to exercise D4's supporting-vs-reportable
  entity boundary at the merge layer (this boundary itself is ADR-024's, not
  new — the fixture just needs to exist for the merge tests to use it).
- A "scope drift" fixture pair: same manifest structure, new side adds one
  extra TU — used to assert Phase A's `SCOPE_MISMATCH` fires correctly and
  that a *report-only* mode correctly demotes it instead of hard-failing.

**Files & surfaces.** New fixtures under `tests/fixtures/g32/` (raw AST
captures, not committed as generated `.abi.json` — those are produced by
the tests themselves once Phase A/B land) and, once ChangeKinds exist
(Phase A/C), `examples/case2xx_*/` per the standard example-catalog
convention (`ground_truth.json` entry, `README.md`, AI-readiness
`examples-ground-truth`/`examples-readme-sync` checks).

**Tests.** No new production tests yet — this phase is fixture capture and
a short `tests/test_g32_fixtures.py` asserting the fixtures parse as valid
JSON / are non-empty, so a later phase's tests have something real to load
without a live DPC++ toolchain in every CI lane.

**Out of scope.** No production code changes.

---

## Phase A — `ExtractionContract`: profile/scope fingerprints and the comparability gate

Implements ADR-049 D1 and D2.

**Goal & acceptance criteria.**
- `AbiSnapshot.contract: ExtractionContract | None` (additive field,
  `model.py`) carries `profile_fingerprint`/`scope_fingerprint` plus the
  resolved inputs that produced them.
- `comparability.check_contracts_comparable(old, new)` raises
  `ProfileMismatchError`/`ScopeMismatchError` (`errors.py`) when both sides
  carry a `contract` and the fingerprints differ; a snapshot with no
  `contract` field compares exactly as it does today (backward compatible
  with every existing baseline).
- The gate is wired at **all three** entry points in one phase, closing the
  gap AGENTS.md's "Known gaps" section already names for the depth
  contract rather than repeating the CLI-only mistake: `checker.compare`
  (core), `service.py`'s `ScanRequest`/`compare_snapshots`, and
  `mcp_server.py`'s MCP compare tools.
- Reporting: `reporter.py`/`sarif.py`/`junit_report.py` gain a
  `not_comparable` top-level result distinct from every existing verdict
  value — never coerced into `compatible`/`breaking`.
- `--diagnostic-comparison` opt-in flag: downgrades the hard-fail to a
  tentative diff, every finding stamped `assurance: none`.
- **Rollout, matching this repo's existing default-on-after-validation
  pattern (ADR-041's `--header-graph` flag flip):** ships behind a
  `--strict-comparability` flag, default **off**, for one release cycle —
  a mismatch demotes to a `PROFILE_SCOPE_DRIFT_DETECTED` RISK-tier finding
  (reusing the existing `SOURCE_FACT_COVERAGE_INCOMPLETE`-style
  degrade-not-block pattern) rather than hard-failing, so real-world
  fingerprint false-positives (an overlooked resolved-field gap — see
  ADR-049 Consequences) surface as a visible warning before the gate can
  ever block a CI pipeline outright. Flip to default-on is a follow-up PR
  once the fixture corpus (Phase 0) and a real-world dry run both show
  zero unexpected mismatches.

**Files & surfaces.** `model.py` (new `ExtractionContract`), new
`abicheck/comparability.py` (fingerprint computation + gate), `errors.py`
(two new exception classes), `checker.py` (gate call at the top of
`compare`), `service.py`, `mcp_server.py`, `cli.py`/`cli_compare_release.py`
(flag + `not_comparable` exit-code handling — see `docs/reference/exit-codes.md`,
which needs a new row), `reporter.py`, `sarif.py`, `junit_report.py`.

**Tests.** Unit tests for fingerprint stability (same manifest,
independent-TU reordering unaffected; include-order-within-a-TU changes
the fingerprint); gate unit tests for all three entry points; a
`--diagnostic-comparison` end-to-end test; a backward-compat test asserting
a contract-less snapshot pair compares unchanged.

**Example fixtures.** The Phase 0 "scope drift" pair, promoted to a real
`examples/case2xx_profile_scope_mismatch_gate/` once the gate exists.

**Out of scope (deferred to later phases or explicitly not planned).**
`expected_public_headers` coverage inventory (ADR-049 non-goals) is not
part of this phase.

---

## Phase B — Manifest and real multi-TU dump

Implements ADR-049 D3. The highest-risk phase — see Risk above.

**Goal & acceptance criteria.**
- New `abicheck/dump_manifest.py`: strict YAML parser (unknown fields
  error), `roots`/`translation_units` schema, `name`-uniqueness,
  `contributes_to_abi=True ⇒ required=True` invariant enforced at parse
  time (a validation error, not a silent coercion).
- `dumper.py` gains a manifest-driven `dump()` path: one castxml/clang
  invocation per TU (shared base profile + that TU's own forced includes),
  each producing a `TuFragment`. The existing single-header CLI path
  becomes this path's one-TU special case (`legacy-main`) — same code, not
  a parallel implementation.
- A manifest declaring TUs with different compilers/target triples is
  rejected at parse time (`HETEROGENEOUS_ABI_CONTEXT` at manifest-validation
  time, before any extraction runs — cheaper failure than after a wasted
  compile).
- A required TU's compile failure is a hard extraction failure for the
  whole snapshot (`IncompleteAttempt`, never silently merged as if it
  succeeded); an optional (`required: false`) TU's failure degrades to a
  diagnostic, and — enforced by the parse-time invariant above — an
  optional TU can never be `contributes_to_abi: true`, so this can never
  produce a false removal.
- New CLI surface: `abicheck dump --manifest path/to/manifest.yml` (or
  `compare --manifest`), plus a `abicheck plan --manifest ...` diagnostic
  command that prints the normalized manifest and both D1 fingerprints
  without running extraction — cheap to run in CI before committing to a
  full dump.

**Files & surfaces.** New `abicheck/dump_manifest.py`, `abicheck/dumper.py`
(per-TU invocation loop, `TuFragment` type), new `cli_dump_manifest.py`
sibling command module (per the root `CLAUDE.md`'s "larger command → sibling
module" convention), `cli_dump_helpers.py` (extend
`resolve_dump_depth`/`check_requested_depth_satisfied` to operate per-TU).

**Tests.** Manifest parser unit tests (the invariant violation, duplicate
TU names, unknown fields, relative-path resolution). `dumper.py` multi-TU
integration tests (`@pytest.mark.integration`, needs castxml/clang) using
Phase 0's fixtures. A `plan --manifest` unit test asserting it never invokes
a compiler.

**Example fixtures.** Phase 0's ODR-safe and external-STL-noise pairs,
wired through the real manifest path end to end.

**Out of scope.** D4 (merge across TUs) is Phase C — Phase B's
`TuFragment`s are produced but not yet merged into one `AbiSnapshot`
usable by `checker.compare`; Phase B ships with a minimal "no conflicts
possible" merge (concatenate, error loudly on any duplicate `entity_key`)
as a placeholder, replaced by Phase C's real compatible-merge lattice.

---

## Phase C — Compatible merge across translation units

Implements ADR-049 D4. Depends on Phase B.

**Goal & acceptance criteria.**
- New `abicheck/tu_merge.py`: for each `entity_key` seen in more than one
  `TuFragment`, classify as trivial-merge (forward-decl + definition,
  declaration + redeclaration, default-argument-only difference — union
  provenance, keep the richer declaration), `INCONSISTENT_DECLARATION`
  (same-context conflict — different return type/layout/calling
  convention), or `HETEROGENEOUS_ABI_CONTEXT` (should Phase B's
  single-profile-per-manifest rule ever be relaxed — not expected in this
  phase).
- `entity_key` excludes return type (kept in `abi_facts`) — reuses the
  ADR-045/048 "prefer specific identity, never fold a mutable fact into the
  key" principle explicitly, not a fresh design.
- A snapshot with unresolved conflicts cannot pass Phase A's comparability
  gate as a clean side — it is not a `CompleteSnapshot`.
- Merge is deterministic regardless of TU-completion order (a required
  property, tested directly — shuffle TU processing order, assert
  byte-identical merged output).

**Files & surfaces.** New `abicheck/tu_merge.py`, reusing
`buildsource/crosscheck.py`'s existing merge/classify shape (not a new
algorithm — see ADR-049 D4). `dumper.py`'s manifest path calls this instead
of Phase B's placeholder concatenation.

**Tests.** Phase 0's ODR-safe fixture (must merge cleanly) and
conflicting-return-type fixture (must produce `INCONSISTENT_DECLARATION`);
an order-independence property test (`tests/test_detector_properties.py`
style, per the repo's existing metamorphic-test convention).

**Example fixtures.** `examples/case2xx_multi_tu_compatible_merge/`,
`examples/case2xx_multi_tu_inconsistent_declaration/`.

---

## Phase D — SYCL/DPC++ host vs. device AST context selection

Implements ADR-049 D5. Independent of Phases B/C; depends only on Phase 0's
captured DPC++ fixture.

**Goal & acceptance criteria.**
- New `abicheck/sycl_context.py`: decodes a DPC++ frontend's
  (possibly-multi-document) JSON output as a stream of `{kind, target,
  ast}` contexts — real document-boundary streaming, not a bracket/string
  split; rejects trailing garbage and truncated documents.
- Context selection is explicit: the manifest/CLI's `frontend_context`
  (`host` default) is matched against the compiler-reported target triple
  of each decoded context; a run producing only a mismatched context
  (e.g. only `spir64` when `host` was requested) is `AST_CONTEXT_MISSING`,
  an extraction failure — never a successful snapshot with the wrong
  target silently selected.
- `dumper_clang.py`'s existing single-context assumption is generalized to
  call this module when the detected frontend is DPC++-capable; a plain
  (non-SYCL) clang/castxml invocation is unaffected — zero-context-stream
  output degrades to the existing single-context path, not a new failure
  mode for the common case.

**Files & surfaces.** New `abicheck/sycl_context.py`, `dumper_clang.py`
(wiring), `sycl_metadata.py` (unaffected — this phase adds frontend-level
context selection, D5 explicitly does not touch the existing binary-symbol
classifier).

**Tests.** Fixture-driven parser tests against Phase 0's real captured
output (multi-document, malformed/truncated variants added once the happy
path is proven — matching the review's own "fixture-first, don't guess the
parser" sequencing advice). `AST_CONTEXT_MISSING`/`AST_CONTEXT_AMBIGUOUS`
error-path tests.

**Example fixtures.** None required beyond Phase 0's captures — this phase
is extraction-layer, not diff-layer; no new `ChangeKind`.

---

## Phase E — Resource-aware frontend scheduling and cache-key extension

Implements ADR-049 D6. Depends on Phase A (fingerprints for the cache key);
the scheduling half loosely depends on Phase B (the per-TU loop it
schedules) but the cache-key half can land immediately after Phase A.

**Goal & acceptance criteria.**
- The RAM-probing/pool-sizing helper in `buildsource/source_replay.py` is
  factored out into new leaf module `abicheck/process_resources.py`; both
  `source_replay.py` and `dumper.py`'s per-TU loop import it — one
  implementation, not two, per AGENTS.md's own import-cycle guidance ("move
  shared logic to a leaf module both sides can depend on").
- `dumper.py`'s per-TU castxml/clang invocations (Phase B) run under this
  pool instead of a fully sequential loop; a killed/timed-out TU records
  its exit signal and never silently retries as a clean empty TU.
- `snapshot_cache.py`'s `_cache_key()` (`:130`) gains
  `profile_fingerprint`/`scope_fingerprint` as additional key inputs — a
  pure compile-profile change with identical header content now correctly
  invalidates the cache, closing the one real gap in an otherwise-already-
  correct (content-hash-based) cache design.

**Files & surfaces.** New `abicheck/process_resources.py`,
`buildsource/source_replay.py` (import from it instead of its own inline
implementation), `dumper.py` (per-TU pool), `snapshot_cache.py`
(`_cache_key` inputs).

**Tests.** `process_resources.py` unit tests migrated from
`source_replay.py`'s existing RAM-probing tests (same behavior, new import
path — a refactor test, not new coverage). Cache-key test: identical
headers, differing `profile_fingerprint` ⇒ cache miss.

**Out of scope.** No new scheduling *policy* — this phase ports the
existing, already-proven `source_replay.py` policy verbatim; a different
policy (e.g. per-manifest-declared memory budget) is future work, not
scoped here.

---

## How to pick up this plan

1. Read [ADR-049](../adr/049-comparability-contract-and-multi-tu-manifest.md)
   in full before starting any phase — it has the authority-boundary rule
   (`The one rule that does not change`) every phase must preserve.
2. Start with Phase 0 regardless of which later phase you're aiming for.
3. Implement against each phase's acceptance criteria above; add a
   `changelog.d/` fragment per AGENTS.md convention for any
   `abicheck/**/*.py` change.
4. Update this doc's Effort/Risk line for a phase once it ships (matching
   the convention `g31`/`g19` already use — "Phase A — S, done").
