### Added

- **New `actions/resolve-baseline` composite Action** (G30 P1.2, ADR-047
  §4/§6) resolves one check's baseline — `channel × target/bundle × profile`
  — against an already-staged baseline-set, returning one of six typed
  outcomes (`resolved`, `not_found`, `ambiguous`, `wrong_profile`,
  `stale_schema`, `incompatible_evidence`) instead of ever silently
  degrading to "no baseline = compatible." Supports both `kind: target`
  (returns a resolved `.abicheck.json` snapshot path) and `kind: bundle`
  (returns every member's staged binary path, since bundle analysis reads
  real ELF binaries, never JSON snapshots). New
  `abicheck/buildsource/baseline_set.py` is the shared, pure resolver
  backing it.

