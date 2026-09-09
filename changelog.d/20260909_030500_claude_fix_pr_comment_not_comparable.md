<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The sticky PR comment rendered "No ABI changes" for a `mode: compare`
  scope/profile mismatch that never ran a comparison at all** (Codex
  review, round 12). Once the exit-16 `NOT_COMPARABLE` mapping let
  `_maybe_post_pr_comment()` reach `pr_comment.build_model()` for
  `_report_not_comparable()`'s refusal document, `_from_compare()` read
  `report.get("changes")` — absent on that document (only root
  `{"verdict": null, "reason": {...}}` exists) — so every compatibility
  bucket stayed empty and the comment (and `should_post`'s default
  `pr-comment-on: changes` gate) treated the run as a clean, comparison-free
  pass. `_from_compare()` now detects this shape (mirroring
  `pr_comment_scan.from_scan`'s existing `diff["reason"]` handling for
  scan's own `NOT_COMPARABLE`) and surfaces it as a single blocking
  "analysis incomplete" finding, so the comment reads "🛑 Source analysis
  incomplete" with the real mismatch message, and posts even under the
  default `--on changes` policy. Regression test built from the real
  `not_comparable_document`/`render_not_comparable_json` report builder,
  not a hand-rolled dict, proven to fail pre-fix and pass post-fix.
