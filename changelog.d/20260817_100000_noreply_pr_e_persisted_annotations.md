### Added

- **`compare --format json` now persists `annotations` (schema 2.43) —
  CLI cleanup phase two, PR E's persistence prerequisite.** One
  already-classified, already-formatted entry (`{"level": "error"|
  "warning"|"notice", "annotation": "::error file=...,line=...,title=...
  ::message"}`) per finding a full annotation pass over the comparison
  found, always the superset (as if `--annotate-additions` had also been
  given) regardless of what this run's own `--annotate`/
  `--annotate-additions` flags were. Reuses the exact classification and
  formatting `compare --annotate`'s stderr output already uses
  (`annotations.collect_annotations`/`_format_annotation`), so the two can
  never disagree. Exists so a rendering front end other than the CLI's own
  stderr output — a future revision of the composite GitHub Action — can
  read an already-resolved answer instead of inferring one from stderr or
  re-running the comparison, the same "New invariant" this plan slice
  already established for `exit`/`analysis_assurance`. See
  `docs/contribute/plans/cli-cleanup-phase-two.md`'s PR E section for what
  else this slice unblocks and what remains.
