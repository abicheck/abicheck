<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Internal: `compare-release`'s gate-pack folding is now pinned against
  single-pair `compare`'s own equivalent logic by a property test** —
  `tests/test_release_gate_pack_fold_parity.py` (ADR-063 Track 4, 7B
  investigation). `policy.release_gate_options.apply_release_gate_pack`
  independently mirrors `pack_application.apply_to_compare_config`'s
  severity/exit-code-scheme fold logic (documented, deliberate, pending a
  larger unification); this closes the drift risk with a Hypothesis
  equivalence test instead. No behavior change.
