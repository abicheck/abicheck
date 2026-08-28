<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 bundle analysis: signature-change promotion no longer attributes a
  consumer to the wrong provider, and now honors a `--policy-file`
  override, not just a named base policy.** `_detect_intra_dep_signature_
  changed` previously treated *any* sibling importing a changed symbol's
  name as affected by the change — if two unrelated libraries in the same
  bundle happen to export a same-named symbol, a consumer that only needs
  the *unchanged* one could still receive a fabricated
  `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED` finding attributed to the changed
  one. Fixed by requiring the consumer to actually resolve the symbol
  against that specific provider (reachability, symbol versioning, and
  default-definition matching — mirroring the same checks
  `_detect_unresolved_intra_dependency` already applies for its own
  provider-matching). Separately, a `PolicyFile` override demoting a
  promotable kind (e.g. via `--policy-file`) is resolved through a
  different path than a named base policy (`policy_file.compute_verdict`,
  which does not populate `Change.effective_verdict`), so the promotion
  check previously couldn't see it — promotion now defers to the
  originating diff's own `policy_file` when one is present, the same way
  it already couldn't defeat a named-policy demotion like `plugin_abi`.
