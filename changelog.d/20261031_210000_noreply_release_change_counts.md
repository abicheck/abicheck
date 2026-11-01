### Added

- **Release reports count findings by entity and operation** — a
  directory/package `compare -o json` report (release schema 2.7) carries
  `change_counts` on every compared member and at the top level: findings
  per entity (`function`, `variable`, `type`, …) and operation (`removed`,
  `modified`, `added`), counted before any `output.view` `show=` filter. The
  PR comment's "What changed" table for a release now reads it, so it shows
  exact totals instead of "Not exact totals" lower bounds.
