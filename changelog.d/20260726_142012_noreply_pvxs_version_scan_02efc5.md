### Fixed

- **Two more comparability-gate correctness bugs (Codex review): an
  unrecognized scope field could hide behind a waived known one, and a
  syntactically valid but non-string JSON list could crash the gate.** The
  scope carve-out's `all(...)` only ever checks `SCOPE_FIELD_KEYS`
  (`headers`/`public_header_dirs`), so a contract carrying an extra
  `scope_fields` key this build doesn't recognize was invisible to it —
  if the two known fields happened to be equal or additive, the whole
  `scope_fingerprint` mismatch was silently waived without ever examining
  the unrecognized field. Fixed with a `scope_unknown_differing` check,
  mirroring the profile-side `unknown_differing` check from the previous
  round. Separately, last round's `_json_load_list` guard validated only
  that a `profile_fields`/`scope_fields` value decodes to a JSON *list*,
  not that its members are plain strings — a syntactically valid but
  non-scalar member (e.g. `headers: "[{}]"`) still raised
  `TypeError: unhashable type: 'dict'` downstream instead of declining
  cleanly. Fixed with a new `_json_load_str_list` helper, used everywhere
  a plain string-identity list is expected.
