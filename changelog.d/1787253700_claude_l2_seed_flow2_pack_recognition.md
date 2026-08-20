### Fixed

- **The L2 include-dir/compile-context seed now recognizes a Flow-2
  `abicheck_inputs/` pack given as `--sources`/`--build-info`, not just a
  classic `BuildSourcePack`.** `embed_build_source` already folded both pack
  shapes uniformly, but the L2 seed's own pack-precedence resolver
  (`buildsource.l2_seed._l2_seed_pack_inputs`, used by `dump`/`scan`'s
  `-H`-header parsing) recognized only `BuildSourcePack`. A Flow-2 pack given
  alongside `-H` headers was therefore silently treated as a literal,
  un-normalized source tree: its own compile-unit include dirs never reached
  L2 seeding, and a trusted, explicit `build.query`/`--config` could
  genuinely be re-executed against the pack directory itself, even though the
  pack already carries its own resolved L3 evidence to fold in directly.
  Fixed by recognizing the pack the same way `embed_build_source` does,
  loading its `BuildEvidence` through the lighter `load_inputs_manifest` +
  compile-DB-only parse (not the full source-facts ingest, which this L3-only
  seed doesn't need). `dump --dry-run`'s `build.query` trust report
  (CLI cleanup phase two, PR F prerequisite 3) now reflects both pack shapes
  identically too, closing what its own module docstring previously listed
  as a "known, deliberately unclosed gap."
