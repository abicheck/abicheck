### Fixed

- **The review digest's category counts and Markdown's severity-summary
  table now reuse a `ReportEnvelope`'s own finalized findings/resolution
  date instead of independently recomputing them** — `compute_review_digest`
  already passed the envelope's findings to its impacted-symbols list, but
  its breaking/source-break/risk/compatible/quality-issue counts still came
  from `build_summary(result)`, which called `result.breaking`/`.compatible`
  and `classify_effective_change` fresh. `build_summary` gained an optional
  `findings` parameter so those counts are read from the same already-
  resolved verdicts/categories. Separately, `compute_severity_summary`'s
  `categorize_changes` calls had no way to pin a dated `PolicyFile.
  reclassify` rule's expiry check to the envelope's own `resolved_today`;
  `categorize_changes` gained a `today` parameter, threaded through from
  `envelope.resolved_today`. Without either fix, a rule expiring between
  envelope construction and render could make the review digest or the
  severity-summary table disagree with the verdict shown elsewhere in the
  same rendered document.
