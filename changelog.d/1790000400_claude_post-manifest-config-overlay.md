### Added

- **`.abicheck.yml` `contract.overlays.post_manifest`** (one-comparison-product
  Phase 9c): the config home of the POST Python export manifest overlay that
  `compare --post-manifest` used to take per run. A project now states it
  once: `contract: {overlays: {post_manifest: python/abi/manifest.json}}`. A
  relative path resolves against the project root. Because the overlay
  narrows what gates, it applies only from a config named with `--config`,
  never from an auto-discovered one a pull request could edit (the same trust
  boundary as `build.query`/`compile.compiler`); the composite Action drops
  it from a discovered config. It applies to a single-pair `compare`. A directory/package comparison and a `--no-baseline`
  audit note on stderr that they do not apply it; the old flag was silently
  ignored by the directory/package fan-out. A stored-bundle-facts baseline
  rejects the `contract:` block like the other config blocks it cannot
  honour.
