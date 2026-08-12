<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`protect-committed-baseline.yml`'s glob matcher** now translates
  `**/` as zero or more *complete* path segments
  (`(?:[^/]+/)*`) instead of `.*` followed by an optional `/` — the old
  translation let the `.*` consume a partial segment while the optional
  `/` matched zero times, so a pattern like `baselines/**/manifest.json`
  wrongly matched an unrelated file like `baselines/notmanifest.json`
  too. This required protection check could therefore falsely fail a PR
  that never touched a protected path at all.
