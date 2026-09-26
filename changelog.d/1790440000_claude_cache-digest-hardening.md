### Fixed

- AST cache digests: the castxml store now records its entry's digest at
  publication (a hand-edited XML entry was otherwise trusted on its first
  read), a failed digest write removes any stale sidecar rather than leaving
  it paired with the new entry, and a sidecar that is not a hex digest is
  treated as unrecorded instead of evicting an intact entry.
