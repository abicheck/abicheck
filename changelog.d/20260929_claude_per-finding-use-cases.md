### Added

- **Per-finding use-case attribution.** Under `compare --use-cases MANIFEST`,
  every JSON `changes` entry now carries `affected_use_cases` (report schema
  5.10): the sorted use cases whose declared entrypoints reach that finding.
  It is the report-level `use_case_impact.by_use_case` block read per
  finding (joined on `finding_id`), so the two always agree, and it follows
  `--view show=...` the same way. `[]` means no declared entrypoint was shown
  to reach the finding; the key is absent without `--use-cases`. The Markdown
  report shows an "Affects use cases" note under each reached finding, and
  the review digest's review groups and impacted-symbol list name them too.
