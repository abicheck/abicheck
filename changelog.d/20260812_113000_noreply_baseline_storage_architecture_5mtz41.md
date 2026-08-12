<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`action/run.sh`'s baseline-set archive fallback** now downloads the
  release asset to a fixed, platform-safe local filename instead of one
  derived from the (untrusted, potentially metacharacter-containing)
  resolved asset name — a legal Linux filename (containing `?`, for
  example) can be an illegal one on NTFS, which would otherwise fail the
  download on a Windows runner even after the exact-name lookup itself
  succeeded. The archive's real encoding is still selected from the
  resolved asset name's own suffix.
- **`publish-baseline.yml`'s "Upload release asset" step** now validates
  an existing asset's manifest `manifest_version` AND `snapshot_schema`
  against the same two `stale_schema` checks
  `resolve_target()`/`resolve_bundle()` apply, before accepting a
  matching content digest as a safe retry — an existing asset with an
  absent/unsupported `manifest_version`, or a `snapshot_schema` newer
  than this checkout's installed reader, but otherwise-matching profile
  and content, previously passed as a safe no-op even though a real
  consumer would reject it as `stale_schema`.
- **`actions/stage-baseline/run.sh`'s asset-name validation** now also
  rejects a literal `#` character — `gh release upload` treats anything
  after a `#` in a file argument as a display label, not part of the
  filename, so an asset name like `baseline#debug.tar.zst` would package
  correctly here but then fail `publish-baseline.yml`'s first-time-publish
  upload outright.
- **`publish-baseline.yml`'s "Upload release asset" step** now also
  validates an existing asset's manifest `project_ref` against the
  current run's `RELEASE_TAG` before accepting a matching content digest
  as a safe retry — an existing asset published under a different (or
  missing) `project_ref`, but otherwise-matching profile and content,
  previously passed as a safe no-op even though a real consumer
  supplying that tag as `resolve-baseline`'s `expected-project-ref`
  would reject it as `wrong_project_ref`.
