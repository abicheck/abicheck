<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The root Action's release-contract baseline-set fallback no longer
  uses `gh release download --pattern` at all.** A prior fix backslash-
  escaped glob metacharacters (`*`, `?`, `[`, `]`) in the resolved asset
  name before passing it as a `--pattern` value, but that escaping is
  itself wrong on a Windows runner: Go's `path/filepath.Match` (what `gh`
  uses under the hood) disables escaping entirely there, treating `\` as
  the OS path separator instead. The fallback now looks the asset up by
  exact name via `gh release view --json assets` + `gh api <apiUrl>` —
  the same technique `publish-baseline.yml`'s own "Upload release asset"
  step already uses — sidestepping platform-dependent glob semantics
  entirely.
- **`actions/baseline/build_manifest.py`'s `recompute_content_digest_from_disk`**
  now refuses a `snapshot`/`binary` path from an existing asset's
  manifest.json that is absolute or escapes the extracted archive
  (`"../../..."`), mirroring `resolve_target()`/`resolve_bundle()`'s own
  containment check. Without it, a broken or malformed existing asset
  could point outside its own extraction directory — hashing an unrelated
  file (e.g. the current run's own fresh baseline output) and
  coincidentally matching the new digest, taking the safe-retry path for
  an asset a real consumer would later reject.
