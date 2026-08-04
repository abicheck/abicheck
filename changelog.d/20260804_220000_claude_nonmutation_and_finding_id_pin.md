### Fixed

- `augment_report()` no longer rewrites its caller's report when
  neutralizing a `gate-mode: advisory` scan. Its copy is shallow, so writing
  the contract-coverage contribution through the nested `diff` mapping
  reached back into the caller's own payload, contrary to the function's
  documented non-mutation contract. Each container along a coverage path is
  now rebound to a copy first — cheap, and unlike a blanket deep copy it
  leaves the report's large `changes` payload untouched.
- `scripts/measure_contract_shadow.py` pins the accepted `public`
  unresolved-loss findings by `report_finding_id`, the same identity the
  decision receipt uses. Two distinct findings can share a kind and a
  symbol, so the previous `case:mode:kind:symbol` key still admitted a
  substitution.
