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
  report already does; `NOT_COMPARABLE` (a scope/profile mismatch) is now its
  own Action verdict, and header counts stay exact even when a large diff's
  `findings`/`additions` were truncated below the report cap.
- **`scan` gained `--secondary-format`/`--secondary-output`**, mirroring
  `compare`'s own flags: render a second output format (typically JSON)
  from the same scan run without re-running it. The GitHub Action's own
  PR-comment renderer uses this to avoid a second, potentially
  `--depth build/source`-expensive scan when the primary step output stays
  the documented default `--format text`.
