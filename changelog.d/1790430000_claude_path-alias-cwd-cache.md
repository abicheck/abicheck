### Fixed

- `extract.path_aliases.canonical_spelling`/`source_header_alias_segments`
  no longer serve one working directory's answer to another. Both were
  cached on the path string alone, but a relative spelling (`old/include`)
  resolves against the working directory, so two comparisons run from two
  directories in one process -- a test session, a long-lived service --
  matched the second one's headers against the first one's include roots.
  A relative spelling's cache key now includes the directory it resolves
  from.
