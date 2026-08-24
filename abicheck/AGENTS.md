# AGENTS.md — `abicheck/` package

This file scopes the repository-level `AGENTS.md` for production Python code.
It is vendor-neutral and authoritative for this directory. Tool-specific files
must point here rather than restating these rules.

## Read order

1. `/AGENTS.md` — repository-wide verification, compatibility, and PR rules.
2. This file — task routing and dependency direction for `abicheck/`.
3. The nearest package `AGENTS.md`, when one exists.
4. The relevant ADR/plan only when the task changes that contract.

The physical migration is defined in
`/docs/contribute/plans/module-boundaries-and-file-health.md`; the
machine-readable boundary graph is `/architecture/module-boundaries.json`.

## Route work by responsibility

| The change primarily… | Owner |
|---|---|
| defines stable ABI facts, findings, identity, or typed result values | `abicheck/domain/` |
| extracts binary, debug, header, build, or source facts | `abicheck/evidence/` |
| matches old/new entities or emits compatibility findings | `abicheck/compare/` |
| applies suppression, contract relevance, policy, severity, or gate logic | `abicheck/evaluate/` |
| reads/writes snapshots, schemas, caches, or persisted envelopes | `abicheck/storage/` |
| orchestrates dump/compare/scan/deps/aggregate/project/release | `abicheck/workflows/` |
| builds a canonical report model or renders a format | `abicheck/report/` |
| exposes Click or typed-Python input/output adaptation | `abicheck/interfaces/` |

Most of those packages are migration targets and may not exist yet. Until a
responsibility moves, edit its current owner, but do not create another
root-level `cli_*`, `service_*`, `dumper_*`, `diff_*`, `reporter_*`,
`bundle_*`, `contract_*`, or similar overflow sibling. Start the target package
with real code and tests instead.

## Dependency direction

```text
interfaces -> workflows -> evidence / compare / evaluate / storage -> domain
interfaces -> report -> domain / compare / evaluate
```

Additional allowed edges are recorded in
`architecture/module-boundaries.json`. Important consequences:

- `domain` has no dependency on another target layer.
- extractors produce facts and coverage; they do not classify policy or render.
- comparison emits findings; it does not apply user severity or exit policy.
- evaluation does not parse binaries or invoke frontends.
- report code does not mutate/recompute compatibility or gate decisions.
- workflows do not import Click.
- interfaces translate to typed requests/results and contain no scanner logic.

## Working with legacy large modules

The current 2,000-line files are debt, not a design precedent. Files already
above the architecture threshold may shrink but may not grow.

Do not react to a full file by moving arbitrary functions to a new
`*_helpers.py`, `*_lib.py`, or one-function sibling. A valid extraction:

1. names a responsibility and its destination package;
2. moves the authoritative implementation and its tests together;
3. changes callers to import the new owner directly;
4. preserves only documented public imports through a thin explicit façade;
5. reduces dependency direction or responsibility count;
6. adds no import-cycle or boundary allowlist entry.

Tests should patch the owner of behavior. Do not keep a private re-export only
so an old monkeypatch path continues to work.

## Existing-to-target map

- `cli.py`, `cli_*.py`, `compat/cli.py` -> `interfaces`.
- `service*.py`, `scan_engine.py` -> `workflows`.
- `dumper*.py`, platform/debug metadata, `buildsource` extraction -> `evidence`.
- `diff_*.py`, comparability, reachability, bundle detectors -> `compare`.
- policy, suppression, severity, contract evaluation -> `evaluate`.
- reporter and format modules -> `report`.
- serialization, snapshot I/O, caches, schemas -> `storage`.
- shared models, finding identity, change-kind catalog -> `domain`.

This map is directional, not permission for a bulk rename. Move one vertical
slice at a time and preserve the typed request/result pipelines already used by
`service_compare_pipeline.py`, `service_dump_pipeline.py`, and `service_scan.py`.

## Common task recipes

### Add an evidence source or fact

Define/extend the fact value in `domain`, collect it in `evidence`, merge it in
one canonical normalization path, and add parity/coverage tests. Do not emit a
severity or report string from the extractor.

### Add a change kind

Add the catalog metadata once, implement detection in `compare`, classify it in
`evaluate` only when the default/policy behavior requires it, then test the
primitive detector and a public workflow. Do not duplicate kind lists in
renderers or CLI code.

### Add a CLI option

First identify the typed request/config field. The interface parses and passes
it; the workflow resolves precedence and executes. A Click callback must not
become the only implementation of product behavior.

### Add a report field or format

Add it to the canonical typed/report model with schema/version implications,
then project it in formats. A renderer may not independently infer a verdict,
coverage contribution, or exit code.

### Add a new workflow

Use `request -> resolved plan -> execute -> typed result`. Keep resource
lifetime in the plan/execution boundary and reuse existing extraction,
comparison, evaluation, and storage services.

## Public compatibility

Treat documented imports from `abicheck.service`, the typed Python API, and
published model/result types as public. Treat underscore-prefixed helpers,
module-local patch sites, and incidental test imports as internal unless a
documented contract says otherwise.

A compatibility façade must be explicit (`__all__`), logic-free, and targeted
below 150 lines. Record why it exists and when it can be removed.

## Verification

For changes in this package, run the repository-prescribed profile from root.
At minimum, architecture work must include:

```bash
python scripts/verify.py --profile pr \
  --only module-architecture-tests,module-architecture
python scripts/verify.py --profile fast
```

Use focused tests for the migrated responsibility, then the established broader
suite. A mechanical import move is not proven by import success alone; preserve
public workflow behavior and output/schema parity.
