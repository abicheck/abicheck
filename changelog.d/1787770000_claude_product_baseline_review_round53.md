### Fixed

- **`abicheck.package.TarExtractor`**: `_validate_symlink_target()`
  resolved a symlink member's *own full path* (including its own final
  component) to derive the directory a relative target is interpreted
  against -- for a duplicate member name (a real, legal tar shape where
  the last occurrence wins on extraction, replacing whatever was there),
  this meant a second `a -> ../victim` symlink validated its target
  against wherever the *first* `a -> deep/x` symlink already pointed,
  not against `a`'s own real parent directory, since real extraction
  unlinks and replaces `a` outright rather than writing through the
  stale symlink validated against. Now resolves only the lexical parent
  of the member's path (never its own final component), closing the gap
  for both the Python 3.10/3.11 fallback and the round-52 fix's
  member-by-member extraction path alike.
