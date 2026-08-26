# AGENTS.md — `abicheck/workflows/`

## Purpose

This package owns operation orchestration: sequencing extraction, comparison,
policy evaluation, resource lifetimes, and typed request/plan/result flows. It
is the composition ring between domain packages and frontends.

## Permitted imports

Workflow code may import `abicheck.model`, `abicheck.storage`,
`abicheck.extract`, `abicheck.compare`, and `abicheck.policy`. During the
ADR-061 migration, legacy flat modules classified to those owners may remain
dependencies, but workflow code must never import through `abicheck.cli`,
`abicheck.service`, or another compatibility facade.

Within this package, prefer explicit implementation-module imports. Package
`__init__.py` files provide narrow external surfaces, not an internal service
locator.

## Canonical entry points

Each major operation converges on `Request -> ResolvedPlan -> Result`, with
`contracts.py`, `resolve.py`, and `execute.py` as the standard ownership split.
Aggregation currently exposes
`workflows.aggregate.execute.aggregate_reports_dir` as its canonical composed
entry point. `workflows.artifact.contracts.ResolvedArtifactPlan` (ADR-061
Phase 3) is the shared, dependency-free `contracts.py` half of the dump/scan
artifact-resolution contract — a session type owning cleanup-thunk lifetime
across a resolve/execute pipeline.

`abicheck/service_dump_pipeline.py` is classified `workflows` via
`legacy_paths`: it is free of CLI imports and owns `DumpRequest ->
ResolvedDumpRequest -> DumpResult`, but has not moved into this directory
yet. Know what that classification enforces, because the two gates differ:
`check_architecture.py` rejects a forbidden *direction* to a classified layer
(a `workflows -> report` import fails, and reports the cycle), while the CLI
boundary for a still-flat module is held by the separate
`engine-cli-boundary` gate. Both are live; neither is decorative.

`service_input_resolution.py` is classified too, since
`embed_build_source` moved to `buildsource/embed.py`. Only
`service_compare_pipeline.py` is left: it still imports
`prepare_embedded_build_source`/`attach_evidence_metrics` from
`cli_buildsource`.

When you move an engine operation off the CLI layer, the error types are the
contract, not an implementation detail. `buildsource/embed.py` raises
`ValidationError` for a usage error (the CLI renders exit 64) and
`SnapshotError` for an operational one (exit 1); the CLI adapter translates,
and this package's Tier-2 surface flattens both onto `SnapshotError` because
that is what its callers already catch. Pin the codes with characterization
tests *before* moving — see `tests/test_build_source_embed_errors.py`.

Shared vocabulary those modules used to reach into the CLI layer for now
lives in leaves any layer may depend on: `abicheck/evidence_depth.py` (the
depth ladder and what depth an artifact reached) and
`buildsource/pack_shape.py` + `buildsource/inputs_pack.py` (the pack-shape
predicates). Prefer them over re-deriving; the previous arrangement produced
four copies of the depth ladder and three of the inputs-pack guard.

## Tests

Workflow unit tests live under `tests/unit/workflows/` as migration proceeds.
Existing aggregation coverage remains in `tests/test_aggregate*.py`, with
compatibility imports covered separately. Move tests with implementation; patch
the implementation owner rather than a root facade.

## Prohibited responsibilities

Do not declare Click commands, parse presentation-only flags, render output
formats, implement binary/header parsers, define raw comparison detectors, or
recompute policy decisions in this package. A workflow returns typed achieved
facts and decisions; it does not print them.

Dry-run and execution must consume the same resolved plan. Do not add a second
configuration resolver or an estimator that independently predicts effective
policy, backend, or evidence depth.

## Change checklist

Before adding workflow behavior, identify the request field that carries user
intent, the resolved-plan field that records the effective value and
provenance, and the result field that records what execution achieved. Keep
resource acquisition inside the plan's explicit lifetime.

When migrating a flat implementation, switch every internal caller in the
same change, retain only documented compatibility exports, add a facade
contract test, update `architecture/modules.yaml`, and reduce the corresponding
`architecture/debt.yaml` entry. Run `python scripts/check_architecture.py`
before the canonical PR profile.

## Public compatibility

Root `abicheck.service` and command modules are compatibility/front-end
surfaces, not workflow dependencies. A supported old import may delegate to a
workflow object, but the workflow must not import back through that facade.
