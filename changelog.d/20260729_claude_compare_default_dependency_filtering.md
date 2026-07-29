<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **`compare` now filters live-binary dependency scope by default, matching
  `dump`** (follow-up to the dependency-scope comparability gate): `dump`'s
  default output has excluded toolchain/system-header declarations since
  `dumper_scoping.py`, but `compare`'s own live-binary dumping never applied
  that filter, so `compare a.so b.so` always compared the unfiltered
  surface even after that change. `compare` now takes the same
  `--include-dependencies` flag `dump` does (shared via
  `cli_options.include_dependencies_option`) and filters both sides by
  default the same way, threaded through `service.run_dump`'s new
  `include_dependencies` parameter (default `True`, preserving every other
  caller — `scan`, MCP, `dump`'s own inline calls — that doesn't opt in
  explicitly) and folded into the whole-snapshot disk cache key so a
  filtered and an unfiltered dump of the same binary never share a cache
  entry. This is what makes the recommended `dump old.so -o base.json` then
  `compare base.json new.so` workflow actually filter consistently by
  default, instead of merely failing loudly on the mismatch the way the
  prior comparability-gate-only fix did.
