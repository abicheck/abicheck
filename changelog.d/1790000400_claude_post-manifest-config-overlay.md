### Added

- **`.abicheck.yml` `contract.overlays.post_manifest`** (one-comparison-product
  Phase 9c): the config home of `compare --post-manifest`. A POST Python export
  manifest is a stable project property, so a project now states it once:
  `contract: {overlays: {post_manifest: python/abi/manifest.json}}`. A
  relative path resolves against the project root. `--post-manifest` still
  overrides it for one run, and the composite Action's config relocation
  rewrites the path like `compile.include_dirs`.

### Fixed

- **`compare --post-manifest` on a directory/package comparison is now a
  usage error (exit `64`).** The release fan-out never received the manifest,
  so the flag was accepted and silently ignored. A configured
  `contract.overlays.post_manifest` is noted on stderr as not applied there
  (and on a `--no-baseline` audit). A stored-bundle-facts baseline rejects the
  `contract:` block like the other config blocks it cannot honour.
