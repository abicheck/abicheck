### Changed

- **One owner for the per-change verdict a renderer shows** — the summary,
  JUnit and scoped-gate paths each carried a private copy of "the finding's
  verdict if resolved, else the policy resolver". The rule now lives once, as
  `policy.evaluate.verdict_for`, and the duplicate in `junit_report.py` is
  deleted. Report output is unchanged.
