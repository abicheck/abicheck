<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **The GitHub Action's sticky PR comment now supports `mode: scan`** (single
  artifact, not `new-library-set`) — `pr-comment`/`pr-comment-on`/
  `pr-comment-mode`/`pr-comment-detail` all work the same way they already do
  for `compare`, rendering `scan`'s own verdict, breaking/needs-review
  findings, a green "Public API additions" section, and a short risk/coverage
  summary line, without a second `compare` run. `scan --against`'s JSON gained
  an always-on `additions` array (schema `1.13`, `cli_scan_baseline.py`) so
  the comment can render new public-API surface the same way `compare`'s own
  report already does.
