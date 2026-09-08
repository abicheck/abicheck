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
- **`bounded_decoded_prefix` now honours its raw-input budget on the first
  read.** The cap was only consulted in the escalation step, so a
  caller-supplied prefix length above it was read (and allocated) in full
  before the cap was ever checked. Nothing over-read in practice — the one
  large-window caller passes exactly the cap — but the bound is the
  function's contract.
