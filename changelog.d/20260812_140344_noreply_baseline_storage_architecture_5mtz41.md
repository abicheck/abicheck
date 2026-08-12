<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`actions/stage-baseline/run.sh`** no longer deletes a pre-existing
  `./$asset_name` before archiving — it now excludes it from each `tar`
  invocation instead (`--exclude`), which never touches anything on disk.
  The prior `rm -f` ran unconditionally and *before* the archive-suffix
  validation: a misconfigured `asset-name-template` resolving to an
  existing input filename with an unsupported suffix (e.g. `manifest.json`
  itself) deleted the required manifest before the suffix check even had
  a chance to reject the template, corrupting the supposedly read-only
  baseline directory before reporting the (unrelated) unsupported-suffix
  error — and even for a valid suffix, deleting a name that aliases a
  genuine, unrelated source member (not a stale leftover output at all)
  would have silently destroyed real baseline-set content.
