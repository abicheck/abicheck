### Fixed

- **A demoted break stays visible at a promoted exit code too** — the composite
  Action's verdict mapping read the report's compatibility verdict only for
  exit 0, so a severity policy that demotes `abi_breaking` below `error` while
  something else still gates exited non-zero and published the *gating* tier
  for a run whose report says `BREAKING` — a workflow branching on `verdict`
  acted on the wrong tier. This affected both promoted exits: an error-level
  `--crosscheck` or `potential_breaking: error` published `API_BREAK` at exit
  2, and an error-level `addition`/`quality_issues` finding published
  `SEVERITY_ERROR` at exit 1. The published verdict now follows the report whenever the report
  is the more severe of the two, on `compare` and `scan` alike. The *gate*
  deliberately does not move with it: it keeps following the tier the exit code
  gated at, because letting an escalated `BREAKING` reach `fail-on-breaking`
  (default true) would re-gate the very finding the severity policy demoted.
  `action.yml` now documents `verdict` and `exit-code` as the two different
  axes they are — what was *detected* versus what was *blocked*. Only a break may escalate — a
  `COMPATIBLE` report never displaces `COVERAGE_INCOMPLETE` or
  `SEVERITY_ERROR`, which would invert the point.
