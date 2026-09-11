### Fixed

- Out-of-band readers of a snapshot written by `abicheck dump` now unwrap the
  ADR-063 Phase 8 sectioned envelope before indexing formerly-top-level keys.
  The Clang-plugin end-to-end lane read `baseline["build_source"]` and failed
  with "merged baseline has no embedded build_source payload" against a dump
  that had folded the plugin pack correctly, and `validate_examples.py`'s
  embedded-layer check silently answered "no L3/L4/L5 layer present" for every
  real dump-written snapshot.
