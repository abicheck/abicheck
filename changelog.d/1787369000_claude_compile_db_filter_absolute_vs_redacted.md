### Fixed

- **`--compile-db-filter` with an absolute path still silently matched
  nothing against a redacted compile database.** The `is_relative_to()` fix
  for the double-prefix bug (`~/proj/~/proj/a.cpp`) closed the relative-file
  case, but an absolute filter (e.g. `--compile-db-filter
  /home/u/proj/a.cpp`, the real path a user would actually type) shares no
  path segments with a redacted unit (`~/proj/a.cpp`) at all — neither is a
  prefix of the other, so the earlier fix couldn't help. `source_matches_
  filter()` now expands a literal leading `~`/`~user` component
  (`os.path.expanduser`) on the file, directory, and filter pattern before
  comparing, so a real, unredacted filter matches a redacted unit (Codex
  review on #814, fresh evidence beyond the relative-filter fix).
