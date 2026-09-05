### Fixed

- **`compare OLD_BUNDLE_FACTS NEW_DIR` now honors `.abicheck.yml`'s
  `bundle:` block.** CLI cleanup phase two's PR J removed
  `--bundle-system-providers`/`--bundle-cohort` as CLI flags in favor of
  `.abicheck.yml`, but the stored-BundleFacts dispatch path (`compare`
  against an already-captured bundle-facts document) kept deriving both
  settings from the now-removed Click kwargs, silently discarding a
  declared `bundle:` block instead of honoring it. Fixed: this path now
  reads `bundle.system_providers`/`bundle.cohorts` off the same resolved
  `--config`/auto-discovered `.abicheck.yml` the live/live compare path
  uses.
