### Fixed

- **The comparability gate's scope-growth corroboration could be satisfied
  by an unrelated `public_header_dirs` change alone.** `_scope_growth_corroborated`
  required only that `scope_fingerprint` differ overall and that every
  `SCOPE_FIELD_KEYS` field be an additive superset (an unchanged field
  trivially satisfies that) — but `scope_fingerprint` hashes `headers` and
  `public_header_dirs` together, so a new `-I` search directory alone, with
  the declared `headers` set completely unchanged, could corroborate a
  `header_sequence`/`include_sequence` carve-out for exactly the silent
  false-negative scenario corroboration exists to catch: a header already
  declared identically on both sides via `--public-header`, but fed to the
  L2 frontend only on the new side, has no parsed AST content on the old
  side at all, so a real removal inside it is invisible rather than
  reported. `_scope_growth_corroborated` now also requires `headers`
  specifically to differ before corroborating either sequence carve-out.
