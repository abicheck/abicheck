### Added

- **Failed-member exit contribution in the release report.** A directory or
  package release's `comparison_scope` block now carries
  `failed_member_exit_contribution` (`0` or `4`, ADR-065 D8): the same number
  the release exit folds in for a `failed` member. A JSON consumer can read
  why the run exits `4` from the scope block itself. The key is additive and
  optional; the report schema moves to 7.5 and the release envelope to 3.2.
