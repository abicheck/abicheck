### Added

- **`.abicheck.yml` `contract.overlays.post_manifest`** (one-comparison-product
  Phase 9c): the config home of the POST Python export manifest overlay that
  `compare --post-manifest` used to take per run. A project now states it
  once: `contract: {overlays: {post_manifest: python/abi/manifest.json}}`. A
  relative path resolves against the project root, and the composite Action's
  config relocation rewrites it like `compile.include_dirs`. It applies to a
  single-pair `compare`. A directory/package comparison and a `--no-baseline`
  audit note on stderr that they do not apply it; the old flag was silently
  ignored by the directory/package fan-out. A stored-bundle-facts baseline
  rejects the `contract:` block like the other config blocks it cannot
  honour.
