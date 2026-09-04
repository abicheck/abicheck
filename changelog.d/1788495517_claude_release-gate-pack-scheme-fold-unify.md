### Changed

- **ADR-063 Track A, 7B**: `pack_application.apply_to_compare_config`
  (single-pair `compare`'s gate-pack fold) and `policy.release_gate_options
  .apply_release_gate_pack` (the directory/package release fan-out's own
  fold) no longer independently re-derive the same "which way does the
  exit-code-scheme move" precedence. `policy.release_gate_options.
  resolve_gate_pack_exit_code_scheme` is now the one function both call for
  that piece, guarded by the existing `tests/
  test_release_gate_pack_fold_parity.py` property test. No behavior change
  for either command -- purely an internal deduplication of logic that
  previously had a real regression history (Codex review, PR #1032). The
  two fold functions' *severity-level* application remains separately
  expressed against their different pre-resolution shapes, deferred to
  ADR-064's own named "PR G2" prerequisite work.
