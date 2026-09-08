### Fixed

- **A compressed snapshot whose stored length lands exactly on
  `bounded_decoded_prefix`'s raw-input cap is no longer rejected.** A plain
  `read(raw_size)` cannot distinguish "the file is exactly this long" from
  "there is more past the window", so a file sitting exactly on the cap
  reported itself as not exhausted and fell into the past-budget branch,
  which answers `None`. One byte is now read past the window purely as an
  EOF probe and then dropped, making the exhausted/not-exhausted decision
  exact at every escalation step. Follow-up to the fix released in #1165,
  which introduced the past-budget branch this depends on.
- **A prefix request larger than `bounded_decoded_prefix`'s raw-input cap is
  served rather than refused.** The cap bounds *amplification* — raw input
  read per decoded byte asked for — not how much a caller may ask for, and
  the escalation ceiling now says so (`max(n, cap)`). This restores the
  behaviour that shipped before an intermediate attempt in this branch
  clamped the first read to the cap outright: that clamp refused prefixes
  the function could produce (a 1.3 MB stored snapshot asked for a 4 MiB
  prefix returned `None` while `read_snapshot_bytes` returned all 3.9 MB of
  it), and applied to the compressed path only, leaving the plain branch
  inconsistent. No released version carried the clamp.
