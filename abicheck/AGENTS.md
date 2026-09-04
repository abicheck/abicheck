# AGENTS.md — `abicheck/` package

This file scopes the repository-level `AGENTS.md` for production Python code.
It is vendor-neutral and authoritative for this directory's *rules* (task
routing, dependency direction, migration policy) — a tool-specific *adapter*
file (one whose only job is pointing at the canonical source, e.g. this
directory's own `CLAUDE.md`) must point here rather than restating those
rules. This does not apply to a genuinely scoped subdirectory `CLAUDE.md`
carrying its own per-area context (pipeline orientation, test pointers, and
similar) — see root `CLAUDE.md`'s own distinction between an adapter and
scoped context, which this directory's `CLAUDE.md` follows.

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
are debt, not a design precedent — that ledger, not this file, is the
source of truth for which files are current debt. `scripts/check_architecture.py`
enforces no-growth relative to each file's own recorded adoption baseline
(never exceed it), not a stricter never-add-a-line rule against the PR's own
base revision: a file that has already shrunk below its baseline may still
grow back up to that baseline without tripping the gate. Treat the baseline
as a ceiling to pay down, not a floor to hug.

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

There is no reliable prefix-based rule here: most flat-namespace families
(`dumper_*`, `bundle_*`, `service*`, `diff_*`/`type_*`/`source_*`,
`snapshot_*`, `pack_*`, `report_*`, `scan_*`, `checker_*`, and others) are
already split across two or three target layers by responsibility, not by
filename. **Look up the specific file in `architecture/modules.yaml`'s
`legacy_paths` (per-layer lists) rather than assuming its prefix** — an
earlier version of this table generalized several families wholesale and
was repeatedly wrong for a specific existing file (`dumper_cache.py` is
`storage`, not `extract`; `bundle_manifest.py` is `model`, not `workflows`;
`service_metadata_attach.py` is `extract` and `service_render.py` is
`frontends`, not `workflows`).

Move one vertical slice at a time and preserve the typed request/result
pipelines already used by `service_compare_pipeline.py`,
`service_dump_pipeline.py`, and `service_scan.py`.

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
