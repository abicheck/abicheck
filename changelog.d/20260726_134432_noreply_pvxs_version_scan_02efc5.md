### Fixed

- **Two comparability-gate correctness bugs (P1, Codex review): sequence
  carve-outs required scope corroboration, and opaque profile-fingerprint
  mismatches were silently waived.** `check_contracts_comparable`'s
  header-sequence/include-sequence carve-outs accepted an additive-looking
  `profile_fields` change on its own — a header already declared
  identically on both sides via `--public-header`, but fed to the L2
  frontend via `-H` only on the new side, left `scope_fingerprint`
  completely unchanged while the profile fields still grew additively,
  even though the old snapshot never actually parsed that header's content
  (a real removal inside it would then be silently invisible, not
  reported). Both carve-outs now additionally require
  `_scope_growth_corroborated` — a genuinely differing, independently
  verified-additive scope-level change. Separately, an empty `differing`
  set (profile_fingerprint differs but no `PROFILE_FIELD_KEYS` field
  explains it — e.g. a deserialized contract whose `profile_fields` was
  absent/malformed) was wrongly treated as "nothing to explain, therefore
  comparable"; it now raises unconditionally instead of bypassing the
  fail-closed gate. Both fixes proven via direct repro (new regression
  tests fail without the fix, pass with it); the real F8 end-to-end
  scenarios are unaffected since genuine header additions really do grow
  `scope_fingerprint`.
