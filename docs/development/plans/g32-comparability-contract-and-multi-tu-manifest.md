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
out to already be shipped under different names — see ADR-050's Context
for the full audit. This plan implements only the genuinely new decisions.

**ADR:** [ADR-050](../adr/050-comparability-contract-and-multi-tu-manifest.md)
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
fields; ships as a hard default per ADR-050 D2 from day one — risk is
mitigated by Phase 0 fixture coverage and a pre-merge dry run, not by a
runtime soft-default — see Phase A below). Phase B — **high** — changes
`dumper.py`'s single hottest
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
a short `tests/test_g32_fixtures.py` asserting the fixtures are non-empty
and readable, so a later phase's tests have something real to load without
a live DPC++ toolchain in every CI lane. The multi-document DPC++ capture
is **not** asserted to parse as one JSON value — by design it's a stream
of concatenated documents, the exact shape Phase D's stream parser exists
to handle; requiring single-document JSON-parseability here would reject
the real capture and push Phase 0 toward synthesizing a fake one,
undermining the fixture-first point of this phase. It's validated only for
non-emptiness now, and by the real stream parser once Phase D exists.

**Out of scope.** No production code changes.

---

## Phase A — `ExtractionContract`: profile/scope fingerprints and the comparability gate

Implements ADR-050 D1 and D2.

**Goal & acceptance criteria.**
- `AbiSnapshot.contract: ExtractionContract | None` (`model.py`) carries
  `profile_fingerprint`/`scope_fingerprint` plus the resolved inputs that
  produced them. `scope_fingerprint`'s hashed inputs include each TU's
  `required`/`contributes_to_abi` flags, not just its includes/forced
  includes (ADR-050 D1) — flipping `contributes_to_abi` changes which
  declarations feed the ABI model without necessarily touching a TU's
  includes, so leaving it out of the hash would let that exact class of
  scope drift pass the gate undetected.
- **Both fingerprints hash root-relative paths, never raw absolute ones —
  the single highest-priority correctness requirement in this phase.**
  `compare`'s side-scoped `--header old=v1/foo.h --header new=v2/foo.h` /
  `--include old=inc1 --include new=inc2` (ADR-040) is the ordinary
  two-checkout compare workflow, and its old/new sides necessarily resolve
  to different absolute paths even for an identical logical surface.
  Hashing absolute paths directly would fingerprint-mismatch and hard-fail
  *every routine compare* as `not_comparable` — breaking the gate's primary
  use case on day one. Each side's paths are normalized relative to that
  side's own resolution root (legacy CLI path: the common ancestor
  **directory** of that side's inputs — each header's *parent* directory
  plus each include directory itself, **not** the header paths taken
  literally; manifest path: the manifest file's own directory) before
  hashing (ADR-050 D1). Deriving the root from header paths directly instead
  of their parents breaks the single-header-per-side case — the common
  ancestor of a one-element path set is that whole path, so `old=v1/foo.h`
  and `new=v2/bar.h` would both normalize to the same empty marker and hash
  identically despite being different scopes, the opposite failure from the
  one this fix exists to close; taking the parent directory first preserves
  the filename (`v1/foo.h` → root `v1/`, normalized `foo.h`). A dedicated
  test using `--header old=v1/foo.h --header new=v2/foo.h` against
  logically identical trees under different roots, asserting the resulting
  `profile_fingerprint`s match — **and** a second test asserting
  `old=v1/foo.h`/`new=v2/bar.h` (genuinely different header names) produce
  *different* fingerprints — is non-negotiable for this phase to be
  considered done.
- **Modeling `contract` is not the same as populating it — this phase must
  do both.** `dump()` (`dumper.py`) is the one place that already resolves
  every input both fingerprints need; it calls
  `comparability.compute_extraction_contract(...)` and attaches the result
  to the `AbiSnapshot` it returns, for every dump (not only a manifest-
  driven one — Phase B). Without this, `contract` stays `None` on every
  freshly-produced snapshot, and since the gate below only ever raises when
  **both** sides carry one, two ordinary dumps would silently take the same
  code path as the intentionally-lenient mixed-pair case forever — a fully
  specified, fully inert gate.
- **The whole-snapshot cache is the same bypass by a different route — this
  phase closes it too, not just Phase E's later cache-key work.**
  `service_dump_cache.cached_run_dump` looks up `snapshot_cache` *before*
  calling `dump()`; a warm cache entry from a pre-Phase-A abicheck (no
  `contract` computed) served after upgrading would still come back with
  `contract=None`, defeating the fix above through a different code path.
  `snapshot_cache._SNAPSHOT_CACHE_VERSION` (`:48`, currently `"3"`) is
  bumped in this same phase so every pre-Phase-A cache entry misses once
  and gets rebuilt through the now-`contract`-populating `dump()`. This is
  separate from Phase E's later `profile_fingerprint`/`scope_fingerprint`-
  as-cache-key work (a different gap: a pure profile change with identical
  headers not invalidating) and cannot be deferred to it without leaving
  the gate inert for every warm-cache user until Phase E ships.
- `serialization.SCHEMA_VERSION` is bumped (11 → 12) in the same change
  that starts writing `contract` — **not** treated as a free additive field
  the way ADR-041's advisory `extractor_passes`/`narrowed_passes` were. The
  bump alone is not sufficient: `snapshot_from_dict`'s existing
  newer-than-supported handling (`serialization.py:556-572`) only calls
  `warnings.warn(...)` and keeps deserializing — it never raises, so an old
  reader would print an easily-missed warning and still produce an ordinary
  verdict on a `contract`-bearing snapshot it can't check. This phase adds a
  real guard alongside the bump: a new
  `_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION = 12` constant (same naming
  convention as the existing `_MIN_SCHEMA_VERSION_FOR_CV_FACTS`) and a new
  `IncompatibleSnapshotSchemaError` (`errors.py`), raised by
  `snapshot_from_dict` *before* the existing warn-only branch when the
  snapshot's `schema_version` is at or above that threshold and the running
  `SCHEMA_VERSION` is below it. Versions below the threshold keep today's
  warn-and-continue behavior unchanged — only the specific version that
  first introduces a verdict-blocking field becomes a hard failure for an
  older reader (ADR-050 D1).
- `comparability.check_contracts_comparable(old, new)` raises
  `ProfileMismatchError`/`ScopeMismatchError` (`errors.py`) **only when
  both sides carry a `contract`** and the fingerprints differ. A **mixed**
  pair (one side has a `contract`, the other doesn't) is unambiguous, not
  an implementer's judgment call: it takes the exact same code path as a
  pair where neither side has one — never hard-fails, never becomes
  `not_comparable` — so comparing a freshly-produced snapshot against a
  pre-ADR stored CI baseline (a common real workflow) never regresses on
  upgrade — **including under strict severity settings, not only the
  default ones.**
- **`UNKNOWN_PROFILE` is report-level metadata, not a `ChangeKind`/`Change`
  finding — this took two wrong designs to converge on, worth getting
  right the first time here.** Classifying it `RISK_KINDS` (matching
  `SOURCE_FACT_COVERAGE_INCOMPLETE`'s shape) broke under
  `--severity-potential-breaking=error`/`--severity-preset strict`
  (promotes to exit 2). Reclassifying it `COMPATIBLE_KINDS`'s
  `QUALITY_KINDS` instead only relocated the same collision:
  `--severity-quality-issues=error`/`--severity-preset strict` promotes
  `QUALITY_KINDS` too (exit 1) — proving no `ChangeKind` category is
  permanently severity-immune, since severity gating can reach any of them
  by design. `UNKNOWN_PROFILE` is instead a new field on the comparison
  result (e.g. `contract_coverage: "partial"`), alongside the existing
  `assurance` field this same phase adds for `--diagnostic-comparison` —
  never entering the `changes`/findings list any `--severity-*` flag scans,
  so it's structurally unreachable by severity promotion rather than
  merely unreachable by the flags checked so far.
- **The exit code is part of the published contract too, not an
  afterthought.** `docs/reference/exit-codes.md` documents two co-existing
  `compare` schemes (legacy: 0/2/4; severity-aware: 0/1/2/4) where `0`
  means *compatible* in both. `not_comparable` must never exit `0` in
  either scheme — otherwise the exact "missing evidence reads as safe"
  failure this ADR exists to prevent reappears at the process-exit
  boundary, undoing the JSON-level fix. This phase reserves one new,
  distinct nonzero code and adds it as its own row to both tables in
  `docs/reference/exit-codes.md`, not folded into either scheme's existing
  numbering.
- The gate is wired at **all four** entry points in one phase, closing the
  gap AGENTS.md's "Known gaps" section already names for the depth
  contract rather than repeating the CLI-only mistake: `checker.compare`
  (core), `service.py`'s `ScanRequest`/`compare_snapshots`,
  `mcp_server.py`'s MCP compare tools, **and** `cli_compare_release.py`'s
  directory/package fan-out.
- **The release fan-out needs a dedicated fix, not inherited behavior.**
  `_compare_one_library` (`cli_compare_release.py:180-269`) wraps its
  entire per-library flow in `except (click.ClickException,
  click.UsageError):` / `except Exception:`, both returning
  `{"verdict": "ERROR", ...}` — documented at `:1142` as flooring the
  release's exit code at 4 regardless of severity settings.
  `ProfileMismatchError`/`ScopeMismatchError` are plain exceptions, so
  today's broad `except Exception` would swallow them into that same
  `"ERROR"`/exit-4 bucket — one incomparable library in a release would
  silently report as the *worst possible* classification (an ABI break)
  instead of `not_comparable`, inverting this ADR's purpose on its one
  multi-library surface. `_compare_one_library` gains a dedicated
  `except (ProfileMismatchError, ScopeMismatchError) as exc:` branch,
  ordered before the generic `except Exception`, returning
  `{"verdict": "not_comparable", "reason": ...}`; the release aggregator
  and `docs/reference/exit-codes.md`'s multi-library section recognize
  that verdict value distinctly, not folded into `"ERROR"`.
- Reporting: `reporter.py`/`sarif.py`/`junit_report.py` gain a
  `not_comparable` top-level result distinct from every existing verdict
  value — never coerced into `compatible`/`breaking`.
- The published JSON contract moves with the reporters, in this phase, not
  after: `abicheck/schemas/compare_report.schema.json` currently requires
  `verdict` and restricts it to a fixed string enum with no `null` member.
  It's updated to allow `verdict: null` alongside the new `not_comparable`
  state (and a `reason` object), and `tests/test_report_schema.py` — which
  already validates emitted reports against this exact file — gains a case
  for a `not_comparable` report. Shipping the reporter change without this
  either emits JSON that fails its own published schema, or ships a stale
  schema — not an acceptable outcome for either. The schema's own version
  metadata moves in lockstep, not as an afterthought:
  `abicheck/schemas/__init__.py`'s `REPORT_SCHEMA_VERSION` (currently
  `"2.12"`, emitted in every report as `report_schema_version`) is bumped,
  and the published mirror `docs/schemas/v1/compare_report.schema.json` is
  regenerated via the existing `scripts/publish_schemas.py` — skipping
  either fails `tests/test_report_schema.py::test_docs_mirror_matches_packaged_schema`,
  which already asserts the two stay byte-identical.
- `--diagnostic-comparison` opt-in flag: downgrades the hard-fail to a
  tentative diff, every finding stamped `assurance: none`.
- **Rollout: the hard gate is the default from the first shipped version of
  this phase — no soft-launch flag, and no second flag with a default that
  contradicts ADR-050 D2.** D2 is explicit that a contract mismatch is a
  precondition failure producing `not_comparable`, never an ordinary
  verdict with a RISK-tier finding attached; a runtime default that quietly
  downgraded that to "warn, still produce a verdict" would ship exactly the
  behavior the ADR forbids. The two things that *do* need to be true before
  this phase merges — real-world fingerprint false positives from an
  overlooked resolved-field gap must not exist in practice — are handled at
  merge-review time, not at runtime: Phase 0's fixture corpus must cover the
  common drift/no-drift cases, and a dry run over a real multi-snapshot
  corpus using the **already-specified** `--diagnostic-comparison` flag
  (D2's one sanctioned escape hatch, not a new one) must show zero
  unexpected mismatches before Phase A is considered done. Backward
  compatibility for every *existing* baseline is unaffected regardless,
  since a snapshot with no `contract` field (everything produced before
  this phase) compares exactly as it does today (see the bullet above) —
  there is no legacy flow this gate could break on day one, unlike
  ADR-041's header-graph flag flip, which changed behavior for an
  already-common default-off-to-on transition.

**Files & surfaces.** `model.py` (new `ExtractionContract`), new
`abicheck/comparability.py` (fingerprint computation, `compute_extraction_contract`,
the gate, and `contract_coverage` metadata computation — no detector, since
`UNKNOWN_PROFILE` is report metadata, not a `Change`), `dumper.py` (`dump()`
calls `compute_extraction_contract(...)` and attaches it to every returned
snapshot — see the acceptance-criteria bullet above; this is not optional
plumbing), `snapshot_cache.py` (`_SNAPSHOT_CACHE_VERSION` bump — see the
warm-cache acceptance-criteria bullet above), `errors.py`
(`ProfileMismatchError`/`ScopeMismatchError`/`IncompatibleSnapshotSchemaError`),
`serialization.py` (`SCHEMA_VERSION` bump,
`_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION` threshold + the
`snapshot_from_dict` hard-rejection branch, `contract` round-trip through
`snapshot_to_dict`/`snapshot_from_dict`), `checker.py` (gate call at the
top of `compare`, `contract_coverage` field on the result), `service.py`,
`mcp_server.py`, `cli.py` (flag + the new,
distinct `not_comparable` exit code), `cli_compare_release.py`
(`_compare_one_library`'s dedicated
`except (ProfileMismatchError, ScopeMismatchError)` branch, ordered before
`except Exception` — see the release-fan-out acceptance-criteria bullet
above; this is not covered by the CLI's own exit-code handling, it is a
separate call path), `docs/reference/exit-codes.md` (a
new row in both the legacy and severity-aware tables, **and** the
multi-library section), `reporter.py`,
`sarif.py`, `junit_report.py`, `abicheck/schemas/compare_report.schema.json`,
`abicheck/schemas/__init__.py` (`REPORT_SCHEMA_VERSION` bump),
`docs/schemas/v1/compare_report.schema.json` (regenerated via
`scripts/publish_schemas.py`, not hand-edited).

**Tests.** A `dump()`-level test asserting a real (non-manifest) dump
returns a snapshot with a populated, non-`None` `contract` — the specific
gap that would otherwise leave the gate permanently inert. A **warm-cache**
regression test: seed `snapshot_cache` with a pre-bump-version entry (no
`contract`), call `cached_run_dump` for the same inputs post-bump, and
assert it misses and rebuilds with `contract` populated rather than
serving the stale hit — the cache-layer analogue of the `dump()` test
above, closing the same class of bypass through a different code path.
Unit tests for fingerprint stability (same manifest,
independent-TU reordering unaffected; include-order-within-a-TU changes
the fingerprint; flipping one TU's `contributes_to_abi` or `required` flag
with its includes held identical also changes `scope_fingerprint`); a
hard-rejection test asserting a pre-bump reader (a stubbed/patched
`SCHEMA_VERSION` below the threshold) raises `IncompatibleSnapshotSchemaError`
on a schema-12 `contract`-bearing snapshot instead of the pre-existing
warn-and-continue path; a regression test pinning that a schema bump
*below* the threshold still only warns (today's lenient behavior for
ordinary additive fields must not become accidentally stricter);
`tests/test_report_schema.py` gains a `not_comparable` case validated
against the updated `compare_report.schema.json`, and its existing
`test_docs_mirror_matches_packaged_schema` must still pass against the
regenerated `docs/schemas/v1` copy; a root-relative-path fingerprint test
(the acceptance-criteria bullet above — same-tree-different-root compare
must not fingerprint-mismatch); an exit-code test
asserting `not_comparable` returns the new dedicated code, never `0`, from
both the legacy and severity-aware `compare` invocations; a **release
fan-out** test asserting a `not_comparable`-triggering library inside a
directory/package `compare` reports `verdict: "not_comparable"` in its
release-level entry, not `"ERROR"` — the specific inversion (incomparable
reported as the worst-possible classification) this phase must close on
its fourth entry point; gate unit tests for all
four entry points; a `--diagnostic-comparison` end-to-end test; a
backward-compat test asserting a contract-less snapshot pair compares
unchanged; a **mixed-pair** test (one side `contract`, one side none)
asserting the comparison never hard-fails and instead carries a
`contract_coverage: "partial"` report field alongside an otherwise-ordinary
verdict — the specific case the ADR calls out as unambiguous, not left to
interpretation; a **severity-neutrality** test asserting a mixed-pair
comparison run under *every* `--severity-*` flag (`--severity-potential-breaking=error`,
`--severity-quality-issues=error`, and `--severity-preset strict`) still
exits successfully — proving `contract_coverage` is structurally outside
the findings list any severity flag scans, not merely untested against the
one flag checked last time.

**Example fixtures.** The Phase 0 "scope drift" pair, promoted to a real
`examples/case2xx_profile_scope_mismatch_gate/` once the gate exists.

**Out of scope (deferred to later phases or explicitly not planned).**
`expected_public_headers` coverage inventory (ADR-050 non-goals) is not
part of this phase.

---

## Phase B — Manifest and real multi-TU dump

Implements ADR-050 D3. The highest-risk phase — see Risk above.

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
module" convention) — registering it is not implicit: `cli.py`'s bottom
side-effect `from . import (...)` block gains `cli_dump_manifest`, or the
new `plan --manifest`/`dump --manifest` commands never attach to `main` at
all, and `pyproject.toml`'s `disallow_untyped_decorators = false` override
list gains `abicheck.cli_dump_manifest` alongside the existing per-module
entries, or the typed-decorator mypy lane fails on its `@click` decorators
(both required steps of the root `CLAUDE.md`'s "Adding a new top-level
command" procedure, steps 3–4) — `cli_dump_helpers.py` (extend
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

Implements ADR-050 D4. Depends on Phase B.

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
algorithm — see ADR-050 D4). `dumper.py`'s manifest path calls this instead
of Phase B's placeholder concatenation.

**Tests.** Phase 0's ODR-safe fixture (must merge cleanly) and
conflicting-return-type fixture (must produce `INCONSISTENT_DECLARATION`);
an order-independence property test (`tests/test_detector_properties.py`
style, per the repo's existing metamorphic-test convention).

**Example fixtures.** `examples/case2xx_multi_tu_compatible_merge/`,
`examples/case2xx_multi_tu_inconsistent_declaration/`.

---

## Phase D — SYCL/DPC++ host vs. device AST context selection

Implements ADR-050 D5. Independent of Phases B/C; depends only on Phase 0's
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

Implements ADR-050 D6. Depends on Phase A (fingerprints for the cache key);
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

1. Read [ADR-050](../adr/050-comparability-contract-and-multi-tu-manifest.md)
   in full before starting any phase — it has the authority-boundary rule
   (`The one rule that does not change`) every phase must preserve.
2. Start with Phase 0 regardless of which later phase you're aiming for.
3. Implement against each phase's acceptance criteria above; add a
   `changelog.d/` fragment per AGENTS.md convention for any
   `abicheck/**/*.py` change.
4. Update this doc's Effort/Risk line for a phase once it ships (matching
   the convention `g31`/`g19` already use — "Phase A — S, done").
