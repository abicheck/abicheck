### Fixed

- **A `CompareRequest` pairing an unknown `policy` name with a valid
  `policy_file_path` was accepted through comparison, then failed while
  installing the resolved gate receipt.** `load_suppression_and_policy`
  already treats the file as authoritative in this combination (the ignored
  `policy` name chooses nothing), but the gate-receipt installer forwarded
  the raw, unknown name into `builtin_policy_identity`, which raises for
  anything outside the built-in policy set — turning an otherwise-completed
  comparison into a receipt-install failure. `compare_request_inputs` now
  normalizes the name through `stated_policy_base` first, the same fix
  already applied on the `scan --against` side.
