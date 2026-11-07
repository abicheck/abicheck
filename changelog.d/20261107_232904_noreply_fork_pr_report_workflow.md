### Added

- **Turnkey fork-PR comments** — the main Action's new `pr-comment-via`
  input (`direct` default, `artifact`, `auto`) uploads the report, with the
  analysed commit recorded, instead of posting from a job whose token cannot
  write; the new reusable `.github/workflows/report-pr.yml` verifies and
  publishes it from a `workflow_run` job. A project's publisher is now an
  eight-line file, and each run takes one path, so wiring both never
  double-posts. `pr-comment-artifact-name` overrides the artifact name. See
  `docs/use/fork-pr-reporting.md`.
