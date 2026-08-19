### Added

- **`compare --format json` now persists `annotations` (schema 2.43,
  `always_visible` added at 2.44) — CLI cleanup phase two, PR E's
  persistence prerequisite.** One already-classified, already-formatted
  entry (`{"level": "error"|"warning"|"notice", "annotation": "::error
  file=...,line=...,title=...::message", "always_visible": true|false}`)
  per finding a full annotation pass over the comparison found, always the
  superset (as if `--annotate-additions` had also been given) regardless
  of what this run's own `--annotate`/`--annotate-additions` flags were.
  `always_visible` is what a consumer must actually gate a `"notice"`
  entry on instead of `level` alone — one notice kind (a `--contract`
  finding compatibility policy never evaluated) is shown by plain
  `--annotate` with no `--annotate-additions` at all. Reuses the exact
  classification and
  formatting `compare --annotate`'s stderr output already uses
  (`annotations.collect_annotations`/`_format_annotation`), so the two can
  never disagree. Exists so a rendering front end other than the CLI's own
  stderr output — a future revision of the composite GitHub Action — can
  read an already-resolved answer instead of inferring one from stderr or
  re-running the comparison, the same "New invariant" this plan slice
  already established for `exit`/`analysis_assurance`. See
  `docs/contribute/plans/cli-cleanup-phase-two.md`'s PR E section for what
  else this slice unblocks and what remains.
