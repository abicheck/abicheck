### Changed

- Parameter-signature canonicalization skips the per-character array-decay
  scan for spellings that contain no `[` (the common case); output is
  unchanged. This removes a hot spot seen in profiles of large header surfaces.
