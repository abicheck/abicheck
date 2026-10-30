### Fixed

- **Republishing a `ProjectSnapshot` package can no longer leave the previous
  one unreadable** — `write_project_manifest` used to overwrite
  `refs/<kind>/<id>.json` before checking objects, so a republish whose
  object check failed, or that was interrupted, left `manifest.json` naming
  refs that already held the new content. Package format v2 (ADR-062 D17)
  stores each ref record as a content-addressed object named by digest in
  `manifest.json`, which is replaced atomically as the single commit point,
  and a second concurrent writer to one package root is refused
  (`ConcurrentPublishError`). v1 packages stay readable; an older abicheck
  refuses a v2 package through the fail-closed `package_format_version`
  axis.
