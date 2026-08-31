### Changed

- **`abicheck/buildsource/inline.py` split further, closing a G38 Phase 15
  file-split prerequisite**: the `.abicheck.yml` project-config schema
  (`BuildConfig`/`load_build_config`/`discover_build_config`) moved into a
  new sibling module, `abicheck/buildsource/build_config.py` — it shared no
  state with `inline.py`'s own inline build/source collection pipeline, the
  two were bundled in one file purely by history. `inline.py` re-exports all
  three names for back-compat (`inline.py`: 1975 -> 1414 lines), unblocking
  future work that needs new `.abicheck.yml` parsing surface without hitting
  the AI-readiness 2000-line hard cap.
