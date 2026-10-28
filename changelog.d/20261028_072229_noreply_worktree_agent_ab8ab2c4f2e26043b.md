<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The one-line summary's breakdown adds up to its total** -- `compare -o
  oneline=...` (and `--stat`) omitted the `informational` and
  `quality_issues` findings from its breakdown while still counting them in
  the total, so `5 breaking, 2 compatible (8 total)` was printed for a run
  whose JSON summary carried `breaking` 5, `compatible_additions` 2 and
  `informational` 1. The line now names every outcome bucket (`5 breaking, 2
  compatible, 1 informational (8 total)`), and a finding the compatibility
  policy did not score is stated as `N not evaluated` instead of being
  dropped. The release-level line and the scoped consumer fold read the same
  bucket table, so the three cannot drift apart again.
