# AGENTS.md — aggregation workflow

## Purpose

This package owns fan-in of already-produced per-target ABI reports. It
resolves the expected-target contract, loads report facts, reconciles findings
across profiles, folds compatibility/coverage/gate axes, and returns one
`AggregateResult`.

## Permitted imports

Imports may follow the workflow layer contract: model, compare, policy,
storage, and extraction owners. Existing flat compare/model modules are
migration dependencies only. Never import `abicheck.aggregate`,
`abicheck.aggregate_findings`, or `abicheck.aggregate_manifest`; those are
external compatibility facades.

## Canonical entry points

- `contracts.py` owns immutable workflow values shared by the stages.
- `resolve.py` owns expected-target manifest validation and gate-policy
  resolution.
- `load.py` owns report-file and report-shape interpretation.
- `reconcile.py`/`matrix.py` own finding identity and cross-profile matching.
- `fold.py` owns the immutable aggregate result and its derived gate facts.
- `execute.py::aggregate_reports_dir` composes the stages.

`abicheck.aggregate` remains the supported compatibility import path for
external callers. New internal callers use the modules above.

## Tests

The semantic suite is `tests/test_aggregate*.py`; facade compatibility belongs
in `tests/test_aggregate_import_compatibility.py`. Patch the actual stage owner,
not a re-export in the facade.

## Prohibited responsibilities

Do not analyze binaries, rerun comparison, recompute a target's severity
policy, translate Click parameters, or write output here. A target's recorded
gate decision remains authoritative. Missing required coverage is an
orthogonal exit contribution, not an ABI-break verdict.

Keep JSON/text projection behavior compatible until the canonical
`ReportDocument` phase moves all rendering to `abicheck.report`.

## Change checklist

Preserve the three independent axes: compatibility verdict, policy-aware gate,
and required-target coverage. Contract coverage and analysis assurance remain
orthogonal contributions as well. New fields must be carried through the typed
result and the stable aggregate schema deliberately.

A migration change must keep `abicheck.aggregate` import identity compatible,
update the facade's explicit `__all__`, and prove no production caller imports
the facade. Reduce the aggregation entries in `architecture/debt.yaml`; never
raise their baselines to accommodate a move.

Run the aggregate semantic suite, the compatibility-import suite, the focused
architecture checker, and the canonical PR verification profile.
