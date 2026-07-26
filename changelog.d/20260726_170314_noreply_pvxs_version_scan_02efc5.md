### Fixed

- **The `include_sequence` slot validation checked only the `ext:`/`sys:`
  token prefix, not that the payload was a genuine digest.** The previous
  round's delimiter/token-shape fix required a recognized prefix
  (`hdrs:`/`ext:`/`label:`) after each slot's index, but never validated
  what followed an `ext:`/`sys:` prefix — a malformed, unchanged payload
  like `"ext:bogus"` or `"sys:not-a-sha256"` still passed, and (like the
  previous rounds' gaps) could ride alongside a genuinely-growing `hdrs:`
  slot undetected via the per-slot equality short-circuit. Added
  `_is_valid_digest_payload`, which every `ext:`/`sys:` payload must now
  match the real `"sha256:<64 lowercase hex chars>"` shape
  `compute_extraction_contract` always produces — `label:` (arbitrary
  user-supplied text) and `hdrs:` (validated separately via its own JSON
  shape) are unaffected.
