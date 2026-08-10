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
  `SEVERITY_ERROR`, which would invert the point. The job summary
  names the displaced gate too: an escalated verdict describes a break that is
  not why the step failed, so both the `BREAKING` and `API_BREAK` branches now
  render which tier actually gated and which severity categories blocked —
  through one shared note, since the `API_BREAK` branch having its own copy
  and `BREAKING` having none is exactly how escalation produced a failing
  summary that mentioned only the ABI break. An escalated
  contract-coverage gate keeps its own axis: `GATE_TIER` can hold
  `COVERAGE_INCOMPLETE`, and describing that as a severity-policy failure is
  the exact confusion ADR-049's orthogonal axis exists to prevent — the note
  now names the coverage axis and still reports which provider fell short,
  which escalation had been dropping along with the displaced verdict branch. Two further corrections to
  that note: the "independently of `fail-on-*`" claim is now made only at the
  `SEVERITY_ERROR` tier, since at `API_BREAK`/`BREAKING` the severity policy
  produced the exit but the fail-on flags still decide whether the step fails;
  and the contract-coverage axis is reported on its own terms rather than only
  when it happens to own `GATE_TIER`, so a run where both exit-1 axes fire no
  longer hides the missing provider behind the severity tier. `scan` is the
  second case that bypasses the flags at every tier — its final branch blocks
  unconditionally on a configured severity category — so the note claims
  independence there too. Three more: a promoted `--crosscheck` is named as
  itself rather than as the severity policy (it is filtered out of the
  blocking-category list and still follows `fail-on-api-break`, so pointing at
  the severity policy pointed at a knob that would not change the outcome);
  and the verdict fallback parser now reads the release renderer's
  `| **Verdict** | … |` table row, not only the `Verdict:` spelling — a
  directory/package compare rejects `--secondary-format`, so a markdown
  release comparison reaches that parser with no JSON at all and every one of
  them published the gating tier for a report that said `BREAKING`.
