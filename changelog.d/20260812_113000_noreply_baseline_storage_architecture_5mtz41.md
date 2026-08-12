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
  an existing asset's manifest `manifest_version` against the same
  `SUPPORTED_MANIFEST_VERSIONS` `resolve_target()`/`resolve_bundle()`
  check, before accepting a matching content digest as a safe retry — an
  existing asset with an absent or unsupported schema version, but
  otherwise-matching profile and content, previously passed as a safe
  no-op even though a real consumer would reject it as `stale_schema`.
