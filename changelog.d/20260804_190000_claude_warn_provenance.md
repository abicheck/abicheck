### Fixed

- `aggregate`'s text output no longer claims a target accepted incomplete
  contract evidence via `contract.unresolved=warn` when the report merely
  omitted its coverage contribution or stated an unusable one. The
  contribution reads `0` in all three cases — correct for the gate, which
  must not block on any of them — but only the declared `0` is a policy the
  run actually set.
