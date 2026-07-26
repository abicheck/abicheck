### Fixed

- **The comparability gate never verified that a contract's stored
  fingerprint was actually computed from its own stored fields.** Every
  carve-out reasons entirely from `scope_fields`/`profile_fields` ("this
  recognized field grew additively, so the mismatch is explained"), but
  that reasoning is only sound if the fingerprint genuinely reflects those
  fields. For a snapshot `compute_extraction_contract` produced, that
  invariant always holds by construction; it was completely unenforced,
  though, so a deserialized or externally constructed contract could carry
  a stale or fabricated fingerprint alongside fields that merely *look*
  additive and still be waived through. Fixed with
  `_fingerprint_matches_fields`, which recomputes the fingerprint from the
  stored fields and compares it against the stored value — called on both
  sides, for both scope and profile, before any carve-out is trusted.
