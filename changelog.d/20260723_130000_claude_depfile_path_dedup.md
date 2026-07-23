<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A repeated depfile entry could flip `profile_fingerprint`** (Codex
  review, PR #624): `depfile_resolved_paths` can realistically list the
  same resolved file more than once (e.g. concatenated per-TU depfiles, or
  an un-deduplicated depfile parse). Left un-deduped, a repeated entry was
  bucketed and hashed twice — into an external `-I` slot's content-hash
  pairs, or the unattributed system/toolchain bucket — so an otherwise
  identical extraction fingerprinted differently purely because one side
  happened to repeat the same dependency entry once more than the other,
  potentially raising `ProfileMismatchError` for no real drift.
  `depfile_resolved_paths` is now deduplicated by resolved identity before
  any bucketing.
