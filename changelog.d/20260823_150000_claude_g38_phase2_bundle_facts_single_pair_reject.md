### Fixed

- **`--bundle-facts-out` was silently ignored for a single-file/snapshot
  `compare` instead of persisting the requested baseline (Codex review,
  fresh evidence).** The option is directory/package-only (it captures the
  OLD-side library map a single-pair compare never builds), but the
  single-pair code path never read it after dispatch, so
  `abicheck compare old.so new.so --bundle-facts-out baseline.json`
  reported success without writing `baseline.json` — automation could be
  led to believe a baseline was persisted when none was. Added
  `_reject_bundle_facts_out_for_single_pair()`, rejecting the combination
  outright with a usage error, the mirror image of the existing
  `_reject_set_input_flags()` (which rejects single-pair-only flags on a
  directory/package compare).
