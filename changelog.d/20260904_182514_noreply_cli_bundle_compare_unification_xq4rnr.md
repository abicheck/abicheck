### Added

- **`compare` now supports a stored/stored `BundleFacts` comparison** — when
  both OLD_INPUT and NEW_INPUT classify as persisted `BundleFacts`
  documents (from a prior `--bundle-facts-out`), `compare` diffs them
  directly (`bundle_side_input.compare_stored_bundle_facts_pair`): a pure
  in-memory per-library diff, reading no binaries and parsing no header AST
  on either side. Closes one of the two operand shapes CLI cleanup phase
  two's PR I left without a real execution engine (`docs/contribute/plans/
  cli-cleanup-phase-two.md`); the JSON envelope's `new_dir` field keeps its
  established meaning, with a new `new_is_stored` field distinguishing the
  two shapes. The remaining shape — a live OLD_INPUT compared against a
  stored NEW_INPUT — is still rejected outright with a clear error.
