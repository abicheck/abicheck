### Changed

- **Split three more CodeFactor "Complex Method" findings, in the unnamed-type
  detector, the stack ABI-risk verdict and the platform-baseline floor check.**
  `diff_unnamed_types._unnamed_kind`'s Itanium token-boundary scan becomes one
  skip function per production behind `_next_token_index`;
  `stack_checker._compute_abi_risk` moves its per-change classification into
  `_stack_change_risk`/`_abi_diff_risk`, leaving the fold and the verdict;
  `diff_versioning._check_baseline_floor_for_prefix` gains a `_FloorScan`
  accumulator, so the DT_RELR floor — previously folded in by two hand-written
  copies, one per way it can be observed — is stated once. All three
  behaviour-preserving and verified against their pre-refactor selves: 671,465
  inputs for the token scan, and exhaustive branch coverage for the other two
  (all 360 stack-change combinations, all 840 baseline-floor combinations) — no
  differences.
