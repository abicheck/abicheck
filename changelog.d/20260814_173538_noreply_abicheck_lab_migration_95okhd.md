### Fixed

- **Closed two soundness gaps in the ctor/dtor synthetic-key
  format-drift-reconciliation infrastructure introduced earlier in this
  branch, and permanently disabled the one heuristic that produced a match
  at all.** `dumper_castxml.py`'s synthetic ctor/dtor snapshot key changed
  from a bare class name (`__abicheck_ctor__Foo()`) to a
  namespace-qualified one (`__abicheck_ctor__ns::Foo()`) in PR #582 —
  correct going forward, but it meant an old-format baseline and a
  new-format snapshot of the same unchanged declaration reported a
  spurious `func_removed`/`func_added` pair. An initial fallback in
  `finding_identity_ctor_dtor.py` merged such a pair whenever exactly one
  side's raw owner scope was namespace-qualified and the other was not.
  **That heuristic is now known to be unsound and has been disabled.**
  Investigation found that a non-namespaced (global) class on a CURRENT
  (post-PR-#582) snapshot *also* always produces a bare owner scope, so a
  real, breaking move of a class from the global namespace into a named
  one — between two current-schema snapshots, no legacy baseline involved
  — produces the identical bare-old/qualified-new split as genuine
  key-format drift; the two are indistinguishable from the keys alone.
  `AbiSnapshot` carries no `schema_version`/producer-version field a
  detector could branch on instead (verified against `model.py`), and even
  a hypothetical one would not help retroactively, since the key-format
  change itself shipped without any accompanying schema bump. Per this
  repo's "known gaps over risky reactive patches" convention, the fallback
  now unconditionally declines to merge — the previously-fixed
  false-positive on a genuine legacy-vs-current comparison is reported
  again (a visible false positive) rather than risk silently hiding a real
  namespace move (a hidden false negative). The reconciliation
  infrastructure (canonicalization, one-to-one-ambiguity matching,
  `iter_matched_function_pairs`) remains in place for a future fix with a
  genuine evidence source. See `finding_identity_ctor_dtor.py`'s module
  docstring for the full investigation.
- Fixed two further gaps in the (currently dormant, pending re-enablement)
  reconciliation wiring, both general and independent of whether the
  fallback above ever fires again: `diff_symbols._detect_newly_deleted_functions`
  now consults a reconciled pair's OLD-side key (not just the NEW-side key
  it iterates by) via `finding_identity_ctor_dtor.ctor_dtor_drift_old_by_new_key`,
  so a legacy-key constructor gaining `= delete` while the new snapshot
  already used the qualified key is correctly reported rather than
  silently reading as `NO_CHANGE`; and `_diff_func_deprecated`/
  `_diff_param_defaults` now look up per-declaration fact provenance under
  EACH side's own key (`f_old.mangled`/`f_new.mangled`) instead of one
  shared key, fixing a hybrid-snapshot false negative where a genuine
  `[[deprecated]]`/default-value transition on a reconciled pair was
  suppressed because the old snapshot's real provenance entry was probed
  under the new snapshot's key.
