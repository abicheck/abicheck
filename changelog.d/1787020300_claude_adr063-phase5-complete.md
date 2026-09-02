### Changed

- **ADR-063 Phase 5 (the fact/capability registry) is complete.** Its
  Status paragraph, `adr/index.md` row and the implementation plan's own
  Phase 5 section now record that, and
  `KNOWN_UNCONVERTED_ELIGIBLE_FACTS`'s two halves are documented as
  deliberately-retained empty baselines (the completeness gate reads them
  in both directions, so a newly-added eligible field fails outright
  rather than joining a silent allowlist).
- **`storage/fact_codec.py` split.** The case-(a) legacy-load corrections
  moved to `storage/fact_backfill.py` and the per-field schema-version
  thresholds both modules need to the `storage/fact_schema_versions.py`
  leaf, keeping every module under ADR-061's 800-line ceiling with no
  import cycle. `fact_codec` re-exports the three moved names, so every
  existing import path is unaffected.
