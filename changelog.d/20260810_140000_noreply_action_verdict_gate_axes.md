### Fixed

- **A demoted break stays visible at a promoted exit code too** — the composite
  Action's verdict mapping read the report's compatibility verdict only for
  exit 0, so a severity policy that demotes `abi_breaking` below `error` while
  something else still gates (an error-level `--crosscheck`, or
  `potential_breaking: error`) exited 2 and published `API_BREAK` for a run
  whose report says `BREAKING` — a workflow branching on `verdict` acted on the
  wrong tier. The published verdict now follows the report whenever the report
  is the more severe of the two, on `compare` and `scan` alike. The *gate*
  deliberately does not move with it: it keeps following the tier the exit code
  gated at, because letting an escalated `BREAKING` reach `fail-on-breaking`
  (default true) would re-gate the very finding the severity policy demoted.
  `action.yml` now documents `verdict` and `exit-code` as the two different
  axes they are — what was *detected* versus what was *blocked*.
