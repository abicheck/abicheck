<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Documentation

- **Corrected a stale error-message rationale for `--pack`-asserted
  `contract.unresolved` on `compare-release`.** The `PackManifestError`
  raised when a directory/package (release) comparison's `--pack` sets
  `contract.unresolved`, and `resolve_release_pack_application`'s own
  docstring, previously claimed the release fan-out has no persisted
  contract-coverage context to fold the value into. That plumbing exists
  (`record_release_resolved_config` already builds and merges one per
  library); the rejection itself remains in place pending investigation of
  whether it is still needed, and the message/docstring now say so
  precisely instead of citing a gap that no longer exists. No behavior
  change — `contract.unresolved` still cannot be applied to a release
  comparison today.
