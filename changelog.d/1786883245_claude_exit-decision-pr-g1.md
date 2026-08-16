### Added

- **CLI cleanup phase two, PR G1**: a new, canonical `exit_decision.ExitDecision`
  resolves every orthogonal exit-code axis (the compatibility gate -- or,
  under `--used-by`/`--required-symbol(s)` scoping, the scoped gate --,
  contract coverage, and analysis assurance) for a single-pair `compare`/`scan
  --against` comparison into one explainable object -- `code` plus which
  axis or axes actually determined it. `compare --format json`'s report now
  persists this as a top-level, schema-optional `exit` object (schema 2.41),
  and the real process exit is computed through the same resolver, so a
  report reader and the CLI cannot disagree about why a comparison exited the
  way it did. `compat check`'s JSON output (its own ABICC 0/1/2 exit scheme)
  omits the block rather than emit a disagreeing `code`. No CLI flag changes
  and no change to any existing exit code -- this is the first, additive step
  toward the phase-two plan's `--exit-code-scheme` consolidation, not a
  behavioural change on its own.
