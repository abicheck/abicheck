### Fixed

- **Republishing a `ProjectSnapshot` package can no longer leave the previous
  one unreadable** — `write_project_manifest` used to overwrite
  `refs/<kind>/<id>.json` before checking objects, so a republish whose
  object check failed, or that was interrupted, left `manifest.json` naming
  refs that already held the new content. Package format v2 (ADR-062 D17)
  writes each ref document once to `refs/<kind>/<id>/<ref-digest>.json`,
  names it by `{id, ref_digest}` in `manifest.json`, and replaces
  `manifest.json` atomically as the single commit point; writers to one
  package root are serialized. v1 packages stay readable; an older abicheck
  refuses a v2 package through the fail-closed `package_format_version`
  axis.
