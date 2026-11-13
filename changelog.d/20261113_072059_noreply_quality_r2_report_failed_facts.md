### Fixed

- **Every report format now shows an incomplete analysis, and none calls it
  clean** — a snapshot section whose extractor failed (or was blocked or not
  collected), a comparison forced through `--diagnostic-comparison`, a
  partial contract coverage, a finding contract evaluation left
  `NOT_EVALUATED`, and an unresolved public surface were recorded in the JSON
  report but dropped by some formats: the full Markdown and HTML reports,
  JUnit, `--stat` and the Action comment/job summary each showed a different
  subset, and the review digest read "✅ NO_CHANGE — no ABI/API change — safe
  to merge" over all of them. Each format now states them under one
  "Analysis incomplete" heading: Markdown (full and root-cause), HTML, the
  review digest and terminal view, the Action comment, `--stat`
  (`[analysis incomplete: <keys>]`), SARIF (`toolExecutionNotifications`
  with `abicheck.analysis_incomplete.<key>`) and JUnit (one `<skipped>`
  testcase per fact). The review headline and the Action headline no longer
  show a green check or "safe to merge"/"No ABI changes" for such a run. A
  forced not-comparable run's `analysis_assurance` also keeps its
  `section_availability` entries instead of dropping them.
