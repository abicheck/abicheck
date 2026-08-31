### Changed

- **Split `.abicheck.yml` config schema out of `buildsource/inline.py`** —
  `BuildConfig`/`load_build_config()`/`discover_build_config()` now live in
  a new sibling module, `abicheck/buildsource/build_config.py`, a
  mechanical extraction (unchanged behavior) that leaves `inline.py` at
  1415 lines (down from 1975) with real headroom under the AI-readiness
  2000-line hard cap. `inline.py` re-exports the moved names for
  back-compat, so no existing `from abicheck.buildsource.inline import
  BuildConfig` call site breaks. This was purely the file-split
  prerequisite `docs/contribute/plans/
  g38-bundle-facts-model-and-multibuild-comparability.md`'s Phase 15
  identified as blocking new `bundle_variants:` CLI/config surface —
  Phase 15's own feature work is separate, not-yet-attempted work.
