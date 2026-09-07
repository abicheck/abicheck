### Added

- **New `surface_changes` report block ("what changed / review actions").**
  Every JSON `compare` report and Markdown view (full, `--report-mode leaf`,
  `--report-mode root-cause`, and the `--format review` digest) now carries
  a `surface_changes` section grouping every detected finding into
  `additions`, `removals`, and `modifications`, each entry carrying its
  `old_declaration`/`new_declaration`/`source_location` so a reviewer can
  act on it directly instead of reading the raw `changes` array. This makes
  workstream G's "compatible additions are visible changes" invariant
  explicit: a fully-compatible run — "0 breaking" — still itemizes what it
  added. Report schema bumped to 3.9 (additive; no existing key changes
  shape or meaning, and no verdict, gate, or exit code is affected). See
  `docs/contribute/plans/vision-api-abi-evolution.md`, workstream G slice
  S1.
