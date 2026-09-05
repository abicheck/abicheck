### Removed

- **Deleted the `--allow-build-query` and `--header-graph`/
  `--header-graph-includes` hidden, deprecated no-op flags** from `dump`
  and `compare` (they were already inert — `build.query` is authorized
  purely by an explicit `--config`, and the L2 header-only semantic graph
  is always attempted) instead of leaving them as hidden shims forever;
  passing any of the three is now a usage error (exit 64) rather than a
  silently-ignored flag. The GitHub Action's `allow-build-query` input
  stays registered for back-compat but is no longer forwarded to the CLI.
  Also removed the now-dead `allow_build_query` parameter it fed through
  `buildsource.inline.collect_inline_pack`, `buildsource.embed.
  embed_build_source`, and the CLI-side `embed_build_source`/
  `dump_source_only`/`_write_snapshot_output`/`embed_side_build_source`
  call chain, rather than leaving an accepted-but-ignored kwarg in place.

