### Added

- **Accepted breaks are visible on a passing run** — under `acknowledgment.breaks: accept`, the JSON report's `disposition_audit` gains `accepted_breaks` (`kind`, `symbol`, `verdict_class` per acknowledged break made non-gating; report schema 6.4), and the GitHub Action emits a `::notice::` naming the count and the first five symbols ("and K more" past that) when the run exits 0 with the verdict still a break. Symbols are escaped for workflow commands (`%`, CR, LF). No notice under `breaks: gate` or when nothing was accepted.
