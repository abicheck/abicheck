### Fixed

- **`compare-release --policy` now reaches bundle-level (cross-library)
  findings, not just per-library ones.** `_run_bundle_analysis()`/
  `_collect_bundle_result()` (`cli_compare_release_helpers.py`) called
  `compare_bundle()` with no `policy` argument at all, so a `BUNDLE_*`
  finding (`bundle_library_removed`, `bundle_intra_dep_removed`, ...) was
  always scored under the hardcoded `strict_abi` default regardless of the
  release's own `--policy` selection — a release configured with, say,
  `--policy plugin_abi` could still see its worst-of verdict pinned to
  `BREAKING` by a bundle-level finding the selected policy would otherwise
  demote. Both helper functions now accept and forward `policy`, and
  `cli_compare_release.py`'s real call site passes its own resolved
  `policy` through.
- **`pack_product_baseline()`'s dangling-symlink case/Unicode-fold check now
  walks every path component of the symlink target, not just the final
  basename.** The check exists to reject a relative symlink target that is
  dangling on a case-sensitive packing host but would resolve live on a
  case-insensitive one (Windows/macOS) via a case- or Unicode-fold match,
  since such a target would otherwise round-trip as an honestly-packed
  archive that `unpack_product_baseline()`'s own discovery walk then rejects
  as undeclared. The previous implementation only compared the target's
  basename against siblings in its immediate parent directory, so a target
  like `../payload/data` alongside a real `Payload/data` directory — dangling
  on the *intermediate* `payload` component, not the final `data` one — was
  missed entirely. `_case_insensitive_target_resolves()` now walks the full
  target, handling `.`/`..` and falling back to a case/Unicode-folded lookup
  at each component in turn.
