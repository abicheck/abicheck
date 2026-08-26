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
across a resolve/execute pipeline; its four flat call sites
(`service_dump_pipeline.py`, `service_input_resolution.py`,
`cli_dump_helpers.py`, `cli_dump_non_elf.py`) import it from here rather than
duplicating it, but stay flat themselves until the larger `service_dump_pipeline.py`/
`service_input_resolution.py` migration (blocked on their own
`cli_dump_helpers.py`/frontends coupling — see ADR-061's Phase 3 status note)
lands.

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
