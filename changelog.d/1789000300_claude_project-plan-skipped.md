### Changed

- **`project plan --allow-empty` is retired; an empty plan now explains
  itself.** The two empty run-plans mean opposite things and used to be one
  error with one bypass flag past both. A `CONFIG` declaring no
  `targets:`/`bundles:` `checks[]` at all — a project bootstrapping
  `.abicheck.yml` — now produces an *explained skipped plan*: exit `0`, with
  a `skipped` block (`reason`, `declared_checks`, `explanation`) in
  `run-plan.json` and a pointer to `abicheck project validate CONFIG` for the
  config's own well-formedness. A `CONFIG` that declares `checks[]` which
  resolve to no cell at all still exits `1`, and there is no longer any way
  to accept it — that was the capability `--allow-empty` provided, and it is
  the "a CI matrix silently gates nothing" case. The old spelling is a usage
  error (exit `64`), with no hidden alias. A plan carrying `skipped` is
  stamped `schema: abicheck.run-plan/v3`; a plan with checks keeps its
  existing `v1`/`v2` spelling.
