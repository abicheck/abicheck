### Changed

- **One owner for each step a release member shares with a single-pair
  compare** (release-via-pair-sessions plan, Phase 2) — following a GNU ld
  linker script is `binary_utils.follow_linker_script` (re-exported by
  `workflows.extraction`) for the single-pair session, every release member
  and `dump`, with `linker_script_note` as the one wording `resolve_input`
  also uses; `release_members.normalize_member_operand`,
  `workflows.compare.session_inputs.normalize_operand`,
  `cli_resolve._maybe_follow_linker_script`,
  `cli_resolve._normalize_binary_input` and the now test-only
  `cli_resolve._resolve_linker_script` are deleted. The ADR-050 refusal kind
  is a class attribute, `ProfileMismatchError`/`ScopeMismatchError.not_comparable_kind`,
  replacing `workflows.release_scope.mismatch_kind` and its two inline copies.
  A release side's headers are composed by `release_inputs.release_side_headers`
  (replacing the test-only `resolve_release_headers`). A member's exit
  decision derives its gate with `workflows.gate.effective_gate_for_compare`,
  the single-pair derivation. Output and exit codes are unchanged.
