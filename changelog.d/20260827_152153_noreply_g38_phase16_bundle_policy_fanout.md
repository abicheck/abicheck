<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`compare-release`'s directory/package fan-out now honors a `--policy`
  document's overrides for bundle-level findings** — a custom
  `overrides:` entry for a `bundle_*` `ChangeKind` (e.g.
  `bundle_intra_dep_removed: ignore`) previously had no effect on the
  release's aggregate `bundle_verdict`, even though the same override
  already applied correctly to every per-library verdict and to the
  stored-`BundleFacts` comparison driver. The release fan-out's own
  resolved `PolicyFile` is now threaded into bundle analysis the same way,
  and the same override now also reaches the severity-aware process exit
  code (`--severity-preset`/`--severity-*`), which previously scored
  bundle findings from the bare policy name alone.
