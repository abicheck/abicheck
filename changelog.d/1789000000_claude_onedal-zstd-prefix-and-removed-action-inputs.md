### Fixed

- **A compressed `.json.zst` snapshot with a realistic compression ratio is no
  longer misclassified as `Cannot detect format`.** `snapshot_io.
  bounded_decoded_prefix` treated "the decoder returned without raising" as a
  successful decode, but a zstd frame cut at the 4096-byte raw-probe boundary
  returns a *short* result — commonly `b""`, whenever the first compressed
  block is still incomplete — with no exception at all. The empty prefix was
  accepted and returned, so `classify.CompressedAbiJsonClassifier` and
  `workflows.input_resolution` rejected a perfectly valid baseline. A result
  shorter than the requested length now escalates the raw read exactly as a
  raised exception already did, unless the file is already exhausted. Only
  low-ratio snapshots were affected (a highly-compressible one decodes past
  the probe boundary on the first attempt), which is why the pre-existing
  toy-scale fixtures never reached the branch.

### Changed

- **The Action's removed `jobs` and `bundle-system-providers` inputs are
  re-declared as tombstones and now report themselves.** Deleting an input
  from `action.yml` does not make a workflow that still sets it fail —
  GitHub drops the undeclared key before the composite action runs, so a
  pinned caller keeps a setting that has silently stopped applying. Setting
  `jobs` now emits a warning naming its removal (ADR-068 D5) and the
  resource-use consequence; setting `bundle-system-providers`, which
  configured analysis semantics rather than tuning, is a hard error naming
  its `.abicheck.yml` `bundle.system_providers:` replacement.
