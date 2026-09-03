# AGENTS.md — `abicheck/` package

This file scopes the repository-level `AGENTS.md` for production Python code.
It is vendor-neutral and authoritative for this directory. Tool-specific files
must point here rather than restating these rules.

## Read order

1. `/AGENTS.md` — repository-wide verification, compatibility, PR rules, and
   the canonical ADR-061 responsibility-package routing table
   (`model`/`storage`/`extract`/`compare`/`policy`/`workflows`/`report`/
   `frontends`) and dependency-direction rule. Read that table first — this
   file does not repeat it.
2. This file — task recipes and migration bookkeeping specific to
   `abicheck/`.
3. The nearest package `AGENTS.md`/`CLAUDE.md`, when one exists (e.g.
   `abicheck/buildsource/CLAUDE.md`).
4. [ADR-061](/docs/contribute/adr/061-responsibility-package-architecture.md)
   only when the task changes the contract itself.

The machine-readable boundary graph and no-growth debt ledger are
`architecture/modules.yaml` and `architecture/debt.yaml`
(`architecture/README.md`); the enforcement gate is
`python scripts/check_architecture.py` (`verify.py`'s `architecture` step).

## Working with legacy large modules

Files already above the architecture threshold (`architecture/debt.yaml`)
may shrink but may not grow — that ledger, not this file, is the source of
truth for which files are current debt.

Do not react to a full file by moving arbitrary functions to a new
`*_helpers.py`, `*_lib.py`, or one-function sibling. A valid extraction:

1. names a responsibility and its destination package;
2. moves the authoritative implementation and its tests together;
3. changes callers to import the new owner directly;
4. preserves only documented public imports through a thin explicit façade
   (`architecture/modules.yaml`'s `limits.facade` ceiling: 150 lines);
5. reduces dependency direction or responsibility count;
6. adds no import-cycle or boundary allowlist entry, and no new
   `architecture/debt.yaml` entry for the new file.

Tests should patch the owner of behavior. Do not keep a private re-export
only so an old monkeypatch path continues to work.

## Existing-to-target map

A quick-reference summary of the flat-namespace families and where new code
in that area belongs — `architecture/modules.yaml`'s `legacy_paths` is the
machine-checked version this table must stay consistent with:

- `cli.py`, `cli_*.py`, `compat/cli.py` -> `frontends`.
- `service*.py`, `scan_engine.py`, `bundle.py` and most other `bundle_*.py`
  orchestration/detection modules, `dumper_hybrid.py` -> `workflows`. This is
  not a whole-family rule: `dumper_cache.py` is `storage` and
  `bundle_manifest.py` is `model` — check `architecture/modules.yaml`'s
  `legacy_paths` for a specific file rather than assuming its prefix.
- most `dumper_*.py` parsing/AST-config modules, platform/debug metadata
  parsers, `buildsource` extraction -> `extract` (again excluding
  `dumper_cache.py`/`dumper_hybrid.py` above).
- `diff_*.py`, comparability, reachability -> `compare`.
- policy, suppression, severity, contract evaluation -> `policy`.
- `reporter*.py`, `html_report.py`, `sarif.py`, `junit_report.py` -> `report`.
- serialization, snapshot I/O, caches, schemas -> `storage`.
- shared models, finding identity, change-kind catalog -> `model`.

This map is directional, not permission for a bulk rename. Move one vertical
slice at a time and preserve the typed request/result pipelines already used
by `service_compare_pipeline.py`, `service_dump_pipeline.py`, and
`service_scan.py`.

## Common task recipes

### Add an evidence source or fact

Define/extend the fact value in `model`, collect it in `extract`, merge it in
one canonical normalization path, and add parity/coverage tests. Do not emit
a severity or report string from the extractor.

### Add a change kind

Add the catalog metadata once (root `AGENTS.md`'s "Adding a new ChangeKind"),
implement detection in `compare`, classify it in `policy` only when the
default/policy behavior requires it, then test the primitive detector and a
public workflow. Do not duplicate kind lists in renderers or CLI code.

### Add a CLI option

First identify the typed request/config field. The `frontends` layer parses
and passes it; the `workflows` layer resolves precedence and executes. A
Click callback must not become the only implementation of product behavior.

### Add a report field or format

Add it to the canonical `ReportDocument`/report schema, with schema/version
implications, then project it in formats (`abicheck/report/AGENTS.md`). A
renderer may not independently infer a verdict, coverage contribution, or
exit code.

### Add a new workflow

Use `request -> resolved plan -> execute -> typed result`. Keep resource
lifetime in the plan/execution boundary and reuse existing extraction,
comparison, policy, and storage services.

## Public compatibility

Treat documented imports from `abicheck.service`, the typed Python API, and
published model/result types as public. Treat underscore-prefixed helpers,
module-local patch sites, and incidental test imports as internal unless a
documented contract says otherwise.

A compatibility façade must be explicit (`__all__`), logic-free, and targeted
below `architecture/modules.yaml`'s 150-line facade ceiling. Record why it
exists and when it can be removed.

## Verification

For changes in this package, run the repository-prescribed profile from
root. At minimum, architecture work must include:

```bash
python scripts/verify.py --profile pr --only architecture
python scripts/verify.py --profile fast
```

Use focused tests for the migrated responsibility, then the established
broader suite. A mechanical import move is not proven by import success
alone; preserve public workflow behavior and output/schema parity.
