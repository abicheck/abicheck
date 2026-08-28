---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G41 — Baseline/candidate context parity and a declarative assurance contract

## Problem

An external upstream-only review (base commit `327df7b5616bcfaea8c330aad418b796c17f3970`,
including merged PRs #860 and #883) found that two previously-tracked
declarative-project blockers are now closed — per-target declared evidence
routing (`check-project.yml` resolving `targets[].evidence.path`, rejecting
unsafe shared/inferred packs) and candidate-side `consumer_compile`
extraction (a dedicated candidate dump parsing the producer binary under the
declared client compiler/frontend context, G34 Phase 0) — but found four
narrower, still-open correctness gaps that the review explicitly asks to be
treated as **one coordinated initiative**, because together they establish a
single invariant this project does not yet hold end to end:

> Old and new sides are extracted from the same resolved target, headers,
> compiler context, evidence context, and assurance contract.

Each gap below is independently observable and independently fixable, but
none of them is complete in isolation — a baseline that carries the right
consumer-compiler context is still useless if the project schema has no way
to *require* that it was actually produced under source-depth evidence, and
neither matters if the real `dump` execution path can silently diverge from
whatever was resolved for `--dry-run`. This plan sequences the four as
phases with a shared acceptance harness rather than four independent PRs
with no shared invariant to check against.

Cross-references, so this plan doesn't duplicate what's tracked elsewhere:

- G34 ([`g34-producer-consumer-compiler-profile-separation.md`](g34-producer-consumer-compiler-profile-separation.md))
  already owns the `profiles.<id>.consumer_compile` schema, `RunPlanCheck`
  projection, and the candidate-side dump this review confirms is done.
  Phase 1 below is the missing other half: the **baseline** side never
  receives the identical projection.
- The root `AGENTS.md`'s "PR C (typed `dump`/`scan` convergence...)" known-gap
  entry is the primary source of truth for Phase 4's current state — it
  already documents, in detail, which sub-blockers are closed
  (`resolve_dump_request`/`execute_dump_request` split; `scan`'s candidate
  resolver migrated; several dump-vs-scan/dump-vs-typed-API divergences
  found and fixed by direct measurement) and which two remain (the
  `--compile-db-filter` typed-API gap — now also closed per that same
  entry's later update — and castxml's unavailability in every environment
  this work has been done in, which blocks verifying the real `dump` CLI
  execution path). Phase 4 here is the scheduling/acceptance wrapper around
  that already-detailed work, not a restatement of it.
- `docs/contribute/plans/cli-cleanup-phase-two.md`'s "New since the plan was
  written — `--require-complete-analysis`" section is the closest existing
  work to Phase 3's assurance contract; read it before starting Phase 3 to
  avoid re-deriving a design it may have already partially settled.

## Goal & acceptance criteria

1. Baseline publication (`publish-baseline.yml`, `update-main-baseline.yml`)
   resolves the *same* `ResolvedExtractionContext` — profile, toolchain
   bindings, target `build-output.json`, requested depth, and
   `consumer_compile` overlay — that candidate-side checking already
   resolves, and persists enough of it in the baseline manifest that a later
   `check-target` can tell whether a given baseline is even eligible to be
   compared against a given candidate resolution before running the compare
   at all (not merely receiving `NOT_COMPARABLE` after the fact).
2. `RunPlanCheck` carries target-specific `public_header_roots`,
   `generated_header_roots`, `include_dirs`, and `compile_context`, sourced
   from the current profile's validated `build-output.json`, and
   `check-project.yml` forwards them per-target instead of one
   workflow-global `header:`/`old-header:`/`new-header:` input.
3. The project schema can declare a minimum acceptable evidence assurance
   per check (at minimum, `require_complete_analysis: true`; ideally a
   structured `assurance:` block), and the aggregate distinguishes a
   compatibility failure from an assurance failure from an operational
   failure from a missing-report coverage failure — a clean `NO_CHANGE`
   compatibility verdict must never silently erase an assurance failure.
4. The real `dump` CLI execution (ELF/PE/Mach-O) routes through the same
   `DumpRequest` → `resolve_dump_request()` → `execute_dump_request()`
   pipeline that `--dry-run` already renders from, so the plan a dry run
   describes is provably the plan that executes — closing the divergence
   the pipeline's own module docstring still documents as open.

### Acceptance tests (one per phase, all must pass before this plan is
considered done — each is lifted directly from the review, not rewritten)

- **Phase 1**: build one `.so` with GCC and declare two client profiles (a
  GCC client, a Clang client). Introduce a header change that breaks only
  one client. Both profiles must complete a real accepted-main comparison;
  one must be red and the other green, and neither may return
  `NOT_COMPARABLE`.
- **Phase 2**: two targets in one profile with different header roots and
  different generated headers. A default-argument or macro change in target
  A must be detected only for A, with no workflow-global header input
  involved.
- **Phase 3**: request `depth: source`, deliberately remove one target's
  facts pack, keep an otherwise byte-identical binary. The report must stay
  readable and show the achieved (lower) depth, but the project gate must
  fail because the declared assurance contract was not met.
- **Phase 4**: for every supported input shape, the `dump` CLI, the typed
  Python API, the baseline Action, and `compare`'s implicit-dump operand
  must produce equivalent normalized snapshots and fingerprints, and the
  `--dry-run` plan must describe the exact execution that follows.

## Design

### Phase 1 — consumer-context-aware baseline generation

Today:

```
.abicheck.yml + profile + toolchain bindings + target build-output + depth
        ↓ (candidate side only)
ResolvedExtractionContext
        ↓
candidate dump  (consumer_compile applied)     baseline dump (build-output.json only)
```

Target:

```
.abicheck.yml + profile + toolchain bindings + target build-output
  + check depth + consumer_compile
        ↓
ResolvedExtractionContext
        ↓                              ↓
   baseline dump                  candidate dump
```

`publish-baseline.yml`/`update-main-baseline.yml` currently build the
old-side snapshot purely from `build-output.json` — they never resolve a
run plan or apply a client-compiler overlay, so a `consumer_compile`-scoped
project produces:

```
old:  producer compiler/header context
new:  consumer compiler/header context
```

and the comparability gate correctly rejects the pair as `NOT_COMPARABLE`
rather than silently comparing mismatched snapshots — which is the right
failure mode, but it means "same binary, different client compiler" is not
operational for accepted-main or release baselines at all today. Fix this by
having baseline publication call the same run-plan resolution
(`abicheck/buildsource/run_plan.py`'s `generate_run_plan`/`RunPlanCheck`
projection) candidate checking already uses, and by threading the resolved
`consumer_compile_*` fields into whichever dump invocation
`publish-baseline.yml`/`update-main-baseline.yml` shell out to — the same
`--gcc-path`/`--gcc-options`/`--ast-frontend` forwarding `check-project.yml`
already does for the candidate side (see `abicheck/buildsource/run_plan.py`
lines documenting `consumer_compile_gcc_path`/`consumer_compile_gcc_options`/
`consumer_compile_ast_frontend`/`consumer_compile_active`).

**The manifest schema/serialization/selection-key logic belongs in
`abicheck/storage/`, not grown inline in `buildsource/baseline_publish.py`/
`baseline_set.py`.** Per ADR-061's routing table and `architecture/
modules.yaml`'s own `storage` layer definition (`may_import: [model]`),
`storage/` is the canonical owner of "serialize snapshots/baselines, own
their schemas/migrations, or manage caches" — the new manifest fields,
their schema-version bump, and the widened `(target, profile, channel,
requested depth, evidence-producer identity, fingerprint)` selection key
described below are exactly that. Add a new
baseline-manifest schema/reader/writer module under `abicheck/storage/`
(it only needs `model/`, matching the layer's own import constraint), and
have `buildsource/baseline_publish.py`/`baseline_set.py` — which still own
the *orchestration* (resolving a run plan, invoking the dump, deciding
*when* to publish) — call into it rather than serializing the manifest
inline themselves. This mirrors G44's own package-routing discipline: new
schema/storage logic goes to its canonical owner, and the existing
`buildsource/` module keeps only the coordination glue.

The baseline manifest (its schema now owned by the new `abicheck/storage/`
module above, orchestrated from `abicheck/buildsource/baseline_publish.py`/
`abicheck/buildsource/baseline_set.py`) should persist, per stored baseline
entry:

- producer compiler context (what actually built the binary being snapshotted);
- consumer compiler context (what the baseline's header/source facts were
  extracted under, when `consumer_compile` is active);
- header frontend (`--ast-frontend`);
- public/generated header roots used for this snapshot;
- evidence producer and evidence-pack identity (already partially present
  via `evidence_producer` in `build-output.json`, see G39/Phase 0 below);
- requested and effective depth;
- extraction/effective-configuration fingerprint (the same
  `profile_fingerprint`/`scope_fingerprint` machinery `comparability.py`
  already computes for live dumps — reuse it, don't invent a second one).

A baseline entry selection key becomes at minimum `(target, profile,
channel, requested depth, evidence-producer identity, extraction-context
fingerprint)`, not `target` alone, and not `(target, profile, channel,
fingerprint)` either — a real gap in an earlier draft of this plan,
confirmed by checking what `profile_fingerprint`/`scope_fingerprint`
actually cover: compile context and headers/TUs, neither of which encodes
*requested depth* or *which evidence producer* ran (replay vs. Clang
plugin, say). Two checks on the same target/profile/channel that differ
only in depth (a `headers`-depth check and a `source`-depth check) or only
in evidence producer would otherwise collide on an identical key and one
baseline would silently overwrite or be selected for the other — recreating
exactly the mismatched-old-side problem this whole plan exists to close,
just at the selection-key layer instead of the extraction layer. Depth and
evidence-producer identity must be explicit key components (both are
already-resolved, already-typed values by this point — G42's own
`analysis.evidence` vocabulary is the natural source for the producer axis
if G41 and G42 land in either order) rather than assumed to be implied by
the fingerprint.

**Widening the selection key alone does not make the extra baselines
exist — confirmed by reading the actual publication mechanism, not
assumed.** `publish-baseline.yml`/`update-main-baseline.yml` both run one
job per contract profile, and the `actions/baseline` composite Action
those jobs call accepts exactly one workflow-global `depth` input plus one
`libraries` JSON array, producing exactly one `.abicheck.json` snapshot
per uniquely-named library entry (confirmed directly in
`actions/baseline/run.sh`: `DEPTH="${INPUT_DEPTH:-}"` is applied
identically to every library's `abicheck dump` call, and each library name
maps to exactly one output file). When one (target, profile, channel)
genuinely needs *two* baselines under the widened key — e.g. a project
declaring both a `headers`-depth check and a `source`-depth check against
the same target — today's publication mechanism can still only produce
**one** snapshot for that library name, whichever depth the single Action
invocation happened to run with; the other selection-key entry has no
baseline to select at all, regardless of how correctly the storage layer
now distinguishes the two keys. Closing this needs baseline publication
itself to fan out over every *resolved check context* that requires a
distinct baseline, not just over the library list:

- `derive_baseline_libraries()` (`baseline_publish.py`) must resolve the
  full set of (target, profile, channel, depth, evidence-producer)
  contexts a project's `checks:` declarations actually require baselines
  for — not one row per library name — the same run-plan-derived grouping
  G42's own multi-environment work establishes the precedent for (group by
  everything except the axis that must fan out, here depth/evidence-
  producer rather than environment).
  - **Grouping key, stated explicitly rather than left to guesswork,
    consistent with G42's own corrected mistake in the identical
    situation**: group by (target, profile, channel) and fan out over
    every distinct (depth, evidence-producer) pair that context needs —
    not the reverse, and not a coarser or finer grouping. This is
    deliberately the *complement* of G42's environment grouping (which
    groups on everything *except* environment and fans out over
    environments): here depth/evidence-producer are the axis needing
    distinct baselines, so they are excluded from the group key and
    become the fan-out dimension instead.
- `actions/baseline/action.yml`/`run.sh` gain support for either (a) a
  per-library-entry depth/evidence-producer override in the `libraries`
  JSON array (each entry names its own distinct output identity, not
  relying on the library's bare name alone to key the output file), or
  (b) the calling workflow invoking the Action once per distinct
  (depth, evidence-producer) group with a distinct output artifact name
  per invocation — either closes the gap; a per-entry override is likely
  the smaller change given the Action already parses a structured
  `libraries` JSON array per-entry.
- The baseline-set artifact naming/discovery `check-project.yml` already
  uses (`<baseline-artifact-prefix><profile-id>-<channel>`) must widen to
  disambiguate by depth/evidence-producer too, or two distinct baselines
  for the same target/profile/channel collide on the same artifact name
  the moment both are published.

This is real, new publication-orchestration logic, not merely a
consequence of the wider storage key — the "Effort & risk" section below
reflects it as such.

**Historical-correctness constraint, stated explicitly because it is easy to
get backwards**: an old baseline must contain facts extracted from the old
project's generated/public headers as they existed *at baseline publish
time* — never re-parse the old binary against the current checkout's
headers. Baseline regeneration therefore needs the historical build-output
manifest (or an equivalent pinned artifact) as an input, not just "run the
current pipeline against the old binary."

### Phase 2 — per-target header/compile-context projection

`build-output.json` already has the concrete fields
(`abicheck/buildsource/build_output.py`):

```json
{
  "public_header_roots": ["headers/foo"],
  "generated_header_roots": ["generated-headers/foo"],
  "compile_context": {...}
}
```

and validates that those directories exist and are non-empty. What's
missing is projecting them into `RunPlanCheck` the same way
`consumer_compile_*` is already projected (G34 Phase 0's precedent is the
model to copy structurally): add `public_header_roots`,
`generated_header_roots`, `include_dirs`, `compile_context` fields to
`RunPlanCheck` (`abicheck/buildsource/run_plan.py`), populate them per-target
from the current profile's validated `build-output.json` in
`_generate_target_checks`/`_generate_bundle_checks`, and have
`check-project.yml` read them per matrix cell instead of the single
`inputs.header`/`inputs.old-header`/`inputs.new-header` workflow inputs
(`check-project.yml` lines ~1004-1006 today).

Configuration-model boundary, stated for anyone extending this later:
`.abicheck.yml` declares the *logical* compatibility topology (which targets
exist, which channels/profiles apply to them); `build-output.json` owns the
*concrete*, profile-specific artifact paths (where the headers actually are
for this build). If both specify headers for the same target,
`project validate-build` must reconcile them and fail loudly on
disagreement — never silently prefer one, which would create two
competing, silently-diverging sources of truth for the same fact.

### Phase 3 — declarative assurance requirement

The assurance *engine* already exists (`analysis_assurance`, the assurance
exit contribution, an effective-configuration digest in the native report —
see `abicheck/contract_coverage_exit.py`, `abicheck/contract_context.py` and
neighbors, and `cli-cleanup-phase-two.md`'s `--require-complete-analysis`
section for the CLI-level work already done). What's missing is a clean
*project*-level declaration:

```yaml
checks:
  - channel: accepted-main
    depth: source
    assurance: complete
```

Minimal first slice (ship this before the richer shape below): a boolean
`require_complete_analysis: true` field on the check/run-plan model itself
— not hidden in a workflow-global `extra-args` string, which is how it
would otherwise leak into every check regardless of whether that check
actually wants the floor. Extend later to a structured block once a real
second consumer needs more than a boolean:

```yaml
assurance:
  status: complete
  minimum_effective_depth: source
  require_target_resolution: true
  require_all_selected_translation_units: true
```

The report and aggregate must be able to say, distinctly, which of these
failed: compatibility failure (a real ABI break), analysis-assurance
failure (the declared floor wasn't met — evidence was incomplete even
though nothing broke), operational failure (the check itself couldn't run —
missing binary, tool crash), or missing-report coverage failure (an
expected report never showed up in the aggregate at all). A clean
`NO_CHANGE` compatibility verdict must never overwrite or hide an assurance
failure recorded alongside it — these are two independent axes, the same
"orthogonal, folded with `max`" pattern `contract_coverage_exit.py` already
establishes for the existing coverage-exit contribution; reuse that
pattern rather than inventing a second fold rule.

### Phase 4 — route real `dump` execution through `DumpRequest`

This phase is almost entirely already-tracked work; see the cross-reference
at the top of this plan. What remains open, per the root `AGENTS.md`'s own
"PR C" entry (read that entry in full before starting — it is the living
status record, not this plan): the real ELF/PE/Mach-O execution still runs
through `perform_elf_dump`/`handle_non_elf_dump`, not
`execute_dump_request()`, blocked on (a) `dump`'s default header backend
being castxml, which has not been obtainable as a working build in any
environment this work has been done in, so migrating the default-backend
real-run path is not a verified change; and (b) whichever of the two
remaining `scan`-side behavioural divergences (L4 extractor default,
`public_headers` expansion shape) a future slice chooses to close as opt-in
parameters on the shared primitive rather than leaving scan on its own
resolver indefinitely.

This plan's job for Phase 4 is narrower than re-deriving that design: keep
the acceptance test (dump CLI / typed API / baseline Action / implicit
dump-in-compare all agree, `--dry-run` matches real execution) as the
standing bar, and land the migration once a working non-default-backend
verification path (or a working castxml build) removes the blocker
`AGENTS.md` names. Do not attempt to force the migration around that
blocker by skipping verification — that is exactly the "reactive patch
under review pressure" failure mode this repo's own conventions warn
against, and this exact code area already has a long history of that
mistake (see `AGENTS.md`'s numbered findings on the L3→L2-fold entry).

## Files & surfaces

- `.github/workflows/publish-baseline.yml`, `update-main-baseline.yml` —
  Phase 1: resolve a run plan / `ResolvedExtractionContext` before dumping
  the old side; forward `consumer_compile_*`.
- **`actions/baseline/action.yml`/`actions/baseline/run.sh`** — required,
  not optional: confirmed by reading both directly. Both workflows above
  don't invoke `dump` themselves — they call `uses:
  ./.publish-baseline-src/actions/baseline`, and that composite Action's
  own `run.sh` builds every dump command as `CMD=(abicheck dump
  "$artifact")`, extended only from its own `libraries`/`build-info`/
  `depth` inputs (`action.yml`'s full input list has no compiler/frontend
  field at all). Resolving `consumer_compile_*` in the calling workflow
  therefore cannot reach the actual dump invocation unless
  `actions/baseline/action.yml` gains new inputs (e.g. `gcc-path`/
  `gcc-options`/`ast-frontend`, mirroring `check-project.yml`'s own
  `consumer-gcc-path`/`consumer-gcc-options`/`consumer-ast-frontend`
  naming) and `run.sh` forwards them onto its `CMD` array — without this,
  the workflow-level resolution work is inert.
- `abicheck/storage/` (new module) — Phase 1: the baseline-manifest
  schema/serialization itself — manifest fields (producer/consumer compiler
  context, header frontend, header roots, evidence identity, depth,
  fingerprint), the schema-version bump, and the widened
  `(target, profile, channel, requested depth, evidence-producer identity,
  fingerprint)` selection key — per ADR-061's
  routing (`storage/` owns schemas/migrations for snapshots/baselines).
- `abicheck/buildsource/baseline_publish.py`, `baseline_set.py` — Phase 1:
  orchestration only (resolving the run plan, invoking the dump, calling
  the new `storage/` module to read/write the manifest) — no schema logic
  grown here directly.
- **Phase 2 per-target header/compile-context projection — the
  *generation* logic is `workflows/`-owned coordination, per ADR-061's own
  routing table ("Coordinate dump, compare, scan, release, aggregate,
  project, or dependency behavior" names `project` explicitly, and
  per-target run-plan generation from `build-output.json` is exactly
  that).** `abicheck/buildsource/run_plan.py` is not yet in `architecture/
  modules.yaml`'s classified inventory, so growing it wouldn't trip the
  gate today — but new generation logic (the function reading a profile's
  validated `build-output.json` and populating `public_header_roots`/
  `generated_header_roots`/`include_dirs`/`compile_context`) should still
  be added as `workflows/`-owned coordination rather than grown inline in
  `_generate_target_checks`/`_generate_bundle_checks`, consistent with
  every other package-routing fix in this plan set. The `RunPlanCheck`
  dataclass's own new fields are a data-model question (`model/` is the
  more defensible long-term home for a shared value every stage reads),
  but relocating `RunPlanCheck` itself is out of scope for this plan —
  decide that as part of whichever pass eventually migrates `run_plan.py`
  into the classified inventory, not as a side effect of adding four
  fields to it.
- `.github/workflows/check-project.yml` — Phase 2: per-cell header/compile
  forwarding instead of `inputs.header`/`old-header`/`new-header`.
- `abicheck/cli_project.py` (`project validate-build`) — Phase 2:
  reconciliation/failure when `.abicheck.yml` and `build-output.json` both
  declare headers and disagree. `cli_project.py` is a
  `frozen_root_families["cli_"]` entry, so keep this to the thin CLI
  adapter call; any real reconciliation logic belongs in `workflows/`.
- Project schema / `abicheck/buildsource/project_targets.py` — Phase 3:
  `require_complete_analysis` (minimal) and, later, the structured
  `assurance:` block.
- **Phase 3 aggregate failure-class distinction — `workflows/`, not
  `abicheck/cli_aggregate.py` directly.** `cli_aggregate.py` is a
  `frozen_root_families["cli_"]` no-growth entry, and ADR-061's routing
  table names `aggregate` explicitly as `workflows/`'s responsibility
  ("Coordinate dump, compare, scan, release, aggregate, project, or
  dependency behavior"). The compatibility/assurance/operational/
  missing-report-coverage distinction belongs in a `workflows/`-owned
  aggregation module; `cli_aggregate.py` gains only the thin CLI
  presentation/exit-code adapter over it.
- `abicheck/service_dump_pipeline.py`, `abicheck/cli_dump_helpers.py`,
  `abicheck/service.py` — Phase 4: already-tracked, see `AGENTS.md`'s "PR C"
  entry for the current file-level state.

## Tests

- New `integration`-marked end-to-end fixtures for each phase's acceptance
  test above (two real client-compiler profiles for Phase 1; two targets
  with distinct header roots for Phase 2; a deliberately-incomplete
  evidence pack for Phase 3; a cross-entry-point snapshot/fingerprint
  equivalence test for Phase 4, extending
  `tests/test_dump_cli_typed_api_parity.py`'s existing pattern).
- Unit tests on `RunPlanCheck`'s new fields and `baseline_publish.py`'s new
  manifest fields (round-trip, schema-version bump if the on-disk shape
  changes).
- A regression test pinning that a baseline whose stored extraction-context
  fingerprint disagrees with the candidate's resolved fingerprint fails
  *before* the compare runs (a clear, typed rejection reason), not merely
  as an eventual `NOT_COMPARABLE` from the generic comparability gate.
- A multi-depth/evidence-producer publication test: one project fixture
  declaring both a `headers`-depth and a `source`-depth check against the
  same (target, profile, channel), asserting that baseline publication
  produces **two** distinct, separately-selectable baseline entries rather
  than one overwriting the other — the regression case for the publication
  fan-out gap above.

## Example fixtures

- A minimal two-profile (`gcc-client`, `clang-client`) project fixture
  under `examples/` or `tests/fixtures/` exercising Phase 1's acceptance
  test end to end.
- A two-target, distinct-header-roots project fixture for Phase 2.

## Effort & risk

XL, phased, sequential (each phase's acceptance test should stay green
before starting the next):

- Phase 1 (baseline consumer-context parity): L — workflow + manifest
  plumbing, reusing G34's already-built projection, plus new
  `actions/baseline` inputs to actually carry the resolved
  `consumer_compile_*` values into the dump command that Action
  constructs (confirmed by reading `actions/baseline/run.sh` directly —
  the workflow-level resolution alone doesn't reach it), **and,
  confirmed by a fresh review round, a genuine publication fan-out**:
  `derive_baseline_libraries()`/`actions/baseline` currently produce
  exactly one snapshot per library name regardless of how many distinct
  (depth, evidence-producer) contexts the widened selection key now
  distinguishes, so a target needing both a `headers`-depth and a
  `source`-depth baseline gets only one of the two published today. This
  is new orchestration logic on top of the manifest/selection-key
  widening, not a consequence of it — see the "Baseline publication" note
  above.
- Phase 2 (per-target header projection): M — schema + `RunPlanCheck` +
  workflow forwarding, following an established precedent
  (`consumer_compile_*`) closely.
- Phase 3 (declarative assurance): M for the minimal boolean slice, L for
  the structured block — the underlying assurance engine already exists,
  this is exposure/enforcement, not invention.
- Phase 4 (real dump execution convergence): already extensively scoped and
  partially done elsewhere (see `AGENTS.md`); the remaining work is blocked
  on environment/tooling availability (castxml), not on design.

Risk: Phase 1 and Phase 2 both touch `check-project.yml`, which is already
large and has a documented history of subtle ordering/gating bugs in this
exact area (evidence routing, `consumer_compile` forwarding) — sequence
changes as small, independently-testable diffs and re-run the full
declarative-project `integration` lane after each.

## Out of scope

- Re-litigating G34's already-implemented candidate-side `consumer_compile`
  extraction or PR #860's already-implemented per-target evidence routing —
  both are done; this plan only closes the baseline-side gap and the
  header-roots-projection gap the review found next to them.
- The full non-boolean `assurance:` schema beyond what a real second
  consumer motivates — ship the minimal `require_complete_analysis: true`
  slice first and extend only when a concrete use case needs more.
- Redesigning `perform_elf_dump`/`handle_non_elf_dump`'s post-processing
  hooks or `scan`'s remaining opt-in-parameter gaps — tracked in `AGENTS.md`
  and referenced, not restated, here.
