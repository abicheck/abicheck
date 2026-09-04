### Fixed

- **`compare`'s directory/package fan-out no longer corrupts a
  `bundle.system_providers:` entry containing a comma.** The resolved
  config list was joined into a comma string and re-split downstream — a
  leftover of the removed `--bundle-system-providers` CLI flag's own
  comma-separated syntax — so a provider SONAME containing a literal comma
  (unusual but valid) was silently split into two entries and never matched.
  `bundle_system_providers` is now threaded through as a real sequence end
  to end, with no comma-join/split round trip; `scan --artifact-set` and
  stored-BundleFacts compare were already unaffected.
