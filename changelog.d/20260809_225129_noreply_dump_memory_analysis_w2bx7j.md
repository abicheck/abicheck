<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Performance

- **Bounded DWARF parse memory on large binaries** — pyelftools permanently
  caches every DIE it parses for the lifetime of the `CompileUnit`/
  `DWARFInfo` objects, and `dump`'s DWARF passes (basic metadata, advanced
  metadata, snapshot build, layout backfill) share one such session across
  up to three full-tree walks so later passes hit that cache instead of
  re-parsing. On a template-heavy C++ library (millions of DIEs from a
  ~100 MB `.debug_info`), the per-DIE Python object overhead inflated that
  cache to 50-100x the raw section bytes, peaking above 12 GB of RSS for a
  single `dump`. Each DWARF walk now frees a `CompileUnit`'s DIE cache as
  soon as it finishes with it whenever `.debug_info` exceeds
  `ABICHECK_DWARF_LOW_MEMORY_MB` (default 32 MiB) — trading the cross-pass
  cache-reuse CPU savings for bounded peak memory on binaries large enough
  for that trade to matter. Output is unaffected; small binaries (the
  common case) keep the existing cache-reuse behavior unchanged.

