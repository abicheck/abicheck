### Fixed

- A corrupt header-graph projection-cache sidecar no longer aborts the dump.
  `load_cached_projection` read its entry with `read_text(encoding="utf-8")`,
  which raises `UnicodeDecodeError` — a `ValueError`, not caught by the
  surrounding `except OSError` — so a sidecar holding invalid UTF-8 (a
  truncated write, or an unrelated binary file landing at that name) escaped
  into `_attach_header_graph` and failed the run, leaving the bad entry in
  place to do it again. That is the ADR-028 D3 guarantee inverted: a cache
  failure became a run failure. Two sibling calls were unguarded for the same
  reason and are fixed with it — `unlink(missing_ok=True)` suppresses only
  ENOENT, so eviction could raise on a read-only mount, and the write path's
  own cleanup used a bare `unlink` inside `except OSError`, running in exactly
  the conditions that made the write fail. All three now go through one
  best-effort `_discard_sidecar`, so every outcome of this cache is either a
  projection or a re-parse, which is what the run would have done without the
  cache at all.
