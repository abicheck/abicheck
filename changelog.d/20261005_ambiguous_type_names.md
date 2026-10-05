### Fixed

- Type findings on a bare name that several declarations share (`lib::m0::Ctx`
  and `lib::m3::Ctx`, both stored as `Ctx` by the header backends) are now
  labelled with their qualified spelling, list only their own namespace's
  functions in `affected_symbols`, and are no longer collapsed into one
  finding when two namesakes change identically (the second break was
  dropped). Findings on a unique bare name are unchanged, and suppression
  rules still see the bare label.
