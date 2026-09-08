### Fixed

- **A compressed `.json.zst` snapshot with a realistic compression ratio is no
  longer misclassified as `Cannot detect format`.**
  `snapshot_io.bounded_decoded_prefix` treated "the decoder returned without
  raising" as a successful decode, but a zstd frame cut at the 4096-byte
  raw-probe boundary
  returns a *short* result — commonly `b""`, whenever the first compressed
  block is still incomplete — with no exception at all. The empty prefix was
  accepted and returned, so `classify.CompressedAbiJsonClassifier` and
  `workflows.input_resolution` rejected a perfectly valid baseline. A result
  shorter than the requested length now escalates the raw read exactly as a
  raised exception already did, unless the file is already exhausted. Only
  low-ratio snapshots were affected (a highly-compressible one decodes past
  the probe boundary on the first attempt), which is why the pre-existing
  toy-scale fixtures never reached the branch.
- **A valid multi-frame (concatenated) zstd snapshot is no longer read as
  just its first frame.** A single `read()` on a decompressing stream is not
  guaranteed to return the requested number of bytes even when the stream
  holds them, and `zstandard`'s `stream_reader` stops at every frame
  boundary in particular — so a snapshot stored as back-to-back frames
  decoded to only the first frame's payload, however small. Reads now
  continue to true EOF, in a new dependency-free
  `abicheck/storage/bounded_read.py` leaf that owns that rule for any
  reader. The prefix-classification logic it serves moved to
  `abicheck/storage/snapshot_prefix.py`; `snapshot_io` re-exports
  `bounded_decoded_prefix` unchanged.

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

### Security

- **A workflow-controlled Action input can no longer forge a GitHub workflow
  command from `action/validate-inputs.sh`.** Every message that script emits
  interpolates an `INPUT_*` value into a GitHub annotation, which is
  line-delimited — a value carrying a newline ended the annotation and had
  whatever followed parsed as a *new* workflow command, so
  `build-info: "x\n::error::spoofed"` emitted a spoofed error (and
  `::set-output`/`::add-mask` were reachable the same way). CR/LF is now
  collapsed in the shared `_warn`/`_fail` emitters, covering every
  interpolation site in the file at once, and those emitters now use
  `printf '%s\n'` rather than `echo`: under a shell with `xpg_echo` enabled
  (a build-time default on some bash builds, and reachable through
  `BASHOPTS`/`BASH_ENV`) `echo` expands backslash escapes, so a value
  carrying the *literal* characters `\n::error::` held no real newline for
  the collapse to remove and was turned into one by the emitter itself. `action/run.sh` builds its
  annotations with inline `echo` calls rather than a shared helper and is not
  covered by this fix; that pass is recorded as a known gap on the
  `trust_boundary.shell_workflow_injection` bug class.
