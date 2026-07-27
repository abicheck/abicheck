# G33 — Typed API convergence: schema registry, Request/Result completeness, MCP dedup

**Status:** Not started (Phase 0 partially landed, see per-phase status below)
**Normative decision:** [ADR-055](../adr/055-typed-request-result-completeness-and-schema-registry.md)
(Proposed — not implemented)
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
reads the same typed result for that classification step. Today
`abi_compare` is the one confirmed violation of that invariant (Phase 4/D4
below); the other phases close gaps that make the invariant harder to reach
even where it isn't yet violated. This deliberately does **not** cover every
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

**Progress:** the registry itself is done —
`abicheck.schemas.current(name)` (`abicheck/schemas/__init__.py`) covers
all six artifact names above, backed by each artifact's existing constant
via a function-local import (a module-level import would create a real
cycle: `run-plan` needs `buildsource.run_plan.RUN_PLAN_SCHEMA`, but
`buildsource/run_plan.py` imports `buildsource/check_report.py`, which
already imports `abicheck.schemas` — confirmed by reading both modules
before choosing the deferred-import shape). Covered by
`tests/test_schemas_registry.py`. Still remaining: wiring an actual doc
page or generator to *read from* `current()` instead of a hand-copied
literal — `docs/reference/snapshot-format.md` (the designated fact-owner
page for the snapshot version) still states "17" by hand, same as every
other artifact's own doc page. That's a separate, larger follow-up (a real
doc-generator change, reviewed on its own), not bundled into this slice.

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

**Progress:** not started; a real complication found while scoping it,
not yet reflected in ADR-055 itself. `run_compare_request` (the function
`CompareRequest` feeds) is **not** what the CLI `compare` command actually
calls for its full depth/sources/build-info/multi-TU-manifest feature set
— reading `cli_compare_helpers.py` and `cli_resolve.py` shows the CLI's
own resolution goes through `cli_resolve._resolve_compare_snapshots`, a
CLI-layer function with a much richer parameter set (per-side
`CompileContext`/`dump_manifest`/debug-root/debuginfod overrides, explicit
old/new format detection, dependency-graph following) that itself calls
`service.resolve_input`/`compare_snapshots` directly — not
`run_compare_request`. So today there are genuinely **two** independent
resolution paths: the CLI's rich one (`_resolve_compare_snapshots`) and
the typed one (`run_compare_request`, what `CompareRequest` feeds, and
what a future `abi_compare` rewrite in Phase 4 would use). Adding fields to
`CompareRequest`/`InputSpec` without addressing this split would give the
typed request a way to *carry* depth/sources/build-info in, but would not
by itself make `CompareRequest` capable of everything the CLI supports —
`_resolve_compare_snapshots`'s extra logic (dependency-graph following,
per-side backend override, debug-root/debuginfod per side) would still
need to either move into `run_compare_request` or be reconciled with it.
Before writing code, whoever picks this up should decide: (a) migrate the
CLI `compare` command onto an extended `run_compare_request` (higher risk,
touches the CLI's own heavily-tested resolution path), or (b) extend
`run_compare_request` to match `_resolve_compare_snapshots`'s capability
set in parallel, keeping both paths but eliminating the *capability* gap
between them (lower risk, but the two-path duplication ADR-055 D1 set out
to close would persist one level down). The `DumpManifest`/`CompileContext`
machinery itself is no longer a blocker either way — G32 (Phase 0 and
Phases A–E) is now done, per that plan's own status — so this phase's
only real blocker is the resolution-path decision above, not missing
lower-level machinery.

### Phase 3 — `CompareResult` wrapper (ADR-055 D2)

Introduce `CompareResult` (`diff`/`old_snapshot`/`new_snapshot`, a pure
rename of the existing tuple shape) and a parallel typed entry point
returning it, while `run_compare`/`run_compare_request`'s existing
tuple-returning signatures stay exactly as they are for every current
caller.

**Gate:** a new field (resolved depth, an `EvaluationReceipt`, a coverage
summary) has somewhere to land without a second tuple-shape break.

**Progress:** not started. Depends on Phase 2 landing first (ADR-055's own
rollout order — the wrapper is only worth adding once the request side is
worth wrapping too).

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

**Progress:** not started. Deliberately last and gated on Phase 2 — a
rewrite before `CompareRequest` can express `abi_compare`'s full parameter
set would either lose capability (dropping a parameter the tool currently
accepts) or invent an ad hoc extension outside ADR-055's own decision.

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

**Progress:** not started; blocked on Phase 4.

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

- All four ADR-055 decisions (D1–D4) implemented and tested per their
  individual gates above.
- `tests/test_mcp_server_unit.py`'s `abi_compare` parity test passes against
  both the pre- and post-rewrite CLI `compare` output for a representative
  input matrix (binary-only, headers, snapshot inputs, `used_by`-scoped,
  severity-aware).
- `docs/reference/python-api-reference.md` and
  `docs/reference/mcp-tools-reference.md` regenerated and committed.
- ADR-055's status line updated from "Proposed" to "Accepted —
  implemented" (or the individual D1–D4 statuses split out, if they land
  in separate PRs on different schedules).
