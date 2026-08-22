### Fixed

- **`pack_product_baseline()` no longer rejects a legitimately dangling
  symlink chain whose intermediate link is itself broken.** The
  case/Unicode-fold hazard check introduced in the previous round used
  `Path.exists()` to detect an exact-spelling match for each target
  component, which follows the *entire* remaining symlink chain rather than
  checking the component itself. For a chain like `libfoo.so -> alias ->
  missing`, `alias` genuinely exists on disk (as a broken symlink), but
  `exists()` reads it as absent because its own target is missing --
  causing the fold check to fall through to the sibling scan, find `alias`
  by its own exact (unfolded) spelling, and misreport it as a case-fold
  match. `_case_insensitive_target_resolves()` now uses a lexical existence
  check (`os.path.lexists()`) for the exact-match branch, so a component
  that is present under its own exact spelling -- even a symlink whose own
  further chain is broken -- is correctly treated as an ordinary dangling
  target rather than a case-fold hazard.
