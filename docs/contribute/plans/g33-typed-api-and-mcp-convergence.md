# G33 — Typed API convergence: schema registry, Request/Result completeness, MCP dedup

**Status:** Phases 0-4 done (ADR-055 D1-D4 all implemented); Phase 5
unblocked but not started; Phase 6 is a standing constraint, not work —
see per-phase status below
**Normative decision:** [ADR-055](../adr/055-typed-request-result-completeness-and-schema-registry.md)
(Accepted — implemented)
**Related:** ADR-037 (G22, CLI consolidation — done), ADR-049 (contract relevance,
see [public-contract-default.md](public-contract-default.md) for its own
rollout), ADR-043 (`used_by`/`required_symbols` app scoping), ADR-050
(comparability contract / `frontend_context`, see [G32](g32-comparability-contract-and-multi-tu-manifest.md)),
[G30](g30-github-actions-integration-model.md) (Actions tiers — this plan
defers to it rather than duplicating)
**Scope:** `abicheck/service.py`, `abicheck/api_types.py`,
`abicheck/schemas/`, `abicheck/mcp_server.py`'s `abi_compare`

## 1. Where this came from

Two rounds of an external, Russian-language "API layers" review of `abicheck`
(CLI/Python service/MCP/Actions/schemas/producer-SPI/ADR-049) were checked
against `main` in the same session. The first round was largely stale —
G22/ADR-037 had already landed the CLI consolidation and a real MCP↔Tier-2
chokepoint the review flagged as missing — but verifying it line-by-line
surfaced two real, narrower gaps, written up as ADR-055 D1–D3 and addressed
partially in [PR #646](https://github.com/abicheck/abicheck/pull/646) (new
`abi_deps`/`abi_aggregate`/`abi_project_validate`/`abi_project_plan` MCP
tools, an Actions-tiers doc section, ADR-055's draft). A second, more
detailed round of the same review reasserted that `abi_compare` duplicates
Tier-2 business logic as its top-priority finding. Re-reading
`mcp_server.py`'s `abi_compare` line-by-line (not just trusting either
review) confirmed the second round was right and ADR-055's own
first-draft Non-goal ("not touching the MCP server's tool surface... already
routes through `compare_snapshots`") was wrong — see ADR-055's "Gap 3"
section and its correction note for the specifics. This plan is the
implementation tracker for ADR-055's four decisions (D1–D4) plus the parts
of the review's remaining scope that don't already have a home elsewhere in
this repo's plan/ADR set.

**What this plan deliberately does not re-litigate**, because it already has
an owner:

- **Actions tiers / a second external CI-integration pilot** — owned by
  [G30](g30-github-actions-integration-model.md) (already tracks "P2 —
  Deeper architecture" and the open "second complex pilot" gap). The
  Recommended/Advanced/Legacy classification this plan's source review
  proposed is already partially documented in `docs/integration/index.md`'s
  "Which Actions building block do I use?" table (landed in PR #646); a
  further narrowing of the root `action.yml` itself is G30's to schedule,
  not duplicated here.
- **ADR-049 shadow-evaluator rollout** — owned by
  [public-contract-default.md](public-contract-default.md). This plan's
  Phase 6 below only asserts the *sequencing constraint* (don't make
  ADR-049 authoritative until all frontends share one resolved config), it
  does not track ADR-049's own phase-by-phase status.
- **Producer/extension SPI** (`abicheck.evidence_providers` entry points) —
  the source review itself judged this premature before producer identity/
  version/digest stabilizes (ADR-049 already lays that groundwork). No
  phase below covers it; revisit only after ADR-049's evidence-provider
  model is live.

## 2. Target architecture

```text
CLI / Python / MCP / Action
            │
            ▼
    Public Request Models
            │
            ▼
 Resolve + Validate + Provenance
            │
            ▼
   Resolved Operation Request
   + Evaluation Receipt
            │
            ▼
        Core Engine
            │
            ▼
      Typed Result Model
            │
   ┌────────┼─────────┐
   ▼        ▼         ▼
 CLI text  MCP obj  JSON artifacts
```

Invariant this plan exists to restore: **no front end (CLI, MCP, or an
Action) resolves inputs, loads policy/suppression, or classifies a
comparison on its own** — every front end builds the same typed request and
reads the same typed result for that classification step. `abi_compare` was
the one confirmed violation (Phase 4/D4 below) and no longer is; the other
phases closed gaps that made the invariant harder to reach even where it
wasn't yet violated. One qualification the invariant needs, now that it
holds: the CLI `compare` command builds the same *request* type and reads the
same *result*, but still runs its own richer input resolution
(`cli_resolve._resolve_compare_snapshots`) — a recorded decision, not an
outstanding violation. See Phase 2's progress note for what that actually
covers. This deliberately does **not** cover every
downstream presentation concern: `used_by`/`required_symbols` app-scoping
and severity/exit-code computation are explicitly kept as thin,
front-end-specific glue applied *after* a typed result exists (Phase 4
below) — they are not part of "recomputes scope/severity" in the sense this
invariant restricts, since there is no shared typed field for them to
recompute *from* a single source in the first place; folding them into the
request/result types would recreate the "one growing struct" problem this
plan's earlier phases exist to avoid.

## 3. Acceptance outcomes

- `abi_compare`'s implementation contains no local `_resolve_input` call and
  no direct `PolicyFile.load`/`SuppressionList.load` calls — every one of
  those goes through `run_compare_request`/`CompareRequest`.
- A `CompareRequest` can express every input `abicheck compare` itself
  accepts (depth, sources, build_info, dump_manifest, per-side
  `CompileContext`/`frontend_context`, public_header_dirs) — no caller needs
  to fall back to loose kwargs on a lower-level function to reach a feature
  the CLI already has.
- `abicheck.schemas.current(name)` returns the real, current version for
  every persisted artifact this repo emits (snapshot, compare, scan,
  aggregate, build-output, run-plan), and every doc that states a version
  number pulls it from there instead of a hand-copied literal.
- A parity test proves `abi_compare`'s output for a fixed input matches the
  CLI `compare` command's output for the equivalent flags, both before and
  after the D4 rewrite — the rewrite must not silently change behavior.

## 4. Work breakdown

### Phase 0 — record the gap accurately (done)

- ADR-055 written (D1–D3), then corrected once Gap 3 was confirmed by
  reading `mcp_server.py` directly rather than trusting either review
  verbatim (D4 added).
- `OutputSpec` re-exported from `service.__all__` (was created in
  `api_types.py` but never exported — a smaller, already-fixed instance of
  the same "typed surface exists but isn't the real chokepoint" pattern).
- `docs/use/python-api.md`'s stale `schema_version 8` claim fixed to link to
  `reference/snapshot-format.md` instead of a hand-copied number (the
  specific bug D3 exists to prevent recurring).

**Gate:** ADR-055 accurately describes current `main`, not a stale
snapshot of it. **Progress:** done, including the correction.

### Phase 1 — schema-version registry (ADR-055 D3)

Add `abicheck.schemas.current(name)` (or an equivalent lookup), backed by
the *existing* constants: `serialization.SCHEMA_VERSION`,
`abicheck.schemas.REPORT_SCHEMA_VERSION`,
`abicheck.schemas.SCAN_SCHEMA_VERSION`,
`aggregate.AGGREGATE_SCHEMA_VERSION`,
`buildsource.build_output.BUILD_OUTPUT_SCHEMA`,
`buildsource.run_plan.RUN_PLAN_SCHEMA`. Read-only lookup facade — no new
versioning scheme, no compatibility metadata, no change to any constant's
current value or bump policy. (Deliberately not quoting each constant's
current value here — this page would go stale exactly the same way the
`docs/use/python-api.md` bug that motivated D3 did; call `schemas.current()`
or read the owning module's docstring for the live value.)

**Gate:** every persisted-artifact version number quoted in `docs/` is
generated from this registry (or a page that itself reads from it), not a
hand-copied literal.

**Progress:** done. The registry itself —
`abicheck.schemas.current(name)` (`abicheck/schemas/__init__.py`) covers
all six artifact names above, backed by each artifact's existing constant
via a function-local import (a module-level import would create a real
cycle: `run-plan` needs `buildsource.run_plan.RUN_PLAN_SCHEMA`, but
`buildsource/run_plan.py` imports `buildsource/check_report.py`, which
already imports `abicheck.schemas` — confirmed by reading both modules
before choosing the deferred-import shape). Covered by
`tests/test_schemas_registry.py`.

The gate above ("every version number quoted in docs is generated from this
registry, not a hand-copied literal") is now met too, by a route this phase
did not originally anticipate: rather than converting pages to generated
ones, `scripts/check_ai_readiness.py`'s existing `doc-count-sync` check
reads its expected snapshot and compare-report versions from
`schemas.current()` and pins them against the pages that quote them. These
numbers live inside hand-written prose and JSON examples, so a generator
would have had to own a whole page to own one number, while the check fails
the build on exactly the drift D3 was about — and caught two live instances
on its first run (`docs/use/output-formats.md` and
`docs/reference/check-target.md` both quoting long-superseded
`report_schema_version` values). Extending it to the remaining four
artifacts is mechanical: add an anchor when a doc page starts quoting one.

### Phase 2 — extend `InputSpec`/`CompareRequest` (ADR-055 D1)

Add, as additive fields with defaults (`api_types.py`'s own stated
convention): `InputSpec.sources`/`build_info`/`dump_manifest`/`compile`
(per-side `CompileContext` override)/`public_header_dirs`;
`CompareRequest.depth`/`frontend_context`. `run_compare_request` already
resolves every one of these concepts internally — this phase gives the
*typed request* a way to carry them in, not new resolution logic. Also add
the policy/suppression fields Phase 4 needs (`CompareRequest` has neither
today).

**Gate:** the two `service.py` comments ADR-055 quotes ("`CompareRequest`
has no explicit `--gcc-options` equivalent today"; "no lower-level
'parse only, don't classify' mode") are no longer true.

**Progress:** done, in [PR #651](https://github.com/abicheck/abicheck/pull/651)
— whose title ("close dump/compare dependency-scoping asymmetry") is why this
note went on saying "not started" for several PRs afterwards. `InputSpec`
gained `sources`/`build_info`/`dump_manifest`/`compile`/`public_header_dirs`,
`CompareRequest` gained `depth`/`frontend_context`, and the resolution wiring
lives in the new `abicheck/service_compare_evidence.py`. The gate holds:
neither `service.py` comment quoted above still exists. Tests:
`TestCompareRequestAdr055Evidence` (`tests/test_service_unit.py`) and the
ADR-055 D1 blocks in `tests/test_api_types.py`.

The two-resolution-path question this note raised is now **decided as (b)**,
recorded in ADR-055's D1 section: `run_compare_request` was extended in
parallel and the CLI `compare` command still resolves through
`cli_resolve._resolve_compare_snapshots`, because option (a) would rewrite
the CLI's most heavily-tested resolution path for no user-observable gain.

A follow-up slice then closed the *capability* half properly. A first attempt
at naming what stayed CLI-only listed three things and was wrong on all
three: the per-side AST-frontend override already worked (`InputSpec.compile.
frontend` reaches that side's `run_dump`, verified by spying on its
arguments); `source.method` is expressible as `CompareRequest.depth`, and a
Tier-2 API deliberately does not read `.abicheck.yml` from the cwd; and the
set-input guard protects an input *kind* the typed path never accepts.
Diffing the two parameter lists instead of reasoning about them found the
real delta — `dwarf_only`, `debug_format`, ADR-050 D1's `include_labels`, and
`--follow-deps` — all four now on `CompareRequest`, with `--follow-deps`'s
implementation moved to the new leaf module `abicheck/dependency_info.py`
that both layers depend on.

So the two paths now differ in structure, not in what they can express.
Migrating the CLI onto `run_compare_request` remains possible as its own
change, gated on its own before/after parity evidence across the CLI's flag
matrix.

### Phase 3 — `CompareResult` wrapper (ADR-055 D2)

Introduce `CompareResult` (`diff`/`old_snapshot`/`new_snapshot`, a pure
rename of the existing tuple shape) and a parallel typed entry point
returning it, while `run_compare`/`run_compare_request`'s existing
tuple-returning signatures stay exactly as they are for every current
caller.

**Gate:** a new field (resolved depth, an `EvaluationReceipt`, a coverage
summary) has somewhere to land without a second tuple-shape break.

**Progress:** done. `CompareResult` (`diff`/`old_snapshot`/`new_snapshot`)
lives in `api_types.py` beside `CompareRequest`, and
`service.run_compare_request_v2` returns it. `run_compare_request` keeps its
exact tuple signature for every existing caller — it is now a one-line view
(`CompareResult.as_tuple()`) over the same implementation, rather than a
second copy of the resolution logic, which would have been this plan's own
failure mode reproduced inside its fix.

One departure from the decision as written: the struct carries a fourth
field, `suppression`, not just the tuple's three. Phase 4 is why — the
service resolves the suppression list internally, but `appcompat.
scope_diff_to_app(..., suppression=...)` needs the resolved object *after*
classification, and `DiffResult` carries the resolved policy file but not the
suppression list. Without it the MCP server would have kept a
`SuppressionList.load` call solely to re-derive what the service had already
loaded — the duplication Phase 4 removes, reintroduced by the shape chosen to
enable removing it. Tests: `TestCompareResult` (`tests/test_api_types.py`),
`TestRunCompareRequestV2` (`tests/test_service_unit.py`).

### Phase 4 — route `abi_compare` through `run_compare_request` (ADR-055 D4)

The review's stated top-priority item. Rewrite `mcp_server.py`'s
`abi_compare` to build one `CompareRequest` and call `run_compare_request`
for resolve+classify, instead of its own `_resolve_input` +
`compare_snapshots` pair. `used_by`/`required_symbols` scoping and
`severity_*`/exit-code computation stay MCP-specific glue *over* the
returned `CompareResult` — they are cross-cutting concerns applied after
classification, not part of the typed request/result shape itself (folding
them in would recreate the "one growing struct" problem Phases 2–3 exist to
avoid).

**Gate:** `abi_compare` has no local `_resolve_input` call and no direct
`PolicyFile.load`/`SuppressionList.load` calls; a parity test confirms
identical output to the CLI `compare` command for equivalent flags, both
before and after the rewrite.

**Progress:** done. `abi_compare` builds one `CompareRequest` and calls
`run_compare_request_v2`; `mcp_server.py` no longer imports
`compare_snapshots` at all. Both halves of the gate are executable in
`tests/test_mcp_server_unit.py`'s `TestAbiCompareCliParity`: the source-level
absence check (comments stripped first — the function now *documents* what it
stopped calling), and CLI-parity assertions over a flag matrix (defaults,
policy profile, policy file, suppression file, `--show-only`,
`--report-mode`, severity-aware gating) covering the rendered report and the
exit code. Parity was additionally verified as a before/after diff of the
tool's own output across that matrix, so the rewrite is shown not to have
moved anything rather than only agreeing with the CLI.

Three findings worth carrying forward:

1. One MCP guard had to become *request* surface: `_resolve_input` pinned
   `follow_linker_scripts=False` because the tool size-checks only the
   caller-supplied path, and a GNU ld script's `INPUT()` target would bypass
   that. `InputSpec.follow_linker_scripts` (default `True`, matching
   `resolve_input`'s own default) carries it now. Path containment and file
   size stayed MCP-local, applied before the request is built — those
   constrain untrusted input, which really is the front end's job.
2. The report matches the CLI's exactly, except for two keys the CLI's
   renderer adds and this tool has never emitted (`old_evidence_depth`/
   `new_evidence_depth`).
3. One intentional behaviour change: an unsupported `language` is now a
   structured validation error instead of a value passed quietly down the
   resolver — `CompareRequest.validate()` doing what the CLI's `--lang`
   choice already did (ADR-037 D9's front-end parity), so it is recorded as
   intended rather than tolerated.

`used_by`/`required_symbols` scoping and severity/exit-code computation
stayed MCP glue over the returned `CompareResult`, exactly as scoped above.

### Phase 5 — extend the other MCP tools to the same parity (deferred)

Once Phase 4 lands, `abi_dump`/`abi_scan` gain the same
depth/sources/build_info/`DumpManifest`/`CompileContext`/host-device-context
parity `abi_compare` gains in Phase 4. The source review explicitly framed
this as conditional on the "service convergence" phases landing first
(its own item 6), not a parallel, independent piece of work — don't start
this before Phase 4 is done.

**Gate:** `abi_dump`/`abi_scan`'s MCP parameter sets are a strict superset
of `abi_compare`'s post-Phase-4 parameter set for every concept `dump`
and `compare` share.

**Progress:** not started — but no longer blocked: Phase 4 is done, which
was its only stated precondition.

### Phase 6 — ADR-049 sequencing constraint (no new work here)

Not a phase this plan implements — a standing constraint on
[public-contract-default.md](public-contract-default.md)'s own rollout:
its shadow evaluator must not become authoritative in any frontend until
CLI, Python, MCP, and Actions all construct the same resolved
`CompatibilityEvaluationConfig` and the same provenance receipt. Recorded
here only so a reader of this plan sees the explicit dependency; track
actual progress in that plan's own "Work breakdown," not here.

## 5. Out of scope

- Anything already covered by [G30](g30-github-actions-integration-model.md)
  (Actions tiers, a second external CI-integration pilot) or
  [public-contract-default.md](public-contract-default.md) (ADR-049
  rollout) — see "Where this came from" above.
- Producer/extension SPI (`abicheck.evidence_providers` entry points) — not
  ready until ADR-049's evidence-provider identity model is live.
- Any change to detector logic, `ChangeKind` taxonomy, or snapshot
  contents — this plan is request/response/tool-surface shape only.
- A `--dry-run --format json` normalized-operation schema for
  `dump`/`compare`/`scan` (the source review's Level A / CLI-API gap) — a
  real, separate gap, but orthogonal to the service/MCP convergence this
  plan tracks; needs its own plan if picked up.

## 6. Definition of done

All met:

- [x] All four ADR-055 decisions (D1–D4) implemented and tested per their
  individual gates above.
- [x] `tests/test_mcp_server_unit.py`'s `abi_compare` parity test passes
  against both the pre- and post-rewrite CLI `compare` output for a
  representative input matrix. Two deliberate narrowings of the matrix this
  line originally listed, each for a reason rather than for convenience:
  *binary-only/headers* inputs need a real compiled library (the marker lanes
  this fast-suite test can't require), and the JSON-snapshot inputs it does
  use exercise the same request-building and classification path; and
  *`used_by`-scoped* runs are deliberately excluded because that scoping is
  applied after a result exists and has no CLI-comparable rendering here —
  the sibling `TestAbiCompare` scoping tests own it. *Snapshot inputs* and
  *severity-aware* are both covered.
- [x] `docs/reference/python-api-reference.md` and
  `docs/reference/mcp-tools-reference.md` regenerated and committed.
- [x] ADR-055's status line updated from "Proposed" to "Accepted —
  implemented".

Remaining work tracked elsewhere, not reopening this plan: Phase 5
(`abi_dump`/`abi_scan` parity) is unblocked and still open, and migrating the
CLI onto `run_compare_request` is a possible future change under Phase 2's
recorded decision (b).
