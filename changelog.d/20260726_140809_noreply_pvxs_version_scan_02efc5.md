### Fixed

- **Two more comparability-gate correctness bugs (Codex review): unknown
  profile deltas could hide behind a waived recognized one, and carve-out
  helpers could crash on malformed JSON.** `check_contracts_comparable`'s
  `differing` set only ever iterates `PROFILE_FIELD_KEYS`, so a contract
  carrying an extra field this build doesn't recognize (a newer schema key)
  was invisible to it — if that unrecognized delta co-occurred with an
  otherwise-legitimate, carve-out-waived delta (e.g. additive
  `header_sequence` growth), the pair was wrongly reported comparable,
  silently ignoring the unrecognized field. Fixed with a new
  `unknown_differing` check, computed independently of and applied before
  any carve-out result is trusted. Separately, `_scope_field_is_additive_superset`,
  `_header_sequence_is_additive_reorder_free`, and
  `_include_sequence_is_additive_owned_growth` all called `json.loads` on
  untrusted `str` inputs unguarded, so a malformed `profile_fields`/
  `scope_fields` value (e.g. from a hand-constructed or corrupted
  serialized contract) raised an unhandled `JSONDecodeError` instead of the
  clean `ScopeMismatchError`/`ProfileMismatchError` the gate is supposed to
  fail closed with. Fixed with a shared `_json_load_list` helper that
  declines (rather than crashes) on any decode failure or non-list result.
