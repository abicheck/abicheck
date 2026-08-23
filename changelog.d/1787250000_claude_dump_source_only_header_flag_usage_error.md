### Fixed

- **`dump --sources TREE -H hdr` (no binary) silently ignored `-H`/`--header`
  and wrote an empty, `depth="binary"` snapshot with no trace the flag was
  dropped.** A source-only `dump` (no `SO_PATH`) dispatches to
  `dump_source_only()`, which embeds only L3/L4/L5 build/source facts — it
  has no L2 header-AST pass and never even receives `headers`. This is now
  rejected as a usage error (exit 64) naming the dropped flag, instead of
  exiting 0 with a misleadingly "successful" empty snapshot.
