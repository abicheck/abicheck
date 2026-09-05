### Changed

- Internal (no user-visible behavior change): the gate-pack severity fold is
  now written once. `policy/gate_pack_fold.py`'s `fold_gate_pack_severity` is
  the single implementation of "a `kind: gate` pack's level replaces the
  pre-pack value for exactly the categories the pack supplied"; both
  single-pair `compare`'s `pack_application.apply_to_compare_config` and the
  directory/package release fan-out's
  `policy.release_gate_options.apply_release_gate_pack` call it instead of
  each expressing the rule in its own idiom. The new module is a leaf inward
  of both callers, which is what lets them share it without `policy`
  importing the flat-root `pack_application` (duplication-and-convergence-
  assessment track T6).
- Internal: `GateOptions.exit_code_scheme` and
  `ResolvedCompareConfig.exit_code_scheme` are derived read-only properties
  rather than independently settable dataclass fields. Both were already
  documented as purely derived from their neighbouring "is severity in
  effect" predicate, but the model still permitted the two to disagree — and
  two unit-test helpers were in fact constructing a `GateOptions` whose
  scheme contradicted its own severity config. Every site that re-spelled the
  `"severity" if ... else "legacy"` derivation now calls one shared
  `gate_exit_code_scheme`.
