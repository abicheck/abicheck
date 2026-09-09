### Removed

- **`compare`'s release/bundle topology flags are gone (Phase 7d)** —
  `--dso-only`, `--fail-on-removed-library`/`--no-fail-on-removed-library`,
  `--include-private-dso`, and `--on-incomplete-scope` no longer exist on
  `compare`'s CLI (each exits `64`, "No such option"). `.abicheck.yml`'s
  `release.dso_only`, `gate.fail_on_removed_library`,
  `release.include_private_dso`, and `scope.on_incomplete` are their only
  source now — stable release-topology/CI-gate properties, not per-run
  choices. `--instantiation-manifest`, `--bundle-facts-out`, and
  `--bundle-facts-library-manifest` stay CLI flags this phase pending,
  respectively: a real ADR-049-coordinated config home for a declared
  contract document, an equivalent `dump` directory/package capability, and
  the G42 named-environments prerequisite.
