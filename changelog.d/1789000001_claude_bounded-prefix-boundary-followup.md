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
  read per decoded byte asked for — not how much a caller may ask for. Above
  the cap the ceiling is now the request plus one cap of escalation
  headroom: sitting it exactly on the request leaves the escalation loop
  nowhere to go, so a stream needing slightly more than `n` stored bytes to
  yield `n` decoded ones (an incompressible payload, or `compresslevel=0`
  gzip, where stored exceeds raw) answered `None` for a prefix it could
  produce. At or below the cap nothing changes, which is every caller in the
  tree. Two intermediate attempts in this branch got this wrong in opposite
  directions — clamping the first read to the cap refused serveable requests,
  and a ceiling of `max(n, cap)` removed the headroom — and neither was ever
  released.
