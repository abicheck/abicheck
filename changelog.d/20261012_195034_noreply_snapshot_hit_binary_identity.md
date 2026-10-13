### Fixed

- **A warm snapshot-cache hit reports the path it was asked for** — the whole-snapshot cache keys on binary content, so an entry written for one path served every other path holding the same bytes, and it returned that first path's `library`, `source_path`, `source_mtime`, and `source_size`. A hit now re-derives those identity fields from the requested path, matching an uncached dump.

