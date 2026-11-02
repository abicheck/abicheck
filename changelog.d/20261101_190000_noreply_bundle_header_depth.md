### Added

- **A `kind: bundle` check runs at `depth: headers`** — `actions/check-target`
  now compares each member's own baseline snapshot (`resolve-baseline`'s
  `member-snapshots-dir`, with the new `member-snapshot-paths` output)
  against one candidate dump per member, made with that member's own
  `public_headers:` (run-plan `member_headers`, check-target's new
  `member-headers` input). Before, a bundle's OLD side was the baseline's
  binary directory and both sides would have been parsed against one header
  set, which misses a header-only change, so the depth was refused. The check
  refuses a baseline whose member snapshots do not all carry header evidence,
  and `project validate` requires `public_headers:` on every member of a
  header-depth bundle.
