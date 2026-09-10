### Fixed

- **`declaration_renamed` no longer over-fires on a closure whose only
  change is its own source coordinates.** L5 graph reconciliation
  (`buildsource.graph_reconcile`) was labeling a lambda/anonymous-tag pair
  it had already correctly matched (via structural context) as a genuine
  rename purely because the closure's qualified-name spelling embeds its
  own `:line:col`, which an unrelated edit elsewhere in the same header
  shifts. A real-world report on oneTBB (template/lambda-heavy) showed 139
  such findings against oneDNN's 3 for a comparable corpus size. The new
  `model.graph_identity.closure_location_free_identity` primitive strips a
  closure/anonymous-tag marker's basename+coordinates for the
  rename-vs-not-renamed comparison only (never for matching/merging, so no
  new merge rides on it); a genuine cross-file move is still reported as
  `declaration_moved` via the separate, coordinate-text-blind declaring-file
  check.
