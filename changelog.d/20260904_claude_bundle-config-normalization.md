### Fixed

- **`.abicheck.yml`'s `bundle.system_providers:`/`bundle.cohorts:` entries
  are now stripped of surrounding whitespace once, at parse time**, instead
  of inconsistently across consumers. `compare`'s directory/package fan-out
  incidentally stripped entries via a comma-join/split round trip through a
  legacy string parameter, while `scan --artifact-set` and stored-BundleFacts
  compare forwarded the raw config value unchanged — so a quoted entry with
  stray whitespace could suppress a provider during a live comparison but
  emit a false unresolved-dependency finding under `scan --artifact-set` or
  a stored-facts comparison for the identical config.
- **The GitHub Action's `build-config` input description no longer implies
  `bundle.cohorts:` applies to `scan --artifact-set`.** The SONAME-skew
  check needs an old/new release pair to detect a skew between; an
  `--artifact-set` audit has none, so the setting has no effect there —
  only `bundle.system_providers:` applies to both modes.
