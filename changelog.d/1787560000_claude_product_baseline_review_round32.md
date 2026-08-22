### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: when `SOURCE_DIR`
  was passed as a symlink alias (`source-link -> source`) and `OUTPUT`
  was spelled through the real, non-aliased directory
  (`source/artifacts/base.tar.zst`, not `source-link/artifacts/...`), the
  scaffold-directory detection compared unresolved paths and silently
  reported the freshly-created `artifacts/` directory as not being inside
  `SOURCE_DIR` at all. That directory then read as genuine, pre-existing
  empty-directory content instead of output-only scaffolding, letting an
  otherwise genuinely empty `SOURCE_DIR` bypass the "no files found"
  rejection entirely. The scaffold computation now resolves the same
  alias the sibling output-exclusion logic already handles, translated
  back into `SOURCE_DIR`'s own spelling.
