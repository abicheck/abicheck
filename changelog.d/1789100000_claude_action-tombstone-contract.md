### Documentation

- **The Action's retired inputs now have one executable fail-closed
  contract.** Every `action.yml` input kept as a tombstone (`against`,
  `audit`, `build-target`, `bundle-system-providers`, `crosscheck`,
  `estimate`, `new-library-set`, `require-complete-analysis`, `risk-rules`)
  is refused with an explicit `::error::` on every mode — that behavior is
  unchanged, but nothing exhaustively proved it: the previous coverage named
  three of the nine and asserted the *text* of `run.sh` rather than running
  it. `bundle-system-providers` is now marked `RETIRED` like its eight
  siblings (it was already a hard error, just worded differently), so one
  marker identifies the whole class.
