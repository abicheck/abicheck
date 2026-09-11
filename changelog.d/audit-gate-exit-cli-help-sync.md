### Documentation

- `compare --help`/`--help-all` now documents the audit-gate exit axis
  (ADR-068's 2026-09-10 amendment): the `compare_cmd` docstring gained a
  fourth "orthogonal axis" paragraph alongside the existing
  `--contract`/`--require-complete-analysis` ones, and `--no-baseline`'s own
  help text now names `--severity-preset` as the sole switch that arms it
  under `--no-baseline` (any preset other than `info-only` contributes exit
  3). `docs/reference/exit-codes.md` already covered this axis; the CLI help
  surface, rendered by `compare --help-all`, previously had no mention of it
  at all.
