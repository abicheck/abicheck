### Fixed

- The full-CLI L2 perf harness validates **both** exports of its two-format
  scenario against the same break-family expectation. The JSON half was checked
  only for "did this reach L2" and the rendered half only for the word
  `BREAKING`, so a renderer or comparison that kept the verdict metadata and
  dropped the removal/layout findings was accepted as a faster run — which
  defeats the scenario's whole claim that one completed analysis feeds both
  artifacts.
- The multi-library workload is measured as one cache lifecycle, reset once
  before the set rather than before each member. Every member previously started
  from an empty cache, so cross-library reuse — the only thing distinguishing the
  shared-context arm from the distinct-context one — was unobservable by
  construction. With the fix the measurement reports that no cross-library reuse
  happens today (identical extraction and include-pass counts in both arms);
  that is recorded in `docs/contribute/performance.md`, not acted on.
- The performance workflow decides whether to gate the head run against a base
  receipt by asking the gate's own rule how many baseline points that receipt can
  supply (`check_l2_cli_perf.py --count-gateable-points`), rather than testing
  the file for non-emptiness. A base run that failed every scenario still writes
  a full diagnostic receipt, so the old test passed and the head run then failed
  for zero overlap — turning an unmeasurable base into a failed PR.
