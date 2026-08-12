<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`publish-baseline.yml`'s "Upload release asset" schema check** now
  also validates each individual snapshot file's own `schema_version`
  field (via `abicheck.snapshot_io.read_snapshot_bytes`, transparent to
  compression), not just the manifest's aggregate `snapshot_schema` —
  the real resolver (`resolve_target()`/`resolve_bundle()`) checks both
  independently, since an older/hand-authored manifest with no aggregate
  `snapshot_schema` has nothing for that check alone to compare while
  every snapshot file still carries its own. The check also now
  explicitly validates `snapshot_schema` (and each snapshot's own
  `schema_version`) is an integer before comparing it, instead of
  letting a malformed non-integer value raise `TypeError` and abort the
  whole step with a raw traceback under `set -euo pipefail`.
- **`publish-baseline.yml`'s "Upload release asset" step** now rejects an
  existing asset whose archive contains a symlink before accepting a
  matching content digest as a safe retry — `TarExtractor`'s own member
  validation only rejects a symlink that escapes the extraction root,
  not one that stays inside it, but `actions/resolve-baseline` (the
  canonical consumer) rejects any symlink at all, so such an asset could
  previously be reported as successfully published while remaining
  unusable to a real consumer.
- **`actions/stage-baseline/run.sh`'s zstd fast path** now verifies
  `tar --zstd` actually works with a real trial archive, not just that a
  `zstd` binary is on `PATH` — some `tar` builds reject `--zstd` as an
  unrecognized option even with a standalone `zstd` CLI present, which
  would otherwise fail the fast path outright before the Python fallback
  ever got a chance to run.
