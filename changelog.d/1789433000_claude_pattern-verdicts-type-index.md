### Performance

- `--pattern-verdicts` no longer rebuilds every type name and rescans it for
  each layout finding, nor rescans every PIMPL tag per finding: both are
  indexed once per comparison (`pattern_verdicts._TypeNameIndex`,
  `_PimplPointeeIndex`), turning a types x findings scan into a linear one
  with identical results (tested against the old linear-scan algorithm on
  generated name collisions). Found by the new call-count complexity gate,
  `tests/test_compare_call_complexity.py`.
