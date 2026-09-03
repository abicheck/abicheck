### Fixed

- **A `scan` abort's forced `aggregate` gate invented a compatibility
  verdict and an analyzed-target count for a comparison that never ran**
  (PR review finding). `workflows/aggregate/load._load_report_file`'s
  forced-blocking branch for `"BUDGET_OVERFLOW"`/`"EVIDENCE_CONTRACT_ERROR"`
  set `compatibility_verdict=Verdict.BREAKING`, so `AggregateResult.to_dict()`
  reported `compatibility.verdict: "BREAKING"`, a complete `analyzed_targets`
  count, and an affected profile even though the only known fact was the
  separate blocking abort gate — a scan that aborted before comparing never
  produced an ABI-break finding. The target now stays `compatibility_verdict
  = None` (unavailable) for a scan abort, while its forced gate still counts
  toward `AggregateResult.exit_code()`/`blocking_targets` regardless of the
  target's own required/optional declaration, via a new
  `AggregateResult._forced_gate_targets` fold (the unavailable-but-gated
  shape `operational_error`/`not_comparable` reports don't need, since those
  keep the synthetic `BREAKING` verdict this fix removes only for scan
  aborts). Text rendering surfaces the forced gate on the "unavailable" line
  too, not only in the JSON `gate` block.
