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

`workflows.artifact` is now complete as a `Request -> ResolvedPlan -> Result`
trio (ADR-061 Phase 3): `resolve.py` decides what an extraction will do
without doing it, and `execute.py` runs that plan and reports what it
achieved. Keeping "decide" runnable without "do" is what lets `dump --dry-run`
render the same resolved plan a real run consumes -- a preview computed by a
second resolver looks authoritative while being connected to nothing, which is
worse than two implementations kept in sync by hand, because nothing fails
when they drift. `abicheck/service_input_resolution.py` remains as a
delegating facade; import the owners.

Six narrow re-export surfaces exist so a frontend can reach an operation
without importing a ring it may not (`frontends` may import only `model`,
`workflows`, `report`):

| Module | What a frontend reaches through it |
|---|---|
| `gate.py` | The whole process response — verdict, contract-coverage floor, assurance floor (ADR-061 Phase 4 item 4) |
| `extraction.py` | Input-side operations: header expansion, the L2 seed, the L3→L2 fold, build-source embedding |
| `findings.py` | Finding identity and the probe matrix |
| `scan_config.py` | Scan config, risk rules, and the public-provenance rule (owned here, not aliased) |
| `scan_abi3_dry_run.py` | The `--abi3` dry-run precondition check both `scan --dry-run` renderers use (CLI cleanup phase two, PR 5 follow-up) — delegates candidate resolution to `scan_abi3_resolve.py` (a flat `workflows`-legacy root module, not this package: it needs `serialization.load_snapshot`, which has no ADR-061 layer of its own, and this migrated package may not import an unclassified module directly), so it stays outside the CLI-registration import cycle entirely |
| `suppression.py` | `SuppressionList`/`Suppression`, so a CLI helper can type and load a `--suppress` file without importing `policy`-classified `suppression.py` directly |

`gate.py` earns its place rather than laundering an import: three orthogonal
axes feed one exit code, and a frontend importing them separately is free to
fold two and forget the third. One consequence of the re-export surfaces is
worth knowing before writing a test against them — `from ..x import y` **binds**
`y` at import time, so patching `abicheck.x.y` afterwards does not change what
a caller reaching it through the facade sees. Patch it where the call
resolves.

`render.py` is the reverse shape from the five above: it exists so
`abicheck.service` (`workflows`) can keep re-exporting
`render_output`/`_render_json_output`/`_render_deps_section_md` without a
forbidden `workflows -> frontends` edge to `service_render.py`, which owns
the real implementation but is itself classified `frontends` (it needs
`report`). Each function is a real, separately-typed `def` that resolves
`service_render.py` via `importlib.import_module` inside its own body — a
runtime call, invisible to the static import scans `dependency-direction`
and `import-cycle-growth` run — rather than a blanket `__getattr__`, which
would resolve every name as `Any` for external callers (ADR-061).

`input_resolution.py` is a third shape: the real `resolve_input`
implementation (plus `detect_binary_format`, `sniff_text_format`,
`collect_metadata`, `load_env_matrix`, private helpers), moved here from
`service.py` (ADR-061 Phase 4). `service_dump_native` (`run_dump`/`_emit`)
reaches the baselined CLI-registration SCC via `service_header_graph_attach ->
service_scan -> service`, and `service` imports this module — a static edge
would grow that cycle, so it's bound via `importlib.import_module` instead,
the same escape hatch `render.py`'s bridge uses. `service.py` re-exports the
rest with a plain static import; a test intercepting a call `resolve_input`
makes *internally* must patch `abicheck.workflows.input_resolution.<name>`,
not `abicheck.service.<name>`. `compare_snapshots`/`load_suppression_and_
policy` stayed in `service.py`: both need `PolicyFile`, an open
`policy_file.py` debt question.

`abicheck/service_dump_pipeline.py` is classified `workflows` via
`legacy_paths`: it is free of CLI imports and owns `DumpRequest ->
ResolvedDumpRequest -> DumpResult`, but has not moved into this directory
yet. Know what that classification enforces, because the two gates differ:
`check_architecture.py` rejects a forbidden *direction* to a classified layer
(a `workflows -> report` import fails, and reports the cycle), while the CLI
boundary for a still-flat module is held by the separate
`engine-cli-boundary` gate. Both are live; neither is decorative.

`service_input_resolution.py` is classified too, since `embed_build_source`
moved to `buildsource/embed.py`. Only `service_compare_pipeline.py` is left:
it still imports `prepare_embedded_build_source`/`attach_evidence_metrics`
from `cli_buildsource`.

When you move an engine operation off the CLI layer, the error types are the
contract, not an implementation detail. `buildsource/embed.py` raises
`ValidationError` for a usage error (the CLI renders exit 64) and
`SnapshotError` for an operational one (exit 1); the CLI adapter translates,
and this package's Tier-2 surface flattens both onto `SnapshotError` because
that is what its callers already catch. Pin the codes with characterization
tests before moving — see `tests/test_build_source_embed_errors.py`.

Shared vocabulary those modules used to reach into the CLI layer for now
lives in leaves any layer may depend on: `abicheck/evidence_depth.py` (the
depth ladder) and `buildsource/pack_shape.py` + `buildsource/inputs_pack.py`
(the pack-shape predicates). Prefer them over re-deriving.

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

## Product invariant (local consequence)

A workflow resolves the **user task and comparison scope** before it
executes anything: which members or variants were selected, which were
expected for this run and with what provenance, what was actually
acquired, and what is out of scope. *Unselected*, *expected but not
produced*, *failed*, and *deliberately retired* are four different states
that reach the typed result as such; a run that completed zero valid
comparisons is reported as no comparison, never as a clean pass. One
resolution serves scalar, package, and matrix cardinalities. Root
`AGENTS.md` "Product decisions and change routing" states the rule;
ADR-065 and `docs/contribute/plans/vision-api-abi-evolution.md` own the gaps.
